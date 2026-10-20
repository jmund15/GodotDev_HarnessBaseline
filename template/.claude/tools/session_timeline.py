#!/usr/bin/env python3
"""Wall-time partition of one main Claude Code transcript.

Every gap between consecutive timestamped user/assistant rows goes to exactly one bucket chosen by
the LATER row's kind, so the buckets sum to the span. A gap over 30 minutes contributes its excess to
`away` and is capped at 30 minutes for its own bucket. The partition is a report classification
(`orchestration_metrics.py --rollup` reads it); nothing here enforces anything.

    session_timeline.py <transcript.jsonl>     print the partition as JSON
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcript_usage  # noqa: E402

AWAY_CAP_S = 1800.0
BIG_RESULT_CHARS = 20000


def runner():
    """The proof runner module (`scripts/harness_tests.py`), which owns its budgets and log paths."""
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    sys.path.insert(0, scripts)
    try:
        import harness_tests
        return harness_tests
    finally:
        sys.path.remove(scripts)


def commit_budget_s():
    """The proof runner's commit-check budget, the one owner of the figure."""
    return runner().COMMIT_BUDGET_S


TEST_RE = re.compile(
    r"harness_tests\.py|\.claude/tests/|-m unittest|regression_gate|run_tests\.ps1|run_test_suite\.ps1"
    r"|gdunit|GdUnit|\btest_\w+\.(py|ps1|sh|mjs|js)\b", re.I)
COMMIT_RE = re.compile(r"\bgit (-C \S+ )?commit\b")
BOOKKEEPING_RE = re.compile(
    r"task_record\.py|orchestration_metrics\.py|review_ledger\.py|extract_numbers\.py|worklog"
    r"|git (-C \S+ )?(add|commit|status|diff|log|show|rev-parse|ls-files)\b"
    r"|restate_scan|post_check\.py|session_end_check|triage\.py")
NOTIFIED_TOOL_RE = re.compile(r"<tool-use-id>([\w-]+)<")
SHELLS = ("Bash", "PowerShell")


def _text(content):
    if isinstance(content, str):
        return content
    return " ".join(str(x.get("text", "")) for x in (content or []) if isinstance(x, dict))


def _kind(row):
    content = (row.get("message") or {}).get("content")
    if row["type"] == "assistant":
        return "assistant"
    if isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
        return "tool_result"
    text = _text(content)
    if "<task-notification>" in text:
        return "notification"
    if row.get("isMeta") or text.startswith("<") or "Caveat:" in text[:40]:
        return "meta"
    return "owner"


def tool_class(name, tool_input):
    """The bucket class of one tool call; a shell call splits into commit, test and other."""
    if name in SHELLS:
        cmd = (tool_input or {}).get("command", "") or ""
        base = "shell_commit" if COMMIT_RE.search(cmd) else "shell_test" if TEST_RE.search(cmd) else "shell_other"
        return base + ("_bg" if (tool_input or {}).get("run_in_background") else "")
    if name.startswith("mcp__"):
        return "mcp"
    return name


def _result_chars(content):
    if isinstance(content, str):
        return len(content)
    return sum(len(str(x.get("text", ""))) for x in (content or []) if isinstance(x, dict))


def _empty(state):
    return dict(state=state, span=0.0, start=None, buckets={}, counts={}, compactions=0,
                fg_test_over_budget={"n": 0, "secs": 0.0}, commit_secs=[], bookkeeping_ids=[],
                tool_messages=0, big_results={"n": 0, "chars": 0}, bad_lines=0)


def partition(transcript_path):
    """Partition one transcript's wall time. `state` is `ok`, `absent` or `unreadable`."""
    if not os.path.exists(transcript_path):
        return _empty("absent")
    bad = {}
    rows = [r for r in transcript_usage.iter_records(transcript_path, bad)
            if r.get("type") in ("user", "assistant") and transcript_usage.parse_ts(r.get("timestamp")) is not None]
    if bad.get("unreadable"):
        return _empty("unreadable")
    out = _empty("ok")
    out["bad_lines"] = bad.get("lines", 0)
    if not rows:
        return out
    fg_budget = commit_budget_s()
    buckets, counts = collections.Counter(), collections.Counter()
    uses, launched = {}, {}
    msg_tools = collections.OrderedDict()
    seen_msgs = set()
    prev = None
    for row in rows:
        kind = _kind(row)
        t = transcript_usage.parse_ts(row["timestamp"])
        content = (row.get("message") or {}).get("content")
        gap = t - prev[1] if prev else 0.0
        if gap > AWAY_CAP_S:
            buckets["away"] += gap - AWAY_CAP_S
            counts["away"] += 1
            gap = AWAY_CAP_S
        if row.get("isCompactSummary"):
            out["compactions"] += 1
        if kind == "assistant":
            mid = (row.get("message") or {}).get("id")
            if mid not in seen_msgs:
                seen_msgs.add(mid)
                counts["gen"] += 1
            buckets["gen"] += gap
            calls = msg_tools.setdefault(mid, [])
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name, tinput = block.get("name") or "?", block.get("input") or {}
                    cls = tool_class(name, tinput)
                    uses[block.get("id")] = (cls, t)
                    launched[block.get("id")] = cls
                    calls.append((name, tinput))
        elif kind == "tool_result":
            blocks = [x for x in content if isinstance(x, dict) and x.get("type") == "tool_result"]
            matched = [uses.pop(x.get("tool_use_id")) for x in blocks if x.get("tool_use_id") in uses]
            cls = matched[0][0] if matched else "unmatched"
            buckets["tool:" + cls] += gap
            counts["tool:" + cls] += 1
            if matched:
                full = t - matched[0][1]
                if cls == "shell_test" and full > fg_budget:
                    out["fg_test_over_budget"]["n"] += 1
                    out["fg_test_over_budget"]["secs"] += full
                if cls == "shell_commit":
                    out["commit_secs"].append(full)
            for block in blocks:
                chars = _result_chars(block.get("content"))
                if chars > BIG_RESULT_CHARS:
                    out["big_results"]["n"] += 1
                    out["big_results"]["chars"] += chars
        elif prev is not None and prev[0] != "tool_result" and kind in ("notification", "owner"):
            if kind == "notification":
                m = NOTIFIED_TOOL_RE.search(_text(content))
                what = launched.get(m.group(1), "unknown") if m else "unknown"
                buckets["idle:" + what] += gap
                counts["idle:" + what] += 1
            else:
                buckets["owner"] += gap
                counts["owner"] += 1
        else:
            buckets["other:%s>%s" % (prev[0] if prev else "start", kind)] += gap
        prev = (kind, t)
    out["span"] = transcript_usage.parse_ts(rows[-1]["timestamp"]) - transcript_usage.parse_ts(rows[0]["timestamp"])
    out["start"] = rows[0]["timestamp"]
    out["buckets"] = dict(buckets)
    out["counts"] = dict(counts)
    calling = {mid: calls for mid, calls in msg_tools.items() if calls}
    out["tool_messages"] = len(calling)
    out["bookkeeping_ids"] = [
        mid for mid, calls in calling.items()
        if all(name in SHELLS and BOOKKEEPING_RE.search(str(inp.get("command", "")))
               and not TEST_RE.search(str(inp.get("command", ""))) for name, inp in calls)]
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(partition(sys.argv[1])))
