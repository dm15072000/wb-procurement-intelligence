"""Canonical keys and normalization rules (docs/02_data_model.md §2).

Plain-language summary: different World Bank sources write the same identifier in slightly different ways. These small
functions turn each identifier into one standard form, so records from different sources can be joined reliably.
Anything that does not fit the expected pattern is rejected (None) rather than guessed.
"""
import re
from datetime import date, datetime

_PROJECT_ID = re.compile(r"^P\d{6}$")

# World Bank fiscal year N runs from 1 July of year N-1 to 30 June of year N.
# Contract-data coverage by fiscal year (decision 2026-10-21, Option A):
#   FY <= 2016  DS01004: prior-reviewed contracts only
#   FY 2017     DS04691: partial, likely incomplete (STEP was being rolled out)
#   FY 2018-19  DS04691: partial
#   FY >= 2020  DS00005: all IPF contract awards from STEP
STEP_DATASETS = ("DS00005", "DS04691")          # same STEP schema; contract models train on these
CONTRACT_DATASETS = ("DS00005", "DS04691", "DS01004")  # de-duplication precedence, highest first
PARTIAL_COVERAGE_FYS = (2017, 2018, 2019)


def project_id(raw):
    """'p147864 ' -> 'P147864'; anything not matching P + 6 digits -> None."""
    s = (raw or "").strip().upper()
    return s if _PROJECT_ID.match(s) else None


def ref_key(raw):
    """Borrower / bid reference number -> join key: uppercase, keep only A-Z and 0-9 (Stage 2 H1 rule)."""
    s = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    return s or None


def tender_key(pid, raw_ref):
    p, r = project_id(pid), ref_key(raw_ref)
    return f"{p}:{r}" if p and r else None


def contract_key(number):
    """World Bank contract number -> canonical contract key: 1543858 -> 'C:1543858'.
    Keyed on the number ALONE (not the dataset), so a contract listed in two datasets collapses to one."""
    n = str(number or "").strip()
    return f"C:{n}" if n else None


_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y")


def parse_date(raw):
    """Dates as they appear in the verified sources: '19-May-2022', '2024-12-20T00:00:00Z',
    '12/20/2025 12:00:00 AM', '2023-06-13'. Returns a date or None."""
    s = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def fiscal_year(d):
    """World Bank fiscal year of a date (FY runs July-June): 2016-07-01 -> 2017."""
    return d.year + 1 if d.month >= 7 else d.year


def coverage_status(fy):
    """How complete the contract data is for a fiscal year (see the table above)."""
    if fy is None:
        return None
    if fy <= 2016:
        return "prior_review_only"
    if fy == 2017:
        return "partial_likely_incomplete"
    if fy in PARTIAL_COVERAGE_FYS:
        return "partial"
    return "full"
