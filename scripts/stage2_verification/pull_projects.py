"""Stage 2 verification helper: all projects from the Projects API (sector filtering is done locally)."""
import json, sys
from http_util import get_json  # retries with exponential backoff, honours 429 Retry-After

OUT = sys.argv[1]
FL = ("id,project_name,countryshortname,countrycode,regionname,approvalfy,boardapprovaldate,closingdate,"
      "status,sector1,sector2,sector3,sector4,sector5,sector_namecode,idacommamt,ibrdcommamt,totalcommamt,lendinginstr,prodline,"
      "envassesmentcategorycode,impagency")
rows, os_ = {}, 0
while True:
    url = (f"https://search.worldbank.org/api/v2/projects?format=json&rows=500&os={os_}"
           f"&fl={FL}")
    d = get_json(url, pause=1.0)
    batch = d.get("projects", {})
    rows.update(batch)
    print(os_, len(batch), d.get("total"), flush=True)
    if len(batch) < 500:
        break
    os_ += 500
json.dump(rows, open(OUT, "w"))
print("total", len(rows))
