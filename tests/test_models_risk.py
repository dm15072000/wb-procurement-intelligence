"""Tests for the duration-model helpers, re-bid labels/rates and the amendment score."""
from datetime import date

import pytest

from src.models import duration as du
from src.risk import amendment, labels


# --- duration: leakage-safe governance -------------------------------------------------------------------------------

def test_latest_published_year_respects_release_lag():
    assert du.latest_published_year(date(2021, 9, 30)) == 2019   # 2020 values not yet released
    assert du.latest_published_year(date(2021, 10, 1)) == 2020


def test_governance_asof_uses_only_published_year_and_never_imputes():
    lk = du.governance_lookup([
        {"iso3": "IND", "year": 2019, "indicator": "GOV_WGI_GE.EST", "value": 0.1},
        {"iso3": "IND", "year": 2020, "indicator": "GOV_WGI_GE.EST", "value": 0.3},   # future at t
        {"iso3": "IND", "year": 2018, "indicator": "IQ.CPA.PUBS.XQ", "value": 3.5},   # too old: not carried forward
        {"iso3": "IND", "year": 2019, "indicator": "IQ.CPA.TRAN.XQ", "value": None},
    ])
    f = du.governance_asof(lk, "IND", date(2021, 3, 1))
    assert f["wgi_ge"] == 0.1 and f["governance_year"] == 2019
    assert f["cpia_pubs"] is None and f["cpia_tran"] is None and f["cpia_missing"] == 1
    assert du.governance_asof(lk, None, date(2021, 3, 1))["wgi_ge"] is None


def test_feature_row_uses_only_information_known_at_start():
    lk = du.governance_lookup([{"iso3": "LKA", "year": 2019, "indicator": "GOV_WGI_GE.EST", "value": -0.2}])
    d = {"contract_key": "C:1", "start_notice_date": date(2021, 2, 1), "duration_days": 200, "review_type": "Prior",
         "source_dataset": "DS00005"}
    c = {"project_id": "P132833", "procurement_category": "Civil Works", "procurement_method": "Request for Bids",
         "borrower_country_iso3": "LKA", "total_amount_usd": 1_000_000}
    p = {"board_approval_date": date(2020, 2, 1), "roads_pct": 82, "total_commitment_usd": 1e8}
    earlier = [date(2020, 5, 1), date(2020, 12, 1), date(2021, 2, 1), date(2021, 6, 1)]
    r = du.feature_row(d, c, p, "Invitation for Bids", earlier, lk, "IDA")
    assert r["n_earlier_tenders"] == 2          # strictly before the start date
    assert r["project_age_days"] == 366
    assert r["log_amount_usd"] == pytest.approx(6.0)
    assert r["wgi_ge"] == -0.2 and r["fy_month"] == 8   # February = 8th month of the July-June fiscal year
    assert set(du.CATEGORICAL + du.NUMERIC) <= set(r)


def test_time_split_has_no_train_rows_on_or_after_cutoff():
    rows = [{"start_notice_date": date(2020, 1, i + 1)} for i in range(10)] + [{"start_notice_date": date(2020, 1, 9)}]
    train, test, cutoff = du.time_split(rows, test_share=0.2)
    assert all(r["start_notice_date"] < cutoff for r in train)
    assert all(r["start_notice_date"] >= cutoff for r in test)
    assert len(train) + len(test) == len(rows)


def test_baseline_backs_off_to_category_then_global():
    rows = ([{"procurement_category": "Works", "procurement_method": "RFB", "duration_days": d} for d in (100, 200, 300, 400, 500)]
            + [{"procurement_category": "Works", "procurement_method": "RFQ", "duration_days": 50}]
            + [{"procurement_category": "Goods", "procurement_method": "RFQ", "duration_days": 10}])
    b = du.GroupQuantileBaseline(q=0.5, min_n=5).fit(rows)
    assert b.predict_one({"procurement_category": "Works", "procurement_method": "RFB"}) == 300
    # RFQ has 1 row (< 5) and Works has 6 rows -> category median
    assert b.predict_one({"procurement_category": "Works", "procurement_method": "RFQ"}) == 250
    assert b.predict_one({"procurement_category": "Other", "procurement_method": "X"}) == 200   # global median


def test_metrics():
    y, p = [100, 200, 300], [110, 190, 330]
    assert du.mae(y, p) == pytest.approx(50 / 3)
    assert du.coverage(y, [150, 150, 400]) == pytest.approx(2 / 3)
    assert du.pinball([10], [0], 0.8) == pytest.approx(8.0)
    assert du.pinball([0], [10], 0.8) == pytest.approx(2.0)
    m = du.evaluate(y, p, p80=[150, 250, 350], p20=[50, 250, 250])
    assert m["coverage_p80"] == 1.0 and m["share_below_p20"] == pytest.approx(1 / 3)


def test_evaluate_by_country():
    rows = [{"country_iso3": "IND", "duration_days": 100}, {"country_iso3": "MLI", "duration_days": 300}]
    out = du.evaluate_by(rows, [120, 300], "country_iso3", p80=[200, 250])
    assert out["IND"]["mae_p50"] == 20 and out["MLI"]["coverage_p80"] == 0.0


def test_promotion_rule():
    assert du.promote({"mae_p50": 90, "coverage_p80": 0.8}, {"mae_p50": 100})[0]
    assert not du.promote({"mae_p50": 110, "coverage_p80": 0.8}, {"mae_p50": 100})[0]
    assert not du.promote({"mae_p50": 50, "coverage_p80": 0.5}, {"mae_p50": 100})[0]   # badly calibrated
    assert du.promote({"mae_p50": 50, "coverage_p80": 0.75}, None)[0]


def test_days_to_text():
    assert du.days_to_text(152, 213, 304) == ("about 7 months (typically 5–10; 1 in 5 tenders like this takes longer "
                                              "than 10 months)")


# --- re-bid labels and rates -----------------------------------------------------------------------------------------

def _n(t, d, status="Published", desc="", text=""):
    return {"notice_type": t, "notice_date": d, "notice_status": status, "bid_description": desc, "notice_text": text}


def test_rebid_label_reasons():
    assert labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1))]) ==         {"rebid": False, "cancellation": False, "rebid_or_cancellation": False, "reasons": []}
    two = labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1)), _n("Invitation for Bids", date(2020, 4, 1))])
    assert two["rebid"] and two["reasons"] == ["ifb_notices:2"]
    # a same-week correction is not a re-bid
    assert not labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1)),
                                    _n("Invitation for Bids", date(2020, 1, 8))])["rebid"]
    assert labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1), status="Cancelled")])["reasons"] == ["notice_cancelled"]
    assert labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1), desc="Re-tender of Package 3")])["rebid"]
    assert labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1), text="Avis de relance de l'appel")])["rebid"]
    cancelled = labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1))], [{"is_cancelled": True}])
    assert cancelled["reasons"] == ["plan:cancelled"]
    # a cancellation is reported separately: it is NOT a re-bid, only part of "re-bid or cancellation"
    assert (cancelled["rebid"], cancelled["cancellation"], cancelled["rebid_or_cancellation"]) == (False, True, True)
    both = labels.tender_label([_n("Invitation for Bids", date(2020, 1, 1), desc="Re-tender of Package 3")],
                               [{"is_cancelled": True, "mentions_rebid": False}])
    assert both["rebid"] and both["cancellation"] and both["reasons"] == ["text:re-tender", "plan:cancelled"]


def test_rebid_patterns_avoid_common_false_hits():
    assert labels.mentions_rebid("Bidders must submit a bid security") is None
    assert labels.mentions_rebid("rebidding was required after all bids exceeded the estimate")


def test_hierarchical_rates_shrink_small_groups_toward_parent():
    rows = ([{"procurement_category": "Works", "procurement_method": "RFB", "country_iso3": "IND", "rebid": i < 10}
             for i in range(100)]
            + [{"procurement_category": "Works", "procurement_method": "RFB", "country_iso3": "MLI", "rebid": True}
               for _ in range(2)])
    m = labels.HierarchicalRates(label="rebid").fit(rows)
    mli = m.predict_one({"procurement_category": "Works", "procurement_method": "RFB", "country_iso3": "MLI"})
    ind = m.predict_one({"procurement_category": "Works", "procurement_method": "RFB", "country_iso3": "IND"})
    assert mli["n"] == 2 and mli["positives"] == 2
    assert 0.1 < mli["rate"] < 0.3              # 2/2 raw, shrunk strongly toward the ~12% parent
    assert ind["rate"] == pytest.approx(0.1, abs=0.02)
    assert mli["ci90"][0] < mli["rate"] < mli["ci90"][1]
    unseen = m.predict_one({"procurement_category": "Goods", "procurement_method": "RFQ", "country_iso3": "CHN"})
    assert unseen["level"] == "all"
    assert "of 2 similar tenders" in labels.rate_to_text(mli)


# --- amendment score -------------------------------------------------------------------------------------------------

def test_amendment_score_counts_known_factors_and_reports_unknowns():
    s = amendment.score({"category": "Civil Works", "amount_usd": 25e6, "category_p75_usd": 5e6, "p80_days": 400,
                         "wgi_ge": None, "latest_isr_min_score": 4, "n_earlier_amendment_events": 1})
    assert s["score"] == 20 + 10 + 15 + 20
    assert s["unknown"] == ["weak_government_effectiveness"]
    assert s["max_known"] == 100 - 15
    assert "not a probability" in s["label"]
    assert amendment.score({})["score"] == 0 and len(amendment.score({})["unknown"]) == len(amendment.FACTORS)


# --- the sklearn pipeline and MLflow wrapper (runs locally: scikit-learn is installed) ------------------------------

def test_quantile_pipeline_fits_with_unknown_categories_and_missing_values():
    import random

    import numpy as np
    import pandas as pd

    from src.models.serving import QuantileDurationModel, build_pipeline
    random.seed(0)
    rows = []
    for i in range(300):
        cat = random.choice(["Civil Works", "Goods"])
        rows.append({"procurement_category": cat, "procurement_method": "RFB", "start_notice_type": "IFB",
                     "country_iso3": random.choice(["IND", "MLI"]), "lending_type": "IDA",
                     "log_amount_usd": random.uniform(4, 8), "project_age_days": random.choice([None, 300.0]),
                     "roads_pct": 90, "log_project_commitment_usd": 8.0, "n_earlier_tenders": i % 7, "fy_month": 3,
                     "wgi_ge": None, "wgi_rq": 0.1, "wgi_rl": 0.0, "wgi_cc": 0.0, "cpia_pubs": None, "cpia_tran": None,
                     "cpia_missing": 1, "y": (400 if cat == "Civil Works" else 150) + random.gauss(0, 40)})
    df = pd.DataFrame(rows).astype({c: float for c in du.NUMERIC})
    models = {q: build_pipeline(du.CATEGORICAL, du.NUMERIC, quantile=v, max_iter=50).fit(df[du.CATEGORICAL + du.NUMERIC], df.y)
              for q, v in (("p20", 0.2), ("p50", 0.5), ("p80", 0.8))}
    m = QuantileDurationModel(models, du.CATEGORICAL + du.NUMERIC)
    new = df.head(2)[du.CATEGORICAL + du.NUMERIC].copy()
    new.loc[new.index[0], "country_iso3"] = "XXX"            # unseen category must not crash
    out = m.predict(None, new)
    assert list(out.columns) == ["p20", "p50", "p80"]
    assert (np.diff(out.values, axis=1) >= 0).all()          # ordered range
    works = m.predict(None, df[df.procurement_category == "Civil Works"].head(20)[du.CATEGORICAL + du.NUMERIC])
    assert 300 < works.p50.median() < 500


# Real notice texts from the first full run of notebook 51 (2026-10-22), where 120 of 171 positives were false.
@pytest.mark.parametrize("text,expected", [
    ("Nom du Projet : Projet de Relance et de D&eacute;veloppement de la R&eacute;gion du Lac Tchad", None),
    ("PROJET DE RELANCE ET DE DEVELOPPEMENT DE LA REGION DU LAC TCHAD", None),
    ("Conditions Not awarded Lots within the REP will be subject to re-bidding. 2. The CDR now invites", None),
    ("Bangui, le RELANCE Avis d'Appel d'Offres National Ouvert N° P", "RELANCE"),
    ("phases III et IV du Projet PAAR en lots multiples (Relance).", "Relance"),
    ("SUPPLY OF HIGHWAY PATROLLING VEHICLE (Rebid)", "Rebid"),
    ("This is a re-tendering of Lot-1 and Lot-3 because their responses", "re-tender"),
    ("Akkarai (km 22/300) to Mamallapuram (km56/700) - Second Call", "Second Call"),
])
def test_rebid_wording_skips_project_names_and_conditions(text, expected):
    assert labels.mentions_rebid(text) == expected


def test_feature_row_accepts_timestamps_and_dates():
    from datetime import datetime
    d = {"contract_key": "C:1", "start_notice_date": datetime(2021, 3, 1, 0, 0), "duration_days": None}
    row = du.feature_row(d, {"procurement_category": "Works"}, {"board_approval_date": date(2020, 1, 1)},
                         "Invitation for Bids", [datetime(2020, 6, 1), datetime(2022, 1, 1)], {}, "IDA")
    assert row["project_age_days"] == (date(2021, 3, 1) - date(2020, 1, 1)).days and row["n_earlier_tenders"] == 1
    assert du.as_date(datetime(2021, 3, 1, 12, 0)) == date(2021, 3, 1)
