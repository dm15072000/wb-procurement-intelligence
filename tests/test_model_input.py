"""The app's duration-model input must match the registered model's schema (doubles, not whole numbers)."""
import math

from src.models import duration as du


def test_numeric_features_become_floats_and_missing_stays_missing():
    row = {c: None for c in du.CATEGORICAL + du.NUMERIC}
    row.update({"procurement_category": "Works", "project_age_days": 412, "n_earlier_tenders": 3, "fy_month": 7,
                "cpia_missing": 1, "log_amount_usd": 13.2})
    out = du.model_input_row(row)
    assert set(out) == set(du.CATEGORICAL + du.NUMERIC)
    assert all(isinstance(out[c], float) for c in du.NUMERIC)
    assert out["project_age_days"] == 412.0
    assert math.isnan(out["cpia_tran"])            # missing is NaN, never 0
    assert out["procurement_category"] == "Works"
