"""Tests for ratings extraction, conflict resolution and early-warning flags."""
from datetime import date

from src.conflict import ratings as ra
from src.monitoring import early_warning as ew

ISR, ICR = ra.ISR_TYPE, ra.ICR_TYPE
IEG = "Implementation Completion Report Review"


def test_extract_ratings_from_isr_overview_and_icr():
    isr = ("Implementation Status Overview\nProgress towards achievement of PDO: Moderately Satisfactory\n"
           "Overall Implementation Progress (IP): Moderately Unsatisfactory")
    got = {r["dimension"]: r["value"] for r in ra.extract_ratings(isr)}
    assert got == {"outcome": "Moderately Satisfactory", "implementation": "Moderately Unsatisfactory"}
    icr = "Outcome Rating | Satisfactory\nBank Performance: Moderately Satisfactory"
    got = {r["dimension"]: r["score"] for r in ra.extract_ratings(icr)}
    assert got == {"outcome": 5, "bank_performance": 4}


def test_extract_ratings_previous_current_table_takes_current():
    t = "Name | Previous Rating | Current Rating\nProcurement | Moderately Satisfactory | Moderately Unsatisfactory"
    assert ra.extract_ratings(t) == [{"dimension": "procurement", "value": "Moderately Unsatisfactory", "score": 3,
                                      "matched": "Procurement | Moderately Satisfactory | Moderately Unsatisfactory"}]
    # without that header, the first value is used
    assert ra.extract_ratings("Procurement: Satisfactory | Unsatisfactory")[0]["score"] == 5


def test_extract_ratings_ignores_unrelated_text():
    assert ra.extract_ratings("The procurement of works was satisfactory overall in the sense that…") == []
    assert ra.extract_ratings("Unsatisfactory performance of contractors delayed outcome indicators.") == []


def _o(doc_type, d, score, dim="outcome", cid=None):
    return {"dimension": dim, "score": score, "doc_type": doc_type, "doc_date": d, "chunk_id": cid or f"{doc_type[:3]}{d}"}


OBS = [_o(ISR, "2017-06-01", 4), _o(ISR, "2018-06-01", 4), _o(ISR, "2019-01-01", 5),
       _o(ICR, "2019-12-01", 3), _o(IEG, "2020-09-01", 2)]


def test_outcome_question_prefers_ieg_then_icr():
    r = ra.resolve(OBS, "outcome")
    assert r["chosen"]["doc_type"] == IEG and len(r["others"]) == 4
    r = ra.resolve(OBS, "outcome", as_of="2020-01-01")       # IEG review not yet published
    assert r["chosen"]["doc_type"] == ICR


def test_state_question_uses_latest_isr_before_date():
    r = ra.resolve(OBS, "outcome", question="state", as_of="2018-12-31")
    assert r["chosen"]["doc_date"] == "2018-06-01"


def test_conflict_flags_are_raised_not_hidden():
    flags = {f["flag"] for f in ra.resolve(OBS, "outcome")["flags"]}
    assert flags == {"isr_icr_disconnect", "ieg_downgrade"}   # S -> MU is 2 levels; IEG below ICR
    vol = ra.flags([_o(ISR, "2017-01-01", 5), _o(ISR, "2017-07-01", 3)])
    assert vol[0]["flag"] == "isr_volatility"
    assert ra.resolve([], "outcome")["chosen"] is None


def test_overdue_tenders():
    out = ew.overdue_tenders([{"tender_key": "P1:A", "start_notice_date": "2023-01-01", "p80_days": 300},
                              {"tender_key": "P1:B", "start_notice_date": "2023-11-01", "p80_days": 300},
                              {"tender_key": "P1:C", "start_notice_date": "2023-01-01", "p80_days": None}],
                             date(2024, 1, 1))
    assert [o["key"] for o in out] == ["P1:A"]


def test_disbursement_lag_linear_benchmark():
    snaps = [{"end_of_period": "2021-01-31", "disbursed_usd": 10.0, "original_principal_usd": 100.0},
             {"end_of_period": "2021-01-31", "disbursed_usd": 0.0, "original_principal_usd": 0.0}]
    out = ew.disbursement_lag(snaps, date(2018, 1, 1), date(2023, 1, 1), date(2021, 6, 1))
    assert out and out[0]["flag"] == "disbursement_lag"        # 10% disbursed after ~60% of the period
    snaps[0]["disbursed_usd"] = 50.0
    assert ew.disbursement_lag(snaps, date(2018, 1, 1), date(2023, 1, 1), date(2021, 6, 1)) == []
    assert ew.disbursement_lag([], date(2018, 1, 1), date(2023, 1, 1), date(2021, 6, 1)) == []


def test_rating_and_event_flags():
    obs = [{"dimension": "procurement", "score": 5, "value": "Satisfactory", "doc_date": "2020-01-01", "doc_type": ISR},
           {"dimension": "procurement", "score": 3, "value": "Moderately Unsatisfactory", "doc_date": "2021-01-01",
            "doc_type": ISR, "chunk_id": "k"}]
    assert ew.rating_flags(obs, date(2021, 6, 1))[0]["reason"].endswith("Moderately Unsatisfactory")
    assert ew.rating_flags(obs, date(2020, 6, 1)) == []
    ev = [{"event_type": "rebid", "doc_date": "2021-03-01", "quote": "Package 3 was re-bid", "doc_type": ISR}]
    assert len(ew.event_flags(ev, date(2021, 6, 1))) == 1 and ew.event_flags(ev, date(2023, 6, 1)) == []
