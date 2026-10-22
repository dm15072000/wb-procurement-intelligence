# 07 — Evaluation results (Stage 6)

Status: **template: no results yet.** Every cell marked _[run]_ is filled from the summary cell of the named
notebook after you run it. Nothing here may be filled by estimate (CLAUDE.md: never invent results).

## 1. Tender duration (notebook 50, `ml.duration_model_report`)

| | n train | n test | test from | P50 MAE (days) | median AE | P80 coverage | pinball 0.8 |
|---|---|---|---|---|---|---|---|
| Baseline (median by category × method), prior review | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| GBT quantile, **prior review (main)** | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| GBT, post review (secondary) | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| Ablation: no governance features | _[run]_ | | | _[run]_ | | _[run]_ | |
| Ablation: without DS04691 training rows | _[run]_ | | | _[run]_ | | _[run]_ | |

Per country (prior review, test set): _[run]_ · Per dataset: _[run]_ · Top permutation importances: _[run]_ ·
Promotion decision: _[run]_

## 2. Re-bid or cancellation risk (notebook 51, `ml.rebid_report`)

**Run 1 (2026-10-22) [M], label defect found:** 171 positives of 3,224 tenders, but **120 were false**:
P161706's project is named "Projet de **Relance** et de Développement de la Région du Lac Tchad" (relance = recovery;
86 of 88 tenders) and P160223 (Lebanon) prints "Not awarded Lots … **will be subject to re-bidding**" in every
invitation (34 of 43). The classifier's AUC of 0.93 was an artefact of those two projects.

**Run 2 (fixed wording rule, 2026-10-22) [M]:** 3,224 tenders · **51 positives (1.6%)**: wording 29, cancelled plan
entries 19, a second IFB ≥ 30 days later 4 · 35 IFB notices set aside as modifications (10 sampled, all genuine
corrigenda / addenda / deadline extensions) · time check from 2023-02-23: 646 test tenders with only **4 positives**;
Brier hierarchical 0.0066 vs global rate 0.0063 (no measurable gain) · classifier trained (≥ 50 positives) but
**AUC 0.50**, not kept. Conclusion: shown as descriptive rates with 90% intervals, not as a prediction.

**Two outcomes, reported separately (from the next run of notebook 51):** *re-bid* (second IFB, re-bid wording, plan
says re-bid) and *cancellation* (cancelled notice or plan entry). The rates use the combined outcome, always named
**"re-bid or cancellation"** in the app, the RAG facts and the docs. Expected from run 2's reasons: about 33 re-bid and
19 cancellation, with little overlap (exact counts printed as `positives_by_outcome`).

**P144726's cancelled plan entries (10 of its 13 positives; the other 3 are "Retender" notices) [M]:**
| Package (plan reference) | Later round found? | Verdict |
|---|---|---|
| AMR-01 (IN-GSDMA-13515, Jul 2017) | re-advertised as IN-GSDMA-42799 (Jan 2018), awarded 2022 | re-bid under a new reference |
| BR-2 (IN-GSDMA-42115, Jan 2018) | re-advertised as IN-GSDMA-60341 (May 2018), awarded 2023 | re-bid under a new reference |
| JMN-1 (IN-GSDMA-42300, Jan 2018) | re-advertised as IN-GSDMA-52294 (Jun 2018), awarded 2023 | re-bid under a new reference |
| Gujarat EWDS system integrator (IN-GSDMA-115480, Aug 2019) | IN-GSDMA-156902 (Mar 2020), same scope | re-bid under a new reference |
| GS-22 (IN-GSDMA-60352, Jul 2018) | IN-GSDMA-81100 (Nov 2018); GS-22 was advertised 4 times (Jun 2017 – Nov 2018), never awarded | re-bid |
| GS-22 (IN-GSDMA-81100, Nov 2018) | none | dropped (after 4 rounds) |
| GS-24 (IN-GSDMA-42297), GS-25 (IN-GSDMA-42299), GS-07R (IN-GSDMA-4729), Gandhidham cable works (IN-GSDMA-71811) | none | dropped |

So **5 re-bids, 5 dropped**: a cancellation is not a re-bid, which is why the two outcomes are now separate.

**Recall check (2026-10-22) [M].** Population: tenders NOT labelled after the fix whose notices contain a re-bid
trigger word (EN + FR). A broad text search finds 1,518 such notices; 1,393 match only inside other words ("pre-bid"
2,776 times, "pre-tender" 52, "prebid" 1). French forms such as "relancé", "infructueux" or "deuxième appel" occur in
none of them. The other **125 notices (120 tenders) contain a whole-word trigger, and all 125 are the P161706 project
name or Lebanon's conditional line** (every match in each notice was checked, not just the first). Random sample of 25
(seed 42; 18 P161706, 7 P160223), all verdict *not a re-bid*:

| # | Tender | Notice type, date | Snippet around the trigger word | Matches in notice | Verdict |
|---|---|---|---|---|---|
| 1 | P161706:CMMINEPAT247008GORFQ | Contract Award, 2022-09-27 | …ion de fournitures de bureau pour l'unit de mise en uvre du Projet de Relance et de Dveloppement de la rgion du Lac Tchad.… | 1 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 2 | P161706:CMMINEPAT395891GORFB | Invitation for Bids, 2024-10-22 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 3 | P161706:CMMINEPAT477214CWRFB | Invitation for Bids, 2025-04-05 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 4 | P161706:CMMINEPAT272390GORFB | Invitation for Bids, 2024-03-04 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 5 | P161706:CMMINEPAT395869CWRFB | Invitation for Bids, 2024-03-26 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 6 | P161706:CMMINEPAT395890GORFB | Invitation for Bids, 2024-03-26 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 12 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 7 | P161706:CMMINEPAT265146CWRFQ | Invitation for Bids, 2024-08-13 | …iale, l’accord de crédit N°6643-CM en vue du financement du Projet de Relance et de Développement de la Région du Lac Tcha… | 2 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 8 | P161706:TDPROLAC411653CWRFB | Invitation for Bids, 2024-11-19 | …des Finances, du Budget, de l’Economie et du Plan Projet : Projet de Relance et de Développement de la Région du Lac Tcha… | 3 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 9 | P161706:CMMINEPAT265115CWRFB | Invitation for Bids, 2022-06-16 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 10 | P161706:CMMINEPAT329512GORFB | Invitation for Bids, 2023-05-06 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 11 | P161706:CMMINEPAT458697GORFB | Invitation for Bids, 2025-01-27 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 12 | P161706:CMMINEPAT265151CWRFQ | Invitation for Bids, 2023-02-25 | …iale, l’accord de crédit N°6643-CM en vue du financement du Projet de Relance et de Développement de la Région du Lac Tcha… | 2 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 13 | P161706:CMMINEPAT300652CWRFB | Invitation for Bids, 2023-08-15 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 14 | P161706:CMMINEPAT477127CWRFB | Invitation for Bids, 2025-04-05 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 15 | P161706:CMMINEPAT395851CWRFB | Invitation for Bids, 2024-03-27 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 16 | P161706:CMMINEPAT395856CWRFB | Invitation for Bids, 2024-03-26 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 6 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 17 | P161706:CMMINEPAT295203GORFQ | Invitation for Bids, 2023-05-13 | …-- GENERAL DIRECTORATE OF PLANNING AND REGIONAL DEVELOPMENT PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHA… | 3 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 18 | P161706:TDPROLAC288401CWRFB | Invitation for Bids, 2022-12-22 | …çu un "Don” de la Banque Mondiale pour couvrir les coûts du Projet de Relance et de Développement de la Région du Lac Tcha… | 1 (all the same phrase) | Not a re-bid: project name (relance = recovery) |
| 19 | P160223:RM016 | Invitation for Bids, 2023-11-03 | …hase 4) Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 20 | P160223:REPW003A | Invitation for Bids, 2023-05-26 | …2 Chouf Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 21 | P160223:PW004 | Invitation for Bids, 2020-11-24 | …ut of 13 bids. Not awarded packages within the REP will be subject to re-bidding excluding previously awarded bidders. Th… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 22 | P160223:RM012 | Invitation for Bids, 2023-08-30 | …hase 2) Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 23 | P160223:RM007 | Invitation for Bids, 2023-02-23 | …aa Aley Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 24 | P160223:RM004 | Invitation for Bids, 2023-01-19 | …aa Aley Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |
| 25 | P160223:RM011 | Invitation for Bids, 2023-08-30 | …hase 2) Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The Council for Development and Reco… | 1 (all the same phrase) | Not a re-bid: conditional boilerplate (future) |

Notice 21 (P160223:PW004) was read in full: a lot-allocation rule ("may be awarded a maximum of two bids out of 13 …
not awarded packages … will be subject to re-bidding"), not a re-bid. **Missed real re-bids among trigger-word
notices: 0 of 125.**

**Recall gap found outside the trigger words [M]:** re-bids advertised again under a **new reference** are invisible
to the "same reference, second IFB" rule and usually carry no re-bid word. Counting tenders whose identical package
description (≥ 25 characters) is advertised again ≥ 30 days later in the same project: **378 tenders in 22 projects,
375 not labelled**. Most are Madhya Pradesh rural-road packages (P157054), e.g. "MP04WB-03: Upgradation and
resurfacing of 14 Gravel Roads in Bhind District", advertised 7 times (May 2017 – Jun 2020) and never awarded. The
label's recall was therefore low. **Fixed by the rule below.**

**Re-advertisement under a new reference: `re_advertised_new_ref` (added 2026-10-22, `src/risk/readvertise.py`) [M].**
Rule: same project; package descriptions identical after normalising (case, punctuation, spacing: "MP04WB - 03" =
"MP04 WB-03") or ≥ 0.95 similar with the same distinctive words, and the **same numbers in the same order**; at least
25 characters and 3 non-generic words; the earlier tender has **no matched contract** and the later one starts **≥ 30
days** after it; identical descriptions started < 30 days apart are parallel lots and skipped. The earlier attempt is
flagged, reported separately from wording-based re-bids, and counted in "re-bid or cancellation".

On the 3,224 tenders of run 2: 3,079 eligible (145 too short or generic) · **324 chains (721 rounds), 211 ending in a
signed contract** · 4 parallel-lot groups skipped · 6 earlier rounds already had a contract (not flagged) · **391
tenders flagged**, only 3 of which were already labelled by wording / plan / second IFB. Concentrated: **345 of 391 in
P157054** (Madhya Pradesh rural roads; whole batches were re-advertised on the same days, e.g. 26–28 Sep 2017, 12 Mar
2019, 4 Sep 2019), 13 in P144726, the rest in 16 projects.

**Precision [M, hand-checked]:** random sample of 30 flags (seed 30): **29 correct (97%)**. The one wrong pair was
"Tender 4 … Lot 5" vs "Tender 5 … Lot 4" (P170267), matched because numbers were compared as an unordered set; fixed
(numbers must match in order), after which it links to its true successor ("Tender 4 … Lot 5" re-issued May 2024).
Because P157054 dominates a random sample, **all 44 flags outside P157054 were also checked: 44 correct**; 24 of them
carry a re-advertisement suffix in the new reference ("R", "RE", "A", "R1", "RE1", "1"). A unit test then exposed
spacing variants of the same code ("MP04WB" vs "MP04 WB"); comparing without spaces added 74 flags (72 in P157054);
**10 of 10 sampled were correct**. Accepted: precision ≥ 95% by every check; recall is not measured (descriptions
that were reworded between rounds are still missed).

**Durations from the first notice of a chain [M] (`gold.tender_durations_chain`, original kept):** **206 of 3,202**
durations belong to a round that had earlier failed rounds: **199 post-review, 7 prior-review**. Counting from the
first notice adds a median of **136 days** (quartiles 89 / 136 / 176; range 52–1,413); medians move from 85 → 219
days (post) and 251 → 630 days (prior); 3 would exceed the 3-year limit.

**Which duration the model uses: the original (per round).** (1) It answers the question the planner asks at the
start notice ("once advertised, how long until signing?"), with one clear definition and no dependence on a fuzzy
match; (2) the risk that a package must be re-advertised is already a separate, measured output (the "re-bid or
cancellation" rate), so adding it into the duration target would count it twice; (3) the main prior-review model is
almost unaffected (7 of 625 rows) and the change is concentrated in one project (P157054). The chain duration is kept
and shown as context ("this package took N days from its first advertisement"). Notebook 50 was therefore **not**
re-run: its target did not change.

**Run 3 of notebook 51 (with the rule, 2026-10-22) [M]:** 3,224 tenders · **re-bid or cancellation: 439 (13.6%)** =
re-advertised under a new reference 391 · re-bid (wording 29, second IFB 4; 33 tenders) · cancellation 19 (1 tender is
both re-bid and cancelled; no re-advertised tender also has re-bid wording). Rates: all tenders 13.6% (90% interval
12.7–14.6%); works 15.9% (417 / 2,615), the other categories 4.4% or less. Time check from 2023-02-23: 646 test
tenders, **11 positives**; Brier **0.032 hierarchical vs 0.039 global rate** (the grouped rates now beat one overall
rate, 18% lower error). The test period's positive rate (1.7%) is far below training (16.6%): P157054's mass
re-advertising happened in 2017–2022, so the rates are high for recent tenders and are read as history, not a
forecast. Classifier: **AUC 0.46, not kept**. In this run's first pass it was marked "kept" because the gate compared
Brier only (a model predicting "low" everywhere wins on Brier when the test period has few positives); the gate now
also requires AUC ≥ 0.6. Nothing downstream used the classifier (the app uses the rates).

**How much one project drives the rate [M] (gold.rebid_labels, run 3):**

| Category | Tenders | Re-bid or cancellation | Rate | Without P157054: tenders | positives | rate |
|---|---|---|---|---|---|---|
| **All** | 3,224 | 439 | **13.6%** | 2,025 | 94 | **4.6%** |
| Works | 2,615 | 417 | 15.9% | 1,423 | 72 | 5.1% |
| Goods | 542 | 22 | 4.1% | 535 | 22 | 4.1% |
| Non-consulting services | 42 | 0 | 0% | 42 | 0 | 0% |
| unknown category | 24 | 0 | 0% | 24 | 0 | 0% |
| Consultant services | 1 | 0 | 0% | 1 | 0 | 0% |

P157054 (Madhya Pradesh rural roads, 1,199 IFB tenders) holds **345 of the 439 positives (79%)**, all of them works
packages re-advertised under new references (345 of the 391 re-advertisement flags). Without it the works rate is
5.1% and the overall rate 4.6%; goods are unaffected. The hierarchical rates compare within country + category +
method, so an Indian works tender is compared mostly with P157054, while other countries' rates are not inflated by it.

**The low rate from 2023 is partly right-censoring [A, supported by M].** By start year, the rate without P157054 is
2018 22% (23 / 105), 2019 4.5%, 2020 2.5%, 2021 4.1%, 2022 7.7%, then **2023 2.0%, 2024 1.2%, 2025 1.8%, 2026 2.6%**
(data to 2026-10-20). A re-advertisement appears a median **134 days** after the previous round (90% within 223 days;
outside P157054 median 182, 90% within 485 days), and a plan entry is marked cancelled only when the plan is next
revised. So tenders started in roughly the last 6–16 months have not yet had time to be re-advertised or cancelled,
and their observed rate is a lower bound. Part of the drop is real (P157054's mass re-advertising ended in 2022);
the time-split test (test period from Feb 2023) is affected by both, which is why its base rate (1.7%) is far below
training (16.6%).

**Time from first advertisement: a monitoring metric only (user decision 2026-10-22).** The duration model keeps the
per-round duration as its target. "Time from first advertisement" (gold.tender_durations_chain) is shown only as a
monitoring metric, e.g. "package advertised 3 times, 630 days since the first advertisement", never as a model
prediction.

## 3. Extraction (notebook 55)

Events must have **happened**: the prompt excludes conditions, plans and words inside project names, and a mechanical check rejects event quotes that are future or conditional ("will be subject to re-bidding", "sera relancé") or whose only re-bid word is a project name ("Projet de Relance…"); rejections are counted as `not_happened` (unit-tested with both real examples).

**Dry run (2026-10-22) [M]:** 5,226 chunks · 4,310 in reporting document types (excluded: Country Assistance Strategy 383, Procurement Plan 306, CPF 227) · 268 repeat copies skipped · first dry run **1,767 calls**. Bug found in the filter: the French termination trigger "résili" also matched English "resilience" / "resilient" (582 hits; 222 passages passed for that reason alone). Fixed to the French termination words only (*résiliation, résilié, résilier*) with a word start; recount on the same chunks: **1,548 calls** (ISR 447, PAD 414, ICR 370, Project Paper 180, ICR Review 137). Ratings (section 1, regex): 230 ratings for 16 projects, 3 rating conflicts.

**Extraction run (2026-10-22, APPROVED_CALLS = 1,548, approved by the user) [M]:** **1,548 of 1,548 passages processed in 16 batches, 0 call errors, not stopped by quota** (Llama 3.3 70B via `ai_query`, temperature 0). Kept: **1,199 lessons** (procurement capacity 279, contract management 272, design quality 183, safeguards 92, supervision 70, market capacity 20, packaging 6, specifications 3, other 274) and **1,042 events** (procurement delay 334, amendment 216, cancellation 194, time extension 167, termination 73, cost overrun 23, re-bid 20, complaint 15). 760 passages gave nothing usable. Rejected by the checks: events 341 with a type outside the fixed list, 83 quotes not found word for word, **89 not happened** (conditions / plans / project names), 1 unparseable; lessons 29 quotes not found, 1 unparseable. Graph: 1,199 Lesson and 230 Rating nodes, 0 orphan edges. Rating conflicts: 1 IEG downgrade, 2 ISR volatility.

**Event → contract links: 3 of 1,042.** The first pass linked 0 because the LLM writes references with extra words ("Contract HAMP-GD-02"); codes inside the reference are now matched (`match_contract`) and saved events are re-linked each run. Only 26 references contain any code at all, and most are loan numbers ("IDA-59380-001") or restructuring sequences: reports describe problems at project or package-group level ("three contracts in Bihar", "works contracts for 285 packages"), which matches the 1.3% passage-link coverage at checkpoint (c). Events are therefore used per project (early warnings, L4), not per contract. Precision of lessons and events is not yet measured: notebook 55 section 6 prints 20 of each for your review.

**The 341 events rejected for an unknown type cannot be listed yet.** The run logged only rejection COUNTS, not the
LLM replies, so the type values the LLM wrote are not stored anywhere (a gap in the notebook, fixed). From now on:
(1) every raw reply is stored in `ops.extraction_replies`; (2) unknown type values are logged by name
(`bad_type_values`); (3) mode **revalidate** re-checks stored replies with the current rules and the type map
`EVENT_TYPE_SYNONYMS` (kept empty until real values are seen, never guessed) **without any LLM call**. To list the 341,
mode **redo_bad_type** re-sends only the **176 passages** that had unknown types (176 calls, needs approval); after
that, mapping types and re-validating costs nothing.

Ratings rows _[run]_ · conflicts by flag _[run]_ · lessons kept / rejected _[run]_ · events kept / rejected _[run]_ ·
precision on 20 lessons and 20 events: _[your review]_ · ratings sample check (20): _[your review]_

## 4. Retrieval and answers (notebook 61, `ml.rag_eval_results`)

All metrics are on the **test half**. The refusal threshold was set on the calibration half (checkpoint d change 4).

| Test half | n | recall@5 | recall@10 | MRR | citation validity | claim support | refusal accuracy |
|---|---|---|---|---|---|---|---|
| All | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| Reviewed questions only | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| By kind: rating / lesson / tender design (L2) / contract / refusal | _[run]_ | | | | | | |
| India / China / Mali / Albania / Sri Lanka | _[run]_ | | | | | | |

Retrieval path (hybrid or keyword fallback) _[run]_ · gate threshold and calibration-half pass rate _[run]_ ·
claims removed: mechanical _[run]_ / verifier unsupported _[run]_ / no verdict _[run]_ · judge second opinion _[run]_ ·
rating-answer accuracy _[run]_ · synthetic-question word overlap (median; count above 0.5) _[run]_ · prompt injection
(quarantined? planted claim survived?) _[run]_ · notice chunks indexed (notebook 21) _[run]_ · hybrid identifier check
(notebook 60) _[run]_ · Re-bid: IFB notices excluded as modifications (notebook 51) _[run]_

## 5. Early warnings and backtest (notebook 70)

Flags by type _[run]_ · backtest crosstab (any flag one year before ICR vs outcome ≤ MU), n ≤ 16, descriptive _[run]_ ·
amendment indicator vs extracted events (Spearman, descriptive) _[run]_
