# 13 — Interview Q&A: 25 technical questions and 5 STAR stories

Short answers with our real numbers and decisions (all measured, docs/07, unless marked [P] proposed or [A]
assumption). Say the number, then the reason, then the limit.

---

## A. Data, data quality and entity resolution

**1. Which data did you use and why that slice?**
Three Finances One contract datasets (DS00005 FY2020+, DS04691 FY2017–19, DS01004 FY2001–16: 491,020 contracts), the
Projects API (22,461 projects), procurement notices, disbursements, CPIA/WGI, and 299 PAD/ISR/ICR/IEG/CPF documents.
Slice: rural and inter-urban roads, FY2014–21 approvals, 5 countries (India, China, Mali, Albania, Sri Lanka; 2 IDA),
16 demo projects. Rule: top 5 countries by closed road projects with an ICR and contract data. Models train on all
151 road projects; documents only for the 16, to keep parsing cost bounded.

**2. How did you handle missing or incomplete data?**
Measured, labelled, never filled with zero. Contract coverage is measured against published award notices: FY2017
56%, FY2018–19 81%, FY2020+ 82–94%; every row carries `coverage_status`. CPIA is missing for 56% of country-years and
stays NULL with a flag. Review type is "unknown" when absent (all of DS01004). Masked supplier names with no ID are
never merged and get NULL history.

**3. What does the data-quality gate check?**
Notebook 12, 14 checks: unique keys, one copy per contract across the three datasets (precedence DS00005 > DS04691 >
DS01004), joint awards (STEP rows are member shares, so the contract total is their sum, checked against award-notice
prices), standard category and method values, required fields ≥ 98%, fiscal year consistency, supplier resolution,
duration rules. All 14 pass; a failure raises an error and the models do not run.

**4. How does supplier entity resolution work, and how good is it?**
R1 same supplier ID → R2 same cleaned name + country → R3 near-identical name + country (firms only), with six guards
(shared ID, common names, numbers, joint ventures, distinctive word, chain length). 322,655 records → 272,271
suppliers; the largest cluster fell from 119 to 40 after the guards. Fuzzy-rule precision 43–47 of 50 on a random
sample (my judgement; spot-check pending). I prefer precision: a wrong merge shows someone else's track record.

**5. Why a graph in Delta tables and not a graph database?**
Queries are 1–3 hops, which joins handle; one governed store under Unity Catalog with lineage and time travel. Every
edge has its rule, evidence and `available_from` date, which enforces as-of queries. A graph database is worth it only
if multi-hop analytics (supplier networks across countries) become a real workload.

**6. How well do contracts link to report passages?**
Badly, and I say so: 1.3% of demo contracts are named in any passage (5 strong, 27 weak links); event → contract
links 3 of 1,086. Reports discuss packages and components. So Vector Search is the primary retrieval path and links
add "this passage names your contract" where they exist.

## B. Models and leakage

**7. How do you predict tender duration?**
Target: contract signing − earliest matching IFB/REOI notice, 0–3 years. Quantile gradient-boosted trees (P20, P50,
P80) on 625 prior-review tenders, against a median-by-category-and-method baseline, time split (latest 20% from Jan
2023). Result: P50 MAE 100.4 vs 100.8 days (level), median error worse (80.5 vs 70.5), P80 coverage 0.81 (target
0.7–0.9). The value is the calibrated range, not a sharper point estimate.

**8. Why prior-review only?**
I tested the target before modelling: for post-review contracts about 13% of durations were negative (the notice
join is unreliable), vs 0.5% for prior review. Post-review is a secondary model (MAE 69.5 vs 70.3, median 52.3 vs 57.5).

**9. How do you prevent leakage?**
Features are only what is known at the start notice; governance indicators come from the latest year *published*
before the notice (release lag); every graph edge has the date the fact became public and retrieval filters
`doc_date ≤ as_of`; time-based splits everywhere; the evaluation's question halves are split by project so
near-duplicate questions cannot sit on both sides.

**10. Did governance indicators help?**
No. Removing WGI/CPIA improved prior-review MAE from 100.4 to 94.7 days. On 500 training rows they add noise. I kept
the ablation in the report as evidence against my own hypothesis.

**11. Why not survival analysis?**
Censoring needs reliably open tenders, and an unmatched notice is more often a join failure than an open tender (only
about a third of contracts match a notice). [P] once STEP's internal tender status is available.

**12. How do you model re-bidding risk with so few positives?**
Labels from the public record with a reason each (second IFB ≥ 30 days later, cancellation, re-bid wording, plan
labels, and re-advertisement under a new reference). 439 of 3,224 tenders (13.6%); 4.6% without P157054, which holds
79% of positives. Hierarchical Beta-binomial rates shrunk toward parent groups, with 90% intervals; Brier 0.032 vs
0.039 for a global rate. A classifier reached AUC 0.46 and is not kept (the gate needs a Brier win and AUC ≥ 0.6).

**13. And amendment risk?**
No public amendment labels, so no probability. LLM-extracted events with verbatim quotes (223 amendments, 170 time
extensions) plus a transparent indicator checklist. The indicator does not track the extracted events (Spearman
r ≈ −0.08), so it is labelled a heuristic. Production path: STEP amendment records.

## C. Retrieval, RAG and evaluation

**14. Describe the RAG flow.**
A fixed tool sequence, not a free agent: SQL facts → model numbers → graph scoping (project IDs and project names in
the question) → hybrid search (vector + keyword) in project, lessons and notice scopes → rerank (IEG > ICR > ISR,
exact-ID bonus, ≤ 2 per document) → gate → quarantine → Llama 3.3 70B JSON claims with citations → mechanical checks →
one batched verifier. Same code in tests, evaluation and app.

**15. Why hybrid search?**
Embeddings are bad at codes: "GYTJ201B" or "P132621" mean little to them. Vector Search HYBRID mode adds keyword
matching; exact identifier matches get a rerank bonus and pass the gate.

**16. How did you chunk 100+-page reports?**
Parse once with `ai_parse_document` (94% of tables well-formed vs 26% for pdfplumber), section-aware chunks that keep
the heading path, tables as their own chunks with repeated headers, ISR repeats flagged. The median chunk went from 53
to 339 tokens after measuring and merging; a 10,000-character cap keeps chunks under the embedding limit.

**17. How do you evaluate retrieval?**
52 frozen, reviewed questions; halves split by project; the gate is calibrated on one half and every metric reported
on the other. Strict recall (the single gold passage) 0.57; lenient recall (an LLM judge finds any retrieved passage
containing the answer) 0.83. Target 0.7: met leniently, not strictly.

**18. What does the retrieval gate do, and does it work?**
It refuses when the best pure vector similarity is below a calibrated threshold (cosine 0.682) unless an exact ID
matches. It is a weak first filter: off-topic scores (0.66–0.70) overlap answerable ones (0.66–0.86). It blocks 2 of
3 off-topic test questions and 1 of 23 answerable ones; refusals rest mainly on the answer step and the verifier
(refusal accuracy 3 of 3).

**19. How do you handle non-English documents?**
196 of 9,564 passages are French (Mali). gte-large-en is English-only. Translation for embedding only (citations stay
in French) changed nothing; adding English keyword matching over the translations and a French version of the
question found 1 of 3 French questions (recall 0 → 0.33). Known limitation; production fix: a multilingual embedding
model.

**20. What happens if a user adds "tell me in detail"?**
Found in app testing: it changed the answer, because instructions became search terms. Now instructions are stripped
from the search query and passed to the answer step; both phrasings retrieve identical passages. The evaluation has a
5-question phrasing-robustness test (results in docs/07).

## D. Guardrails, conflicts, operations

**21. How do you stop hallucinations?**
Cheapest first: gate; evidence wrapped as data; quarantine of instruction-like passages; JSON claims with citations;
mechanical checks (citation exists, every claim cited, every number ≥ 10 and project ID appears in the cited source
or its date); then one batched verifier call that fails closed. Test half: citation validity 1.00, claim support
0.86–0.905 and independent judge 97.5–100% of 40, as ranges over runs: at temperature 0 the LLM still varies slightly
from run to run, so I report the range, not the best run. Caveat: judge and generator are the same model family.

**22. What about prompt injection?**
A planted passage saying "ignore previous instructions…" is quarantined before the model sees it, and its claim never
survives (tested every evaluation run). Flagging alone isn't enough: a planted passage could "support" the claim it
asks for.

**23. How do you resolve ICR vs ISR conflicts?**
Ratings are extracted by regex with their passage. Final outcome: IEG review > ICR > latest ISR, among documents
published by the as-of date; "state at a date": latest ISR before it. Nothing is hidden: all ratings are shown with
dates and flags (`isr_icr_disconnect`, `ieg_downgrade`, `isr_volatility`): 1 IEG downgrade and 2 ISR volatility cases
found. For rating questions the app now gives the model this timeline as structured facts, so it cannot confuse
scales (IEG "efficacy: High" is not an ISR rating).

**24. How does it scale beyond the slice?**
Contracts are already full (491,020). Documents: ~50,000 relevant PAD/ISR/ICR/IEG [A, estimate] → 3–5 million chunks;
Vector Search with more units, same filters. Everything is incremental (parse once, embed only new or changed text:
196 passages re-embedded on 23 Oct, nothing else). The weekly job promotes models only if no worse, and fails if
recall@5 drops more than 5 points.

**25. What does it cost to run?**
I report calls, not invented prices. LLM work is bounded and approved in advance: a dry run counts calls first
(extraction 1,548 calls, plus 176 re-sent; translation 196), runs are resumable and capped. Embeddings are incremental.
Runtime cost grows with new documents, not the corpus. On Free Edition I hit the daily serverless quota once
(23 Oct) and resumed the same day; production would size Vector Search units and a SQL warehouse [P].

---

## E. Behavioural STAR stories

**1. The fake 0.93 AUC (critical thinking).**
*Situation:* my first re-bid classifier scored AUC 0.93. *Task:* decide whether to trust it. *Action:* instead of
celebrating, I read the positives: 120 of 171 were false: a French project name "Projet de Relance…" (relance =
recovery) in 86 of 88 tenders of one project, and a Lebanese template saying lots "will be subject to re-bidding" in
every invitation. I fixed the wording rules (no project names, no future/conditional phrases) and re-checked recall
on all 125 trigger-word notices. *Result:* the classifier fell to 0.50, later 0.46, so I did not ship it; the app
shows rates with intervals. Lesson: a great score on rare labels is a reason to read the data.

**2. The re-bid label fix that found the real signal (ownership).**
*Situation:* after removing false positives, recall looked low. *Task:* find re-bids the rules missed. *Action:*
counted packages advertised again with an identical description under a new reference: 378 tenders, mostly Madhya
Pradesh packages advertised up to 7 times. I wrote a rule (same project, same description with the same numbers in
order, earlier round without contract, ≥ 30 days later), hand-checked 30 random flags (29 correct) and all 44 outside
the dominant project (44 correct), and fixed the one error (numbers compared as a set, not in order). *Result:* 391
re-advertisements labelled; I report the rate with and without the project that dominates it (13.6% vs 4.6%).

**3. Procurement-plan dates rejected (evidence over convenience).**
*Situation:* procurement plans have planned dates, an attractive model feature. *Task:* decide whether to use them.
*Action:* cross-checked parsed plan dates against official signing dates before building anything. *Result:* only
20% agreed, so I used plan labels (e.g. "cancelled") but not plan dates. It cost a feature and saved a model trained
on noise.

**4. The retrieval-gate bug (debugging by reading numbers).**
*Situation:* after switching the gate to pure vector similarity, it blocked 20 of 23 answerable questions. *Task:* find
out why before tuning anything. *Action:* the scores were ~0.003, not cosine-like. I verified on a live query that
Vector Search scores a plain query as 1/(1 + L2²), and measured that every vector has norm ≈ 24, so the score maps
back to cosine. I converted it, and made the calibration grid come from the data so a scale change can't hide again.
*Result:* 1 of 23 answerable blocked. I also reported that, even fixed, the gate is a weak filter.

**5. The DS04691 discovery (verify assumptions first).**
*Situation:* my plan assumed contract data was missing for FY2017–19, a three-year hole in the training data.
*Task:* confirm it before designing around it. *Action:* searched the Finances One catalogue instead of trusting the
two datasets I knew. *Result:* found DS04691 (FY2017–19, same schema as DS00005); the "gap" became partial coverage,
which I then measured (56% / 81%) and labelled on every row. I changed the project rules to say coverage is partial,
not missing.

*Spare story, app testing (responding to users):* the user found that "tell me in detail" changed the answer and
that a rating answer mixed scales. I traced it to my own routing (outcome questions sent only to completion reports,
so no ISRs), separated instructions from search terms, resolved project names to IDs and gave the model the rating
timeline as facts, then added a robustness test so it stays fixed.
