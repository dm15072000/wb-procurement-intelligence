# 09 — Slide outline (14 slides, ~20 minutes + demo)

Each slide: **title** · the one message · what to show · the numbers to say (all [M] from docs/07 unless marked).
Speaker rule: say "measured", "proposed" or "assumption" for every claim; never round a weak result up.

1. **The problem**: procurement knowledge sits in three silos (what happened / how / why), so plans repeat past
   mistakes. Show: three layers → four lifecycle stages (assessment §1–2). Say: "I built the bridge on real public
   data for one sector, and measured where it works and where it does not."

2. **What I built**: one Databricks workspace, nothing else. Show: architecture (Volumes → bronze/silver/gold → graph
   + chunks → Vector Search → models → RAG with guardrails → App + dashboard; one weekly job). Numbers: 491,020
   contracts, 22,461 projects, 299 documents → 9,564 indexed passages, 16 demo projects in 5 countries, models on all
   151 road projects.

3. **Data before models**: every source verified first; three surprises. Show: source register + coverage by fiscal
   year. Say: DS04691 found (FY2017–19 is partial, not missing: 56% / 81% / 82–94%), WGI codes renamed, the duration
   target fails for post-review (≈13% negative) so the main model is prior-review.

4. **Incomplete data, handled openly**: coverage label on every row, NULL never 0, review type "unknown", masked
   suppliers never merged, data-quality gate (14 checks, all pass) that stops the models on failure.

5. **Knowledge graph**: two Delta tables, every edge with rule, evidence and the date it became public (no leakage).
   Show: traced example contract → supplier → project → ISR passage naming re-bid contract GYTJ201B. Say: contract ↔
   passage links cover only 1.3% of contracts, so Vector Search is the primary path; links add context.

6. **Documents**: parse once (94% vs 26% of tables well-formed), section-aware chunks (median 53 → 339 tokens), ISR
   repeats flagged. Languages: 196 French passages; gte-large-en is English-only (slide 11).

7. **Duration model**: quantile GBT, prior-review main, time split. Numbers: P50 MAE 100.4 vs baseline 100.8 days
   (level), P80 coverage 0.81 (well calibrated); without governance features MAE 94.7, so WGI/CPIA do not help on 500
   rows. Message: the value is the honest P20–P80 range.

8. **Re-bid and amendment risk**: rare events → shrunk rates with intervals. Numbers: re-bid or cancellation 13.6%
   (4.6% without one project, P157054); re-advertisement rule precision 97% (30 sampled) and 44/44 outside P157054;
   Brier 0.032 vs 0.039; classifier AUC 0.46 not kept. Amendment score is a heuristic (r ≈ −0.08), shown as a checklist.

9. **Agentic RAG**: fixed, auditable tool sequence (SQL facts → models → graph → hybrid search in 3 scopes + routing →
   rerank → gate → LLM → guardrails). Live demo from the app: "Plan a package" with sources and removed-claim count.

10. **Guardrails and conflicts**: mechanical citation / number / ID checks, then one batched claim verifier;
    quarantine for injected instructions. Numbers (test half): citation validity 1.00, claim support 0.86–0.905
    (range over evaluation runs: LLM run-to-run variance), independent judge 97.5–100% of 40, refusals 3 / 3, injection quarantined. Conflicts: IEG > ICR >
    latest ISR, all ratings shown, flags raised (1 IEG downgrade, 2 ISR volatility).

11. **Evaluation, honestly**: 52 frozen questions, halves by project, threshold set on one half. Numbers: recall@5
    0.57 strict / 0.83 lenient (lenient = any retrieved passage an LLM judge finds contains the answer). Two lessons:
    (a) the gate was mis-scaled (Vector Search scores 1/(1+L2²)); fixed, but it is a weak filter (scores overlap), so
    refusals rest on the answer step and the verifier; (b) French: translation for embedding alone did nothing; English
    keywords over the translations + a French query pass found 1 of 3 French questions (0 → 0.33) → known limitation;
    production fix = multilingual embedding model.

12. **Continuous learning**: weekly job (ingest → gate → extract → embed + sync → retrain champion/challenger →
    regression eval → dashboard), incremental at every step; feedback buttons → ops tables → evaluation set.

13. **KPI dashboard and rollout**: AI/BI dashboard (data health, model quality, assistant quality, usage = test only).
    Phases 0–3 with go/no-go criteria; phase 0 status: claim support 0.86–0.905 (≈ 0.9), P80 ✔, P50 ≈ baseline, recall strict ✘ /
    lenient ✔. Business KPIs (cycle time, adoption) are proposed pilot targets, not measured.

14. **Limitations and next steps**: small eval set; same-family verifier; no amendment labels; French retrieval;
    sparse contract links; Free Edition limits. Next: multilingual embeddings, STEP internal data (estimates,
    amendments), more documents, a shadow pilot with 10 specialists.

**Backup slides:** supplier entity resolution (322,655 → 272,271; precision 43–47/50 [R]); extraction (1,198
lessons, 1,086 events, quotes verified word for word); early-warning backtest (only 3 projects: too few to read).
