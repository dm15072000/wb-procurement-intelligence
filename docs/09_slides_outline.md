# 09 — Slide outline (12 slides, ~20 minutes)

Each slide: title · the one message · what to show. Numbers marked [run] come from docs/07 after the notebook runs.

1. **The problem**: three kinds of knowledge in silos (assessment §1). Show: the three layers → four lifecycle stages.
2. **What I built**: one Databricks workspace; demo slice of roads in 5 countries, 16 projects; models on 151 projects.
   Show: architecture diagram (docs/01 §3).
3. **Data before models**: every source verified; three surprises (DS04691 found, WGI codes renamed, H1 fails for
   post-review). Show: source register + coverage-by-FY table (56% / 81% / 82–94%).
4. **Incomplete data, handled openly**: coverage labels, NULL never 0, "unknown" review type, masked suppliers.
5. **Knowledge graph**: two Delta tables, every edge with rule, evidence and date; the traced example contract → ISR
   passage (GYTJ201B); the honest 1.3% link coverage → why Vector Search is primary.
6. **Documents**: parse once (94% vs 26% tables), section-aware chunks (53 → 339 median tokens), duplicates flagged.
7. **Duration model**: prior-review main, quantile range, time split, baseline comparison. Show: results table [run].
8. **Re-bid and amendment risk**: rare labels → shrunk rates with intervals; amendment = heuristic indicator, stated.
9. **Agentic RAG**: the fixed 8-step flow; one live example from the app with sources.
10. **Guardrails and conflicts**: the 10 layers; the injection test; ISR vs ICR vs IEG rules and flags.
11. **Continuous learning**: the weekly job, incremental at every step, champion/challenger, regression gate, feedback.
12. **Rollout and KPIs**: phases 0–3 with go/no-go criteria; offline [run] and online KPIs; limitations.
