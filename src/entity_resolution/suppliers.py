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
from collections import Counter, defaultdict
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
    """Accents removed AND non-Latin letters kept ("Société" -> "Societe", Arabic stays Arabic).
    Real-data regression: an ASCII-only version erased 68 Iraqi firm names written in Arabic, turning real firms into
    empty "placeholders"."""
    decomposed = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw):
    """'M/S. AIRES INGEGNERIA S.R.L.' -> 'AIRES INGEGNERIA'. Letters of any script are kept."""
    s = _ascii(raw).upper()
    s = re.sub(r"\bM\s*/\s*S\b\.?", " M/S ", s)
    # Join dotted abbreviations first ("S.R.L." -> "SRL", "S.A." -> "SA") so legal forms are recognised.
    s = re.sub(r"\b((?:[A-Z]\.){2,})", lambda m: m.group(1).replace(".", ""), s)
    s = "".join(ch if (ch.isalnum() or ch in "/& ") else " " for ch in s).replace("&", " AND ")
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
SHARED_ID_MIN_SIMILARITY = 0.6      # under one supplier_id, names less similar than this to the modal name don't merge
COMMON_NAME_MIN_IDS = 5             # a (name, country) used by this many different supplier_ids is a common name


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


# Business words (several languages). Used ONLY to decide whether a name is person-like for the common-name guard.
BUSINESS_WORDS = {
    "AND", "CONSTRUCTION", "CONSTRUCTIONS", "ENGINEERING", "ENGINEERS", "CONSULTING", "CONSULTANTS", "CONSULTANT",
    "CONSULTANCY", "CONSULT", "SERVICES", "SERVICE", "INFRASTRUCTURE", "INDUSTRIES", "ENTERPRISES", "ENTERPRISE",
    "GROUP", "HOLDING", "HOLDINGS", "TECHNOLOGIES", "TECHNOLOGY", "SYSTEMS", "SOLUTIONS", "INTERNATIONAL", "ASSOCIATES",
    "PROJECTS", "BUILDERS", "CONTRACTORS", "CONTRACTOR", "TRADING", "TRADERS", "SUPPLIERS", "BUREAU", "INSTITUTE",
    "UNIVERSITY", "FOUNDATION", "AGENCY", "MEDIA", "ROADS", "HIGHWAYS", "BRIDGE", "INFRA", "MOTORS", "WORKS", "CENTRE",
    "CENTER", "CONSTRUCTORA", "INGENIERIA", "INGEGNERIA", "INGENIERIE", "INGENIEURS", "INGENIEUR", "CONSEIL", "CONSEILS",
    "ENTREPRISE", "ETS", "SOCIETE", "ETUDES", "CABINET", "BTP", "GROUPE", "GROUPEMENT", "COMPANY", "CORPORATION",
    "ORGANIZATION", "ORGANISATION", "NETWORK", "LOGISTICS", "PRINT", "PRINTERS", "ELECTRIC", "ELECTRICALS", "TRAVELS",
}


def is_person_like(norm_name):
    """2-3 tokens of 3+ letters each and no business word: 'RAJ KUMAR' yes; 'FRED ENGINEERING', 'CAFI B' no."""
    toks = norm_name.split()
    return 2 <= len(toks) <= 3 and all(t.isalpha() and len(t) >= 3 for t in toks) and not set(toks) & BUSINESS_WORDS


# Function words (several languages), ignored when comparing distinctive words.
FUNCTION_WORDS = {"AND", "OF", "THE", "FOR", "DE", "DU", "DES", "LA", "LE", "LES", "ET", "EN", "Y", "E", "D", "L",
                  "DI", "DA", "DO", "DEL", "POUR", "AU", "AUX"}
DISTINCTIVE_MIN = 0.8   # a distinctive word needs a counterpart at least this similar in the other name


def _tokens(norm_name):
    """Tokens with runs of single letters joined ('K K ENTERPRISE' -> ['KK', 'ENTERPRISE']), legal forms dropped."""
    out, run = [], ""
    for t in norm_name.split():
        if len(t) == 1 and t.isalpha() and t not in ("Y", "E"):
            run += t
            continue
        if run:
            out.append(run)
            run = ""
        out.append(t)
    if run:
        out.append(run)
    return [t for t in out if t not in LEGAL_FORMS]


def distinctive_match(na, nb):
    """R3 guard (checkpoint c review of 50 fuzzy pairs): the overall similarity of two names can be high only because
    of shared generic words ('SHAANXI HUICAN CONSTRUCTION ENGINEERING' vs 'SHAANXI HUAXIANG CONSTRUCTION
    ENGINEERING'). So the distinctive words must agree too:
      1. the first distinctive word (not a business or function word) must match: exactly when either is 3 characters
         or shorter ('IQ' vs 'SI', 'BP' vs 'BT'), otherwise with similarity >= DISTINCTIVE_MIN;
      2. every other distinctive word of 4+ characters, in either name, needs a counterpart in the other name with
         similarity >= DISTINCTIVE_MIN, or must appear inside it ('INFOSOLUTIONS' vs 'INFO SOLUTIONS').
    Typos still pass ('CHAKLE'/'CHAKILE', 'TECHNILOGIES'/'TECHNOLOGIES', 'GHNAN'/'GHANA')."""
    ta, tb = _tokens(na), _tokens(nb)
    da = [t for t in ta if t not in BUSINESS_WORDS and t not in FUNCTION_WORDS and not t.isdigit()]
    db = [t for t in tb if t not in BUSINESS_WORDS and t not in FUNCTION_WORDS and not t.isdigit()]
    if not da or not db:
        return True   # nothing distinctive to compare; the other R3 checks decide
    fa, fb = da[0], db[0]
    if min(len(fa), len(fb)) <= 3:
        if fa != fb:
            return False
    elif SequenceMatcher(None, fa, fb).ratio() < DISTINCTIVE_MIN:
        return False

    def covered(t, others, joined):
        return t in joined or any(SequenceMatcher(None, t, u).ratio() >= DISTINCTIVE_MIN for u in others)
    ja, jb = "".join(ta), "".join(tb)
    return (all(covered(t, tb, jb) for t in da[1:] if len(t) >= 4)
            and all(covered(t, ta, ja) for t in db[1:] if len(t) >= 4))


def _numbers(name):
    """Numbers in a name: 'CHINA RAILWAY NO 13TH GROUP' -> {'13'}; 'HYDRAULIC CONSTR CO 24' -> {'24'}."""
    return set(re.findall(r"\d+", name or ""))


def resolve(records, stats=None):
    """Resolve supplier records into canonical suppliers.

    records: list of dicts with keys raw_name, country, supplier_id (str or None), method (optional).
    stats:   optional dict; filled with counts of merges each guard blocked (for the notebook report).
    Returns (mapping, merges):
      mapping: one dict per input record: supplier_key, norm_name, kind, is_jv_name, rule, score
      merges:  list of (i, j, rule, score) union decisions, for auditing / sampling / rule chains
    Guards added after the full-data run (checkpoint c): shared-ID, common-name, number, JV, distinctive-word and chain
    guards.
    """
    stats = stats if stats is not None else {}
    for k in ("R1_blocked_dissimilar_name_under_one_id", "R2_blocked_common_name", "R3_blocked_numbers_differ",
              "R3_blocked_jv_vs_non_jv", "R3_blocked_distinctive_word", "R3_blocked_chain_canonical"):
        stats.setdefault(k, 0)
    n = len(records)
    norm = [normalize_name(r.get("raw_name")) for r in records]
    country = [(r.get("country") or "").strip().upper() for r in records]
    sid = [str(r["supplier_id"]).strip() if r.get("supplier_id") not in (None, "") else None for r in records]
    indiv = [is_individual(r.get("raw_name"), r.get("method", "")) for r in records]
    placeholder = [nm in PLACEHOLDERS for nm in norm]
    jv = [bool(JV_PATTERN.search(_ascii(r.get("raw_name")).upper())) for r in records]
    uf, merges = _UnionFind(n), []

    # R1: same supplier_id. Shared-ID guard: under one ID, a record whose name is not similar to the ID's most common
    # name is NOT merged (full-data run: one cluster mixed "AJAY KUMAR", "RAJ KUMAR", "NEERAJ KUMAR", ...).
    # Placeholder names carry no name evidence, so they always merge by ID.
    by_sid = defaultdict(list)
    for i in range(n):
        if sid[i]:
            by_sid[sid[i]].append(i)
    for idx in by_sid.values():
        named = [i for i in idx if not placeholder[i]]
        modal = Counter(norm[i] for i in named).most_common(1)[0][0] if named else None
        anchor = next((i for i in idx if norm[i] == modal), idx[0])
        for j in idx:
            if j == anchor:
                continue
            if not placeholder[j] and modal is not None and similarity(norm[j], modal) < SHARED_ID_MIN_SIMILARITY:
                stats["R1_blocked_dissimilar_name_under_one_id"] += 1
                continue
            if uf.union(anchor, j):
                merges.append((anchor, j, "R1_supplier_id", 1.0))

    # R2: same cleaned name + same country. Never for placeholders. For individuals, only when the supplier_ids don't
    # contradict it. Common-name guard: a PERSON-LIKE (name, country) used by >= COMMON_NAME_MIN_IDS different
    # supplier_ids is a common name, not one firm ("RAJ KUMAR" in India), so different-ID and no-ID records with it are
    # not merged. Firms registered under several IDs ("FRED ENGINEERING", 6 IDs) still merge: the guard needs both.
    by_exact = defaultdict(list)
    for i in range(n):
        if not placeholder[i]:
            by_exact[(norm[i], country[i], indiv[i])].append(i)
    common = {k for k, idx in by_exact.items()
              if is_person_like(k[0]) and len({sid[i] for i in idx if sid[i]}) >= COMMON_NAME_MIN_IDS}
    for key, idx in by_exact.items():
        # Individuals: when the same name carries 2+ different supplier_ids, those are different people, and an
        # ID-less record with that name cannot be assigned to either. So nothing merges by name (R1 already joined
        # same-ID records). Bug fixed 2026-10-21: before, an ID-less record that came FIRST became the anchor and
        # chained two different IDs together (Spark's distinct() row order is arbitrary, so this could happen).
        if key[2] and len({sid[i] for i in idx if sid[i]}) > 1:
            continue
        for j in idx[1:]:
            a = idx[0]
            if key in common and not (sid[a] and sid[a] == sid[j]):
                stats["R2_blocked_common_name"] += 1
                continue
            if uf.union(a, j):
                merges.append((a, j, "R2_exact_norm", 1.0))

    # R3: compare one representative per (normalized name, country) within a block. Firms only (never individuals,
    # with or without an ID), no placeholders, no common names.
    reps = {}
    for (nm, c, ind), idx in by_exact.items():
        if not ind and (nm, c, ind) not in common:
            reps[(nm, c)] = idx[0]
    # Chain guard: each cluster keeps a canonical name (its shortest member name). An R3 merge must pass the threshold
    # between the two clusters' canonical names too, so A~B, B~C can't drag in a C that is unlike A.
    canon = {}
    def canon_of(i):
        r = uf.find(i)
        return canon.setdefault(r, norm[r])
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
                if s < thr or uf.find(ia) == uf.find(ib):
                    continue
                if _numbers(na) != _numbers(nb):      # "CO #24" vs "CO #25", "BUREAU 1" vs "BUREAU 16"
                    stats["R3_blocked_numbers_differ"] += 1
                    continue
                if jv[ia] != jv[ib]:                    # "JV PT SURYA ABADI" vs "PT SURYA ABADI"
                    stats["R3_blocked_jv_vs_non_jv"] += 1
                    continue
                if not distinctive_match(na, nb):       # "SHAANXI HUICAN ..." vs "SHAANXI HUAXIANG ..."
                    stats["R3_blocked_distinctive_word"] += 1
                    continue
                ca, cb = canon_of(ia), canon_of(ib)
                if fuzzy_score(ca, cb) < thr or _numbers(ca) != _numbers(cb):
                    stats["R3_blocked_chain_canonical"] += 1
                    continue
                uf.union(ia, ib)
                canon[uf.find(ia)] = min((ca, cb), key=lambda x: (len(x), x))
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
                          # Placeholder name AND no supplier_id: identity unknowable. Never merged (no rule can
                          # apply), and every supplier-history feature must be NULL for it, never 0.
                          "supplier_name_masked": placeholder[i] and not sid[i],
                          "is_jv_name": bool(JV_PATTERN.search(_ascii(records[i].get("raw_name")).upper())),
                          "rule": rule, "score": score}
    return mapping, merges
