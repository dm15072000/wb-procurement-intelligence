// Builds slides/procurement_intelligence.pptx (docs/09_slides_outline.md). Every number is a measured result from
// docs/07_evaluation.md. Run: NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = path.join(__dirname, "procurement_intelligence.pptx");
const IMG = path.join(__dirname, "diagrams");

const THEME = {
  name: "Procurement Intelligence",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1B2A2E", lt1: "FFFFFF", dk2: "0F5257", lt2: "EEF4F3",
    accent1: "0F5257", accent2: "F3B23C", accent3: "5E8C87", accent4: "A23B2C", accent5: "9DBCB8", accent6: "2E6F95",
    hlink: "2E6F95", folHlink: "5E8C87",
  },
};
const TEAL = "0F5257", AMBER = "F3B23C", INK = "1B2A2E", MUTED = "5B6B6E", MIST = "EEF4F3", LINE = "9DBCB8",
  RED = "A23B2C", WHITE = "FFFFFF", SAGE = "5E8C87";
const H = "Cambria", B = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 in
pres.author = "Dhruv Agrawal";
pres.title = "Procurement Intelligence on Databricks";
pres.theme = { headFontFace: H, bodyFontFace: B };

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "DARK", background: { color: TEAL },
  objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.7, y: 2.2, w: 11.9, h: 1.4, fontFace: H,
    fontSize: 44, bold: true, color: WHITE, valign: "bottom", align: "left", margin: 0 }, text: "" } }],
});
pres.defineSlideMaster({
  title: "CONTENT", background: { color: WHITE },
  margin: [0.5, 0.6, 0.6, 0.6],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 8.4, h: 0.9, fontFace: H, fontSize: 30,
      bold: true, color: TEAL, valign: "middle", align: "left", margin: 0 }, text: "" } },
    { text: { text: "Procurement Intelligence · Databricks · World Bank open data", options: { x: 0.6, y: 7.0, w: 8,
      h: 0.3, fontFace: B, fontSize: 10, color: MUTED, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.5, h: 0.3, fontFace: B, fontSize: 10, color: MUTED, align: "right" },
});

// ---------- helpers ----------
const STEPS = ["Plan", "Tender design", "Monitor", "Learn"];
function journey(slide, active) { // the recurring motif: the officer's journey, current step highlighted
  const x0 = 9.25, gap = 1.0;
  slide.addShape(pres.shapes.LINE, { x: x0 + 0.17, y: 0.67, w: gap * 3, h: 0, line: { color: LINE, width: 1.5 },
    objectName: "journey line" });
  STEPS.forEach((s, i) => {
    const on = i === active;
    slide.addShape(pres.shapes.OVAL, { x: x0 + i * gap, y: 0.5, w: 0.34, h: 0.34,
      fill: { color: on ? AMBER : WHITE }, line: { color: on ? AMBER : LINE, width: 1.5 }, objectName: `journey ${s}` });
    slide.addText(String(i + 1), { x: x0 + i * gap, y: 0.5, w: 0.34, h: 0.34, align: "center", valign: "middle",
      fontFace: B, fontSize: 11, bold: true, color: on ? INK : MUTED, margin: 0, isTextBox: true });
    slide.addText(s, { x: x0 + i * gap - 0.33, y: 0.88, w: 1.0, h: 0.28, align: "center", fontFace: B, fontSize: 10,
      bold: on, color: on ? INK : MUTED, margin: 0, isTextBox: true });
  });
}
function stat(slide, x, y, w, big, label, opts = {}) {
  slide.addText(big, { x, y, w, h: 0.75, fontFace: H, fontSize: opts.size || 40, bold: true,
    color: opts.color || TEAL, margin: 0, valign: "bottom", isTextBox: true });
  slide.addText(label, { x, y: y + 0.8, w, h: opts.lh || 0.6, fontFace: B, fontSize: 14, color: INK, margin: 0,
    valign: "top", isTextBox: true });
}
function card(slide, x, y, w, h, fill = MIST) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill },
    line: { color: fill }, objectName: "card" });
}
function bullets(slide, items, x, y, w, h, size = 15) {
  slide.addText(items.map((t, i) => ({ text: t, options: { bullet: { indent: 16 }, breakLine: i < items.length - 1 } })),
    { x, y, w, h, fontFace: B, fontSize: size, color: INK, valign: "top", paraSpaceAfter: 8, margin: 0, isTextBox: true });
}
function para(slide, text, x, y, w, h, o = {}) {
  slide.addText(text, { x, y, w, h, fontFace: B, fontSize: o.size || 15, color: o.color || INK, bold: !!o.bold,
    italic: !!o.italic, valign: o.valign || "top", align: o.align || "left", margin: 0, isTextBox: true });
}
function honest(slide, text, x, y, w, h) { // amber-tinted "stated limitation" box
  card(slide, x, y, w, h, "FDF1D8");
  slide.addText([{ text: "Stated limitation  ", options: { bold: true, color: "8A5A00" } }, { text, options: { color: INK } }],
    { x: x + 0.2, y: y + 0.12, w: w - 0.4, h: h - 0.24, fontFace: B, fontSize: 13, valign: "middle", margin: 0, isTextBox: true });
}
const CHART_TXT = { catAxisLabelColor: MUTED, valAxisLabelColor: MUTED, catAxisLabelFontFace: "+mn-lt",
  valAxisLabelFontFace: "+mn-lt", dataLabelFontFace: "+mn-lt", titleFontFace: "+mn-lt", legendFontFace: "+mn-lt",
  catAxisLabelFontSize: 11, valAxisLabelFontSize: 10, dataLabelFontSize: 11, titleFontSize: 13, titleColor: INK };

// ================= MAIN DECK =================
pres.addSection({ title: "Opening" });

// 1. Title
let s = pres.addSlide({ masterName: "DARK", sectionTitle: "Opening" });
s.addText("Procurement Intelligence", { placeholder: "title" });
para(s, "Evidence-backed planning, tender design and monitoring for World Bank projects, built and measured on Databricks",
  0.7, 3.75, 10.5, 0.9, { size: 20, color: "DCEBE8" });
STEPS.forEach((st, i) => {
  s.addShape(pres.shapes.OVAL, { x: 0.7 + i * 2.2, y: 5.25, w: 0.42, h: 0.42, fill: { color: i === 0 ? AMBER : TEAL },
    line: { color: AMBER, width: 1.5 }, objectName: `step ${st}` });
  para(s, st, 1.25 + i * 2.2, 5.25, 1.6, 0.42, { size: 15, color: WHITE, valign: "middle" });
});
para(s, "Technical assessment · Dhruv Agrawal · October 2026", 0.7, 6.55, 8, 0.4, { size: 13, color: "B9D3CF" });
s.addNotes(`Opening (30 seconds).
"This is a procurement intelligence platform I built end to end on Databricks with World Bank open data. I'll walk it the way a procurement officer would use it: plan a package, design the tender, monitor execution, and let the system learn from every new report.
Everything I show is measured on real data, and I'll be explicit about what works, what is only level with a baseline, and what is not solved yet."
Keep it short: the four circles are the journey that structures the talk.`);

// 2. Problem
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Opening" });
s.addText("Knowledge sits in three silos", { placeholder: "title" });
const silos = [
  ["What happened", "Contract awards, projects, disbursements, notices", "structured"],
  ["How it was executed", "ISRs, procurement plans, REOI / IFB notices", "semi-structured"],
  ["Why it succeeded or failed", "PADs, ICRs, IEG reviews, CPFs", "unstructured"],
];
silos.forEach(([t, d, k], i) => {
  const x = 0.6 + i * 4.1;
  card(s, x, 1.65, 3.8, 2.3);
  para(s, k.toUpperCase(), x + 0.3, 1.85, 3.2, 0.3, { size: 11, color: SAGE, bold: true });
  para(s, t, x + 0.3, 2.2, 3.2, 0.5, { size: 20, bold: true, color: TEAL });
  para(s, d, x + 0.3, 2.85, 3.2, 0.9, { size: 15 });
});
para(s, "The officer's journey: each step needs all three", 0.6, 4.35, 8, 0.4, { size: 16, bold: true });
const jdesc = ["How long, which method, what risk?", "Scope, criteria, lots: what went wrong before?",
  "Which tenders and projects need a look?", "New reports update models and the index"];
STEPS.forEach((st, i) => {
  const x = 0.6 + i * 3.08;
  s.addShape(pres.shapes.CHEVRON, { x, y: 4.95, w: 2.95, h: 0.75, fill: { color: i === 3 ? SAGE : TEAL },
    line: { color: WHITE }, objectName: `chevron ${st}` });
  para(s, `${i + 1}  ${st}`, x + 0.55, 4.95, 2.2, 0.75, { size: 16, bold: true, color: WHITE, valign: "middle" });
  para(s, jdesc[i], x + 0.1, 5.85, 2.75, 0.8, { size: 13, color: MUTED });
});
s.addNotes(`The problem (1.5 min).
The assessment describes three layers: what happened (structured), how it was executed (semi-structured) and why it succeeded or failed (unstructured). Today they live in separate systems: STEP and the contract datasets, ISRs and notices, and PDF reports in Documents & Reports.
The result is plans that repeat past mistakes: unrealistic schedules, re-tenders, cancellations.
I framed the build around the officer's journey: 1 Plan a package, 2 Design the tender, 3 Monitor execution, 4 Learn. Each step pulls from all three silos. The circles at the top right of the next slides show where we are on that journey.`);

// 3. Architecture (native, from docs/10 diagram 1)
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Opening" });
s.addText("One Databricks workspace, four modules", { placeholder: "title" });
const cols = [
  ["Open data", ["Contract awards (3 datasets)", "Projects · notices", "Disbursements", "CPIA · WGI", "PAD · ISR · ICR · IEG"], MIST, TEAL],
  ["M1 Ingest + knowledge", ["bronze → silver → gold", "Quality gate: 14 / 14", "Graph: nodes + edges", "Parse once → chunks", "Vector Search hybrid"], WHITE, TEAL],
  ["M2 Models + RAG", ["Duration P20 / P50 / P80", "Re-bid or cancellation", "Ratings · lessons · events", "Agentic RAG", "Early warnings"], WHITE, TEAL],
  ["M3 Guardrails", ["Citation · number · ID", "Claim verifier", "Injection quarantine", "Conflicts: IEG > ICR > ISR"], WHITE, TEAL],
  ["Users + M4 loop", ["App: Plan · Monitor · Ask", "AI/BI KPI dashboard", "Weekly job", "Feedback → eval set"], TEAL, WHITE],
];
cols.forEach(([t, items, fill, txt], i) => {
  const x = 0.6 + i * 2.5, w = 2.15;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.55, w, h: 3.85, rectRadius: 0.08, fill: { color: fill },
    line: { color: i === 0 || i === 4 ? fill : LINE, width: 1.25 }, objectName: `column ${t}` });
  para(s, t, x + 0.15, 1.7, w - 0.3, 0.6, { size: 15, bold: true, color: txt });
  items.forEach((it, j) => para(s, it, x + 0.15, 2.4 + j * 0.58, w - 0.3, 0.5, { size: 13, color: txt === WHITE ? WHITE : INK }));
  if (i < 4) s.addShape(pres.shapes.CHEVRON, { x: x + w + 0.08, y: 3.28, w: 0.2, h: 0.36,
    fill: { color: AMBER }, line: { color: AMBER }, objectName: "flow arrow" });
});
const strip = [["491,020", "contracts"], ["22,461", "projects"], ["9,564", "indexed passages"], ["16", "demo projects, 5 countries"]];
strip.forEach(([n, l], i) => {
  const x = 0.6 + i * 3.1;
  para(s, n, x, 5.55, 2.9, 0.55, { size: 28, bold: true, color: TEAL, valign: "middle" });
  para(s, l, x, 6.12, 2.9, 0.35, { size: 13, color: MUTED });
});
s.addNotes(`Architecture (2 min). Source: docs/10 diagram 1, redrawn for the slide.
"Everything runs in one Databricks workspace governed by Unity Catalog: Delta tables, Foundation Model APIs (Llama 3.3 70B and GTE-large), Vector Search, MLflow, Workflows, a Databricks App and an AI/BI dashboard. No other infrastructure."
Left to right: public data lands raw in bronze (retries with backoff, honour 429), silver types and standardises it and labels coverage per fiscal year. A data-quality gate with 14 checks stops the models if anything fails (it passes 14 of 14). The graph is two Delta tables, nodes and edges, each edge with its rule, evidence and the date the fact became public. Reports are parsed once with ai_parse_document into section-aware chunks and indexed in Vector Search with hybrid (vector + keyword) search.
M2: duration model, re-bid or cancellation rates, LLM extraction of ratings, lessons and events with verbatim quotes, and the RAG flow. M3: guardrails on every claim and rules for conflicting ratings. M4: the app, the KPI dashboard, a weekly refresh job and feedback.
Scale: 491,020 contracts, 22,461 projects, 299 documents giving 9,564 indexed passages (reports + notices), 16 demo projects in India, China, Mali, Albania and Sri Lanka. Models train on all 151 road projects.`);

// 4. Data before models
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Opening" });
s.addText("Data checked before any model was built", { placeholder: "title" });
const fy = ["2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"];
const cov = [0.557, 0.806, 0.811, 0.82, 0.832, 0.855, 0.872, 0.943, 0.929, 0.941];
s.addChart(pres.charts.BAR, [
  { name: "Partial, likely incomplete", labels: fy, values: cov.map((v, i) => (i === 0 ? v : 0)) },
  { name: "Partial", labels: fy, values: cov.map((v, i) => (i === 1 || i === 2 ? v : 0)) },
  { name: "Full", labels: fy, values: cov.map((v, i) => (i >= 3 ? v : 0)) },
], { x: 0.6, y: 1.5, w: 6.6, h: 4.9, barDir: "col", barGrouping: "stacked", chartColors: [RED, AMBER, TEAL],
  showValue: true, dataLabelPosition: "inEnd", dataLabelFormatCode: "0%;;", dataLabelColor: WHITE,
  valAxisMaxVal: 1, valAxisLabelFormatCode: "0%", valGridLine: { color: "E3E9E8", size: 0.75 },
  catGridLine: { style: "none" }, showLegend: true, legendPos: "b", legendFontSize: 11,
  showTitle: true, title: "Award notices matched in the contract data, by fiscal year", ...CHART_TXT });
const surprises = [
  ["A third contract dataset", "DS04691 covers FY2017–19: the assumed gap was partial coverage, not missing data"],
  ["Plan dates rejected", "Procurement-plan dates agreed with signing dates only 20% of the time"],
  ["Target tested first", "Post-review durations ≈13% negative, so the main model is prior-review"],
  ["Automatic quality gate", "14 checks, all pass; models do not run on a failed refresh"],
];
surprises.forEach(([t, d], i) => {
  const y = 1.55 + i * 1.22;
  card(s, 7.6, y, 5.1, 1.05);
  para(s, t, 7.85, y + 0.12, 4.6, 0.35, { size: 15, bold: true, color: TEAL });
  para(s, d, 7.85, y + 0.48, 4.7, 0.55, { size: 13 });
});
s.addNotes(`Data before models (2 min).
I verified every source before writing a pipeline. Coverage is measured against an independent source: published Contract Award notices. FY2017 56%, FY2018-19 81%, FY2020 onward 82-94%. Every row carries its coverage status, and a zero in a partial year is never read as "no activity".
Four things this check changed:
1. A third contract dataset (DS04691, FY2017-19) existed; I had assumed a three-year gap.
2. Procurement-plan dates agreed with official signing dates only 20% of the time, so they are not used in models (plan labels are).
3. The duration target was tested before modelling: for post-review contracts about 13% of durations were negative, so the main model is prior-review (625 tenders).
4. Notebook 12 is an automatic quality gate: joint awards (STEP lists member shares; contract total = sum), standard category and method values across three datasets, uniqueness, required fields. 14 checks, all pass; a failure stops the refresh.
Also: missing values stay missing (CPIA is missing for 56% of country-years and stays NULL with a flag), review type "unknown" is stored not guessed, masked supplier names are never merged.`);

// 5. Knowledge graph
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Opening" });
s.addText("Every link carries its evidence and its date", { placeholder: "title" });
const nodes = { Notice: [0.8, 1.8], Tender: [2.9, 1.8], Contract: [5.0, 1.8], Supplier: [5.0, 3.45], Project: [2.9, 3.45],
  Document: [0.8, 3.45], Lesson: [0.8, 5.1], Chunk: [2.9, 5.1], Rating: [5.0, 5.1] };
const edges = [["Notice", "Tender"], ["Tender", "Contract"], ["Contract", "Supplier"], ["Contract", "Project"],
  ["Document", "Project"], ["Document", "Chunk"], ["Lesson", "Chunk"], ["Chunk", "Rating"], ["Chunk", "Contract"]];
edges.forEach(([a, b]) => {
  const [x1, y1] = nodes[a], [x2, y2] = nodes[b];
  const ax = x1 + 0.8, ay = y1 + 0.3, bx = x2 + 0.8, by = y2 + 0.3;
  s.addShape(pres.shapes.LINE, { x: Math.min(ax, bx), y: Math.min(ay, by), w: Math.abs(bx - ax) || 0.001,
    h: Math.abs(by - ay) || 0.001, flipH: (bx < ax) !== (by < ay), line: { color: b === "Contract" && a === "Chunk" ? AMBER : LINE,
      width: b === "Contract" && a === "Chunk" ? 2.5 : 1.5, dashType: b === "Contract" && a === "Chunk" ? "dash" : "solid" }, objectName: `edge ${a}-${b}` });
});
Object.entries(nodes).forEach(([n, [x, y]]) => {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 1.6, h: 0.6, rectRadius: 0.1,
    fill: { color: n === "Contract" || n === "Chunk" ? TEAL : WHITE }, line: { color: TEAL, width: 1.25 }, objectName: `node ${n}` });
  para(s, n, x, y, 1.6, 0.6, { size: 14, bold: true, align: "center", valign: "middle",
    color: n === "Contract" || n === "Chunk" ? WHITE : TEAL });
});
para(s, "DESCRIBES: a passage names the contract", 0.8, 6.05, 5.8, 0.35, { size: 12, color: "8A5A00", italic: true });
stat(s, 7.4, 1.55, 5.2, "1.3%", "of demo contracts are named in any report passage (5 strong, 27 weak links)");
para(s, "So Vector Search is the primary path; links add “this passage names your contract” where they exist.",
  7.4, 3.05, 5.2, 0.9, { size: 15 });
card(s, 7.4, 4.15, 5.3, 2.1);
para(s, "Traced on real data", 7.65, 4.3, 4.8, 0.35, { size: 14, bold: true, color: TEAL });
para(s, "Contract GYTJ201B → supplier → project → ISR passage reporting that this contract was re-bid", 7.65, 4.7, 4.85,
  0.9, { size: 14 });
para(s, "11 node types · 11 edge types · 0 orphan edges", 7.65, 5.65, 4.85, 0.4, { size: 13, color: MUTED });
s.addNotes(`Knowledge graph (1.5 min).
The graph is two Delta tables, nodes and edges, not a graph database: queries are 1-3 hops and joins handle them; one governed store. A graph database is a future option only if multi-hop analytics (supplier networks across countries) become a real workload.
Every edge has a rule, the evidence (source row or matched text) and available_from, the date the fact became public. That enforces "as-of" queries: a model trained for a 2019 decision cannot see a 2021 ICR.
The assessment's example (link a contract row to an ICR lesson section) is traced end to end: contract GYTJ201B → supplier → project → the ISR passage that says it was re-bid.
Honest finding: only 1.3% of demo contracts are named in any passage (5 strong, 27 weak links). Reports talk about packages and components, not individual contracts. So retrieval does not depend on these links; Vector Search is primary.
Entity resolution: supplier records 322,655 → 272,271 suppliers with six guards (backup slide).`);

// ---------- Journey step 1: Plan ----------
pres.addSection({ title: "1 Plan" });

// 6. Duration
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "1 Plan" });
s.addText("Plan: how long will this tender take?", { placeholder: "title" });
journey(s, 0);
stat(s, 0.6, 1.5, 3.6, "0.81", "P80 coverage: the range holds for 81% of tenders (target 0.7–0.9)");
stat(s, 4.4, 1.5, 3.6, "≈ baseline", "P50 error 100.4 vs 100.8 days: no sharper point estimate yet", { size: 34 });
stat(s, 8.2, 1.5, 4.5, "94.7", "days MAE without governance features: WGI / CPIA did not help on 500 rows", { color: RED });
const thead = ["Test set (time split)", "n", "P50 MAE (days)", "Median error", "P80 coverage"].map((t) => ({ text: t,
  options: { bold: true, color: WHITE, fill: { color: TEAL } } }));
const trow = (r, hi) => r.map((t, i) => ({ text: t, options: { fill: { color: hi ? "E3EFEC" : WHITE }, bold: hi && i === 0 } }));
s.addTable([thead,
  trow(["Prior review: median baseline", "125", "100.8", "70.5", "0.800"]),
  trow(["Prior review: quantile GBT (champion)", "125", "100.4", "80.5", "0.808"], true),
  trow(["Post review: median baseline", "519", "70.3", "57.5", "0.827"]),
  trow(["Post review: quantile GBT", "519", "69.5", "52.3", "0.825"], true),
], { x: 0.6, y: 3.45, w: 12.1, colW: [4.5, 1.2, 2.2, 2.1, 2.1], fontFace: B, fontSize: 14, color: INK,
  border: { type: "solid", color: "D6E2E0", pt: 0.75 }, rowH: 0.45, valign: "middle", margin: [0, 0.12, 0, 0.12] });
para(s, "Features known at the start notice only · governance from the latest PUBLISHED year · promoted to champion only if no worse on the latest time slice",
  0.6, 6.05, 12.1, 0.6, { size: 13, color: MUTED });
s.addNotes(`Duration model (2 min). Measured, notebook 50, docs/07 §1.
Target: contract signing minus the earliest matching IFB/REOI notice, 0 to 3 years. Main population 625 prior-review tenders; post-review is secondary. Quantile gradient-boosted trees give P20, P50 and P80; the baseline is the median by category and method. Time split: the latest 20% of tenders (from January 2023) are the test set.
Honest reading: on prior-review tenders the model is LEVEL with the baseline (MAE 100.4 vs 100.8 days; median error is worse, 80.5 vs 70.5). What it does well is the range: P80 coverage 0.81, inside the 0.7-0.9 target, pinball 42.8 vs 44.0. Post-review it is modestly better (median error 52.3 vs 57.5).
Governance hypothesis: removing WGI/CPIA IMPROVES MAE to 94.7, so on 500 training rows they add noise. I kept them only because promotion compares against a champion; the ablation is the evidence and I report it.
Leakage rules: features are what is known at the start notice; WGI/CPIA come from the latest year PUBLISHED before the notice (release lag).
What would improve it: STEP's internal cost estimate (we use contract value as a proxy) and more rows.`);

// 7. Re-bid
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "1 Plan" });
s.addText("Plan: will it need re-bidding?", { placeholder: "title" });
journey(s, 0);
s.addChart(pres.charts.BAR, [
  { name: "All road tenders", labels: ["All categories", "Works", "Goods"], values: [0.136, 0.159, 0.041] },
  { name: "Without project P157054", labels: ["All categories", "Works", "Goods"], values: [0.046, 0.051, 0.041] },
], { x: 0.6, y: 1.5, w: 6.4, h: 4.9, barDir: "col", barGrouping: "clustered", chartColors: [TEAL, AMBER],
  showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.0%", dataLabelColor: INK,
  valAxisLabelFormatCode: "0%", valAxisMaxVal: 0.2, valGridLine: { color: "E3E9E8", size: 0.75 },
  catGridLine: { style: "none" }, showLegend: true, legendPos: "b", legendFontSize: 11,
  showTitle: true, title: "Re-bid or cancellation rate, 3,224 road tenders", ...CHART_TXT });
const rb = [["97%", "precision of the re-advertisement rule (29 of 30 sampled; 44 / 44 outside P157054)"],
  ["0.032 vs 0.039", "Brier score: grouped rates beat one global rate on the latest period"],
  ["AUC 0.46", "classifier not kept: rates with 90% intervals instead"]];
rb.forEach(([n, l], i) => {
  const y = 1.55 + i * 1.45;
  para(s, n, 7.4, y, 5.3, 0.6, { size: 28, bold: true, color: i === 2 ? RED : TEAL, valign: "bottom" });
  para(s, l, 7.4, y + 0.62, 5.3, 0.7, { size: 14 });
});
honest(s, "one project holds 79% of positives; recent rates are a lower bound (re-advertisement takes a median 134 days).", 7.4, 5.85, 5.3, 0.85);
s.addNotes(`Re-bid or cancellation (2 min). Measured, notebook 51, docs/07 §2.
Labels come from the public record, each with its reason: a second IFB 30+ days later (corrigenda, addenda, deadline extensions excluded), cancelled notices or plan entries, re-bid wording, and - the biggest source - the same package re-advertised under a NEW reference (same project, same description with the same numbers in order, earlier round without a contract, 30+ days later). Precision: 29 of 30 random flags correct, and all 44 outside the dominant project.
Result: 439 of 3,224 tenders (13.6%) were re-bid or cancelled. One project, P157054 (Madhya Pradesh rural roads), holds 345 of them (79%): whole batches were re-advertised. Without it the rate is 4.6%. I always show both.
Method: rare events, so hierarchical Beta-binomial rates (all → category → method → country), shrunk toward the parent, with 90% intervals. On the latest period they beat a single global rate (Brier 0.032 vs 0.039). A classifier only reached AUC 0.46, so it was not kept; the gate now requires AUC ≥ 0.6 as well as a Brier win.
Caveat: tenders from the last 6-16 months haven't had time to be re-advertised, so recent rates are a lower bound.
Amendments: no public labels, so the app shows a transparent indicator checklist; it does not track extracted amendment events (r = -0.08), so it is a heuristic, never a probability.`);

// 8. RAG flow
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "1 Plan" });
s.addText("Plan: a cited brief, built step by step", { placeholder: "title" });
journey(s, 0);
s.addImage({ path: path.join(IMG, "rag_flow.png"), x: 0.75, y: 1.45, w: 11.8, h: 5.43, altText:
  "RAG request flow: facts from SQL and models, evidence from graph-scoped hybrid search with rerank and gate, answer checked by mechanical checks and one verifier call" });
s.addNotes(`RAG flow (2 min). Source: docs/10 diagram 2, updated with routing, the French pass and the pure-vector gate. Live demo here if time allows: "Plan a package" in the app.
"This is a fixed sequence of tools, not a free-roaming agent, so every answer is reproducible and the same code runs in my unit tests, my evaluation and the app."
Row 1 - facts: SQL finds similar past contracts signed before the as-of date, with their coverage status; the models give the duration range and the re-bid-or-cancellation rate. Numbers never come from the LLM.
Row 2 - evidence: the graph scopes the search to the right projects; outcome and rating questions are routed to ICR / ICR Review / PPAR first. Hybrid search (vector + keyword, because embeddings are bad at codes like GYTJ201B) runs in three scopes: the project, lessons across the corpus, and past notices for planning. For French-speaking countries the question is also searched in French. Rerank favours independent reviews (IEG > ICR > ISR) and exact identifier matches, at most 2 passages per document. The gate needs cosine ≥ 0.68 or an exact ID match.
Row 3 - answer: passages with instruction-like text are withheld; Llama 3.3 70B at temperature 0 writes JSON claims with citation IDs; code checks every citation, number and ID against the cited text; one batched verifier call removes unsupported claims, failing closed. The answer shows how many claims were removed. Every request is logged.`);

// ---------- Journey step 2: Tender design ----------
pres.addSection({ title: "2 Tender design" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "2 Tender design" });
s.addText("Tender design: learn from past notices", { placeholder: "title" });
journey(s, 1);
bullets(s, [
  "Past REOI / IFB text for similar packages: scope, lots, qualification criteria (4,338 notice passages)",
  "PAD procurement sections: planned arrangements and risks",
  "ICR and IEG lessons with verbatim quotes, by theme",
  "Ask about a reference or project ID and it is matched exactly",
], 0.6, 1.6, 5.6, 3.3, 15);
s.addChart(pres.charts.BAR, [{ name: "Lessons", labels: ["Procurement capacity", "Contract management",
  "Design quality", "Safeguards", "Supervision", "Market capacity", "Packaging", "Specifications"],
  values: [279, 272, 183, 92, 70, 20, 6, 3] }], { x: 6.6, y: 1.45, w: 6.1, h: 4.2, barDir: "bar",
  chartColors: [TEAL], showValue: true, dataLabelPosition: "outEnd", dataLabelColor: INK, catAxisOrientation: "maxMin",
  valGridLine: { style: "none" }, catGridLine: { style: "none" }, valAxisHidden: true, showLegend: false,
  showTitle: true, title: "Lessons extracted from reports, by theme (first extraction run)", ...CHART_TXT });
honest(s, "standard bidding documents, ToRs and bid evaluation reports are not public data. Tender-design recall@5 is 0.33 strict / 0.67 lenient on only 3 test questions.", 0.6, 5.85, 12.1, 0.85);
s.addNotes(`Tender design (1.5 min).
For tender design the assessment asks to cross-reference specifications, qualification criteria and ToRs against history. Public data has the procurement notices (REOI / IFB) with scope, lots and qualification criteria, PAD procurement sections, and ICR / IEG lessons. I indexed 4,338 notice passages for the demo projects and search them in their own scope when planning.
Lessons: 1,198 lessons extracted by Llama 3.3 70B with verbatim quotes checked word for word (quotes that don't appear in the passage are rejected). The chart shows the themes from the first extraction run: procurement capacity and contract management dominate; packaging and specifications are rare, which itself says the reports rarely discuss tender design.
Honest limit: standard bidding documents, ToRs and bid evaluation reports are not available as public data, and tender design is my weakest question type: recall@5 0.33 strict / 0.67 lenient, on only 3 test questions. With internal STEP documents this module gets much stronger; the pipeline already handles notice text.`);

// 10. Guardrails
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "2 Tender design" });
s.addText("Every claim is checked before it is shown", { placeholder: "title" });
journey(s, 1);
const gm = [["1.00", "citation validity"], ["0.86–0.905", "claim support after guardrails (range over evaluation runs)"],
  ["97.5–100%", "of 40 kept claims supported, independent judge"], ["3 / 3", "off-topic questions refused"]];
gm.forEach(([n, l], i) => stat(s, 0.6 + i * 3.1, 1.5, 2.9, n, l, { size: n.length > 6 ? 32 : 40 }));
const layers = [["Mechanical", "Each claim cites a supplied source; every number ≥ 10 and every project ID appears in the cited text"],
  ["Verifier", "One batched LLM call reads each claim beside its sources; unsupported or no verdict → removed"],
  ["Quarantine", "Passages with instruction-like text never reach the model (planted injection: blocked)"],
  ["Conflicts", "Final outcome: IEG > ICR > latest ISR; every rating shown with its date; flags raised"]];
layers.forEach(([t, d], i) => {
  const x = 0.6 + (i % 2) * 6.15, y = 3.35 + Math.floor(i / 2) * 1.6;
  card(s, x, y, 5.95, 1.4);
  para(s, t, x + 0.25, y + 0.15, 5.4, 0.35, { size: 16, bold: true, color: TEAL });
  para(s, d, x + 0.25, y + 0.55, 5.45, 0.8, { size: 14 });
});
s.addNotes(`Guardrails and conflicts (2 min). Measured on the held-out test half, notebook 61, final run 2026-10-23.
Layers, cheapest first: the retrieval gate; evidence wrapped as data; quarantine of instruction-like passages; JSON output; mechanical checks (citation exists, every claim cited, every number ≥ 10 and project ID appears in a CITED source); then ONE batched verifier call per answer that reads each claim next to the exact text of its sources and fails closed.
Results: citation validity 1.00; claim support 0.86-0.905 after guardrails across the evaluation runs (the verifier removes about 6-9 claims per run); an independent judge found 97.5-100% of 40 kept claims supported; Same frozen questions and temperature 0, yet the LLM's answers and the verifier's verdicts vary slightly between runs, so answer-quality metrics are reported as the range over the 2026-10-23 evaluation runs.; refusal accuracy 3 of 3; the planted prompt-injection passage was quarantined and its claim never appeared.
Caveat: verifier and judge are the same model family as the generator, so the judge is a second opinion, not ground truth.
Conflicting evidence (ICR vs ISR): ratings are extracted by regex with their passage. For the final outcome, IEG review > ICR > latest ISR, among documents published by the as-of date; for "state at a date", the latest ISR before it. Nothing is hidden: every rating is shown with its date, and flags are raised (isr_icr_disconnect, ieg_downgrade, isr_volatility). Found: 1 IEG downgrade and 2 ISR volatility cases.`);

// ---------- Journey step 3: Monitor ----------
pres.addSection({ title: "3 Monitor" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "3 Monitor" });
s.addText("Monitor: early warnings that say “check”", { placeholder: "title" });
journey(s, 2);
const mon = [["91", "tenders past their predicted P80 with no contract"], ["16", "disbursement-lag flags"],
  ["38", "active projects flagged"], ["1 · 2", "IEG downgrade · ISR volatility conflicts"]];
mon.forEach(([n, l], i) => stat(s, 0.6 + i * 3.1, 1.5, 2.8, n, l));
card(s, 0.6, 3.4, 6.0, 2.3);
para(s, "Each flag shows its evidence", 0.85, 3.55, 5.5, 0.4, { size: 16, bold: true, color: TEAL });
bullets(s, ["Tender: notice date, predicted P50 / P80, days overdue", "Project: disbursement vs similar projects, latest ISR ratings",
  "Reported problems: amendments, delays, extensions with quotes"], 0.85, 4.05, 5.6, 1.6, 14);
honest(s, "an overdue flag means “check”, not “late”: only about a third of contracts join to a notice, so a missing contract may be a join failure. The backtest has only 3 projects; the amendment indicator does not track extracted events (r ≈ −0.08).",
  6.9, 3.4, 5.8, 2.3);
s.addNotes(`Monitor (1.5 min). Measured, notebook 70, docs/07 §5.
Execution & monitoring: the app's Monitor screen shows a project's rating timeline (ISR → ICR → IEG, conflicts flagged) and early-warning flags with their reasons.
On the demo run: 138 open tenders after removing rounds superseded by a re-advertisement and notices older than 3 years; 91 are past their predicted P80 with no contract; 16 projects lag on disbursement; 38 active projects have at least one flag. Rating conflicts: 1 IEG downgrade, 2 cases of ISR volatility.
Honest framing, stated in the app: an overdue flag means "check this tender", not "late". About one third of contracts match a notice, so an unmatched notice may simply be a contract that didn't join. The backtest (flag a year before the ICR vs ICR outcome) has only 3 projects, too few to read. The amendment indicator does not track the events extracted from reports (Spearman r ≈ -0.08), so it is shown as a checklist, never a risk estimate.`);

// 12. Evaluation
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "3 Monitor" });
s.addText("Evaluation, measured honestly", { placeholder: "title" });
const eh = ["Test half, 26 questions", "Before", "After translation", "Final"].map((t) => ({ text: t,
  options: { bold: true, color: WHITE, fill: { color: TEAL } } }));
const er = (r, hi) => r.map((t, i) => ({ text: t, options: { bold: hi && i === 3, fill: { color: hi ? "E3EFEC" : WHITE } } }));
s.addTable([eh,
  er(["Recall@5 strict (gold passage)", "0.52", "0.52", "0.57"], true),
  er(["Recall@5 lenient (any passage with the answer)", "0.78", "0.78", "0.83"], true),
  er(["French-source questions (3)", "0", "0", "0.33"]),
  er(["Claim support after guardrails", "–", "0.905", "0.86–0.893"]),
  er(["Refusal accuracy", "3 / 3", "3 / 3", "3 / 3"]),
], { x: 0.6, y: 1.5, w: 7.3, colW: [3.4, 1.0, 1.4, 1.5], fontFace: B, fontSize: 14, color: INK,
  border: { type: "solid", color: "D6E2E0", pt: 0.75 }, rowH: 0.5, valign: "middle", margin: [0, 0.12, 0, 0.12] });
para(s, "52 frozen questions, halves split by project; the gate is calibrated on one half, metrics reported on the other. Claim support varies a little between runs (LLM run-to-run variance).",
  0.6, 4.75, 7.3, 0.6, { size: 13, color: MUTED });
const lessons = [["Gate bug found and fixed", "Vector Search scores 1/(1+L2²), not cosine: the gate blocked 20 of 23 answerable questions. Now converted to cosine."],
  ["The gate is a weak filter", "On- and off-topic scores overlap (0.66–0.86 vs 0.66–0.70). Refusals rest on the answer step and the verifier."],
  ["French is only partly solved", "1 of 3 French questions found after English keywords + a French query pass. Fix: multilingual embeddings."]];
lessons.forEach(([t, d], i) => {
  const y = 1.5 + i * 1.75;
  card(s, 8.3, y, 4.4, 1.55);
  para(s, t, 8.5, y + 0.12, 4.0, 0.35, { size: 15, bold: true, color: i === 0 ? TEAL : "8A5A00" });
  para(s, d, 8.5, y + 0.5, 4.05, 1.0, { size: 13 });
});
s.addNotes(`Evaluation (2 min). Measured, notebook 61, docs/07 §4.
The set: 52 frozen questions (lessons, ratings, contracts, tender design, off-topic refusals), reviewed by me; synthetic questions had to paraphrase their source (word overlap measured). Halves are split by project so near-duplicates can't leak; the refusal threshold is set on the calibration half and every metric is on the test half.
Strict recall counts only the single gold passage; lenient recall asks an LLM judge whether any retrieved passage contains the answer (the same fact often appears in the ICR and its IEG review). Final: 0.57 strict, 0.83 lenient. The phase-0 target was 0.7: met leniently, not strictly.
Three lessons I'd highlight:
1. A bug I found by reading the numbers: Vector Search scores plain queries as 1/(1+L2 distance²). Our vectors have norm ~24, so scores were ~0.003 and the gate blocked 20 of 23 answerable questions. Verified the formula on a live query, converted to cosine, and the calibration grid now comes from the data itself.
2. Even fixed, the gate is a weak first filter: off-topic and answerable scores overlap. It blocks 2 of 3 off-topic questions; the third is refused by the answer step. The real protection is the answer step plus the verifier.
3. The embedding model is English-only. Translating 196 French passages for embedding changed nothing; adding English keyword matching over translations and a French version of the question found 1 of 3 French questions. Known limitation; production fix: a multilingual embedding model.`);

// ---------- Journey step 4: Learn ----------
pres.addSection({ title: "4 Learn" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "4 Learn" });
s.addText("Learn: the loop runs every week", { placeholder: "title" });
journey(s, 3);
const loop = ["Ingest new data", "Quality gate", "Extract new reports", "Embed changes + sync", "Retrain: champion / challenger", "Regression eval", "KPI dashboard"];
const cx = 3.4, cy = 4.15, r = 2.3;
s.addShape(pres.shapes.OVAL, { x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, fill: { color: WHITE }, line: { color: LINE, width: 1.5, dashType: "dash" }, objectName: "loop ring" });
loop.forEach((t, i) => {
  const a = -Math.PI / 2 + (i * 2 * Math.PI) / loop.length, x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x - 0.85, y: y - 0.3, w: 1.7, h: 0.6, rectRadius: 0.1,
    fill: { color: i === 1 || i === 5 ? AMBER : TEAL }, line: { color: WHITE, width: 1 }, objectName: `loop ${t}` });
  para(s, t, x - 0.85, y - 0.3, 1.7, 0.6, { size: 12, bold: true, align: "center", valign: "middle", color: i === 1 || i === 5 ? INK : WHITE });
});
para(s, "Weekly", cx - 1, cy - 0.25, 2, 0.5, { size: 20, bold: true, color: TEAL, align: "center", valign: "middle" });
bullets(s, [
  "Incremental at every step: only new documents are parsed, only changed passages re-embedded (e.g. 196 on 23 Oct)",
  "A model is promoted only if no worse on the latest time slice; the decision is logged in MLflow",
  "The run fails if recall@5 drops by more than 5 points",
  "👍 / 👎 and “wrong source” feedback → reviewed → added to the evaluation set",
  "AI/BI dashboard: data health, model quality, assistant quality, usage",
], 6.9, 1.6, 5.8, 4.6, 15);
s.addNotes(`Continuous learning (1.5 min). Module 4.
One Databricks job (jobs/refresh_job.json): ingest → silver → data-quality gate → documents (new only, parse once) → extraction (new chunks only, resumable, cost-capped) → embeddings (new or changed only) and Vector Search Delta Sync → retrain the duration model with champion/challenger (promote only if P50 MAE is no worse and P80 coverage stays 0.70-0.90 on the latest time slice) → re-bid rates → early warnings → RAG regression evaluation (fails the run if recall@5 drops more than 5 points) → KPI dashboard (runs even if the evaluation fails, so the failure shows).
Example of incremental: on 23 October only the 196 translated passages were re-embedded and synced; nothing else.
A new ICR becomes searchable, rated and mined for lessons on the next run, and its rating conflicts are flagged automatically.
Feedback: thumbs and "wrong source" are stored in ops.feedback; thumbs-down answers are reviewed and added to the evaluation set; wrong-source passages become negatives for a future learned reranker.
The KPI dashboard is defined as code in the repo (views + Lakeview JSON) and deployed by notebook 85.`);

// 14. Rollout
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "4 Learn" });
s.addText("Rollout with go / no-go criteria", { placeholder: "title" });
journey(s, 3);
const ph = [["0", "Offline (this work)", "Roads, 5 countries, frozen evaluation set", "recall@5 ≥ 0.7 · claim support ≥ 0.9 · P50 beats baseline · P80 0.7–0.9"],
  ["1", "Shadow pilot, 3 months", "~10 procurement specialists, one region, advisory only", "≥ 60% weekly active · ≤ 5% wrong-source · 0 unsupported claims in a 100-answer audit"],
  ["2", "Expansion", "All transport, then energy and water; all countries", "Metrics hold per sector · weekly refresh ≤ 4 h"],
  ["3", "Integration", "Links into STEP planning; internal data under access control", "Security and data-governance sign-off"]];
ph.forEach(([n, t, sc, ex], i) => {
  const y = 1.5 + i * 1.12;
  s.addShape(pres.shapes.OVAL, { x: 0.6, y: y + 0.15, w: 0.6, h: 0.6, fill: { color: i === 0 ? AMBER : TEAL }, line: { color: WHITE }, objectName: `phase ${n}` });
  para(s, n, 0.6, y + 0.15, 0.6, 0.6, { size: 18, bold: true, align: "center", valign: "middle", color: i === 0 ? INK : WHITE });
  para(s, t, 1.45, y + 0.05, 3.2, 0.4, { size: 16, bold: true, color: TEAL });
  para(s, sc, 1.45, y + 0.45, 3.4, 0.6, { size: 13, color: MUTED });
  para(s, ex, 5.0, y + 0.1, 7.7, 0.8, { size: 14, valign: "middle" });
});
card(s, 0.6, 6.0, 12.1, 0.75, "E3EFEC");
s.addText([{ text: "Phase 0 status (measured):  ", options: { bold: true, color: TEAL } },
  { text: "P80 coverage 0.81 ✔ · claim support 0.86–0.905 (around the 0.9 target) · P50 ≈ baseline · recall@5 0.57 strict ✘ / 0.83 lenient ✔", options: { color: INK } }],
  { x: 0.85, y: 6.0, w: 11.7, h: 0.75, fontFace: B, fontSize: 14, valign: "middle", margin: 0, isTextBox: true });
s.addNotes(`Rollout and KPIs (1.5 min).
Four phases, each with go / no-go criteria decided in advance (proposed). Phase 0 is this work. Its measured status: P80 coverage 0.81 meets the target; claim support 0.86-0.905 across runs sits around the 0.9 target, so not a clean pass; P50 is level with the baseline rather than better; recall@5 meets the target leniently (0.83) but not strictly (0.57). So phase 0 is not a clean pass and I would say so: the next work is retrieval quality and a multilingual model before a pilot.
Phase 1: a 3-month shadow pilot with about 10 specialists in one region, advisory only. Exit: at least 60% weekly active, at most 5% answers flagged wrong-source, and no unsupported claim in a 100-answer audit.
Business KPIs - planning cycle time (before/after timing), adoption, answers accepted without edit, forecast accuracy in use - are PROPOSED pilot targets. No numbers exist for them yet and I didn't invent any. The KPI dashboard already has the page for them, labelled "test usage only" until a pilot starts.`);

// 15. Limitations
pres.addSection({ title: "Close" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Close" });
s.addText("What is not solved yet, and what comes next", { placeholder: "title" });
const lim = ["Small evaluation set (26 test questions); verifier and judge are the same model family",
  "Duration P50 only level with the baseline; governance features did not help",
  "No public amendment labels: the indicator is a heuristic",
  "French retrieval partly solved (1 of 3); English-only embeddings",
  "Contract ↔ passage links are sparse (1.3%)",
  "Free Edition limits: 3 apps, 24 h app runtime, a daily compute quota"];
const nxt = ["Multilingual embedding model on Databricks", "STEP internal data: estimates, amendments, bid evaluations",
  "Larger, reviewed evaluation set; a different-family judge", "Shadow pilot with 10 specialists, then measure cycle time"];
card(s, 0.6, 1.5, 6.6, 4.3, "FDF1D8");
para(s, "Limitations (stated, not hidden)", 0.85, 1.65, 6.1, 0.4, { size: 17, bold: true, color: "8A5A00" });
bullets(s, lim, 0.85, 2.2, 6.15, 3.5, 15);
card(s, 7.5, 1.5, 5.2, 4.3, "E3EFEC");
para(s, "Next steps", 7.75, 1.65, 4.7, 0.4, { size: 17, bold: true, color: TEAL });
bullets(s, nxt, 7.75, 2.2, 4.75, 3.5, 15);
para(s, "Every number in this deck is measured; the tests, the evaluation and the app run the same pipeline.", 0.6, 6.1, 12.1, 0.5, { size: 16, italic: true, color: TEAL });
s.addNotes(`Limitations and next steps (1.5 min).
I'd rather name these than have them found:
- The evaluation set is small (26 test questions), partly synthetic and reviewed by me; the verifier and the judge are the same model family as the generator.
- The duration model's point estimate is only level with a median baseline; the governance hypothesis is not supported on 500 rows.
- There are no public amendment labels, so amendment risk is a heuristic checklist plus extracted events, not a probability.
- French retrieval is partly solved (1 of 3 French questions); the embedding model is English-only.
- Reports rarely name individual contracts (1.3% link coverage), so evidence is mostly at package and project level.
- Free Edition limits: 3 apps, apps stop after 24 hours, and a daily serverless quota that I hit once.
Next: a multilingual embedding model; STEP's internal data (cost estimates, amendments, bid evaluation reports), which would most improve the duration and amendment models; a bigger reviewed evaluation set with a judge from a different model family; then a shadow pilot that measures planning cycle time.
Close: "Every number you've seen is measured, and the code, the evaluation and the app all run the same pipeline. Happy to go deeper anywhere."`);

// ================= BACKUP =================
pres.addSection({ title: "Backup" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Backup: supplier entity resolution", { placeholder: "title" });
stat(s, 0.6, 1.5, 4, "322,655 → 272,271", "supplier records → suppliers", { size: 32 });
stat(s, 5.0, 1.5, 3.5, "119 → 40", "largest cluster, after the six guards", { size: 32 });
stat(s, 8.9, 1.5, 3.8, "43–47 / 50", "fuzzy-rule precision on a random sample (assistant-judged; spot-check pending)", { size: 32 });
const rules = [["R1", "Same supplier ID"], ["R2", "Same cleaned name + country"], ["R3", "Near-identical name + country, firms only"]];
rules.forEach(([k, d], i) => {
  card(s, 0.6 + i * 4.1, 3.6, 3.8, 1.2);
  para(s, k, 0.85 + i * 4.1, 3.7, 0.8, 1.0, { size: 26, bold: true, color: TEAL, valign: "middle" });
  para(s, d, 1.65 + i * 4.1, 3.7, 2.6, 1.0, { size: 14, valign: "middle" });
});
para(s, "Guards: shared-ID, common-name, numbers, joint venture, distinctive word, chain length. Masked names (“INDIVIDUAL CONSULTANT” with no ID) are never merged and get NULL history, never 0. Precision is preferred over recall: a wrong merge shows a user someone else's track record.",
  0.6, 5.1, 12.1, 1.2, { size: 14 });
s.addNotes(`Backup - entity resolution.
Rules in order: R1 same supplier ID; R2 same cleaned name and country; R3 near-identical name and country, firms only. Six guards stop bad chains: shared-ID, common-name, numbers in the name, joint ventures, a distinctive-word check and a chain-length limit. The largest cluster fell from 119 to 40 records after the guards.
The placeholder "INDIVIDUAL CONSULTANT" had merged 176 people before the fix; masked names without an ID are now never merged and get NULL history.
Fuzzy-rule precision 43-47 of 50 on a random sample, assistant-judged sample; my own spot-check is still pending. Supplier names keep non-Latin scripts; only accents are removed. Matching to registered companies is future work.`);

s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Backup: extraction from reports", { placeholder: "title" });
stat(s, 0.6, 1.5, 3.8, "1,548", "LLM calls, approved in advance (dry run first), 0 errors");
stat(s, 4.6, 1.5, 3.8, "1,198", "lessons kept, each with a verbatim quote");
stat(s, 8.6, 1.5, 4.1, "1,086", "events kept: delays 344, amendments 223, cancellations 194, extensions 170");
bullets(s, ["Only events that HAPPENED: conditions, plans and project names are rejected (89 rejected as not happened)",
  "Quotes must appear word for word in the passage (83 event quotes rejected)",
  "A filter bug (“résili” matched “resilience”) cut the calls from 1,767 to 1,548 before any spending",
  "Ratings by regular expression, not LLM: 230 ratings for 16 projects",
  "Event → contract links: 3 of 1,086 (reports describe packages, not contracts)"], 0.6, 3.5, 12.1, 3.0, 15);
s.addNotes(`Backup - extraction (notebook 55).
Dry run first: count the calls, then run only after approval with a hard cap. 1,548 calls, resumable batches of 100, 0 call errors. A trigger-word bug (the French "résili" matched English "resilience") was found in the dry run and cut 219 calls before spending anything.
Kept 1,198 lessons and 1,086 events after the type remap; every item quotes its passage word for word. Events must have happened: conditional text such as "will be subject to re-bidding" and project names such as "Projet de Relance" are rejected (89 not-happened rejections in the first run).
Ratings are extracted with deterministic regular expressions on the six-point scale, not the LLM, because they drive conflict rules.
Lesson and event precision on 20 + 20 samples is still to be hand-reviewed.`);

s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "Backup" });
s.addText("Backup: the fake 0.93 AUC", { placeholder: "title" });
stat(s, 0.6, 1.5, 3.6, "0.93", "classifier AUC on the first re-bid labels", { color: RED });
stat(s, 4.6, 1.5, 3.6, "120 of 171", "positives were false", { color: RED, size: 34 });
stat(s, 8.6, 1.5, 4.1, "0.46", "AUC after fixing the labels: classifier not kept");
bullets(s, ["“Projet de Relance et de Développement…”: relance means recovery, a project name, in 86 of 88 tenders",
  "Lebanon: “Not awarded lots… will be subject to re-bidding” printed in every invitation (34 of 43)",
  "Fix: wording inside a project name or after a future/conditional phrase never counts; recall re-checked on 125 notices",
  "Lesson: a high AUC on rare labels is a reason to read the positives, not to celebrate"], 0.6, 3.5, 12.1, 3.0, 15);
s.addNotes(`Backup - the fake AUC story (also a STAR story in docs/13).
The first re-bid labels gave 171 positives and a classifier AUC of 0.93. Reading the positives showed 120 were false: a French project name containing "Relance" (recovery) in 86 of 88 tenders of one project, and a Lebanese invitation template saying lots "will be subject to re-bidding" in every notice. The model had learned two projects, not re-bidding.
Fix: re-bid wording never counts inside a project name or after a future or conditional phrase; bare "relance" needs a procurement word next to it. Then a recall check: every notice with a whole-word trigger (125) was read: 0 real re-bids missed. The fix also exposed the bigger recall gap - re-advertisement under a new reference - which became the main label source (97% precision).
After the fix the classifier scored 0.50, then 0.46, so it is not kept; rates with intervals are shown instead.`);

(async () => {
  await pres.writeFile({ fileName: OUT });
  const { applyTheme } = require(process.env.APPLY_THEME);
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
