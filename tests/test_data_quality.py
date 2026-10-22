"""Unit tests for the data-quality pass (2026-10-22): joint awards, standard values, per-project coverage, checks 2-7.
Record shapes and numbers copied from real API responses pulled on 2026-10-22."""
from datetime import date

import pytest

from src.entity_resolution.suppliers import resolve
from src.ingest import transform
from src.quality import checks as ck
from src.quality import joint_awards as ja
from src.quality import project_coverage as pc
from src.quality import standard_values as sv
from src.risk import amendment


def _step_row(number, supplier, sid, amount, ref="REF-1", pid="P163239", method="Request for Bids", cat="Works"):
    return {"wb_contract_number": number, "project_id": pid, "borrower_contract_reference_number": ref,
            "contract_signing_date": "10-Mar-2021", "fiscal_year": 2021, "procurement_category": cat,
            "procurement_method": method, "review_type": "Prior", "supplier": supplier, "supplier_id": sid,
            "supplier_country": "Albania", "supplier_country_code": "AL", "supplier_contract_amount_usd": amount}


# --- 3. joint awards ---------------------------------------------------------------------------------------------------

def test_step_joint_award_total_is_sum_of_member_shares():
    # DS00005 contract 1725106: two members at USD 124,448 each; the award notice says USD 248,896.00 in total.
    rows = [_step_row("1725106", "CREATIVE BUSINESS SOLUTIONS", "1", 124448),
            _step_row("1725106", "HORWATH CONSULTING ZAGREB", "2", 124448)]
    cs, aw = transform.contracts_from_awards(rows, "DS00005")
    assert cs[0]["total_amount_usd"] == 248896 and cs[0]["amount_rule"] == "sum_of_member_shares"
    assert cs[0]["is_joint_award"] and [a["amount_usd"] for a in aw] == [124448, 124448]


def test_ds01004_repeated_full_amount_is_counted_once_and_shared():
    rows = [{"contract_number": "9", "project_id": "P1", "contract_signing_date": "01-Mar-2015", "fiscal_year": 2015,
             "supplier": s, "supplier_contract_amount": 1000} for s in ("A", "B")]
    cs, aw = transform.contracts_from_awards(rows, "DS01004")
    assert cs[0]["total_amount_usd"] == 1000 and cs[0]["amount_rule"] == "repeated_full_amount_counted_once"
    assert [a["amount_usd"] for a in aw] == [500, 500] and [a["amount_row_usd"] for a in aw] == [1000, 1000]


def test_ds01004_single_row_jv_name_is_a_joint_award_with_full_amount():
    row = {"contract_number": "7", "project_id": "P1", "contract_signing_date": "01-Mar-2015", "fiscal_year": 2015,
           "supplier": "LEA ASSOCIATES IN JV WITH SHERWOOD", "supplier_contract_amount": 1402235}
    cs, _ = transform.contracts_from_awards([row], "DS01004")
    assert cs[0]["is_joint_award"] and cs[0]["total_amount_usd"] == 1402235 and cs[0]["amount_rule"] == "single_row"


def test_notice_prices_dedupes_a_price_listed_once_per_member():
    html = ("<p>Signed Contract Price CNY 94,669,588.16</p> ... <p>Signed Contract Price CNY 94,669,588.16</p>")
    assert ja.notice_prices(html) == [("CNY", 94669588.16)]


def _notice(ref, price_text, pid="P163239"):
    return {"notice_type": "Contract Award", "tender_key": f"{pid}:{ref}", "notice_text": price_text}


def test_verdicts_shares_vs_repeated_against_local_currency_notices():
    # Single-supplier contract gives the rate (USD 1,000 for XOF 600,000). Joint contract A: rows sum to the price
    # (shares). Joint contract B: each row equals the price (repeated).
    contracts = [{"contract_key": k, "source_dataset": "DS00005", "project_id": "P1", "ref_key": k}
                 for k in ("S", "A", "B")]
    awards = ([{"contract_key": "S", "amount_row_usd": 1000.0}]
              + [{"contract_key": "A", "amount_row_usd": 500.0}] * 2
              + [{"contract_key": "B", "amount_row_usd": 1000.0}] * 2)
    notices = [_notice(k, f"Signed Contract Price XOF 600,000", pid="P1") for k in ("S", "A", "B")]
    rows, summary = ja.verdicts(contracts, awards, notices)
    assert {r["contract_key"]: r["verdict"] for r in rows} == {"A": "shares", "B": "repeated"}
    assert ja.repeated_share(summary["DS00005"]) == 0.5


def test_usd_notice_needs_no_reference_rate():
    contracts = [{"contract_key": "C:1725106", "source_dataset": "DS00005", "project_id": "P163239", "ref_key": "R"}]
    awards = [{"contract_key": "C:1725106", "amount_row_usd": 124448.0}] * 2
    rows, _ = ja.verdicts(contracts, awards, [_notice("R", "Signed Contract Price USD 248896.00")])
    assert rows[0]["verdict"] == "shares" and rows[0]["sum_over_price"] == 1.0


def test_joint_award_gate_fails_when_step_rows_look_repeated():
    ok = ck.joint_award_basis({"DS00005": {"verdict_shares": 28, "verdict_repeated": 1}})
    bad = ck.joint_award_basis({"DS04691": {"verdict_shares": 2, "verdict_repeated": 3}})
    multi01 = ck.joint_award_basis({"DS01004": {"joint_contracts": 1}})
    assert ok["passed"] and not bad["passed"] and not multi01["passed"]


# --- 4. standard values ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,std", [("Civil Works", "Works"), ("Works", "Works"), ("goods", "Goods"),
                                     ("Non-consulting Services", "Non-consulting Services")])
def test_category_map(raw, std):
    assert sv.category(raw) == (std, True)


@pytest.mark.parametrize("raw,std,family,market", [
    ("International Competitive Bidding", "Request for Bids", "open_competitive", "international"),
    ("National Competitive Bidding", "Request for Bids", "open_competitive", "national"),
    ("Request for Bids", "Request for Bids", "open_competitive", None),
    ("Shopping", "Request for Quotations", "limited_competitive", None),
    ("Consultant Qualification  Selection", "CQS", "consultant_competitive", None),     # double space in the source
    ("Selection Based On Consultant’s Qualification", "CQS", "consultant_competitive", None),
    ("Single Source Selection", "Direct Selection", "direct", None),
    ("Quality And Cost-Based Selection", "QCBS", "consultant_competitive", None),
])
def test_method_map(raw, std, family, market):
    m = sv.method(raw)
    assert (m["method"], m["family"], m["market"], m["mapped"]) == (std, family, market, True)


@pytest.mark.parametrize("raw,std", [("Commercial Practices", "Commercial Practices"),
                                     ("Comercial Practices", "Commercial Practices"),
                                     ("Public Private Partnership", "Public-Private Partnership"),
                                     ("Procurement Agents", "Procurement Agents"), ("Imports", "Imports"),
                                     ("Alternative Procurement Arrangement", "Alternative Procurement Arrangement")])
def test_spellings_found_by_the_full_data_run_are_mapped(raw, std):
    m = sv.method(raw)
    assert m["mapped"] and m["method"] == std and m["family"] == "other"


def test_unknown_spelling_is_flagged_not_guessed_and_missing_is_unknown():
    assert sv.method("Some New Method") == {"method": "other", "family": "other", "market": None, "mapped": False}
    assert sv.category(None) == ("unknown", True)
    cs, _ = transform.contracts_from_awards([_step_row("1", "X", "1", 5, method="Some New Method")], "DS00005")
    assert cs[0]["values_mapped"] is False and cs[0]["procurement_method_raw"] == "Some New Method"
    gate = ck.standard_values([dict(cs[0], in_universe=True)])
    assert not gate["passed"]


def test_transform_keeps_raw_and_standard_values():
    cs, aw = transform.contracts_from_awards([_step_row("1", "X", "1", 5, method="Shopping", cat="Civil Works")],
                                             "DS04691")
    c = cs[0]
    assert (c["procurement_category"], c["procurement_method"], c["procurement_category_raw"]) == \
           ("Works", "Request for Quotations", "Civil Works")
    assert aw[0]["procurement_method"] == "Request for Quotations"


def test_amendment_large_works_fires_for_step_and_ds01004_names():
    for cat in ("Works", "Civil Works"):
        s = amendment.score({"category": cat, "amount_usd": 25e6})
        assert any(f["factor"] == "large_civil_works" for f in s["factors"])


# --- 6. suppliers: known answer ----------------------------------------------------------------------------------------

def test_gansu_jinlongyi_ds01004_record_resolves_to_supplier_id_323849():
    """DS01004 contract 1536520 (no supplier ID, line break inside the name) and DS00005 contract 1653878
    (supplier_id 323849), both P132775, China: the same firm."""
    recs = [{"raw_name": "Gansu Jinlongyi Road Bridge Construction (Group) Co., Ltd.", "country": "CHN",
             "supplier_id": "323849", "method": "Request for Bids"},
            {"raw_name": "GANSU JINLONGYI ROAD AND BRIDGE CONSTRUCTION GROUP CO.,\n            LTD.", "country": "CHN",
             "supplier_id": None, "method": "Request for Bids"}]
    m, merges = resolve(recs)
    assert m[0]["supplier_key"] == m[1]["supplier_key"]
    ids_in_cluster = {r["supplier_id"] for r, x in zip(recs, m) if x["supplier_key"] == m[1]["supplier_key"]}
    assert "323849" in ids_in_cluster


# --- 8. per-project coverage -------------------------------------------------------------------------------------------

def _c(pid, ref, signed, ds="DS00005"):
    from src.entity_resolution import keys
    return {"project_id": pid, "ref_key": ref, "signing_date": signed, "fiscal_year": keys.fiscal_year(signed),
            "source_dataset": ds, "coverage_status": keys.coverage_status(keys.fiscal_year(signed)),
            "total_amount_usd": 10.0}


def test_project_coverage_flags():
    projects = [{"project_id": p, "country": "X", "board_approval_date": date(2019, 1, 10),
                 "closing_date": date(2024, 6, 30)} for p in ("P1", "P2")]
    contracts = ([_c("P1", f"R{i}", date(2021, 3, 1)) for i in range(10)] + [_c("P1", "E", date(2018, 12, 1))]
                 + [_c("P2", "R0", date(2020, 3, 1))])
    notices = ([{"project_id": "P1", "notice_type": "Contract Award", "tender_key": f"P1:R{i}"} for i in range(10)]
               + [{"project_id": "P2", "notice_type": "Contract Award", "tender_key": f"P2:R{i}"} for i in range(10)])
    rep = {r["project_id"]: r for r in pc.report(projects, contracts, notices)}
    assert rep["P1"]["coverage_pct"] == 1.0 and "signed_before_approval" in rep["P1"]["flags"]
    assert rep["P1"]["empty_full_years"] == [2020, 2022, 2023]       # FY2019 / FY2024 are the partial end years
    assert rep["P2"]["coverage_pct"] == 0.1 and "low_coverage" in rep["P2"]["flags"]


def test_too_few_notices_is_reported_not_judged():
    projects = [{"project_id": "P1", "country": "X", "board_approval_date": None, "closing_date": None}]
    rep = pc.report(projects, [], [{"project_id": "P1", "notice_type": "Contract Award", "tender_key": "P1:Z"}])
    assert rep[0]["flags"] == ["no_contracts", "few_award_notices"]


# --- 2, 5, 7. other gates ---------------------------------------------------------------------------------------------

def test_duplicate_and_orphan_checks():
    cs = [{"contract_key": "C:1", "source_dataset": "DS00005", "also_in": []},
          {"contract_key": "C:1", "source_dataset": "DS00005", "also_in": []}]
    assert not ck.duplicate_keys(cs, "contract_key", 2, "x")["passed"]
    assert not ck.orphan_awards(cs[:1], [{"contract_key": "C:2"}])["passed"]
    a = {"contract_key": "C:1", "raw_supplier_name": "A", "source_supplier_id": None, "supplier_country": "X",
         "amount_row_usd": 1.0}
    assert not ck.duplicate_award_rows([a, dict(a)])["passed"]


def test_fiscal_year_and_column_checks():
    good = {"contract_key": "C:1", "source_dataset": "DS00005", "signing_date": date(2020, 7, 1), "fiscal_year": 2021}
    bad = dict(good, contract_key="C:2", fiscal_year=2020)
    assert ck.fiscal_year_consistency([good])["passed"] and not ck.fiscal_year_consistency([bad])["passed"]
    step = {"wb_contract_number", "supplier_id"}
    ds01 = {"contract_number", "supplier_contract_amount", "project_id", "contract_signing_date", "fiscal_year",
            "procurement_category", "procurement_method", "supplier", "borrower_contract_reference_number"}
    assert ck.column_sets({"DS00005": step, "DS04691": step, "DS01004": ds01})["passed"]
    assert not ck.column_sets({"DS00005": step, "DS04691": {"wb_contract_number"}, "DS01004": ds01})["passed"]


def test_duration_check_rejects_a_start_that_is_not_ifb_or_reoi():
    notices = [{"tender_key": "P1:A", "notice_date": date(2021, 1, 1), "notice_type": "Invitation for Bids"},
               {"tender_key": "P1:B", "notice_date": date(2021, 1, 1), "notice_type": "Contract Award"}]
    rep = {"Prior": {"contracts": 10, "matched": 5, "kept": 2}}
    ok = ck.durations([{"contract_key": "C:1", "tender_key": "P1:A", "start_notice_date": date(2021, 1, 1),
                        "duration_days": 100}], notices, rep)
    bad = ck.durations([{"contract_key": "C:2", "tender_key": "P1:B", "start_notice_date": date(2021, 1, 1),
                         "duration_days": 100}], notices, rep)
    assert ok["passed"] and ok["numbers"]["match_rate"] == 0.5 and not bad["passed"]
    low = ck.durations([], notices, {"Post": {"contracts": 100, "matched": 10, "kept": 9}})
    assert not low["passed"]
