#!/usr/bin/env python3
"""Simple smoke test: polls the local Flask app until ready, then
fetches /api/items, /api/results, and /api/stats and prints concise output.

No external deps required (uses urllib).
"""
import json
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

BASE = "http://127.0.0.1:5000"
ENDPOINTS = ["/api/items", "/api/results", "/api/stats"]


def fetch(path):
    url = BASE + path
    req = Request(url, headers={"User-Agent": "PetSwipeSmoke/1.0"})
    try:
        with urlopen(req, timeout=5) as r:
            data = r.read()
            return r.getcode(), data
    except HTTPError as e:
        return e.code, getattr(e, 'read', lambda: b'')()
    except URLError as e:
        return None, str(e).encode()
    except Exception as e:
        return None, str(e).encode()


# Poll until the server is up (max ~20s)
print("Waiting for server to respond at http://127.0.0.1:5000…")
ready = False
for i in range(40):
    code, _ = fetch("/api/items")
    if code == 200:
        ready = True
        break
    time.sleep(0.5)

if not ready:
    print("ERROR: Server did not respond in time.")
    sys.exit(2)

print("Server is up — running checks:\n")

# items
code, body = fetch("/api/items")
if code != 200:
    print(f"/api/items -> HTTP {code}")
    sys.exit(3)
try:
    items = json.loads(body)
    print(f"/api/items -> OK, items: {len(items)} (expected 100)")
except Exception as e:
    print("/api/items -> invalid JSON", e)
    sys.exit(4)

# results
code, body = fetch("/api/results")
if code != 200:
    print(f"/api/results -> HTTP {code}")
    sys.exit(5)
try:
    results = json.loads(body)
    rcount = len(results.get("results", [])) if isinstance(results, dict) else 0
    print(f"/api/results -> OK, results: {rcount}")
except Exception as e:
    print("/api/results -> invalid JSON", e)
    sys.exit(6)

# stats
code, body = fetch("/api/stats")
if code != 200:
    print(f"/api/stats -> HTTP {code}")
    sys.exit(7)
try:
    stats = json.loads(body)
    tv = stats.get("total_votes")
    ts = stats.get("total_sessions")
    ti = stats.get("total_items")
    print(f"/api/stats -> OK, total_votes={tv}, total_sessions={ts}, total_items={ti}")
except Exception as e:
    print("/api/stats -> invalid JSON", e)
    sys.exit(8)

print("\nSmoke test PASSED")
sys.exit(0)
