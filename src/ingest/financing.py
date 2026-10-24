"""Project development objectives and financing sources from the Projects API (2026-10-24). Unit-tested.

Plain-language summary: the first ingestion asked the Projects API for a fixed list of fields and missed two that the
assessment names: the project development objective (PDO) and the financiers, which show co-financing. The API returns
financiers as parallel lists (`financier_loan`, `financierdesc`, `financiertype`), repeated once per loan line, so they
are zipped and de-duplicated. A financier whose type is not IBRD or IDA (a trust fund, another lender, the government)
is co-financing.
"""
BANK_TYPES = {"IBRD", "IDA"}
FIELDS = "id,pdo,financier_loan,financierdesc,financiertype,grant_usd_amt,totalamt,totalcommamt"


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def pdo_row(project):
    """(project_id, pdo text or None)."""
    text = (project.get("pdo") or "").strip() or None
    return project.get("id"), text


def financing_rows(project):
    """One row per distinct financier of a project: project_id, loan_code, financier, financier_type, is_cofinancing.
    The API's lists are parallel; a shorter list is padded with None (never guessed)."""
    loans, names, types = (_list(project.get(k)) for k in ("financier_loan", "financierdesc", "financiertype"))
    n = max(len(loans), len(names), len(types))
    seen, out = set(), []
    for i in range(n):
        loan = (loans[i] if i < len(loans) else None)
        name = (names[i] if i < len(names) else None)
        typ = (types[i] if i < len(types) else None)
        key = (loan, name, typ)
        if key in seen or not any(key):
            continue
        seen.add(key)
        out.append({"project_id": project.get("id"), "loan_code": loan, "financier": name, "financier_type": typ,
                    "is_cofinancing": None if typ is None else (str(typ).strip().upper() not in BANK_TYPES)})
    return out
