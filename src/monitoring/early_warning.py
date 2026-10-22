"""Early-warning flags for execution and monitoring (docs/03_model_rag_design.md §6, L3). Unit-tested.

Plain-language summary: for a running project, raise a flag when something is going off track, and always say why
and from which record: a tender open longer than 80% of similar tenders took; money disbursed far behind the time
elapsed; an ISR rating that dropped to Moderately Unsatisfactory or worse; a recent report mentioning a procurement
problem. Thresholds are proposed and stated; none is tuned.
"""
from datetime import date, timedelta

DISBURSEMENT_GAP = 0.25     # flag when share disbursed < share of time elapsed - 0.25 [P]
WEAK_RATING = 3             # Moderately Unsatisfactory or worse on the 6-point scale
RECENT_DAYS = 365


def _d(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def overdue_tenders(open_tenders, today):
    """open_tenders: dicts with tender_key, start_notice_date, p80_days (predicted). Flag when open > P80."""
    out = []
    for t in open_tenders:
        if t.get("p80_days") is None or not t.get("start_notice_date"):
            continue
        age = (_d(today) - _d(t["start_notice_date"])).days
        if age > t["p80_days"]:
            out.append({"flag": "tender_overdue", "key": t["tender_key"],
                        "reason": (f"check: no signed contract found after {age} days; 80% of similar tenders were "
                                   f"signed within {round(t['p80_days'])} days (may also be a contract that did not "
                                   f"match its notice)"),
                        "evidence": f"notice of {t['start_notice_date']}; duration model P80"})
    return out


def disbursement_lag(snapshots, approval, closing, today, commitment=None):
    """snapshots: dicts (end_of_period, disbursed_usd, original_principal_usd) for one project (all its loans and
    credits summed per date). Linear time benchmark [A]: the planned schedule is not available as data."""
    if not snapshots or not approval or not closing:
        return []
    by_date = {}
    for s in snapshots:
        d = _d(s["end_of_period"])
        agg = by_date.setdefault(d, [0.0, 0.0])
        agg[0] += s.get("disbursed_usd") or 0.0
        agg[1] += s.get("original_principal_usd") or 0.0
    last = max(d for d in by_date if d <= _d(today)) if any(d <= _d(today) for d in by_date) else None
    if last is None:
        return []
    disbursed, principal = by_date[last]
    total = commitment or principal
    span = (_d(closing) - _d(approval)).days
    if not total or span <= 0:
        return []
    time_share = min(1.0, max(0.0, (last - _d(approval)).days / span))
    disb_share = disbursed / total
    if disb_share < time_share - DISBURSEMENT_GAP:
        return [{"flag": "disbursement_lag", "key": str(last),
                 "reason": f"{disb_share:.0%} disbursed after {time_share:.0%} of the implementation period",
                 "evidence": f"disbursement snapshot {last}; linear benchmark (assumption: no planned schedule in data)"}]
    return []


def rating_flags(observations, today):
    """observations: extracted ISR ratings (dimension, score, value, doc_date, chunk_id). Flags the latest ISR
    rating per dimension when it is MU or worse."""
    latest = {}
    for o in observations:
        if o.get("doc_type", "Implementation Status and Results Report") != "Implementation Status and Results Report":
            continue
        if _d(o["doc_date"]) > _d(today):
            continue
        if o["dimension"] not in latest or _d(o["doc_date"]) > _d(latest[o["dimension"]]["doc_date"]):
            latest[o["dimension"]] = o
    return [{"flag": "weak_isr_rating", "key": dim,
             "reason": f"latest ISR {dim.replace('_', ' ')} rating: {o['value']}",
             "evidence": f"ISR {o['doc_date']}, chunk {o.get('chunk_id')}"}
            for dim, o in sorted(latest.items()) if o["dimension"] in ("outcome", "implementation", "procurement")
            and o["score"] <= WEAK_RATING]


def event_flags(events, today):
    """Extracted procurement events (event_type, doc_date, quote, chunk_id) in the last RECENT_DAYS."""
    since = _d(today) - timedelta(days=RECENT_DAYS)
    return [{"flag": "procurement_problem_reported", "key": e["event_type"],
             "reason": f"{e['event_type'].replace('_', ' ')} reported: \"{(e.get('quote') or '')[:160]}\"",
             "evidence": f"{e.get('doc_type')} {e['doc_date']}, chunk {e.get('chunk_id')}"}
            for e in events if since <= _d(e["doc_date"]) <= _d(today)]
