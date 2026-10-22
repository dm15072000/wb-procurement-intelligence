"""MLflow wrapper that serves the three quantile models (P20, P50, P80) as one model.

Plain-language summary: notebook 50 trains three gradient-boosted models, one per quantile. This wrapper bundles them,
so the app loads one registered model and gets a range back. Separately trained quantile models can occasionally
cross (P80 below P50 for an odd input); the wrapper sorts the three values per row so the range is always ordered.
"""
try:
    import mlflow.pyfunc
    _Base = mlflow.pyfunc.PythonModel
except ImportError:  # pragma: no cover - only for environments without mlflow
    _Base = object


class QuantileDurationModel(_Base):
    def __init__(self, models, columns):
        """models: {"p20": fitted sklearn pipeline, "p50": ..., "p80": ...}; columns: input column order."""
        self.models, self.columns = models, list(columns)

    def predict(self, context, model_input, params=None):
        import numpy as np
        import pandas as pd
        X = model_input[self.columns]
        raw = np.column_stack([self.models[q].predict(X) for q in ("p20", "p50", "p80")])
        raw = np.sort(np.clip(raw, 1, None), axis=1)   # ordered range, at least 1 day
        return pd.DataFrame(raw, columns=["p20", "p50", "p80"], index=model_input.index)


def build_pipeline(categorical, numeric, quantile, **gbt_params):
    """scikit-learn pipeline: categorical columns ordinal-encoded (unknown/missing -> -1, which the trees treat as
    missing), numeric columns passed through (NaN allowed), then a quantile gradient-boosted regressor."""
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OrdinalEncoder

    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1)
    pre = ColumnTransformer([("cat", enc, categorical), ("num", "passthrough", numeric)])
    params = dict(max_depth=3, learning_rate=0.05, max_iter=300, min_samples_leaf=20, random_state=42)
    params.update(gbt_params)
    gbt = HistGradientBoostingRegressor(loss="quantile", quantile=quantile,
                                        categorical_features=list(range(len(categorical))), **params)
    return Pipeline([("pre", pre), ("gbt", gbt)])
