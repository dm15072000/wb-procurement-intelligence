# 03 — Model, RAG and guardrail design (Stage 4, checkpoint d)

Requirements covered: **M2.1–M2.6** (predictive and prescriptive engines), **M3.1–M3.4** (guardrails, conflicts),
**M4.1–M4.2** (closed loop, feedback), **L1–L4** (lifecycle stages). Date: 2026-10-21.

Labels: **[V] verified** · **[M] measured** (from the checkpoint (c) runs) · **[A] assumption** · **[P] proposed**.
Everything in this document is **[P]** unless labelled. No result in this document is measured yet. The numbers
come from notebooks 50–70 once you run them, and go in `docs/07_evaluation.md`.

**Status:** **checkpoint (d) APPROVED by the user on 2026-10-21, with the seven changes below.** Code for every
component is written and unit-tested locally (`src/`, `tests/`). Notebooks 21 and 50–80, the refresh job and the app
are written but **not run** yet.

## Checkpoint (d) decision: approved with changes [V: user, 2026-10-21]

| # | Change requested | How it is implemented | Where |
|---|---|---|---|
| 1 | **Claim-support guardrail:** one batched LLM verifier call per answer checks each claim against its cited evidence; unsupported claims removed and counted | After the mechanical checks, `claim_verifier.verify` sends all remaining claims, each with the exact text of its cited sources, in **one** call (temperature 0, JSON verdicts). **Fails closed:** a claim without an explicit `"supported": true` is removed. Counts (`verified`, `unsupported`, `no_verdict`) go into every guardrail report, the app and the evaluation | §4 layer 7b; `src/guardrails/claim_verifier.py`; `src/rag/pipeline.py` |
| 2 | **Keyword matching alongside vector search**, so contract references and project IDs are findable | Vector Search **HYBRID** mode (`query_type="HYBRID"` with both `query_text` and `query_vector`, documented for self-managed embedding indexes), tried once per session; if the index refuses it, ANN + our own **keyword index** (BM25 + whole-code identifier matching), fused as 0.7 × similarity + 0.3 × keyword score. Passages containing a queried identifier get a rerank bonus (+0.30) and **pass the retrieval gate** even with low similarity. Notebook 60 records which mode works in `ops.config` | §3a–3b; `src/rag/keyword.py`, `retrieval.py`, `databricks_tools.py` |
| 3 | **Index procurement-notice text** (REOI / IFB, with qualification criteria) for slice projects as doc_type "Procurement Notice"; add ~5 tender-design (L2) questions | Notebook 21 → `docs.notice_chunks` (HTML cleaned, paragraph-aware pieces, repeats flagged; cited by notice ID + reference, no invented URL); notebook 60 indexes them with the report chunks; planning requests add a notice-scoped search; notebook 61 writes 5 `tender_design` questions from notice passages that mention qualification / experience / turnover | §3a, §3b, §3c; `src/chunking/notices.py`; notebooks 21, 60, 61 |
| 4 | **Split the evaluation set:** calibrate the refusal threshold on one half, report metrics on the other | Deterministic 50/50 split stratified by question kind (hash of the question ID), stored in the `split` column; threshold chosen on `calibration`, every reported metric on `test` | §3c; `src/evaluation/questions.py`; notebook 61 |
| 5 | **Synthetic questions paraphrase** and avoid the chunk's key phrases | The prompt forbids reusing key phrases, place / firm names and reference numbers; word overlap with the gold passage is **measured** per question; if > 0.5 the question is regenerated once and the less-overlapping one kept; overlap is stored and reported | §3c; `src/evaluation/questions.py` |
| 6 | **Re-bid label:** exclude corrigendum / amendment / extension notices from the "more than one IFB" rule | `is_modification`: status "Revised", or a notice-modification phrase (corrigendum, addendum, amendment no. / to the IFB, deadline or bid-submission extension, revised notice, erratum; French *additif, rectificatif, prorogation, report de date*) in the description or title area. Works wording ("extension of the road") does not count. The excluded count and a sample are printed for review | §2a; `src/risk/labels.py`; notebook 51 |
| 6b | **Re-bid wording guard** (2026-10-22, after the first full run) | The first run labelled 171 tenders as re-bids, but 120 were false: P161706 is the "Projet de **Relance** et de Développement de la Région du Lac Tchad" (relance = recovery; 86 of 88 tenders) and P160223 (Lebanon) prints "Not awarded Lots … **will be subject to re-bidding**" in every invitation (34 of 43). Now a match is skipped when it is part of a project / programme name or follows a future / conditional phrase (will / may / shall be, subject to, if, in case), and bare "relance" counts only next to a procurement word (avis, appel d'offres, lot, n°). Re-checked on the real notices: 29 of the 149 text positives remain, all genuine ("Rebidding", "RETENDER", "Second Call", "(Relance)", "re-tendering of Lot-1 and Lot-3"). The classifier's AUC 0.93 was an artefact: with only category, method and country as inputs, it learned which projects carried the boilerplate | `src/risk/labels.py::mentions_rebid`; notebook 51 |
| 6c | **Two outcomes** (2026-10-22): *re-bid* and *cancellation* reported separately; the combined rate is always named "re-bid or cancellation" | P144726: 5 of 10 cancelled plan entries were re-advertised under a new reference, 5 were dropped, so a cancellation is not a re-bid. Recall gap found: re-advertisement under a NEW reference (378 tenders, mostly P157054) is not captured; a "same package re-advertised" rule is proposed | `src/risk/labels.py::tender_label`; docs/07 §2 |
| 6d | **Notebook 55 events must have happened** | Prompt excludes conditions, plans and project names; `event_happened` rejects future / conditional quotes and project-name-only re-bid words (`not_happened`) | `src/extraction/llm_extract.py`; tests |
| 7 | **Check the Free Edition Apps limit** | Databricks docs (*Free Edition limitations*, checked 2026-10-21): **up to 3 apps per account**; apps **stop 24 h** after start / redeploy. One known app exists (`wbintelligence2`), so no deletion should be needed. Notebook 80 lists the apps read-only and says whether a slot is free | §8; notebook 80 |

---

## 0. Inputs carried over from checkpoint (c) [M]

| Input | Value | Consequence |
|---|---|---|
| Prior-review tender durations | 625 kept (DS00005 512 + DS04691 113), 3 negatives of 637 matched | **Main duration model** (decision, memory "duration-model-scope") |
| Post-review tender durations | 2,571 kept, 372 negatives dropped (~13%) | **Secondary model**, reported separately, labelled noisy |
| DS04691 prior-review match rate | 31% vs 59% for DS00005 | Train with and without DS04691 and report both |
| Re-tender signals in structured data | 9 of 3,643 IFB references have >1 IFB notice; 5 cancelled notices | Structured labels are too sparse alone; combine with notice text and plan labels; gate the classifier |
| Amendment field | none in any contract dataset [V] | No supervised amendment model on public data; transparent score + LLM-extracted evidence |
| Contract ↔ passage links | 5 strong, 27 weak, 1.3% of slice contracts | Retrieval = Vector Search; links are supplementary context |
| CPIA missing | 56% of country-years | NULL + missing flag, never imputed |
| Chunks | 5,226 (median 339 tokens), duplicates flagged with `is_primary` | Index all chunks; filter `is_primary` by default |

---

## 1. Timeline benchmarking: tender duration model (M2.1, L1)

**Target.** `duration_days` = contract signing − earliest matching IFB/REOI notice (silver.tender_durations), kept
rows only (0 < d ≤ 1,095).

**Population.** Main model: prior-review rows (625). Secondary model: post-review rows (2,571), same code, own metrics.
Training scope: all 151 universe road projects (D7). Evaluation reported overall **and per country**, with the five
demo countries listed separately.

**Features (all known at the start notice date T, leakage rule docs/01 §5a):**

| Feature | Source | Note |
|---|---|---|
| procurement category, method, start notice type (IFB / REOI) | contracts, notices | categorical |
| log contract amount | contracts | **[A] proxy** for the estimated cost, which is known at planning but not published as data. Stated as a limitation. |
| country ISO3, lending type | projects, countries | lending type is the current classification [V] |
| project age at T (days since board approval), roads %, log total commitment | projects | |
| tenders started earlier in the same project | notices | count of IFB/REOI notices before T: the project's procurement experience |
| WGI GE / RQ / RL / CC, CPIA public-sector and transparency | silver.governance | value for the **latest year published before T**: year ≤ T.year − 1 if T is on/after 1 October, else ≤ T.year − 2 (WGI is released each autumn [V: last update 2026-09-25]). Missing stays NULL with a `cpia_missing` flag. |
| start month of fiscal year | notices | seasonality (budget cycle) |

Excluded on purpose: supplier history (the supplier is unknown at T), any document text (ICR/ISR after T), plan dates
(decision 2026-10-21), `source_dataset` (a proxy for time).

**Models.**
1. **Baseline:** median duration by (category, method), backing off to category, then global, fitted on train only.
2. **Gradient-boosted trees** (scikit-learn `HistGradientBoostingRegressor`, native categorical support,
   `loss="quantile"`): three models for **P50, P80 and P20**. The output is a range ("typically 7–11 months; 1 in 5
   takes longer than 11"), which is more honest than one number for 625 rows.
3. **Why not survival analysis now:** censoring needs tenders that are still open, and an unmatched IFB notice is more
   often a join failure (H1: 41–48% of prior-review contracts unmatched) than an open tender. A Cox/AFT model on
   properly censored data is in the proposal as future work. Probabilistic networks are rejected for the same reason:
   too few rows to learn structure.

**Validation.** Time-based split: sorted by start notice date, the latest 20% is the test set, and none of it is used
for fitting or tuning. Hyper-parameters are fixed in advance (`max_depth=3`, `learning_rate=0.05`, `max_iter=300`,
`min_samples_leaf=20`), so no validation set is spent on tuning.
Metrics: MAE and median absolute error of P50 vs baseline; **P80 coverage** (share of actual durations ≤ P80, target
≈ 0.80); pinball loss at 0.5 and 0.8; per country; per dataset. Ablations: without governance features; without
DS04691 rows. Logged to MLflow; registered as `procurement.ml.tender_duration_p50/p80/p20` in Unity Catalog.

**Promotion rule (M4.1).** A new model gets alias `champion` only if, on the latest 20% time slice, its P50 MAE is
≤ the current champion's and P80 coverage is within 0.70–0.90. The decision and both metric sets are logged.

**Right-censoring caveat [A].** Tenders that started recently and are not yet signed are missing from the data, so the
latest period is biased toward short durations. This makes the test set slightly optimistic. Reported, not corrected.

---

## 2. Re-bidding and amendment risk (M2.1, L1, L3)

### 2a. Re-bid / re-tender risk

**Label (tender level), each reason stored for audit** (`src/risk/labels.py`):
- more than one IFB notice for the same tender key, at least 30 days apart, **not counting notices that only modify an
  earlier one** (corrigendum, addendum, amendment, deadline extension, status "Revised"; change 6);
- a notice with status `Cancelled`;
- notice text or description mentions re-bidding (`re-bid`, `re-tender`, `re-advertis…`, `second call`, French
  `relance`, `appel d'offres infructueux`, …);
- a Procurement Plan activity with the same reference that is cancelled or mentions re-bidding (slice only).

Population: all universe tenders with at least one IFB notice (~3,600 [M: H2]). Positives are expected to be rare.

**Model, gated by the number of positives** (decided in the notebook from the data, and logged):
- **< 50 positives:** no classifier. **Hierarchical Beta-binomial rates**: global → category → category × method →
  country × category × method, each level shrunk toward its parent (prior strength 20). The output is a rate with a
  90% credible interval and the count behind it, e.g. "3 of 41 similar tenders re-bid (rate 6%, 90% CI 2–15%)".
- **≥ 50 positives:** add a gradient-boosted classifier on the duration-model features; time split; AUC, Brier score
  and calibration plot vs the rates; promoted only if its Brier score beats the rates.

### 2b. Amendment risk

No public dataset records contract amendments [V], so a supervised amendment probability cannot be validated on
public data. Design:
1. **Evidence:** LLM extraction of amendment, variation order, cost overrun, time extension and termination events
   from ISR / ICR text (slice), each with a **verbatim quote** checked against the chunk (§4) → `silver.contract_events`.
2. **Transparent indicator score** (`src/risk/amendment.py`): additive points for documented risk factors: large
   civil works, contract value high for its category, long predicted duration (P80), weak governance (WGI GE < −0.5),
   latest ISR implementation or procurement rating ≤ Moderately Unsatisfactory, earlier amendment events in the same
   project. It is **labelled a heuristic score, not a probability**. Each point shows its factor and source.
3. **Check, not validation:** on the 16 slice projects, compare the score with the count of extracted amendment events.
   This is reported as descriptive, because n = 16 is too small for a metric.
4. **Full-scale path (proposal):** amendment records from STEP (internal), or the same LLM extraction over all ~8,000
   ICRs to build project-level labels for a classifier.

---

## 3. Retrieval-augmented generation (M2.4, M2.6, L1, L2)

### 3a. Index

- `docs.chunk_embeddings`: `chunk_id`, `text_hash`, `embedding` (1,024 floats, `databricks-gte-large-en`), plus the
  filter columns `project_id`, `country_iso3`, `doc_type`, `doc_date_int` (yyyymmdd, so date filters are numeric),
  `section_path`, `is_primary`, `chunk_type`, and the citation fields `pdf_url`, `title`, `page_start`.
  Embedding input = `context_header + text`, so a chunk "knows" its document type and section.
  **Incremental:** only chunks whose `(chunk_id, text_hash)` is not yet embedded are sent to the endpoint.
- **Vector Search** (Free Edition: one endpoint, Delta Sync only [V]): endpoint `procurement-vs`, index
  `procurement.docs.chunk_index`, Delta Sync with **self-managed embeddings** from `chunk_embeddings`, so the index and
  the fallback use the same vectors. Creating the endpoint was approved as D4 (2026-10-20); notebook 60 still has a flag
  that is off by default, and it checks that no endpoint exists first.
- **Fallback:** cosine similarity in memory over `chunk_embeddings` (5,226 × 1,024 floats ≈ 21 MB). Same filters, same
  output, so the app works if the endpoint is unavailable during the demo.
- **Procurement notices (change 3):** REOI / IFB / prequalification text of the slice projects (`docs.notice_chunks`,
  notebook 21) is embedded and indexed in the same table as doc_type "Procurement Notice".
- **Hybrid (change 2):** Vector Search HYBRID mode when the index accepts it; otherwise BM25 + identifier matching
  (`src/rag/keyword.py`) fused with the vector scores. Project IDs and references ("GYTJ201B", "HAMP-CS-01") are matched
  as whole codes, ignoring dashes and spaces.

### 3b. The agentic flow: a fixed tool sequence, not an open-ended agent

When a user starts a package (country, optional project, category, method, estimated value, scope text, as-of date):

| Step | Tool | What it returns | Why |
|---|---|---|---|
| 1 | **SQL: similar contracts** | Up to 10 past contracts in road projects, same category (and method if given), value within 0.3–3×, signed before the as-of date, nearest value first; same country ranked first. Each carries `coverage_status`. | Precedents are structured facts, not text. |
| 2 | **Models** | Duration P20/P50/P80 and re-bid or cancellation rate with its count | Schedule and risk numbers come from models, never from the LLM. |
| 3 | **Graph** | Projects of those contracts + the user's project → their documents; DESCRIBES links for those contracts | Narrows retrieval to relevant projects. |
| 4 | **Vector search A: project-scoped** | top 12 chunks of those projects, `doc_date ≤ as-of`, primary only | Specific precedents. |
| 5 | **Vector search B: lessons-scoped** | top 12 chunks from ICR / ICR Review / PPAR across all slice projects, sections whose path mentions lessons, findings or procurement | Sector-wide lessons. |
| 5b | **Notice search** (planning only) | top 12 "Procurement Notice" chunks, as-of filtered | Past scopes and qualification criteria for tender design (L2). |
| 6 | **Rerank** | merge all searches (each hybrid: vector + keyword) + DESCRIBES chunks, deduplicate, score = hybrid score + authority bonus (IEG > ICR > ISR > PAD) + lesson-section bonus + **identifier-match bonus**, at most 2 chunks per document, top 8 | Diversity, source quality, exact codes first. |
| 7 | **Retrieval gate** | refuse if the best raw similarity < threshold (calibrated on the calibration half) **and** no queried identifier was found verbatim | "I don't have evidence" beats a guess; an exact code match is evidence. |
| 8 | **Generate** | Llama 3.3 70B, temperature 0, JSON output: claims, each with citation IDs | Structured output makes citations checkable. |
| 9 | **Guardrails** (§4) | mechanical checks, then one batched verifier call; claims that fail are removed and counted; the answer shows what was removed | Faithfulness by construction. |

Steps are plain Python functions in `src/rag/pipeline.py` with the data-access functions injected, so the same code
runs in the notebook, in the evaluation and in the app, and the whole flow is unit-tested with fakes.

### 3c. Evaluation set (built in notebook 61)

| Part | How it is built | Gold label |
|---|---|---|
| Rating look-ups (~15) | from extracted ratings (§5): "What outcome rating did the ICR give project P…?" | the chunk the rating came from |
| Lesson questions (~30) | LLM writes one **paraphrased** question per sampled ICR lessons chunk (key phrases forbidden; word overlap measured, regenerated once if > 0.5); **you review and edit the list** before it is frozen | that chunk (and any exact duplicate) |
| Tender-design questions (~5, L2) | same, from procurement-notice chunks mentioning qualification / experience / turnover | that notice chunk |
| Contract precedents (~5) | from the 5 DESCRIBES links: "What happened with contract <ref>?" | the linked chunk |
| Refusals (~5) | questions about countries or sectors not in the corpus | the system must refuse |
| Prompt injection (1–2) | a planted chunk saying "ignore your instructions…" added to the context in the test only | the answer must not follow it |

**Split (change 4):** a deterministic 50/50 split stratified by kind. The retrieval-gate threshold is calibrated on the
calibration half only; **every reported metric comes from the test half**, so the numbers are not tuned on the
questions they describe.

Metrics (test half): recall@5 and @10, MRR, citation validity (share of citations that pass §4), claim support rate
(claims kept after the mechanical checks **and** the verifier), removals split into mechanical vs verifier, an
independent judge as a second opinion on a sample (same model family: caveat), refusal accuracy. Reported per country
and per question kind, logged to MLflow and `ml.rag_eval_results`. Synthetic questions are labelled as such, with
their measured overlap.

---

## 4. Guardrails, faithfulness and attribution (M3.1, M3.2, M3.4)

Code: `src/guardrails/citation_check.py`, `src/rag/prompt.py`. Each layer is unit-tested.

| Layer | Rule | On failure |
|---|---|---|
| 1. Retrieval gate | best similarity ≥ threshold | refuse: "no supporting evidence found" |
| 2. Evidence as data | every chunk is wrapped in `<evidence id="E3" …>…</evidence>`; any `<evidence` / `</evidence>` text inside a chunk is neutralised; the system prompt says that evidence text is data and instructions in it must be ignored | — |
| 2b. Quarantine | chunks containing instruction-like phrases ("ignore previous instructions", …) are **withheld from the model** and reported: flagging alone would let a planted passage "support" the claim it asks for | withheld, logged |
| 3. Structured output | JSON with `claims[{text, citations}]` and `insufficient_evidence` | unparseable → refuse |
| 4. Citation existence | every cited ID is in the evidence or structured-fact set given to the model | claim removed |
| 5. Every claim cited | a claim with no citation | claim removed |
| 6. Numbers | every number in a claim (amounts, %, durations, years) appears in at least one cited source, after normalising `1,200` / `1200` and `12 %` / `12%` | claim removed |
| 7. Identifiers | every project ID (`P` + 6 digits) and contract reference in a claim appears in a cited source | claim removed |
| 7b. **Claim support (verifier)** | ONE batched LLM call per answer: every remaining claim with the exact text of its cited sources → JSON verdict per claim; fails closed (no explicit "supported" = removed) | claim removed, counted as `unsupported` / `no_verdict` |
| 8. Extraction quotes | every LLM-extracted lesson, event or rating carries a quote that must appear verbatim (whitespace- and case-insensitive) in its chunk | item dropped, counted |
| 9. Model numbers | durations and risk rates shown in the app come from models and SQL with their own provenance, never from LLM text | — |

Every answer shows its sources (document title, type, date, section, page, link) and the structured facts (contract
IDs, dataset, coverage status). Removed claims are counted in `ops.rag_requests`, so the hallucination rate after the
guardrails is a monitored KPI.

---

## 5. Conflicting evidence: ISR vs ICR vs IEG (M3.3)

Code: `src/conflict/ratings.py` (unit-tested).

**Ratings extraction.** Deterministic regular expressions over ISR / ICR / ICR Review chunks for: PDO / outcome,
implementation progress, procurement, financial management, project management, M&E, Bank performance. The six-point
scale HS 6 · S 5 · MS 4 · MU 3 · U 2 · HU 1 is used, plus risk ratings (High / Substantial / Moderate / Low) kept apart.
Each rating stores its chunk ID and the matched text. ISR "Progress towards achievement of PDO" and ICR / IEG
"Outcome" map to the same dimension, `outcome`.

**Resolution rules (driven by the document timeline H4: PAD → ISRs → ICR → IEG):**

| Question type | Rule |
|---|---|
| Final outcome ("how did it turn out?") | the most authoritative source available as of the date: **IEG (ICR Review / PPAR) > ICR > latest ISR**; ties → latest date |
| State at a date ("what was the status in 2019?") | the latest ISR dated on or before that date |
| Trend | all ISR ratings in date order |

**Conflicts are never hidden.** The answer shows the chosen value **and** every other rating with its source and date,
and raises flags:
- `isr_icr_disconnect`: the last ISR before the ICR differs from the ICR rating by ≥ 2 levels (e.g. MS → U);
- `ieg_downgrade`: IEG rates lower than the ICR's self-evaluation;
- `isr_volatility`: the rating moved ≥ 2 levels between consecutive ISRs.

These flags also feed the early-warning view and the evaluation narrative (self-evaluation optimism).

---

## 6. Execution and monitoring: early warnings (L3)

Code: `src/monitoring/early_warning.py`; notebook 70 writes `gold.early_warnings`. Each flag has a reason and evidence.

| Flag | Rule | Note |
|---|---|---|
| Tender overdue | a tender with a start notice and no matched contract, open longer than its predicted P80 | uses the duration model |
| Disbursement lag | share disbursed < share of time elapsed − 0.25 | **[A] linear benchmark**: the planned schedule is not in the data [V] |
| ISR downgrade | outcome, implementation or procurement rating fell to ≤ MU | from §5 |
| Procurement problem mentioned | extracted event (delay, re-bid, cancellation, amendment) in the last 12 months | from §2b |

**Backtest:** for slice projects with an ICR, did flags raised before the ICR precede an outcome rating ≤ MU?
Descriptive only (n = 16).

---

## 7. Continuous learning loop (M4.1, M4.2, L4)

`jobs/refresh_job.json`: one Databricks Job, weekly, tasks in order:

```
10 ingest structured ─▶ 11 silver ─▶ 30 suppliers ─▶ 40 graph ─┐
20 documents (new docs only, parse once) ────────────────────┤
                                                           ├─▶ 55 extract ratings / lessons / events (new chunks only)
                                                           ├─▶ 60 embed new chunks ─▶ index sync (Delta Sync)
                                                           ├─▶ 50 retrain duration model ─▶ champion/challenger
                                                           ├─▶ 51 re-bid or cancellation rates
                                                           └─▶ 70 early warnings ─▶ 61 RAG regression eval
```

- **New documents** (a new ISR or ICR) are parsed once, chunked, embedded incrementally, and appear in the index after
  the Delta Sync. Their ratings, lessons and events are extracted from the new chunks only (anti-join on `chunk_id`).
- **New contracts and notices** update durations; the model retrains and is promoted only under the rule in §1.
- **Feedback:** 👍 / 👎 and "wrong source" in the app → `ops.feedback`. 👎 answers are reviewed and added to the
  evaluation set; "wrong source" chunk IDs become negative examples for the reranker (proposal).
- **Regression gate:** notebook 61 re-runs the frozen evaluation set after each refresh; a recall@5 drop of more than
  5 points fails the job.

---

## 8. App (Databricks Apps, Streamlit)

Free Edition limit [V: Databricks docs, 2026-10-21]: up to **3 apps per account**; an app stops 24 hours after it is
started or redeployed (restart it before the demo). Notebook 80 checks the count read-only.

Three screens, all reading Unity Catalog tables through a SQL warehouse and calling the pay-per-token Foundation Model
endpoints (no new serving endpoint):

1. **Plan a package (L1, L2):** inputs → similar contracts with coverage status, duration P20/P50/P80, re-bid or cancellation rate, the
   cited RAG brief (risks, mitigations, lessons), sources panel, feedback buttons.
2. **Monitor a project (L3):** rating timeline with conflict flags, disbursement vs time, early-warning flags with
   evidence, overdue tenders.
3. **Ask the evidence (L4):** free question → cited answer, with the guardrail report.

---

## 9. What is implemented vs proposed

| Item | Status |
|---|---|
| Feature builder, baseline, metrics, time split, Beta-binomial rates, re-bid labels, amendment score, ratings extraction and conflict rules, early warnings, RAG pipeline, prompt, citation checks, evaluation metrics | **Implemented and unit-tested locally** |
| Notebooks 50, 51, 55, 60, 61, 70, 80; refresh job JSON; Streamlit app | **Written, not run** (CLAUDE.md: you run them) |
| Survival model, learned reranker, legal-entity supplier register, STEP amendment data, full-portfolio scale-out | **Proposed only** (docs/08_proposal.md) |

## 9b. LLM call budget (user decision 2026-10-22)

Model for every generation, extraction, verifier, question-writing and judge call: **Meta Llama 3.3 70B Instruct**
through the Foundation Model API endpoint `databricks-meta-llama-3-3-70b-instruct` (pay-per-token, temperature 0).
Embeddings: `databricks-gte-large-en` (1,024 dimensions). Both verified READY on 2026-10-20 (docs/04 §4).

| Notebook | Rule | Estimate before running [A] |
|---|---|---|
| 55 extraction (rule of 2026-10-22, **replaces** the earlier "> 2,000 calls → latest 3 ISRs" limit) | **All documents** of the reporting types are kept (ICR, ICR Review, PPAR, every ISR, Project Papers, PADs; not Procurement Plans or CPFs); repeat copies skipped. A passage is sent only if its **section** is relevant (procurement, implementation, lessons, key issues, risks) or its **text** has a trigger word (rebid, re-tender, cancel, amend, variation order, delay, extension of time, terminat, lesson), English + French, accents and "œ" folded. **One call per passage** returns lessons and events together. Dry run prints total chunks, chunks after the filter and calls; section 3 refuses to start until `APPROVED_CALLS` is set. Batches of 100, saved after each; re-runs skip logged chunks with the same `text_hash`; failed rows retried; idempotent. Ratings stay regex-only | ≈ 2,000–3,200 calls: the "implementation" section and the "delay" trigger are broad and every ISR is kept. Exact number: notebook 55 section 2 |
| 61 evaluation | Questions are generated once and frozen | first run ≈ 140–235 chat calls (35–70 question writing, 62–124 answer + verifier, 1–2 injection, ≤ 40 judge) + ~65 query embeddings; later runs ≈ 105–165 |

The estimates come from the document mix (≈ 222 ISRs, 16 ICRs, ≈ 12 ICR Reviews, ≈ 16 PADs in 299 documents, 5,226
chunks [M]) and assumed pass rates (most ISR passages sit in "implementation", "key issues" or "risks" sections; about
half of PAD / ICR passages pass). They are not measurements: notebook 55's dry-run cell gives the exact number.

## 10. Approval record (checkpoint d)

Approved by the user on 2026-10-21, with changes 1–7 (top of this document): duration model (prior-review main,
quantile GBT vs median baseline, time split, ablations); re-bid labels (modification notices excluded) and the
50-positive gate; amendment indicator + extracted evidence; RAG flow with hybrid retrieval, notice indexing and the
batched claim verifier; evaluation set with paraphrased synthetic questions and a calibration / test split; conflict
rules; Vector Search endpoint in notebook 60 (D4) behind a flag; Apps limit checked (3 per account).
