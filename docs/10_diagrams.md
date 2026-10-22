# 10 — Diagrams for the interview

Status as of 2026-10-22, taken from `notebooks/README.md`, `docs/00`, `docs/06` and `docs/07`:

- ✅ **done and run** on Databricks (numbers measured)
- 🟡 **written and unit-tested, not yet run** on Databricks (203 local unit tests pass)
- 📝 **proposed only** (written up in `docs/08_proposal.md`)

---

## Diagram 1 — System architecture

```mermaid
flowchart TB
  subgraph SRC["World Bank public sources ✅"]
    direction LR
    S1["Contract awards<br/>DS01004 · DS04691 · DS00005"]
    S2["Projects API<br/>notices · disbursements"]
    S3["CPIA + WGI<br/>indicators"]
    S4["Reports: PAD · ISR · ICR<br/>ICR Review · CPF"]
  end

  subgraph M1["M1 — Ingestion and knowledge representation (Unity Catalog: procurement)"]
    direction TB
    B["bronze<br/>raw copies, retry/backoff<br/>nb 10 ✅"]
    SV["silver<br/>typed, standard values, countries,<br/>coverage by FY, durations · nb 11 ✅"]
    QG{{"Quality gate · nb 12 ✅<br/>14/14 checks passed"}}
    ER["Supplier resolution<br/>R1 ID · R2 name · R3 fuzzy · nb 30 ✅"]
    KG["gold graph<br/>nodes + edges, available_from<br/>nb 40 ✅"]
    DOC["docs<br/>ai_parse_document → chunks<br/>299 docs · 5,226 chunks · nb 20 ✅"]
    NT["Notice text chunks<br/>nb 21 🟡"]
    VS[("Embeddings + Vector Search<br/>hybrid, keyword fallback · nb 60 🟡")]
  end

  subgraph M2["M2 — Models and RAG"]
    direction TB
    DUR["Duration model<br/>P20/P50/P80 vs median · nb 50 🟡"]
    RB["Re-bid or cancellation rates<br/>Beta-binomial, 90% intervals · nb 51 ✅"]
    EX["Ratings by regex ✅<br/>LLM lessons + events 🟡 · nb 55"]
    EW["Early warnings · nb 70 🟡"]
    RAG["RAG flow · 🟡<br/>see Diagram 2"]
  end

  subgraph M3["M3 — Guardrails and conflicts"]
    direction TB
    GR["Citation, number, ID checks<br/>+ claim verifier 🟡"]
    CF["Conflict rules<br/>IEG > ICR > latest ISR 🟡"]
    EV["RAG evaluation<br/>calibration / test split · nb 61 🟡"]
  end

  APP["Streamlit app · nb 80 🟡<br/>Plan a package · Monitor a project · Ask the evidence"]

  subgraph M4["M4 — Continuous learning and rollout"]
    direction LR
    JOB["Weekly refresh job 🟡<br/>gate · incremental · champion/challenger<br/>regression eval"]
    FB["Feedback → ops.feedback 🟡"]
    RO["Phased rollout + online KPIs 📝"]
  end

  SRC --> B --> SV --> QG
  SV --> ER --> QG
  QG --> KG
  QG --> DUR
  QG --> RB
  S4 --> DOC --> VS
  SV --> NT --> VS
  DOC --> KG
  DOC --> EX
  KG --> RAG
  VS --> RAG
  DUR --> RAG
  RB --> RAG
  EX --> CF
  EX --> EW
  DUR --> EW
  RAG --> GR --> APP
  CF --> APP
  EW --> APP
  EV -. "calibrates gate" .-> RAG
  APP --> FB
  FB -. "bad answers → eval set" .-> EV
  JOB -. "re-runs weekly" .-> B
  RO -. "after pilot" .-> APP
```

**What I'd say:** "Everything runs inside one Databricks workspace, governed by Unity Catalog. Public World Bank data
comes in raw to bronze, so I never have to call the APIs again to fix a cleaning bug. Silver types and standardises it,
maps old country codes and labels how complete each fiscal year is. Before anything downstream runs, an automated
quality gate checks the data and stops the pipeline if a check fails; on the full data it passed 14 of 14. From silver
I resolve suppliers and build a knowledge graph as two plain Delta tables, nodes and edges, where every edge records
when the fact became public, so models can't see the future. Reports are parsed once with `ai_parse_document`, cut into
section-aware chunks and embedded into Vector Search, which is the main retrieval path. The models produce the
numbers, the RAG flow finds the evidence, guardrails check every claim, and the app shows all three. A weekly job
re-runs the whole chain incrementally, retrains with a champion/challenger rule and fails if retrieval gets worse. The
ingestion, cleaning, graph and documents have run; the models, index, RAG and app are written and unit-tested but
still waiting for their first run."

---

## Diagram 2 — RAG request flow (status: 🟡 code unit-tested with fakes, not yet run on Databricks)

```mermaid
flowchart TB
  IN["User input<br/>country, project, category, method,<br/>value, scope, as-of date"] --> T{"Plan a package<br/>or Ask?"}
  T -- Plan --> Q1["1 · SQL: similar contracts<br/>≤ 10, signed before as-of,<br/>with coverage status"]
  Q1 --> Q2["2 · Model numbers<br/>duration P20/P50/P80<br/>re-bid or cancellation rate"]
  Q2 --> Q3["3 · Graph scoping<br/>projects → documents<br/>+ DESCRIBES links"]
  T -- Ask --> Q3
  Q3 --> SA["4 · Search A<br/>project-scoped"]
  Q3 --> SB["5 · Search B<br/>lessons-scoped"]
  Q3 --> SN["5b · Notice search<br/>(planning only)"]
  SA & SB & SN --> RR["6 · Merge + rerank<br/>each search = vector + keyword/ID<br/>authority IEG > ICR > ISR > PAD<br/>ID-match bonus · ≤ 2 per doc · top 8"]
  RR --> G{"7 · Retrieval gate<br/>score ≥ threshold<br/>or exact ID match?"}
  G -- no --> R1["Refuse:<br/>no supporting evidence"]
  G -- yes --> QZ{"Quarantine:<br/>drop passages with<br/>instruction-like text.<br/>Any left?"}
  QZ -- no --> R1
  QZ -- yes --> GEN["8 · Llama 3.3 70B, temperature 0<br/>evidence wrapped as data<br/>JSON claims + citation IDs"]
  GEN --> P{"Valid JSON?"}
  P -- no --> R2["Refuse:<br/>unparseable"]
  P -- yes --> MC["9a · Mechanical checks<br/>citation exists · every claim cited<br/>numbers + IDs in cited source"]
  MC -- fail --> X["Claim removed<br/>and counted"]
  MC -- pass --> VF["9b · One batched verifier call<br/>supported? fails closed"]
  VF -- "no / no verdict" --> X
  VF -- supported --> K{"Any claims left?"}
  K -- no --> R3["Answer: insufficient evidence"]
  K -- yes --> OUT["Answer<br/>cited claims + sources<br/>model numbers from step 2 (never from LLM)<br/>+ count of removed claims"]
  OUT --> LOG[("ops.rag_requests<br/>+ user feedback")]
  R1 & R2 & R3 --> LOG
```

**What I'd say:** "This is a fixed sequence of tools, not a free-roaming agent, so every answer is reproducible and
the same code runs in my tests, my evaluation and the app. When a planner describes a package, SQL first finds
similar past contracts, and the models give the duration range and the re-bid-or-cancellation rate. Those numbers
never come from the LLM. The graph then narrows the search to the right projects, and I run hybrid searches, vector
plus keyword, because embeddings are bad at codes like contract references. After reranking, which favours the
independent IEG review over self-evaluations, a retrieval gate refuses if the evidence is weak; its threshold is
calibrated on half of my evaluation questions and reported on the other half. Passages that look like instructions
are held back entirely. Llama writes JSON claims, each with citations. Code then checks every claim: the citation
exists, and every number and ID appears in the cited text. A single verifier call then reads each claim next to its
source and removes anything not clearly supported. The answer says how many claims were removed, and every request is
logged."

---

## Diagram 3 — Build timeline

```mermaid
flowchart TB
  A["Stage 1 · Planning<br/>traceability matrix, hypotheses with pass/fail rules"] --> CA(["Checkpoint a ✅"])
  CA --> B["Stage 2 · Data verification<br/>every source called and measured"]
  B --> CB(["Checkpoint b ✅<br/>5 countries, 16 demo projects"])
  CB --> C["Stage 3a · Data model + entity resolution<br/>one key per real thing"]
  C --> D["Stage 3 · Ingestion<br/>bronze → silver, coverage labels, durations"]
  D --> E["Documents<br/>parse once, section-aware chunks"]
  E --> CC(["Checkpoint c ✅<br/>Vector Search primary, links supplementary"])
  CC --> CD(["Checkpoint d ✅<br/>design approved with 7 changes"])
  CD --> DQ["Data-quality pass ✅<br/>joint awards, standard values, gate 14/14"]
  DQ --> F["Models + RAG<br/>re-bid rates ✅ · extraction dry run ✅<br/>duration, index, RAG 🟡"]
  F --> G["Evaluation 🟡<br/>nb 50 · 61 · 70 results pending"]
  G --> H["App 🟡<br/>nb 80, deploy steps written"]

  nB1["DS04691 found: the assumed<br/>FY2017–19 gap was partial coverage"] -.-> B
  nB2["Parser: 94% vs 26%<br/>well-formed tables"] -.-> B
  nB3["Procurement-plan dates rejected:<br/>20% agree with signing dates"] -.-> B
  nC1["INDIVIDUAL CONSULTANT placeholder<br/>had merged 176 people → handled by ID"] -.-> C
  nD1["Missing ≠ zero: CPIA flag,<br/>masked suppliers, AF disbursements"] -.-> D
  nE1["Chunk median 53 → 339 tokens"] -.-> E
  nE2["Supplier guards: largest<br/>cluster 119 → 40 records"] -.-> CC
  nE3["Contract–passage links: one fix pass,<br/>1.3% coverage, then stopped"] -.-> CC
  nF1["Re-bid label fix: 120 false positives<br/>('Relance' name, conditional text),<br/>fake AUC 0.93 → after fix 0.50 / 0.46, not kept"] -.-> F
  nF2["Re-advertised under new reference:<br/>29/30 sampled flags correct"] -.-> F
  nF3["Extraction filter bug ('resilience'):<br/>1,767 → 1,548 LLM calls"] -.-> F

  classDef note fill:#fff8e1,stroke:#c9a227,color:#333
  class nB1,nB2,nB3,nC1,nD1,nE1,nE2,nE3,nF1,nF2,nF3 note
```

**What I'd say:** "I built this in stages with four checkpoints where I stopped for review. I started with a
traceability matrix so nothing in the brief got lost, and wrote my assumptions as hypotheses with pass/fail rules
decided in advance. Then I checked every data source before writing a pipeline, and that paid off: a third contract
dataset filled what I'd assumed was a three-year gap, and the procurement-plan dates agreed with official signing dates
only 20% of the time, so I dropped them from the models. During ingestion I treated missing values as missing, never as
zero, and measured chunk size instead of guessing it. Several fixes came from looking at real data rather than trusting
tests. The clearest is the re-bid label: a French project name containing 'Relance', which means recovery, plus
conditional boilerplate created 120 false positives and an AUC of 0.93 that was fake. After the fix the classifier
scored 0.50, and 0.46 on the latest run, so I didn't keep it and report rates with uncertainty instead. The data, documents and graph have run; the
model, retrieval evaluation and app runs are next."
