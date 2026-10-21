"""Polite HTTP client for World Bank APIs (CLAUDE.md rule).

Plain-language summary: every external call sends an explicit User-Agent (Finances One returns HTTP 403 to Python's
default one), pauses after each request, and on HTTP 429 / 5xx or a network error waits and retries, doubling the wait
each time and honouring the server's Retry-After header. This keeps the pipeline from getting rate-limited.
"""
import json
import random
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (procurement-intelligence-research)"
RETRY_CODES = (429, 500, 502, 503, 504)


def backoff_wait(attempt, base=5.0, retry_after=None, cap=300.0):
    """Seconds to wait before retry number `attempt` (0-based): Retry-After if the server sent one, else base * 2^attempt."""
    if retry_after not in (None, ""):
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return min(base * 2 ** attempt, cap)


def get(url, *, as_json=True, max_tries=7, base=5.0, pause=1.5, timeout=180, opener=None, sleep=time.sleep):
    """GET with retries. `opener` and `sleep` can be swapped in tests."""
    opener = opener or urllib.request.urlopen
    last = None
    for attempt in range(max_tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with opener(req, timeout=timeout) as r:
                body = r.read()
            sleep(pause)
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES:
                raise
            last = e
            wait = backoff_wait(attempt, base, e.headers.get("Retry-After") if e.headers else None)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            wait = backoff_wait(attempt, base)
        sleep(wait + random.uniform(0, 1))
    raise RuntimeError(f"gave up after {max_tries} tries: {url} ({last})")


# --- paginators for the verified APIs (docs/04_data_sources.md) -------------------------------------------------------

FONE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice"
FONE_RESOURCES = {"DS00005": "RS00005", "DS01004": "RS00934",  # verified in Stage 2
                  "DS04691": "RS03933"}                        # verified 2026-10-21 (FY2017-FY2019)


def fone_pages(dataset, resource=None, filter_expr=None, top=1000, **kw):
    """Yield pages (lists of records) from a Finances One dataset. Max 1,000 rows per call (verified)."""
    import urllib.parse
    resource = resource or FONE_RESOURCES[dataset]
    skip = 0
    while True:
        url = f"{FONE}?datasetId={dataset}&resourceId={resource}&type=json&top={top}&skip={skip}"
        if filter_expr:
            url += "&filter=" + urllib.parse.quote(filter_expr)
        batch = get(url, **kw).get("data") or []
        yield batch
        if len(batch) < top:
            return
        skip += top


def search_pages(endpoint, params, rows=500, list_key=None, **kw):
    """Yield pages from search.worldbank.org/api/v2/{projects|wds|procnotices}. `os` is the offset parameter."""
    import urllib.parse
    os_ = 0
    while True:
        q = dict(params, format="json", rows=rows, os=os_)
        d = get(f"https://search.worldbank.org/api/v2/{endpoint}?" + urllib.parse.urlencode(q, safe=",:", quote_via=urllib.parse.quote), **kw)
        data = d.get(list_key or endpoint) or {}
        batch = [v for k, v in data.items() if k != "facets"] if isinstance(data, dict) else list(data)
        yield batch
        if len(batch) < rows:
            return
        os_ += rows
