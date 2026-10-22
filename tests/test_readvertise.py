"""Re-advertisement under a new reference (user decision 2026-10-22). Descriptions copied from real notices."""
from datetime import date

from src.risk import readvertise as rv

BHIND = "MP04WB - 03 : Upgradation and resurfacing of 14 Gravel Roads in Bhind District."


def _t(key, d, desc, contract=False, pid="P157054"):
    return {"tender_key": f"{pid}:{key}", "project_id": pid, "start_date": d, "description": desc,
            "has_contract": contract}


def test_same_package_needs_same_numbers_in_order_and_distinctive_words():
    assert rv.same_package(BHIND, "MP04 WB-03: Upgradation and resurfacing of 14 Gravel Roads in Bhind District")
    assert not rv.same_package(BHIND, BHIND.replace("WB - 03", "WB - 04"))          # another package
    assert not rv.same_package(BHIND, BHIND.replace("14 Gravel", "12 Gravel"))
    # wrong match found in the precision sample, fixed: numbers must match IN ORDER
    assert not rv.same_package("Tender 4 - Physical works for Improvement of the local road infrastructure - Lot 5",
                               "Tender 5 - Physical works for improvement of the local road infrastructure - Lot 4")
    assert rv.same_package("MP13 WB - 11 : Upgradation and resurfacing of 05 Gravel Roads in Guna District.",
                           "MP13 WB - 11 : Upgradation and resurfacing of 5 Gravel Roads in Guna District.")


def test_short_or_generic_descriptions_never_chain():
    assert not rv.eligible("Supply of goods")
    assert not rv.eligible("Construction works under the project, lot 2")
    assert rv.eligible(BHIND)


def test_earlier_round_without_contract_is_flagged_and_chain_durations_keep_the_original():
    ts = [_t("A", date(2017, 5, 12), BHIND), _t("B", date(2017, 9, 28), BHIND),
          _t("C", date(2019, 3, 12), BHIND, contract=True)]
    flags, chains, rep = rv.find_chains(ts)
    assert set(flags) == {"P157054:A", "P157054:B"} and flags["P157054:A"]["later_tender_key"] == "P157054:B"
    assert rep["chains"] == 1 and rep["chains_ending_in_contract"] == 1
    rows = rv.chain_durations(chains, {"P157054:C": [{"contract_key": "C:1", "review_type": "Post",
                                                      "duration_days": 100, "start_notice_date": date(2019, 3, 12),
                                                      "signing_date": date(2019, 6, 20)}]})
    assert len(rows) == 1 and rows[0]["duration_days"] == 100 and rows[0]["rounds_before"] == 2
    assert rows[0]["duration_days_from_first_notice"] == (date(2019, 6, 20) - date(2017, 5, 12)).days


def test_awarded_earlier_round_short_gap_and_parallel_lots_are_not_flagged():
    awarded = [_t("A", date(2018, 1, 1), BHIND, contract=True), _t("B", date(2018, 6, 1), BHIND)]
    assert rv.find_chains(awarded)[0] == {}
    parallel = [_t("A", date(2018, 1, 1), BHIND), _t("B", date(2018, 1, 10), BHIND), _t("C", date(2018, 6, 1), BHIND)]
    flags, _, rep = rv.find_chains(parallel)
    assert flags == {} and rep["parallel_lot_groups_skipped"] == 1
    other_project = [_t("A", date(2018, 1, 1), BHIND), _t("B", date(2018, 6, 1), BHIND, pid="P999999")]
    assert rv.find_chains(other_project)[0] == {}
