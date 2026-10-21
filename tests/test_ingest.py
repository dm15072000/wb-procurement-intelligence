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
    assert c["contract_key"] == "DS00005:1543858" and c["n_suppliers"] == 2
    assert c["total_amount_usd"] == pytest.approx(323077.78) and c["ref_key"] == "UDPPPGQCBS2016412"


def test_ds01004_has_no_review_type():
    row = {"contract_number": "1512302", "project_id": "P102172", "contract_signing_date": "31-Jul-2013",
           "fiscal_year": 2014, "supplier_contract_amount": 16671, "supplier": "ALI OKTAY", "review_type": "Prior"}
    contracts, _ = transform.contracts_from_awards([row], "DS01004")
    assert contracts[0]["review_type"] is None and contracts[0]["total_amount_usd"] == 16671


def _c(key, ref, signed, rt="Prior", pid="P1"):
    return {"contract_key": key, "project_id": pid, "ref_key": ref, "signing_date": signed, "review_type": rt}


def _n(ref, d, t="Invitation for Bids", pid="P1"):
    return {"notice_type": t, "tender_key": f"{pid}:{ref}", "notice_date": d}


def test_tender_durations_drop_and_count_bad_values():
    contracts = [_c("a", "R1", date(2021, 6, 1)), _c("b", "R2", date(2020, 1, 1)),
                 _c("c", "R3", date(2025, 1, 1)), _c("d", "R4", date(2021, 1, 1), rt="Post")]
    notices = [_n("R1", date(2021, 1, 1)), _n("R1", date(2020, 12, 1), "Request for Expression of Interest"),
               _n("R2", date(2020, 6, 1)), _n("R3", date(2020, 1, 1)), _n("R4", date(2020, 1, 1), "Contract Award")]
    rows, rep = transform.tender_durations(contracts, notices)
    assert [r["contract_key"] for r in rows] == ["a"] and rows[0]["duration_days"] == 182   # earliest start used
    assert rep["Prior"] == {"contracts": 3, "matched": 3, "kept": 1, "dropped_negative": 1, "dropped_over_3y": 1}
    assert rep["Post"]["matched"] == 0   # a Contract Award notice is not a start notice


def test_fy_activity_gap_is_null_not_zero():
    contracts = [{"project_id": "P1", "fiscal_year": 2016, "total_amount_usd": 10.0},
                 {"project_id": "P1", "fiscal_year": 2020, "total_amount_usd": 5.0}]
    rows = {r["fiscal_year"]: r for r in transform.fy_activity(contracts, 2015, 2021)}
    assert rows[2016]["n_contracts"] == 1 and rows[2015]["n_contracts"] == 0       # a real zero outside the gap
    for fy in (2017, 2018, 2019):
        assert rows[fy]["n_contracts"] is None and rows[fy]["amount_usd"] is None and rows[fy]["coverage_gap"]
    assert rows[2020]["amount_usd"] == 5.0 and not rows[2020]["coverage_gap"]


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
    assert [c["chunk_type"] for c in ch] == ["text", "table", "text"]
    assert ch[0]["section_path"] == "V. LESSONS AND RECOMMENDATIONS > Procurement"
    assert "Rating | Moderately Satisfactory" in ch[1]["text"] and ch[1]["page_start"] == 21
    assert all("Report No" not in c["text"] and c["text"] != "21" for c in ch)
    assert ch[0]["context_header"].startswith("[ICR | P147864 | 2024-12-23]")
    assert [c["chunk_id"] for c in ch] == ["D1-0000", "D1-0001", "D1-0002"]


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


def test_link_rule_records_evidence():
    score, rule = link_score(CONTRACT, _chunk("GAWAR CONSTRUCTION delayed the Badaun Bijnaur works."),
                             supplier_names=["GAWAR CONSTRUCTION"])
    assert rule == "project+temporal+supplier_mention+description_overlap" and score == pytest.approx(0.7)


def test_precomputed_chunk_features_give_same_score():
    from src.graph.links import chunk_features
    ch = _chunk("GAWAR CONSTRUCTION delayed IN-UPPWD-14106-CW-RFB works at Badaun Bijnaur.")
    assert link_score(CONTRACT, dict(ch, **chunk_features(ch)), ["GAWAR CONSTRUCTION"]) ==            link_score(CONTRACT, ch, ["GAWAR CONSTRUCTION"])
