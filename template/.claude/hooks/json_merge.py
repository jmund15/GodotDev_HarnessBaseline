#!/usr/bin/env python3
"""Deep-merge JSON overlays into a target file (overlay wins; dicts recurse).

Single home for the "read JSON (or {}) -> deep-merge -> write indent=2 +
trailing newline" idiom shared by cloud-install.sh (settings.local.json + the
cloud user-level settings.json) and session_context_loader.py (.mcp.json
memory-server patch). Do not re-implement the idiom in either caller.

Merge semantics: dict values recurse; everything else (scalars, lists) is
overwritten by the overlay. Keys absent from the overlay are preserved. List
*replacement* (not union) is intentional — the overlay always specifies the
full desired list, which lets the relocated .mcp.json memory entry drop the
stale npx `args` instead of accumulating them.

CLI: python3 json_merge.py <target.json>   # overlay JSON read from stdin
Importable: deep_merge(base, overlay) / merge_into_file(path, overlay)
"""

import json
import sys
from pathlib import Path


def deep_merge(base, overlay):
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    result = dict(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def merge_into_file(path, overlay):
    p = Path(path)
    try:
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    merged = deep_merge(existing, overlay)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged


def main(argv):
    if len(argv) != 2:
        print("usage: json_merge.py <target.json>  (overlay JSON on stdin)", file=sys.stderr)
        return 2
    try:
        overlay = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"json_merge: invalid overlay JSON on stdin: {e}", file=sys.stderr)
        return 1
    if not isinstance(overlay, dict):
        print("json_merge: overlay must be a JSON object", file=sys.stderr)
        return 1
    merge_into_file(argv[1], overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
