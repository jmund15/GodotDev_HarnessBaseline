"""Shared proof helper: read this checkout's baseline.lock.json rows.

A published proof must not assume it runs in the publishing project. The lock is that project's
own bookkeeping and never publishes, so a template checkout has none. A `forked` row never syncs
upstream and a `local` row never exists there, so a proof asserting such a file's prose belongs
only to the checkout that owns it.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def project_owned(relpath):
    """True when this checkout's lock marks the repo-relative `relpath` `forked` or `local`;
    False when there is no readable lock."""
    try:
        lock = json.loads((ROOT / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    entry = (lock.get("files") or {}).get(relpath) or {}
    return entry.get("status") in ("forked", "local")
