#!/usr/bin/env python3
"""Proof for tools/provider_bands.py's cache seam.

`PROVIDER_BAND_CACHE` exists so a proof can PLANT a band reading. Without it a test either spawns
the provider's real quota probe (45s, network, and an answer that changes under it) or writes the
repo's live cache from a test run — both of which make the suite's verdict depend on the machine.

So the load-bearing cases are that the override is honoured, that the TTL still expires a planted
reading, and that a corrupt cache reads as "no reading" rather than raising into a caller that
treats an exception as an answer.

Run: python3 .claude/tests/test_provider_bands_cache.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "provider_bands.py")
spec = importlib.util.spec_from_file_location("pb", MOD)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

TMP = tempfile.mkdtemp(prefix="pbands_")


def plant(entries):
    """Point the cache at a temp file holding `entries`, and return its path."""
    p = os.path.join(TMP, "cache-%d.json" % len(os.listdir(TMP)))
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(entries, fh)
    os.environ["PROVIDER_BAND_CACHE"] = p
    return p


def read(transport="codex", ttl=3600):
    return pb._cache_read(transport, ttl)


FRESH = {"codex": {"at": time.time(),
                   "reading": {"transport": "codex", "band": "Hot", "pressure": 1.71}}}
STALE = {"codex": {"at": time.time() - 90000,
                   "reading": {"transport": "codex", "band": "Surplus", "pressure": 0.2}}}

CASES = [
    ("the override redirects the cache away from the repo file",
     lambda: (plant(FRESH), pb._cache_path() != os.path.join(
         pb._repo_root(), ".claude", ".cache", "provider_bands.json"))[1]),

    ("a planted fresh reading is what the reader returns",
     lambda: (plant(FRESH), (read() or {}).get("band"))[1] == "Hot"),

    # The load-bearing negative: without it, "returns the planted band" is satisfied by a reader
    # that ignores `at` entirely and would serve a day-old band as current.
    ("a reading past the TTL is NOT returned",
     lambda: (plant(STALE), read())[1] is None),

    ("...and the same stale entry IS returned under a TTL wide enough to cover it",
     lambda: (plant(STALE), (read(ttl=200000) or {}).get("band"))[1] == "Surplus"),

    ("an absent transport reads as no reading, not as an error",
     lambda: (plant(FRESH), read(transport="opencode"))[1] is None),

    ("a corrupt cache reads as no reading rather than raising",
     lambda: _corrupt_reads_none()),

    ("a missing cache file reads as no reading",
     lambda: _missing_reads_none()),

    ("unset, the path falls back to the repo cache",
     lambda: _unset_falls_back()),
]


def _corrupt_reads_none():
    p = os.path.join(TMP, "corrupt.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json")
    os.environ["PROVIDER_BAND_CACHE"] = p
    return read() is None


def _missing_reads_none():
    os.environ["PROVIDER_BAND_CACHE"] = os.path.join(TMP, "does-not-exist.json")
    return read() is None


def _unset_falls_back():
    os.environ.pop("PROVIDER_BAND_CACHE", None)
    try:
        return pb._cache_path() == os.path.join(
            pb._repo_root(), ".claude", ".cache", "provider_bands.json")
    finally:
        os.environ["PROVIDER_BAND_CACHE"] = os.path.join(TMP, "does-not-exist.json")


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    os.environ.pop("PROVIDER_BAND_CACHE", None)
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
