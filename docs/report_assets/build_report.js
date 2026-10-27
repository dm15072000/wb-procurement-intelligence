// Builds docs/REPORT.docx (the submitted technical report). Numbers come from docs/02-08, docs/12 and the Databricks
// tables they cite. Run: NODE_PATH=<dir with docx> node docs/report_assets/build_report.js
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType,
  ShadingType, BorderStyle, ImageRun, TableOfContents, Footer, PageNumber, LevelFormat, PageBreak, VerticalAlign,
} = require("docx");

const A = path.join(__dirname);
const OUT = path.join(__dirname, "..", "REPORT.docx");
const TEAL = "0F5257", INK = "1B2A2E", MUTED = "5B6B6E", MIST = "EEF4F3", AMBER_BG = "FDF1D8";
const W = 9360; // text width in DXA (US Letter, 1-inch margins)

// ---------- helpers ----------
function runs(text, base = {}) {
  // **bold** and _italic_ segments inside plain text
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), ...base, bold: true }));
    else out.push(new TextRun({ text: t.slice(1, -1), ...base, font: "Consolas", size: (base.size || 21) - 2 }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}
const body = [];
const P = (t, o = {}) => body.push(new Paragraph({ children: runs(t, o.run || {}), spacing: { after: 120, line: 276 },
  alignment: o.align || AlignmentType.LEFT, keepNext: o.keepNext }));
const H1 = (t, br = false) => body.push(new Paragraph({ text: t, heading: HeadingLevel.HEADING_1, pageBreakBefore: br }));
const H2 = (t) => body.push(new Paragraph({ text: t, heading: HeadingLevel.HEADING_2 }));
const BUL = (items) => items.forEach((t) => body.push(new Paragraph({ children: runs(t), numbering: { reference: "bul", level: 0 },
  spacing: { after: 60, line: 264 } })));
const BREAK = () => body.push(new Paragraph({ children: [new PageBreak()] }));
let figN = 0, tabN = 0;
function caption(kind, text) {
  const n = kind === "Figure" ? ++figN : ++tabN;
  body.push(new Paragraph({ children: [new TextRun({ text: `${kind} ${n}. `, bold: true, size: 18, color: MUTED }),
    new TextRun({ text, italics: true, size: 18, color: MUTED })], spacing: { before: 60, after: 200 },
    alignment: kind === "Figure" ? AlignmentType.CENTER : AlignmentType.LEFT, keepNext: kind === "Table" }));
}
function figure(file, widthIn, text, maxHIn = 8.2) {
  const buf = fs.readFileSync(path.join(A, file));
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  let wi = widthIn, hi = (widthIn * h) / w;
  if (hi > maxHIn) { hi = maxHIn; wi = (maxHIn * w) / h; }
  body.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120 },
    children: [new ImageRun({ type: "png", data: buf, transformation: { width: Math.round(wi * 96), height: Math.round(hi * 96) },
      altText: { title: text, description: text, name: file } })] }));
  caption("Figure", text);
}
function screenshot(n, title, what) {
  const border = { style: BorderStyle.DASHED, size: 12, color: TEAL };
  body.push(new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [W], rows: [new TableRow({ height: { value: 3400, rule: "atLeast" },
    children: [new TableCell({ width: { size: W, type: WidthType.DXA }, verticalAlign: VerticalAlign.CENTER,
      shading: { type: ShadingType.CLEAR, color: "auto", fill: MIST }, borders: { top: border, bottom: border, left: border, right: border },
      children: [
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: `SCREENSHOT PLACEHOLDER ${n}`, bold: true, color: TEAL, size: 26 })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: title, color: INK, size: 22 })] }),
        new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: what, italics: true, color: MUTED, size: 18 })] }),
      ] })] })] }));
  caption("Figure", title);
}
function table(headers, rows, widths, text, opts = {}) {
  caption("Table", text);
  const tot = widths.reduce((a, b) => a + b, 0);
  const ws = widths.map((x) => Math.round((x * W) / tot));
  ws[ws.length - 1] += W - ws.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "C9D6D4" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const cell = (t, i, head, hi) => new TableCell({ width: { size: ws[i], type: WidthType.DXA }, borders,
    shading: { type: ShadingType.CLEAR, color: "auto", fill: head ? TEAL : (hi ? "E3EFEC" : "FFFFFF") },
    margins: { top: 60, bottom: 60, left: 90, right: 90 },
    children: [new Paragraph({ children: runs(String(t), head ? { size: opts.size || 19, color: "FFFFFF", bold: true } : { size: opts.size || 19, color: INK }) })] });
  body.push(new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: ws, rows: [
    new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, i, true)) }),
    ...rows.map((r, k) => new TableRow({ cantSplit: true, children: r.map((t, i) => cell(t, i, false, (opts.hi || []).includes(k))) })),
  ] }));
  body.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}
function note(text) {
  const b = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  body.push(new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [W], rows: [new TableRow({ children: [new TableCell({
    width: { size: W, type: WidthType.DXA }, shading: { type: ShadingType.CLEAR, color: "auto", fill: AMBER_BG },
    borders: { top: b, bottom: b, left: b, right: b }, margins: { top: 100, bottom: 100, left: 140, right: 140 },
    children: [new Paragraph({ children: runs(text, { size: 20 }) })] })] })] }));
  body.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}

// ================= TITLE PAGE =================
const title = [
  new Paragraph({ spacing: { before: 2400 }, children: [] }),
  new Paragraph({ children: [new TextRun({ text: "AI-Powered Procurement Intelligence Platform", bold: true, size: 52, color: TEAL, font: "Cambria" })] }),
  new Paragraph({ spacing: { after: 400 }, children: [new TextRun({ text: "Technical Report", size: 40, color: TEAL, font: "Cambria" })] }),
  new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: "Evidence-backed planning, tender design and monitoring for World Bank procurement, built and measured on Databricks with World Bank open data", size: 24, color: INK })] }),
  new Paragraph({ spacing: { before: 1600, after: 80 }, children: [new TextRun({ text: "Dhruv Agrawal", bold: true, size: 26, color: INK })] }),
  new Paragraph({ spacing: { after: 80 }, children: [new TextRun({ text: "27 October 2026", size: 24, color: MUTED })] }),
  new Paragraph({ spacing: { before: 1400 }, children: [new TextRun({ text: "Labels used throughout: ", bold: true, size: 20, color: MUTED }),
    new TextRun({ text: "Measured = run on Databricks on real data, number from the stored results · Implemented = built and unit-tested · Proposed = designed, not built.", size: 20, color: MUTED })] }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 32, color: TEAL, font: "Cambria" })], spacing: { after: 200 } }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
];

// ================= 1. EXECUTIVE SUMMARY =================
H1("1. Executive summary", true);
P("**Problem.** Procurement teams plan schedules, packages and risk mitigations largely by hand, because the evidence they need sits in three separate places: transaction records (what happened), operational documents (how it was executed) and evaluation reports (why it succeeded or failed). The result is optimistic schedules, avoidable re-bids and cancellations, and lessons that are learned once and then lost.");
P("**Approach.** I built an end-to-end platform in one Databricks workspace on World Bank open data: 491,020 contracts from three Finances One datasets, 22,461 projects, procurement notices, disbursements, CPIA and WGI governance indicators, and 299 project documents indexed as 9,564 passages. It follows the procurement officer’s journey: **plan** a package (duration range, re-bid or cancellation rate, market-approach options, a cited brief), **design** the tender (past notice scopes and qualification criteria, lessons), **monitor** execution (early warnings and rating timelines with conflict rules) and **learn** (a weekly incremental refresh with a regression gate). Every number shown to a user comes from SQL or a model, never from the language model, and every generated claim must cite a source and pass mechanical and model-based checks.");
P("**Key results (measured).**");
BUL([
  "Duration model (prior-review contracts): the P20–P80 range is well calibrated (**P80 coverage 0.81**, target 0.7–0.9); the P50 point estimate is **level with a median baseline** (MAE 100.4 vs 100.8 days).",
  "Re-bid or cancellation: **13.6%** of 3,224 road tenders (**4.6%** without one dominant project); the re-advertisement rule that finds most cases is **97%** precise; grouped rates beat a single global rate (Brier 0.032 vs 0.039).",
  "Retrieval on 26 held-out questions: **recall@5 0.57 strict / 0.83 lenient**; answers keep the same evidence when the user adds “tell me in detail” (5 of 5 questions).",
  "Faithfulness: **citation validity 1.00**, **claim support 0.86–0.905** after guardrails and **97.5–100%** of sampled claims supported by an independent judge (ranges over evaluation runs), off-topic questions refused 3 of 3, planted prompt injection quarantined.",
]);
P("**Main limitations.** Strict recall is below the 0.7 target; the duration point estimate does not beat the baseline and governance indicators did not help on 500 training rows; there are no public amendment labels, so amendment risk is a heuristic; the English-only embedding model finds 1 of 3 French-source questions; reports name only 1.3% of contracts; the retrieval gate is a weak filter, so refusals rely on the answer step and the claim verifier.");
P("**What a pilot would show.** A three-month shadow pilot with about ten procurement specialists would measure what offline tests cannot: planning cycle time, adoption, the share of answers flagged as wrong-source, and forecast accuracy in use. Its go/no-go criteria are set in advance (Section 9.3).");

// ================= 2. PROBLEM FRAMING =================
H1("2. Problem framing");
P("The assessment describes three layers of institutional knowledge. The platform treats each as a different kind of data with its own processing path:");
table(["Layer", "What it answers", "Sources used", "How it is processed"], [
  ["Structured", "What happened", "Contract awards (3 datasets), projects, disbursements, notices, CPIA / WGI", "Typed Delta tables, standard values, measured coverage"],
  ["Semi-structured", "How it was executed", "ISRs, procurement notices (IFB / REOI), procurement plans", "Ratings by regex, notice text indexed, plan status labels"],
  ["Unstructured", "Why it succeeded or failed", "PADs, ICRs, IEG ICR Reviews, CPFs", "Parse once, section-aware chunks, hybrid search, LLM extraction with verbatim quotes"],
], [1.3, 1.5, 3.0, 3.0], "The three knowledge layers and how each is used.");
P("**Scope.** The written design covers the full portfolio; the build uses a slice that is small enough to parse and evaluate carefully but large enough to train models:");
BUL([
  "**Sector:** Rural and Inter-Urban Roads, with roads at least 25% of the project, approved FY2014–FY2021. Roads have many comparable works packages and a long document trail.",
  "**Training universe: 151 road projects** (all countries). Models need volume; road contracts are comparable across countries.",
  "**Demo slice: 16 projects in 5 countries**: India 7, China 4, Mali 2, Albania 2, Sri Lanka 1 (two IDA countries: Mali and Sri Lanka). Documents are parsed and indexed only for these, which keeps parsing and LLM cost bounded and lets every answer be read and checked.",
]);

// ================= 3. DATA SOURCING =================
H1("3. Data sourcing and scoping");
P("Each source family named in the assessment was mapped to an official World Bank access point and called before any pipeline was written:");
table(["Assessment source family", "Official access point", "Used for"], [
  ["Major contract awards", "Finances One data catalogue: DS00005, DS04691, DS01004", "Precedents, durations, re-bid labels, suppliers"],
  ["Finances and disbursements", "Finances One: DS00975 (IBRD), DS00976 (IDA)", "Disbursement-lag warnings"],
  ["Projects and operations", "Projects API (search.worldbank.org/api/v2/projects)", "Project facts, sectors, PDO, financiers"],
  ["Procurement notices (REOI, IFB, award)", "Procurement notices API (…/api/v2/procnotices)", "Tender start dates, re-bid labels, notice text, coverage"],
  ["PADs, ISRs, ICRs, IEG reviews, CPFs, plans", "Documents & Reports API (…/api/v2/wds)", "Retrieval corpus, ratings, lessons, events"],
  ["CPIA, WGI, lending type", "Indicators API (api.worldbank.org/v2)", "Governance features, country context"],
], [2.4, 3.0, 2.6], "Source families and their access points.");
P("**Country selection was data-driven.** Rule: the top five countries by number of closed road projects that have both an ICR and contract records, with at least two IDA countries. Funnel (measured): 208 road projects approved FY2014–21 → 104 closed → 60 with an ICR → 48 with contract records. Five countries tied for places 3–5 and some candidates were mostly non-road (Madagascar’s two projects are 4% and 24% roads), so the roads ≥ 25% rule was applied before ranking. Result: India, China, Mali, Albania, Sri Lanka.");
P("**Verification before building.** Every source was sampled and five assumptions were tested with pass/fail rules decided in advance:");
BUL([
  "**Tender duration = signing − first notice:** fails overall (11.0% negative durations) but **holds for prior-review contracts** (0–0.3% negative), so the main duration model is prior-review.",
  "**No structured amendment or re-tender field:** confirmed, so labels come from notices and documents.",
  "**Document order PAD → ISRs → ICR → IEG:** holds for 50 of 51 projects with the documents; leakage rules use each document’s actual date.",
  "**CPIA covers mainly IDA countries:** confirmed (missing for 56% of country-years); WGI is the always-available signal.",
  "**Procurement-plan dates as a duration source:** rejected (Section 10).",
]);

// ================= 4. ARCHITECTURE =================
H1("4. Architecture");
figure("architecture.png", 6.2, "System architecture: four modules in one Databricks workspace (content of docs/10, Diagram 1).", 4.5);
P("**Stack.** Unity Catalog and Delta Lake (bronze → silver → gold, a document schema, an ML schema and an operations schema); Foundation Model APIs (Llama 3.3 70B for generation and extraction, GTE-large for 1,024-dimension embeddings); Vector Search with a Delta Sync index; MLflow with the Unity Catalog model registry; Workflows for the weekly job; a Databricks App (Streamlit) for users; an AI/BI dashboard for KPIs. There is no infrastructure outside the workspace.");
P("**Why three stores rather than one.** Each store does one job well, and all three are governed by Unity Catalog:");
BUL([
  "**Delta tables** hold the facts (contracts, projects, ratings, features) and give lineage, time travel and SQL for exact look-ups.",
  "**The graph is two Delta tables** (`nodes`, `edges`) rather than a graph database: queries are one to three hops, which joins handle, and every edge carries its rule, evidence and the date it became public. A graph database would be justified only if multi-hop analytics, such as supplier networks across countries, became a real workload.",
  "**Vector Search** handles meaning-based retrieval over text. It syncs from the same Delta embeddings table that the in-memory fallback uses, so the app keeps working if the endpoint is unavailable.",
]);

// ================= 5. MODULE 1 =================
H1("5. Module 1: Ingestion and knowledge representation");
H2("5.1 Contract data: three datasets, three periods");
P("Contract awards come from three Finances One datasets, each covering its own period. They are de-duplicated by contract number (precedence DS00005 > DS04691 > DS01004; measured overlap 0) and every contract carries a coverage status:");
table(["Dataset", "Fiscal years", "Contracts", "Review type", "Role"], [
  ["DS01004", "FY2001–2016", "146,802", "not recorded (prior-review contracts only)", "Descriptive history"],
  ["DS04691", "FY2017–2019", "61,328", "post 51,839 · prior 6,523 · unknown 2,966", "Model training"],
  ["DS00005", "FY2020 onward", "282,890", "post 264,983 · prior 17,727 · unknown 180", "Model training"],
], [1.1, 1.2, 1.0, 3.2, 1.5], "Contract datasets (measured, notebook 11). Total 491,020 contracts.");
P("**Coverage is measured, not assumed.** For each fiscal year, the share of published Contract Award notices that match a contract in the data. FY2017 is labelled partial and likely incomplete, FY2018–19 partial, FY2020 onward full. A zero in a partial year is never read as “no activity”.");
figure("coverage_fy.png", 6.0, "Contract coverage by fiscal year for the road universe (share of award notices matched; measured).");
H2("5.2 Other sources and how each is used");
table(["Source", "Volume (measured)", "Use"], [
  ["Projects API", "22,461 distinct projects; PDO for 148 of 151 universe projects", "Project facts, sector shares, development objective, financiers"],
  ["Financing lines", "173 lines for 151 projects, all IBRD (45) or IDA (128)", "Financing on the Monitor screen; no co-financier appears in the public fields"],
  ["Disbursements", "17,823 monthly rows for 120 projects", "Disbursement-lag warning"],
  ["Procurement notices", "IFB, REOI and award notices with full text; 4,338 notice passages indexed", "Tender start, re-bid labels, tender-design retrieval, coverage measurement"],
  ["Documents", "299 documents → 5,226 report passages", "Retrieval, ratings, lessons, events"],
  ["Procurement plans", "543 clean activity labels", "Cancellation / re-bid labels only (dates rejected)"],
  ["CPIA / WGI", "CPIA missing 56% of country-years; WGI missing 1–2%", "Duration-model features with a release-lag rule"],
], [1.6, 3.2, 3.2], "Other sources and their roles.");
H2("5.3 Data mapping and standardisation");
BUL([
  "**Categories and methods:** the 2004/2011 Guidelines names (DS01004) and the 2016 Framework names (STEP datasets) are mapped to one standard set, e.g. Civil Works → Works; International / National Competitive Bidding → Request for Bids with the market kept; Shopping → Request for Quotations. Originals are kept; an unknown spelling fails the quality gate. Result: 0 unmapped values among universe contracts.",
  "**Country crosswalk:** every code in all sources was audited. Legacy World Bank codes are mapped with stated reasons (ZR → COD, RY → YEM, GZ → PSE, TP → TLS, YF by a dated rule); regions get an IN_REGION edge; ambiguous codes stay unmapped and are reported. All 151 universe projects reach a country or region.",
  "**Ratings scale:** Highly Satisfactory 6 … Highly Unsatisfactory 1; the ISR “progress towards the PDO” and the ICR / IEG “outcome” are one dimension. Risk ratings and the ICR / IEG efficacy scale (High, Substantial, Modest, Negligible) are kept apart.",
  "**Joint-venture amounts:** tested against published award prices. STEP datasets list one row per member holding that member’s share (sum matched the price for 28 of 30 DS00005 and 16 of 17 DS04691 joint contracts with a notice); DS01004 has one row per contract. Totals are sums of shares; award rows keep the share.",
  "**Data-quality gate (notebook 12):** 14 checks on keys, duplicates, joint awards, standard values, required fields, fiscal years, supplier keys and durations; **14 of 14 pass**. A failure stops the models.",
]);
H2("5.4 Entity resolution");
P("Supplier records (322,655) are resolved in three rules: R1 same supplier ID (14,097 merges), R2 same cleaned name and country (33,156), R3 near-identical name and country, firms only (3,131). Six guards block risky merges: shared ID 116, common name 1,014, numbers in the name 686, joint venture 18, distinctive word 579, chain length 110. Result: **272,271 suppliers**; the largest cluster fell from 119 to 40 records after the guards. R3 precision on a random sample of 50 was **43–47 correct** (assistant-judged; a hand check is pending). Precision is preferred over recall: a wrong merge shows a user someone else’s track record. Placeholder names such as “INDIVIDUAL CONSULTANT” without an ID are never merged and get missing history, never zero.");
H2("5.5 Knowledge graph");
P("The graph has 11 node types and 11 edge types in two Delta tables, with 0 orphan edges and 0 duplicate node IDs. Every node and edge has `available_from`, the date the fact became public (notice date, signing date, document date), so any query “as of date T” sees only what was known then. The assessment’s example, linking a contract row to an ICR lesson section, is scored in three steps: same **project ID** (required), **temporal proximity** (the document must post-date the signing) and **text evidence** (the contract reference or supplier named: strong `DESCRIBES` link; three or more rare shared words: weak `MAY_DESCRIBE`). Sector codes define the universe and form `HAS_SECTOR` edges but are not in the link score, because in a one-sector slice they cannot separate candidates; a sector-match term is proposed for the full portfolio.");
P("**Honest finding:** only **1.3%** of demo contracts are named in any report passage (5 strong and 27 weak links), because reports discuss packages and components, not individual contracts. Vector Search is therefore the primary retrieval path; the links add “this passage names your contract” where they exist.");
H2("5.6 Document parsing benchmark");
table(["Criterion (3 ICRs, 139 pages)", "ai_parse_document", "pdfplumber + pypdf"], [
  ["Tables well-formed / detected", "**117 / 124 (94%)**", "28 / 108 (26%)"],
  ["Recall of the 5 standard ICR sections", "0.93", "0.93 (inflated by table-of-contents lines)"],
  ["Speed", "0.65 s per page", "0.19 s per page"],
], [3.0, 2.5, 2.5], "Parser benchmark (measured).");
P("ICR tables carry the ratings, costs and dates that citations rely on, so table fidelity decided it. The parser’s output is not byte-identical between runs, so each document is **parsed once** and stored, and citations always point to the same text.");
H2("5.7 Section-aware chunking");
P("Chunks follow the document’s structure and keep their heading path (e.g. “IV. Bank performance > Procurement”); page headers and footers are dropped. The first run produced a median chunk of **53 tokens** (single lines such as “Rating: Moderately Satisfactory”), so small pieces now merge within a section, giving a **median of 339 tokens**. Tables over 800 tokens split into row groups with the header repeated; a 10,000-character cap keeps every chunk under the embedding model’s 8,192-token limit. ISRs repeat the same blocks every six months: exact and near repeats are flagged and the latest copy is primary.");
H2("5.8 French passages: translation for indexing");
P("The embedding model is English-only. Of 9,564 passages, 8,836 are English, **196 French** (141 notices, 48 procurement-plan passages, 7 in one ICR annex) and 532 other (tables, name lists). The 196 French passages were translated to English **for embedding only**: the original text is what is shown, cited and quote-checked. Translations kept on average 97.6% of the original numbers (12 below 90%). Section 8 reports the effect: French-source recall rose from 0 to 1 of 3 questions only after keyword matching and a French query were added, so a multilingual embedding model is the production fix.");

// ================= 6. MODULE 2 =================
H1("6. Module 2: Predictive and prescriptive engines");
H2("6.1 Tender duration");
P("**Target:** contract signing minus the earliest matching IFB / REOI notice, kept between 0 and 3 years. **Population:** 625 prior-review tenders (main model) and 2,571 post-review tenders (secondary). **Features** are only those known at the start notice: category, method, notice type, contract value (a proxy for the unpublished cost estimate), country and lending type, project age, roads share, commitment, earlier tenders in the project, fiscal month, and WGI / CPIA from the latest year **published** before the notice (a release-lag rule). **Model:** quantile gradient-boosted trees for P20, P50 and P80 with fixed hyper-parameters, against a median-by-category-and-method baseline, on a **time split** (latest 20% as test).");
table(["Test set (time split)", "n test", "P50 MAE (days)", "Median error", "P80 coverage"], [
  ["Prior review: median baseline", "125", "100.8", "70.5", "0.800"],
  ["Prior review: quantile GBT (champion)", "125", "100.4", "80.5", "0.808"],
  ["Post review: median baseline", "519", "70.3", "57.5", "0.827"],
  ["Post review: quantile GBT", "519", "69.5", "52.3", "0.825"],
  ["Prior review without governance features", "125", "94.7", "–", "0.800"],
], [3.2, 0.9, 1.3, 1.2, 1.2], "Duration model results (measured, notebook 50). Test sets start 2023-01-17 (prior) and 2024-01-16 (post).", { hi: [1, 3] });
P("**Interpretation.** The point estimate is level with the baseline; the value delivered is the calibrated range. Removing governance features improves the error, so the governance hypothesis is not supported on 500 training rows. Procurement method dominates the feature importances. **Survival analysis is not used yet:** censoring needs reliably open tenders, but an unmatched notice is more often a join failure (41–48% of prior-review contracts do not match a notice) than an open tender. It becomes viable with STEP’s internal tender status.");
H2("6.2 Re-bid or cancellation risk");
P("**Labels from the public record, each with its reason:** a second invitation at least 30 days later (corrigenda, addenda, deadline extensions and “Revised” notices excluded), a cancelled notice, re-bid wording, a cancelled plan activity, and a package **re-advertised under a new reference** (same project, same description with the same numbers in order, earlier round without a contract, at least 30 days later).");
P("**The false-positive fix:** the first labels gave a classifier AUC of 0.93, but 120 of 171 positives were false: a French project name containing “Relance” (recovery) and a template sentence saying lots “will be subject to re-bidding”. Wording inside project names or after future or conditional phrases no longer counts. The re-advertisement rule then became the main label source: **391 tenders flagged, 97% precise** (29 of 30 sampled; 44 of 44 outside the dominant project).");
P("**Result:** 439 of 3,224 tenders (13.6%) were re-bid or cancelled; one project (P157054, Madhya Pradesh) holds 79% of them, and without it the rate is 4.6%. With rare and clustered positives, the method is **hierarchical Beta-binomial rates** (global → category → method → country), each shrunk toward its parent and reported with a 90% interval. On the latest period they beat one global rate (Brier 0.032 vs 0.039). A classifier reached **AUC 0.46** and was rejected: promotion needs a Brier improvement **and** AUC ≥ 0.6. Rates for recent tenders are a lower bound, because a re-advertisement appears a median 134 days later.");
H2("6.3 Amendment risk");
P("No public dataset records contract amendments, so a probability cannot be validated. The platform extracts amendment, time-extension and overrun **events** from ISRs and ICRs with verbatim quotes (223 amendment and 170 time-extension events) and shows a transparent **indicator checklist** (large works, high value, long predicted duration, weak governance, weak ISR rating, earlier events). Measured against the extracted events, the score does not track them (**Spearman r ≈ −0.08**), so it is labelled a heuristic, never a probability. Production path: STEP amendment records.");
H2("6.4 Retrieval-augmented generation");
figure("rag_flow.png", 6.3, "The RAG flow: facts, evidence and checked answer (updated from docs/10, Diagram 2).");
P("The flow is a **fixed tool sequence**, not an open-ended agent, so every answer is reproducible and the same code runs in unit tests, the evaluation and the app:");
BUL([
  "**Facts first:** SQL finds similar past contracts and the **market-approach options** of similar packages (method and market approach with median notice-to-signing time); models give the duration range and re-bid or cancellation rate.",
  "**Question understanding:** project IDs and project names resolve to projects; answer-style instructions (“tell me in detail”) are removed from the search query and passed to the answer step; outcome questions are routed to ICR / IEG documents, while questions about change over time keep the ISRs.",
  "**Hybrid search** (vector plus keyword, because embeddings handle codes such as contract references poorly) in project, lessons and notice scopes, with a French query pass for French-speaking countries.",
  "**Rerank** with small bonuses for independent sources (IEG > ICR > ISR), lessons sections and exact identifier matches, at most two passages per document.",
]);
screenshot(1, "App: Plan a package screen", "Insert a screenshot: duration range, re-bid or cancellation rate, market-approach options and the cited brief.");

// ================= 7. MODULE 3 =================
H1("7. Module 3: Guardrails, faithfulness and attribution");
table(["Layer", "Rule", "On failure"], [
  ["Retrieval gate", "best pure vector similarity ≥ calibrated threshold, or an exact identifier match", "refuse"],
  ["Evidence as data", "passages wrapped as data; instructions inside them are ignored", "–"],
  ["Quarantine", "passages with instruction-like text are withheld from the model", "withheld, logged"],
  ["Structured output", "JSON claims, each with citation IDs", "refuse if unparseable"],
  ["Citation checks", "every claim cited; every citation was supplied", "claim removed"],
  ["Numbers and IDs", "every number ≥ 10 and project ID appears in a cited source or its date", "claim removed"],
  ["Claim verifier", "one batched model call reads each claim beside its sources; fails closed", "claim removed, counted"],
  ["Extraction quotes", "every extracted lesson, event or rating quotes its passage word for word", "item dropped"],
  ["Model numbers", "durations and rates come from models and SQL with provenance", "–"],
], [1.7, 4.6, 1.7], "Guardrail layers, cheapest first (implemented and unit-tested).");
P("**Measured.** Citation validity 1.00; the verifier removed 5–9 claims per evaluation run; quotes that do not appear word for word are dropped (83 event quotes in the first extraction run). A planted passage telling the model to ignore its instructions is quarantined in every evaluation run and its claim never appears. The app shows each answer’s sources and how many claims were removed.");
P("**The retrieval gate is a weak first filter.** Vector Search scores a plain query as 1/(1 + squared distance), not cosine; this was found when the first calibrated gate blocked 20 of 23 answerable questions, and the score is now converted to cosine. Even so, off-topic questions score 0.66–0.70 and answerable ones 0.66–0.86, so the distributions overlap. At the calibrated threshold (≈ 0.68) the gate blocks 2 of 3 off-topic and 1 of 23 answerable test questions; the third off-topic question is refused by the answer step. Refusals therefore rest mainly on the answer step and the verifier.");
screenshot(2, "App: Ask the evidence answer with citations", "Insert a screenshot: a cited answer, the sources panel and the “guardrails removed N claims” line.");
H2("7.1 Conflicting evidence: ISR vs ICR vs IEG");
P("Ratings are extracted with deterministic rules, each tied to its passage. The rules follow the document timeline: for the **final outcome**, IEG review > ICR > latest ISR among documents published by the as-of date; for the **state at a date**, the latest ISR on or before it. Conflicts are never hidden: every rating is shown with its source and date, and flags are raised (`isr_icr_disconnect`, `ieg_downgrade`, `isr_volatility`). In the demo projects the flags found 1 IEG downgrade and 2 cases of ISR volatility.");
P("**Example: Yunnan Highway Asset Management Project (P132621).** ISR implementation progress was Moderately Satisfactory (December 2018, June 2020), then Satisfactory (January 2021); the IEG ICR Review rated the final development outcome Highly Satisfactory (May 2023), which the rule selects. The ICR and IEG review also rate the **efficacy** of the objectives as “High”, a different scale. An early app answer mixed the two; the fix gives the model the rating timeline, the rule and a note on the scales as structured facts with source passages.");
screenshot(3, "App: Monitor a project, rating timeline", "Insert a screenshot: the ISR → ICR → IEG rating timeline with any conflict flags, PDO and financing.");

// ================= 8. EVALUATION =================
H1("8. Evaluation");
P("**Question set.** 52 questions, frozen after human review: rating look-ups, lessons, tender design, contract precedents, off-topic refusals and three French-source questions. Synthetic questions had to **paraphrase** their source passage (word overlap measured; regenerated once if above 0.5). The set is split 50/50 **grouped by project**, so near-duplicate questions cannot sit on both sides. The gate threshold is calibrated on one half; **every metric is reported on the other (test) half**.");
P("**Strict vs lenient recall.** Strict recall counts only the single gold passage. The same fact is often stated in the ICR and again in its IEG review, so lenient recall also counts a retrieved passage that an LLM judge finds contains the answer.");
table(["Metric (test half, 26 questions)", "Result", "Note"], [
  ["Recall@5 strict / lenient", "**0.57 / 0.83**", "target 0.7: met leniently, not strictly"],
  ["MRR strict / lenient", "0.42 / 0.71", ""],
  ["Precision@5 strict / lenient", "0.11 / 0.50", "strict maximum is 0.20 (one gold passage per question)"],
  ["French-source questions (3)", "0.33", "was 0 before keyword matching and the French query"],
  ["Citation validity", "1.00", "every run"],
  ["Claim support after guardrails", "**0.86–0.905**", "range over evaluation runs"],
  ["Independent judge (40 kept claims)", "**97.5–100%**", "range over runs; same model family"],
  ["Refusal accuracy", "3 / 3", "2 by the gate, 1 by the answer step"],
  ["Phrasing robustness (5 questions × 2)", "identical top 5: 5 / 5; answers agree: 4 / 5", "the one disagreement differs in which claims survived the checks"],
], [3.1, 2.3, 3.6], "Evaluation results (measured, notebook 61; full tables in Appendix C).", { hi: [0, 5, 6] });
note("**Run-to-run variance.** Same frozen questions and temperature 0, yet the model’s answers and the verifier’s verdicts vary slightly between runs, so answer-quality metrics are reported as ranges over the evaluation runs of 23 October 2026 (one run with a faulty project-name matcher is excluded).");
P("**Interpretation and caveats.** Retrieval finds a passage containing the answer for most questions but often not the one the question was written from; tender design and contract questions are the weakest (0.33 and 0 strict). The set is small (26 test questions, 3 per weak category), partly synthetic and reviewed by its author, and the judge comes from the same model family as the generator, so every figure here is indicative, not definitive. The evaluation also caught its own regressions: a first project-name matcher scoped 20 of 22 matched questions to the wrong project and recall fell to 0.52; the stricter version restored 0.57.");

// ================= 9. MODULE 4 =================
H1("9. Module 4: Continuous learning and rollout");
H2("9.1 Weekly refresh job");
figure("weekly_job.png", 5.2, "Weekly refresh job (jobs/refresh_job.json; notebook numbers in brackets).", 5.6);
BUL([
  "**Incremental at every step:** only new documents are parsed; only passages whose text changed are re-embedded and synced (on 23 October only the 196 translated passages were re-embedded and nothing else); extraction covers new passages only.",
  "**Data-quality gate** before the graph and the models: a failed check stops them.",
  "**Champion / challenger:** a retrained duration model is promoted only if its P50 error is no worse on the latest time slice and P80 coverage stays within 0.70–0.90; the decision is logged in MLflow.",
  "**Regression gate:** the frozen evaluation re-runs and the job fails if recall@5 drops by more than 5 points; the dashboard refreshes even then, so the failure is visible.",
  "**Cost control:** LLM extraction runs as a dry run (counting calls) unless a call count is approved.",
  "**Status:** every step has run on Databricks individually; the full job is defined and paused, and has not yet run end to end.",
]);
H2("9.2 KPI dashboard (dashboard as code)");
P("An AI/BI dashboard is defined in the repository as SQL views plus a JSON definition, deployed and published by a notebook, and refreshed by the weekly job. Four pages: **data health** (refresh dates, row counts, quality-gate history, coverage by fiscal year, missing values), **model quality** (duration error vs baseline, P80 coverage, re-bid or cancellation rates with and without the dominant project), **assistant quality** (strict and lenient recall, citation validity, claim support, removed claims, refusals, trend across runs) and **usage** (requests, latency, feedback; labelled test usage only until a pilot).");
screenshot(4, "KPI dashboard", "Insert a screenshot of one dashboard page, e.g. assistant quality with the recall trend.");
H2("9.3 Phased pilot rollout");
table(["Stage", "Scope", "Go / no-go criteria (proposed)"], [
  ["1. Offline (this work)", "Roads, 5 countries, frozen evaluation set", "recall@5 ≥ 0.7; claim support ≥ 0.9; P50 beats baseline; P80 coverage 0.7–0.9"],
  ["2. Shadow pilot (3 months)", "About 10 procurement specialists in one region; advisory only", "≥ 60% weekly active; ≤ 5% answers flagged wrong-source; no unsupported claim in a 100-answer audit"],
  ["3. Expansion", "All transport, then energy and water; all countries", "Metrics hold per sector; weekly refresh ≤ 4 hours"],
  ["4. Integration", "Links into STEP planning; internal data under access control", "Security and data-governance sign-off"],
], [1.8, 2.9, 3.3], "Rollout stages (the proposal’s Phases 0–3).");
P("**Stage 1 status (measured):** P80 coverage 0.81 meets the target; claim support 0.86–0.905 sits around the 0.9 target; the P50 is level with the baseline; recall@5 meets the target leniently (0.83) but not strictly (0.57). Stage 1 is therefore not a clean pass, and the next work is retrieval quality and a multilingual model before a pilot.");
table(["KPI", "Kind", "Status"], [
  ["Retrieval recall and precision of historical risk factors", "offline", "**Measured:** recall@5 0.57 / 0.83; precision@5 0.11 / 0.50"],
  ["Citation validity, claim support, judge, refusals", "offline", "**Measured:** 1.00; 0.86–0.905; 97.5–100%; 3 / 3"],
  ["Duration error vs baseline; P80 coverage", "offline", "**Measured:** 100.4 vs 100.8 days; 0.81"],
  ["Re-bid rates vs global rate (Brier)", "offline", "**Measured:** 0.032 vs 0.039"],
  ["Planning cycle time", "online", "**Proposed:** before/after timing in pilot teams"],
  ["Adoption (weekly active users), answers accepted without edit", "online", "**Proposed:** ≥ 60% weekly active in Stage 2"],
  ["Forecast accuracy in use", "online", "**Proposed:** predicted P50 vs actual signing for pilot tenders"],
  ["Guardrail removal rate", "monitoring", "**Proposed** as a weekly monitoring KPI (tracked per request)"],
], [3.3, 1.0, 3.7], "KPIs: measured offline results vs proposed pilot targets.");

// ================= 10. LESSONS =================
H1("10. Key engineering decisions and lessons");
BUL([
  "**Procurement-plan dates rejected.** Plans looked like an ideal schedule source, but their “signing” dates agreed with official signing dates within 7 days in only **20%** of matched cases. Plan dates were dropped from all models; plans are used only for status labels.",
  "**A fake 0.93 AUC traced to bad labels.** Reading the positives instead of celebrating showed 120 of 171 were false (a project name and a conditional template sentence). After the fix the classifier scored 0.46 and was not shipped.",
  "**Re-advertised tenders.** Many re-bids are advertised again under a new reference and carry no re-bid word; a rule matching the same package description found 391 of them at 97% precision and became the main label source.",
  "**The supplier placeholder bug.** “INDIVIDUAL CONSULTANT” without an ID had merged 176 different people into one supplier; placeholders are now never merged, and regression tests cover it.",
  "**The gate scale bug.** The gate blocked 20 of 23 answerable questions because Vector Search scores are 1/(1 + squared distance); the formula was verified on a live query and converted to cosine, and the calibration grid now comes from the data.",
  "**Chunking measured, not guessed.** A median chunk of 53 tokens was too small to carry context; merging within sections raised it to 339.",
  "**Coverage traced to its dataset.** A per-year coverage check showed FY2017–19 contracts live in their own dataset (DS04691) with partial coverage; each year now carries its measured coverage status.",
]);

// ================= 11. LIMITATIONS =================
H1("11. Limitations and future work");
table(["Limitation (measured or stated)", "Future work (proposed)"], [
  ["Small evaluation set (26 test questions); judge and generator from the same model family", "Larger reviewed set; a judge from a different model family"],
  ["Duration P50 level with baseline; contract value is a proxy for the estimate", "STEP cost estimates and more rows; survival model once tender status is reliable"],
  ["No public amendment labels: amendment risk is a heuristic", "STEP amendment records → supervised model"],
  ["English-only embeddings: French-source recall 1 of 3", "Multilingual embedding model and multilingual keyword analysis"],
  ["Contract-to-passage links cover 1.3% of contracts", "Sector-match term and package-level linking for the full portfolio"],
  ["SBDs, ToRs, bid evaluations and guidance notes not indexed; Word documents not exercised", "STEP tender documents under access control; guidance notes as a policy retrieval scope; a Word parsing route"],
  ["Weekly job not yet run end to end; Free Edition limits (3 apps, 24-hour app runtime, daily compute quota)", "Scheduled production job on a paid workspace"],
], [4.4, 3.6], "Limitations and the proposed way to address each.");

// ================= 12. IMPLEMENTED VS PROPOSED =================
H1("12. Implemented vs proposed");
table(["Assessment requirement", "Status", "Evidence"], [
  ["Planning & strategy (schedules, market approach, mitigations)", "Done", "Plan screen; duration, rates, market-approach options, cited brief"],
  ["Tender design & packaging", "Partial", "4,338 notice passages, lessons; SBDs / ToRs not public"],
  ["Execution & monitoring", "Partial", "91 overdue-tender, 16 disbursement-lag flags; backtest only 3 projects"],
  ["Continuous learning", "Partial", "Weekly job defined; steps run individually"],
  ["Contract awards, projects, notices, ISRs, PADs, ICRs, CPIA / WGI", "Done", "Sections 5.1–5.2"],
  ["Finances incl. co-financing", "Partial", "Disbursements; financing lines all IBRD / IDA in public data"],
  ["IEG (PPARs, thematic studies), guidance notes, CPFs", "Partial", "12 ICR Reviews, 5 CPF-type documents; 0 PPARs for the slice; guidance notes proposed"],
  ["SBDs / prior-review archives", "Missing", "Not public as project data"],
  ["ETL for CSV / JSON and PDF / Word", "Partial", "JSON and PDF done; Word route proposed"],
  ["Entity resolution and knowledge graph", "Done (sector codes partial)", "Sections 5.4–5.5"],
  ["Parsing, chunking, embedding of long reports", "Done", "Sections 5.6–5.8"],
  ["Tender completion time", "Done", "Section 6.1"],
  ["Amendment probability", "Partial (heuristic)", "Section 6.3"],
  ["Re-bidding risk", "Done", "Section 6.2"],
  ["Agentic RAG", "Done", "Sections 6.4, 8"],
  ["Validation layers against hallucination", "Done", "Section 7"],
  ["Conflicting evidence (ICR vs ISR)", "Done", "Section 7.1"],
  ["Closed loop for models and vector indices", "Partial", "Section 9.1"],
  ["Phased pilot with KPIs", "Done (online KPIs proposed)", "Section 9.3"],
], [3.6, 1.6, 2.8], "Requirement status (from the line-by-line audit).");

// ================= 13. RUBRIC =================
H1("13. Rubric alignment");
table(["Dimension (points)", "Strongest evidence", "Main weakness"], [
  ["System & knowledge architecture (30)", "One governed workspace; graph with evidence and dates; parse once; quality gate 14 / 14", "Link coverage 1.3%; Word path not exercised"],
  ["Analytical & AI methodology (30)", "Baselines, time splits, calibrated ranges, measured guardrails, strict vs lenient recall, robustness test", "Duration ≈ baseline; strict recall 0.57; weak gate; French 1 of 3"],
  ["Domain alignment & data utilization (25)", "Three contract datasets reconciled with measured coverage; re-advertisement rule; PDO and financing", "SBDs and bid evaluations not public; guidance notes not indexed"],
  ["Operationalization & scalability (15)", "Weekly incremental job, champion/challenger, regression gate, KPI dashboard, staged rollout", "Job not yet run end to end; Free Edition limits"],
], [2.4, 3.4, 2.6], "Rubric dimensions.");

// ================= APPENDICES =================
H1("Appendix A. Data source inventory", true);
table(["Source", "Access", "Volume (measured)", "Notes"], [
  ["DS00005 contract awards", "Finances One", "282,890 contracts (FY2020+)", "STEP; one row per joint-venture member share"],
  ["DS04691 contract awards", "Finances One", "61,328 contracts (FY2017–19)", "Same schema as DS00005; partial coverage"],
  ["DS01004 contract awards", "Finances One", "146,802 contracts (FY2001–16)", "Prior-review only; no review-type field"],
  ["DS00975 / DS00976 disbursements", "Finances One", "17,823 rows for 120 universe projects", "Monthly snapshots; no planned schedule"],
  ["Projects", "Projects API", "22,461 distinct projects", "PDO for 148 of 151 universe projects; financing lines"],
  ["Procurement notices", "procnotices API", "IFB, REOI, award notices; 4,338 indexed passages", "Full notice text"],
  ["PAD, ISR, ICR, ICR Review, Project Paper, CPF, plans", "Documents & Reports API", "299 documents → 5,226 report passages", "199 ISRs, 16 ICRs, 15 PADs, 12 ICR Reviews, 36 Project Papers"],
  ["CPIA, WGI, lending type", "Indicators API", "CPIA missing 56%, WGI 1–2% of country-years", "Release-lag rule for features"],
  ["SBDs, bid evaluation reports", "–", "–", "Not public as data"],
], [2.2, 1.5, 2.4, 2.4], "Data sources used.", { size: 18 });
H1("Appendix B. Data model");
figure("er_model.png", 6.4, "Entity-relationship view of the canonical entities; stored as gold.nodes and gold.edges with available_from dates.", 7.9);
table(["Edge type", "Count (measured)", "Edge type", "Count (measured)"], [
  ["FOR_PROJECT", "24,599", "LESSON_EVIDENCE / TEACHES", "1,198 each"],
  ["ANNOUNCED", "13,925", "HAS_SECTOR", "396"],
  ["AWARDED_TO", "11,149", "RATING_EVIDENCE / RATES", "230 each"],
  ["RESULTED_IN", "7,898", "IN_COUNTRY", "142"],
  ["HAS_CHUNK", "5,226", "MAY_DESCRIBE / DESCRIBES", "27 / 5"],
  ["HAS_SECTION", "3,962", "IN_REGION", "9"],
], [2.2, 1.5, 2.8, 1.5], "Graph edges by type (gold.edges).");
H1("Appendix C. Full evaluation tables");
table(["Question kind (test half)", "Recall@5 strict", "Recall@5 lenient"], [
  ["Lesson (16)", "0.69", "0.88"], ["Rating (1)", "1.00", "1.00"], ["Tender design (3)", "0.33", "0.67"], ["Contract (3)", "0", "0.67"], ["Refusal (3)", "–", "–"],
], [3.6, 2.2, 2.2], "Recall by question kind (final run, 23 October 2026).");
table(["Test half", "Before translation", "After translation", "Final"], [
  ["Recall@5 strict / lenient", "0.52 / 0.78", "0.52 / 0.78", "0.57 / 0.83"],
  ["French-source recall@5 (3)", "0", "0", "0.33"],
  ["Claim support", "–", "0.905", "0.86–0.893"],
  ["Refusal accuracy", "3 / 3", "3 / 3", "3 / 3"],
], [2.8, 1.8, 1.8, 1.6], "Effect of the French-retrieval steps.");
table(["French-source question", "Gold rank before (French vector)", "After (English translation)"], [
  ["Sikasso phase II lots (notice)", "150", "25 (rank 3 in the final hybrid run)"],
  ["Six-lot works value (plan)", "3", "8"],
  ["Mali ICR recommendation", "> 200", "> 200"],
], [3.2, 2.4, 2.4], "Pure vector rank of the gold passage among 9,564 passages.");
table(["Scope", "Tenders", "Re-bid or cancellation", "Works rate"], [
  ["All road tenders", "3,224", "439 (13.6%)", "15.9%"], ["Without P157054", "2,025", "94 (4.6%)", "5.1%"],
], [2.6, 1.4, 2.4, 1.6], "Re-bid or cancellation rates (notebook 51).");
H1("Appendix D. Repository guide and run order");
P("Repository: https://github.com/dm15072000/wb-procurement-intelligence. The README gives setup and the run order; `notebooks/README.md` lists each notebook with its status.");
table(["Folder", "Contents"], [
  ["notebooks/", "One Databricks notebook per pipeline step (.py source format)"],
  ["src/", "Tested logic: ingestion, quality checks, entity resolution, graph, models, risk, RAG, guardrails, evaluation"],
  ["tests/", "241 unit tests, run locally with pytest"],
  ["app/, app.yaml", "Databricks App: Plan a package, Monitor a project, Ask the evidence, Data coverage"],
  ["dashboards/", "KPI dashboard as code: SQL views and the dashboard definition"],
  ["jobs/", "Weekly refresh job definition"],
  ["slides/, docs/", "Presentation, proposal, evaluation results"],
], [1.8, 6.2], "Repository layout.");
table(["Step", "Notebooks", "Purpose"], [
  ["Ingest and clean", "10 → 11 → 13 → 30 → 12", "APIs with retries and backoff; silver tables; PDO and financing; suppliers; quality gate"],
  ["Documents", "20 → 21 → 22 → 40", "Parse once, notice passages, French translation for embedding, graph"],
  ["Models", "50 → 51", "Duration model; re-bid or cancellation rates"],
  ["Extraction", "55", "Ratings, lessons, events (dry run first; approved call count)"],
  ["Retrieval", "60 → 61", "Embeddings and Vector Search index; evaluation"],
  ["Monitoring and app", "70 → 80 → 85", "Early warnings; app permissions; KPI dashboard"],
], [1.7, 2.2, 4.1], "Run order.");
H1("Appendix E. Glossary");
table(["Term", "Meaning"], [
  ["ISR", "Implementation Status and Results Report: periodic supervision report with ratings (about every six months)"],
  ["ICR", "Implementation Completion and Results Report: the project’s own evaluation at closing"],
  ["PAD", "Project Appraisal Document: design, procurement arrangements and risks at approval"],
  ["IEG", "Independent Evaluation Group; its ICR Review validates the ICR"],
  ["PPAR", "Project Performance Assessment Report: IEG’s in-depth evaluation, years after closing"],
  ["CPIA", "Country Policy and Institutional Assessment: mainly for IDA-eligible countries"],
  ["WGI", "Worldwide Governance Indicators: governance scores for all countries"],
  ["STEP", "Systematic Tracking of Exchanges in Procurement: the Bank’s procurement system behind DS00005 and DS04691"],
  ["IFB / REOI", "Invitation for Bids / Request for Expressions of Interest: notices that start a tender"],
  ["Prior / post review", "Whether the Bank reviews the procurement before or after the contract is awarded"],
  ["P20 / P50 / P80", "Predicted durations that 20%, 50% and 80% of similar tenders stay within"],
  ["Recall@k", "Share of questions whose gold passage is among the top k retrieved passages (strict) or whose top k contain the answer (lenient)"],
  ["Re-bid or cancellation", "A tender re-run (second invitation, re-advertisement, re-bid wording) or cancelled"],
], [1.6, 6.4], "Glossary.");

// ================= DOCUMENT =================
const doc = new Document({
  creator: "Dhruv Agrawal", title: "AI-Powered Procurement Intelligence Platform: Technical Report",
  styles: {
    default: { document: { run: { font: "Calibri", size: 21, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Cambria", size: 32, bold: true, color: TEAL }, paragraph: { spacing: { before: 360, after: 160 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Cambria", size: 25, bold: true, color: TEAL }, paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [{ reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 360, hanging: 260 } } } }] }] },
  sections: [
    { properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } }, titlePage: true },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [
        new TextRun({ text: "AI-Powered Procurement Intelligence Platform · Technical Report   ", size: 16, color: MUTED }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUTED })] })] }),
        first: new Footer({ children: [new Paragraph({ children: [] })] }) },
      children: [...title, ...body] },
  ],
});
Packer.toBuffer(doc).then((b) => { fs.writeFileSync(OUT, b); console.log("wrote", OUT, figN, "figures", tabN, "tables"); });
