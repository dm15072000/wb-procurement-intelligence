"""Stage 2 helper: all DS00005 rows per road project (paginated), resumable; plus country lending types."""
import json, os, sys, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_util import get_json
fac = json.load(open('data/docfacets.json'))
OUT = 'data/contracts_by_project.json'
out = json.load(open(OUT)) if os.path.exists(OUT) else {}
B = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice?datasetId=DS00005&resourceId=RS00005&type=json&top=1000"
for pid in fac:
    if pid in out and 'error' not in out[pid]:
        continue
    try:
        rows, skip = [], 0
        while True:
            d = get_json(f"{B}&skip={skip}&filter=" + urllib.parse.quote(f"project_id='{pid}'"))
            batch = d.get("data") or []
            rows += batch
            if len(batch) < 1000:
                break
            skip += 1000
        out[pid] = {"count": d.get("count", 0), "rows": rows}
    except Exception as e:
        out[pid] = {"error": str(e)}
    json.dump(out, open(OUT, 'w'))
json.dump(get_json("https://api.worldbank.org/v2/country?format=json&per_page=400"), open('data/countries.json', 'w'))
print(len(out), 'with contracts:', sum(1 for v in out.values() if v.get('count')), 'errors:', sum('error' in v for v in out.values()))
