"""The Databricks App runs Python 3.11, which rejects a backslash inside an f-string expression ({...}); Python 3.12+
(local tests, serverless notebooks) accepts it. This caught the app crash of 2026-10-23 (databricks_tools.py)."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py_files():
    for d in ("src", "app"):
        for base, _, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(base, f)


def test_no_backslash_inside_fstring_expressions():
    bad = []
    for path in _py_files():
        src = open(path, encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FormattedValue):
                seg = ast.get_source_segment(src, node.value) or ""
                if "\\" in seg:
                    bad.append(f"{os.path.relpath(path, ROOT)}:{node.lineno}")
    assert not bad, bad
