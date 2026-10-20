"""Polite JSON GET: exponential backoff, honours 429 Retry-After, fixed pause after each call."""
import json, random, time, urllib.error, urllib.request

UA = "Mozilla/5.0 (procurement-intelligence-research; contact via repo owner)"

def get_json(url, max_tries=7, base=5, pause=1.5, timeout=120):
    for attempt in range(max_tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            time.sleep(pause)
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = float(e.headers.get("Retry-After") or base * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            wait = base * 2 ** attempt
        time.sleep(min(wait, 300) + random.uniform(0, 1))
    raise RuntimeError(f"gave up after {max_tries} tries: {url}")
