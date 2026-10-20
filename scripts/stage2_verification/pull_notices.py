"""Stage 2 helper: all procurement notices per road project (paginated, resumable, polite)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http_util import get_json
fac = json.load(open('data/docfacets.json'))
OUT = 'data/notices_by_project.json'
out = json.load(open(OUT)) if os.path.exists(OUT) else {}
FL = "id,notice_type,noticedate,notice_status,project_id,bid_reference_no,bid_description,procurement_group,procurement_method_code,submission_deadline_date"
for pid in fac:
    if pid in out and 'error' not in out[pid]:
        continue
    try:
        rows, os_ = [], 0
        while True:
            d = get_json(f"https://search.worldbank.org/api/v2/procnotices?format=json&rows=500&os={os_}&project_id={pid}&fl={FL}", pause=0.7)
            b = d.get('procnotices') or []
            rows += b
            if len(b) < 500:
                break
            os_ += 500
        out[pid] = {"total": int(d.get('total') or 0), "rows": rows}
    except Exception as e:
        out[pid] = {"error": str(e)}
    json.dump(out, open(OUT, 'w'))
print(len(out), 'notices:', sum(len(v.get('rows', [])) for v in out.values()), 'errors:', sum('error' in v for v in out.values()))
