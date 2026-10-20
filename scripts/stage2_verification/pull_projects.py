"""Stage 2 verification helper: all projects from the Projects API (sector filtering is done locally)."""
import json, sys, time, urllib.request

OUT = sys.argv[1]
FL = ("id,project_name,countryshortname,countrycode,regionname,approvalfy,boardapprovaldate,closingdate,"
      "status,sector1,sector2,sector3,sector4,sector5,sector_namecode,idacommamt,ibrdcommamt,totalcommamt,lendinginstr,prodline,"
      "envassesmentcategorycode,impagency")
rows, os_ = {}, 0
while True:
    url = (f"https://search.worldbank.org/api/v2/projects?format=json&rows=500&os={os_}"
           f"&fl={FL}")
    with urllib.request.urlopen(url, timeout=120) as r:
        d = json.load(r)
    batch = d.get("projects", {})
    rows.update(batch)
    print(os_, len(batch), d.get("total"), flush=True)
    if len(batch) < 500:
        break
    os_ += 500
    time.sleep(1)
json.dump(rows, open(OUT, "w"))
print("total", len(rows))
