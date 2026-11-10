"""Fetch specific Freesound sounds by id into a directory, with a provenance sidecar.

Token auth reaches previews only; full-quality download is an OAuth2 scope and
returns 401. The preview is a lossy re-encode of the uploader's original, so the
sidecar records both what was fetched and what the original is — the ledger must
never imply a master shipped when a preview did.

Usage: python3 freesound_fetch.py <out_dir> <id> [<id> ...]
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".claude" / "tools"))
from cli_usage import usage

import json
import os
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FIELDS = "id,name,type,license,username,url,duration,channels,samplerate,bitdepth,filesize,previews"


def load_key():
    p = os.path.join(os.path.expanduser("~"), ".config", "freesound", "api_key")
    if not os.path.isfile(p):
        print(f"ERROR: no Freesound key at {p}", file=sys.stderr)
        raise SystemExit(2)
    return open(p).read().strip()


def main():
    if len(sys.argv) < 3:
        print(usage(__doc__))
        raise SystemExit(1)

    out_dir = sys.argv[1]
    ids = sys.argv[2:]
    os.makedirs(out_dir, exist_ok=True)
    token = load_key()

    records = []
    for sid in ids:
        req = urllib.request.Request(
            f"https://freesound.org/apiv2/sounds/{sid}/?fields={FIELDS}",
            headers={"Authorization": f"Token {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                m = json.load(r)
        except urllib.error.HTTPError as e:
            print(f"  [{sid}] REQUEST FAILED")
            print(f"  HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}", file=sys.stderr)
            continue

        lic = m.get("license", "")
        # Match the canonical instrument URL — the page prose renders bare "CC0"
        # and collection names containing "Attribution" produce false positives.
        if "publicdomain/zero/1.0" not in lic:
            print(f"  [{sid}] REFUSED — license is not CC0: {lic}")
            continue

        previews = m.get("previews") or {}
        url = previews.get("preview-hq-ogg")
        if not url:
            print(f"  [{sid}] no hq-ogg preview — skipped")
            continue

        dest = os.path.join(out_dir, f"{sid}.ogg")
        urllib.request.urlretrieve(url, dest)
        size = os.path.getsize(dest)
        print(f"  [{sid}] {m.get('name')} -> {os.path.basename(dest)} ({size} bytes)")
        print(f"        original: .{m.get('type')} {m.get('samplerate')}Hz "
              f"{m.get('channels')}ch {m.get('bitdepth')}bit {m.get('filesize')} bytes")

        records.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "author": m.get("username"),
            "license": "CC0-1.0",
            "license_url": lic,
            "source_url": m.get("url"),
            "fetched_file": os.path.basename(dest),
            "fetched_variant": "preview-hq-ogg",
            "fetched_url": url,
            "original_format": m.get("type"),
            "original_samplerate": m.get("samplerate"),
            "original_channels": m.get("channels"),
            "original_bitdepth": m.get("bitdepth"),
            "duration": m.get("duration"),
        })

    side = os.path.join(out_dir, "provenance.json")
    with open(side, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)
    print(f"\n{len(records)} fetched. Provenance sidecar: {side}")


if __name__ == "__main__":
    main()
