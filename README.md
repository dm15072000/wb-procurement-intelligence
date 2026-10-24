# Procurement Intelligence on Databricks

Evidence-backed planning, tender design and monitoring for World Bank procurement, built on World Bank open data
inside one Databricks workspace (Delta Lake, Unity Catalog, Foundation Model APIs, Vector Search, MLflow, Workflows,
a Databricks App and an AI/BI dashboard). Built for the AI-Powered Procurement Intelligence Platform technical
assessment (`assessment.md`).

**Start here:** [docs/00_submission_summary.md](docs/00_submission_summary.md) (one page) ·
[docs/08_proposal.md](docs/08_proposal.md) (technical proposal) · [docs/07_evaluation.md](docs/07_evaluation.md)
(measured results) · [docs/12_final_audit.md](docs/12_final_audit.md) (requirement-by-requirement audit) ·
[slides/procurement_intelligence.pptx](slides/procurement_intelligence.pptx) (deck, also as PDF).

## Repository layout

| Folder | What is in it |
|---|---|
| `notebooks/` | Databricks notebooks (`.py` source format), one per pipeline step; run order in `notebooks/README.md` |
| `src/` | Pure-Python logic used by the notebooks and the app (ingestion, quality checks, entity resolution, graph, models, risk, RAG, guardrails, evaluation) |
| `tests/` | Unit tests for `src/` (run locally with pytest, no Databricks needed) |
| `app/`, `app.yaml` | The Databricks App (Streamlit): Plan a package · Monitor a project · Ask the evidence · Data coverage |
| `dashboards/` | KPI dashboard as code: SQL views + Lakeview JSON builder (deployed by notebook 85) |
| `jobs/` | The weekly refresh job definition (`refresh_job.json`) |
| `slides/` | Deck builder (`build_deck.js`), diagrams and the generated deck |
| `docs/` | Plan, data sources, data model, design, evaluation, proposal, audit, interview Q&A |

## Setup

1. **Databricks workspace** with Unity Catalog, serverless compute, a SQL warehouse, Vector Search and the Foundation
   Model endpoints `databricks-meta-llama-3-3-70b-instruct` and `databricks-gte-large-en` (checked by notebook 00).
2. **Git folder:** add this repository as a Databricks Git folder (Workspace → Create → Git folder).
3. **Catalog:** notebooks create catalog `procurement` with schemas `bronze`, `silver`, `gold`, `docs`, `ml`, `ops`
   and the volume `procurement.bronze.raw_files`. No credentials are stored in code: notebooks use the workspace
   identity; the app uses its own service principal.
4. **Local tests** (optional): `pip install pytest scikit-learn scipy pandas numpy` then `python -m pytest tests`.

## Run order (details and status per notebook: `notebooks/README.md`)

| Step | Notebooks | Notes |
|---|---|---|
| Ingest and clean | `10` → `11` → `13` → `30` → `12` | public APIs with retries and backoff; `12` is the data-quality gate (stops on failure) |
| Documents | `20` → `21` → `22` → `40` | parse once with `ai_parse_document`; `22` translates French passages for embedding only (dry run by default) |
| Models | `50` → `51` | duration model (MLflow, champion alias); re-bid or cancellation rates |
| Extraction | `55` | dry run first; LLM extraction only with an approved call count |
| Retrieval | `60` → `61` | embeddings + Vector Search index (incremental); evaluation (`stage = full`) |
| Monitoring and app | `70` → `80` → `85` | early warnings; app permissions; KPI dashboard |
| Weekly loop | `jobs/refresh_job.json` | the same steps as one job, incremental, created paused |

**App:** deploy with the repo root as source (`app.yaml`), add the SQL warehouse as a resource, then run notebook 80
for permissions. **Dashboard:** notebook 85 creates the views, deploys and publishes it, and saves the link for the app.
