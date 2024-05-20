import os
import warnings
import joblib
import numpy as np
import pandas as pd
from schema.data_schema import ForecastingSchema
from autogluon.timeseries import TimeSeriesDataFrame, TimeSeriesPredictor, models
from sklearn.exceptions import NotFittedError
from logger import get_logger


warnings.filterwarnings("ignore")


PREDICTOR_FILE_NAME = "predictor.joblib"
MODEL_FILE_NAME = "model.joblib"

logger = get_logger(task_name="model")


class Forecaster:
    """A wrapper class for the NPTS Forecaster.

    This class provides a consistent interface that can be used with other
    Forecaster models.
    """

    """
    The NPTS model from AutoGluon does not support covariates.
    """

    model_name = "NPTS Forecaster - AutoGluon"

    def __init__(
        self,
        data_schema: ForecastingSchema,
        kernel_type: str = "exponential",
        exp_kernel_weights: float = 1.0,
        use_seasonal_model: bool = True,
        num_samples: int = 100,
        num_default_time_features: int = 1,        
        use_static_features: bool = False,    # static_covariates
        use_future_covariates: bool = False,  # called known_covariates in AutoGluon
        use_past_covariates: bool = False,
        **kwargs,
    ):
        """Construct a new NPTS Forecaster

        Args:

            data_schema (ForecastingSchema):
                The schema of the data.

            kernel_type ({"exponential", "uniform"}, default = "exponential") – 
                Kernel used by the model.

            exp_kernel_weights (float, default = 1.0)
                Scaling factor used in the exponential kernel.

            use_seasonal_model (bool, default = True) 
                Whether to use the seasonal variant of the model.

            num_samples (int, default = 100)
                Number of samples generated by the forecast.

            num_default_time_features (int, default = 1)
                Number of time features used by seasonal model.

            use_static_features (bool):
                Whether the model should use static features if available.

            use_future_covariates (bool):
                Whether the model should use future covariates if available.

            use_past_covariates (bool):
                Whether the model should use past covariates if available.

            **kwargs:
                Optional arguments.
        """
        self.data_schema = data_schema
        self.kernel_type = kernel_type
        self.exp_kernel_weights = exp_kernel_weights
        self.use_seasonal_model = use_seasonal_model
        self.num_samples = num_samples
        self.num_default_time_features = num_default_time_features
        
        self.use_static_features = use_static_features and (
            len(data_schema.static_covariates) > 0
        )
        self.use_past_covariates = (
            use_past_covariates and len(data_schema.past_covariates) > 0
        )
        self.use_future_covariates = use_future_covariates and (
            len(data_schema.future_covariates) > 0
            or self.data_schema.time_col_dtype in ["DATE", "DATETIME"]
        )

        self.kwargs = kwargs
        self._is_trained = False

    def _prepare_data(self, data: pd.DataFrame) -> TimeSeriesDataFrame:
        """Prepare the data for training or prediction.

        Args:
            data (pd.DataFrame): The input data.

        Returns:
            TimeSeriesDataFrame: The prepared data.
        """

        if not self.use_past_covariates and set(self.data_schema.past_covariates).issubset(data.columns):
            data = data.drop(columns=self.data_schema.past_covariates)

        if not self.use_future_covariates and set(self.data_schema.future_covariates).issubset(data.columns):
            data = data.drop(columns=self.data_schema.future_covariates)

        static_features_df = None
        if self.use_static_features:
            static_features_df = data[[
                self.data_schema.id_col]+self.data_schema.static_covariates]
            static_features_df.drop_duplicates(inplace=True, ignore_index=True)

        data = data.drop(columns=self.data_schema.static_covariates)

        prepared_data = TimeSeriesDataFrame.from_data_frame(df=data,
                                                            id_column=self.data_schema.id_col,
                                                            timestamp_column=self.data_schema.time_col,
                                                            static_features_df=static_features_df,
                                                            )

        return prepared_data

    def fit(
        self,
        train_data: pd.DataFrame,
        model_dir_path: str
    ) -> None:
        """Fit the Forecaster model.
            NPTS model is a Statistical model - Non-Parametric Time Series Forecaster.
            This models is especially well suited for forecasting sparse or intermittent time series with many zero values.
        
        Args:
            train_data (pd.DataFrame): Training data.
            model_dir_path (str): Path to save the model.

        """

        """
        more Hyperparameters:

        n_jobs (int or float, default = 0.5)
            Number of CPU cores used to fit the models in parallel.
            When set to a float between 0.0 and 1.0, that fraction of available CPU cores is used. When set to a positive integer, that many cores are used. When set to -1, all CPU cores are used.

        max_ts_length (int, default = 2500)
                If not None, only the last max_ts_length time steps of each time series will be used to train the model.
                This significantly speeds up fitting and usually leads to no change in accuracy.

        """
        prepared_data = self._prepare_data(train_data)
        
        future_covariates = None
        if self.use_future_covariates:
            future_covariates = self.data_schema.future_covariates

        self.model = TimeSeriesPredictor(path=os.path.join(model_dir_path, MODEL_FILE_NAME),
                                         target=self.data_schema.target,
                                         prediction_length=self.data_schema.forecast_length,
                                         known_covariates_names=future_covariates,
                                         cache_predictions=False,
                                         ).fit(
            train_data=prepared_data,
            hyperparameters={
                "NPTS": {
                    "kernel_type": self.kernel_type,
                    "exp_kernel_weights": self.exp_kernel_weights,
                    "use_seasonal_model": self.use_seasonal_model,
                    "num_samples": self.num_samples,
                    "num_default_time_features": self.num_default_time_features,
                    **self.kwargs,
                }
            },
            skip_model_selection=True,
            verbosity=0,
        )
        self._is_trained = True

    def predict(
        self, train_data: pd.DataFrame, prediction_col_name: str
    ) -> pd.DataFrame:
        """Make the forecast of given length.
        Args:
            train_data (pd.DataFrame): Given test input for forecasting.
            prediction_col_name (str): Name to give to prediction column.
        Returns:
            pd.DataFrame: The predictions dataframe.
        """
        if not self._is_trained:
            raise NotFittedError("Model is not fitted yet.")

        prepared_data = self._prepare_data(train_data)
        predictions = self.model.predict(data=prepared_data, use_cache=False)
        predictions.reset_index(inplace=True)

        predictions = predictions.rename(columns={"item_id": self.data_schema.id_col,
                                                  "timestamp": self.data_schema.time_col,
                                                  "mean": prediction_col_name})

        if self.data_schema.time_col_dtype in ["INT", "OTHER"]:
            last_timestamp = train_data[self.data_schema.time_col].max()
            new_timestamps = np.arange(
                last_timestamp + 1, last_timestamp + 1 + self.data_schema.forecast_length
            )
            predictions[self.data_schema.time_col] = np.tile(
                new_timestamps, predictions[self.data_schema.id_col].nunique())

        return predictions[[self.data_schema.id_col, self.data_schema.time_col, prediction_col_name]]

    def save(self, model_dir_path: str) -> None:
        """Save the Forecaster to disk.

        Args:
            model_dir_path (str): Dir path to which to save the model.
        """
        if not self._is_trained:
            raise NotFittedError("Model is not fitted yet.")
        self.model.save()
        joblib.dump(self, os.path.join(model_dir_path, PREDICTOR_FILE_NAME))

    @ classmethod
    def load(cls, model_dir_path: str) -> "Forecaster":
        """Load the Forecaster from disk.

        Args:
            model_dir_path (str): Dir path to the saved model.
        Returns:
            Forecaster: A new instance of the loaded Forecaster.
        """
        forecaster = joblib.load(os.path.join(
            model_dir_path, PREDICTOR_FILE_NAME))
        model = TimeSeriesPredictor.load(
            os.path.join(model_dir_path, MODEL_FILE_NAME))
        forecaster.model = model
        return forecaster

    def __str__(self):
        # sort params alphabetically for unit test to run successfully
        return f"Model name: {self.model_name}"


def train_predictor_model(
    data_schema: ForecastingSchema,
    train_data: pd.DataFrame,
    model_dir_path: str,
    hyperparameters: dict,
) -> Forecaster:
    """
    Instantiate and train the predictor model.

    Args:
        data_schema (ForecastingSchema): Schema of the training data.
        hyperparameters (dict): Hyperparameters for the Forecaster.

    Returns:
        'Forecaster': The Forecaster model
    """

    model = Forecaster(
        data_schema=data_schema,
        **hyperparameters,
    )
    model.fit(train_data=train_data, model_dir_path=model_dir_path)
    return model


def predict_with_model(
    model: Forecaster, train_data: pd.DataFrame, prediction_col_name: str
) -> pd.DataFrame:
    """
    Make forecast.

    Args:
        model (Forecaster): The Forecaster model.
        train_data (pd.DataFrame): The train input data for forecasting used to do prediction.
        prediction_col_name (int): Name to give to prediction column.

    Returns:
        pd.DataFrame: The forecast.
    """
    return model.predict(train_data, prediction_col_name)


def save_predictor_model(model: Forecaster, predictor_dir_path: str) -> None:
    """
    Save the Forecaster model to disk.

    Args:
        model (Forecaster): The Forecaster model to save.
        predictor_dir_path (str): Dir path to which to save the model.
    """
    if not os.path.exists(predictor_dir_path):
        os.makedirs(predictor_dir_path)
    model.save(predictor_dir_path)


def load_predictor_model(predictor_dir_path: str) -> Forecaster:
    """
    Load the Forecaster model from disk.

    Args:
        predictor_dir_path (str): Dir path where model is saved.

    Returns:
        Forecaster: A new instance of the loaded Forecaster model.
    """
    return Forecaster.load(predictor_dir_path)
