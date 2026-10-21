"""Supplier-name resolution (docs/02_data_model.md §4).

Plain-language summary: the same company appears under many spellings ("M/S. AIRES INGEGNERIA S.R.L.",
"Aires Ingegneria SRL"). We clean each name, then group records that are the same supplier using three rules, from
safest to riskiest:
  R1 same DS00005 supplier_id; R2 same cleaned name + same country; R3 very similar name + same country (firms only).
Placeholder names such as "INDIVIDUAL CONSULTANT" are not names: those records resolve by supplier_id only.
A wrong merge is worse than a missed one, so R3 is strict and never applied to individual people.
Every grouping decision is recorded with its rule and score so it can be audited.
"""
import hashlib
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

PREFIXES = {"M/S", "MS", "MESSRS", "THE"}
LEGAL_FORMS = {
    "PVT", "PRIVATE", "LTD", "LIMITED", "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED", "LLC", "LLP",
    "PLC", "SA", "SARL", "SAS", "SRL", "SPA", "GMBH", "AG", "BV", "NV", "JSC", "OJSC", "CJSC", "OOO", "PJSC", "PT",
    "TBK", "SDN", "BHD", "PTY", "SAU", "SL", "SARLU", "EURL", "AB", "OY", "KFT", "DOO", "SHPK",
}
JV_PATTERN = re.compile(r"\b(JV|J V|JOINT VENTURE|CONSORTIUM)\b")
FUZZY_THRESHOLD = 0.92


def _ascii(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def normalize_name(raw):
    """'M/S. AIRES INGEGNERIA S.R.L.' -> 'AIRES INGEGNERIA'."""
    s = _ascii(raw).upper()
    s = re.sub(r"\bM\s*/\s*S\b\.?", " M/S ", s)
    # Join dotted abbreviations first ("S.R.L." -> "SRL", "S.A." -> "SA") so legal forms are recognised.
    s = re.sub(r"\b((?:[A-Z]\.){2,})", lambda m: m.group(1).replace(".", ""), s)
    s = re.sub(r"[^A-Z0-9/& ]", " ", s).replace("&", " AND ")
    tokens = [t for t in s.split() if t]
    while tokens and tokens[0] in PREFIXES:
        tokens = tokens[1:]
    tokens = [t for t in tokens if t not in LEGAL_FORMS and t != "/"]
    return " ".join(tokens)


# Names that are not names: anonymised or missing. Measured on Stage 2 data: "INDIVIDUAL CONSULTANT" on 2,094 rows, each
# with its own anonymised supplier_id, i.e. many different people. These records resolve by supplier_id ONLY.
PLACEHOLDERS = {"", "INDIVIDUAL CONSULTANT", "INDIVIDUAL CONSULTANTS", "INDIVIDUAL", "CONSULTANT", "VARIOUS",
                "VARIOUS SUPPLIERS", "MULTIPLE SUPPLIERS", "N A", "NA", "TBD", "NOT APPLICABLE", "OTHERS", "UNKNOWN"}


def is_placeholder(raw):
    return normalize_name(raw) in PLACEHOLDERS


def is_individual(raw, method=""):
    """Person rather than firm: decided by the contract's procurement method (Individual Consultant Selection) or an
    individual placeholder name. A name-shape guess was tried and rejected: on real data it labelled short French firm
    names ("ACE INGENIEURS CONSEILS", "AGECET BTP") as people."""
    return "individual" in (method or "").lower() or normalize_name(raw).startswith("INDIVIDUAL")


FUZZY_THRESHOLD_ID_CONFLICT = 0.95  # stricter when the two records carry different DS00005 supplier_ids
MAX_BLOCK = 1500                    # blocks bigger than this are split by the second token


def similarity(a, b):
    """Token-order-insensitive similarity in [0, 1] (stdlib only)."""
    ta, tb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
    return SequenceMatcher(None, ta, tb).ratio()


def fuzzy_score(a, b):
    """R3 score = the LOWER of sorted-token and in-order similarity. Requiring both stops word swaps of initials
    ("A M ENGINEERING" vs "M A ENGINEERING": sorted 1.0, in-order lower) from counting as a match."""
    return min(similarity(a, b), SequenceMatcher(None, a, b).ratio())


def _block(norm):
    """Blocking key: first token of 3+ characters (cheap way to only compare plausible pairs)."""
    for t in norm.split():
        if len(t) >= 3:
            return t
    return norm[:3]


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[max(ri, rj)] = min(ri, rj)
            return True
        return False


def resolve(records):
    """Resolve supplier records into canonical suppliers.

    records: list of dicts with keys raw_name, country, supplier_id (str or None), method (optional).
    Returns (mapping, merges):
      mapping: one dict per input record: supplier_key, norm_name, kind, is_jv_name, rule, score
      merges:  list of (i, j, rule, score) union decisions, for auditing / sampling
    """
    n = len(records)
    norm = [normalize_name(r.get("raw_name")) for r in records]
    country = [(r.get("country") or "").strip().upper() for r in records]
    sid = [str(r["supplier_id"]).strip() if r.get("supplier_id") not in (None, "") else None for r in records]
    indiv = [is_individual(r.get("raw_name"), r.get("method", "")) for r in records]
    placeholder = [nm in PLACEHOLDERS for nm in norm]
    uf, merges = _UnionFind(n), []

    by_sid = defaultdict(list)
    for i in range(n):
        if sid[i]:
            by_sid[sid[i]].append(i)
    for idx in by_sid.values():
        for j in idx[1:]:
            if uf.union(idx[0], j):
                merges.append((idx[0], j, "R1_supplier_id", 1.0))

    # R2: same cleaned name + same country. Never for placeholders. For individuals, only when the supplier_ids don't
    # contradict it (two different people can share a common name).
    by_exact = defaultdict(list)
    for i in range(n):
        if not placeholder[i]:
            by_exact[(norm[i], country[i], indiv[i])].append(i)
    for idx in by_exact.values():
        for j in idx[1:]:
            a = idx[0]
            if indiv[a] and sid[a] and sid[j] and sid[a] != sid[j]:
                continue
            if uf.union(a, j):
                merges.append((a, j, "R2_exact_norm", 1.0))

    # R3: compare one representative per (normalized name, country) within a block. Firms only, no placeholders.
    reps = {}
    for (nm, c, ind), idx in by_exact.items():
        if not ind:
            reps[(nm, c)] = idx[0]
    blocks = defaultdict(list)
    for (nm, c), i in reps.items():
        blocks[(c, _block(nm))].append((nm, i))
    # Very large blocks (e.g. thousands of Indian firms starting "SHRI") are split by their second token, keeping
    # pairwise comparison affordable at full-dataset scale.
    for bk in [k for k, v in blocks.items() if len(v) > MAX_BLOCK]:
        for nm, i in blocks.pop(bk):
            toks = nm.split()
            blocks[bk + ((toks[1][:3] if len(toks) > 1 else ""),)].append((nm, i))
    for items in blocks.values():
        items.sort(key=lambda x: len(x[0]))
        for a in range(len(items)):
            la = len(items[a][0])
            for b in range(a + 1, len(items)):
                (na, ia), (nb, ib) = items[a], items[b]
                # Exact upper bound of SequenceMatcher.ratio(): 2*min/(sum). Sorted by length, so once the bound is
                # under the lowest threshold, every later name is even longer: stop.
                if 2 * la / (la + len(nb)) < FUZZY_THRESHOLD:
                    break
                # Different supplier_ids are weak evidence of different firms (on real data, 598 exact-name pairs had
                # different IDs), so they raise the bar instead of blocking the match.
                conflict = bool(sid[ia] and sid[ib] and sid[ia] != sid[ib])
                thr = FUZZY_THRESHOLD_ID_CONFLICT if conflict else FUZZY_THRESHOLD
                sm = SequenceMatcher(None, na, nb)
                if sm.real_quick_ratio() < thr or sm.quick_ratio() < thr:  # cheap exact upper bounds first
                    continue
                s = min(sm.ratio(), similarity(na, nb))  # == fuzzy_score(na, nb)
                if s >= thr and uf.union(ia, ib):
                    merges.append((ia, ib, "R3_fuzzy", round(s, 3)))

    rule_of = {}
    for i, j, rule, score in merges:
        rule_of[j] = (rule, score)
    clusters = defaultdict(list)
    for i in range(n):
        clusters[uf.find(i)].append(i)
    mapping, used = [None] * n, defaultdict(int)
    ordered = sorted(clusters.values(), key=lambda idx: sorted((norm[i], country[i], sid[i] or "") for i in idx))
    for idx in ordered:
        canon = min((norm[i] for i in idx if norm[i]), key=lambda s: (len(s), s), default="")
        seed = f"{canon}|{country[idx[0]]}|{min((sid[i] for i in idx if sid[i]), default='')}"
        used[seed] += 1
        if used[seed] > 1:  # e.g. two ID-less placeholder records in one country: still distinct suppliers
            seed += f"#{used[seed]}"
        key = "S" + hashlib.sha1(seed.encode()).hexdigest()[:10]
        for i in idx:
            rule, score = rule_of.get(i, ("root", 1.0))
            mapping[i] = {"supplier_key": key, "norm_name": norm[i], "canonical_name": canon,
                          "kind": "individual" if indiv[i] else "firm", "is_placeholder": placeholder[i],
                          "is_jv_name": bool(JV_PATTERN.search(_ascii(records[i].get("raw_name")).upper())),
                          "rule": rule, "score": score}
    return mapping, merges
