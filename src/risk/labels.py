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

# Notices that MODIFY an existing invitation (corrigendum, addendum, amendment, deadline extension, revision) are not a
# new round of bidding, so they never count toward the "more than one IFB" rule (checkpoint d change 6, 2026-10-21).
# Only phrases about the NOTICE itself: "extension of the road" or "road modification" are works, not amendments.
_NOTICE_WORDS = r"(notice|invitation|ifb|ifq|reoi|bidding document|bid|deadline|closing|submission|opening|date)"
MODIFICATION_PATTERNS = [
    r"\bcorrigend", r"\baddend", r"\bamendment (no\.?|number|\d)", r"\bamendment (to|of) (the )?" + _NOTICE_WORDS,
    r"\b(time|date|deadline|bid|submission|closing) extension", r"\bextension of (the )?(time|" + _NOTICE_WORDS[1:],
    r"\b(deadline|closing date|submission date|bid opening)\b.{0,40}\b(extend|chang|postpon|revis)",
    r"\brevised (notice|invitation|ifb|reoi|bidding)", r"\berratum", r"\brectificatif", r"\badditif",
    r"\bprorogation", r"\breport de (la )?date", r"\bmodification (of|to|de) (the |l.|la )?" + _NOTICE_WORDS,
]
_MODIFICATION = re.compile("|".join(MODIFICATION_PATTERNS), re.I)


# Guards added 2026-10-22 after the first full run of notebook 51, where 120 of 171 positives were false:
#  - a NAME, not an event: P161706 is the "Projet de Relance et de Developpement de la Region du Lac Tchad" (relance =
#    recovery), so every one of its notices said "relance" (86 of 88 tenders labelled re-bid);
#  - a CONDITION, not an event: P160223 (Lebanon) prints "Not awarded Lots ... will be subject to re-bidding" in every
#    invitation (34 of 43 tenders).
# A match is skipped when it is part of a project / programme name, or when the words just before it make it future or
# conditional. Bare "relance" counts only next to a procurement word (avis, appel d'offres, DAO, consultation, ...).
_NAME_BEFORE = re.compile(r"\b(projet|project|programme|program|plan)\s+(de|d'|of|for)?\s*(la\s+)?$", re.I)
_CONDITIONAL_BEFORE = re.compile(r"\b(will|may|shall|could|would|might|to)\s+(be\s+)?(subject\s+to\s+)?(a\s+)?$|"
                                 r"\bsubject\s+to\s+(a\s+)?$|\bif\b[^.]{0,60}$|\bin case\b[^.]{0,60}$", re.I)
_PROCUREMENT_WORD = re.compile(r"avis|appel|offres|dao|consultation|march|lot|ifb|bid|n\s*[°o]", re.I)


def _clean(text):
    import html
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t)


def mentions_rebid(text):
    """The first re-bid phrase that reports an actual re-bid, or None (names and conditions are skipped)."""
    t = _clean(text)
    for m in _REBID.finditer(t):
        before = t[max(0, m.start() - 60):m.start()]
        if _NAME_BEFORE.search(before) or _CONDITIONAL_BEFORE.search(before):
            continue
        if m.group(0).lower().startswith("relance"):
            around = t[max(0, m.start() - 40):m.end() + 40]
            if not _PROCUREMENT_WORD.search(around.replace(m.group(0), " ")) or \
                    re.match(r"\s+et\s+de\s+d[eé]veloppement", t[m.end():m.end() + 30], re.I):
                continue
        return m.group(0)
    return None


def is_modification(notice):
    """True if the notice amends / extends / corrects an earlier one: status 'Revised', or a modification phrase in
    its description or the first 600 characters of its text (the title area, where notices state their purpose)."""
    if (notice.get("notice_status") or "").lower().startswith("revis"):
        return True
    head = f"{notice.get('bid_description') or ''} {(notice.get('notice_text') or '')[:600]}"
    return bool(_MODIFICATION.search(head))


def tender_label(notices, plan_labels=()):
    """Label one tender.

    notices: that tender's notices (dicts with notice_type, notice_date, notice_status, bid_description, notice_text).
    plan_labels: Procurement Plan activity labels with the same project + reference (is_cancelled, mentions_rebid).
    Returns {"rebid", "cancellation", "rebid_or_cancellation": bool, "reasons": [...]}.

    Two outcomes, reported separately (2026-10-22): RE-BID = the tender was advertised again (second IFB, re-bid
    wording, plan says re-bid); CANCELLATION = the notice or plan entry was cancelled. A cancellation is not always a
    re-bid: in P144726, 5 of 10 cancelled plan entries were re-advertised later under a NEW reference and 5 were dropped.
    The rates use the combined outcome, always named "re-bid or cancellation".
    """
    rebid, cancel = [], []
    ifb_dates = sorted(n["notice_date"] for n in notices
                       if n.get("notice_type") == "Invitation for Bids" and n.get("notice_date")
                       and not is_modification(n))
    if len(ifb_dates) > 1 and (ifb_dates[-1] - ifb_dates[0]).days >= MIN_GAP_DAYS:
        rebid.append(f"ifb_notices:{len(ifb_dates)}")
    if any((n.get("notice_status") or "").lower().startswith("cancel") for n in notices):
        cancel.append("notice_cancelled")
    for n in notices:
        hit = mentions_rebid(n.get("bid_description")) or mentions_rebid(n.get("notice_text"))
        if hit:
            rebid.append(f"text:{hit.lower()}")
            break
    if any(p.get("mentions_rebid") for p in plan_labels):
        rebid.append("plan:rebid")
    if any(p.get("is_cancelled") for p in plan_labels):
        cancel.append("plan:cancelled")
    return {"rebid": bool(rebid), "cancellation": bool(cancel), "rebid_or_cancellation": bool(rebid or cancel),
            "reasons": rebid + cancel}


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
    """Smoothed "re-bid or cancellation" rates (any boolean label). Levels go from broad to specific; each group's rate is shrunk toward its parent's rate
    with prior strength `strength` (as if we had seen `strength` extra tenders at the parent's rate):
        rate = (positives + strength * parent_rate) / (n + strength)
    so a group of 3 tenders barely moves away from its parent, while a group of 300 speaks for itself."""

    DEFAULT_LEVELS = ((), ("procurement_category",), ("procurement_category", "procurement_method"),
                      ("country_iso3", "procurement_category", "procurement_method"))

    def __init__(self, levels=DEFAULT_LEVELS, strength=20.0, label="rebid_or_cancellation"):
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
    """'3 of 41 similar tenders were re-bid or cancelled (smoothed rate 6%, 90% interval 2–15%; ...)'."""
    if not res:
        return "no comparable tenders"
    lo, hi = res["ci90"]
    return (f"{res['positives']} of {res['n']} similar tenders were re-bid or cancelled (smoothed rate {res['rate']:.0%}, 90% "
            f"interval {lo:.0%}–{hi:.0%}; compared within: {res['level']})")
