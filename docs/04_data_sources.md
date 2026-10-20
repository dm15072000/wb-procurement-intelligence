# 04 — Data-Source Verification (Stage 2, checkpoint b)

Verified on 2026-10-20 with read-only HTTP GET calls from a local machine. Nothing was written to Databricks.
The scripts that produced every number here are in `scripts/stage2_verification/`. Downloaded samples were kept in a
temporary scratch folder, not in the repo.

Labels: **[V] verified** (observed in an API response) · **[M] measured** (computed from verified data) ·
**[A] assumption** · **[P] proposed**.

---

## 1. Source register

| # | Assessment item | Source & access path | Status | Key facts |
|---|---|---|---|---|
| S1 | Major Contract Awards (since FY2020) | Finances One **DS00005**, `https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice?datasetId=DS00005&resourceId=RS00005&type=json` | **[V] works** | 295,058 rows, updated daily, CC BY 4.0, 21 columns. Max 1,000 rows per call (`top`, `skip`). Filtering works with `filter=` + SQL-like text (`project_id='P…'`, `LIKE '%…%'`); **`AND` returned 0 rows**, so filter on one field and narrow locally. |
| S2 | Contract awards FY2001–FY2016 (retired, prior-review only) | Finances One **DS01004**, same API with `resourceId=RS00934` | **[V] works** | 146,802 rows, data as of 2022-03-11. Schema **differs** from DS00005 (see §3). |
| S3 | Projects & Operations portfolio | `https://search.worldbank.org/api/v2/projects` | **[V] works** | 22,729 projects. Has approval FY, status, closing date, sectors with % shares (`sector1..5`), IDA/IBRD commitments, implementing agency, environmental category. **`sectorcode_exact` filter is ignored**, so sector filtering is done locally. |
| S4 | Finances & disbursements | Finances One **DS00976** (IDA credits/grants, historical) and **DS00975** (IBRD loans, historical) | **[V] metadata** | ~1.6M rows each, monthly snapshots (`end_of_period`) with `disbursed_amount`, `undisbursed_amount`, `cancelled_amount`, `project_id`. Gives the **actual** disbursement trajectory. A **planned** disbursement schedule is not in these datasets [V]; it would have to come from PAD tables [A]. |
| S5 | Procurement notices (REOI, IFB, award) | `https://search.worldbank.org/api/v2/procnotices` | **[V] works** | 423,546 notices. Filter `project_id=` works. Fields: `notice_type`, `noticedate`, `bid_reference_no`, `bid_description`, `procurement_group`, `procurement_method_code`, `notice_status`, full `notice_text`. Types seen: Contract Award, Invitation for Bids, REOI, General Procurement Notice, Invitation for Prequalification. |
| S6 | Documents: PAD, ISR, ICR, ICR Review, PPAR, CPF, Procurement Plans | Documents & Reports, `https://search.worldbank.org/api/v2/wds` | **[V] works** | 549,248 documents (v2). `proid=` filter and `fct=docty_exact` facets work. Totals: ICR 8,097 · ICR Review (IEG) 7,303 · PPAR 2,646 · CPF 212 · Procurement Plan 117,318. Each record has `pdfurl`. |
| S7 | IEG evaluations | Through S6 (ICR Review, PPAR doc types) | **[V]** via S6 | `ieg.worldbankgroup.org` returned **HTTP 403** to scripted requests, so we use S6 only. |
| S8 | Open Knowledge Repository | `https://openknowledge.worldbank.org/server/api/discover/search/objects` (DSpace 7) | **[V] works** | Query "rural roads procurement" → 179 items (e.g. *Efficiency in Public Procurement in Rural Road Projects of Nepal*). |
| S9 | Procurement guidance / framework | `worldbank.org/en/projects-operations/products-and-services/brief/procurement-new-framework` | **[V] page reachable** (HTTP 200) | HTML/PDF guidance, no API. Used as a few reference documents [P]. |
| S10 | CPIA | `https://api.worldbank.org/v2/country/{iso3}/indicator/IQ.CPA.*` | **[V] works** | e.g. `IQ.CPA.PUBS.XQ` (public-sector cluster), `IQ.CPA.TRAN.XQ` (transparency/corruption). |
| S11 | WGI | `https://api.worldbank.org/v2/country/{iso3}/indicator/GOV_WGI_*?source=3` | **[V] works** | The old codes (e.g. `RQ.EST`) **no longer exist**; the new codes are `GOV_WGI_RQ.EST`, `GOV_WGI_GE.EST`, `GOV_WGI_RL.EST`, `GOV_WGI_CC.EST` (36 indicators in source 3). Last updated 2026-09-25. |
| S12 | Country lending type (IDA / Blend / IBRD) | `https://api.worldbank.org/v2/country` | **[V] works** | `lendingType`: IDX = IDA, IDB = Blend, IBD = IBRD. This is the **current** classification [V], not the one at project approval [A: may differ, e.g. Sri Lanka]. |
| — | Standard Bidding Documents, bid evaluation reports | — | **Not publicly available as data** [A] | SBD templates exist as PDFs; evaluation reports are not published. Gap stated in the proposal. |

**Access behaviour [V]:** Finances One returned **HTTP 403 to Python's default User-Agent** (curl worked) and
**HTTP 429** on bursts ("try again in 43 seconds"). All ingestion code will send an explicit User-Agent, pause between
calls, and retry with exponential backoff that honours `Retry-After` (CLAUDE.md rule).

**New source found [V], proposed for use [P]:** *Procurement Plans* (117,318 in Documents & Reports) list planned
dates per package. Comparing planned dates with actual notice and signing dates is a direct measure of schedule
slippage, which suits L1 (realistic schedules) and L3 (monitoring).

---

## 2. Demo-slice selection (rule from CLAUDE.md)

**Rule [V: CLAUDE.md]:** top 5 countries by number of *closed* road projects (approval FY2014–FY2021) that have an
*ICR* and *contract records in DS00005*, including at least 2 IDA countries.

**Definition used for "road project" [P]:** the project lists sector *Rural and Inter-Urban Roads* (code `TI`).
Urban Transport is excluded.

### Funnel [M]

| Step | Projects |
|---|---|
| Road projects (`TI`), approved FY2014–FY2021 | 208 |
| … closed | 104 |
| … with an ICR in Documents & Reports | 60 |
| … with ≥1 DS00005 contract | **48**, in 34 countries |
| (any status, with DS00005 contracts: used for model training, D7) | 140 |

### Ranking [M]

| Rank | Country | Lending type (current) | Qualifying projects | Unique contracts | of which prior review | ICR Reviews | ISRs | Roads share of each project |
|---|---|---|---|---|---|---|---|---|
| 1 | India | IBRD | 7 | 691 | 11 | 6 | 94 | 100, 99, 95, 38, 97, 95, 97 % |
| 2 | China | IBRD | 4 | 44 | 19 | 4 | 57 | 32, 98, 75, 99 % |
| 3= | Madagascar | IDA | 2 | 755 | 14 | 0 | 32 | **4, 24 %** |
| 3= | Sri Lanka | IDA | 2 | 155 | 3 | 2 | 30 | 82, **20 %** |
| 3= | Mali | IDA | 2 | 118 | 0 | 0 | 26 | 95, 28 % |
| 3= | Albania | IBRD | 2 | 27 | 4 | 1 | 30 | 94, 86 % |
| 3= | Ukraine | IBRD | 2 | 22 | 1 | 2 | 18 | 63, 96 % |

**The rule as written does not give a unique answer [M]:** five countries tie for places 3–5. Two readings:

- **Option A: rule as written, ties broken by contract count** → **India, China, Madagascar, Sri Lanka, Mali** (3 IDA).
  Weakness: Madagascar's two projects are only 4% and 24% roads, so they are mostly not road projects.
- **Option B: road project = roads ≥ 25% of the project; IDA constraint applied after ranking** → India (7), China (4),
  Mali (2), Albania (2, beats Ukraine on contracts), plus the top IDA country with 1 project: Sri Lanka (82% roads, 89
  contracts) → **India, China, Mali, Albania, Sri Lanka** (2 IDA).
  Weakness: fewer contracts (China 44, Albania 27).

**My recommendation [P]: Option B.** Every demo project is genuinely a road project, the IDA rule is met, and models
are trained on all 140 road projects anyway (D7), so the small slice contract counts mainly affect the demo, not the
model.

---

## 3. Hypothesis results

| ID | Hypothesis | Result [M] | Verdict | Consequence [P] |
|---|---|---|---|---|
| H1 | Notice publication date = tender start; contract signing = end | Join on project ID + normalized reference (`bid_reference_no` ↔ `borrower_contract_reference_number`), start = earliest IFB/REOI. All road projects: 14,072 unique contracts, **26.4% matched**, median 134 days, **11.0% negative**. **Prior review:** 52–61% matched, **0–0.3% negative**, median 208–440 days. **Post review:** 4–30% matched, 10–14% negative. | **Fails overall; holds for prior-review contracts** | Duration model trained on matched prior-review contracts (≈615 [M]) plus post-review matches that pass sanity checks (0 < duration < 3 years). Negative-duration cases are dropped and counted. Probable cause [A]: reference numbers reused across packages. |
| H2 | Amendment / re-tender labels are not in structured data | DS00005 has **no amendment, re-tender or cancellation field** [V]. Notice statuses: Published 21,657, Revised 67, Cancelled 5, Draft 5. Only **9 of 3,643** IFB references have more than one IFB notice. | **Holds** | Labels need LLM extraction from ISR/ICR text (plan option A). Structured re-tender signals are too sparse for a classifier on their own. |
| H3 | Contract data biased to large, prior-reviewed contracts; FY2017–19 gap | DS00005 is **92.8% post review** (273,808 of 295,058) [V], so it is **not** prior-review-biased. DS01004 is prior-review-only by its own description [V]. **Zero rows for FY2017, FY2018 and FY2019 in both datasets** [V]. DS01004 lacks `review_type`, `supplier_id` and country codes, and uses `contract_number` / `supplier_contract_amount` [V]. | **Bias applies to DS01004 only; gap confirmed** | Train on DS00005 only. DS01004 is used for descriptive history only. Projects approved FY2014–FY2016 have **no contract records for FY2017–FY2019**, so their contract histories are incomplete. This is stated in the demo. |
| H4 | Document order PAD → ISRs (~6 months) → ICR → IEG review | Of 60 closed road projects with an ICR, **50 follow the order**. Of the 10 failures, **9 have no document typed "Project Appraisal Document"** and 1 has an ISR dated after its ICR. Median gap between ISRs: **185 days** (n = 702). | **Holds where documents exist (50/51)** | Leakage rules use each document's actual date, not the assumed order. For the 9 projects, look for the PAD under other doc types (e.g. Project Paper) in Stage 3. |
| H5 | CPIA mainly covers IDA countries | 2012–2025 coverage: Madagascar 14/14, Mali 14/14, Sri Lanka 9/14, India 2/14, China, Albania and Ukraine 0/14. **WGI 14/14 for all seven** [M]. | **Holds** | CPIA used with a missing-value flag; WGI is the governance signal that is always present. |

---

## 4. Workspace checks (from `notebooks/00_verify_workspace.py`, run by the user on 2026-10-20)

| Check | Result | Label |
|---|---|---|
| Foundation Model endpoints | Llama 3.3 70B **READY**, GTE-large **READY** | [V] |
| Embedding dimension | **1024** | [V] |
| Vector Search endpoints | **none exist** (precondition for D4 met) | [V] |
| `ai_parse_document` on Free Edition | **AVAILABLE** (function exists; whether it parses well is the D6 benchmark in Stage 3) | [V] |
| Create catalog `procurement` | **Inconclusive.** No metastore grant names the user directly. A grant could still come through a group or ownership, which this check doesn't see. | [A] |
| Databricks Apps | **Enabled.** One app already exists: `wbintelligence2` (earlier project). | [V] |

Follow-ups [P]:
- **Catalog:** Stage 3's first cell runs `CREATE CATALOG IF NOT EXISTS procurement`. If it fails with a permission
  error, the notebook falls back to schemas in `workspace` (D3), and I update CLAUDE.md.
- **Apps:** Free Edition may cap the number of apps [A]. If creating our app is refused in Stage 7, the options are to
  reuse or replace `wbintelligence2` (your decision) or to demo from a notebook.

---

## 5. Checkpoint (b) decisions: answered 2026-10-20 [V]

1. **Countries: Option B.** Lending types [V, World Bank country API, current classification]: India **IBRD**, China
   **IBRD**, Mali **IDA**, Albania **IBRD**, Sri Lanka **IDA**. That is 2 IDA countries, which meets the rule, so **no
   swap is needed**. Caveat [M]: Sri Lanka is IDA today, but its CPIA coverage for 2012–2025 is only 9/14 years (Mali
   14/14). Slice = **16 qualifying projects** (India 7, China 4, Mali 2, Albania 2, Sri Lanka 1); 28 road projects (≥25%)
   in these countries in any status.
2. **Duration target: approved** as in §3 H1. Negative durations are dropped as data errors and counted; prior-review
   and post-review results are reported separately; the model is stated to be mainly valid for prior-review contracts.
3. **Procurement Plans:** approved *if structured*. Format and coverage are reported in §6 below, for your decision.
4. **Parser benchmark (D6):** `notebooks/01_parser_benchmark.py`, ready for you to run. Results will go in
   `docs/05_parser_benchmark.md`.
5. **FY2017–FY2019 gap = missing data, never zero.** Recorded as a data rule in CLAUDE.md.

## 6. Procurement Plans: format and coverage (demo countries) [V/M]

| Item | Finding |
|---|---|
| Where | Documents & Reports API, doc type `Procurement Plan`, filter `proid=` [V] |
| Coverage | **16 / 16** slice projects have plans; **576** plan versions in total (7–122 per project), dated 2000–2024 [M] |
| Format | **Machine-generated, not scanned.** ~95% have report number `STEP…` (exported from STEP); **100% have a plain-text version** (`txturl`) besides the PDF [M] |
| Structure | **Semi-structured, not structured data.** Narrative policy text (methods, prior-review thresholds), then a very wide fixed-width table per category: *Activity Reference No.*, method, market approach, estimated/actual amount, and **Planned / Actual dates for each process step** (bid documents, bid opening, evaluation report, contract signing) [V, one plan inspected]. Column headers are truncated in the text ("Market Approac", "Estimated Am"). |
| Versions | Each version is a revised snapshot of the same plan, so the latest version per project holds the most actual dates [A] |
| Value | Planned vs. actual dates per activity give a **direct schedule-slippage measure** (L1/L3). Actual step dates could also give a **better tender-duration target than H1**, and plans dated 2016–2020 may partly cover the FY2017–FY2019 contract gap [A, to test]. |
| Cost to use | Needs a parser for the fixed-width tables (text, or pdfplumber tables on the PDF). Moderate effort. |

**Your call [P]:** the plans are not scanned, but they are also not structured data, so strictly they fall short of
"structured". My recommendation is to **use them**: parse only the **latest version per slice project** (16
documents), measure the parse success rate, and fall back to "not used" if under ~70% of activities parse cleanly.

**Decision (2026-10-20) [V]:** approved for a test. Parse the latest plan of each of the 16 projects; drop plans if
under ~70% of activities parse cleanly; if they pass, test (a) duration cleanliness vs. the H1 join and (b) gap coverage.
Report the numbers before any model use.

**Text-export check [M] (local, before the real test):** the `.txt` versions are **not usable**. Dates and amounts wrap
across lines (`2016-09-0` … `6`), the rightmost column, where signing dates sit, is cut off (up to 72 truncated dates per
plan), and reference formats vary by country. P132775's "latest plan" is a 48 MB text of a repeated pre-STEP "Sample
Procurement Plan" template. **The real test therefore parses the PDFs with `ai_parse_document`:**
`notebooks/02_procplan_test.py`.

**Run 1 result (2026-10-20) [M]:** 765 activities detected in 13 of 16 plans (plan coverage 81%). **0 parsed cleanly
(0%), so the gate failed**, and tests (a) and (b) were skipped automatically. Failure reasons: no status 390, bad date
285, no method 64, no ref 12, failed only on review type 14. P132775 was skipped (4,028 pages); P160505 and P155522
yielded no activity tables.

**This 0% is not a valid measurement of the plans [M]:** every detected row contains "Prior"/"Post" by construction, yet
14 rows failed *only* because the review-type column came back empty. That proves the parser's column mapping is
misaligned, which is a defect in my untested HTML-table code. The plans are not shown to be unusable yet. A diagnostic
cell (§9 of the notebook, no new parsing) is added to fix the mapping from evidence.

**Debugging pass (one only, user's stop rule) [V: unit tests]:** three checker bugs found and fixed:
1. **Contradiction** ("ok": 14 with 0 clean): the clean decision required a Prior/Post review type, but the failure
   explanation skipped that rule. Now one function both decides and explains.
2. **Column shift:** the HTML reader ignored single-quoted `colspan='2'` / `rowspan='2'`, shifting every column after a
   merged header cell. Caught by a unit test.
3. **Fragile mapping:** review type, method and status are now found by cell *value*; dates are read as `2023-06-13`,
   `13-Jun-2023` or `13/06/2023`, including dates wrapped inside a cell.

Checker in `src/procplan/check.py`, 7 unit tests passing locally; notebook §10 re-runs the gate on the stored parse.
**Stop rule:** if the re-run is still below 70%, Procurement Plans are dropped and the reason recorded here.
**Status: re-run pending.**

**Bonus signal [V]:** one India plan shows an activity described as "Rebidding-…" with status "Canceled". Plans may
carry the re-tender/cancellation labels that structured data lacks (H2).

## 7. Original decision list (for reference)

1. **Demo countries:** Option A (India, China, Madagascar, Sri Lanka, Mali) or **Option B (recommended):** India,
   China, Mali, Albania, Sri Lanka. Then I record the choice and the rule in CLAUDE.md.
2. **Duration-model target:** accept the H1 result. Train on matched contracts with sanity filters, report prior-review
   and post-review results separately.
3. **Procurement Plans:** add them as a source for planned-vs-actual schedule slippage (L1/L3)?
4. ~~Run `notebooks/00_verify_workspace.py`~~: done (§4).
