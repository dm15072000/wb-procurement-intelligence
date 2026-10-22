"""Re-bid / re-tender labels and rates (docs/03_model_rag_design.md §2a). Pure Python, unit-tested.

Plain-language summary: no World Bank dataset has a "this tender was re-bid" column (verified, H2). So a tender is
labelled re-bid when the public record shows it: the same tender was advertised twice, a notice was cancelled, a
notice says "re-bid" / "re-tender" / "relance", or the Procurement Plan says so. Every label keeps its reasons, so
anyone can check it.

Re-bids are rare, so a classifier may have too little to learn from. The default estimate is a smoothed rate: "how
often did similar tenders get re-bid?", where a small group is pulled toward its larger parent group (Beta-binomial
shrinkage), and every rate comes with its count and a credible interval.
"""
import re
from collections import defaultdict

REBID_PATTERNS = [
    r"\bre[\s-]?bid", r"\bre[\s-]?tender", r"\bre[\s-]?advertis", r"\bre[\s-]?invit", r"\bre[\s-]?launch",
    r"\bsecond (call|invitation|round of bidding)", r"\bfailed (tender|bidding)", r"\bbidding (was )?cancel",
    r"\brelance", r"\bappel d.offres (infructueux|relanc)", r"\bnouvel appel d.offres", r"\bdeclared unsuccessful",
]
_REBID = re.compile("|".join(REBID_PATTERNS), re.I)
MIN_GAP_DAYS = 30   # two IFB notices for the same tender this far apart = advertised again, not a same-week correction


def mentions_rebid(text):
    """The first re-bid phrase found in the text, or None."""
    m = _REBID.search(text or "")
    return m.group(0) if m else None


def tender_label(notices, plan_labels=()):
    """Label one tender.

    notices: that tender's notices (dicts with notice_type, notice_date, notice_status, bid_description, notice_text).
    plan_labels: Procurement Plan activity labels with the same project + reference (is_cancelled, mentions_rebid).
    Returns {"rebid": bool, "reasons": [...]}.
    """
    reasons = []
    ifb_dates = sorted(n["notice_date"] for n in notices
                       if n.get("notice_type") == "Invitation for Bids" and n.get("notice_date"))
    if len(ifb_dates) > 1 and (ifb_dates[-1] - ifb_dates[0]).days >= MIN_GAP_DAYS:
        reasons.append(f"ifb_notices:{len(ifb_dates)}")
    if any((n.get("notice_status") or "").lower().startswith("cancel") for n in notices):
        reasons.append("notice_cancelled")
    for n in notices:
        hit = mentions_rebid(n.get("bid_description")) or mentions_rebid(n.get("notice_text"))
        if hit:
            reasons.append(f"text:{hit.lower()}")
            break
    for p in plan_labels:
        if p.get("is_cancelled") or p.get("mentions_rebid"):
            reasons.append("plan:" + ("cancelled" if p.get("is_cancelled") else "rebid"))
            break
    return {"rebid": bool(reasons), "reasons": reasons}


def beta_interval(a, b, level=0.9):
    """Central credible interval of Beta(a, b). Uses scipy when available (it is on Databricks); otherwise a normal
    approximation, clipped to [0, 1]."""
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    try:
        from scipy.stats import beta
        return float(beta.ppf(lo_q, a, b)), float(beta.ppf(hi_q, a, b))
    except ImportError:  # pragma: no cover - scipy is present locally and on Databricks
        mean = a / (a + b)
        sd = (a * b / ((a + b) ** 2 * (a + b + 1))) ** 0.5
        z = 1.6449 if level == 0.9 else 1.96
        return max(0.0, mean - z * sd), min(1.0, mean + z * sd)


class HierarchicalRates:
    """Smoothed re-bid rates. Levels go from broad to specific; each group's rate is shrunk toward its parent's rate
    with prior strength `strength` (as if we had seen `strength` extra tenders at the parent's rate):
        rate = (positives + strength * parent_rate) / (n + strength)
    so a group of 3 tenders barely moves away from its parent, while a group of 300 speaks for itself."""

    DEFAULT_LEVELS = ((), ("procurement_category",), ("procurement_category", "procurement_method"),
                      ("country_iso3", "procurement_category", "procurement_method"))

    def __init__(self, levels=DEFAULT_LEVELS, strength=20.0, label="rebid"):
        self.levels, self.strength, self.label = levels, strength, label
        self.table = {}

    def fit(self, rows):
        counts = defaultdict(lambda: [0, 0])
        for r in rows:
            for lvl in self.levels:
                c = counts[(lvl, tuple(r.get(k) for k in lvl))]
                c[0] += int(bool(r[self.label]))
                c[1] += 1
        # Parents are processed before children because levels are ordered broad -> specific.
        for i, lvl in enumerate(self.levels):
            for (l, g), (pos, n) in counts.items():
                if l != lvl:
                    continue
                if i == 0:
                    a, b = pos + 1.0, n - pos + 1.0                      # uniform prior for the global rate
                else:
                    parent_lvl = self.levels[i - 1]
                    parent = self.table[(parent_lvl, tuple(g[lvl.index(k)] for k in parent_lvl))]
                    p = parent["rate"]
                    a, b = pos + self.strength * p, n - pos + self.strength * (1 - p)
                lo, hi = beta_interval(a, b)
                self.table[(lvl, g)] = {"rate": a / (a + b), "positives": pos, "n": n, "ci90": (lo, hi),
                                        "level": "+".join(lvl) or "all"}
        return self

    def predict_one(self, r):
        """Most specific level whose group was seen in training (falls back toward 'all')."""
        for lvl in reversed(self.levels):
            hit = self.table.get((lvl, tuple(r.get(k) for k in lvl)))
            if hit:
                return hit
        return None


def rate_to_text(res):
    """'3 of 41 similar tenders were re-bid (smoothed rate 6%, 90% interval 2–15%; group: category+method)'."""
    if not res:
        return "no comparable tenders"
    lo, hi = res["ci90"]
    return (f"{res['positives']} of {res['n']} similar tenders were re-bid (smoothed rate {res['rate']:.0%}, 90% "
            f"interval {lo:.0%}–{hi:.0%}; compared within: {res['level']})")
