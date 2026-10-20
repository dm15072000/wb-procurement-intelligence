import json, collections
p = json.load(open('data/projects_all.json')); f = json.load(open('data/docfacets.json'))
c = json.load(open('data/contracts_by_project.json')); cn = json.load(open('data/countries.json'))[1]
lt = {x['name']: x['lendingType']['id'] for x in cn}; lt.update({x['iso2Code']: x['lendingType']['id'] for x in cn})
def sec(v, i):
    s = v.get(f'sector{i}'); return s if isinstance(s, dict) else {}
def ti(v): return sum(int(sec(v, i).get('Percent') or 0) for i in range(1, 6) if sec(v, i).get('Name') == 'Rural and Inter-Urban Roads')
rows = []
for pid in f:
    v = p[pid]; cc = v.get('countrycode') or []; cc = cc[0] if isinstance(cc, list) and len(cc) == 1 else None
    rows.append(dict(pid=pid, country=v.get('countryshortname'), cc=cc, lend=lt.get(cc, '?'), fy=int(v['approvalfy']),
        status=v.get('status'), ti=ti(v), icr=f[pid].get('Implementation Completion and Results Report', 0) > 0,
        icrr=f[pid].get('Implementation Completion Report Review', 0) > 0, ncontracts=c[pid].get('count', 0),
        ncontract_ids=len({r['wb_contract_number'] for r in c[pid].get('rows', [])})))
json.dump(rows, open('data/road_projects_summary.json', 'w'))
for label, th in (('any roads share', 1), ('roads >=25%', 25)):
    q = [r for r in rows if r['ti'] >= th and r['status'] == 'Closed' and r['icr'] and r['ncontracts'] > 0]
    print(f'\n== {label}: qualifying projects = {len(q)} (multi-country excluded: {sum(1 for r in q if not r["cc"])})')
    by = collections.defaultdict(list)
    for r in q:
        if r['cc']: by[(r['country'], r['lend'])].append(r)
    ranked = sorted(by.items(), key=lambda kv: (-len(kv[1]), -sum(r['ncontract_ids'] for r in kv[1])))
    for (country, lend), rs in ranked[:15]:
        print(f'{country:28s} {lend}  projects={len(rs)}  contracts={sum(r["ncontract_ids"] for r in rs):5d}  ids={",".join(r["pid"] for r in rs)}')
