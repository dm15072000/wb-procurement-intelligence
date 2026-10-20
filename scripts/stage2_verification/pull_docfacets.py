"""Stage 2 helper: document-type counts per FY14-21 road project (WDS facets)."""
import json, time, urllib.request
p = json.load(open('data/projects_all.json'))
nc = lambda v: [x.get('code') for x in (v.get('sector_namecode') or []) if isinstance(x, dict)]
ids = [k for k, v in p.items() if (v.get('approvalfy') or '').isdigit() and 2014 <= int(v['approvalfy']) <= 2021 and 'TI' in nc(v)]
out = {}
for i, pid in enumerate(ids):
    url = f"https://search.worldbank.org/api/v2/wds?format=json&rows=0&proid={pid}&fct=docty_exact"
    for a in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                d = json.load(r)
            break
        except Exception as e:
            time.sleep(10)
    f = d.get('documents', {}).get('facets', {}).get('docty_exact', {})
    out[pid] = {x['name']: x['count'] for x in f.values()}
    time.sleep(0.5)
json.dump(out, open('data/docfacets.json', 'w'))
print(len(out))
