# Procurement Intelligence on Databricks: submission summary

**Repository:** https://github.com/dm15072000/wb-procurement-intelligence (public) ·
**App:** https://procurement-intelligence-7474649658971091.aws.databricksapps.com ·
**KPI dashboard:** https://dbc-0db6d6cc-81dd.cloud.databricks.com/dashboardsv3/01f1ceeebce61e21a109a466e6866a7a/published
(the app and dashboard need a sign-in to the Databricks workspace; shown live, or via screenshots)

## What was built

An end-to-end procurement intelligence platform in **one Databricks workspace** (Delta Lake + Unity Catalog, Foundation
Model APIs: Llama 3.3 70B and GTE-large, Vector Search, MLflow, Workflows, a Databricks App, an AI/BI dashboard), on
**World Bank open data**: 491,020 contracts from three Finances One datasets, 22,461 projects, procurement notices,
disbursements, CPIA / WGI, and 299 PAD / ISR / ICR / IEG / CPF documents → 9,564 indexed passages. Demo slice: rural and
inter-urban roads, FY2014–21, 16 projects in India, China, Mali, Albania and Sri Lanka; models train on all 151 road
projects. It follows the procurement officer's journey:

- **Plan:** tender-duration range (P20 / P50 / P80), re-bid or cancellation rate of similar tenders, market-approach
  options, similar past contracts, and a cited brief of risks and lessons.
- **Tender design:** past notice scopes and qualification criteria, PAD procurement sections, lessons with quotes.
- **Monitor:** early warnings ("check", not "late"), rating timeline ISR → ICR → IEG with conflict rules, development
  objective, financing, disbursement.
- **Learn:** weekly job (incremental parse, extraction, embedding + index sync, champion/challenger retraining,
  regression evaluation, KPI dashboard) and user feedback into the evaluation set.

## Key results (measured; details in docs/07)

| Area | Result |
|---|---|
| Data quality | automated gate, 14 / 14 checks pass; contract coverage measured per FY (56% / 81% / 82–94%) |
| Duration model (prior review) | P80 coverage **0.81** (target 0.7–0.9); P50 MAE 100.4 vs 100.8 days for the baseline (level) |
| Re-bid or cancellation | 13.6% of 3,224 tenders (4.6% without one dominant project); rule precision 97%; Brier 0.032 vs 0.039 |
| Retrieval (26 held-out questions) | recall@5 **0.57 strict / 0.83 lenient**; precision@5 0.11 strict / 0.50 lenient; same passages for plain and "in detail" phrasings (5 / 5) |
| Guardrails | citation validity **1.00**; claim support **0.86–0.905**; independent judge **97.5–100%** (ranges over runs); refusals 3 / 3; prompt injection quarantined |
| Engineering | 241 unit tests; every notebook run on Databricks; app deployed; dashboard published |

## Known limitations (stated, not hidden)

The duration point estimate only matches the baseline, and governance features did not help on 500 rows · no public
amendment labels, so amendment risk is a heuristic · strict recall 0.57 is below the 0.7 target · the retrieval gate is
a weak first filter; refusals rely on the answer step and the claim verifier · English-only embeddings: French-source
recall 1 of 3 (production fix: a multilingual model) · reports name only 1.3% of contracts · SBDs, bid evaluations,
guidance notes and co-financing amounts are not in the public data used · the weekly job has run step by step, not yet
end to end · small, partly synthetic evaluation set; judge and generator from the same model family.

## What to send the interviewer

1. The repository link (README → this summary → proposal).
2. `slides/procurement_intelligence.pdf` (the deck; the `.pptx` has speaker notes).
3. `docs/08_proposal.md` (technical proposal) and `docs/07_evaluation.md` (measured results), both in the repo.

## What to show live (about 10 minutes, after the slides)

1. **Plan a package:** China · project P132621 · Works · $2M → duration range, re-bid rate, market-approach options,
   cited brief with sources and the "guardrails removed N claims" line.
2. **Ask the evidence:** the Yunnan rating question, plain and with "tell me in detail" → same sources, rating timeline
   with the conflict rule.
3. **Monitor a project:** a demo project with its rating timeline, flags, development objective and disbursement.
4. **KPI dashboard:** data health → model quality → assistant quality (strict vs lenient recall trend).
5. Optional: the repo's tests (`python -m pytest tests`) and one notebook's summary cell.

Before the demo: restart the app (Free Edition apps stop 24 h after start) and open each screen once.
