"""Unit tests for src/ingest, src/chunking, src/graph. Record shapes copied from verified Stage 2 API responses."""
import io
import json
import urllib.error
from datetime import date

import pytest

from src.chunking.sections import chunk_document, elements_from_parse, heading_level
from src.graph.links import link_score
from src.ingest import http, transform

# --- http ------------------------------------------------------------------------------------------------------------

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, retry_after=None):
    hdrs = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("u", code, "err", hdrs, None)


def test_backoff_doubles_and_honours_retry_after():
    assert [http.backoff_wait(a, base=5) for a in range(4)] == [5, 10, 20, 40]
    assert http.backoff_wait(0, retry_after="43") == 43.0
    assert http.backoff_wait(20, base=5) == 300.0  # capped


def test_get_retries_429_then_succeeds_and_sends_user_agent():
    calls, sleeps = [], []

    def opener(req, timeout):
        calls.append(req.get_header("User-agent"))
        if len(calls) == 1:
            raise _http_error(429, "43")
        return _Resp(b'{"ok": 1}')

    assert http.get("https://x", opener=opener, sleep=sleeps.append) == {"ok": 1}
    assert calls[0] == http.UA and len(calls) == 2
    assert 43 <= sleeps[0] <= 44 and sleeps[-1] == 1.5  # waited Retry-After, then the polite pause


def test_get_does_not_retry_403():
    def opener(req, timeout):
        raise _http_error(403)
    with pytest.raises(urllib.error.HTTPError):
        http.get("https://x", opener=opener, sleep=lambda s: None)


def test_get_gives_up_after_max_tries():
    def opener(req, timeout):
        raise _http_error(503)
    with pytest.raises(RuntimeError):
        http.get("https://x", opener=opener, sleep=lambda s: None, max_tries=3)


# --- transforms ------------------------------------------------------------------------------------------------------

PROJECT = {"id": "P124639", "project_name": "PMGSY Rural Roads Project", "countrycode": ["IN"], "approvalfy": "2011",
           "boardapprovaldate": "2010-12-20T00:00:00Z", "closingdate": "12/15/2020 12:00:00 AM", "status": "Closed",
           "sector1": {"Name": "Rural and Inter-Urban Roads", "Percent": 96},
           "sector2": {"Name": "Public Administration - Transportation", "Percent": 4}, "sector3": "",
           "idacommamt": "1,000,000,000", "ibrdcommamt": "500,000,000", "totalcommamt": "1,500,000,000"}


def test_project_row_and_universe():
    r = transform.project_row(PROJECT)
    assert r["roads_pct"] == 96 and r["approval_fy"] == 2011 and r["closing_date"] == date(2020, 12, 15)
    assert r["ida_commitment_usd"] == 1e9 and not r["is_multi_country"]
    assert json.loads(r["sectors_json"])[0] == {"name": "Rural and Inter-Urban Roads", "pct": 96}  # read by SQL from_json
    assert not transform.in_universe(r)                       # FY2011 is outside FY2014-FY2021
    assert transform.in_universe(dict(r, approval_fy=2016))
    assert not transform.in_universe(dict(r, approval_fy=2016, roads_pct=20))
    assert transform.project_row({"id": "bad"}) is None


AWARD = {"borrower_contract_reference_number": "UDP PPG-QCBS-2016-4-12", "contract_description": "Technical Supervision",
         "contract_signing_date": "19-May-2022", "fiscal_year": 2022, "procurement_category": "Consultant Services",
         "procurement_method": "Quality And Cost-Based Selection", "project_id": "P151416", "review_type": "Prior",
         "supplier_contract_amount_usd": 161538.89, "wb_contract_number": "1543858", "supplier_id": "316882",
         "supplier": "M/S. AIRES INGEGNERIA", "supplier_country": "Italy", "supplier_country_code": "IT"}


def test_joint_venture_rows_become_one_contract():
    jv = [AWARD, dict(AWARD, supplier="ALL INGEGNERIA", supplier_id="325453")]   # real DS00005 pattern
    contracts, awards = transform.contracts_from_awards(jv, "DS00005")
    assert len(contracts) == 1 and len(awards) == 2
    c = contracts[0]
    assert c["contract_key"] == "C:1543858" and c["n_suppliers"] == 2 and c["coverage_status"] == "full"
    assert c["total_amount_usd"] == pytest.approx(323077.78) and c["ref_key"] == "UDPPPGQCBS2016412"


def test_review_type_null_or_absent_is_unknown_never_guessed():
    row = {"contract_number": "1512302", "project_id": "P102172", "contract_signing_date": "31-Jul-2013",
           "fiscal_year": 2014, "supplier_contract_amount": 16671, "supplier": "ALI OKTAY", "review_type": "Prior"}
    contracts, _ = transform.contracts_from_awards([row], "DS01004")          # DS01004 has no review-type field
    assert contracts[0]["review_type"] == "unknown" and contracts[0]["total_amount_usd"] == 16671
    assert contracts[0]["coverage_status"] == "prior_review_only"
    step, _ = transform.contracts_from_awards([dict(AWARD, review_type=None, fiscal_year=2017)], "DS04691")
    assert step[0]["review_type"] == "unknown" and step[0]["coverage_status"] == "partial_likely_incomplete"


def test_ds04691_uses_step_schema():
    contracts, awards = transform.contracts_from_awards([dict(AWARD, fiscal_year=2018)], "DS04691")
    assert contracts[0]["contract_key"] == "C:1543858" and contracts[0]["review_type"] == "Prior"
    assert awards[0]["amount_usd"] == pytest.approx(161538.89) and contracts[0]["coverage_status"] == "partial"


def test_merge_datasets_dedupes_by_number_with_precedence_and_reports_overlap():
    new, new_aw = transform.contracts_from_awards([AWARD], "DS00005")
    mid, mid_aw = transform.contracts_from_awards([dict(AWARD, fiscal_year=2019, contract_signing_date="01-Jun-2019"),
                                                   dict(AWARD, wb_contract_number="999", fiscal_year=2018)], "DS04691")
    old, old_aw = transform.contracts_from_awards([{"contract_number": "1543858", "project_id": "P000001",
                                                    "contract_signing_date": "31-Jul-2013", "fiscal_year": 2014,
                                                    "supplier_contract_amount": 5, "supplier": "X"}], "DS01004")
    contracts, awards, rep = transform.merge_datasets({"DS00005": (new, new_aw), "DS04691": (mid, mid_aw),
                                                       "DS01004": (old, old_aw)})
    by = {c["contract_key"]: c for c in contracts}
    assert len(contracts) == 2 and by["C:1543858"]["source_dataset"] == "DS00005"
    assert by["C:1543858"]["also_in"] == ["DS04691", "DS01004"]
    assert rep["contracts_by_dataset_combination"] == {"DS00005+DS04691+DS01004": 1, "DS04691": 1}
    assert rep["overlap_conflicts"] == {"project_differs": 1, "signing_date_differs": 1}
    # Only the kept copy's awards survive: no double-counted amounts.
    assert sum(a["contract_key"] == "C:1543858" for a in awards) == 1 and len(awards) == 2


def _c(key, ref, signed, rt="Prior", pid="P1"):
    return {"contract_key": key, "project_id": pid, "ref_key": ref, "signing_date": signed, "review_type": rt}


def _n(ref, d, t="Invitation for Bids", pid="P1"):
    return {"notice_type": t, "tender_key": f"{pid}:{ref}", "notice_date": d, "project_id": pid}


def test_tender_durations_drop_and_count_bad_values():
    contracts = [_c("a", "R1", date(2021, 6, 1)), _c("b", "R2", date(2020, 1, 1)),
                 _c("c", "R3", date(2025, 1, 1)), _c("d", "R4", date(2021, 1, 1), rt="Post")]
    notices = [_n("R1", date(2021, 1, 1)), _n("R1", date(2020, 12, 1), "Request for Expression of Interest"),
               _n("R2", date(2020, 6, 1)), _n("R3", date(2020, 1, 1)), _n("R4", date(2020, 1, 1), "Contract Award")]
    rows, rep = transform.tender_durations(contracts, notices)
    assert [r["contract_key"] for r in rows] == ["a"] and rows[0]["duration_days"] == 182   # earliest start used
    assert rep["Prior"] == {"contracts": 3, "matched": 3, "kept": 1, "dropped_negative": 1, "dropped_over_3y": 1}
    assert rep["Post"]["matched"] == 0   # a Contract Award notice is not a start notice


def test_fy_activity_flags_partial_coverage_and_measures_it():
    contracts = [{"project_id": "P1", "fiscal_year": 2018, "total_amount_usd": 10.0, "ref_key": "R1"},
                 {"project_id": "P1", "fiscal_year": 2020, "total_amount_usd": 5.0, "ref_key": "R2"}]
    notices = [_n("R1", date(2018, 3, 1), "Contract Award"), _n("R9", date(2018, 4, 1), "Contract Award"),
               _n("R2", date(2019, 8, 1), "Contract Award")]       # 2019-08-01 is FY2020
    rows = {r["fiscal_year"]: r for r in transform.fy_activity(contracts, notices, 2016, 2021)}
    assert rows[2018]["n_contracts"] == 1 and rows[2018]["coverage_status"] == "partial"
    assert rows[2018]["award_notices"] == 2 and rows[2018]["notice_coverage_pct"] == 0.5   # 1 of 2 awards found
    assert rows[2017]["coverage_status"] == "partial_likely_incomplete" and rows[2017]["notice_coverage_pct"] is None
    assert rows[2020]["notice_coverage_pct"] == 1.0 and rows[2020]["coverage_status"] == "full"
    assert rows[2016]["coverage_status"] == "prior_review_only"


def test_governance_and_country_rows():
    page = [{"page": 1}, [{"indicator": {"id": "GOV_WGI_RQ.EST"}, "countryiso3code": "KEN", "date": "2025", "value": -0.37},
                          {"indicator": {"id": "IQ.CPA.PUBS.XQ"}, "countryiso3code": "IND", "date": "2020", "value": None}]]
    g = transform.governance_rows(page)
    assert g[0]["value"] == -0.37 and not g[0]["is_missing"] and g[1]["is_missing"]
    cp = [{}, [{"id": "MLI", "iso2Code": "ML", "name": "Mali", "region": {"value": "Sub-Saharan Africa"},
                "incomeLevel": {"value": "Low income"}, "lendingType": {"value": "IDA"}},
               {"id": "WLD", "iso2Code": "1W", "name": "World", "region": {"value": "Aggregates"}, "lendingType": {}}]]
    assert transform.country_rows(cp) == [{"iso3": "MLI", "iso2": "ML", "name": "Mali", "region": "Sub-Saharan Africa",
                                           "income_level": "Low income", "lending_type": "IDA"}]


# --- chunking --------------------------------------------------------------------------------------------------------

def _el(t, c, p=1):
    return {"type": t, "content": c, "page_id": p}


def test_elements_from_parse_reads_document_elements():
    doc = {"document": {"elements": [_el("section_header", "I. OUTCOME", 3), _el("text", "Body.", 3)]}}
    els = elements_from_parse(json.dumps(doc))
    assert [e["type"] for e in els] == ["section_header", "text"] and els[0]["page"] == 3


def test_heading_levels():
    assert heading_level("V. LESSONS AND RECOMMENDATIONS") == 1
    assert heading_level("3.2 Procurement") == 2 and heading_level("A. Procurement") == 2
    assert heading_level("KEY DATES") == 1 and heading_level("Procurement arrangements") == 2


def test_chunking_keeps_sections_tables_and_drops_boilerplate():
    meta = {"doc_id": "D1", "project_id": "P147864", "doc_type": "ICR", "doc_date": "2024-12-23"}
    els = [_el("page_header", "Report No: ICR00200"), _el("section_header", "V. LESSONS AND RECOMMENDATIONS", 20),
           _el("section_header", "Procurement", 20), _el("text", "Re-tendering delayed works by six months.", 20),
           _el("table", "<table><tr><td>Rating</td><td>Moderately Satisfactory</td></tr></table>", 21),
           _el("page_number", "21", 21), _el("text", "Second lesson.", 21)]
    ch = chunk_document(els, meta)
    # The three tiny pieces share one section, so they merge into one chunk (quality pass); nothing is lost.
    assert len(ch) == 1 and ch[0]["chunk_type"] == "mixed"
    assert ch[0]["section_path"] == "V. LESSONS AND RECOMMENDATIONS > Procurement"
    assert "Re-tendering delayed works" in ch[0]["text"] and "Rating | Moderately Satisfactory" in ch[0]["text"]
    assert "Second lesson." in ch[0]["text"] and (ch[0]["page_start"], ch[0]["page_end"]) == (20, 21)
    assert "Report No" not in ch[0]["text"] and "\n21\n" not in ch[0]["text"]
    assert ch[0]["context_header"].startswith("[ICR | P147864 | 2024-12-23]")
    assert ch[0]["chunk_id"] == "D1-0000" and ch[0]["text_hash"]


def test_chunking_respects_size_with_overlap():
    meta = {"doc_id": "D2", "project_id": "P1", "doc_type": "PAD", "doc_date": "2015-01-01"}
    paras = [_el("text", ("word " * 100).strip() + f" para{i}.", i) for i in range(20)]
    ch = chunk_document([_el("section_header", "I. CONTEXT")] + paras, meta)
    assert len(ch) > 2 and all(c["n_tokens"] <= 900 for c in ch)


# --- graph links -----------------------------------------------------------------------------------------------------

CONTRACT = {"project_id": "P147864", "signing_date": date(2018, 2, 22), "ref_key": "INUPPWD14106CWRFB",
            "description": "Construction of SH-51 Badaun Bilsi Bijnaur Road"}


def _chunk(text, doc_date=date(2024, 12, 23), section="V. LESSONS > Procurement", pid="P147864",
           doc_type="Implementation Completion and Results Report"):
    return {"project_id": pid, "doc_type": doc_type, "doc_date": doc_date, "section_path": section, "text": text}


def test_link_requires_project_time_and_evidence():
    assert link_score(CONTRACT, _chunk("Package IN-UPPWD-14106-CW-RFB was rebid."))[0] >= 0.8
    assert link_score(CONTRACT, _chunk("IN-UPPWD-14106-CW-RFB", pid="P000001"))[0] == 0          # other project
    assert link_score(CONTRACT, _chunk("IN-UPPWD-14106-CW-RFB", doc_date=date(2017, 1, 1)))[0] == 0  # before signing
    assert link_score(CONTRACT, _chunk("The project improved roads."))[0] == 0                  # no evidence
    assert link_score(CONTRACT, _chunk("IN-UPPWD-14106-CW-RFB", doc_type="Procurement Plan"))[0] == 0


def test_link_rule_records_evidence_and_strong_edge_type():
    score, rule, edge = link_score(CONTRACT, _chunk("GAWAR CONSTRUCTION delayed the Badaun Bijnaur works."),
                                   supplier_names=["GAWAR CONSTRUCTION"])
    # Only 2 shared description words (< 3), so the evidence is the supplier mention alone.
    assert rule == "project+temporal+supplier_mention" and edge == "DESCRIBES" and score == pytest.approx(0.6)


def test_weak_link_needs_three_rare_words_and_weighs_less():
    from src.graph.links import chunk_features, document_frequencies
    c = dict(CONTRACT, ref_key=None)
    hit = _chunk("Works on the Badaun Bilsi Bijnaur stretch slipped by six months.")
    score, rule, edge = link_score(c, hit)
    assert edge == "MAY_DESCRIBE" and rule.endswith("rare_words:3") and score <= 0.45
    # With corpus frequencies: if "badaun" appears in most chunks it is not rare any more, so only 2 rare words remain.
    corpus = [dict(_chunk(t), **chunk_features(_chunk(t))) for t in
              ["Badaun district office."] * 50 + ["Unrelated text about rainfall."] * 50]
    df, n = document_frequencies(corpus + [dict(hit, **chunk_features(hit))])
    assert link_score(c, hit, df=df, n_chunks=n) == (0.0, None, None)


def test_common_words_carry_no_weight():
    from src.graph.links import rare_shared
    df = {"rural": 900, "access": 950, "liangshuijing": 2, "laping": 1, "subgrade": 4}
    w = rare_shared("Subgrade works rural access Liangshuijing to Laping", {"rural", "access", "liangshuijing", "laping", "subgrade"},
                    df=df, n_chunks=1000)
    assert set(w) == {"liangshuijing", "laping", "subgrade"} and w["laping"] > w["subgrade"]   # rarer = heavier


def test_precomputed_chunk_features_give_same_score():
    from src.graph.links import chunk_features
    ch = _chunk("GAWAR CONSTRUCTION delayed IN-UPPWD-14106-CW-RFB works at Badaun Bijnaur.")
    assert link_score(CONTRACT, dict(ch, **chunk_features(ch)), ["GAWAR CONSTRUCTION"]) ==            link_score(CONTRACT, ch, ["GAWAR CONSTRUCTION"])


# --- chunk quality pass (run 1 on real data: median 53 tokens, tables up to ~5,300 tokens) ---------------------------

def _meta(doc_id="D9"):
    return {"doc_id": doc_id, "project_id": "P1", "doc_type": "ISR", "doc_date": "2020-01-01"}


def test_small_sibling_sections_merge_but_top_levels_do_not():
    els = [_el("section_header", "I. PROJECT DEVELOPMENT OBJECTIVES"), _el("section_header", "A. PDO", 1),
           _el("text", "Improve rural road access.", 1), _el("section_header", "B. Key results", 1),
           _el("text", "Travel time reduced.", 1), _el("section_header", "II. IMPLEMENTATION", 2),
           _el("text", "Works delayed by rains.", 2)]
    ch = chunk_document(els, _meta())
    assert len(ch) == 2                                       # A + B merged; II stays separate (other top level)
    assert "Improve rural road access." in ch[0]["text"] and "[B. Key results]" in ch[0]["text"]
    assert ch[0]["merged_sections"] == 2 and ch[1]["section_path"] == "II. IMPLEMENTATION"


def test_small_table_and_text_in_same_section_become_mixed():
    els = [_el("section_header", "I. RATINGS"), _el("text", "Overall ratings:", 1),
           _el("table", "<table><tr><td>Progress</td><td>Moderately Satisfactory</td></tr></table>", 1)]
    ch = chunk_document(els, _meta())
    assert len(ch) == 1 and ch[0]["chunk_type"] == "mixed"


def test_large_table_split_with_header_repeated():
    rows = "".join(f"<tr><td>Contract {i}</td><td>{'word ' * 30}</td></tr>" for i in range(60))
    html = "<table><tr><th>Package</th><th>Description</th></tr>" + rows + "</table>"
    ch = chunk_document([_el("section_header", "ANNEX 2. PROCUREMENT"), _el("table", html, 40)], _meta())
    parts = [c for c in ch if c["table_part"]]
    assert len(parts) >= 3 and parts[0]["table_part"] == f"1/{len(parts)}"
    assert all(c["text"].startswith("Package | Description") for c in parts)
    assert all(c["n_tokens"] <= 800 for c in parts)
    body = [l for c in parts for l in c["text"].split("\n")[1:]]
    assert len(body) == 60                                    # every row kept exactly once


def test_table_separators_do_not_count_as_tokens():
    from src.chunking.sections import est_tokens
    assert est_tokens("a | b | c") == est_tokens("a b c")


def test_text_hash_flags_exact_repeats():
    from src.chunking.sections import text_hash
    assert text_hash("Rating:  Satisfactory") == text_hash("rating: satisfactory")
    assert text_hash("Rating: Satisfactory") != text_hash("Rating: Unsatisfactory")


# --- duplicates: latest primary, older searchable by date -------------------------------------------------------------

ISR = "Implementation Status and Results Report"


def _ck(cid, doc, date_, text, section="RATINGS", doc_type=ISR, pid="P1"):
    return {"chunk_id": cid, "doc_id": doc, "project_id": pid, "doc_type": doc_type, "doc_date": date_,
            "section_path": section, "text": text}


RATING_2019 = ("Progress towards achievement of PDO: Moderately Satisfactory. Overall implementation progress: "
               "Moderately Satisfactory. Rating as of 12 March 2019 for the road works component in five districts.")
RATING_2020 = RATING_2019.replace("12 March 2019", "03 September 2020")


def test_near_duplicate_isr_latest_is_primary_older_kept():
    from src.chunking.dedup import flag_repeats
    cs_ = [_ck("a", "ISR-2019", date(2019, 3, 12), RATING_2019), _ck("b", "ISR-2020", date(2020, 9, 3), RATING_2020)]
    flag_repeats(cs_)
    old, new = cs_
    assert new["is_primary"] and not new["is_duplicate"]
    assert old["is_duplicate"] and old["dup_kind"] == "near" and old["dup_group"] == "b"   # points to the latest
    assert old["doc_date"] == date(2019, 3, 12) and "12 March 2019" in old["text"]         # kept, searchable by date


def test_exact_duplicates_any_doc_type_and_distinct_text_untouched():
    from src.chunking.dedup import flag_repeats
    cs_ = [_ck("a", "PAD", date(2015, 1, 1), "Same paragraph.", doc_type="Project Appraisal Document"),
           _ck("b", "PP", date(2018, 1, 1), "Same paragraph.", doc_type="Project Paper"),
           _ck("c", "ISR-1", date(2019, 1, 1), "Procurement is delayed for package 3 due to re-bidding."),
           _ck("d", "ISR-2", date(2020, 1, 1), "Disbursement reached 60 percent after the restructuring.")]
    flag_repeats(cs_)
    by = {c["chunk_id"]: c for c in cs_}
    assert by["a"]["dup_kind"] == "exact" and by["a"]["dup_group"] == "b" and by["b"]["is_primary"]
    assert by["c"]["is_primary"] and by["d"]["is_primary"]                # different ISR text: not near-dups


def test_near_duplicates_only_for_isrs_and_same_section():
    from src.chunking.dedup import flag_repeats
    # PADs in their own project, so the identical text in the ISRs below isn't an (expected) exact duplicate of them.
    pads = [_ck("a", "PAD1", date(2015, 1, 1), RATING_2019, doc_type="Project Appraisal Document", pid="P2"),
            _ck("b", "PAD2", date(2016, 1, 1), RATING_2020, doc_type="Project Appraisal Document", pid="P2")]
    other_section = [_ck("c", "ISR-1", date(2019, 1, 1), RATING_2019, section="RATINGS"),
                     _ck("d", "ISR-2", date(2020, 1, 1), RATING_2020, section="KEY ISSUES")]
    flag_repeats(pads + other_section)
    assert all(c["is_primary"] for c in pads + other_section)


def test_char_cap_keeps_every_chunk_under_the_embedding_limit():
    from src.chunking.sections import MAX_CHARS, enforce_char_cap
    long_row = "Contract | " + "54,431,569.00 " * 2000            # one enormous table row, numbers only
    out = enforce_char_cap([{"text": long_row, "n_tokens": 0, "chunk_type": "table"}])
    assert len(out) > 1 and all(len(c["text"]) <= MAX_CHARS for c in out)
    assert "".join(c["text"] for c in out).replace(" ", "") == long_row.replace(" ", "")


# --- link fixes A/B: cases from the checkpoint (c) review of real links ---------------------------------------------

def test_person_like_supplier_names_and_staff_sections_never_link():
    c = dict(CONTRACT, ref_key=None, description="PMGSY road Majhuwa")
    staff = _chunk("Task Team Leader Sangeeta Kumari, Procurement Specialist Puneet Kapoor", section="ADM STAFF")
    assert link_score(c, staff, ["SANGEETA KUMARI"]) == (0.0, None, None)
    body = _chunk("Sangeeta Kumari visited the site.", section="IV. BANK PERFORMANCE")
    assert link_score(c, body, ["SANGEETA KUMARI"]) == (0.0, None, None)       # person-like name: not evidence
    assert link_score(c, _chunk("ROADMAINT finished the pilot."), ["ROADMAINT"]) == (0.0, None, None)   # one word
    acr = _chunk("GAWAR CONSTRUCTION | AGETIER | Agence ...", section="ABBREVIATIONS AND ACRONYMS")
    assert link_score(c, acr, ["GAWAR CONSTRUCTION"]) == (0.0, None, None)


def test_reference_matches_on_token_boundaries_and_returns_matched_text():
    from src.graph.links import link_detail
    c = dict(CONTRACT, ref_key="HAMPGD02", description="YHB cloud computation data center")
    score, rule, edge, matched = link_detail(c, _chunk("Contract HAMP-GD-02 was delivered."))
    assert edge == "DESCRIBES" and matched == "ref:HAMP-GD-02"
    assert link_detail(c, _chunk("Contract HAMP GD 02 was delivered."))[3] == "ref:HAMP GD 02"
    assert link_score(c, _chunk("Contract HAMP-GD-021 was delivered.")) == (0.0, None, None)   # inside a longer code
    assert link_score(c, _chunk("See XHAMPGD02 annex.")) == (0.0, None, None)


def test_rarity_is_per_project_so_everyday_french_words_are_not_rare():
    from src.graph.links import chunk_features, document_frequencies, link_detail
    c = dict(CONTRACT, ref_key=None, description="Travaux d’aménagement de pistes rurales à Kadiolo")
    hit = _chunk("Les travaux d'aménagement des pistes avancent; Kadiolo reste en retard.")
    # a French project: these words appear in most of its chunks, so they are not rare for this project
    project = [dict(_chunk(t), **chunk_features(_chunk(t))) for t in
               ["Les travaux d'aménagement des pistes rurales."] * 60 + ["Rapport annuel."] * 40]
    df, n = document_frequencies(project + [dict(hit, **chunk_features(hit))])
    assert link_score(c, hit, df=df, n_chunks=n) == (0.0, None, None)
    # accents are folded before tokenizing: "aménagement" is one word
    assert "amenagement" in link_detail(c, hit)[3]
