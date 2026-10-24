#!/usr/bin/env python3
"""The plan-window usage samples file: the one locked writer and the one reader.

Each line is one observation of a plan window:
    {"ts": <epoch>, "provider": "anthropic"|"codex", "plan": <registry transport>,
     "window": "five_hour"|"seven_day"|<slot>, "used": <percent>, "resets_at": <epoch>, ...}

`plan` names the paying plan (the registry transport), so two plans of one provider keep separate rows;
a legacy row without one is read under its `provider` (`plan_of`). Writers: hooks/budget_posture.py
appends the Anthropic windows at each UserPromptSubmit, from the statusline capture (`ts` is its
`captured_at`); scripts/codex_quota_probe.py appends the Codex windows of each successful reading
(`ts` is its `observedAt`); tools/provider_capacity.py appends every window of every plan-quota
transport's fresh reading. Reader: tools/plan_usage_calc.py.

Path: HARNESS_USAGE_SAMPLES when set, else ~/.claude/usage_samples.jsonl. User-level rather than a
per-project log, because a plan window is account-wide. A proof sets the override; none writes the
real file.

`record` skips a sample older than MAX_AGE_SECONDS and one no newer than the last kept sample for
its plan and window. The last-kept map lives in `<path>.state.json`, and the compare and the
append run under that file's lock (`_hook_state.update_json_locked`), so concurrent sessions reading
one capture write one line. The first append of each UTC day drops lines older than RETENTION_DAYS.
Every failure returns None and never raises: a sampler failure costs only the sample.
"""
import io
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _hook_state import update_json_locked  # noqa: E402

MAX_AGE_SECONDS = 600
RETENTION_DAYS = 35


def samples_path():
    return os.environ.get("HARNESS_USAGE_SAMPLES") or os.path.expanduser("~/.claude/usage_samples.jsonl")


def plan_of(sample):
    """The plan a sample observes: its `plan`, else its `provider` for a legacy row, else None."""
    plan = (sample or {}).get("plan")
    return plan if isinstance(plan, str) and plan else (sample or {}).get("provider")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _prune(path, cutoff):
    if not os.path.exists(path):
        return
    kept = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ts = _number(json.loads(line).get("ts"))
            except (ValueError, AttributeError):
                ts = None
            if ts is None or ts >= cutoff:
                kept.append(line)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".jsonl", dir=directory)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(kept)
    os.replace(tmp, path)


def record(samples, now=None, path=None):
    """Append the new samples; the count appended, or None when nothing could be written."""
    now = time.time() if now is None else now
    path = path or samples_path()
    fresh = [s for s in samples if _number(s.get("ts")) is not None
             and now - s["ts"] <= MAX_AGE_SECONDS]
    if not fresh:
        return 0

    def update(state):
        last = state.get("last") if isinstance(state.get("last"), dict) else {}
        kept = []
        for s in fresh:
            key = "%s/%s" % (plan_of(s), s.get("window"))
            prior = _number(last.get(key))
            if prior is not None and s["ts"] <= prior:
                continue
            last[key] = s["ts"]
            kept.append(s)
        if not kept:
            return 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if state.get("pruned_day") != today:
            _prune(path, now - RETENTION_DAYS * 86400)
            state["pruned_day"] = today
        with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
            for s in kept:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
        state["last"] = last
        return len(kept)

    written, count = update_json_locked(path + ".state.json", update)
    return count if written else None


def read(path=None, bad=None):
    """Every sample object in the file; malformed lines are counted in `bad['lines']`."""
    path = path or samples_path()
    out = []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    value = None
                if isinstance(value, dict):
                    out.append(value)
                elif bad is not None:
                    bad["lines"] = bad.get("lines", 0) + 1
    except OSError:
        pass
    return out
