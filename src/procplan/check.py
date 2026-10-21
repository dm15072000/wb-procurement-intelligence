"""Checker for activity rows parsed from STEP Procurement Plans.

Plain-language summary: a Procurement Plan PDF is turned into HTML tables by ai_parse_document. Each table row describes
one procurement activity. This module decides, row by row, whether the row "parses cleanly". It finds review type, method
and status by looking at the cell *values* (not by trusting header-to-column alignment, which proved unreliable), and it
reads dates in the formats seen in World Bank documents.
"""
import html as _html
import re
from datetime import date, datetime

# --- dates -----------------------------------------------------------------------------------------------------------

_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d %b %Y", "%b %d, %Y", "%d-%B-%Y")
# Anything that looks like the start of a date: used to tell "a broken date" apart from "not a date at all".
_DATE_LIKE = re.compile(r"\b\d{4}-\d{1,2}(-\d{0,2})?\b|\b\d{1,2}-[A-Za-z]{3,9}-\d{2,4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def parse_date(s):
    """Parse one date string in any known format. Returns a date or None."""
    s = (s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def cell_dates(cell):
    """Dates in one table cell.

    Returns (dates, bad): `dates` is a list of parsed dates; `bad` is True when the cell looks like it holds a date but it
    cannot be read. Handles: one date; several dates separated by spaces; and one date wrapped across lines inside the
    cell (e.g. "2016-09-0 6" -> 2016-09-06).
    """
    text = re.sub(r"\s+", " ", (cell or "")).strip()
    if not text or not _DATE_LIKE.search(text):
        return [], False
    whole = parse_date(text)
    if whole:
        return [whole], False
    tokens = text.split(" ")
    parsed = [parse_date(t) for t in tokens]
    if all(parsed):
        return parsed, False
    joined = parse_date(text.replace(" ", ""))
    if joined:
        return [joined], False
    return [], True


# --- value-based field detection -------------------------------------------------------------------------------------

_squash = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())

METHODS = [
    "request for bids", "request for quotations", "request for proposals", "quality and cost-based selection",
    "quality-based selection", "least cost-based selection", "fixed budget-based selection",
    "consultant qualification-based selection", "individual consultant selection", "direct selection",
    "direct contracting", "shopping", "national competitive bidding", "international competitive bidding",
    "limited international bidding", "limited competitive bidding", "force account", "commercial practices",
    "selection based on consultants qualification", "single source selection", "quality based selection",
    "least cost selection", "fixed budget selection", "community participation",
]
STATUSES = [
    "signed", "canceled", "cancelled", "completed", "under implementation", "pending implementation", "draft",
    "under review", "cleared", "terminated", "not started", "in progress", "ongoing", "awarded", "pending",
]
_METHODS_SQ = [_squash(m) for m in METHODS]
_STATUSES_SQ = sorted((_squash(s) for s in STATUSES), key=len, reverse=True)


def find_review(cells):
    for c in cells:
        if c.strip() in ("Prior", "Post"):
            return c.strip()
    return ""


def find_method(cells):
    for c in cells:
        sq = _squash(c)
        if sq and any(m == sq or (len(sq) >= 12 and m.startswith(sq)) for m in _METHODS_SQ):
            return c.strip()
    return ""


def find_status(cells):
    for c in cells:
        sq = _squash(c)
        if sq and sq in _STATUSES_SQ:
            return c.strip()
    return ""


# --- one row ---------------------------------------------------------------------------------------------------------

def check_row(cells):
    """Map one activity row (list of cell strings) to fields plus a single verdict.

    `reason` is 'clean' or the FIRST failed rule. Deciding and explaining use the same code path, so they cannot disagree.
    """
    cells = [re.sub(r"\s+", " ", c or "").strip() for c in cells]
    ref = cells[0].split(" / ")[0].strip() if cells else ""
    description = cells[0].split(" / ", 1)[1].strip() if cells and " / " in cells[0] else ""
    dates, bad = [], []
    for i, c in enumerate(cells[1:], start=1):
        ds, is_bad = cell_dates(c)
        dates += ds
        if is_bad:
            bad.append((i, c))
    rec = {"ref": ref, "description": description, "review": find_review(cells), "method": find_method(cells), "status": find_status(cells),
           "n_dates": len(dates), "bad_dates": bad}
    rec["reason"] = reason(rec)
    return rec


def reason(rec):
    if not rec["ref"]:
        return "no ref"
    if rec["review"] not in ("Prior", "Post"):
        return "no review type"
    if not rec["method"]:
        return "no method"
    if not rec["status"]:
        return "no status"
    if not rec["n_dates"]:
        return "no dates"
    if rec["bad_dates"]:
        return "bad date"
    return "clean"


# --- date columns for tests (a)/(b) ----------------------------------------------------------------------------------

def date_column(names, rows, *need, exclude=()):
    """Index of the first column whose header contains all `need` words AND whose non-empty values are mostly (>= 50%)
    readable dates. The value check guards against a misaligned header pointing at the wrong column."""
    for i, n in enumerate(names):
        if not (all(k in n for k in need) and not any(x in n for x in exclude)):
            continue
        vals = [r[i] for r in rows if i < len(r) and (r[i] or "").strip()]
        if vals and sum(bool(cell_dates(v)[0]) for v in vals) / len(vals) >= 0.5:
            return i
    return None


def first_date(cell):
    ds, _ = cell_dates(cell)
    return min(ds) if ds else None


# --- HTML tables -----------------------------------------------------------------------------------------------------

def html_grid(s):
    """HTML table -> rows of cell texts, expanding colspan and rowspan so columns line up across rows."""
    grid, carry = [], {}  # carry: column -> (text, rows_left) from a rowspan above
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", s or "", flags=re.S | re.I):
        cells = re.findall(r"<t([dh])([^>]*)>(.*?)</t[dh]>", tr, flags=re.S | re.I)
        row, col, k = [], 0, 0
        while k < len(cells) or col in carry:
            if col in carry:
                txt, left = carry[col]
                row.append(txt)
                if left > 1:
                    carry[col] = (txt, left - 1)
                else:
                    del carry[col]
                col += 1
                continue
            _, attrs, raw = cells[k]
            k += 1
            txt = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
            cs = int((re.search(r"""colspan\s*=\s*["']?(\d+)""", attrs, re.I) or [0, 1])[1])
            rs = int((re.search(r"""rowspan\s*=\s*["']?(\d+)""", attrs, re.I) or [0, 1])[1])
            for _ in range(cs):
                if rs > 1:
                    carry[col] = (txt, rs - 1)
                row.append(txt)
                col += 1
        grid.append(row)
    return grid


GAP_START, GAP_END = date(2016, 7, 1), date(2019, 6, 30)  # World Bank FY2017-FY2019
