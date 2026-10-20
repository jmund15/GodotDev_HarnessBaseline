"""Ledger identity on worker ratings: repeats collapse, coverage ignores single paths, `unusable` warns on readable rows.

A rate is only comparable across rows that measured the same worker on distinct requests, so
(1) exact repeats (same tool, request key, server sha) count once at their worst outcome,
(2) `paths_covered` is meaningless for one path, and (3) `unusable` on a row with readable
non-degenerate output is warned, because truncated output is checkable on its prefix.

    python .claude/tests/test_worker_rate_epoch.py
"""

import pathlib
import sys

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import worker_rate as wr  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        FAILURES.append(msg)


def row(ts, outcome=None, key="k1", sha="aaa", tool="read_files", **kw):
    return {"ts": ts, "tool": tool, "call_key": key, "server_sha": sha, "outcome": outcome, **kw}


# (1) repeats
out = wr.collapse_repeats([row("1", "faithful"), row("2", "fabricated"), row("3", "faithful")])
check(len(out) == 1 and out[0]["outcome"] == "fabricated", "repeats collapse to the worst outcome")
check(out[0].get("repeats") == 3, "a collapsed row reports how many calls it stands for")
out = wr.collapse_repeats([row("1", "faithful", sha="aaa"), row("2", "faithful", sha="bbb")])
check(len(out) == 2, "the same request on different server shas stays separate")
out = wr.collapse_repeats([row("1", "faithful", key=None), row("2", "faithful", key=None)])
check(len(out) == 2, "rows without a request key never collapse")
out = wr.collapse_repeats([row("1", sha=None), row("2", sha=None)])
check(len(out) == 2, "rows without a server sha never collapse (an unknown worker is not one worker)")
out = wr.collapse_repeats([row("1", None), row("2", "faithful")])
check(len(out) == 1 and out[0]["outcome"] == "faithful", "a rated repeat represents its unrated twin")
out = wr.collapse_repeats([row("1", "faithful", key="a"), row("2", "faithful", key="b")])
check(len(out) == 2, "distinct requests stay separate")

# (2) coverage
check(not wr.is_partial({"paths_requested": 1, "paths_covered": 0}), "a single path is never partial")
check(wr.is_partial({"paths_requested": 3, "paths_covered": 2}), "fewer than requested is partial")
check(not wr.is_partial({"paths_requested": 3, "paths_covered": 3}), "full coverage is not partial")
check(not wr.is_partial({"paths_requested": None, "paths_covered": None}), "no path list is not partial")

# (3) unusable warning
check(wr.unusable_warning({"chars": 10730, "degenerate": False, "truncated": True, "error": None}),
      "truncated-only content warns before `unusable`")
check(wr.unusable_warning({"chars": 2500, "degenerate": True, "truncated": True, "error": None}) is None,
      "a degenerate row needs no warning")
check(wr.unusable_warning({"chars": None, "degenerate": None, "truncated": False, "error": "boom"}) is None,
      "an errored row needs no warning")
check(wr.unusable_warning({"chars": 0, "degenerate": None, "truncated": False, "error": None}) is None,
      "an empty row needs no warning")

# server filter
rows = [row("1", sha="abc123"), row("2", sha="def456"), row("3", sha=None)]
check([r["ts"] for r in wr.filter_server_sha(rows, "abc")] == ["1"], "--server-sha matches by prefix")
check(len(wr.filter_server_sha(rows, None)) == 3, "no --server-sha keeps every row")

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED")
    sys.exit(1)
print("\nALL PASS")
