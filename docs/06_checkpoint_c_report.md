# 06 — Checkpoint (c) report: ingestion, resolution and graph (Stage 3)

Status: **for approval** before checkpoint (d), model and RAG design. Date: 2026-10-21.

Labels: **[V] verified** (seen in a source or a run output) · **[M] measured** (computed on our data) · **[A]
assumption** · **[P] proposed** · **[PROV] provisional**: a manual judgement by the assistant, pending the user's
spot-check. Numbers come from the user's runs of notebooks 11, 30 and 40 (outputs read on 2026-10-21) unless a section
says otherwise.

---

## 1. Summary

| Area | Result |
|---|---|
| Ingestion | All sources in bronze (raw JSON in Delta) and silver (typed, de-duplicated). Runs from bronze with no API calls. [M] |
| Contracts | 491,020 contracts from 3 datasets; **0** contract numbers overlap across datasets. [M] |
| Coverage | Coverage status on every contract, and **measured** coverage per FY: 56% in FY2017, 81% in FY2018–19, 82–94% from FY2020. [M] |
| Tender durations | **625 prior-review** and **2,571 post-review** durations kept; 372 post-review negatives dropped and counted. [M] |
| Countries | Every code audited. **151 / 151** universe projects reach a Country or a Region; 0 unmapped. [M] |
| Suppliers | 322,655 records → **272,271** suppliers; largest cluster 40 records (was 119 before the guards). R3 precision **43–47 / 50** [PROV]. |
| Documents | 299 documents (the 16 slice projects' reports plus country CPFs) → **5,226** section-aware chunks (median 339 tokens). [M, Stage 3 run of notebook 20] |
| Graph | 11 node types and 11 edge types in Delta; **0** orphan endpoints and **0** duplicate node IDs. [M] |
| Contract ↔ passage links | **5** strong + **27** weak links; strong 3–5 / 5 correct, weak 16 / 27 specific [PROV]. Coverage **1.3%** of slice contracts. |
| Decision | **Vector Search is the primary retrieval path**; graph links are supplementary context. |

Unit tests: **83 passing** locally (`pytest tests`), covering keys, transforms, chunking, supplier resolution, country
mapping and link scoring.

---

## 2. Ingestion and silver tables [M]

**De-duplication on read.** Paging through sources that change while they are read returns repeated records, so exact
duplicates are removed and counted: projects 22,729 rows → 22,461 distinct IDs (268 repeats), notices 3, disbursements 8.
The contract datasets had none.

**Contracts (notebook 11):**

| Dataset | FYs | Contracts | Review type |
|---|---|---|---|
| DS00005 | 2020+ | 282,890 | Post 264,983 · Prior 17,727 · unknown 180 |
| DS04691 | 2017–2019 | 61,328 | Post 51,839 · Prior 6,523 · unknown 2,966 |
| DS01004 | 2001–2016 | 146,802 | all unknown (no review-type field) |

- Cross-dataset overlap by contract number: **0**. So no project or signing-date conflicts, and amounts are never
  double-counted.
- Every dataset stays inside its documented fiscal years (checked by an assertion).
- Universe contracts (151 road projects, FY2014–21): DS00005 7,692 · DS04691 2,274 · DS01004 307.

**Coverage by fiscal year (universe projects)**, as the share of published Contract Award notices matched in the data:

| FY | Status | Contracts | Award notices | Coverage |
|---|---|---|---|---|
| 2014 | prior_review_only | 46 | 2 | 0% |
| 2015 | prior_review_only | 123 | 21 | 38% |
| 2016 | prior_review_only | 134 | 23 | 78% |
| 2017 | partial_likely_incomplete | 619 | 70 | 56% |
| 2018 | partial | 909 | 324 | 81% |
| 2019 | partial | 746 | 371 | 81% |
| 2020 | full | 1,094 | 367 | 82% |
| 2021–2026 | full | 588–1,371 per year | 769–1,597 per year | 83–94% |
| 2027 (current FY, in progress) | full | 84 | 200 | 85% |

FY2018–19 keep the label "partial" despite measuring 81% (user decision). For FY≤2016 the coverage figure is only an
indicator, because DS01004 holds prior-review contracts only. A zero in a partial year is never read as "no activity".

**Tender durations** (signing − earliest matching IFB/REOI notice; joined on project ID + normalized reference):

| Review type | Contracts | Matched | Kept | Dropped: negative | Dropped: > 3 years |
|---|---|---|---|---|---|
| Prior | 1,265 | 637 | **625** | 3 | 9 |
| Post | 8,654 | 2,944 | **2,571** | 372 | 1 |
| unknown | 47 | 6 | 6 | 0 | 0 |

The duration model is mainly valid for prior-review contracts. Post-review has ~13% negative durations and stays a
secondary result (decision for checkpoint d).

**Governance data:** WGI is missing for 1–2% of country-years; CPIA for **56%**, as expected, because CPIA covers
mainly IDA-eligible countries. Missing stays NULL.

**Disbursements:** 17,823 rows for 120 projects, 0 unparsed dates. 31 universe projects have no disbursement series:
25 Additional Financings and 6 others (Stage 3 finding). AF → parent mapping is deferred to checkpoint (d); until then
their disbursement features stay missing.

**Documents (notebook 20, Stage 3 run):** 299 documents (slice-project reports plus the 5 countries' CPFs, which carry no project ID and are never linked to contracts) → 5,226 chunks, median 339 tokens.
Chunks are section-aware: small pieces merge, tables over 800 tokens split with the header repeated, a 10,000-character
cap fits the embedding limit, and exact/near repeats are flagged with the latest copy primary. Procurement Plans give
**543** clean activity labels. A row-by-row comparison with the gate confirmed the parse is stable (765 rows, 0 verdicts
changed; docs/04 §6). Plan dates are not used in models.

---

## 3. Country mapping [M]

Full audit and reasons: docs/04 §8; code: `src/entity_resolution/countries.py`. Original codes are always kept next
to the mapped ISO3 and a mapping status.

- **Universe (151 projects):** 137 direct, **5 legacy codes now mapped** (TP → TLS ×2, ZR → COD, YF → SRB, RY → YEM),
  9 regional (`IN_REGION`), 0 unmapped. Before the fix the 5 legacy-code projects had no country, so no governance
  features.
- **All projects:** 17 YF projects stay ambiguous (before Montenegro's independence, and the name names neither
  country); 90 YU projects (former Yugoslavia) are unmapped. Both are reported, not guessed.
- **Contracts:** 0 unmapped borrower rows. 19 unmapped supplier rows (Stateless 10, Netherlands Antilles 8, CIS 1).
  DS01004 (names only) is mapped by name.

---

## 4. Supplier resolution

Rules: R1 same supplier ID → R2 same normalized name + country → R3 near-identical name + country (firms only). Guards:
shared-ID, common-name, number, JV, chain, and (this pass) **distinctive word**. The country key is the mapped ISO3, so
DS01004 names and STEP codes for the same country match.

| Measure [M] | Before guards | Run 2 | **Final run** |
|---|---|---|---|
| Suppliers (from 322,655 records) | 270,474 | 271,828 | **272,271** |
| R1 / R2 / R3 merges | 14,213 / 33,949 / 4,019 | 14,097 / 33,156 / 3,574 | **14,097 / 33,156 / 3,131** |
| Largest cluster | 119 | 40 | **40** (SMEC International) |

Merges blocked in the final run: shared-ID 116, common-name 1,014, numbers 686, JV 18, **distinctive word 579**, chain 110.
All awards got a supplier key. Placeholder-named award rows with no supplier ID (25,158 in run 2) are
`supplier_name_masked`; their supplier-history features are NULL, never 0.

**R3 precision [PROV]: 43–47 of 50 (~86–94%).** This is a random sample of run-2 merges, judged by the assistant.
2 were wrong (SHAANXI HUICAN / HUAXIANG; IQ / SI CONSULTING), 1 probably wrong (BP / B.T. NIRMAN SEWA), and 4 uncertain
(two person-like names on firm-method rows, MAMAN ISSA / ISSAKA, GROUPE ZENIT / ZENITH). The distinctive-word guard
added afterwards blocks all 3 wrong ones and keeps the 43 correct ones. The sample was **not redrawn** after the guard,
so the final run's precision is not separately measured. **Pending the user's spot-check.**

The 5 largest clusters (SMEC, Ernst & Young, WAPCOS, Studi International, Deloitte India) were inspected rule by rule
and are genuine.

---

## 5. Graph [M]

| Nodes | | Edges | |
|---|---|---|---|
| Notice | 14,032 | FOR_PROJECT | 24,599 |
| Contract | 10,273 | ANNOUNCED | 13,925 |
| Tender | 9,771 | AWARDED_TO | 11,149 |
| Supplier | 7,763 | RESULTED_IN | 7,898 |
| Chunk | 5,226 | HAS_CHUNK | 5,226 |
| Section | 3,962 | HAS_SECTION | 3,962 |
| Document | 299 | HAS_SECTOR | 396 |
| Country | 217 | IN_COUNTRY | 142 |
| Project | 151 | IN_REGION | 9 |
| Sector | 35 | MAY_DESCRIBE | 27 |
| Region | 5 | DESCRIBES | 5 |

Integrity: 0 orphan edge endpoints, 0 duplicate node IDs, 0 universe projects without a country or region. Every edge
has `available_from` (leakage control), a `rule` and its `evidence`.

---

## 6. Contract ↔ document-passage links

**One fix pass, then accepted (stop rule, 2026-10-21).**

| Run | Strong (DESCRIBES) | Weak (MAY_DESCRIBE) | Contracts linked | What went wrong / changed |
|---|---|---|---|---|
| 1 | — | 11,220 links (one type) | 1,067 | 99.9% rested on common words only |
| 2 | 11 | 137 | 72 | ~7 of 11 strong were a person-like supplier name matching Bank staff lists; weak links dominated by French words and acronym tables |
| **Final** | **5** | **27** | **27 of 2,092 (1.3%)** | firm-only supplier names; staff/acronym sections excluded; whole-code reference matches; rarity per project |

**Strong links [PROV]: 3–5 of 5 correct.**

| # | Matched | Verdict |
|---|---|---|
| 1 | ref:GYTJ201B, ISR: "All four rebid contracts, GYTJ201B…" | correct (also a re-bid label for H2) |
| 2 | ref:HAMP-CS-01, ICR annex: workshop minutes | correct |
| 3 | ref:HAMP-GD-02, Project Paper | correct |
| 4 | supplier:HOHAI UNIVERSITY, "External resettlement monitoring" | plausible, not verified from the snippet |
| 5 | supplier:GUIZHOU UNIVERSITY, "Maintenance Pilot Design Contract" | uncertain |

**Weak links [PROV]: all 27 reviewed (the whole population, < 30).**

| Verdict | Count | Examples |
|---|---|---|
| Specific to the contract | **16** | Mali road names (Tiendaga–Fakola–Socourani; Nyamina–Tamani–Kiban); named equipment ("Dragon search light", "hydraulic combi tool"); Sichuan works (Zhongling North Road; Lianpowan Daduhe Bridge contract row); the SLRAMS module annex |
| Same topic only | 4 | four underground-cabling contracts linked to one efficiency-analysis passage |
| Wrong | 7 | only district words matched (Uttara Kannada, taluk, Udupi) or generic words (school, children) |

Weak-link precision: **59% strict (16/27), 74% lenient (20/27)**. Run 2 was ~2 of 27 seen. The verdicts are based on
the matched words plus the first ~260 characters of each passage, not the full passage: **pending the user's
spot-check.**

---

## 7. Decision: retrieval design [P, agreed 2026-10-21]

- **Vector Search is the primary retrieval path.** The index is a Delta Sync index over chunk embeddings
  (`databricks-gte-large-en`), filtered by project, as-of document date and document type. Fallback: cosine similarity
  over a Delta embeddings table.
- **Graph links are supplementary context.** Structured edges (FOR_PROJECT, IN_COUNTRY, HAS_SECTOR) narrow the search
  to the right projects. DESCRIBES links add "this passage cites your contract" context where they exist.
- **MAY_DESCRIBE links are excluded from retrieval evaluation.** They are reported only.
- Why: link coverage is 1.3%, so retrieval cannot depend on links. Vector Search also needs no hand-written evidence
  rules per document style.

---

## 8. Known limitations

1. **Link coverage is 1.3%** (27 of 2,092 slice contracts). Most contracts are never named in ICR/ISR text; reports
   discuss packages and components, not individual contracts. This is a property of the documents, not only of the
   rules.
2. **Short references are not matched.** References under 6 characters are ignored to avoid false hits, so the
   Project Paper table row "SM-3 | Civil works contract No.3 for Shimian County…" became only a weak link. Under the
   stop rule this is recorded, not tuned.
3. **Supplier granularity is brand + country, not legal entity.** "Deloitte Touche Tohmatsu India LLP" and "… Pvt
   Ltd", or SMEC "Pty" and "Pvt", are one supplier. This suits supplier-history features; it is not a legal-entity
   register.
4. **Supplier names are matched in Latin script only.** The HAMP-CS-01 minutes name the supplier JV in Chinese and
   the supplier-name check could not see it. Cross-script aliases are future work.
5. **R3 precision after the distinctive-word guard is not separately measured** (the 50-pair sample predates it).
6. **Manual judgements are the assistant's** and provisional until the user's spot-check (§4, §6).
7. Carried forward: DS01004 has no review type (all "unknown"); FY2017 coverage is likely incomplete; CPIA is missing
   for 56% of country-years; 31 universe projects (25 AF) have no disbursement series until the AF → parent mapping
   at checkpoint (d).

---

## 9. For approval

- Accept the checkpoint (c) results above, with provisional verdicts to be updated from the user's spot-check.
- Proceed to **checkpoint (d): model and RAG design**, on the retrieval decision in §7.

Next update to this file: replace the [PROV] figures in §4 and §6 with the spot-checked ones.
