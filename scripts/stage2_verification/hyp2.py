import json, re, collections, statistics as st
from datetime import datetime as dt
c = json.load(open('data/contracts_by_project.json')); n = json.load(open('data/notices_by_project.json'))
dd = json.load(open('data/docdates.json')); summ = {r['pid']: r for r in json.load(open('data/road_projects_summary.json'))}
norm = lambda s: re.sub(r'[^A-Z0-9]', '', (s or '').upper()); D = lambda s: dt.strptime(s[:11], '%d-%b-%Y')
grp = collections.defaultdict(lambda: [0, 0, []])
for pid, cv in c.items():
    starts = collections.defaultdict(list)
    for x in n[pid]['rows']:
        if x.get('notice_type') in ('Invitation for Bids', 'Request for Expression of Interest') and x.get('bid_reference_no'):
            starts[norm(x['bid_reference_no'])].append((D(x['noticedate']), x['notice_type']))
    for r in {x['wb_contract_number']: x for x in cv.get('rows', [])}.values():
        key = (r['review_type'], r['procurement_category']); g = grp[key]; g[0] += 1
        k = norm(r['borrower_contract_reference_number'])
        if k in starts:
            g[1] += 1; s, t = min(starts[k]); g[2].append(((D(r['contract_signing_date']) - s).days, t))
print('H1 by review type x category: contracts / matched / match% / median days / negative%')
for k, (t, m, ds) in sorted(grp.items(), key=lambda kv: -kv[1][0]):
    if t < 100: continue
    dd_ = [d for d, _ in ds]
    print(f'{str(k):55s} {t:6d} {m:6d} {m/t:6.1%}  {st.median(dd_) if dd_ else "-":>6}  {(sum(d<0 for d in dd_)/len(dd_)) if dd_ else 0:6.1%}')
neg = [t for g in grp.values() for d, t in g[2] if d < 0]; print('negative by start type', collections.Counter(neg))
print('\nH4 failures:')
for pid, v in dd.items():
    s = summ[pid]
    if not (s['status'] == 'Closed' and s['icr']): continue
    X = collections.defaultdict(list)
    for x in v['rows']:
        if x.get('docdt'): X[x['docty']].append(x['docdt'][:10])
    pad = min(X.get('Project Appraisal Document', ['none'])); isr = sorted(X.get('Implementation Status and Results Report', []))
    icr = min(X['Implementation Completion and Results Report']); icrr = min(X.get('Implementation Completion Report Review', ['9999']))
    probs = []
    if pad == 'none': probs.append('no PAD in WDS')
    elif isr and pad > isr[0]: probs.append(f'PAD {pad} after first ISR {isr[0]}')
    if isr and isr[-1] > icr: probs.append(f'last ISR {isr[-1]} after ICR {icr}')
    if icr > icrr: probs.append(f'ICR {icr} after ICRR {icrr}')
    if probs: print(' ', pid, '; '.join(probs))
