# Stage 2 verification scripts

Throwaway, read-only scripts that produced the numbers in `docs/04_data_sources.md`. They run locally (not in Databricks)
and write raw responses to a `data/` folder next to them. That folder is not committed.

Run order, from inside this folder:

1. `python pull_projects.py data/projects_all.json`: all projects (Projects API)
2. `python pull_docfacets.py`: document-type counts per FY2014–21 road project
3. `python pull_contract_counts.py`: DS00005 rows per road project + country lending types
4. `python pull_notices.py`: procurement notices per road project
5. `python pull_docdates.py`: PAD/ISR/ICR/ICRR/PPAR dates per road project
6. `python select_countries.py`: demo-country ranking
7. `python hyp.py` and `python hyp2.py`: hypothesis tests H1–H4

`http_util.py` is the polite HTTP client: explicit User-Agent, pause after every call, exponential backoff that honours
HTTP 429 `Retry-After`. The Stage 3 ingestion code will reuse the same pattern.
