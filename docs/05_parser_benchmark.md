# 05 — PDF Parser Benchmark (decision D6)

Run by the user on 2026-10-20 with `notebooks/01_parser_benchmark.py` on Databricks Free Edition (serverless).
Three ICRs from the demo slice: India P147864 (41 pages), Sri Lanka P132833 (57 pages), China P129401 (41 pages).
All numbers are **[M] measured** unless labelled otherwise.

## Results

| Criterion | `ai_parse_document` | `pdfplumber` (+ `pypdf` text) |
|---|---|---|
| **Tables: well-formed / detected** | **117 / 124 (94%)**: IND 41/41, LKA 48/55, CHN 28/28 | **28 / 108 (26%)**: IND 9/44, LKA 12/46, CHN 7/18 |
| Key Dates table intact (Approval / Effectiveness / Closing in one table) | 3 / 3 | 3 / 3 |
| **Headings: recall of the 5 standard ICR sections** | IND 0.8, LKA 1.0, CHN 1.0 (mean **0.93**) | IND 0.8, LKA 1.0, CHN 1.0 (mean **0.93**) |
| Heading source | Native element types (`title`, `section_header`) | Inferred from font size / boldness (heuristic) |
| **Speed** | 91.0 s for 139 pages, about **0.65 s/page** | pdfplumber 19.2 s + pypdf 7.4 s, about **0.19 s/page** |
| Extra structure | Also tags `page_header`, `page_number`, `footnote`, `figure`, `caption`, so boilerplate can be dropped before chunking | None; headers and footers would need custom rules |
| Errors | none (`error_status` null for all 3) | none |

"Well-formed" = at least 2 rows, at least 2 columns and under half the cells empty (an automatic proxy, defined in the notebook).

## Decision

**Use `ai_parse_document`** [P, matches the rule in D6: use it if it works on Free Edition].

- **Table fidelity decides it:** 94% vs 26% well-formed tables. ICR tables (ratings, costs, key dates) carry many of the
  facts our citations rely on, so shredded tables would weaken grounding (M3).
- **Headings are a tie** on recall. But `ai_parse_document` labels headings natively, while pdfplumber depends on a font
  heuristic that may break on other layouts [A].
- **Speed is the cost:** about 3.5× slower. For the slice, that's minutes, not hours. At full-portfolio scale it's a
  cost and quota question for the proposal [P].
- **Fallback:** `pypdf` plain text, kept for any document where `ai_parse_document` returns an error.

## Caveats

- **3 documents only.** These results show a direction, not a guarantee across all document types (PADs, ISRs).
- **The table check is automatic, not a check of cell correctness.** The side-by-side sample (notebook cell 6) has not
  been reviewed yet. To do: compare one table per parser against the PDF by eye.
- Both parsers missed one of the five standard sections in the India ICR. Which one is not yet known [A: likely a
  heading phrased differently]; to check when chunking in Stage 3.
- `ai_parse_document` uses Free Edition AI-function quota; the limit has not been measured.
