# Notebooks: run order

Sync the repo to a Databricks Git folder first. Every notebook imports tested code from `src/`.

| Order | Notebook | What it does | Calls external APIs / endpoints? | Status |
|---|---|---|---|---|
| done | `00_verify_workspace.py` | Read-only checks of workspace features | no | ran 2026-10-20 |
| done | `01_parser_benchmark.py` | Parser benchmark on 3 ICRs (D6) | yes (3 PDFs) | ran |
| done | `02_procplan_test.py` | Procurement Plan parse test; stores plan parses | yes | ran |
| done | `10_ingest_structured.py` | Structured sources → raw JSONL → bronze (resumable; `run_tag` widget) | yes | ran 2026-10-21; no re-run needed |
| **1** | `11_silver_structured.py` | Bronze → silver. **Re-run (2026-10-22):** standard category / method values, explicit joint-award amount rule, new columns | no | re-run |
| **2** | `30_entity_resolution.py` | Supplier resolution (R2 fix 2026-10-21; must follow 11 because award rows changed) | no | re-run |
| **3** | `12_data_quality.py` | **Data-quality gate**, checks 2–7, fails loudly; joint-award check; demo-project coverage report | no | new |
| **4** | `20_ingest_documents.py` | Documents: only if not already done. Re-running is safe and skips what exists | yes | optional |
| **5** | `40_build_graph.py` | Graph tables + contract↔passage links (after 30) | no | re-run |
| **6** | `21_notice_chunks.py` | REOI / IFB notice text of slice projects → `docs.notice_chunks` ("Procurement Notice") | no | new |
| **6b** | `22_translate_for_embedding.py` | Language of every passage; French passages translated to English for embedding only (dry run by default; `mode` = translate + `approved_calls`) | LLM endpoint | new |
| **7** | `50_duration_model.py` | Duration P20/P50/P80 (standard method values now), baseline, MLflow, UC registry, champion rule | no | new |
| **8** | `51_rebid_risk.py` | Re-bid labels (modification notices excluded), Beta-binomial rates, classifier gate | no | new |
| **9a** | `55_extract_ratings_lessons_events.py` (default `mode` = dry_run) | Ratings (regex) + conflicts, then the **dry run**: prints TOTAL CHUNKS / AFTER FILTER / LLM CALLS, saves them to `ops.extraction_dry_run` and stops; no LLM calls | no | new |
| **9b** | `55_extract_ratings_lessons_events.py`, section 3 onwards | Set widget `mode` = extract and `APPROVED_CALLS` to the dry-run number, then run: lessons + events via `ai_query`, resumable batches of 100 | LLM endpoint | after your OK |
| **10** | `60_embeddings_vector_index.py` | Embeddings of report + notice chunks (incremental), Vector Search index (widget `create_endpoint`, D4), hybrid check | embedding endpoint, VS | new |
| **11** | `61_rag_evaluation.py` | Eval set (stops after building it: widget `stage` = questions; review, then `stage` = full), 50/50 split, hybrid RAG + verifier, calibrated gate, injection test | LLM + embeddings | new |
| **12** | `70_early_warnings.py` | Early-warning flags, amendment indicator, backtest | no | new |
| **13** | `80_app_setup.py` | Apps limit check (read-only), ops tables, grants for the app's service principal, deployment steps | no | new |
| **14** | `85_kpi_dashboard.py` | KPI views (`dashboards/kpi_views.sql`) + AI/BI dashboard from `dashboards/procurement_kpis.lvdash.json`: create or update, publish, link saved in `ops.config` | no (Lakeview API) | new |

If notebook 12 fails, stop: its output names the failed check and up to 5 examples; the models (50, 51) must not run on
a refresh that failed the gate. The weekly job enforces this order (50, 51 and 40 depend on the gate).

Each notebook ends with a **summary cell**: paste its output back, and the numbers go into `docs/07_evaluation.md`.
Re-running is safe: downloads, parsing, embeddings and LLM extraction skip what's already done.
The weekly refresh job that chains all of these is `jobs/refresh_job.json` (paused; create it only when you decide to).
