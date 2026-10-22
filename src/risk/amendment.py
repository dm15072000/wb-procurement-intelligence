"""Transparent amendment-risk indicator score (docs/03_model_rag_design.md §2b). Pure Python, unit-tested.

Plain-language summary: no public dataset records contract amendments, so we cannot train or validate an amendment
probability on public data. Instead this adds up documented risk factors, each worth a fixed number of points, and
shows which ones applied and where the evidence came from. It is a HEURISTIC SCORE (0-100), not a probability, and
the weights are proposed, not learned. Its role is to tell a reviewer where to look.
"""

# (factor id, points, plain-language reason). Weights are proposed [P] and stated openly in the app.
FACTORS = [
    ("large_civil_works", 20, "civil works contract above USD 10 million: large works are the most often varied"),
    ("high_value_for_category", 10, "contract value in the top quarter for its category"),
    ("long_predicted_duration", 15, "predicted P80 tender duration above 12 months"),
    ("weak_government_effectiveness", 15, "WGI government effectiveness below -0.5 in the latest published year"),
    ("weak_isr_rating", 20, "latest ISR implementation or procurement rating Moderately Unsatisfactory or worse"),
    ("earlier_amendments_in_project", 20, "earlier amendment / variation / time-extension events in the same project"),
]
POINTS = {f: p for f, p, _ in FACTORS}
REASONS = {f: r for f, _, r in FACTORS}


def score(ctx):
    """ctx keys (missing = unknown, never counted as a risk or as safe):
      category, amount_usd, category_p75_usd, p80_days, wgi_ge, latest_isr_min_score (1-6 scale),
      n_earlier_amendment_events, plus optional evidence strings under '<factor>_evidence'.
    Returns {"score", "max_known", "factors": [{factor, points, reason, evidence}], "unknown": [factor ids]}."""
    checks = {
        "large_civil_works": (None if ctx.get("amount_usd") is None or ctx.get("category") is None
                              else ctx["category"].strip().lower() in ("works", "civil works") and ctx["amount_usd"] > 10e6),
        "high_value_for_category": (None if ctx.get("amount_usd") is None or ctx.get("category_p75_usd") is None
                                    else ctx["amount_usd"] > ctx["category_p75_usd"]),
        "long_predicted_duration": None if ctx.get("p80_days") is None else ctx["p80_days"] > 365,
        "weak_government_effectiveness": None if ctx.get("wgi_ge") is None else ctx["wgi_ge"] < -0.5,
        "weak_isr_rating": None if ctx.get("latest_isr_min_score") is None else ctx["latest_isr_min_score"] <= 3,
        "earlier_amendments_in_project": (None if ctx.get("n_earlier_amendment_events") is None
                                          else ctx["n_earlier_amendment_events"] > 0),
    }
    factors, unknown, total, known_max = [], [], 0, 0
    for f, hit in checks.items():
        if hit is None:
            unknown.append(f)
            continue
        known_max += POINTS[f]
        if hit:
            total += POINTS[f]
            factors.append({"factor": f, "points": POINTS[f], "reason": REASONS[f],
                            "evidence": ctx.get(f"{f}_evidence")})
    return {"score": total, "max_known": known_max, "factors": factors, "unknown": unknown,
            "label": "heuristic indicator score (not a probability)"}
