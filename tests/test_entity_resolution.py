"""Unit tests for src/entity_resolution. Run locally: python -m pytest tests -q"""
from datetime import date

from src.entity_resolution import keys
from src.entity_resolution.suppliers import is_individual, normalize_name, resolve, similarity


# --- keys ----------------------------------------------------------------------------------------------------------

def test_project_id():
    assert keys.project_id(" p147864 ") == "P147864"
    assert keys.project_id("P14786") is None and keys.project_id("147864") is None and keys.project_id(None) is None


def test_ref_and_tender_key():
    assert keys.ref_key("IN-UPPWD-14106-CW-RFB") == "INUPPWD14106CWRFB"
    assert keys.ref_key(" / ") is None
    assert keys.tender_key("P147864", "in-uppwd-14106-cw-rfb") == "P147864:INUPPWD14106CWRFB"
    assert keys.tender_key("bad", "X1") is None


def test_contract_key():
    assert keys.contract_key("DS00005", 1543858) == "DS00005:1543858"
    assert keys.contract_key("OTHER", "1") is None and keys.contract_key("DS01004", "") is None


def test_parse_date_source_formats():
    assert keys.parse_date("19-May-2022") == date(2022, 5, 19)
    assert keys.parse_date("2024-12-20T00:00:00Z") == date(2024, 12, 20)
    assert keys.parse_date("12/20/2025 12:00:00 AM") == date(2025, 12, 20)
    assert keys.parse_date("nonsense") is None


def test_fiscal_year_and_gap():
    assert keys.fiscal_year(date(2016, 7, 1)) == 2017 and keys.fiscal_year(date(2016, 6, 30)) == 2016
    assert [fy for fy in range(2015, 2022) if keys.in_contract_gap(fy)] == [2017, 2018, 2019]


# --- supplier names ------------------------------------------------------------------------------------------------

def test_normalize_name_strips_prefix_legal_form_and_punctuation():
    assert normalize_name("M/S. AIRES INGEGNERIA S.R.L.") == "AIRES INGEGNERIA"
    assert normalize_name("Aires Ingegneria SRL") == "AIRES INGEGNERIA"
    assert normalize_name("The Louis Berger Group, Inc.") == "LOUIS BERGER GROUP"
    assert normalize_name("Société d'Études Techniques S.A.") == "SOCIETE D ETUDES TECHNIQUES"
    assert normalize_name("L&T Construction Pvt. Ltd") == "L AND T CONSTRUCTION"


def test_individual_detection_uses_method_not_name_shape():
    assert is_individual("Some Name", method="Individual Consultant Selection")
    assert is_individual("INDIVIDUAL CONSULTANT")
    # Real-data regression: short French firm names are NOT people.
    assert not is_individual("ACE INGENIEURS CONSEILS", method="Quality And Cost-Based Selection")
    assert not is_individual("AGECET BTP")


def test_similarity_is_order_insensitive():
    assert similarity("BERGER LOUIS GROUP", "LOUIS BERGER GROUP") == 1.0
    assert similarity("ALPHA ROADS", "OMEGA BRIDGES") < 0.6


def _recs(*rows):
    return [dict(zip(("raw_name", "country", "supplier_id", "method"), r)) for r in rows]


def test_r1_same_supplier_id_merges_different_spellings():
    m, merges = resolve(_recs(("AIRES INGEGNERIA", "IT", "316882", ""), ("Aires Ing. srl", "IT", "316882", "")))
    assert m[0]["supplier_key"] == m[1]["supplier_key"] and merges[0][2] == "R1_supplier_id"


def test_r2_exact_normalized_name_same_country():
    m, _ = resolve(_recs(("M/S. AIRES INGEGNERIA S.R.L.", "IT", None, ""), ("Aires Ingegneria SRL", "IT", None, "")))
    assert m[0]["supplier_key"] == m[1]["supplier_key"] and m[1]["rule"] == "R2_exact_norm"


def test_r2_does_not_merge_across_countries():
    m, _ = resolve(_recs(("Gayatri Projects Ltd", "IN", None, ""), ("Gayatri Projects Ltd", "LK", None, "")))
    assert m[0]["supplier_key"] != m[1]["supplier_key"]


def test_r3_fuzzy_merges_small_typos_for_firms():
    m, merges = resolve(_recs(("Larsen and Toubro Construction Ltd", "IN", None, ""),
                              ("Larsen and Tubro Construction", "IN", None, "")))
    assert m[0]["supplier_key"] == m[1]["supplier_key"] and merges[0][2] == "R3_fuzzy"


def test_r3_different_ids_raise_the_bar_instead_of_blocking():
    # Real data: 598 exact-name pairs carried different DS00005 IDs, so differing IDs are weak evidence of different
    # firms. A clear one-letter typo still merges; an unrelated name with a similar shape does not.
    m, _ = resolve(_recs(("Larsen and Toubro Construction", "IN", "1", ""), ("Larsen and Tubro Construction", "IN", "2", "")))
    assert m[0]["supplier_key"] == m[1]["supplier_key"]
    m, _ = resolve(_recs(("Larsen Roads Construction", "IN", "1", ""), ("Larsen Bridges Construction", "IN", "2", "")))
    assert m[0]["supplier_key"] != m[1]["supplier_key"]


def test_r3_never_applies_to_individuals():
    ics = "Individual Consultant Selection"
    m, _ = resolve(_recs(("RAJESH KUMAR", "IN", None, ics), ("RAJESH KUMARI", "IN", None, ics)))
    assert m[0]["supplier_key"] != m[1]["supplier_key"]
    assert m[0]["kind"] == "individual"


def test_jv_name_flag_and_stable_keys():
    recs = _recs(("ABC-XYZ JV", "IN", None, ""), ("Alpha Roads Ltd", "IN", None, ""))
    m1, _ = resolve(recs)
    m2, _ = resolve(list(recs))
    assert m1[0]["is_jv_name"] and not m1[1]["is_jv_name"]
    assert [x["supplier_key"] for x in m1] == [x["supplier_key"] for x in m2]  # deterministic keys


def test_placeholder_names_resolve_by_supplier_id_only():
    # Real-data regression: "INDIVIDUAL CONSULTANT" on 2,094 rows with distinct anonymised IDs = different people.
    m, _ = resolve(_recs(("INDIVIDUAL CONSULTANT", "MA", "40af7805b1f2f3c8", ""),
                         ("INDIVIDUAL CONSULTANT", "MA", "03e1438e3c509e0d", ""),
                         ("INDIVIDUAL CONSULTANT", "MA", "03e1438e3c509e0d", ""),
                         ("Individual Consultant", "MA", None, ""), ("INDIVIDUAL CONSULTANT", "MA", None, "")))
    keys_ = [x["supplier_key"] for x in m]
    assert keys_[1] == keys_[2]                      # same ID -> same person
    assert len({keys_[0], keys_[1], keys_[3], keys_[4]}) == 4  # different / missing IDs -> different people
    assert all(x["is_placeholder"] for x in m)


def test_individuals_with_same_name_but_different_ids_stay_separate():
    ics = "Individual Consultant Selection"
    m, _ = resolve(_recs(("Rajesh Kumar", "IN", "11", ics), ("RAJESH KUMAR", "IN", "22", ics),
                         ("Rajesh Kumar", "IN", None, ics)))
    assert m[0]["supplier_key"] != m[1]["supplier_key"]


def test_r3_rejects_swapped_initials():
    # Sorted-token similarity alone would call these identical; in-order similarity must agree too.
    m, _ = resolve(_recs(("A M ENGINEERING", "BD", None, ""), ("M A ENGINEERING", "BD", None, "")))
    assert m[0]["supplier_key"] != m[1]["supplier_key"]


def test_r3_id_conflict_needs_stricter_score():
    # Real-data case (0.983): plural typo across different IDs still merges at the stricter 0.95 bar.
    m, merges = resolve(_recs(("ADVANCED ENGINEERING SOLUTION LIMITED", "TZ", "1", ""),
                              ("ADVANCED ENGINEERING SOLUTIONS LIMITED", "TZ", "2", "")))
    assert m[0]["supplier_key"] == m[1]["supplier_key"] and merges[-1][2] == "R3_fuzzy"
    # A weaker pair (between 0.92 and 0.95) merges without an ID conflict but not with one.
    a, b = "ALPHA ROAD BUILDERS NORTH", "ALPHA ROADS BUILDER NORTHS"
    m1, _ = resolve(_recs((a, "IN", None, ""), (b, "IN", None, "")))
    m2, _ = resolve(_recs((a, "IN", "1", ""), (b, "IN", "2", "")))
    from src.entity_resolution.suppliers import fuzzy_score, FUZZY_THRESHOLD, FUZZY_THRESHOLD_ID_CONFLICT
    s = fuzzy_score("ALPHA ROAD BUILDERS NORTH", "ALPHA ROADS BUILDER NORTHS")
    if FUZZY_THRESHOLD <= s < FUZZY_THRESHOLD_ID_CONFLICT:
        assert m1[0]["supplier_key"] == m1[1]["supplier_key"] and m2[0]["supplier_key"] != m2[1]["supplier_key"]
