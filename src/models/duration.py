"""Tender-duration model helpers (docs/03_model_rag_design.md §1). Pure Python, unit-tested.

Plain-language summary: to predict how long a tender will take, every feature must be something we would have known
on the day the tender started (its first notice). This module builds those features, makes the time-based train/test
split, fits the simple baseline we must beat, and computes the metrics. The gradient-boosted model itself is fitted
in notebook 50 with scikit-learn.
"""
import math
from collections import defaultdict
from datetime import date

# Governance indicators used as features: WGI (always present) and two CPIA clusters (mostly IDA countries).
WGI = ("GOV_WGI_GE.EST", "GOV_WGI_RQ.EST", "GOV_WGI_RL.EST", "GOV_WGI_CC.EST")
CPIA = ("IQ.CPA.PUBS.XQ", "IQ.CPA.TRAN.XQ")
GOV_FEATURES = {"GOV_WGI_GE.EST": "wgi_ge", "GOV_WGI_RQ.EST": "wgi_rq", "GOV_WGI_RL.EST": "wgi_rl",
                "GOV_WGI_CC.EST": "wgi_cc", "IQ.CPA.PUBS.XQ": "cpia_pubs", "IQ.CPA.TRAN.XQ": "cpia_tran"}

CATEGORICAL = ["procurement_category", "procurement_method", "start_notice_type", "country_iso3", "lending_type"]
NUMERIC = ["log_amount_usd", "project_age_days", "roads_pct", "log_project_commitment_usd", "n_earlier_tenders",
           "fy_month", "wgi_ge", "wgi_rq", "wgi_rl", "wgi_cc", "cpia_pubs", "cpia_tran", "cpia_missing"]
GOVERNANCE_COLUMNS = ["wgi_ge", "wgi_rq", "wgi_rl", "wgi_cc", "cpia_pubs", "cpia_tran", "cpia_missing"]


def latest_published_year(t):
    """Latest indicator year that was public on date t. WGI and CPIA for year Y are released in the autumn of Y+1
    (WGI last update seen: 2026-09-25), so on/after 1 October of year t we may use Y = t.year - 1, before it only
    Y = t.year - 2. This is the leakage rule for governance features."""
    return t.year - 1 if (t.month, t.day) >= (10, 1) else t.year - 2


def governance_lookup(rows):
    """silver.governance rows (iso3, year, indicator, value) -> {(iso3, indicator): {year: value}} (missing skipped)."""
    out = defaultdict(dict)
    for r in rows:
        if r.get("value") is not None:
            out[(r["iso3"], r["indicator"])][int(r["year"])] = float(r["value"])
    return out


def governance_asof(lookup, iso3, t):
    """Governance features for a country as known on date t. Uses exactly the latest published year: no carrying
    forward of older years and no imputation. Missing values stay None; cpia_missing = 1 when both CPIA values are
    missing (CLAUDE.md: CPIA mainly covers IDA countries)."""
    year = latest_published_year(t)
    feats = {GOV_FEATURES[ind]: lookup.get((iso3, ind), {}).get(year) if iso3 else None for ind in WGI + CPIA}
    feats["cpia_missing"] = int(feats["cpia_pubs"] is None and feats["cpia_tran"] is None)
    feats["governance_year"] = year
    return feats


def _log(x):
    return math.log10(x) if x and x > 0 else None


def fiscal_month(d):
    """Month within the World Bank fiscal year: July = 1 ... June = 12."""
    return (d.month - 7) % 12 + 1


def count_earlier(sorted_dates, t):
    """Number of dates strictly before t in an ascending list (binary search)."""
    lo, hi = 0, len(sorted_dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if as_date(sorted_dates[mid]) < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def feature_row(duration, contract, project, start_notice_type, earlier_tender_dates, gov_lookup, lending_type):
    """One model row. duration: silver.tender_durations row; contract: silver.contracts row; project:
    silver.projects row; earlier_tender_dates: sorted start-notice dates of the project's tenders (any tender);
    lending_type: current IDA/IBRD/Blend of the borrower country. Everything is as of the start notice date t."""
    t = as_date(duration["start_notice_date"])
    iso3 = contract.get("borrower_country_iso3")
    approval = as_date(project["board_approval_date"]) if project.get("board_approval_date") else None
    row = {
        "contract_key": duration["contract_key"],
        "start_notice_date": t,
        "duration_days": duration["duration_days"],
        "review_type": duration.get("review_type"),
        "source_dataset": duration.get("source_dataset"),
        "project_id": contract.get("project_id"),
        "procurement_category": contract.get("procurement_category") or "unknown",
        "procurement_method": contract.get("procurement_method") or "unknown",
        "start_notice_type": start_notice_type or "unknown",
        "country_iso3": iso3 or "unknown",
        "lending_type": lending_type or "unknown",
        "log_amount_usd": _log(contract.get("total_amount_usd")),
        "project_age_days": (t - approval).days if approval else None,
        "roads_pct": project.get("roads_pct"),
        "log_project_commitment_usd": _log(project.get("total_commitment_usd")),
        "n_earlier_tenders": count_earlier(earlier_tender_dates, t),
        "fy_month": fiscal_month(t),
    }
    row.update(governance_asof(gov_lookup, iso3, t))
    return row


def time_split(rows, date_key="start_notice_date", test_share=0.2):
    """Sort by date; the latest test_share of rows is the test set. Rows on the cutoff date all go to test, so no
    tender in train starts on or after the first test date. Returns (train, test, cutoff_date)."""
    ordered = sorted(rows, key=lambda r: r[date_key])
    if len(ordered) < 2:
        return ordered, [], None
    cut_idx = max(1, int(len(ordered) * (1 - test_share)))
    cutoff = ordered[cut_idx][date_key]
    train = [r for r in ordered if r[date_key] < cutoff]
    test = [r for r in ordered if r[date_key] >= cutoff]
    return train, test, cutoff


def quantile(values, q):
    """Linear-interpolated quantile (q in [0, 1]) of a non-empty list."""
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


class GroupQuantileBaseline:
    """Baseline to beat: the training quantile of duration by (category, method), backing off to category and then to
    all rows when a group has fewer than min_n training rows."""

    LEVELS = (("procurement_category", "procurement_method"), ("procurement_category",), ())

    def __init__(self, q=0.5, min_n=5, target="duration_days"):
        self.q, self.min_n, self.target, self.table = q, min_n, target, {}

    def fit(self, rows):
        for level in self.LEVELS:
            groups = defaultdict(list)
            for r in rows:
                groups[tuple(r[k] for k in level)].append(r[self.target])
            for g, vals in groups.items():
                if len(vals) >= self.min_n or not level:
                    self.table[(level, g)] = (quantile(vals, self.q), len(vals))
        return self

    def predict_one(self, r):
        for level in self.LEVELS:
            hit = self.table.get((level, tuple(r[k] for k in level)))
            if hit:
                return hit[0]
        return None

    def predict(self, rows):
        return [self.predict_one(r) for r in rows]


def mae(y, p):
    pairs = [(a, b) for a, b in zip(y, p) if b is not None]
    return sum(abs(a - b) for a, b in pairs) / len(pairs) if pairs else None


def median_ae(y, p):
    errs = [abs(a - b) for a, b in zip(y, p) if b is not None]
    return quantile(errs, 0.5) if errs else None


def coverage(y, upper):
    """Share of actual values at or below the predicted upper quantile (P80 should give about 0.80)."""
    pairs = [(a, b) for a, b in zip(y, upper) if b is not None]
    return sum(a <= b for a, b in pairs) / len(pairs) if pairs else None


def pinball(y, p, q):
    """Mean pinball (quantile) loss: the proper score for a quantile prediction."""
    pairs = [(a, b) for a, b in zip(y, p) if b is not None]
    if not pairs:
        return None
    return sum(max(q * (a - b), (q - 1) * (a - b)) for a, b in pairs) / len(pairs)


def evaluate(y, p50, p80=None, p20=None):
    """All headline metrics for one set of predictions."""
    out = {"n": len(y), "mae_p50": mae(y, p50), "median_ae_p50": median_ae(y, p50), "pinball_50": pinball(y, p50, 0.5)}
    if p80 is not None:
        out.update({"coverage_p80": coverage(y, p80), "pinball_80": pinball(y, p80, 0.8)})
    if p20 is not None:
        out["share_below_p20"] = sum(a < b for a, b in zip(y, p20) if b is not None) / max(1, len(y))
    return out


def evaluate_by(rows, preds, key, **pred_lists):
    """Metrics per value of `key` (e.g. country_iso3): {value: metrics}. pred_lists: p80=[...], p20=[...]."""
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[r[key]].append(i)
    out = {}
    for g, idx in sorted(groups.items(), key=lambda kv: str(kv[0])):
        y = [rows[i]["duration_days"] for i in idx]
        extra = {k: [v[i] for i in idx] for k, v in pred_lists.items()}
        out[g] = evaluate(y, [preds[i] for i in idx], **extra)
    return out


def promote(challenger, champion, min_cov=0.70, max_cov=0.90):
    """Champion/challenger rule (docs/03 §1): promote if P50 MAE is no worse and P80 coverage is in [min_cov, max_cov].
    Returns (decision bool, reason str)."""
    cov = challenger.get("coverage_p80")
    if cov is None or not min_cov <= cov <= max_cov:
        return False, f"P80 coverage {cov} outside [{min_cov}, {max_cov}]"
    if champion is None:
        return True, "no current champion"
    if challenger["mae_p50"] <= champion["mae_p50"]:
        return True, f"MAE {challenger['mae_p50']:.1f} <= champion {champion['mae_p50']:.1f}"
    return False, f"MAE {challenger['mae_p50']:.1f} > champion {champion['mae_p50']:.1f}"


def days_to_text(p20, p50, p80):
    """Plain-language range for the app: 'about 7 months (typically 5–9; 1 in 5 takes longer than 9)'."""
    m = lambda d: max(1, round(d / 30.4))
    return (f"about {m(p50)} months (typically {m(p20)}–{m(p80)}; 1 in 5 tenders like this takes longer than "
            f"{m(p80)} months)")


def as_date(x):
    """Accept a date, a timestamp or a 'YYYY-MM-DD' string (Spark rows give dates or timestamps; JSON gives strings).
    A timestamp is reduced to its date: datetime is a subclass of date, and mixing the two in arithmetic fails
    (notebook 70, 2026-10-22: gold.rebid_labels.start_date is a timestamp)."""
    from datetime import datetime
    if isinstance(x, datetime):
        return x.date()
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])
