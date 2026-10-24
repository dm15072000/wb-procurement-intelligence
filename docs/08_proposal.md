# Technical Proposal: AI-Powered Procurement Intelligence Platform

World Bank technical assessment · Databricks implementation · Final draft 2026-10-23

Labels: **[V] verified** · **[M] measured** (run on Databricks; full tables in `docs/07_evaluation.md`) ·
**[A] assumption** · **[P] proposed** · **[R] needs the author's hand review**.
Each section names the requirement it answers (assessment §4 modules, §5 rubric) and the file that implements it.

---

## 1. Summary

The platform joins three layers of World Bank knowledge: **what happened** (contract awards, projects,
disbursements, notices), **how it was executed** (ISRs, procurement plans, notices) and **why it succeeded or failed**
(PADs, ICRs, IEG reviews). It turns them into decision support at four procurement stages:

| Stage (§2) | What the user gets | Evidence behind it |
|---|---|---|
| Planning & strategy | predicted tender duration range (P20/P50/P80), re-bid or cancellation rate of similar tenders, similar past contracts, a cited brief of risks and mitigations | gradient-boosted quantile model; Beta-binomial rates; SQL; RAG with guardrails |
| Tender design & packaging | past scopes and qualification criteria from indexed REOI / IFB notices; lessons on packaging, specifications and market capacity | procurement-notice text, PAD procurement sections, ICR lessons (public SBDs/ToRs are scarce, stated) |
| Execution & monitoring | early-warning flags with reasons: overdue tenders, disbursement lag, weak ISR ratings, reported problems (a flag means "check", not "late") | duration model, disbursement snapshots, extracted ratings and events |
| Continuous learning | new ISRs/ICRs become searchable, rated and mined for lessons each week; models retrain under a promotion rule | one Databricks Job, incremental at every step |

Everything runs inside one Databricks workspace: Delta Lake + Unity Catalog, Foundation Model APIs (Llama 3.3 70B,
GTE-large), Vector Search, MLflow, Workflows and a Databricks App. There is no other infrastructure.

**Demo slice [V]:** Transport – Rural and Inter-Urban Roads, projects approved FY2014–FY2021, documents for 16 projects
in India, China, Mali, Albania and Sri Lanka (2 IDA countries). Models train on all 151 road projects in the universe.

---

## 2. Data ecosystem used (§3; rubric: Domain Alignment & Data Utilization, 25)

| Assessment source | Implemented with | Status |
|---|---|---|
| Major Contract Awards | Finances One **DS00005** (FY2020+), **DS04691** (FY2017–19), **DS01004** (FY2001–16): 491,020 contracts, 0 cross-dataset overlaps | [M] ingested |
| Projects & Operations | Projects API (22,461 projects): sectors with shares, dates, commitments, agency, env. category | [M] ingested |
| Finances & disbursements | DS00975 (IBRD) / DS00976 (IDA) monthly snapshots: 17,823 rows for 120 universe projects | [M] ingested |
| Procurement notices, REOIs, award summaries | procnotices API: notice type, date, reference, full text | [M] ingested |
| Procurement Plans | Documents & Reports, parsed with `ai_parse_document`: labels only (dates failed a cross-check: 20% agreement) | [M] labels used |
| ISRs, PADs, ICRs, IEG ICR Reviews, PPARs, Project Papers | Documents & Reports API → 299 documents → 5,226 section-aware chunks | [M] ingested |
| CPFs | latest CPF per demo country (filed under two doc types, verified) | [M] ingested |
| OKR thematic studies, procurement guidance | reachable [V]; proposed as extra lesson sources (§9) | [P] |
| CPIA, WGI | Indicators API (new `GOV_WGI_*` codes [V]) | [M] ingested |
| SBDs, bid evaluation reports | not public as data [A] | gap stated |

**Incomplete reporting is handled explicitly, never hidden:**
- Contract coverage per fiscal year is **measured** against an independent source, published award notices: FY2017
  56%, FY2018–19 81%, FY2020+ 82–94% [M]. Every contract carries `coverage_status`, and a zero in a partial year is
  never read as "no activity".
- CPIA is missing for 56% of country-years (mostly IBRD countries) [M]: kept NULL with a missing flag; WGI (≥98% present)
  is the always-available governance signal.
- Review type absent (all of DS01004) → `"unknown"`, counted.
- Anonymised suppliers ("INDIVIDUAL CONSULTANT" with no ID) are never merged and get NULL history, never 0.
- Legacy and regional country codes (ZR, YF, TP, 3W, …) were audited in full and mapped with stated reasons; ambiguous
  ones stay unmapped and are reported [M].
- The tender-duration target was **tested before use** (H1): it holds for prior-review contracts (0.5% negative) and
  not for post-review (≈13% negative), so the main model is prior-review only [M].

---

## 3. Module 1: Ingestion pipeline and multimodal knowledge representation

### 3.1 ETL (rubric: System & Knowledge Architecture, 30)

```
APIs (retry + backoff, honour 429 Retry-After) → raw JSON Lines in a UC Volume → bronze (raw JSON string per record)
  → silver (typed, de-duplicated, standard keys; tested transforms in src/) → gold (graph, features, rates, flags)
PDFs → Volume → ai_parse_document ONCE (stored, because output is not byte-identical between runs [M])
  → section-aware chunks → embeddings → Vector Search (Delta Sync)
```

- **Raw first:** any cleaning bug is fixed and re-run from bronze without calling the APIs again.
- **Resumable:** page files are written atomically; a re-run continues where it stopped. The refresh job passes a new
  run tag, so each refresh downloads fresh data.
- **Parser chosen by measurement** (D6): `ai_parse_document` kept 94% of tables well-formed vs 26% for pdfplumber on
  three real ICRs [M].
- Notebooks `10`, `11`, `20`; code `src/ingest/`, `src/chunking/`; 84 unit tests for this layer.

### 3.2 Entity resolution across disjoint keys

| Entity | Canonical key | Method |
|---|---|---|
| Project | `P` + 6 digits | strict pattern; anything else rejected, not guessed |
| Contract | contract number | the same number in two datasets = one contract; precedence rule |
| Tender | project ID + normalised reference | joins notices to contracts (H1 method) |
| Country | ISO3 | audited code table + names + regions; original kept |
| Supplier | cluster key | R1 same supplier ID → R2 same cleaned name + country → R3 near-identical name + country (firms only), with six guards (shared-ID, common-name, numbers, JV, distinctive word, chain) |

Supplier results [M]: 322,655 records → 272,271 suppliers; largest cluster 40 (was 119 before the guards); fuzzy-rule
precision 43–47 of 50 on a random sample (assistant-judged, pending the user's spot-check). Precision is preferred over
recall: a wrong merge shows a user someone else's track record.

### 3.3 Knowledge graph topology

Two Delta tables, `gold.nodes` and `gold.edges`. Every edge carries `rule`, `evidence` (source row or matched text) and
`available_from` (the date the fact became public), which enforces "as-of" queries and prevents leakage.

```
Country ◀─IN_COUNTRY─ Project ─HAS_SECTOR▶ Sector          Region ◀─IN_REGION─ Project
Notice ─ANNOUNCED▶ Tender ─RESULTED_IN▶ Contract ─FOR_PROJECT▶ Project
Contract ─AWARDED_TO▶ Supplier
Document ─FOR_PROJECT▶ Project;  Document ─HAS_SECTION▶ Section ─HAS_CHUNK▶ Chunk
Chunk ─DESCRIBES (strong: reference or supplier named)▶ Contract
Chunk ─MAY_DESCRIBE (weak: ≥3 rare shared words; excluded from evaluation)▶ Contract
Rating ─RATES▶ Project, ─RATING_EVIDENCE▶ Chunk          Lesson ─TEACHES▶ Project, ─LESSON_EVIDENCE▶ Chunk
```

Measured [M]: 11 node types and 11 structured edge types; 0 orphan edges; 0 duplicate node IDs. The assessment's
example, "link a contract row to an ICR lesson section", is traced end-to-end in notebook 40 on real data
(contract → supplier → project → ISR passage that names the re-bid contract GYTJ201B).

**Honest finding [M]:** contract-to-passage links cover 1.3% of slice contracts (5 strong, 27 weak). Reports discuss
packages and components, rarely individual contracts. So retrieval does not depend on these links: **Vector Search is
the primary path**, and the links add "this passage names your contract" context where they exist.

**Why not a graph database [P]:** queries are 1–3 hops, which joins handle well, and Delta keeps one governed store.
A graph database becomes worthwhile only if multi-hop analytics (e.g. supplier networks across countries) become a
real workload.

### 3.4 Parsing, chunking and embedding of 100+-page reports

- **Structure first:** headings start sections and every chunk keeps its heading path ("V. LESSONS > Procurement");
  page headers, footers and numbers are dropped; figures are skipped and captions kept.
- **Tables** become their own chunks; tables over 800 tokens split into row groups that repeat the header row.
- **Size measured, not guessed:** the first run's median chunk was 53 tokens ("Rating: Moderately Satisfactory"), so
  small pieces merge within a section. The final median is 339 tokens; a 10,000-character cap keeps every chunk under
  the 8,192-token embedding limit [M].
- **Repeats:** ISRs repeat the same blocks every six months. Exact and near repeats are flagged and the latest copy is
  primary; older copies stay searchable by date.
- **Embedding input** = context header (doc type, project, date, section path) + text, with `databricks-gte-large-en`
  (1,024 dimensions [V]); incremental by `(chunk_id, text_hash)`.
- **Languages.** `databricks-gte-large-en` is an **English-only** embedding model. In the demo slice 196 of 9,564
  passages are French (Mali notices and procurement plans, part of one ICR) [M]; they are translated to English **for
  embedding only** (notebook 22: the original text is what is shown, cited and quote-checked), so an English question
  can find them. **Production option [P]:** the Bank's portfolio is multilingual (French, Spanish, Portuguese, Arabic,
  Russian, Chinese...), so the production index should use a **multilingual embedding model** served on Databricks
  (to be selected and verified, e.g. an open multilingual model such as BGE-M3 or multilingual-E5), which removes the
  translation step and its cost; translation remains a fallback for a model that is English-only.
- **Measured [M] (docs/07 §4b):** translation for embedding alone did not change French-source recall (0 of 3); adding
  English keyword matching over the translations and a French version of the question (for French-speaking countries)
  recovered 1 of 3 (recall@5 0.33) and lifted overall recall@5 from 0.52 to 0.57. **Known limitation**: the
  multilingual embedding model is the production fix.

---

## 4. Module 2: Predictive and prescriptive engines (rubric: Analytical & AI Methodology, 30)

### 4.1 Tender duration (timeline benchmarking)

- **Target:** contract signing − earliest matching IFB/REOI notice; 0 < d ≤ 3 years. Main population: **625
  prior-review tenders**; post-review (2,571) is a secondary model [M].
- **Model:** scikit-learn gradient-boosted trees with quantile loss → **P20, P50 and P80**, compared with a median by
  (category, method) baseline. A range is more honest than one number for 625 rows.
- **Features known at the start notice only:** category, method, notice type, log contract value ([A] proxy for the
  estimate), country, lending type, project age, roads share, commitment, earlier tenders in the project, fiscal month,
  WGI and CPIA from the latest year **published** before the notice (a release-lag rule), with a CPIA-missing flag.
- **Validation:** time-based split (latest 20% as test); MAE, median AE, P80 coverage, pinball loss; per country and
  per dataset; ablations without governance and without DS04691.
- **Results [M]** (125 prior-review test tenders from Jan 2023): P50 MAE **100.4 days vs 100.8 for the baseline**,
  P80 coverage **0.81** (pinball 42.8 vs 44.0), but median error 80.5 vs 70.5 days: **level with the baseline**, with a
  well-calibrated range. Post-review (519 test tenders): MAE 69.5 vs 70.3, median AE 52.3 vs 57.5, P80 coverage 0.83.
  **Without governance features the prior-review MAE improves to 94.7**: on 500 training rows WGI/CPIA do not help, so
  the governance hypothesis is not supported on this sample. Method dominates importance (36 days of MAE). Promoted
  to `champion` as version 1 (no earlier champion). The value delivered is the **calibrated P20–P80 range**, not a
  sharper point estimate; with STEP's internal estimate (not public) and more rows, the model should pull ahead.
- **Survival analysis** is proposed rather than built: censoring needs reliably open tenders, and an unmatched notice
  is more often a join failure than an open tender [M: H1].

### 4.2 Re-bidding risk

- **Labels from the public record**, each with its reason: a second IFB ≥ 30 days later (corrigenda, addenda,
  amendments, deadline extensions and "Revised" notices do **not** count: they modify a tender, they don't re-run it),
  a cancelled notice, re-bid wording in notice text (English and French), or a Procurement Plan saying so.
- **Rare-event method:** hierarchical Beta-binomial rates (all → category → category×method → country×category×method),
  each shrunk toward its parent, reported with count and 90% interval ("3 of 41 similar tenders were re-bid or cancelled…"). A
  classifier is trained only with ≥ 50 positives and kept only if it beats the rates on Brier score **and** reaches
  AUC ≥ 0.6.
- **Re-advertisement under a new reference** (same project, same package description with the same numbers in order,
  earlier round without a contract, ≥ 30 days later) is the main source of positives; precision **97%** on 30 random
  flags and **44 / 44** outside the dominant project [M, hand-checked].
- **Results [M]:** 3,224 tenders, **re-bid or cancellation 439 (13.6%)** = 391 re-advertised, 33 re-bid by wording or a
  second IFB, 19 cancelled. One project (P157054, Madhya Pradesh) holds 79% of positives; **without it the rate is
  4.6%**, works 5.1%, goods 4.1%. Time check from Feb 2023: Brier **0.032 hierarchical vs 0.039 global** (18% lower);
  classifier AUC 0.46, **not kept**. Recent rates are a lower bound (right-censoring: a re-advertisement appears a median
  134 days later). The app shows rates with intervals as history, never a forecast.

### 4.3 Amendment risk

No public dataset records amendments [V], so a supervised probability cannot be validated on public data. Instead:
(1) LLM-extracted amendment, extension and overrun **events** from ISR/ICR text, each with a verbatim quote; (2) a
transparent **indicator score** (large works, high value, long predicted duration, weak governance, weak ISR rating,
earlier amendments), labelled "heuristic, not a probability", with every factor and unknown shown. **Measured: the
score does not track the amendment events extracted from reports (Spearman r ≈ −0.08, notebook 70), so it is a
heuristic checklist only, never a risk estimate.** Full-scale path:
STEP amendment records (internal), or the same extraction over all ~8,000 ICRs to create labels.

### 4.4 Agentic RAG

A **fixed tool sequence**, which is easier to test, explain and audit than an open-ended agent:

1. SQL: similar past contracts (category, method, value band, same country first, signed before the as-of date), each
   carrying dataset and coverage status;
2. models: duration range and re-bid or cancellation rate (numbers never come from the LLM);
3. graph: related projects and their documents; project IDs named in a question scope the search;
4. **hybrid** search (vector + keyword: Vector Search HYBRID mode, or BM25 + whole-code identifier matching as a
   fallback, so references like "GYTJ201B" and project IDs are found exactly): A (those projects), B (ICR / IEG lessons
   across the corpus) and, for planning, C (**past procurement notices**: REOI / IFB text with scope and qualification
   criteria, for tender design), all with `doc_date ≤ as-of` and primary copies only; DESCRIBES passages added;
5. merge and rerank: hybrid score + small bonuses for independent sources (IEG > ICR > ISR) and lessons sections, a
   larger bonus for an exact identifier match, at most 2 passages per document;
6. retrieval gate on **pure vector similarity** (cosine; threshold calibrated on the calibration half; an exact
   identifier match passes). Outcome / rating questions are **routed** to ICR, ICR Review and PPAR first. The gate is a
   **weak first filter** (§5); refusals come mainly from the answer step and the claim verifier;
7. Llama 3.3 70B at temperature 0 → JSON claims with citation IDs;
8. guardrails (§5), ending with one batched claim-support verifier call.

The same code (`src/rag/pipeline.py`) runs in the evaluation notebook, the app and the unit tests, with the
data-access functions injected.

---

## 5. Module 3: Guardrails, faithfulness and source attribution

| Layer | Check | On failure |
|---|---|---|
| Retrieval gate | best similarity ≥ calibrated threshold | refuse |
| Evidence as data | passages wrapped in `<evidence id=…>`; tag-like text neutralised; system prompt: instructions inside evidence are ignored | — |
| **Quarantine** | passages with instruction-like text ("ignore previous instructions…") are withheld from the model and reported | withheld |
| Structured output | JSON claims with citations | refuse if unparseable |
| Citation existence | each cited ID was supplied | claim removed |
| Every claim cited | ≥ 1 citation | claim removed |
| Numbers | every number ≥ 10 appears in a **cited** source (format-normalised, no unit conversion) | claim removed |
| Identifiers | every project ID appears in a cited source | claim removed |
| **Claim support** | ONE batched LLM verifier call per answer: each remaining claim next to the exact text of its cited sources; fails closed | claim removed, counted |
| Extraction quotes | lessons, events and ratings quote their passage word for word | item dropped |
| Model numbers | durations and rates come from models and SQL, with provenance | — |

All are unit-tested, including a prompt-injection test showing that the planted passage never reaches the model and
the claim it asks for is rejected. The app shows the sources (title, type, date, section, page, PDF link), the
structured facts, and how many claims the guardrails removed.

**Measured on the held-out test half (26 questions, final run 2026-10-23) [M]:** recall@5 **0.57 strict / 0.83
lenient**; citation validity **1.00**; claim support **0.86–0.905** after guardrails across the evaluation runs (the
verifier removes about 6–9 claims per run; 0 mechanical removals); an independent judge found **97.5–100%** of 40 kept
claims supported. *Run-to-run variance:* Same frozen questions and temperature 0, yet the LLM's answers and the verifier's verdicts vary slightly between runs, so answer-quality metrics are reported as the range over the 2026-10-23 evaluation runs. Also measured: refusal accuracy **3 / 3**; rating-answer accuracy 1 / 1 (after routing to the
ICR; it was wrong before); the planted injection passage was quarantined and its claim did not survive.
**The retrieval gate is a weak first filter:** calibrated cosine threshold 0.682; off-topic questions score 0.66–0.70
and answerable ones 0.66–0.86 (median 0.76), so the distributions **overlap**. On the test half it blocked 1 of 23
answerable and 2 of 3 off-topic questions; the third off-topic question was refused by the answer step. Refusals
therefore rest mainly on the **answer step ("insufficient evidence") and the claim verifier**, not on the gate.

### Conflicting evidence (ISR vs ICR vs IEG)

Ratings are extracted with deterministic regular expressions on the six-point scale, each tied to its passage. Rules
follow the document timeline (PAD → ISRs → ICR → IEG, verified in 50 of 51 projects [M]):
- **final outcome:** IEG review > ICR > latest ISR, among documents published by the as-of date;
- **state at a date:** the latest ISR on or before that date;
- **never hidden:** the chosen value is shown with every other rating and its date, and flags are raised:
  `isr_icr_disconnect` (≥ 2 levels between the last ISR and the ICR), `ieg_downgrade`, `isr_volatility`.

---

## 6. Module 4: Continuous learning and implementation strategy (rubric: Operationalization & Scalability, 15)

### 6.1 Closed loop

One weekly Databricks Job (`jobs/refresh_job.json`):
ingest → silver → documents (new only, parse once) → suppliers → graph → extraction (new chunks only) → embeddings
(new or changed only) → Delta Sync index → model retrain with **champion/challenger** (promote only if P50 MAE is no
worse and P80 coverage is 0.70–0.90 on the latest time slice; the decision is logged in MLflow) → re-bid or cancellation rates → early
warnings → **RAG regression evaluation** (the job fails if recall@5 drops more than 5 points).

- A new ICR becomes searchable, rated and mined for lessons and events on the next run; its rating conflicts are
  flagged automatically.
- New contracts and notices extend the training data; amendments mentioned in new ISRs feed the amendment indicator.
- **User feedback** (👍 / 👎, "wrong source") is stored in `ops.feedback`. 👎 answers are reviewed and added to the
  evaluation set; wrong-source chunks become negative examples for a future learned reranker.

### 6.2 Phased pilot rollout

| Phase | Scope | Exit criteria (go / no-go) [P] |
|---|---|---|
| 0. Offline (this work) | roads, 5 countries, frozen evaluation set | recall@5 ≥ 0.7; claim support ≥ 0.9 after guardrails; duration P50 beats baseline; P80 coverage 0.7–0.9 · **Status [M]: claim support 0.86–0.905 (≈ the 0.9 target), P80 coverage 0.81 ✔, P50 level with baseline (≈), recall@5 0.57 strict / 0.83 lenient (✘ strict, ✔ lenient)** |
| 1. Shadow pilot (3 months) | ~10 procurement specialists in one region, roads; outputs advisory only | ≥ 60% weekly active; ≤ 5% answers flagged wrong-source; no unsupported claim in a 100-answer audit |
| 2. Expansion | all transport + energy/water, all countries; Vector Search scaled up; docs parsed for every active project | metrics hold per sector; refresh job ≤ 4 h |
| 3. Integration | links into STEP planning screens (proposal only); internal data (amendments, bid evaluation reports) under access control | security and data-governance sign-off |

### 6.3 KPIs

| KPI | Type | Measured how |
|---|---|---|
| Retrieval recall@5 / MRR of historical risk factors | offline | frozen evaluation set (test half; the refusal threshold is set on the other half), per country and per question kind incl. tender design: strict 0.57 / lenient 0.83 [M] |
| Citation validity, claim support, judge faithfulness, refusal accuracy | offline | notebook 61: 1.00 / 0.86–0.905 / 97.5–100% / 3 of 3 [M, range over runs] |
| Duration P50 MAE vs baseline, P80 coverage | offline | notebook 50: 100.4 vs 100.8 days; 0.81 [M] |
| Re-bid Brier score vs global rate | offline | notebook 51: 0.032 vs 0.039 [M] |
| Planning cycle time (procurement plan drafting) | online | before/after timing in pilot teams [P] |
| Weekly active users, answers accepted without edit, feedback rate | online | `ops.rag_requests`, `ops.feedback` [P] |
| Forecast accuracy in use | online | predicted P50 vs actual signing for pilot tenders [P] |
| Guardrail removal rate | monitoring | removed / total claims per week (hallucination pressure) [P] |

All of these are on one AI/BI dashboard (`dashboards/`, notebook 85; docs/07 §7): data health, model quality, assistant
quality and usage. Online KPIs show test usage only until a pilot, and the business KPIs appear as the proposed targets above.

### 6.4 Scaling the same design [P]

| Dimension | Slice | Full portfolio (estimate, illustrative) | What changes |
|---|---|---|---|
| Documents | 299 | ~550,000 in Documents & Reports; ~50,000 relevant (PAD/ISR/ICR/IEG for IPF) | parse in batched Jobs; parse once and store |
| Chunks | 5,226 | ~3–5 million | Vector Search standard endpoint with more units; filters unchanged |
| Contracts | 491,020 | same (already full) | none |
| Suppliers | 322,655 records | same | blocking already scales; legal-entity register is future work |
| LLM extraction | slice | incremental only, keyword pre-filter | cost grows with *new* documents, not the corpus |

---

## 7. Architecture and why each component is there

| Component | Role | Why |
|---|---|---|
| Unity Catalog + Delta | one governed store for files, tables, graph, models | lineage and access control in one place; time travel for audits |
| Foundation Model APIs | Llama 3.3 70B (generation, extraction); GTE-large (embeddings) | pay-per-token, no endpoint to manage |
| Vector Search (Delta Sync) | primary retrieval | the index follows the chunk table, which is the retrieval side of the loop; fallback = cosine over the same embeddings |
| MLflow + UC model registry | experiments, versions, champion alias | reproducible models and logged promotion decisions |
| Workflows | weekly refresh | the loop actually runs |
| Databricks App (Streamlit) | user interface | same identity and governance as the data |

---

## 8. Implemented vs proposed

| Implemented, unit-tested (231 local tests) **and run on Databricks** [M] | Built, run once, needs more use | Proposed only [P] |
|---|---|---|
| ingestion (10–11), documents and chunking (20–22), data-quality gate (12), suppliers (30), graph (40), duration model (50), re-bid labels and rates (51), extraction of ratings / lessons / events (55), embeddings + Vector Search hybrid index (60), RAG evaluation with guardrails (61), early warnings (70), app setup and deployment (80), KPI dashboard (85) | Databricks App (deployed; pages need a demo session), weekly refresh job (defined, created paused, not yet scheduled), feedback loop (tables and buttons exist; no pilot users yet) | survival model; learned reranker; multilingual embedding model; legal-entity supplier register; STEP amendment data; OKR / guidance documents in the index; full-portfolio parsing |

## 9. Risks and limitations (stated, not hidden)

1. Duration model valid mainly for prior-review contracts and only level with the baseline on its P50; contract value
   is a proxy for the estimate; recent periods are right-censored.
2. Re-bid labels are observable only where notices exist; one project drives most positives (13.6% → 4.6% without it).
3. Amendment risk is a heuristic indicator on public data (r ≈ −0.08 against extracted events).
4. Contract ↔ passage links are sparse (1.3%); retrieval does not rely on them.
5. The evaluation set is small (26 test questions) and partly synthetic (LLM-written, paraphrased, reviewed by the
   author); the verifier and the judge are the same model family as the generator. Metrics are on a held-out half.
6. The retrieval gate is a weak first filter (overlapping score distributions); refusals rely on the answer step and
   the verifier.
7. **French and other non-English sources** (§3.4): only partly solved. Translating 196 French passages for embedding
   changed nothing measurable; adding English keyword matching over the translations and a French query pass found 1
   of 3 French-source test questions (recall@5 0 → 0.33, n = 3). Known limitation; production fix: a multilingual
   embedding model.
8. Lending type is today's classification, not the one at approval.
9. Supplier identity is brand + country, not legal entity; cross-script aliases are future work.
10. Databricks Free Edition: up to 3 apps, an app stops 24 hours after start, and a **daily serverless compute
    quota** (hit once on 2026-10-23; runs resumed the same day).
