# 00 — Status against the assessment (2026-10-21)

**Done & measured** = ran on Databricks, numbers in docs/06. **Built & tested** = code written, unit-tested locally,
notebook not yet run. **Proposed** = written up in docs/08_proposal.md only.

| Assessment item | Status | Where |
|---|---|---|
| M1 ETL for CSV/JSON + PDF/Word | Done & measured | notebooks 10, 11, 20; `src/ingest`, `src/chunking` |
| M1 Entity resolution / KG across disjoint keys | Done & measured (supplier step needs a re-run after a bug fix) | notebooks 30, 40; `src/entity_resolution`, `src/graph` |
| M1 Parsing, chunking, embedding of long reports | Parsing/chunking done & measured; embeddings built & tested | notebooks 20, 60 |
| M2 Timeline benchmarking (duration) | Built & tested | notebook 50; `src/models` |
| M2 Re-bidding risk | Built & tested | notebook 51; `src/risk/labels.py` |
| M2 Amendment risk | Built & tested (heuristic indicator + extracted events; no public labels) | notebooks 55, 70; `src/risk/amendment.py` |
| M2 Governance indicators in models | Built & tested (release-lag rule, CPIA missing flag) | `src/models/duration.py` |
| M2 Agentic RAG | Built & tested | `src/rag`, notebooks 60, 61, app |
| M3 Validation layers / citations | Built & tested (incl. injection quarantine) | `src/guardrails`, `src/rag/prompt.py` |
| M3 Conflicting evidence (ISR vs ICR vs IEG) | Built & tested | `src/conflict/ratings.py`, notebook 55 |
| M4 Closed-loop updates of models and indices | Built (job definition, incremental steps, champion/challenger, regression gate) | `jobs/refresh_job.json` |
| M4 Feedback | Built | app + `ops.feedback` (notebook 80) |
| M4 Phased rollout + KPIs | Written | docs/08 §6 |
| L1 Planning / L2 Tender design / L3 Monitoring / L4 Learning | App screens + notebooks built; L2 limited by public data (stated) | `app/app.py`, notebook 70 |
| Written proposal | Draft complete; results cells pending runs | docs/08_proposal.md, docs/07_evaluation.md |
| Slides | Outline | docs/09_slides_outline.md |
| Measured evaluation results | **Pending your runs** | docs/07_evaluation.md |

## Fixes made in this review

1. **Supplier resolution (R2, individuals):** an ID-less record listed first could chain two different people with the
   same name and different IDs into one supplier. Spark's row order is arbitrary, so this could happen on real data.
   Fixed, with a regression test over every ordering. **Re-run notebooks 30 and 40.**
2. **Refresh would not refresh:** notebook 10's run folder was hard-coded, so a scheduled run would download nothing new.
   Now it's a widget (default unchanged), and the job passes the run date.
3. **Retry rule:** two Stage 2 scripts called APIs without backoff; they now use the shared retry helper (CLAUDE.md rule).

## Still open (needs you)

- Run notebooks 30 → 40 → 50 → 51 → 55 → 60 → 61 → 70 → 80 and paste the summaries (docs/07 gets filled from them).
- Approve checkpoint (d) (docs/03 §10). Set `CREATE_ENDPOINT = True` in notebook 60 when ready (D4).
- Review: the 20-rating sample (55), 20 lessons + 20 events (55), 20 re-bid labels (51), and the LLM-written lesson
  questions (61, set `reviewed = true`).
- Spot-check the [PROV] verdicts in docs/06 (supplier R3 sample, link review).
- Create and deploy the app (notebook 80 steps), and pin scikit-learn in `requirements.txt` to the version notebook 50
  prints.
- Slides from the outline once results are in.
