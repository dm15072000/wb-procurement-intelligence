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
label's recall is therefore low; adding a "same package re-advertised" rule is proposed, not done (it changes the label
from about 51 to several hundred positives, so it needs a precision sample first).

## 3. Extraction (notebook 55)

Events must have **happened**: the prompt excludes conditions, plans and words inside project names, and a mechanical check rejects event quotes that are future or conditional ("will be subject to re-bidding", "sera relancé") or whose only re-bid word is a project name ("Projet de Relance…"); rejections are counted as `not_happened` (unit-tested with both real examples).

**Dry run (2026-10-22) [M]:** 5,226 chunks · 4,310 in reporting document types (excluded: Country Assistance Strategy 383, Procurement Plan 306, CPF 227) · 268 repeat copies skipped · first dry run **1,767 calls**. Bug found in the filter: the French termination trigger "résili" also matched English "resilience" / "resilient" (582 hits; 222 passages passed for that reason alone). Fixed to the French termination words only (*résiliation, résilié, résilier*) with a word start; recount on the same chunks: **1,548 calls** (ISR 447, PAD 414, ICR 370, Project Paper 180, ICR Review 137). Ratings (section 1, regex): 230 ratings for 16 projects, 3 rating conflicts.

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
