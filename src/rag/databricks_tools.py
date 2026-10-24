"""Databricks implementations of the RAG tools (used by notebook 61 and by the app).

Plain-language summary: src/rag/pipeline.py only knows "a function that searches", "a function that runs SQL", and
so on. This file supplies the real ones on Databricks: SQL over Unity Catalog tables, the Foundation Model endpoints
for embeddings and chat, Vector Search (or the cosine fallback), the registered duration model and the re-bid or cancellation rates.
The SQL runner is passed in, so the same code works with spark.sql in a notebook and with the SQL connector in the
app. Values that end up inside SQL text (project IDs, contract keys) are validated against strict patterns first.
"""
import math
import re
from datetime import date

from src.models import duration as du
from src.rag import retrieval as rt

CATALOG = "procurement"
LLM_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"
EMBED_ENDPOINT = "databricks-gte-large-en"
VS_ENDPOINT = "procurement-vs"
VS_INDEX = f"{CATALOG}.docs.chunk_index"
DURATION_MODEL = f"models:/{CATALOG}.ml.tender_duration@champion"
_PID = re.compile(r"^P\d{6}$")
_CKEY = re.compile(r"^C:[A-Za-z0-9\-_/.]+$")
_ISO3 = re.compile(r"^[A-Z]{3}$")
# outside the f-string below: Python 3.11 (the Databricks App runtime) rejects a backslash inside an f-string expression
_CNODE = re.compile(r"^contract:C:[A-Za-z0-9\-_/.]+$")


def _in_list(values, pattern):
    vals = [v for v in values if isinstance(v, str) and pattern.match(v)]
    return ", ".join(f"'{v}'" for v in sorted(set(vals))) or "NULL"


def _fmt_usd(x):
    return f"USD {x:,.0f}" if x is not None else "amount not recorded"


# Vector Search scores a plain (ANN) query as 1 / (1 + L2 distance squared) on our unnormalised vectors (measured
# 2026-10-23: equal to the exact value to 6 decimals). Every stored gte-large-en vector has norm ~24.0 (min 23.40,
# max 24.38, sd 0.08, 9,564 passages), so the score converts back to cosine similarity with an error below ~0.01.
CHUNK_NORM = 24.0


def l2_score_to_cosine(score, query_norm, chunk_norm=CHUNK_NORM):
    """Cosine similarity from a Vector Search L2 score: d^2 = 1/score - 1, cos = (|q|^2 + |c|^2 - d^2) / (2|q||c|)."""
    if score is None or score <= 0 or not query_norm:
        return None
    d2 = 1.0 / float(score) - 1.0
    return round((query_norm ** 2 + chunk_norm ** 2 - d2) / (2 * query_norm * chunk_norm), 4)


def attach_vector_scores(hybrid_hits, vector_hits):
    """The retrieval gate must use PURE vector similarity (2026-10-22): a HYBRID score mixes in keyword matching and was
    close to 1.0 for every question, so the gate never refused anything. A plain vector query over the same scope gives
    each hit its raw similarity (vector_score), and every hit also carries the best raw similarity of the scope
    (vector_top), even when that passage did not make the hybrid top k."""
    vs = {h["chunk_id"]: h.get("score") for h in vector_hits}
    top = max((s for s in vs.values() if s is not None), default=None)
    return [dict(h, vector_score=vs.get(h["chunk_id"]), vector_top=top) for h in hybrid_hits]


class DatabricksBackend:
    """sql: callable(query, params) -> list[dict]. workspace_client: databricks.sdk.WorkspaceClient.
    vs_index: a databricks.vector_search index object, or None to use the cosine fallback over fallback_rows
    (rows of docs.chunk_embeddings with their embedding). duration_model: an mlflow pyfunc returning p20/p50/p80."""

    def __init__(self, sql, workspace_client, vs_index=None, fallback_rows=None, duration_model=None,
                 duration_model_info=None):
        self.sql, self.w, self.vs_index = sql, workspace_client, vs_index
        self.fallback_rows = fallback_rows
        self.duration_model, self.duration_model_info = duration_model, duration_model_info or {}
        # Hybrid search (checkpoint d change 2): try Vector Search HYBRID mode once; if the index refuses it, use
        # ANN + our own keyword index. None = not tried yet.
        self.hybrid_supported = None
        self._keyword_index = None
        self._en_keyword_index = None
        self._project_names = None         # road-project names for name -> ID matching
        self._country_words = set()      # English-aware keyword index (translated passages), built on first use

    # --- Foundation Model endpoints -------------------------------------------------------------------------------
    def embed(self, text):
        resp = self.w.serving_endpoints.query(name=EMBED_ENDPOINT, input=[text[:8000]])
        return list(resp.data[0].embedding)

    def chat(self, messages, max_tokens=1500):
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
        msgs = [ChatMessage(role=ChatMessageRole(m["role"]), content=m["content"]) for m in messages]
        resp = self.w.serving_endpoints.query(name=LLM_ENDPOINT, messages=msgs, temperature=0.0, max_tokens=max_tokens)
        return resp.choices[0].message.content

    # --- retrieval ------------------------------------------------------------------------------------------------
    def _vs(self, vec, filters, k, text=None, hybrid=False):
        kwargs = dict(query_vector=vec, columns=rt.COLUMNS, filters=filters or None, num_results=k)
        if hybrid:
            # Self-managed embeddings: hybrid needs BOTH the vector and the text (Databricks docs, checked 2026-10-21).
            kwargs.update(query_text=text, query_type="HYBRID")
        res = self.vs_index.similarity_search(**kwargs)
        cols = [c["name"] for c in res["manifest"]["columns"]]
        hits = [dict(zip(cols, row), hybrid=hybrid) for row in res.get("result", {}).get("data_array", []) or []]
        if not hybrid:      # report cosine similarity, the same scale as the cosine fallback; keep the raw score
            qn = math.sqrt(sum(x * x for x in vec))
            hits = [dict(h, vs_l2_score=h.get("score"), score=l2_score_to_cosine(h.get("score"), qn)) for h in hits]
        return hits

    def search(self, vec, filters, k, text=None):
        """Vector Search (HYBRID when the index supports it and a text is given), else the cosine fallback."""
        if self.vs_index is None:
            return rt.cosine_search(vec, self.fallback_rows or [], filters, k)
        if text and self.hybrid_supported is not False:
            try:
                hits = self._vs(vec, filters, k, text, hybrid=True)
                self.hybrid_supported = True
            except Exception:
                self.hybrid_supported = False      # remembered: keyword_search takes over the keyword side
            else:
                return attach_vector_scores(hits, self._vs(vec, filters, k))
        return self._vs(vec, filters, k)

    def needs_keyword_fallback(self):
        """True when keyword matching must come from our own index (no Vector Search, or HYBRID not supported)."""
        return self.vs_index is None or self.hybrid_supported is False

    def keyword_search(self, query, filters, k):
        """Keyword side of hybrid retrieval (BM25 + whole-code identifiers) over the indexed chunk text. Returns []
        when Vector Search HYBRID mode already does keyword matching."""
        if not self.needs_keyword_fallback():
            return []
        if self._keyword_index is None:
            from src.rag.keyword import KeywordIndex
            rows = self.fallback_rows or self.sql(
                f"SELECT {', '.join(rt.COLUMNS)}, doc_date_int, is_primary FROM {CATALOG}.docs.chunk_embeddings", {})
            self._keyword_index = KeywordIndex(rows)
        return self._keyword_index.search(query, filters, k)

    def translated_keyword_search(self, query, filters, k):
        """English keyword matching that also reaches the translated (French) passages (2026-10-23): BM25 over ALL
        indexed passages, using the English translation of the 196 translated ones (docs.chunk_translations, same
        text_hash) and the original text of the rest. Hits are ranked against the whole corpus; translated ones are
        flagged and carry their ORIGINAL text, which is what is shown, cited and quote-checked."""
        if self._en_keyword_index is None:
            from src.rag.keyword import KeywordIndex
            cols = ", ".join(f"e.{c}" for c in rt.COLUMNS)
            rows = self.sql(f"""
                SELECT {cols}, e.doc_date_int, e.is_primary, t.translated_text
                FROM {CATALOG}.docs.chunk_embeddings e
                LEFT JOIN {CATALOG}.docs.chunk_translations t ON t.chunk_id = e.chunk_id AND t.text_hash = e.text_hash""",
                            {})
            for r in rows:
                r["original_text"], r["translated"] = r["text"], bool(r.get("translated_text"))
                r["text"] = r.pop("translated_text") or r["text"]
            self._en_keyword_index = KeywordIndex(rows)
        hits = self._en_keyword_index.search(query, filters, k)
        return [dict(h, text=h["original_text"]) for h in hits]

    def resolve_projects(self, question):
        """Project IDs named (by name, not ID) in a question, matched against the road-project universe."""
        from src.rag import query as qy
        if self._project_names is None:
            self._project_names = self.sql(f"""SELECT project_id, project_name, in_slice FROM {CATALOG}.silver.projects
                                               WHERE in_universe""", {})
            self._country_words = {w for r in self.sql(f"SELECT name FROM {CATALOG}.silver.countries", {})
                                   for w in qy._words(r["name"])}
        return qy.resolve_project_names(question, self._project_names, exclude=self._country_words)

    def rating_rows(self, project_ids):
        """Extracted ratings (gold.ratings) of these projects, oldest first."""
        ids = [p for p in project_ids if _PID.match(p or "")]
        if not ids:
            return []
        return self.sql(f"""SELECT project_id, dimension, value, score, doc_type, doc_date, chunk_id
                            FROM {CATALOG}.gold.ratings WHERE project_id IN ({_in_list(ids, _PID)})
                            ORDER BY project_id, dimension, doc_date""", {})

    def translate_to_french(self, question):
        """The question in French (1 LLM call), or None if the reply is unusable."""
        from src.rag import multilingual as ml
        try:
            return ml.clean_translation(self.chat(ml.translate_messages(question), max_tokens=300), question)
        except Exception:
            return None

    def describes_hits(self, contract_keys):
        """Passages that a strong DESCRIBES link says mention one of these contracts. Score = link weight."""
        if not contract_keys:
            return []
        rows = self.sql(f"""
            SELECT e.weight AS score, e.evidence AS link_evidence, k.chunk_id, k.doc_id, k.project_id, k.country_iso3,
                   k.doc_type, k.doc_date, k.section_path, k.text, k.title, k.pdf_url, k.page_start, k.chunk_type
            FROM {CATALOG}.gold.edges e JOIN {CATALOG}.docs.chunk_embeddings k ON e.src_id = concat('chunk:', k.chunk_id)
            WHERE e.edge_type = 'DESCRIBES'
              AND e.dst_id IN ({_in_list(['contract:' + c for c in contract_keys if _CKEY.match(c)], _CNODE)})""", {})
        return rows

    # --- structured facts -----------------------------------------------------------------------------------------
    def similar_contracts(self, pkg, limit=8):
        """Past contracts in road projects: same category (and method if given), signed before as-of, value within
        0.3-3x when a value is given; same country first, then nearest value. Each fact carries dataset + coverage."""
        where = ["c.in_universe", "c.signing_date <= :as_of", "c.procurement_category = :category"]
        params = {"as_of": str(pkg.get("as_of") or date.today()), "category": pkg.get("category") or ""}
        if pkg.get("method"):
            where.append("c.procurement_method = :method")
            params["method"] = pkg["method"]
        order = "CASE WHEN c.borrower_country_iso3 = :iso3 THEN 0 ELSE 1 END"
        params["iso3"] = pkg.get("country_iso3") or ""
        if pkg.get("amount_usd"):
            where.append("c.total_amount_usd BETWEEN :lo AND :hi")
            params.update(lo=0.3 * pkg["amount_usd"], hi=3 * pkg["amount_usd"], amt=float(pkg["amount_usd"]))
            order += ", abs(log10(c.total_amount_usd) - log10(:amt))"
        rows = self.sql(f"""
            SELECT c.contract_key, c.project_id, c.description, c.signing_date, c.procurement_category,
                   c.procurement_method, c.review_type, c.coverage_status, c.total_amount_usd,
                   c.borrower_country_iso3, c.source_dataset, d.duration_days
            FROM {CATALOG}.silver.contracts c
            LEFT JOIN {CATALOG}.silver.tender_durations d ON c.contract_key = d.contract_key
            WHERE {' AND '.join(where)}
            ORDER BY {order}, c.signing_date DESC
            LIMIT {int(limit)}""", params)
        facts = []
        for r in rows:
            dur = f"; notice-to-signing {r['duration_days']} days" if r.get("duration_days") is not None else \
                "; tender duration not matched"
            facts.append({"kind": "similar_contract", "contract_key": r["contract_key"], "project_id": r["project_id"],
                          "coverage_status": r["coverage_status"], "source_dataset": r["source_dataset"],
                          "text": (f"Past contract {r['contract_key']} in project {r['project_id']} "
                                   f"({r['borrower_country_iso3']}): {r['procurement_category']}, "
                                   f"{r['procurement_method']}, {r['review_type']} review, "
                                   f"{_fmt_usd(r['total_amount_usd'])}, signed {r['signing_date']}{dur}. "
                                   f"Description: {(r['description'] or '')[:160]}. "
                                   f"[source {r['source_dataset']}; coverage {r['coverage_status']}]")})
        return facts + self.method_options(pkg)

    def method_options(self, pkg, top=4):
        """Market-approach options (planning, assessment §2): how past road-project contracts of the same category and
        value band (0.3-3x) were procured, by method and market approach, with the median notice-to-signing time where
        a duration is matched. Descriptive history, not a recommendation model."""
        if not pkg.get("category"):
            return []
        where = ["c.in_universe", "c.signing_date <= :as_of", "c.procurement_category = :category",
                 "c.procurement_method IS NOT NULL"]
        params = {"as_of": str(pkg.get("as_of") or date.today()), "category": pkg["category"]}
        if pkg.get("amount_usd"):
            where.append("c.total_amount_usd BETWEEN :lo AND :hi")
            params.update(lo=0.3 * pkg["amount_usd"], hi=3 * pkg["amount_usd"])
        rows = self.sql(f"""
            WITH ifb AS (   -- market approach stated in the tender's invitation for bids (STEP awards rarely carry it)
              SELECT tender_key,
                     max(CASE WHEN lower(notice_text) RLIKE 'international (competitive|open)|open international'
                              THEN 1 ELSE 0 END) AS intl,
                     max(CASE WHEN lower(notice_text) RLIKE 'national (competitive|open)|open national'
                              THEN 1 ELSE 0 END) AS natl
              FROM {CATALOG}.silver.notices WHERE notice_type = 'Invitation for Bids' GROUP BY tender_key)
            SELECT c.procurement_method AS method,
                   coalesce(c.market_approach,
                            CASE WHEN i.intl = 1 AND i.natl = 0 THEN 'international (from notice)'
                                 WHEN i.natl = 1 AND i.intl = 0 THEN 'national (from notice)' END,
                            'not recorded') AS market,
                   count(*) AS n, count(d.duration_days) AS n_dur,
                   percentile(d.duration_days, 0.5) AS median_days
            FROM {CATALOG}.silver.contracts c
            LEFT JOIN {CATALOG}.silver.tender_durations d ON c.contract_key = d.contract_key
            LEFT JOIN ifb i ON d.tender_key = i.tender_key
            WHERE {' AND '.join(where)}
            GROUP BY 1, 2 ORDER BY n DESC""", params)
        total = sum(int(r["n"]) for r in rows)
        facts = []
        for r in rows[:top]:
            dur = (f", median notice-to-signing {int(round(float(r['median_days'])))} days (n={r['n_dur']})"
                   if r.get("median_days") is not None else ", no matched durations")
            facts.append({"kind": "method_option", "method": r["method"], "market": r["market"], "n": int(r["n"]),
                          "share": round(int(r["n"]) / total, 3) if total else None,
                          "median_days": r.get("median_days"),
                          "text": (f"Past {pkg['category']} contracts in road projects of similar value: "
                                   f"{r['method']}, market approach {r['market']}, was used for {r['n']} of {total}"
                                   f"{dur}. Descriptive history of what was used, not a recommendation.")})
        return facts

    def _context(self, pkg):
        """Project, country and governance data needed to build the duration-model features for a new package."""
        iso3 = pkg.get("country_iso3") if _ISO3.match(pkg.get("country_iso3") or "") else None
        project = {}
        if pkg.get("project_id") and _PID.match(pkg["project_id"]):
            rows = self.sql(f"SELECT board_approval_date, roads_pct, total_commitment_usd FROM {CATALOG}.silver.projects "
                            f"WHERE project_id = :pid", {"pid": pkg["project_id"]})
            project = rows[0] if rows else {}
        lending = self.sql(f"SELECT lending_type FROM {CATALOG}.silver.countries WHERE iso3 = :iso3",
                           {"iso3": iso3 or ""})
        gov = self.sql(f"SELECT iso3, year, indicator, value FROM {CATALOG}.silver.governance WHERE iso3 = :iso3",
                       {"iso3": iso3 or ""})
        earlier = []
        if project:
            earlier = sorted(du.as_date(r["d"]) for r in self.sql(
                f"""SELECT min(notice_date) AS d FROM {CATALOG}.silver.notices WHERE project_id = :pid
                    AND notice_type IN ('Invitation for Bids', 'Request for Expression of Interest')
                    AND tender_key IS NOT NULL GROUP BY tender_key""", {"pid": pkg["project_id"]}) if r["d"])
        return iso3, project, (lending[0]["lending_type"] if lending else None), du.governance_lookup(gov), earlier

    def predictions(self, pkg):
        """Model facts: duration P20/P50/P80 and the re-bid or cancellation rate. Numbers come from models, never from the LLM."""
        facts = []
        as_of = du.as_date(pkg.get("as_of") or date.today())
        if self.duration_model is not None and pkg.get("category"):
            import pandas as pd
            iso3, project, lending, gov, earlier = self._context(pkg)
            notice_type = "Request for Expression of Interest" if "consult" in pkg["category"].lower() \
                else "Invitation for Bids"
            row = du.feature_row({"contract_key": "new", "start_notice_date": as_of, "duration_days": None},
                                 {"project_id": pkg.get("project_id"), "procurement_category": pkg["category"],
                                  "procurement_method": pkg.get("method"), "borrower_country_iso3": iso3,
                                  "total_amount_usd": pkg.get("amount_usd")},
                                 project, notice_type, earlier, gov, lending)
            pred = self.duration_model.predict(pd.DataFrame([row])[du.CATEGORICAL + du.NUMERIC]).iloc[0]
            p20, p50, p80 = (int(round(pred[c])) for c in ("p20", "p50", "p80"))
            info = self.duration_model_info
            facts.append({"kind": "duration_model", "p20": p20, "p50": p50, "p80": p80,
                          "text": (f"Duration model ({info.get('population', 'prior-review')} contracts, trained on "
                                   f"{info.get('n_train', '?')} tenders, test MAE {info.get('mae_p50', '?')} days): "
                                   f"predicted notice-to-signing time P20 {p20} days, P50 {p50} days, P80 {p80} days. "
                                   f"Mainly valid for prior-review contracts.")})
        rates = self.sql(f"""SELECT level, rate, positives, n, ci_low, ci_high FROM {CATALOG}.gold.rebid_rates
                             WHERE (country_iso3 = :iso3 OR country_iso3 IS NULL)
                               AND (procurement_category = :grp OR procurement_category IS NULL)
                               AND (procurement_method = :method OR procurement_method IS NULL)
                             ORDER BY depth DESC LIMIT 1""",
                         {"iso3": pkg.get("country_iso3") or "", "grp": pkg.get("category") or "",
                          "method": pkg.get("method") or ""})
        if rates:
            r = rates[0]
            facts.append({"kind": "rebid_rate", "rate": r["rate"],
                          "text": (f"Re-bid or cancellation history: {r['positives']} of {r['n']} similar tenders were "
                                   f"re-bid or cancelled "
                                   f"(smoothed rate {100 * r['rate']:.0f}%, 90% interval {100 * r['ci_low']:.0f}% to "
                                   f"{100 * r['ci_high']:.0f}%; compared within {r['level']}).")})
        return facts

    def related_projects(self, pkg, facts):
        """Slice projects that have documents: those of the similar contracts, plus the package country's."""
        ids = [f["project_id"] for f in facts if f.get("project_id")]
        rows = self.sql(f"""
            SELECT DISTINCT d.project_id FROM {CATALOG}.docs.documents d
            WHERE d.project_id IN ({_in_list(ids, _PID)})
               OR d.project_id IN (SELECT project_id FROM {CATALOG}.silver.projects
                                   WHERE in_slice AND array_contains(country_iso3_list, :iso3))""",
                        {"iso3": pkg.get("country_iso3") or ""})
        return [r["project_id"] for r in rows if r.get("project_id")]


def fallback_rows_from(rows):
    """Rows of docs.chunk_embeddings (as dicts) ready for the cosine fallback."""
    out = []
    for r in rows:
        r = dict(r)
        r["embedding"] = [float(x) for x in r["embedding"]]
        out.append(r)
    return out


def spark_sql_runner(spark):
    """SQL runner for notebooks: spark.sql with named parameters (:name)."""
    def run(query, params):
        return [r.asDict() for r in spark.sql(query, args=params or None).collect()]
    return run

