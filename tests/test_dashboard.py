"""The KPI dashboard definition is consistent: every widget reads a declared dataset and its columns, every dataset
reads a view created in dashboards/kpi_views.sql, widgets fit the 6-column grid without overlapping, and the
committed JSON matches the builder."""
import importlib.util
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("build_dashboard", os.path.join(ROOT, "dashboards", "build_dashboard.py"))
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)


def test_widgets_use_declared_datasets_and_columns():
    d = bd.build()
    for page in d["pages"]:
        for item in page["layout"]:
            for q in item["widget"].get("queries", []):
                ds = q["query"]["datasetName"]
                assert ds in bd.DATASETS
                assert {f["name"] for f in q["query"]["fields"]} <= set(bd.DATASETS[ds][2])


def test_datasets_read_views_defined_in_sql():
    sql = open(os.path.join(ROOT, "dashboards", "kpi_views.sql"), encoding="utf-8").read()
    views = set(re.findall(r"CREATE OR REPLACE VIEW (\S+) AS", sql))
    for _, query, _ in bd.DATASETS.values():
        used = set(re.findall(r"procurement\.ops\.kpi_\w+", query))
        assert used and used <= views


def test_layout_fits_grid_without_overlap():
    for page in bd.build()["pages"]:
        cells = set()
        for item in page["layout"]:
            p = item["position"]
            assert 0 <= p["x"] and p["x"] + p["width"] <= 6
            box = {(x, y) for x in range(p["x"], p["x"] + p["width"]) for y in range(p["y"], p["y"] + p["height"])}
            assert not box & cells, (page["name"], item["widget"]["name"])
            cells |= box


def test_committed_json_matches_builder():
    with open(bd.OUT, encoding="utf-8") as f:
        assert json.load(f) == json.loads(json.dumps(bd.build()))


def test_usage_page_is_labelled_test_usage_and_targets_proposed():
    usage = next(p for p in bd.build()["pages"] if p["name"] == "usage")
    text = " ".join(" ".join(i["widget"].get("multilineTextboxSpec", {}).get("lines", [])) for i in usage["layout"])
    assert "TEST USAGE ONLY" in text and "PROPOSED" in text
