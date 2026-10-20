"""Stage 2 helper: dates of key document types per road project (for H4)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_util import get_json
fac = json.load(open('data/docfacets.json'))
OUT = 'data/docdates.json'
out = json.load(open(OUT)) if os.path.exists(OUT) else {}
KEEP = {"Project Appraisal Document", "Implementation Status and Results Report",
        "Implementation Completion and Results Report", "Implementation Completion Report Review",
        "Project Performance Assessment Report", "Project Paper"}
for pid in fac:
    if pid in out and 'error' not in out[pid]:
        continue
    try:
        rows, os_ = [], 0
        while True:
            d = get_json(f"https://search.worldbank.org/api/v2/wds?format=json&rows=500&os={os_}&proid={pid}&fl=id,docty,docdt,pdfurl,display_title", pause=0.7)
            docs = [v for k, v in (d.get('documents') or {}).items() if k != 'facets']
            rows += [{k: v.get(k) for k in ('id', 'docty', 'docdt', 'pdfurl')} for v in docs if v.get('docty') in KEEP]
            if len(docs) < 500:
                break
            os_ += 500
        out[pid] = {"rows": rows}
    except Exception as e:
        out[pid] = {"error": str(e)}
    json.dump(out, open(OUT, 'w'))
print(len(out), 'errors:', sum('error' in v for v in out.values()))
