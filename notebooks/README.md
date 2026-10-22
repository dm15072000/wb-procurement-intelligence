# Notebooks: run order

Sync the repo to a Databricks Git folder first. Every notebook imports tested code from `src/`.

| Order | Notebook | What it does | Calls external APIs / endpoints? | Status |
|---|---|---|---|---|
| done | `00_verify_workspace.py` | Read-only checks of workspace features | no | ran 2026-10-20 |
| done | `01_parser_benchmark.py` | Parser benchmark on 3 ICRs (D6) | yes (3 PDFs) | ran |
| done | `02_procplan_test.py` | Procurement Plan parse test; stores plan parses | yes | ran |
| done | `10_ingest_structured.py` | Structured sources → raw JSONL → bronze (resumable; `run_tag` widget) | yes | ran 2026-10-21 |
| done | `11_silver_structured.py` | Bronze → silver; durations; coverage; quality checks | no | ran |
| done | `20_ingest_documents.py` | Slice documents → PDFs → parse once → chunks; plan labels | yes | ran |
| **1** | `30_entity_resolution.py` | Supplier resolution. **Re-run**: R2 bug fix for same-name individuals (2026-10-21) | no | re-run |
| **2** | `40_build_graph.py` | Graph tables + contract↔passage links (re-run after 30) | no | re-run |
| **3** | `50_duration_model.py` | Duration P20/P50/P80, baseline, ablations, MLflow, UC registry, champion rule | no | new |
| **4** | `51_rebid_risk.py` | Re-bid labels, Beta-binomial rates, classifier gate | no | new |
| **5** | `55_extract_ratings_lessons_events.py` | Ratings (regex) + conflicts; lessons/events via `ai_query` (LLM, pay-per-token) | LLM endpoint | new |
| **6** | `60_embeddings_vector_index.py` | Embeddings (incremental) + Vector Search index (`CREATE_ENDPOINT` flag, D4) + fallback test | embedding endpoint, VS | new |
| **7** | `61_rag_evaluation.py` | Builds the eval set (review lesson questions!), runs RAG, calibrates gate, injection test, metrics | LLM + embeddings | new |
| **8** | `70_early_warnings.py` | Early-warning flags, amendment indicator, backtest | no | new |
| **9** | `80_app_setup.py` | ops tables, grants for the app's service principal, deployment steps | no | new |

Each notebook ends with a **summary cell**: paste its output back, and the numbers go into `docs/07_evaluation.md`.
Re-running is safe: downloads, parsing, embeddings and LLM extraction skip what's already done.
The weekly refresh job that chains all of these is `jobs/refresh_job.json` (paused; create it only when you decide to).
