"""Per-project coverage report for the demo-slice projects, with outlier flags (unit-tested).

Plain-language summary: for each demo project we count its contracts per dataset and measure how complete they look
against an independent source, the project's published Contract Award notices (share whose reference matches a
contract). Then we flag projects that look unusual, so nobody reads a thin record as "little happened":

  low_coverage           measured coverage < 50% (with at least 5 award notices to judge from)
  coverage_outlier       coverage below Q1 - 1.5 x IQR of the slice (a statistical outlier among the 16)
  few_award_notices      fewer than 5 award notices: coverage cannot be judged (reported, not a failure)
  empty_full_year        a FULL-coverage fiscal year (FY2020+) strictly between the approval and closing years with 0
                         contracts
  signed_before_approval contracts signed before Board approval. Usually advance contracting / retroactive financing
                         (e.g. 215 Madhya Pradesh rural-road works signed Dec 2017 - Mar 2018 for P157054): a check,
                         not an error
  signed_after_closing   contracts signed more than 180 days after the closing date
  no_contracts           no contract at all in any dataset
"""
import statistics
from collections import Counter, defaultdict

from src.entity_resolution import keys

MIN_NOTICES = 5
LOW_COVERAGE = 0.5
AFTER_CLOSING_GRACE_DAYS = 180


def _quartiles(vals):
    v = sorted(vals)
    if len(v) < 4:
        return None, None
    q = statistics.quantiles(v, n=4, method="inclusive")
    return q[0], q[2]


def report(projects, contracts, notices):
    """projects: dicts with project_id, country, board_approval_date, closing_date (dates or None).
    contracts: silver.contracts-like dicts (any dataset). notices: silver.notices-like dicts.
    Returns one row per project, each with a list of flags."""
    by_p = defaultdict(list)
    for c in contracts:
        by_p[c.get("project_id")].append(c)
    have = {f"{c['project_id']}:{c['ref_key']}" for c in contracts if c.get("project_id") and c.get("ref_key")}
    award = defaultdict(lambda: [0, 0])
    for n in notices:
        if n.get("notice_type") == "Contract Award" and n.get("tender_key"):
            award[n["project_id"]][0] += 1
            award[n["project_id"]][1] += n["tender_key"] in have

    rows = []
    for p in projects:
        pid, cs = p["project_id"], by_p.get(p["project_id"], [])
        appr, close = p.get("board_approval_date"), p.get("closing_date")
        n_aw, n_match = award[pid]
        fys = Counter(c.get("fiscal_year") for c in cs if c.get("fiscal_year"))
        # Whole years only: the approval and closing fiscal years are partial, and a quiet first or last year is normal.
        inner = range(keys.fiscal_year(appr) + 1, keys.fiscal_year(close)) if appr and close else range(0)
        empty_full = [fy for fy in inner if keys.coverage_status(fy) == "full" and fys.get(fy, 0) == 0]
        before = sum(1 for c in cs if appr and c.get("signing_date") and c["signing_date"] < appr)
        after = sum(1 for c in cs if close and c.get("signing_date")
                    and (c["signing_date"] - close).days > AFTER_CLOSING_GRACE_DAYS)
        rows.append({
            "project_id": pid, "country": p.get("country"),
            "board_approval_date": appr, "closing_date": close,
            "contracts": len(cs),
            "contracts_by_dataset": dict(Counter(c["source_dataset"] for c in cs)),
            "contracts_by_coverage_status": dict(Counter(c.get("coverage_status") for c in cs)),
            "total_amount_usd": round(sum(c.get("total_amount_usd") or 0 for c in cs), 2),
            "award_notices": n_aw, "award_notices_matched": n_match,
            "coverage_pct": round(n_match / n_aw, 3) if n_aw else None,
            "empty_full_years": empty_full,
            "signed_before_approval": before, "signed_after_closing": after,
        })

    judged = [r["coverage_pct"] for r in rows if r["award_notices"] >= MIN_NOTICES]
    q1, q3 = _quartiles(judged)
    fence = q1 - 1.5 * (q3 - q1) if q1 is not None else None
    for r in rows:
        f = []
        if not r["contracts"]:
            f.append("no_contracts")
        if r["award_notices"] < MIN_NOTICES:
            f.append("few_award_notices")
        else:
            if r["coverage_pct"] < LOW_COVERAGE:
                f.append("low_coverage")
            if fence is not None and r["coverage_pct"] < fence:
                f.append("coverage_outlier")
        if r["empty_full_years"]:
            f.append("empty_full_year")
        if r["signed_before_approval"]:
            f.append("signed_before_approval")
        if r["signed_after_closing"]:
            f.append("signed_after_closing")
        r["flags"] = f
        r["outlier_fence"] = None if fence is None else round(fence, 3)
    return rows
