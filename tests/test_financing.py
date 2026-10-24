"""Projects API financing and PDO parsing (src/ingest/financing.py)."""
from src.ingest import financing as fn


def test_financiers_are_deduplicated_and_cofinancing_flagged():
    p = {"id": "P1", "pdo": " Improve roads. ",
         "financier_loan": ["IBRD84850", "IBRD84850", "TF0A1234"],
         "financierdesc": ["International Bank for Reconstruction and Development"] * 2 + ["Global Infrastructure Facility"],
         "financiertype": ["IBRD", "IBRD", "Trust Fund"]}
    rows = fn.financing_rows(p)
    assert len(rows) == 2
    assert [r["is_cofinancing"] for r in rows] == [False, True]
    assert fn.pdo_row(p) == ("P1", "Improve roads.")


def test_missing_and_uneven_lists():
    assert fn.financing_rows({"id": "P2"}) == []
    rows = fn.financing_rows({"id": "P3", "financier_loan": ["IDA5000"], "financiertype": []})
    assert rows[0]["financier_type"] is None and rows[0]["is_cofinancing"] is None   # unknown, never guessed
    assert fn.pdo_row({"id": "P4", "pdo": ""}) == ("P4", None)
