# Databricks notebook source
# MAGIC %md
# MAGIC # 40 — Knowledge graph as Delta tables: `gold.nodes`, `gold.edges` (Stage 3)
# MAGIC
# MAGIC **What this notebook does:** builds the graph defined in `docs/02_data_model.md` §3 from the silver and docs
# MAGIC tables. Every node and edge carries `available_from` (the date the fact became public), which enforces the
# MAGIC leakage rule: a question "as of date T" only sees edges with `available_from <= T`.
# MAGIC
# MAGIC **Why Delta and not a graph database:** our questions are 1–3 hops (contract → project → ICR passage), which
# MAGIC joins handle well, with the same governance as everything else (CLAUDE.md).
# MAGIC
# MAGIC **The key link (assessment M1):** `DESCRIBES` connects a contract to a document chunk *only with evidence*:
# MAGIC same project, document dated after signing, and the passage mentions the contract's reference, its supplier, or
# MAGIC distinctive description words. Each link is scored, with its rule recorded (`src/graph/links.py`, unit-tested).

# COMMAND ----------

import os, sys, json
from collections import Counter, defaultdict
sys.path.append(os.path.abspath(".."))
import importlib
import src.graph.links as links
importlib.reload(links)

CATALOG = "procurement"
S, G, D = (f"{CATALOG}.{x}" for x in ("silver", "gold", "docs"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Nodes
# MAGIC Universe projects and their contracts, suppliers, notices and tenders; all countries and sectors they touch; the
# MAGIC slice documents, sections and chunks. `props` holds type-specific attributes as JSON.

# COMMAND ----------

spark.sql(f"""CREATE OR REPLACE TEMP VIEW u_projects AS SELECT * FROM {S}.projects WHERE in_universe""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW u_contracts AS SELECT * FROM {S}.contracts WHERE in_universe""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW u_notices AS
              SELECT n.* FROM {S}.notices n JOIN u_projects p ON n.project_id = p.project_id""")
spark.sql(f"""CREATE OR REPLACE TEMP VIEW u_awards AS
              SELECT a.* FROM {S}.contract_awards_resolved a JOIN u_contracts c ON a.contract_key = c.contract_key""")

spark.sql(f"""CREATE OR REPLACE TABLE {G}.nodes AS
SELECT concat('country:', iso3) AS node_id, 'Country' AS node_type, name AS label,
       to_json(named_struct('lending_type', lending_type, 'income_level', income_level, 'region', region)) AS props,
       CAST(NULL AS DATE) AS available_from, 'silver.countries' AS source
FROM {S}.countries
UNION ALL
SELECT concat('project:', project_id), 'Project', project_name,
       to_json(named_struct('approval_fy', approval_fy, 'status', status, 'roads_pct', roads_pct,
                            'closing_date', closing_date, 'in_slice', in_slice, 'total_commitment_usd', total_commitment_usd)),
       board_approval_date, 'silver.projects'
FROM u_projects
UNION ALL
SELECT DISTINCT concat('sector:', s.name), 'Sector', s.name, CAST(NULL AS STRING), CAST(NULL AS DATE), 'silver.projects'
FROM u_projects LATERAL VIEW explode(from_json(sectors_json, 'array<struct<name:string,pct:int>>')) t AS s
UNION ALL
SELECT concat('contract:', contract_key), 'Contract', description,
       to_json(named_struct('project_id', project_id, 'category', procurement_category, 'method', procurement_method,
                            'review_type', review_type, 'amount_usd', total_amount_usd, 'fiscal_year', fiscal_year,
                            'reference', borrower_reference, 'source', source_dataset)),
       signing_date, 'silver.contracts'
FROM u_contracts
UNION ALL
SELECT concat('supplier:', s.supplier_key), 'Supplier', s.canonical_name,
       to_json(named_struct('country', s.country, 'kind', s.kind, 'n_name_variants', s.n_name_variants,
                            'is_placeholder', s.is_placeholder)),
       CAST(NULL AS DATE), 'silver.suppliers'
FROM {S}.suppliers s WHERE s.supplier_key IN (SELECT supplier_key FROM u_awards)
UNION ALL
SELECT concat('notice:', notice_id), 'Notice', bid_description,
       to_json(named_struct('type', notice_type, 'status', notice_status, 'reference', bid_reference_no)),
       notice_date, 'silver.notices'
FROM u_notices
UNION ALL
SELECT concat('tender:', tender_key), 'Tender', first(bid_description),
       to_json(named_struct('n_notices', count(*), 'n_ifb', sum(CASE WHEN notice_type = 'Invitation for Bids' THEN 1 ELSE 0 END))),
       min(notice_date), 'silver.notices'
FROM u_notices WHERE tender_key IS NOT NULL GROUP BY tender_key
UNION ALL
SELECT concat('document:', doc_id), 'Document', title,
       to_json(named_struct('doc_type', doc_type, 'project_id', project_id, 'country', country, 'url', pdf_url)),
       doc_date, 'docs.documents'
FROM {D}.documents
UNION ALL
SELECT concat('section:', s.doc_id, '#', sha1(s.section_path)), 'Section', s.section_path,
       to_json(named_struct('doc_id', s.doc_id, 'page_start', s.page_start, 'page_end', s.page_end, 'n_chunks', s.n_chunks)),
       d.doc_date, 'docs.sections'
FROM {D}.sections s JOIN {D}.documents d ON s.doc_id = d.doc_id
UNION ALL
SELECT concat('chunk:', chunk_id), 'Chunk', substr(text, 1, 120),
       to_json(named_struct('doc_id', doc_id, 'chunk_type', chunk_type, 'page_start', page_start, 'n_tokens', n_tokens)),
       doc_date, 'docs.chunks'
FROM {D}.chunks
""")
print(spark.sql(f"SELECT node_type, count(*) n FROM {G}.nodes GROUP BY node_type ORDER BY n DESC").toPandas())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Structured edges

# COMMAND ----------

spark.sql(f"""CREATE OR REPLACE TABLE {G}.edges AS
SELECT concat('project:', project_id) AS src_id, concat('country:', iso3) AS dst_id, 'IN_COUNTRY' AS edge_type,
       CAST(NULL AS DOUBLE) AS weight, 'project_country_code' AS rule, project_id AS evidence,
       board_approval_date AS available_from
FROM u_projects LATERAL VIEW explode(country_iso3_list) t AS iso3
UNION ALL
SELECT concat('project:', project_id), concat('sector:', s.name), 'HAS_SECTOR', CAST(s.pct AS DOUBLE), 'sector_share',
       project_id, board_approval_date
FROM u_projects LATERAL VIEW explode(from_json(sectors_json, 'array<struct<name:string,pct:int>>')) t AS s
UNION ALL
SELECT concat('contract:', contract_key), concat('project:', project_id), 'FOR_PROJECT', CAST(NULL AS DOUBLE), 'project_id',
       contract_key, signing_date
FROM u_contracts
UNION ALL
SELECT concat('notice:', notice_id), concat('project:', project_id), 'FOR_PROJECT', NULL, 'project_id', notice_id, notice_date
FROM u_notices
UNION ALL
SELECT concat('document:', doc_id), concat('project:', project_id), 'FOR_PROJECT', NULL, 'project_id', doc_id, doc_date
FROM {D}.documents WHERE project_id IS NOT NULL
UNION ALL
SELECT concat('contract:', a.contract_key), concat('supplier:', a.supplier_key), 'AWARDED_TO', a.amount_usd, 'entity_map',
       concat(a.contract_key, '|', coalesce(a.raw_supplier_name, '')), c.signing_date
FROM u_awards a JOIN u_contracts c ON a.contract_key = c.contract_key WHERE a.supplier_key IS NOT NULL
UNION ALL
SELECT concat('notice:', notice_id), concat('tender:', tender_key), 'ANNOUNCED', NULL, 'project+ref_key', notice_id, notice_date
FROM u_notices WHERE tender_key IS NOT NULL
UNION ALL
SELECT concat('tender:', t.tender_key), concat('contract:', c.contract_key), 'RESULTED_IN', NULL, 'project+ref_key',
       c.contract_key, c.signing_date
FROM (SELECT DISTINCT tender_key FROM u_notices WHERE tender_key IS NOT NULL) t
JOIN u_contracts c ON t.tender_key = concat(c.project_id, ':', c.ref_key)
UNION ALL
SELECT concat('document:', s.doc_id), concat('section:', s.doc_id, '#', sha1(s.section_path)), 'HAS_SECTION', NULL,
       'parse_structure', s.doc_id, d.doc_date
FROM {D}.sections s JOIN {D}.documents d ON s.doc_id = d.doc_id
UNION ALL
SELECT concat('section:', doc_id, '#', sha1(section_path)), concat('chunk:', chunk_id), 'HAS_CHUNK', NULL,
       'parse_structure', chunk_id, doc_date
FROM {D}.chunks
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. `DESCRIBES` edges: contract ↔ document chunk, scored (slice projects only)
# MAGIC For each slice project: every contract × every chunk of its ISRs / ICR / ICR Review / PPAR / Project Papers.
# MAGIC Chunk text features are computed once per chunk. Supplier names used for evidence are **firm** canonical names
# MAGIC (no placeholders, no individuals).

# COMMAND ----------

slice_ids = [r.project_id for r in spark.table(f"{S}.projects").where("in_slice").select("project_id").collect()]
contracts = defaultdict(list)
for r in spark.sql(f"""SELECT contract_key, project_id, signing_date, ref_key, description FROM u_contracts
                       WHERE project_id IN ({','.join(repr(x) for x in slice_ids)})""").collect():
    contracts[r.project_id].append(r.asDict())
names = defaultdict(list)
for r in spark.sql(f"""SELECT a.contract_key, s.canonical_name FROM u_awards a JOIN {S}.suppliers s ON a.supplier_key = s.supplier_key
                       WHERE s.kind = 'firm' AND NOT s.is_placeholder""").collect():
    names[r.contract_key].append(r.canonical_name)

describes, compared = [], 0
doc_types = tuple(links.LINK_DOC_TYPES)
for pid in slice_ids:
    chunks = [dict(r.asDict(), **links.chunk_features(r.asDict())) for r in spark.sql(
        f"""SELECT chunk_id, project_id, doc_type, doc_date, section_path, text FROM {D}.chunks
            WHERE project_id = '{pid}' AND doc_type IN ({','.join(repr(x) for x in doc_types)})""").collect()]
    for c in contracts.get(pid, []):
        sn = names.get(c["contract_key"], [])
        for ch in chunks:
            compared += 1
            score, rule = links.link_score(c, ch, sn)
            if score:
                describes.append({"src_id": f"chunk:{ch['chunk_id']}", "dst_id": f"contract:{c['contract_key']}",
                                  "edge_type": "DESCRIBES", "weight": score, "rule": rule,
                                  "evidence": ch["chunk_id"], "available_from": ch["doc_date"]})
if describes:
    spark.createDataFrame(describes, "src_id string, dst_id string, edge_type string, weight double, rule string, "
                          "evidence string, available_from date").write.mode("append").saveAsTable(f"{G}.edges")
desc_report = {"pairs_compared": compared, "describes_edges": len(describes),
               "contracts_linked": len({d["dst_id"] for d in describes}),
               "slice_contracts": sum(len(v) for v in contracts.values()),
               "by_rule": dict(Counter(d["rule"] for d in describes).most_common())}
print(json.dumps(desc_report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Integrity checks
# MAGIC Every edge endpoint must exist as a node (no orphans). Counts per type for the checkpoint report.

# COMMAND ----------

orphans = spark.sql(f"""
  SELECT e.edge_type, count(*) AS n FROM {G}.edges e
  LEFT ANTI JOIN {G}.nodes n1 ON e.src_id = n1.node_id
  GROUP BY e.edge_type
  UNION ALL
  SELECT e.edge_type, count(*) FROM {G}.edges e
  LEFT ANTI JOIN {G}.nodes n2 ON e.dst_id = n2.node_id
  GROUP BY e.edge_type""").collect()
dupe_nodes = spark.sql(f"SELECT count(*) - count(DISTINCT node_id) AS d FROM {G}.nodes").first().d
graph_report = {
    "nodes": {r.node_type: r.n for r in spark.sql(f"SELECT node_type, count(*) n FROM {G}.nodes GROUP BY node_type").collect()},
    "edges": {r.edge_type: r.n for r in spark.sql(f"SELECT edge_type, count(*) n FROM {G}.edges GROUP BY edge_type").collect()},
    "orphan_edge_endpoints": {r.edge_type: r.n for r in orphans}, "duplicate_node_ids": dupe_nodes,
    "describes": desc_report,
}
print(json.dumps(graph_report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Worked example: one contract traced to the ICR passage that discusses it
# MAGIC The highest-scoring `DESCRIBES` link: contract → supplier → project → document → section → passage. This is
# MAGIC the assessment's example ("link a supplier contract row to a specific section of an ICR lesson") on real data.

# COMMAND ----------

ex = spark.sql(f"""
  SELECT e.weight, e.rule, e.dst_id AS contract_node, c.label AS contract, c.props AS contract_props,
         c.available_from AS signed, k.section_path, k.page_start, k.doc_type, k.doc_date, substr(k.text, 1, 700) AS passage
  FROM {G}.edges e
  JOIN {G}.nodes c ON e.dst_id = c.node_id
  JOIN {D}.chunks k ON e.src_id = concat('chunk:', k.chunk_id)
  WHERE e.edge_type = 'DESCRIBES'
  ORDER BY e.weight DESC, k.doc_type LIMIT 3""").collect()
for r in ex:
    sups = [x.label for x in spark.sql(f"""SELECT n.label FROM {G}.edges e JOIN {G}.nodes n ON e.dst_id = n.node_id
                                          WHERE e.edge_type = 'AWARDED_TO' AND e.src_id = '{r.contract_node}'""").collect()]
    print(f"score {r.weight} | {r.rule}")
    print(f"  CONTRACT: {r.contract} (signed {r.signed}) {r.contract_props}")
    print(f"  SUPPLIER(S): {sups}")
    print(f"  PASSAGE: {r.doc_type} {r.doc_date}, section '{r.section_path}', p.{r.page_start}")
    print(f"  {r.passage}")
    print()
