"""One standard set of procurement categories and methods across DS00005, DS04691 and DS01004 (unit-tested).

Plain-language summary: the three contract datasets come from different eras and use different words for the same
thing. DS01004 (FY2001-2016, the 2004/2011 Guidelines) says "Civil Works", "International Competitive Bidding",
"Shopping"; the STEP datasets (FY2017+, the 2016 Procurement Framework) say "Works", "Request for Bids", "Request for
Quotations". Models and rates group contracts by category and method, so the same thing must have the same name.

Every value below was SEEN in the data (transport-sector extract of all three datasets, pulled 2026-10-22; counts in
docs/02_data_model.md §2b). A value not in the table maps to "other" with mapped=False, and the data-quality notebook
fails if any universe contract has one, so a new spelling is noticed instead of silently grouped. The original value is
always kept next to the standard one.
"""
import re

UNKNOWN = "unknown"
OTHER = "other"

CATEGORY_MAP = {
    "works": "Works",
    "civil works": "Works",                     # DS01004 name for the same category
    "goods": "Goods",
    "consultant services": "Consultant Services",
    "non-consulting services": "Non-consulting Services",
}

# raw (folded) -> (standard method, family, market approach or None)
# Families: open_competitive, limited_competitive, consultant_competitive, individual, direct, other.
METHOD_MAP = {
    "request for bids": ("Request for Bids", "open_competitive", None),
    "international competitive bidding": ("Request for Bids", "open_competitive", "international"),
    "national competitive bidding": ("Request for Bids", "open_competitive", "national"),
    "request for proposals": ("Request for Proposals", "open_competitive", None),
    "e-auctions": ("E-Auction", "open_competitive", None),
    "limited international bidding": ("Limited Bidding", "limited_competitive", "international"),
    "request for quotations": ("Request for Quotations", "limited_competitive", None),
    "shopping": ("Request for Quotations", "limited_competitive", None),
    "national shopping": ("Request for Quotations", "limited_competitive", "national"),
    "international shopping": ("Request for Quotations", "limited_competitive", "international"),
    "quality and cost-based selection": ("QCBS", "consultant_competitive", None),
    "quality based selection": ("QBS", "consultant_competitive", None),
    "fixed budget selection": ("FBS", "consultant_competitive", None),
    "least cost selection": ("LCS", "consultant_competitive", None),
    "consultant qualification selection": ("CQS", "consultant_competitive", None),   # source has a double space
    "selection based on consultant's qualification": ("CQS", "consultant_competitive", None),
    "individual consultant selection": ("Individual Consultant Selection", "individual", None),
    "direct selection": ("Direct Selection", "direct", None),
    "single source selection": ("Direct Selection", "direct", None),
    "un agencies (direct)": ("UN Agencies", "direct", None),
    "force account": ("Force Account", "other", None),
    "community driven development": ("Community-Driven Development", "other", None),
    "non-profit organizations": ("Non-Profit Organizations", "other", None),
    "service delivery contracts": ("Service Delivery Contracts", "other", None),
    # Seen only in the full-data run of notebook 11 (2026-10-22), outside the transport extract: 233 contracts.
    "commercial practices": ("Commercial Practices", "other", None),
    "comercial practices": ("Commercial Practices", "other", None),      # misspelling in the source (1 contract)
    "public private partnership": ("Public-Private Partnership", "other", None),
    "procurement agents": ("Procurement Agents", "other", None),
    "imports": ("Imports", "other", None),
    "alternative procurement arrangement": ("Alternative Procurement Arrangement", "other", None),
}


def _fold(raw):
    """Lower case, curly quotes straightened, runs of spaces collapsed ('Consultant Qualification  Selection')."""
    s = (raw or "").replace("’", "'").replace("‘", "'").strip().lower()
    return re.sub(r"\s+", " ", s)


def category(raw):
    """-> (standard category, mapped). Empty -> ('unknown', True): missing is not an unmapped spelling."""
    f = _fold(raw)
    if not f:
        return UNKNOWN, True
    return (CATEGORY_MAP[f], True) if f in CATEGORY_MAP else (OTHER, False)


def method(raw):
    """-> {"method", "family", "market", "mapped"}."""
    f = _fold(raw)
    if not f:
        return {"method": UNKNOWN, "family": UNKNOWN, "market": None, "mapped": True}
    if f in METHOD_MAP:
        m, fam, mkt = METHOD_MAP[f]
        return {"method": m, "family": fam, "market": mkt, "mapped": True}
    return {"method": OTHER, "family": OTHER, "market": None, "mapped": False}


def mapping_table():
    """Rows for the docs / the data-quality notebook: raw value -> standard values."""
    rows = [{"field": "procurement_category", "raw": k, "standard": v, "family": None, "market": None}
            for k, v in CATEGORY_MAP.items()]
    rows += [{"field": "procurement_method", "raw": k, "standard": m, "family": fam, "market": mkt}
             for k, (m, fam, mkt) in METHOD_MAP.items()]
    return rows
