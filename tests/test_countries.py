"""Country-code mapping (src/entity_resolution/countries.py). Cases come from the 2026-10-21 audit."""
from src.entity_resolution import countries as co

ISO2 = {"IN": "IND", "CN": "CHN", "ML": "MLI", "AL": "ALB", "LK": "LKA", "RS": "SRB", "ME": "MNE", "TR": "TUR"}
NAMES = {"india": "IND", "turkiye": "TUR", "serbia": "SRB"}


def test_direct_code_keeps_original():
    r = co.resolve_code("IN", ISO2)
    assert (r["country_iso3"], r["country_code_original"], r["mapping_status"], r["is_regional"]) == \
        ("IND", "IN", "direct", False)


def test_legacy_codes_map_with_reason():
    for code, iso3 in (("ZR", "COD"), ("RY", "YEM"), ("GZ", "PSE"), ("TP", "TLS"), ("TW", "TWN")):
        r = co.resolve_code(code, ISO2)
        assert r["country_iso3"] == iso3 and r["mapping_status"] == "legacy_code" and r["mapping_reason"]
        assert r["country_code_original"] == code


def test_yf_split_by_date_and_name():
    after = co.resolve_code("YF", ISO2, project_id="P158413", project_name="Serbia Roads", approval_date="2017-03-01")
    assert after["country_iso3"] == "SRB"
    mne = co.resolve_code("YF", ISO2, project_id="P076764", project_name="Structural Adjustment Credit (SAC) (Montenegro)",
                          approval_date="2002-08-08")
    assert mne["country_iso3"] == "MNE"
    srb = co.resolve_code("YF", ISO2, project_id="P075207", project_name="Transport Rehabilitation Project (Serbia)",
                          approval_date="2004-05-25")
    assert srb["country_iso3"] == "SRB"
    # override: the name mentions both countries but the component is Serbia's
    both = co.resolve_code("YF", ISO2, project_id="P088867",
                           project_name="Energy Community ... Serbia and Montenegro Component - Serbia Project",
                           approval_date="2005-06-30")
    assert both["country_iso3"] == "SRB"


def test_yf_pre_split_unnamed_is_ambiguous_not_guessed():
    r = co.resolve_code("YF", ISO2, project_id="P075343", project_name="Energy Efficiency Project", approval_date="2004-03-16")
    assert r["country_iso3"] is None and r["mapping_status"] == "ambiguous"
    undated = co.resolve_code("YF", ISO2, project_id="P079150", project_name="Public Expenditure Capacity Building")
    assert undated["mapping_status"] == "ambiguous"


def test_yf_undated_uses_name_and_contract_rows_are_serbia():
    assert co.resolve_code("YF", ISO2, project_id="P096592", project_name="Serbian Railway Reform")["country_iso3"] == "SRB"
    assert co.resolve_code("YF", ISO2, project_id="P074618",
                           project_name="Montenegro Environmental Infrastructure")["country_iso3"] == "MNE"
    assert co.resolve_code("YF", ISO2)["country_iso3"] == "SRB"   # STEP contract row: FY2017+


def test_unmappable_codes_are_reported_not_guessed():
    for code in ("YU", "AN", "SU", "XB", "QQ"):
        r = co.resolve_code(code, ISO2)
        assert r["country_iso3"] is None and r["mapping_status"] == "unmapped" and r["mapping_reason"]


def test_region_codes_are_regional():
    r = co.resolve_code("3W", ISO2)
    assert r["is_regional"] and r["region_name"] == "Western and Central Africa" and r["country_iso3"] is None
    # the source's own label wins over the table (AC = "Aral Sea" in Projects, "Africa" in DS04691)
    assert co.resolve_code("AC", ISO2, source_name="Africa")["region_name"] == "Africa"
    assert co.resolve_code("AC", ISO2)["region_name"] == "Aral Sea"


def test_missing_and_null_strings():
    for v in (None, "", "null", " "):
        assert co.resolve_code(v, ISO2)["mapping_status"] == "missing"


def test_territory_not_in_wb_api():
    r = co.resolve_code("RE", ISO2)
    assert r["country_iso3"] == "REU" and not r["is_regional"]


def test_names_for_ds01004():
    assert co.resolve_name("Turkey", NAMES)["country_iso3"] == "TUR"      # alias
    assert co.resolve_name("Turkiye", NAMES)["country_iso3"] == "TUR"     # exact WB name
    assert co.resolve_name("Vietnam", NAMES)["country_iso3"] == "VNM"
    assert co.resolve_name("Western Africa", NAMES)["is_regional"]
    assert co.resolve_name("Atlantis", NAMES)["mapping_status"] == "unmapped"


def test_resolve_falls_back_to_name_when_no_code():
    assert co.resolve(None, "India", ISO2, NAMES)["country_iso3"] == "IND"
    assert co.resolve("CN", "whatever", ISO2, NAMES)["country_iso3"] == "CHN"


def test_resolve_project_multi_country_and_regional():
    r = co.resolve_project(["3W", "ML"], ISO2)
    assert r["country_iso3_list"] == ["MLI"] and r["is_regional"] and r["region_names"] == ["Western and Central Africa"]
    r = co.resolve_project(["YU"], ISO2)
    assert r["country_iso3_list"] == [] and r["country_mapping_status"] == "unmapped" and r["country_mapping_notes"]
    assert co.resolve_project([], ISO2)["country_mapping_status"] == "missing"
