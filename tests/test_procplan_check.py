"""Unit tests for src/procplan/check.py. Run locally: python -m pytest tests -q"""
from datetime import date

from src.procplan.check import cell_dates, check_row, date_column, html_grid, parse_date, reason


def test_parse_date_formats():
    assert parse_date("2023-06-13") == date(2023, 6, 13)
    assert parse_date("13-Jun-2023") == date(2023, 6, 13)
    assert parse_date("13/06/2023") == date(2023, 6, 13)
    assert parse_date("2017-08-2") is None or parse_date("2017-08-2") == date(2017, 8, 2)  # strptime accepts 1-digit day


def test_cell_dates_wrapped_and_multiple():
    assert cell_dates("2016-09-0 6") == ([date(2016, 9, 6)], False)          # wrapped inside one cell
    assert cell_dates("2017-05-01 2017-05-08") == ([date(2017, 5, 1), date(2017, 5, 8)], False)
    assert cell_dates("54,431,569.00") == ([], False)                        # an amount is not a date
    assert cell_dates("") == ([], False)
    ds, bad = cell_dates("2016-13-")                                          # date-like but unreadable
    assert ds == [] and bad is True


def test_check_row_clean_with_misordered_columns():
    # Review/method/status are found by value, so their column order doesn't matter.
    row = ["IN-UPPWD-14106-CW-RFB / Construction of SH-51", "IBRD / 8940", "Network Improvement",
           "Request for Bids", "Prior", "Open - International", "54,431,569.00", "Signed", "2017-05-01", "13-Jun-2018"]
    rec = check_row(row)
    assert rec["ref"] == "IN-UPPWD-14106-CW-RFB" and rec["description"] == "Construction of SH-51"
    assert (rec["review"], rec["method"], rec["status"]) == ("Prior", "Request for Bids", "Signed")
    assert rec["n_dates"] == 2 and rec["reason"] == "clean"


def test_check_row_reasons_are_consistent():
    # Run-1 bug: a row could be counted not-clean yet explained as "ok". Now one function does both.
    rec = check_row(["REF-1 / x", "Request for Bids", "Signed", "2020-01-01"])  # no Prior/Post
    assert rec["reason"] == "no review type"
    rec = check_row(["REF-1 / x", "Post", "Request for Bids", "Signed", "2020-01-0x"])
    assert rec["reason"] in ("bad date", "no dates")
    for r in (check_row(["A / b", "Prior", "Shopping", "Completed", "2019-02-03"]),):
        assert r["reason"] == reason(r) == "clean"


def test_wrapped_method_text_matches():
    rec = check_row(["CS-1 / audit", "Post", "Quality And Cost-Based Selectio", "Under Implementation", "2021-03-04"])
    assert rec["method"] and rec["status"] == "Under Implementation"


def test_html_grid_colspan_rowspan():
    html = ("<table><tr><th rowspan='2'>Ref</th><th colspan='2'>Signed Contract</th></tr>"
            "<tr><th>Planned</th><th>Actual</th></tr>"
            "<tr><td>A / x</td><td>2020-01-01</td><td>2020-02-01</td></tr></table>")
    g = html_grid(html)
    assert g[0] == ["Ref", "Signed Contract", "Signed Contract"]
    assert g[1] == ["Ref", "Planned", "Actual"]
    assert g[2] == ["A / x", "2020-01-01", "2020-02-01"]


def test_date_column_requires_dates_in_values():
    names = ["ref", "signed contract actual", "signed contract actual note"]
    rows = [["a", "2020-01-01", "n/a"], ["b", "2020-02-01", "x"]]
    assert date_column(names, rows, "signed", "actual") == 1
    assert date_column(["ref", "signed actual"], [["a", "Prior"]], "signed", "actual") is None
