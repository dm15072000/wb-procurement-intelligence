"""Bronze -> silver transforms (pure Python, unit-tested).

Plain-language summary: raw API records come in many shapes. These functions turn each raw record into one clean,
typed row for a silver table, following docs/02_data_model.md. Missing values stay missing (None), never 0.
"""
import json
from collections import defaultdict

from src.entity_resolution import keys

ROADS_SECTOR = "Rural and Inter-Urban Roads"


def _sectors(p):
    out = []
    for i in range(1, 6):
        s = p.get(f"sector{i}")
        if isinstance(s, dict) and s.get("Name"):
            try:
                pct = int(s.get("Percent") or 0)
            except (TypeError, ValueError):
                pct = None
            out.append((s["Name"], pct))
    return out


def _amount(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def project_row(p):
    """Projects API record -> silver.projects row. Returns None if the project ID is invalid."""
    pid = keys.project_id(p.get("id"))
    if not pid:
        return None
    sectors = _sectors(p)
    cc = p.get("countrycode")
    cc = cc if isinstance(cc, list) else ([cc] if cc else [])
    approval = keys.parse_date(p.get("boardapprovaldate"))
    closing = keys.parse_date(p.get("closingdate"))
    fy = p.get("approvalfy")
    return {
        "project_id": pid,
        "project_name": p.get("project_name"),
        "country_iso2_list": [c for c in cc if c],
        "is_multi_country": len(cc) != 1,
        "region": p.get("regionname"),
        "approval_fy": int(fy) if str(fy or "").isdigit() else None,
        "board_approval_date": approval,
        "closing_date": closing,
        "status": p.get("status"),
        "sectors_json": json.dumps([{"name": n, "pct": pct} for n, pct in sectors]),
        "roads_pct": sum(pct or 0 for name, pct in sectors if name == ROADS_SECTOR),
        "ida_commitment_usd": _amount(p.get("idacommamt")),
        "ibrd_commitment_usd": _amount(p.get("ibrdcommamt")),
        "total_commitment_usd": _amount(p.get("totalcommamt")),
        "lending_instrument": p.get("lendinginstr"),
        "env_category": p.get("envassesmentcategorycode"),
        "implementing_agency": p.get("impagency"),
    }


def in_universe(row, min_roads_pct=25, fy_from=2014, fy_to=2021):
    """Training universe (CLAUDE.md): road projects (roads >= 25% of the project), approved FY2014-FY2021, any status."""
    return bool(row and row["roads_pct"] >= min_roads_pct and row["approval_fy"]
                and fy_from <= row["approval_fy"] <= fy_to)


def contracts_from_awards(rows, source):
    """DS00005 / DS01004 supplier rows -> (contracts, awards).

    DS00005 lists a joint venture as one row per member with the value split equally (verified), so one contract is
    many rows. Contracts get the summed amount and the supplier count; awards keep one row per supplier.
    """
    num_field = "wb_contract_number" if source == "DS00005" else "contract_number"
    amt_field = "supplier_contract_amount_usd" if source == "DS00005" else "supplier_contract_amount"
    grouped = defaultdict(list)
    for r in rows:
        ck = keys.contract_key(source, r.get(num_field))
        if ck:
            grouped[ck].append(r)
    contracts, awards = [], []
    for ck, rs in grouped.items():
        first = rs[0]
        signed = keys.parse_date(first.get("contract_signing_date"))
        amounts = [_amount(r.get(amt_field)) for r in rs]
        contracts.append({
            "contract_key": ck,
            "source_dataset": source,
            "project_id": keys.project_id(first.get("project_id")),
            "borrower_reference": first.get("borrower_contract_reference_number"),
            "ref_key": keys.ref_key(first.get("borrower_contract_reference_number")),
            "description": first.get("contract_description"),
            "signing_date": signed,
            "fiscal_year": first.get("fiscal_year") if isinstance(first.get("fiscal_year"), int) else (keys.fiscal_year(signed) if signed else None),
            "procurement_category": first.get("procurement_category"),
            "procurement_method": first.get("procurement_method"),
            "review_type": first.get("review_type") if source == "DS00005" else None,  # DS01004 has no such field
            "borrower_country": first.get("borrower_country"),
            "borrower_country_code": first.get("borrower_country_code"),
            "total_amount_usd": sum(a for a in amounts if a is not None) if any(a is not None for a in amounts) else None,
            "n_suppliers": len(rs),
        })
        for r, a in zip(rs, amounts):
            awards.append({
                "contract_key": ck,
                "raw_supplier_name": r.get("supplier"),
                "supplier_country": r.get("supplier_country"),
                "supplier_country_code": r.get("supplier_country_code"),
                "source_supplier_id": str(r["supplier_id"]) if r.get("supplier_id") not in (None, "") else None,
                "procurement_method": r.get("procurement_method"),
                "amount_usd": a,
            })
    return contracts, awards


def notice_row(n):
    """procnotices record -> silver.notices row."""
    from datetime import datetime
    try:
        nd = datetime.strptime((n.get("noticedate") or "")[:11], "%d-%b-%Y").date()
    except ValueError:
        nd = None
    pid = keys.project_id(n.get("project_id"))
    return {
        "notice_id": n.get("id"),
        "project_id": pid,
        "notice_type": n.get("notice_type"),
        "notice_date": nd,
        "notice_status": n.get("notice_status"),
        "bid_reference_no": n.get("bid_reference_no"),
        "tender_key": keys.tender_key(pid, n.get("bid_reference_no")),
        "bid_description": n.get("bid_description"),
        "procurement_group": n.get("procurement_group"),
        "procurement_method_code": n.get("procurement_method_code"),
        "notice_text": n.get("notice_text"),
    }


START_NOTICE_TYPES = ("Invitation for Bids", "Request for Expression of Interest")


def tender_durations(contracts, notices, max_days=1095):
    """Tender duration per contract (CLAUDE.md data rule): signing date - earliest matching IFB/REOI notice date, joined
    on project ID + normalized reference. Negative or > 3-year durations are dropped as data errors and COUNTED.

    Returns (rows, report): rows for silver.tender_durations; report with match and drop counts by review type.
    """
    starts = defaultdict(list)
    for n in notices:
        if n["notice_type"] in START_NOTICE_TYPES and n["tender_key"] and n["notice_date"]:
            starts[n["tender_key"]].append(n["notice_date"])
    rows = []
    report = defaultdict(lambda: {"contracts": 0, "matched": 0, "kept": 0, "dropped_negative": 0, "dropped_over_3y": 0})
    for c in contracts:
        rt = c["review_type"] or "unknown"
        report[rt]["contracts"] += 1
        tk = f"{c['project_id']}:{c['ref_key']}" if c["project_id"] and c["ref_key"] else None
        if not (tk and tk in starts and c["signing_date"]):
            continue
        report[rt]["matched"] += 1
        start = min(starts[tk])
        days = (c["signing_date"] - start).days
        if days < 0:
            report[rt]["dropped_negative"] += 1
            continue
        if days > max_days:
            report[rt]["dropped_over_3y"] += 1
            continue
        report[rt]["kept"] += 1
        rows.append({"contract_key": c["contract_key"], "tender_key": tk, "start_notice_date": start,
                     "signing_date": c["signing_date"], "duration_days": days, "review_type": c["review_type"]})
    return rows, {k: dict(v) for k, v in report.items()}


def fy_activity(contracts, fy_from, fy_to, group_keys=("project_id",)):
    """Contract counts and value per group and fiscal year, with the FY2017-FY2019 gap stored as NULL + flag.

    Every (group, FY) in range gets a row. Gap years: n_contracts = None, amount = None, coverage_gap = True. Non-gap years
    with no contracts are a real 0 (the datasets cover those years).
    """
    agg = defaultdict(lambda: [0, 0.0])
    groups = set()
    for c in contracts:
        g = tuple(c.get(k) for k in group_keys)
        groups.add(g)
        fy = c.get("fiscal_year")
        if fy is not None:
            agg[(g, fy)][0] += 1
            agg[(g, fy)][1] += c.get("total_amount_usd") or 0.0
    out = []
    for g in sorted(groups, key=str):
        for fy in range(fy_from, fy_to + 1):
            gap = keys.in_contract_gap(fy)
            n, amt = agg.get((g, fy), (0, 0.0))
            row = dict(zip(group_keys, g))
            row.update({"fiscal_year": fy, "coverage_gap": gap,
                        "n_contracts": None if gap else n, "amount_usd": None if gap else amt})
            out.append(row)
    return out


def governance_rows(api_page):
    """Indicators API page [meta, rows] -> silver.governance rows (value None = missing, flagged)."""
    rows = api_page[1] if isinstance(api_page, list) and len(api_page) > 1 and api_page[1] else []
    out = []
    for x in rows:
        iso3 = x.get("countryiso3code")
        if not iso3 or not str(x.get("date", "")).isdigit():
            continue
        out.append({"iso3": iso3, "year": int(x["date"]), "indicator": x["indicator"]["id"],
                    "value": x.get("value"), "is_missing": x.get("value") is None})
    return out


def country_rows(api_page):
    """Country API page -> silver.countries rows (aggregates excluded)."""
    rows = api_page[1] if isinstance(api_page, list) and len(api_page) > 1 else []
    out = []
    for x in rows:
        lt = (x.get("lendingType") or {}).get("value")
        if (x.get("region") or {}).get("value") == "Aggregates":
            continue
        out.append({"iso3": x["id"], "iso2": x.get("iso2Code"), "name": x.get("name"),
                    "region": (x.get("region") or {}).get("value"), "income_level": (x.get("incomeLevel") or {}).get("value"),
                    "lending_type": {"IDA": "IDA", "IBRD": "IBRD", "Blend": "Blend"}.get(lt, lt)})
    return out
