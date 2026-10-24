# 12 — Final audit against assessment.md (re-read line by line, 2026-10-24)

Each requirement in `assessment.md` §2–§5, with its status, the evidence (file or table) and our measured numbers.
**Done** = built, run on Databricks and measured · **Partial** = built and run, but limited or not fully exercised ·
**Proposed** = written up in docs/08 with the reason and how it would be built · **Missing** = not available.
Items fixed during this audit are marked *(fixed 2026-10-24)*.

## §2 Lifecycle stages

| Stage (assessment wording) | Status | Evidence and numbers | Gap / how it would be closed |
|---|---|---|---|
| **Planning & Strategy**: realistic schedules, viable market-approach options, proactive risk mitigations from precedent | Done | App "Plan a package": duration P20/P50/P80 (P80 coverage 0.81; P50 ≈ baseline), re-bid or cancellation rate with 90% interval (13.6%, 4.6% without P157054), **market-approach options** of similar packages *(fixed 2026-10-24: method × market approach with median notice-to-signing; e.g. works of $0.6–6M: Request for Bids with a national market stated in the notice, 467 contracts, median 109 days)*, similar contracts, cited brief | STEP award data rarely records market approach; inferred from the IFB wording where stated, otherwise "not recorded" |
| **Tender Design & Packaging**: specifications, qualification criteria, ToRs vs historical benchmarks | Partial | 4,338 procurement-notice passages (scope, lots, qualification criteria) searched in a notice scope; PAD procurement sections; 1,198 lessons; tender-design recall@5 0.33 strict / 0.67 lenient (n = 3) | SBDs, ToRs, bid evaluation reports are not public data; production: STEP tender documents under access control (docs/08 §2, §6.2 phase 3) |
| **Execution & Monitoring**: milestones, early-warning indicators for slippage / compliance | Partial | Notebook 70 + app "Monitor": 91 overdue tenders, 16 disbursement-lag flags, 38 projects flagged; rating timeline with conflict flags; development objective and financing on the Monitor screen *(fixed 2026-10-24)* | Backtest only 3 projects; an overdue flag means "check", not "late" (about a third of contracts match a notice) |
| **Continuous Learning**: new evaluations become intelligence | Partial | Weekly job (`jobs/refresh_job.json`, created paused, id 223004783343690): incremental parsing, extraction (dry run unless a call count is approved), incremental embedding + Delta Sync, champion/challenger, regression evaluation, KPI dashboard. Incremental sync exercised on 2026-10-23 (only the 196 changed vectors) | The full job has not yet run end to end: the scheduled run on 2026-10-24 waited for a permission approval. Each step has run individually |

## §3 Data ecosystem

| Source | Status | Evidence and numbers | Gap / how it would be closed |
|---|---|---|---|
| Major Contract Awards (descriptions, values, suppliers, country, category, review type) | Done | DS00005 + DS04691 + DS01004: 491,020 contracts; coverage per FY measured (56% / 81% / 82–94%); review type "unknown" where absent | — |
| Projects & Operations (IDs, development objectives, agencies, commitments, E&S category, status) | Done | Projects API: 22,461 projects with agency, environmental category, commitments, status; **development objective (PDO) for 148 of 151 road-universe projects** *(fixed 2026-10-24, notebook 13, `silver.project_pdo`)* | — |
| Finances & Disbursement (schedules, commitments, **co-financing**) | Partial | DS00975 / DS00976: 17,823 monthly rows for 120 projects; **financing lines per project** *(fixed 2026-10-24, notebook 13, `silver.project_financing`: 173 lines for 151 projects)*. Measured: every line is IBRD (45) or IDA (128); **no co-financier appears in the public Projects API fields** for these projects | Co-financing amounts and non-Bank financiers are not in the public fields used; production: the Bank's internal financing data, same table shape |
| Procurement notices: **REOIs**, **contract award summaries** | Done | procnotices API, full text; IFB + REOI text indexed for the demo projects; award notices used to measure coverage and check joint awards | — |
| **Standard Bidding Documents** / prior-review archives | Missing (stated) | — | Not public as project data; STEP internal documents in production (docs/08 §2) |
| ISRs (ratings, milestones, roadblocks) | Done | 199 ISRs parsed; 230 ratings by regex; events extracted with quotes | — |
| PADs, ICRs | Done | 15 PADs, 16 ICRs, 36 Project Papers parsed | — |
| IEG: ICR Reviews, **PPARs**, **thematic studies** | Partial | 12 ICR Reviews indexed; rule IEG > ICR > ISR | 0 PPARs exist for the 16 demo projects; thematic studies not indexed (IEG's site returned HTTP 403; available via Documents & Reports [P]) |
| **Procurement guidance notes** and **CPFs** (policy constraints) | Partial | CPFs indexed: 3 CPFs + 2 Country Assistance Strategies (227 + 383 passages) | Guidance notes not indexed [P]: same parse → chunk → index path with a `policy` retrieval scope (docs/08 §2) |
| CPIA, WGI | Done | Indicators API, release-lag rule, CPIA missing 56% kept NULL with a flag; ablation: the model is better without them on 500 rows (MAE 94.7 vs 100.4) | — |

## §4 Module 1: Ingestion and knowledge representation

| Requirement | Status | Evidence and numbers | Gap |
|---|---|---|---|
| ETL for tabular **CSV/JSON** + unstructured **PDF/Word** | Partial | JSON APIs → raw Volume → bronze → silver with retries, backoff and resumable pages; PDFs parsed once with `ai_parse_document` (94% vs 26% of tables well-formed) | **Word** path not exercised: the API served PDFs for every slice document; route proposed in docs/08 §3.1 |
| Entity resolution | Done | Suppliers 322,655 → 272,271 (largest cluster 119 → 40 after guards; fuzzy precision 43–47 / 50, assistant-judged, your spot-check pending); contracts de-duplicated across 3 datasets; country codes audited | — |
| ER schema / KG linking **via Project ID, sector codes and temporal proximity** | Partial | `gold.nodes` / `gold.edges` (11 node and 11 edge types, 0 orphans); links use **project ID** and **time** (`project+temporal` rule in `src/graph/links.py`, `available_from` on every edge) plus identifiers; traced contract GYTJ201B → ISR passage | **Sector codes** define the universe and form `HAS_SECTOR` edges (396) but are not in the link score (they cannot separate candidates in a one-sector slice); sector term proposed for the full portfolio (docs/08 §3.3). Link coverage 1.3% |
| Parsing, chunking, embedding of 100+-page reports (tables, annexes) | Done | Section-aware chunks with heading paths, tables with repeated headers, ISR repeats flagged, median 339 tokens, 9,564 passages, Vector Search hybrid; French passages translated for embedding | English-only embedding model (French recall 1 of 3) |

## §4 Module 2: Predictive and prescriptive engines

| Requirement | Status | Evidence and numbers | Gap |
|---|---|---|---|
| **Tender completion time** | Done | Quantile GBT, time split, P80 coverage 0.81, P50 MAE 100.4 vs 100.8 baseline (≈), champion v1 | Point estimate not better than the baseline |
| **Amendment probability** | Partial (no probability) | 223 amendment and 170 time-extension events extracted with quotes; transparent indicator, Spearman r ≈ −0.08 against events → heuristic only | No public amendment labels; production: STEP amendment records → supervised model (docs/08 §4.3) |
| **Re-bidding risk** | Done | Labels with reasons (re-advertisement rule 97% precision); hierarchical rates Brier 0.032 vs 0.039; classifier AUC 0.46 not kept | One project drives most positives (reported with and without it) |
| Model family: GBT / **survival analysis** / probabilistic | Done (GBT + Beta-binomial); survival justified as proposed | docs/08 §4.1: censoring needs reliably open tenders; an unmatched notice is more often a join failure | [P] with STEP tender status |
| Governance indicators as inputs | Done | WGI / CPIA with release lag; ablation reported | They do not help on this sample |
| **Agentic RAG** surfacing precedents, past specifications and lessons for a new package | Done | Fixed tool sequence, 3 scopes + routing + project-name resolution, hybrid search; recall@5 0.57 strict / 0.83 lenient; **precision@5 0.11 strict / 0.50 lenient** *(added 2026-10-24)*; phrasing robustness: identical top 5 for 5 of 5 questions | Strict recall under the 0.7 target |

## §4 Module 3: Guardrails and conflicting evidence

| Requirement | Status | Evidence and numbers | Gap |
|---|---|---|---|
| Validation layers; every recommendation anchored in documents and data | Done | Citation validity 1.00; claim support 0.86–0.905 and independent judge 97.5–100% (ranges over runs); injection quarantined; rating facts carry source passages | Judge and generator are the same model family |
| **Conflicting evidence (ICR vs ISR)** | Done | IEG > ICR > latest ISR; every rating shown; flags found: 1 IEG downgrade, 2 ISR volatility; the rating timeline is given to the answer step as facts | Ratings sample check (20) [your review] |

## §4 Module 4: Continuous learning and implementation

| Requirement | Status | Evidence and numbers | Gap |
|---|---|---|---|
| Closed loop: milestones, supervision notes, **contract amendments** update **models AND vector indices** | Partial | Indices: incremental embedding + Delta Sync (exercised: 196 passages on 2026-10-23); models: champion/challenger rule in notebook 50; new ISRs → extraction → amendment events → indicator; feedback → evaluation set | Full job not yet run end to end; LLM extraction in the job stays a dry run until a call count is approved (cost control) |
| **Phased pilot** with KPIs: planning cycle time, retrieval precision/recall, adoption | Done (offline KPIs measured; online KPIs proposed) | Phases 0–3 with go/no-go (docs/08 §6.2); recall and precision measured; KPI dashboard deployed with the proposed pilot targets for cycle time and adoption | No pilot users yet: cycle time and adoption are proposed targets only |

## §5 Rubric dimensions

| Dimension | Strongest evidence | Weakest point (prepare an answer) |
|---|---|---|
| System & Knowledge Architecture (30) | one governed workspace; graph with evidence and dates; parse once; data-quality gate (14 / 14) | link coverage 1.3%; Word path not exercised; sector codes not in the link score |
| Analytical & AI Methodology (30) | baselines, time splits, calibrated ranges, measured guardrails, strict vs lenient recall and precision, robustness test | duration ≈ baseline; strict recall 0.57; weak gate; French 1 of 3 |
| Domain Alignment & Data Utilization (25) | three contract datasets reconciled; coverage measured; re-advertisement rule; PDO and financing added | SBDs / bid evaluations not public; guidance notes not indexed; no public co-financing data for the slice |
| Operationalization & Scalability (15) | weekly job, KPI dashboard, rollout with go/no-go, scaling table | weekly job not yet run end to end; Free Edition limits |

## What you must still do yourself

1. **Run the weekly job once**: approve the waiting scheduled session, or ask me to run it (it uses compute).
2. **Open the app after the latest redeploy** so the duration-model status is logged (the sidebar shows it).
3. **Hand reviews:** 20 lessons + 20 events (notebook 55 §6), 20 ratings, the supplier fuzzy sample.
4. Restart the app shortly before the demo (apps stop 24 h after start on Free Edition).
