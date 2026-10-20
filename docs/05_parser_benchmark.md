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

## Manual check of the side-by-side sample (India ICR, notebook cell 6) [M]

- **Tables:** `ai_parse_document`'s first table is the currency table, correctly split into cells
  (`Currency Unit = | Indian Rupee (Rs.)`, `Rs. 84 = | US$1`). pdfplumber's first "table" is the **cover page dumped into
  one cell** with empty cells around it, not a real table.
- **Headings:** `ai_parse_document` returns clean structural headings (DATA SHEET, BASIC DATA, KEY DATES, RATINGS
  SUMMARY, ISR RATINGS…). pdfplumber's heuristic returns **cover-page lines** ("Loan Number 8940-IN", "December 23, 2024")
  and **table-of-contents lines** ("IV. BANK PERFORMANCE … 14").
- **So pdfplumber's heading recall is inflated:** some of its "found" sections are TOC entries, not real headings. The
  tie on recall flatters pdfplumber, which strengthens the decision.

## Repeatability [M]

A second run of the same cell gave slightly different counts for the same PDFs: China `tables_well_formed` 28 → 27,
`page_header` 15 → 17; Sri Lanka `page_header` 80 → 79; total time 91.0 s → 93.3 s. **`ai_parse_document` output is not
fully deterministic** [M]. Consequence [P]: parse each document **once**, store the result in Delta, and chunk from that
stored copy, so citations always point to the same text.

## Caveats

- **3 documents only.** These results show a direction, not a guarantee across all document types (PADs, ISRs).
- The table check is automatic plus one manual sample; cell-by-cell correctness was not checked for every table.
- Both parsers missed one of the five standard sections in the India ICR. Which one is not yet known [A: likely a
  heading phrased differently]; to check when chunking in Stage 3.
- `ai_parse_document` uses Free Edition AI-function quota; the limit has not been measured.
