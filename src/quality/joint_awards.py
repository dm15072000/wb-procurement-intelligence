"""Joint awards: do multi-supplier rows hold each member's SHARE or the full amount REPEATED? (unit-tested)

Plain-language summary: a contract won by a joint venture appears as one row per member. If each row holds the
member's share, the contract total is the sum of the rows. If each row repeats the full price, summing would double
it. We test this against an independent source: the "Signed Contract Price" in the published Contract Award notice.

Notice prices are usually in local currency (INR, CNY, XOF ...), so we compare like with like: single-supplier
contracts of the same project and currency give the exchange rate the dataset used (USD amount / notice price). For a
joint contract we then ask which is closer to the notice price: the SUM of the rows, or ONE row. Closer is measured on a
log scale, so "half" and "double" are equally far.

Measured 2026-10-22 on the 16 demo projects (docs/02_data_model.md §2c): DS00005 28 of 29 decidable joint contracts
and DS04691 16 of 17 are "sum of rows = price". DS01004 never has more than one row per contract (0 of 6,589
transport contracts): a joint venture is one row with a combined name and the full amount.
"""
import math
import re
import statistics
from collections import Counter, defaultdict

PRICE = re.compile(r"Signed Contract [Pp]rice:?\s*([A-Z]{3})\s*([\d,]+(?:\.\d+)?)")


def clean_notice_text(html):
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"&nbsp;|&\w+;", " ", t)
    return re.sub(r"\s+", " ", t)


def notice_prices(notice_text):
    """Distinct (currency, amount) signed prices in an award notice. A notice may list the same JV price once per
    member; duplicates are removed so they are not added twice."""
    out = []
    for cur, amt in PRICE.findall(clean_notice_text(notice_text)):
        v = float(amt.replace(",", ""))
        if v > 0 and (cur, v) not in out:
            out.append((cur, v))
    return out


def verdicts(contracts, awards, notices):
    """contracts: dicts with contract_key, source_dataset, project_id, ref_key, n_suppliers.
    awards:    dicts with contract_key, amount_row_usd (the value as published; amount_usd is used if absent).
    notices:   dicts with notice_type, tender_key, notice_text.
    Returns (rows, summary): one row per joint contract with an award notice; summary counts per dataset.
    Verdicts: 'shares' (sum of rows matches the price), 'repeated' (one row matches), 'no_reference_rate'."""
    prices = {}
    for n in notices:
        if "Contract Award" in (n.get("notice_type") or "") and n.get("tender_key"):
            p = notice_prices(n.get("notice_text"))
            if p:
                prices[n["tender_key"]] = p
    amounts = defaultdict(list)
    for a in awards:
        amounts[a["contract_key"]].append(a["amount_row_usd"] if "amount_row_usd" in a else a.get("amount_usd"))
    tk = lambda c: f"{c['project_id']}:{c['ref_key']}" if c.get("project_id") and c.get("ref_key") else None

    rates = defaultdict(list)                              # (dataset, project, currency) -> usd per unit
    for c in contracts:
        p, amts = prices.get(tk(c)), amounts.get(c["contract_key"], [])
        if p and len(p) == 1 and len(amts) == 1 and amts[0]:
            rates[(c["source_dataset"], c["project_id"], p[0][0])].append(amts[0] / p[0][1])

    rows, summary = [], defaultdict(Counter)
    for c in contracts:
        amts = [a for a in amounts.get(c["contract_key"], []) if a]
        if len(amts) < 2:
            continue
        ds = c["source_dataset"]
        summary[ds]["joint_contracts"] += 1
        summary[ds]["joint_all_rows_equal" if len(set(amts)) == 1 else "joint_rows_differ"] += 1
        p = prices.get(tk(c))
        if not p:
            continue
        summary[ds]["joint_with_award_notice"] += 1
        cur, price = p[0][0], sum(v for cu, v in p if cu == p[0][0])
        rl = rates.get((ds, c["project_id"], cur))
        if cur != "USD" and not rl:
            verdict, ratio_sum, ratio_one = "no_reference_rate", None, None
        else:
            usd_price = price * (1.0 if cur == "USD" else statistics.median(rl))
            ratio_sum, ratio_one = sum(amts) / usd_price, max(amts) / usd_price
            verdict = "shares" if abs(math.log(ratio_sum)) <= abs(math.log(ratio_one)) else "repeated"
        summary[ds][f"verdict_{verdict}"] += 1
        rows.append({"contract_key": c["contract_key"], "source_dataset": ds, "project_id": c["project_id"],
                     "n_rows": len(amts), "row_amounts_usd": amts, "notice_currency": cur, "notice_price": price,
                     "sum_over_price": None if ratio_sum is None else round(ratio_sum, 3),
                     "one_row_over_price": None if ratio_one is None else round(ratio_one, 3), "verdict": verdict})
    return rows, {ds: dict(v) for ds, v in summary.items()}


def repeated_share(summary_for_dataset):
    """Share of decidable joint contracts whose rows look REPEATED (None if nothing was decidable)."""
    s, r = summary_for_dataset.get("verdict_shares", 0), summary_for_dataset.get("verdict_repeated", 0)
    return r / (s + r) if s + r else None
