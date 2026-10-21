# Databricks notebook source
# MAGIC %md
# MAGIC # 30 — Supplier resolution → `silver.suppliers`, `silver.entity_map` (Stage 3)
# MAGIC
# MAGIC **What this notebook does:** groups the many spellings of each supplier into one canonical supplier, using the
# MAGIC tested rules in `src/entity_resolution/suppliers.py` (docs/02_data_model.md §4):
# MAGIC R1 same DS00005 supplier_id → R2 same cleaned name + country → R3 near-identical name + country (firms only,
# MAGIC stricter when supplier_ids differ). Placeholder names ("INDIVIDUAL CONSULTANT") resolve by supplier_id only.
# MAGIC
# MAGIC **Why it matters:** supplier history ("has this firm been late before?") is only as good as this grouping. A wrong
# MAGIC merge puts someone else's record in front of a user, so the rules favour precision.
# MAGIC
# MAGIC **Measured output:** merges per rule, cluster sizes, and a random sample of 50 R3 merges for **your** precision
# MAGIC review (the Stage 3a check on Stage 2 data was reviewed by the assistant only).

# COMMAND ----------

import os, sys, json, random
from collections import Counter
sys.path.append(os.path.abspath(".."))
import importlib
import src.entity_resolution.suppliers as sup
importlib.reload(sup)

CATALOG = "procurement"
S = f"{CATALOG}.silver"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Distinct supplier records → resolve
# MAGIC One record per distinct (raw name, supplier country code, source supplier_id, procurement method). Method matters:
# MAGIC it decides whether the supplier is an individual.

# COMMAND ----------

rows = (spark.table(f"{S}.contract_awards")
        .select("raw_supplier_name", "supplier_country_code", "supplier_country", "source_supplier_id", "procurement_method")
        .distinct().collect())
# DS01004 has supplier country NAMES but no codes; DS00005 has both. Without a crosswalk, the same firm in the two
# datasets would never match (R2/R3 require the same country). Build name -> code from DS00005's own pairs.
pairs = Counter((r.supplier_country.strip().upper(), r.supplier_country_code) for r in rows
                if r.supplier_country and r.supplier_country_code)
name_to_code = {}
for (name, code), n in pairs.most_common():
    name_to_code.setdefault(name, code)          # most frequent code per country name
def country_of(r):
    if r.supplier_country_code:
        return r.supplier_country_code
    return name_to_code.get((r.supplier_country or "").strip().upper(), (r.supplier_country or "").strip().upper())
unmapped = sorted({(r.supplier_country or "") for r in rows if not r.supplier_country_code
                   and (r.supplier_country or "").strip().upper() not in name_to_code})
print("country names without a code (kept as uppercase names):", unmapped[:30], "...total", len(unmapped))

records = [{"raw_name": r.raw_supplier_name, "country": country_of(r),
            "supplier_id": r.source_supplier_id, "method": r.procurement_method or "",
            "_country_code": r.supplier_country_code, "_country": r.supplier_country} for r in rows]
mapping, merges = sup.resolve(records)
print(f"{len(records):,} distinct supplier records -> {len({m['supplier_key'] for m in mapping}):,} suppliers")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Write `silver.entity_map` (one row per raw record) and `silver.suppliers` (one row per supplier)

# COMMAND ----------

emap = [{"raw_supplier_name": rec["raw_name"], "supplier_country_code": rec["_country_code"], "supplier_country": rec["_country"],
         "source_supplier_id": rec["supplier_id"], "procurement_method": rec["method"] or None,
         "supplier_key": m["supplier_key"], "norm_name": m["norm_name"], "canonical_name": m["canonical_name"],
         "resolved_country": rec["country"], "kind": m["kind"],
         "is_placeholder": m["is_placeholder"], "supplier_name_masked": m["supplier_name_masked"],
         "is_jv_name": m["is_jv_name"], "rule": m["rule"], "score": float(m["score"])}
        for rec, m in zip(records, mapping)]
spark.createDataFrame(emap, "raw_supplier_name string, supplier_country_code string, supplier_country string, "
                      "source_supplier_id string, procurement_method string, supplier_key string, norm_name string, "
                      "canonical_name string, resolved_country string, kind string, is_placeholder boolean, "
                      "supplier_name_masked boolean, is_jv_name boolean, rule string, score double") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{S}.entity_map")

spark.sql(f"""CREATE OR REPLACE TABLE {S}.suppliers AS
  SELECT supplier_key,
         first(canonical_name) AS canonical_name,
         first(resolved_country) AS country,
         first(kind) AS kind,
         max(is_placeholder) AS is_placeholder,
         max(supplier_name_masked) AS supplier_name_masked,  -- history features must be NULL for these
         max(is_jv_name) AS is_jv_name,
         count(*) AS n_raw_records,
         count(DISTINCT raw_supplier_name) AS n_name_variants,
         collect_set(source_supplier_id) AS source_supplier_ids
  FROM {S}.entity_map
  GROUP BY supplier_key""")

# Awards with their resolved supplier (null-safe join on all key columns).
spark.sql(f"""CREATE OR REPLACE TABLE {S}.contract_awards_resolved AS
  SELECT a.*, e.supplier_key, e.kind AS supplier_kind
  FROM {S}.contract_awards a
  LEFT JOIN {S}.entity_map e
    ON  a.raw_supplier_name <=> e.raw_supplier_name
    AND a.supplier_country_code <=> e.supplier_country_code
    AND a.supplier_country <=> e.supplier_country
    AND a.source_supplier_id <=> e.source_supplier_id
    AND a.procurement_method <=> e.procurement_method""")
unresolved = spark.sql(f"SELECT count(*) AS n FROM {S}.contract_awards_resolved WHERE supplier_key IS NULL").first().n
print("awards without a supplier_key:", unresolved)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Measurements and a review sample
# MAGIC **Please review the R3 sample:** for each pair, is it the same supplier? Count yes / no / unsure. That gives the
# MAGIC measured precision of the riskiest rule for the proposal.

# COMMAND ----------

keys_ = Counter(m["supplier_key"] for m in mapping)
r3 = [x for x in merges if x[2] == "R3_fuzzy"]
random.seed(42)
sample = random.sample(r3, min(50, len(r3)))
er_report = {
    "distinct_records": len(records), "suppliers": len(keys_), "awards_without_supplier_key": unresolved,
    "merges_by_rule": dict(Counter(r for _, _, r, _ in merges)),
    "kinds": dict(Counter(m["kind"] for m in mapping)), "placeholders": sum(m["is_placeholder"] for m in mapping),
    "jv_named": sum(m["is_jv_name"] for m in mapping),
    "cluster_size_counts": dict(sorted(Counter(keys_.values()).items())[:12]), "largest_cluster": max(keys_.values()),
}
print(json.dumps(er_report, indent=2))
print(f"\nR3 review sample ({len(sample)} of {len(r3)}):")
for n, (i, j, _, s) in enumerate(sample, 1):
    print(f"{n:2d}. {s:.3f} | {records[i]['raw_name'][:45]:45s} | {records[j]['raw_name'][:45]:45s} | {records[i]['country']}")
# Placeholder profile on the FULL data (requested): Stage 2's sample had every "INDIVIDUAL CONSULTANT" row carrying an
# anonymised 16-hex supplier_id (2,094 rows, 1,924 distinct ids, 0 without id) and 0 masked records.
ph_rows = spark.sql(f"""
  SELECT a.raw_supplier_name, a.supplier_country, a.source_supplier_id, e.norm_name, e.supplier_name_masked
  FROM {S}.contract_awards a JOIN {S}.entity_map e
    ON  a.raw_supplier_name <=> e.raw_supplier_name AND a.supplier_country_code <=> e.supplier_country_code
    AND a.supplier_country <=> e.supplier_country AND a.source_supplier_id <=> e.source_supplier_id
    AND a.procurement_method <=> e.procurement_method
  WHERE e.is_placeholder""").collect()
ic = [r for r in ph_rows if r.norm_name.startswith("INDIVIDUAL")]
placeholder_report = {
    "placeholder_award_rows_by_name": dict(Counter(r.norm_name or "<empty>" for r in ph_rows).most_common()),
    "individual_consultant": {
        "award_rows": len(ic),
        "countries_spanned": len({r.supplier_country for r in ic}),
        "top10_countries": Counter(r.supplier_country for r in ic).most_common(10),
        "with_supplier_id": sum(1 for r in ic if r.source_supplier_id),
        "without_supplier_id": sum(1 for r in ic if not r.source_supplier_id),
        "distinct_ids": len({r.source_supplier_id for r in ic if r.source_supplier_id}),
    },
    "masked_award_rows (placeholder AND no id)": sum(1 for r in ph_rows if r.supplier_name_masked),
}
# Other placeholder candidates the list might be missing: frequent short or generic normalized names.
generic = spark.sql(f"""SELECT norm_name, count(*) AS n FROM {S}.entity_map
                        WHERE NOT is_placeholder AND (length(norm_name) <= 3
                           OR norm_name RLIKE '^(N ?A|NONE|NIL|TBD|TBC|UNKNOWN|VARIOUS|MULTIPLE|OTHERS?|CONSULTANTS?|INDIVIDUALS?|NOT APPLICABLE|NOT AVAILABLE|X+|-+)$')
                        GROUP BY norm_name ORDER BY n DESC LIMIT 20""").collect()
placeholder_report["possible_missed_placeholders_top20"] = [(r.norm_name, r.n) for r in generic]
print(json.dumps(placeholder_report, indent=2, default=str))

print("\nLargest clusters:")
for k, n in keys_.most_common(5):
    print(n, sorted({records[i]['raw_name'] for i, m in enumerate(mapping) if m['supplier_key'] == k})[:6])
