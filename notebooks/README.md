# Notebooks: run order

Sync the repo to a Databricks Git folder first. Every notebook imports tested code from `src/`.

| Order | Notebook | What it does | Calls external APIs? | Uses `ai_parse_document`? |
|---|---|---|---|---|
| done | `00_verify_workspace.py` | Read-only checks of workspace features | no | no |
| done | `01_parser_benchmark.py` | Parser benchmark on 3 ICRs (D6) | yes (3 PDFs) | yes (3 docs) |
| done | `02_procplan_test.py` | Procurement Plan parse test; stores plan parses | yes | yes (stored) |
| 1 | `10_ingest_structured.py` | All structured sources → raw JSONL → bronze tables (resumable) | yes, ~20–40 min | no |
| 2 | `11_silver_structured.py` | Bronze → silver; tender durations; FY activity with the gap as NULL; quality checks | no | no |
| 3 | `20_ingest_documents.py` | Slice documents → PDFs → parse once → section chunks; plan labels (no dates) | yes | yes (resumable batches) |
| 4 | `30_entity_resolution.py` | Supplier resolution; prints a 50-pair sample for your review | no | no |
| 5 | `40_build_graph.py` | `gold.nodes` / `gold.edges`, scored contract↔passage links, integrity checks, worked example | no | no |

Each notebook ends with a **summary cell**: paste its output back for the checkpoint (c) report.
Re-running is safe: downloads and parsing skip what's already done.
