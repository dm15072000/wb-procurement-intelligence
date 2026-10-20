import json, re, collections, statistics as st
from datetime import datetime as dt
c = json.load(open('data/contracts_by_project.json')); n = json.load(open('data/notices_by_project.json'))
dd = json.load(open('data/docdates.json')); summ = {r['pid']: r for r in json.load(open('data/road_projects_summary.json'))}
norm = lambda s: re.sub(r'[^A-Z0-9]', '', (s or '').upper())
pd = lambda s, f: dt.strptime(s[:11] if f == '%d-%b-%Y' else s[:10], f)
print('== H1: notice -> contract join (all 140 road projects with contracts)')
tot = matched = 0; durs = []; per_type = collections.Counter(); blank_ref = 0
for pid, cv in c.items():
    rows = cv.get('rows', [])
    if not rows: continue
    starts = collections.defaultdict(list)
    for x in n[pid]['rows']:
        if x.get('notice_type') in ('Invitation for Bids', 'Request for Expression of Interest', 'General Procurement Notice') and x.get('bid_reference_no'):
            starts[norm(x['bid_reference_no'])].append((pd(x['noticedate'], '%d-%b-%Y'), x['notice_type']))
    seen = {}
    for r in rows: seen.setdefault(r['wb_contract_number'], r)
    for r in seen.values():
        tot += 1; k = norm(r.get('borrower_contract_reference_number'))
        if not k: blank_ref += 1; continue
        if k in starts:
            matched += 1; s, t = min(starts[k]); per_type[t] += 1
            durs.append((pd(r['contract_signing_date'], '%d-%b-%Y') - s).days)
print(f'unique contracts {tot}, blank ref {blank_ref}, matched to a start notice {matched} ({matched/tot:.1%})')
print('matched by start-notice type', dict(per_type))
if durs:
    print(f'duration days: median {st.median(durs)}, negative {sum(d<0 for d in durs)} ({sum(d<0 for d in durs)/len(durs):.1%}), >3y {sum(d>1095 for d in durs)}')
print('all notice types', collections.Counter(x.get('notice_type') for v in n.values() for x in v['rows']))
print('\n== H2: amendment / re-tender signals')
print('notice_status values', collections.Counter(x.get('notice_status') for v in n.values() for x in v['rows']))
refs = collections.Counter((pid, norm(x['bid_reference_no'])) for pid, v in n.items() for x in v['rows'] if x.get('notice_type') == 'Invitation for Bids' and x.get('bid_reference_no'))
print('IFB references with >1 IFB notice (possible re-tender):', sum(1 for k, m in refs.items() if m > 1), 'of', len(refs))
print('DS00005 columns:', sorted(next(r for v in c.values() for r in v.get('rows', [])).keys()))
print('\n== H3 (DS00005 side, road projects): review type', collections.Counter(r['review_type'] for v in c.values() for r in {x['wb_contract_number']: x for x in v.get('rows', [])}.values()))
print('FY of signing', sorted(collections.Counter(r['fiscal_year'] for v in c.values() for r in v.get('rows', [])).items()))
print('\n== H4: document order (closed projects with an ICR)')
ok = bad = 0; gaps = []; ppar_lag = []
for pid, v in dd.items():
    s = summ[pid]
    if not (s['status'] == 'Closed' and s['icr']): continue
    D = collections.defaultdict(list)
    for x in v['rows']:
        if x.get('docdt'): D[x['docty']].append(x['docdt'][:10])
    pad = min(D.get('Project Appraisal Document', ['9999'])); isr = sorted(D.get('Implementation Status and Results Report', []))
    icr = min(D['Implementation Completion and Results Report']); icrr = min(D.get('Implementation Completion Report Review', ['9999']))
    order = pad <= (isr[0] if isr else icr) and (not isr or isr[-1] <= icr) and icr <= icrr
    ok += order; bad += (not order)
    for a, b in zip(isr, isr[1:]): gaps.append((dt.fromisoformat(b) - dt.fromisoformat(a)).days)
print(f'projects in expected order PAD<=ISRs<=ICR<=ICRR: {ok} / {ok+bad}')
if gaps: print(f'median gap between ISRs {st.median(gaps)} days (n={len(gaps)})')
