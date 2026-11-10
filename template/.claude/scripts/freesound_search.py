"""Search Freesound for CC0 sounds and print ledger-ready metadata.

The CC0 filter is load-bearing: Freesound is mixed-license and an unfiltered
search returns attribution-bearing sounds indistinguishable from CC0 ones at
download time. Query terms AND together, so 3+ words routinely returns count 0 —
start with two.

Key lives OUTSIDE the repo at ~/.config/freesound/api_key.

Usage: python3 freesound_search.py "cartoon punch" ["comic impact" ...]
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude" / "tools"))
from cli_usage import usage

import json
import os
import urllib.parse
import urllib.request

# Windows consoles default to cp1252, which raises on accented characters in
# uploader-supplied sound names. Replace rather than crash mid-listing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

API = "https://freesound.org/apiv2/search/text/"
CC0 = 'license:"Creative Commons 0"'
FIELDS = "id,name,license,username,duration,channels,samplerate,type,tags,url,previews"

# Metadata-decidable shortlist gates. Timbre and fit are NOT decidable here —
# those are the user's call at audition time (provenance.md §Audition).
MAX_DURATION = 1.2   # seconds; a one-shot past ~500ms smears when hits repeat fast
MIN_DURATION = 0.05


def key_path():
    return os.path.join(os.path.expanduser("~"), ".config", "freesound", "api_key")


def load_key():
    p = key_path()
    if not os.path.isfile(p):
        print(f"ERROR: no Freesound key at {p}", file=sys.stderr)
        raise SystemExit(2)
    with open(p) as fh:
        return fh.read().strip()


def search(token, query, page_size=25):
    params = {
        "query": query,
        "filter": CC0,
        "fields": FIELDS,
        "page_size": str(page_size),
        "sort": "score",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}", file=sys.stderr)
        return None


def main():
    queries = sys.argv[1:]
    if not queries:
        print(usage(__doc__))
        raise SystemExit(1)

    token = load_key()
    seen = {}

    for q in queries:
        data = search(token, q)
        if data is None:
            print(f"\n### {q!r} — REQUEST FAILED")
            continue
        count = data.get("count", 0)
        results = data.get("results") or []
        print(f"\n### {q!r} — count={count}, returned={len(results)}")
        if count == 0:
            print("  (zero results — widen by dropping a term, not by rewording)")
            continue

        kept = 0
        for r in results:
            dur = r.get("duration") or 0
            if not (MIN_DURATION <= dur <= MAX_DURATION):
                continue
            sid = r.get("id")
            if sid in seen:
                continue
            seen[sid] = r
            kept += 1
            print(f"  [{sid}] {r.get('name')}")
            print(f"      {dur:.2f}s | {r.get('channels')}ch | {r.get('samplerate')}Hz "
                  f"| {r.get('type')} | by {r.get('username')} | {r.get('license')}")
            tags = r.get("tags") or []
            print(f"      tags: {', '.join(tags[:12])}")
            print(f"      url: {r.get('url')}")
        print(f"  -> {kept} within duration gate {MIN_DURATION}-{MAX_DURATION}s")

    print(f"\n=== {len(seen)} unique candidate(s) across all queries ===")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                       "scratch", "freesound_candidates.json")
    out = os.path.normpath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(seen, fh, indent=2)
    print(f"Full metadata written to {out}")


if __name__ == "__main__":
    main()
