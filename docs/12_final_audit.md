# 12 — Final audit against assessment.md (2026-10-23)

Every requirement in `assessment.md`, where it is answered, what evidence exists, and what is still missing.
Status: **✔ done** (built, run on Databricks, measured) · **◐ partial** (built, but limited or not fully exercised) ·
**✘ missing** (not built; proposed or a stated gap).

## §2 Lifecycle stages

| Stage | Status | Evidence | Gap |
|---|---|---|---|
| Planning & strategy | ✔ | App "Plan a package": duration P20/P50/P80, re-bid or cancellation rate with interval, similar contracts, cited brief (docs/08 §4) | — |
| Tender design & packaging | ◐ | 4,338 procurement-notice passages (scope, qualification criteria) searched for planning; PAD procurement sections; packaging lessons | SBDs, ToRs and bid evaluation reports are not public data; tender-design recall@5 is 0.33 strict / 0.67 lenient on 3 test questions |
| Execution & monitoring | ◐ | Early warnings (91 overdue, 16 disbursement lag, 38 projects), rating timeline with conflict flags (notebook 70, app "Monitor") | Backtest has only 3 projects; overdue flags mean "check", not "late" |
| Continuous learning | ◐ | Weekly job defined with incremental steps, champion/challenger, regression gate, dashboard; feedback tables + buttons | Job created paused and never run end to end as one job (each notebook ran individually); no pilot users, so no real feedback yet |

## §3 Open data ecosystem

| Source | Status | Evidence | Gap |
|---|---|---|---|
| Major Contract Awards | ✔ | DS00005 + DS04691 + DS01004, 491,020 contracts, coverage measured per FY | — |
| Projects & Operations | ✔ | Projects API, 22,461 projects | — |
| Finances & disbursements | ◐ | DS00975 / DS00976 monthly snapshots (17,823 rows, 120 projects) | **co-financing structures not ingested** |
| Procurement notices / REOIs / award summaries | ✔ | procnotices API, full text; 4,338 indexed passages | — |
| Prior-review archives, Standard Bidding Documents | ✘ | — | not available as public data (stated) |
| ISRs | ✔ | 199 ISRs parsed, ratings extracted | — |
| PADs, ICRs | ✔ | 15 PADs, 16 ICRs, 36 Project Papers | — |
| IEG evaluations | ◐ | 12 ICR Reviews (IEG) | **0 PPARs** for the 16 demo projects (none published for them); thematic sector studies not indexed (ieg site returned 403; proposed) |
| Procurement guidance notes, CPFs | ◐ | 5 CPF / CAS documents | guidance notes not indexed (proposed) |
| CPIA, WGI | ✔ | Indicators API, release-lag rule, missing flag | CPIA missing for 56% of country-years (kept NULL) |

## §4 Module 1: Ingestion and knowledge representation

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| ETL for tabular CSV/JSON + PDF/Word | ◐ | APIs → Volume → bronze/silver/gold with retries and resumable pages; PDFs parsed once with `ai_parse_document` | **Word files not handled**: the Documents API served PDFs for the slice (Word path proposed only) |
| Entity resolution | ✔ | suppliers 322,655 → 272,271 with 6 guards; countries audited; contract de-duplication across datasets | fuzzy-rule precision 43–47 / 50 is assistant-judged **[R] awaiting your spot-check** |
| ER schema / knowledge graph across disjoint keys | ✔ | `gold.nodes` / `gold.edges`, 11 node and 11 edge types, 0 orphans; traced contract → ICR/ISR passage | contract ↔ passage links cover 1.3% of contracts (stated) |
| Parsing, chunking, embedding of 100+-page reports | ✔ | section-aware chunks, tables kept with headers, repeats flagged, 9,564 passages embedded, Vector Search hybrid | English-only embedding model; French-source recall@5 0.33 (1 of 3) after the final attempt (docs/07 §4b) |

## §4 Module 2: Predictive and prescriptive engines

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Tender completion time | ✔ | quantile GBT, P80 coverage 0.81, champion registered | P50 only level with the baseline (100.4 vs 100.8 days) |
| Amendment probability | ◐ | LLM-extracted events with quotes (223 amendments, 170 time extensions) + heuristic indicator | **no probability**: no public amendment labels; indicator does not track events (r ≈ −0.08) |
| Re-bidding risk | ✔ | labels with reasons, 97% precision on the main rule, hierarchical rates beat global rate (Brier 0.032 vs 0.039) | classifier not kept (AUC 0.46); rates are history, not forecast |
| Governance indicators in models | ◐ | WGI / CPIA with release lag | ablation: model is better **without** them on this sample |
| Agentic RAG surfacing precedents and lessons for a new package | ✔ | fixed tool sequence, 3 scopes + routing, rerank, gate, guardrails; recall@5 0.57 strict / 0.83 lenient | strict recall below the 0.7 target |

## §4 Module 3: Guardrails and conflicting evidence

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Validation layers against hallucination, every claim anchored | ✔ | citation validity 1.00, claim support 0.86–0.905 and judge 97.5–100% (range over runs), injection quarantined | verifier and judge are the same model family as the generator |
| Conflicting evidence (ICR vs ISR) | ✔ | IEG > ICR > latest ISR, all ratings shown, flags (1 IEG downgrade, 2 ISR volatility) | ratings sample check (20) **[R]** |

## §4 Module 4: Continuous learning and implementation

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Closed loop updating models and indices | ◐ | incremental embeddings + Delta Sync (used on 2026-10-23 for the 196 re-embedded passages), champion/challenger rule, regression gate | the full weekly job has not been created / run end to end |
| Phased pilot and KPIs | ✔ | docs/08 §6.2–6.3, AI/BI KPI dashboard deployed (4 pages) | dashboard tiles not yet visually checked; business KPIs are proposed targets only (no pilot) |

## §5 Rubric readiness

| Dimension | Strongest evidence | Weakest point to prepare for |
|---|---|---|
| System & Knowledge Architecture (30) | one governed workspace; graph with evidence and dates; parse-once; data-quality gate | sparse contract ↔ passage links; Word / PPAR gaps |
| Analytical & AI Methodology (30) | honest baselines, time splits, calibrated ranges, measured guardrails, lenient vs strict recall | duration ≈ baseline; strict recall 0.57; weak gate; French recall 1 of 3 |
| Domain Alignment & Data Utilization (25) | three contract datasets reconciled; coverage measured; re-advertisement rule from real procurement practice | SBDs / bid evaluations not public; co-financing not used |
| Operationalization & Scalability (15) | weekly job design, dashboard, rollout with go/no-go criteria, scaling table | job never run end to end; Free Edition limits |

**Fixed during the audit:** the Databricks App crashed on start (Python 3.11 rejects a backslash inside an f-string
expression in `src/rag/databricks_tools.py`; notebooks run 3.12, so only the app failed). Fixed, redeployed on
2026-10-23 ("App started successfully"), and `tests/test_py311_compat.py` now guards every `src/` and `app/` file.

## Still missing / to do before the interview (prioritised)

1. **Slides as a deck**: only the outline exists (docs/09).
2. **Open the app and the dashboard once, signed in**: confirm every page and tile renders (needs your sign-in; the
   app stops 24 h after start, so restart it shortly before the demo).
3. **Your hand reviews [R]**: 20 lessons + 20 events (notebook 55 §6), 20 ratings, the supplier fuzzy-rule sample.
4. **Weekly job**: create it (paused) from `jobs/refresh_job.json` and run it once end to end, or say clearly that
   each step ran individually.
5. **docs/00 status report** is from 2026-10-22: superseded by this audit (or refresh it).
6. Gaps to state, not fix: Word ingestion, co-financing, PPARs / thematic studies / guidance notes, SBDs, amendment
   labels, multilingual embeddings.
