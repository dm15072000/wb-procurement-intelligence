"""Source country codes / names -> modern ISO3 (docs/04_data_sources.md §Country codes).

Plain-language summary: World Bank sources use World Bank country codes, not always ISO. Some are legacy codes for
countries that changed name or split (ZR = Zaire, now DR Congo), some are regions ("3W" = Western and Central Africa),
and DS01004 has country NAMES only. We map each to a modern ISO3 code with a documented reason, keep the original in
its own column, and report anything we cannot map confidently instead of guessing.

Every result is a dict:
  country_code_original  what the source said (code, or name for DS01004)
  country_iso3           modern ISO3, or None when it is not one country / not confidently mappable
  is_regional            True for regions, multi-country groupings and "World"
  region_name            the region's name when is_regional
  mapping_status         direct | legacy_code | name | regional | ambiguous | unmapped | missing
  mapping_reason         short human-readable reason (shown in the unmatched-code report)

The tables below were built from a full audit (2026-10-21) of every code in the Projects API (22,589 projects) and
every borrower/supplier code or name in DS00005, DS04691 and DS01004, with row counts in docs/04_data_sources.md.
"""

# Legacy World Bank codes for countries whose modern ISO2/ISO3 differs. Reason recorded per code.
LEGACY = {
    "ZR": ("COD", "Zaire -> Democratic Republic of the Congo (renamed 1997); WB still uses ZR"),
    "RY": ("YEM", "WB code for the Republic of Yemen (unified 1990); ISO is YE/YEM"),
    "GZ": ("PSE", "WB code for West Bank and Gaza; ISO 3166 is PS/PSE"),
    "TP": ("TLS", "East Timor (old ISO TP) -> Timor-Leste (ISO TL/TLS since 2002)"),
    "TW": ("TWN", "Taiwan, China; not in the WB country API, so it has no lending type or governance data"),
}

# YF = WB code for the Federal Republic of Yugoslavia / Serbia and Montenegro (2003-06), and Serbia after Montenegro's
# independence on 3 June 2006. Before that date the code covers both countries, so the project name decides.
YF_SPLIT_DATE = "2006-06-03"
YF_OVERRIDES = {  # checked by hand from project names (audit 2026-10-21)
    "P076764": ("MNE", "YF before 2006-06-03; project name says '(Montenegro)'"),
    "P088867": ("SRB", "YF before 2006-06-03; '...Serbia and Montenegro Component - Serbia Project'"),
}

# ISO 3166 territories that appear as supplier countries but are not in the WB country API (no WB row to join).
TERRITORIES = {
    "AI": "AIA", "CC": "CCK", "CK": "COK", "FK": "FLK", "GF": "GUF", "GG": "GGY", "GP": "GLP", "GS": "SGS",
    "IO": "IOT", "MQ": "MTQ", "MS": "MSR", "NF": "NFK", "NU": "NIU", "PM": "SPM", "RE": "REU", "SH": "SHN",
    "TF": "ATF", "UM": "UMI", "YT": "MYT",
}

# Codes we deliberately do NOT map to one country.
UNMAPPABLE = {
    "YU": "Yugoslavia, former (FY1950-91): predecessor of 7 states; no single modern country",
    "AN": "Netherlands Antilles (dissolved 2010): now CUW / SXM / BES; cannot tell which",
    "SU": "'Commonwealth of Independent States' (old USSR code): not one country",
    "XB": "'Stateless': no country",
}

# World Bank region / grouping codes seen in the audit. "AC" is Aral Sea in the Projects API but the source labels it
# "Africa" in DS04691, so the name given by the source wins over this table (see resolve_code).
REGIONS = {
    "1W": "World", "99": "World", "1M": "Multi-Regional",
    "3A": "Africa", "3W": "Western and Central Africa", "3E": "Eastern and Southern Africa", "3T": "Central Africa",
    "3S": "Southern Africa", "W1": "West Africa I", "W2": "West Africa II", "W4": "Sahel",
    "4E": "East Asia and Pacific", "4P": "Pacific Islands", "4S": "South East Asia", "4A": "Asia", "4M": "Mekong",
    "5M": "Middle East and North Africa", "5R": "Red Sea and Gulf of Aden",
    "6R": "Caribbean", "CB": "Caribbean", "6O": "OECS Countries", "6L": "Latin America", "6C": "Central America",
    "6A": "Andean Countries", "6B": "Mercosur",
    "7C": "Central Asia", "7E": "Europe and Central Asia", "7B": "Western Balkans", "7X": "Caucasus",
    "7A": "EU Accession Countries", "AC": "Aral Sea", "8S": "South Asia",
}

# DS01004 names that differ from the WB country API names.
NAME_ALIASES = {
    "vietnam": "VNM", "turkey": "TUR", "turkiye": "TUR", "congo, democratic republic of": "COD",
    "yemen, republic of": "YEM", "somalia": "SOM", "czech republic": "CZE", "taiwan, china": "TWN",
    "british indian ocean territory": "IOT", "guadeloupe": "GLP", "montserrat": "MSR", "cook islands": "COK",
    "united states minor outlying islands": "UMI", "reunion": "REU",
    # long official names used by DS01004 (the WB API uses short forms such as "Egypt, Arab Rep.")
    "somalia, federal republic of": "SOM", "egypt, arab republic of": "EGY", "lao people's democratic republic": "LAO",
    "iran, islamic republic of": "IRN", "korea, republic of": "KOR", "korea, democratic people's republic of": "PRK",
    "venezuela, republica bolivariana de": "VEN", "congo, republic of": "COG", "micronesia, federated states of": "FSM",
    "puerto rico": "PRI", "virgin islands, british": "VGB",
}
REGION_NAMES = {n.lower() for n in REGIONS.values()} | {
    "western africa", "eastern africa", "europe and central asia", "east asia and pacific",
    "middle east and north africa", "latin america", "commonwealth of independent states",
    # multi-country borrower labels on STEP rows that have no borrower code
    "latin america and caribbean", "drc - angola", "pacific 1", "pacific 2", "horn of africa", "southwest indian ocean"}

_MISSING = {None, "", "null", "none"}


def _result(original, iso3=None, status="direct", reason="", region=None):
    return {"country_code_original": original, "country_iso3": iso3, "is_regional": region is not None,
            "region_name": region, "mapping_status": status, "mapping_reason": reason}


def resolve_code(code, iso2_to_iso3, source_name=None, project_id=None, project_name=None, approval_date=None):
    """Map one source country code. iso2_to_iso3 comes from silver.countries (WB country API, aggregates excluded).
    project_* are only needed for YF; approval_date is an ISO string or date."""
    raw = None if code is None else str(code).strip()
    if raw is None or raw.lower() in _MISSING:
        return _result(None, status="missing", reason="no country code in the source record")
    c = raw.upper()
    if c in iso2_to_iso3:
        return _result(raw, iso2_to_iso3[c])
    if c == "YF":
        return _resolve_yf(raw, project_id, project_name, approval_date)
    if c in LEGACY:
        iso3, why = LEGACY[c]
        return _result(raw, iso3, "legacy_code", why)
    if c in TERRITORIES:
        return _result(raw, TERRITORIES[c], "direct", "ISO 3166 territory, not in the WB country API")
    if c in UNMAPPABLE:
        return _result(raw, status="unmapped", reason=UNMAPPABLE[c])
    if c in REGIONS:
        name = source_name if source_name and source_name.lower() not in _MISSING else REGIONS[c]
        return _result(raw, status="regional", reason="World Bank region / multi-country code", region=name)
    return _result(raw, status="unmapped", reason="code not in any mapping table")


def _resolve_yf(raw, project_id, project_name, approval_date):
    """Order: hand-checked override -> the project name names one country -> approval date after the split -> contract
    record (no project context; all STEP contract rows are FY2017+) -> otherwise ambiguous, reported."""
    if project_id in YF_OVERRIDES:
        iso3, why = YF_OVERRIDES[project_id]
        return _result(raw, iso3, "legacy_code", why)
    name = (project_name or "").lower()
    has_s, has_m = "serbia" in name, "montenegro" in name
    if has_m and not has_s:
        return _result(raw, "MNE", "legacy_code", "YF; project name names Montenegro only")
    if has_s and not has_m:
        return _result(raw, "SRB", "legacy_code", "YF; project name names Serbia only")
    if approval_date is not None and str(approval_date)[:10] >= YF_SPLIT_DATE:
        return _result(raw, "SRB", "legacy_code", "YF approved after Montenegro's independence (2006-06-03) = Serbia")
    if approval_date is None and project_id is None and project_name is None:
        return _result(raw, "SRB", "legacy_code", "YF on a STEP contract record (FY2017+, after 2006-06-03) = Serbia")
    return _result(raw, status="ambiguous",
                   reason="YF approved before 2006-06-03 or undated (Serbia and Montenegro); name names neither")


def resolve_name(name, name_to_iso3):
    """Map a country NAME (DS01004 has no codes). name_to_iso3: lower-case WB country API names -> ISO3."""
    raw = None if name is None else str(name).strip()
    if raw is None or raw.lower() in _MISSING:
        return _result(None, status="missing", reason="no country name in the source record")
    n = raw.lower()
    if n in name_to_iso3:
        return _result(raw, name_to_iso3[n], "name", "exact WB country name")
    if n in NAME_ALIASES:
        return _result(raw, NAME_ALIASES[n], "name", "known alternative spelling")
    if n == "world":
        return _result(raw, status="regional", reason="'World' is not a country", region="World")
    if n in REGION_NAMES:
        return _result(raw, status="regional", reason="region name, not a country", region=raw)
    return _result(raw, status="unmapped", reason="name not in WB country list or alias table")


def resolve(code, name, iso2_to_iso3, name_to_iso3, **project):
    """Code first (STEP datasets, Projects API); fall back to the name when there is no code (DS01004)."""
    r = resolve_code(code, iso2_to_iso3, source_name=name, **project)
    if r["mapping_status"] == "missing" and name:
        return resolve_name(name, name_to_iso3)
    return r


def resolve_project(codes, iso2_to_iso3, project_id=None, project_name=None, approval_date=None):
    """All codes of one project -> (iso3 list, region names, is_regional, worst status, reasons).
    A project is regional if ANY of its codes is a region code (multi-country operations list both)."""
    rs = [resolve_code(c, iso2_to_iso3, project_id=project_id, project_name=project_name, approval_date=approval_date)
          for c in (codes or [])]
    if not rs:
        rs = [resolve_code(None, iso2_to_iso3)]
    order = ["unmapped", "ambiguous", "missing", "legacy_code", "regional", "name", "direct"]
    worst = min((r["mapping_status"] for r in rs), key=order.index)
    return {
        "country_iso3_list": sorted({r["country_iso3"] for r in rs if r["country_iso3"]}),
        "region_names": sorted({r["region_name"] for r in rs if r["region_name"]}),
        "is_regional": any(r["is_regional"] for r in rs),
        "country_mapping_status": worst,
        "country_mapping_notes": sorted({f"{r['country_code_original']}: {r['mapping_reason']}" for r in rs
                                         if r["mapping_status"] not in ("direct", "name")}),
    }
