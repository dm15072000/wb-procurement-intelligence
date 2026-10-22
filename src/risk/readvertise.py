"""Re-advertisement under a NEW reference: the "same package re-advertised" rule (unit-tested).

Plain-language summary: when a tender fails, the borrower often advertises the same package again under a new
reference number, usually without the word "re-bid". Example (P157054, Madhya Pradesh): "MP04WB-03: Upgradation and
resurfacing of 14 Gravel Roads in Bhind District" was advertised 7 times between May 2017 and June 2020. The
"second IFB with the same reference" rule cannot see this. This rule finds it from the package DESCRIPTION.

Rule (user decision 2026-10-22):
  * same project;
  * descriptions near-identical: same normalized text, or similarity >= 0.95 with the same distinctive words, AND the
    same numbers (package numbers, road counts, km): "MP04WB-03 ... 14 roads" never matches "MP04WB-04 ... 12 roads";
  * enough distinctive words: at least MIN_DISTINCTIVE words that are not generic procurement words, and at least
    MIN_CHARS characters, so "Supply of office equipment" never chains;
  * the EARLIER tender has no matched contract, and the later one starts >= MIN_GAP_DAYS after it;
  * a group whose identical descriptions start less than MIN_GAP_DAYS apart is parallel lots, not a re-advertisement,
    and is skipped (counted).
The earlier attempt is flagged `re_advertised_new_ref`. Chains that end in a signed contract also give the duration
from the FIRST notice of the chain.
"""
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

MIN_GAP_DAYS = 30
MIN_CHARS = 25
MIN_DISTINCTIVE = 3
SIMILARITY = 0.95

GENERIC = set("""
a an and the of for in on at to by with from under within into as or its their this that these those
supply supplies delivery deliver procurement procure purchase construction construct works work goods services service
consultancy consultant consultants consulting selection firm individual hiring hire recruitment recruit
rehabilitation rehabilitate maintenance improvement improvements upgradation upgrading upgrade installation install
project projects lot lots package packages contract contracts phase road roads district districts state
acquisition fourniture fournitures travaux de des la le les du et pour dans en au aux une un sur par
""".split())


def _fold(text):
    t = unicodedata.normalize("NFKD", (text or "").replace("œ", "oe"))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def normalize(desc):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _fold(desc)).split())


def numbers(desc):
    """Numbers as integers IN ORDER, so '04' == '4' but '03' != '04', and "Tender 4 ... Lot 5" != "Tender 5 ... Lot 4"
    (an unordered comparison matched those two in the precision sample of 2026-10-22)."""
    return tuple(int(n) for n in re.findall(r"\d+", normalize(desc)))


def distinctive(desc):
    return {w for w in normalize(desc).split() if len(w) >= 3 and not w.isdigit() and w not in GENERIC}


def eligible(desc):
    return len(normalize(desc)) >= MIN_CHARS and len(distinctive(desc)) >= MIN_DISTINCTIVE


def same_package(a, b):
    na, nb = normalize(a), normalize(b)
    if numbers(a) != numbers(b):
        return False
    if na == nb or na.replace(" ", "") == nb.replace(" ", ""):     # "MP04WB - 03" == "MP04 WB-03"
        return True
    return distinctive(a) == distinctive(b) and SequenceMatcher(None, na, nb).ratio() >= SIMILARITY


def find_chains(tenders):
    """tenders: dicts with tender_key, project_id, start_date (date), description, has_contract (bool),
    signing_date (date or None).
    Returns (flags, chains, report):
      flags:  {tender_key: {"re_advertised_new_ref": True, "later_tender_key", "chain_id"}} for flagged EARLIER attempts
      chains: list of chains (each a date-sorted list of tender dicts) with >= 2 rounds >= MIN_GAP_DAYS apart
      report: counts (eligible, skipped generic/short, parallel-lot groups skipped, flagged, chains, ending in contract)
    """
    report = defaultdict(int)
    by_project = defaultdict(list)
    for t in tenders:
        if not t.get("start_date"):
            continue
        if eligible(t.get("description")):
            by_project[t["project_id"]].append(t)
            report["eligible"] += 1
        else:
            report["skipped_short_or_generic"] += 1
    flags, chains = {}, []
    for pid, ts in by_project.items():
        # group near-identical descriptions (single-link within the project)
        groups = []
        for t in sorted(ts, key=lambda x: x["start_date"]):
            for g in groups:
                if same_package(g[0]["description"], t["description"]):
                    g.append(t)
                    break
            else:
                groups.append([t])
        for g in groups:
            if len(g) < 2:
                continue
            g.sort(key=lambda x: x["start_date"])
            gaps = [(b["start_date"] - a["start_date"]).days for a, b in zip(g, g[1:])]
            if any(d < MIN_GAP_DAYS for d in gaps):
                report["parallel_lot_groups_skipped"] += 1   # same text started within 30 days: separate lots
                continue
            chain_id = f"{pid}:{g[0]['tender_key'].split(':', 1)[1]}"
            chains.append(g)
            report["chains"] += 1
            report["chain_rounds"] += len(g)
            if any(t.get("has_contract") for t in g):
                report["chains_ending_in_contract"] += 1
            for earlier, later in zip(g, g[1:]):
                if not earlier.get("has_contract"):
                    flags[earlier["tender_key"]] = {"re_advertised_new_ref": True, "chain_id": chain_id,
                                                    "later_tender_key": later["tender_key"]}
                else:
                    report["earlier_round_had_contract_not_flagged"] += 1
    report["flagged"] = len(flags)
    return flags, chains, dict(report)


def chain_durations(chains, durations):
    """Duration from the FIRST notice of a re-advertisement chain to the signing date, for chain rounds that ended in a
    signed contract. durations: {tender_key: [duration rows]} (one tender can have several contracts, e.g. lots); each
    row has contract_key, review_type, duration_days, start_notice_date, signing_date (the original per-tender
    duration). Returns one row per contract with BOTH durations; the original is never overwritten."""
    rows = []
    for g in chains:
        first = g[0]["start_date"]
        for i, t in enumerate(g):
            if i == 0:
                continue
            for d in durations.get(t["tender_key"], []):
                chain_days = (d["signing_date"] - first).days
                rows.append({"tender_key": t["tender_key"], "contract_key": d.get("contract_key"),
                             "review_type": d.get("review_type"), "rounds_before": i, "chain_first_tender_key":
                             g[0]["tender_key"], "first_notice_date": first,
                             "start_notice_date": d["start_notice_date"], "signing_date": d["signing_date"],
                             "duration_days": d["duration_days"], "duration_days_from_first_notice": chain_days,
                             "added_days": chain_days - d["duration_days"]})
    return rows
