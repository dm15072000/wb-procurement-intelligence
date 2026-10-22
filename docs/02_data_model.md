# 02 — Data Model & Entity Resolution

Stage 3a (before ingestion). Requirements covered: **M1.3** (entity resolution), **M1.4** (ER schema / knowledge
graph), and the temporal side of **H4** (leakage).
Labels: **[V] verified** (a field seen in a Stage 2 API response) · **[P] proposed** · **[A] assumption**.

## 1. Design principles [P]

1. **One canonical key per real-world thing**, made from a source's own stable ID where one exists. Everything else
   maps to it through `silver.entity_map`, which records the rule and score of every match.
2. **Precision over recall in matching.** A wrong merge (two firms treated as one) puts false evidence in front of a
   user; a missed merge only loses some history. So fuzzy matching is conservative, and every merge can be audited.
3. **Every fact carries the date it became public** (`available_from`). Any query "as of date T" can filter on it, which
   is how leakage rules are enforced. For example, an ICR published in 2024 cannot inform a 2019 prediction.
4. **Missing ≠ zero, and partial ≠ complete.** Absent values (e.g. CPIA) are NULL with an explicit flag, never 0. Contract
   coverage varies by fiscal year (`coverage_status`: full / partial / partial_likely_incomplete / prior_review_only),
   is measured against published award notices, and travels with every aggregate (CLAUDE.md data rules).
5. **The graph is two Delta tables** (`gold.nodes`, `gold.edges`) built from the relational silver tables. No graph
   database: our queries are 1–3 hops.

## 2. Canonical entities

| Entity | Canonical key | Built from | Key fields | Notes |
|---|---|---|---|---|
| **Country** | `iso3` (e.g. `IND`) | World Bank country API [V] | iso2, name, lending_type (IDA/IBRD/Blend), income_level, region | Crosswalk iso2 ↔ iso3 ↔ WB code. Contract `borrower_country_code` is a *WB code that can differ from ISO* [V: DS00005 metadata], so it is mapped through the crosswalk, not assumed. Legacy WB codes (ZR, RY, GZ, TP, YF), region codes and DS01004 country *names* are mapped by `src/entity_resolution/countries.py`, which keeps the original code and records a mapping status; codes that cannot be mapped confidently (YU, AN, SU, XB, pre-2006 YF naming neither country) stay NULL and are reported (docs/04 §8). |
| **Project** | `project_id` (`P` + 6 digits) [V] | Projects API [V] | name, country iso3, approval FY, board approval date, closing date, status, sectors + % shares, `roads_pct`, IDA/IBRD amounts, lending instrument, env. category, implementing agency | `roads_pct` = share for sector "Rural and Inter-Urban Roads" (slice rule: ≥ 25%). Multi-country projects keep a project→country edge per country. |
| **Contract** | `contract_key` = `C:{contract number}`: the number alone, so a contract listed in several datasets is one contract | DS00005, DS04691, DS01004 (de-duplicated; precedence DS00005 > DS04691 > DS01004) | project_id, borrower reference no., description, signing date, fiscal year, `coverage_status`, category, method, review type (`"unknown"` when null or absent), **total amount** (sum over suppliers), n_suppliers, `source_dataset`, `also_in` | Joint awards (§2c): STEP datasets list **one row per member holding that member's share** [M: award notices], so the total is the sum; DS01004 has **one row per contract** with the full amount [M]. Category and method are mapped to **one standard set** (§2b); originals kept in `*_raw`. Also `is_joint_award`, `amount_rule`. |
| **ContractAward** | (`contract_key`, `supplier_key`) | DS00005, DS04691, DS01004 | supplier raw name, supplier_id (STEP only), supplier country, `amount_usd` = the supplier's **share**, `amount_row_usd` = value as published | One row per supplier; supplier features use the share. |
| **Supplier** | `supplier_key` (`S` + hash of the resolved cluster) | resolution, §4 | canonical name, country, kind (`firm` / `individual`), source supplier_ids, n_name_variants | DS01004 has **no supplier_id** [V], so name resolution is needed. |
| **Tender** | `tender_key` = `{project_id}:{normalized reference}` | procurement notices + contracts | first notice date, notice types seen, n_IFB_notices | Joins notices to contracts on project ID + normalized reference number (H1 method). |
| **ProcurementNotice** | `notice_id` (`OP…`) [V] | procnotices API [V] | type, notice date, status, bid reference no., description, procurement group, method code, notice text | Notice text is a retrieval document for L2 (past scope, qualification criteria). |
| **Document** | `doc_id` (WDS id) [V] | Documents & Reports API [V] | project_id, doc type, doc date, url, language | Types: PAD, ISR, ICR, ICR Review (IEG), PPAR, CPF, Procurement Plan. |
| **Section / Chunk** | `section_id`, `chunk_id` (doc_id + sequence) | `ai_parse_document` output (stored once) | heading path, chunk type (text / table / mixed), page range, text, `text_hash`, `table_part` ("k/n"), `merged_sections`, `dup_group`, `dup_kind` (exact / near), `is_primary`, `is_duplicate` | Section-aware chunking (M1.5). Quality pass after run 1 (median 53 tokens): small pieces merge within a section or sibling sections (headings kept in text); tables over 800 tokens split into row groups with the header repeated; a hard cap of 10,000 characters keeps every chunk under the embedding model's **8,192-token** input limit (`databricks-gte-large-en`, verified in the Databricks docs). Repeats: exact (any doc type) and near (ISRs: same section, same words once numbers and dates are masked, 3-gram Jaccard ≥ 0.85). The **latest** copy is primary; older copies are kept and searchable by `doc_date`. |
| **PlanActivityLabel** | (`project_id`, plan activity ref) | Procurement Plans (latest per slice project) | status, description, review type, method | **Labels only:** cancellation / re-bid signals from status and description values. **No dates** (decision 2026-10-21). |
| **GovernanceObservation** | (`iso3`, `year`, `indicator`) | Indicators API: CPIA (`IQ.CPA.*`), WGI (`GOV_WGI_*`, source 3) [V] | value, is_missing | CPIA is mostly absent for IBRD countries [M]. |
| **DisbursementSnapshot** | (`loan_or_credit_no`, `end_of_period`) | DS00975 (IBRD) / DS00976 (IDA) [V: metadata] | project_id, disbursed, undisbursed, cancelled, original principal | Monthly snapshots give the actual disbursement trajectory. |
| **Rating / Lesson** | (`doc_id`, item seq) | LLM extraction from ISR / ICR / ICR Review sections | rating name, value, source section; lesson text, section | Built in Stage 5 (design at checkpoint d); placeholders in the graph schema now. |

### 2b. Standard category and method values [M] (`src/quality/standard_values.py`, unit-tested)

The three datasets come from different procurement rule books: DS01004 uses the 2004/2011 Guidelines names, the STEP
datasets the 2016 Framework names. Every contract gets one standard category and method; the original value stays in
`procurement_category_raw` / `procurement_method_raw`, and `values_mapped = false` marks a spelling the map does not know
(notebook 12 fails if a universe contract has one). Every raw value below was **seen** in the transport-sector extract
pulled on 2026-10-22 (DS00005 7,290 rows · DS04691 2,625 · DS01004 6,589); counts are rows in that extract.

**Category**

| Standard | Raw values (rows: DS00005 / DS04691 / DS01004) |
|---|---|
| Works | "Works" (2,815 / 1,482 / –), "Civil Works" (– / – / 2,794) |
| Goods | "Goods" (1,166 / 232 / 975) |
| Consultant Services | "Consultant Services" (3,082 / 864 / 2,803) |
| Non-consulting Services | "Non-consulting Services" (227 / 47 / 17) |

**Method** (the family groups methods with the same level of competition; the market is kept where the old name gave it)

| Standard | Family | Raw values (rows: DS00005 / DS04691 / DS01004) |
|---|---|---|
| Request for Bids | open_competitive | "Request for Bids" (3,000 / 1,445 / 118); "International Competitive Bidding" (– / 22 / 1,541), market international; "National Competitive Bidding" (– / 14 / 1,630), market national |
| Request for Proposals | open_competitive | "Request for Proposals" (35 / 16 / 3) |
| E-Auction | open_competitive | "E-Auctions" (3 / – / –) |
| Limited Bidding | limited_competitive | "Limited International Bidding" (– / – / 25) |
| Request for Quotations | limited_competitive | "Request for Quotations" (928 / 124 / 7); "Shopping" (– / – / 194); "National Shopping" (– / – / 5); "International Shopping" (– / – / 8) |
| QCBS | consultant_competitive | "Quality And Cost-Based Selection" (781 / 303 / 1,377) |
| QBS | consultant_competitive | "Quality Based Selection" (25 / 9 / 59) |
| FBS | consultant_competitive | "Fixed Budget Selection" (9 / 5 / 35) |
| LCS | consultant_competitive | "Least Cost Selection" (81 / 37 / 66) |
| CQS | consultant_competitive | "Consultant Qualification  Selection", with a double space in the source (393 / 46 / 198); "Selection Based On Consultant's Qualification" (– / – / 5) |
| Individual Consultant Selection | individual | "Individual Consultant Selection" (1,386 / 341 / 568) |
| Direct Selection | direct | "Direct Selection" (641 / 151 / 269); "Single Source Selection" (– / 25 / 475) |
| UN Agencies | direct | "UN Agencies (Direct)" (7 / 9 / –) |
| Force Account | other | "Force Account" (– / 2 / –) |
| Community-Driven Development | other | "Community Driven Development" (– / 73 / –) |
| Non-Profit Organizations | other | "Non-Profit Organizations" (1 / 3 / –) |
| Service Delivery Contracts | other | "Service Delivery Contracts" (– / – / 6) |
| Commercial Practices | other | "Commercial Practices" (222 contracts: Goods 153, Non-consulting 41, Consultant 28); "Comercial Practices" (1, misspelt in the source) |
| Public-Private Partnership | other | "Public Private Partnership" (6, Works) |
| Procurement Agents | other | "Procurement Agents" (1) |
| Imports | other | "Imports" (1) |
| Alternative Procurement Arrangement | other | "Alternative Procurement Arrangement" (1) |
| unknown | unknown | empty / null |

The last five rows were found by the full-data run of notebook 11 (2026-10-22, contract counts): 233 of 491,020
contracts used a spelling not in the transport extract. They were added to the map; no other unmapped value exists.

Effect on the 16 demo projects [M]: 2,092 contracts, 0 unmapped. The map is also written to `ops.standard_value_map`.
Side effect found and fixed: the amendment score's "large civil works" factor tested for a category starting with
"civil", so it never fired for STEP contracts (named "Works"); it now accepts both names.

### 2c. Joint awards: member shares, not repeated amounts [M] (`src/quality/joint_awards.py`, unit-tested)

**Question:** when a contract has several suppliers, does each row hold the member's share or the full amount repeated?
The example asked about, contract **1589938**, is in **DS04691** (P132775, China, CQS, signed 13 Jun 2019): two rows
(GANSU HENGXIN ENGINEERING CONSULTING, supplier_id 428869; XI'AN MUNICIPAL DESIGN AND RESEARCH INSTITUTE, 428896), each
**USD 145,047.44**. It is post-review, so no Contract Award notice was published (only the REOI), and it cannot be
checked on its own; it follows the dataset rule below (total USD 290,094.88).

**Test:** every joint contract of the 16 demo projects with a published Contract Award notice is compared with the
notice's "Signed Contract Price". Notice prices are mostly in local currency, so single-supplier contracts of the same
project and currency give the exchange rate the dataset used (USD notices need none). Verdict = whichever is closer to
the price on a log scale: the **sum** of the rows (shares) or **one** row (repeated). A notice that lists the same JV
price once per member is counted once.

| Dataset | Joint contracts | All rows equal | With award notice | Sum of rows = price | One row = price | No rate |
|---|---|---|---|---|---|---|
| DS00005 | 49 | 41 | 30 | **28** | 1 | 1 |
| DS04691 | 23 | 20 | 17 | **16** | 1 | 0 |
| DS01004 | **0** (also 0 of 6,589 transport-sector contracts) | – | – | – | – | – |

- Clear cases: DS00005 1725106 (USD 124,448 × 2 = notice USD 248,896.00) and 1757542 (USD 1,431,705.43 × 2 = notice
  USD 2,863,410.86); DS04691 1552977 (unequal shares 2,342,282.78 + 2 × 2,273,392.11 = notice USD 6,889,067.00).
- The 2 "one row" cases (DS00005 1779719, DS04691 1586167, both XOF, Mali) sit within currency and amendment noise (one
  row = 1.06× and 0.87× the price). They are listed in `ops.joint_award_check`, not corrected.
- DS01004 amounts are in USD (contract 1537291: USD 1,168,104 vs notice INR 78,190,320, about 67 INR/USD, right for
  2015). A joint venture is one row with a combined name ("... IN JV WITH ..."); `is_joint_award` also uses that rule.

**Full data (notebook 11, 2026-10-22) [M]:** multi-supplier contracts summed as shares: DS00005 **8,864** of 282,890
(3.1%), DS04691 **1,838** of 61,328 (3.0%); DS01004 **0** of 146,802 (every contract is one row). All other notebook 11
figures were unchanged from checkpoint (c).

**Rule (`AMOUNT_BASIS` in `src/ingest/transform.py`):** STEP total = sum of member shares; DS01004 total = its single
row. Defensive: if DS01004 ever lists a contract on several rows with the same amount, it is counted once and split
equally (`amount_rule = repeated_full_amount_counted_once`). Award rows stay one per supplier, with the share in
`amount_usd` (supplier features, graph edge weights) and the published value in `amount_row_usd`. The existing totals
were already right (they summed shares); what is new is that the rule is explicit per dataset and tested on every run.

### 2d. Data-quality gate (notebook 12, every run) [P thresholds, M baselines]

| Check | Gate (fails the run) | Baseline measured |
|---|---|---|
| 2 duplicates | unique keys (contracts, projects, notices); 0 orphan or duplicate award rows | 0 contract numbers in two datasets (491,020 contracts, checkpoint c) |
| 3 joint awards | total = sum of shares (± USD 0.01); STEP "one row = price" ≤ 25% of decidable; DS01004 multi-row = 0 | 1/29 and 1/17; 0 |
| 4 standard values | 0 unmapped universe contracts | 0 of 2,092 slice contracts |
| 5 columns | required fields ≥ 98% filled per dataset; FY = FY(signing date); FY inside the documented range; STEP column sets equal | 100%; 0 of 2,175 slice rows; 21 = 21 columns |
| 6 suppliers | 0 awards without a key; 0 masked records merged; GANSU JINLONGYI (DS01004 1536520) → supplier_id 323849 | 0; 0; passes in the unit test |
| 7 durations | start notice is IFB/REOI; 0–1,095 days; match rate ≥ 25% | 36.0% (3,587 / 9,966, checkpoint c) |

The per-project coverage report for the 16 demo projects (`gold.slice_project_coverage`) is written by the same
notebook; it flags, it does not fail.

### Key normalization rules [P] (implemented in `src/entity_resolution/keys.py`, unit-tested)

- **Project ID:** uppercase, must match `^P\d{6}$`; anything else is rejected, not guessed.
- **Reference number** (tender join): uppercase and keep only `A–Z0–9` (`"IN-UPPWD-14106-CW-RFB"` → `"INUPPWD14106CWRFB"`),
  the same rule as the Stage 2 H1 measurement.
- **Contract / supplier amounts:** USD as given; DS01004 `supplier_contract_amount` is assumed to be USD [A, to check].
- **Dates:** parsed from the formats seen in the sources (`19-May-2022`, ISO, `MM/DD/YYYY hh:mm:ss AM` for closing dates).

## 3. Graph: Delta `nodes` and `edges` [P]

```
gold.nodes(node_id STRING,      -- "{type}:{key}", e.g. "project:P147864", "supplier:S1a2b3c4d"
           node_type STRING,    -- Project | Country | Region | Sector | Contract | Supplier | Tender | Notice | Document | Section | Chunk | Rating | Lesson
           label STRING,        -- human-readable name
           props STRING,        -- JSON of type-specific attributes
           available_from DATE, -- when this fact became public (leakage control)
           source STRING)       -- source table / dataset

gold.edges(src_id STRING, dst_id STRING,
           edge_type STRING,    -- see list below
           weight DOUBLE,       -- e.g. sector %, award amount, link score
           rule STRING,         -- how the link was made: "project_id", "ref_match", "temporal_window", ...
           evidence STRING,     -- source row id(s) so every edge is traceable to data
           available_from DATE)
```

| Edge type | From → To | Rule |
|---|---|---|
| `IN_COUNTRY` | Project → Country | project country code mapped to ISO3 (`countries.py`, incl. legacy codes) |
| `HAS_SECTOR` | Project → Sector | sector name; `weight` = % share |
| `FOR_PROJECT` | Contract / Notice / Document → Project | `project_id` |
| `AWARDED_TO` | Contract → Supplier | ContractAward; `weight` = amount |
| `RESULTED_IN` | Tender → Contract | project ID + normalized reference (H1 join) |
| `ANNOUNCED` | Notice → Tender | same |
| `HAS_SECTION` / `HAS_CHUNK` | Document → Section → Chunk | parse structure |
| `DESCRIBES` | Chunk → Contract | **strong** contract-to-lesson link (below): reference-number or supplier mention. Attached to the *chunk* because that is where the evidence (and the citation) sits; its section is one `HAS_CHUNK` hop up. |
| `MAY_DESCRIBE` | Chunk → Contract | **weak** link, weight ≤ 0.45: ≥ 3 shared *rare* words (in ≤ 2% of the same project's chunks) with the contract description. Reported, but **not** used in RAG evaluation |
| `IN_REGION` | Project → Region | regional projects whose World Bank code is a region (`3W`, `7C`, …), not a country |
| `RATES` / `TEACHES` | Rating / Lesson → Project | extraction (notebook 55): regex ratings; LLM lessons with verbatim-quote check |
| `RATING_EVIDENCE` / `LESSON_EVIDENCE` | Rating / Lesson → Chunk | the passage the item was read from (one `HAS_CHUNK` hop to its section) |

### The contract-to-ICR-lesson link (assessment M1, worked example) [P]

A contract row and an ICR section have no shared key apart from the project. The link is made in three steps and
**scored**, never asserted:

1. **Same project** (`project_id`): required.
2. **Temporal proximity:** the section's document is dated *after* the contract signing (it can describe it), and for
   ISRs, the first ISR dated after signing scores highest.
3. **Text evidence:** the passage mentions the contract's reference number or its supplier's canonical name (**strong**
   → `DESCRIBES`), or shares ≥ 3 **rare** words with the contract description (**weak** → `MAY_DESCRIBE`, weight
   capped at 0.45). "Rare" is measured: a word appears in ≤ 2% of the chunks of the same project, weighted by IDF, so
   common words such as "rural", "road", district names and, in French-language projects, everyday French words carry
   no weight. Lessons / key-factor / procurement sections add a small bonus.
   *Why the split:* the first full run made 11,220 links, 99.9% of them on overlap of common words [M].
4. **Guards (after the run 2 review, one fix pass, no further tuning):** supplier names count only if firm-like (2+
   words, not person-like); staff, team and acronym sections never link; reference numbers match on token boundaries
   only. Run 2 [M]: 11 strong links, of which ~3–4 were judged correct (7 were a person-like supplier name matching
   Bank staff lists); 137 weak links, ~2 of 27 reviewed correct (corpus-wide rarity made French words "rare").

**Role in RAG [P]:** retrieval relies on Vector Search over chunks with project / date / doc-type filters. Graph links
are supplementary context (e.g. "this ISR passage cites your contract"), not the main retrieval path; `MAY_DESCRIBE`
links are excluded from retrieval evaluation.

Each link edge stores its `weight`, its `rule` (e.g. `project+temporal+ref_mention`) and its evidence (`chunk_id|matched text`, e.g. `…|ref:GYTJ201B`). This lets the
app show *why* a lesson is linked to a contract, and lets us measure precision per edge type on a reviewed sample.

### Temporal rule (leakage) [P]

`available_from` = notice date for notices, signing date for contracts, document date for documents, sections and
chunks, and indicator year end for governance values. A model or retrieval query "as of T" uses only nodes and edges
with `available_from ≤ T`. For predicting a tender's duration, T = the tender's first notice date.

## 4. Supplier resolution [P] (`src/entity_resolution/suppliers.py`, unit-tested)

**Input:** one record per (raw supplier name, supplier country, DS00005 supplier_id or none, source).
**Output:** `silver.entity_map(raw_name, supplier_country, source_supplier_id, supplier_key, rule, score)` and
`silver.suppliers`.

1. **Normalize the name:** strip accents; uppercase; drop prefixes `M/S`, `MESSRS`, `THE`; drop punctuation; drop legal
   forms (`PVT`, `PRIVATE`, `LTD`, `LIMITED`, `CO`, `COMPANY`, `CORP`, `INC`, `LLC`, `LLP`, `PLC`, `SA`, `SARL`, `SAS`,
   `SRL`, `SPA`, `GMBH`, `AG`, `BV`, `NV`, `JSC`, `OJSC`, `CJSC`, `OOO`, `PJSC`, `PT`, `TBK`, `SDN`, `BHD`, `PTY`);
   collapse spaces. For example, `M/S. AIRES INGEGNERIA S.R.L.` → `AIRES INGEGNERIA`.
2. **Placeholder names are not names.** `INDIVIDUAL CONSULTANT` appears on **2,094** contract rows [M], each with its
   own anonymised supplier_id, so they are many different people. Placeholder or empty names resolve by
   **supplier_id only**, never by name.
3. **Match rules, in order (union-find):**
   - **R1 `supplier_id`:** the same DS00005 `supplier_id` is the same supplier.
   - **R2 `exact_norm`:** the same normalized name + the same supplier country is the same supplier. For individuals,
     only when their supplier_ids don't conflict (two people can share a common name).
   - **R3 `fuzzy`** (firms only): same country, same blocking key (first token of 3+ letters), and score ≥ **0.92**,
     where score = the *lower* of sorted-token and in-order similarity (so "A M ENGINEERING" ≠ "M A ENGINEERING").
     When the two records carry **different** supplier_ids, the bar rises to **0.95**. It is raised rather than
     blocked, because the data shows differing IDs are weak evidence: 598 exact-name pairs have different IDs [M].
4. **Individuals vs firms:** `individual` only if the contract method is *Individual Consultant Selection* (a DS00005
   field) or the name is an individual placeholder. A name-shape heuristic was tried and **rejected**: on real data it
   labelled short French firm names ("ACE INGENIEURS CONSEILS", "AGECET BTP") as people.
5. **Joint ventures:** DS00005 already lists each member as its own row [V], so each member resolves on its own. A name
   that still contains `JV` / `JOINT VENTURE` / `CONSORTIUM` is kept as its own supplier, flagged `is_jv_name`.
6. **Guards added after the first full-data run (checkpoint c)** [M: that run gave 270,474 suppliers; on the user's
   50-pair sample of 4,019 R3 merges, ~4 were clearly wrong (numbered siblings: "Co. #24"/"#25", "Sinohydro Bureau
   1"/"16", "China Railway No.10"/"No.13", "Modulo #2"/"#7"); the largest cluster (119 records) mixed different people
   named "… KUMAR"]:
   - **Shared-ID:** under one supplier_id, a name with similarity < 0.6 to the ID's most common name is not merged.
   - **Common-name:** a *person-like* name (2–3 tokens of 3+ letters, no business word) used by ≥ 5 supplier_ids in
     one country is not merged across IDs. The person-like test gates only this guard: firms registered under several
     IDs ("FRED ENGINEERING", 6 IDs) still merge.
   - **Number:** no R3 merge when the names contain different numbers. Trade-off: it also blocks correct merges like
     "(LOT-1)/(LOT-2) <same firm>", accepted for precision.
   - **JV:** no R3 merge between a JV-named and a non-JV record.
   - **Chain:** an R3 merge must also pass the threshold between the two clusters' canonical (shortest) names, so
     A≈B≈C chains can't drag in a C unlike A.
   - **Distinctive word** (after the 50-pair review of the full run): the first distinctive word (not a business or
     function word) must match, exactly when 3 characters or fewer ("IQ" vs "SI", "BP" vs "BT"); every other distinctive
     word of 4+ characters needs a counterpart ≥ 0.8 similar, or must appear inside the other name. Blocks "SHAANXI
     HUICAN …" vs "SHAANXI HUAXIANG …"; typos and split words still merge.
   - R3 is never applied to individuals, with or without an ID.

### Measured on the full contract data (checkpoint c, run 2: before the distinctive-word guard) [M]

| Measure | Value |
|---|---|
| Distinct supplier records / resolved suppliers | 322,655 / 271,828 |
| Merges: R1 / R2 / R3 | 14,097 / 33,156 / 3,574 |
| Largest cluster | 40 records (SMEC International; was 119 before the guards) |
| Merges blocked by guards | shared-ID 116, common-name 1,014, numbers 686, JV 18, chain 208 |
| **R3 precision, random sample of 50** | **43–47 of 50 correct (~86–94%)**: 2 wrong (#6 HUICAN/HUAXIANG, #48 IQ/SI), 1 probably wrong (#31 BP/BT), 4 uncertain. Provisional: assistant's review, to be spot-checked by the user. |

The 5 largest clusters (SMEC, Ernst & Young, WAPCOS, Studi International, Deloitte India) are all genuine. Design choice:
we resolve to **brand + country**, not legal entity, so "Deloitte … LLP" and "… Pvt Ltd" are one supplier.

### Measured on real data (Stage 2 download: 140 road projects, DS00005) [M]

| Measure | Value |
|---|---|
| Distinct supplier records (name, country, id, method) | 11,371 |
| Resolved suppliers | **10,240** |
| Merges: R1 supplier_id / R2 exact name / R3 fuzzy | 472 / 598 / 61 |
| Placeholder records / individuals / JV-named | 1,973 / 1,924 / 193 |
| Largest cluster | 8 records (spellings of "FRED ENGINEERING SRL") |
| Runtime | 0.2 s |
| **R3 precision (all 61 merges reviewed)** | **59 clearly correct, 2 uncertain, 0 clearly wrong** |

The precision review was done by the assistant, not by an independent labeller: treat it as **provisional, to be
confirmed by the user**. The 2 uncertain merges are "GROUPE ZENIT" / "GROUPE ZENITH" (BF) and two romanizations of a
Cambodian community, "ROLEAK KANG CHOEURNG" / "ROLEAK KONG CHERNG". Bugs this real-data check caught that synthetic
tests had missed, now covered by regression tests: the placeholder mega-cluster (176 "people" merged into one), the
name-shape individual heuristic, and the dead R3 rule (0 merges under the original ID-blocking design).

Code: `src/entity_resolution/suppliers.py`, `src/entity_resolution/keys.py`; tests: `tests/test_entity_resolution.py`
(31 tests; 83 across the repo, all passing on 2026-10-21).

## 5. Missing-data handling [P]

| Case | Stored as | Never |
|---|---|---|
| FY2017–FY2019 contracts (DS04691, partial; FY2017 likely incomplete) | Real counts, plus `coverage_status` and measured `notice_coverage_pct` per project-FY (`gold.contract_activity_fy`) and per FY (`gold.contract_coverage_fy`) | a zero in a partial year read as "no activity" |
| CPIA absent (most IBRD countries) | `value = NULL`, `is_missing = true` | imputed or 0 |
| Review type null (STEP) or absent (DS01004) | `"unknown"`, counted in the reports | assumed "Prior" (although DS01004 is prior-review only by description, the field itself is absent) |
| Supplier identity unknowable: placeholder name AND no supplier_id (`supplier_name_masked = true`) | Never merged; every supplier-history feature NULL | 0 past contracts / "clean record" |
| PAD absent under that doc type (9 of 60 projects [M]) | `pad_doc_id = NULL`; fallback search under "Project Paper" logged | silently using another document |
