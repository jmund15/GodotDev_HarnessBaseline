#!/usr/bin/env python3
"""Proof: pre_bash_dispatch skips runaway_scan_reaper.check() when the chain decides `deny`, and
still runs it (and relays its lines) on an allowed call. In-process; the reaper is a recording fake.

    python3 .claude/tests/test_pre_bash_dispatch_reaper_deny.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import pre_bash_dispatch  # noqa: E402


def fake_hook(name, output):
    module = types.ModuleType(name)
    module.__file__ = name + ".py"

    def main():
        sys.stdin.read()
        if output:
            sys.stdout.write(json.dumps({"hookSpecificOutput": dict(output, hookEventName="PreToolUse")}))
        return 0
    module.main = main
    return module


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail[:300] if detail else ""))

    calls = []
    reaper = types.ModuleType("runaway_scan_reaper")

    def check_reaper(payload=None):
        calls.append(payload)
        return ["[runaway-scan-reaper] planted line"]
    reaper.check = check_reaper
    saved = sys.modules.get("runaway_scan_reaper")
    sys.modules["runaway_scan_reaper"] = reaper
    raw = json.dumps({"tool_name": "Bash", "session_id": "pbdr0001", "tool_input": {"command": "x"}})
    try:
        deny = (fake_hook("deny_hook", {"permissionDecision": "deny", "permissionDecisionReason": "planted deny"}), ())
        code, out, _err = pre_bash_dispatch.dispatch(raw, hooks=(deny,))
        hso = json.loads(out)["hookSpecificOutput"] if out else {}
        check("denied payload returns the deny", code == 0 and hso.get("permissionDecision") == "deny"
              and hso.get("permissionDecisionReason") == "planted deny", out)
        check("denied payload never calls the reaper", calls == [], "calls=%d" % len(calls))
        check("denied payload carries no reaper line", "planted line" not in out, out)

        allow = (fake_hook("quiet_hook", None), ())
        code, out, _err = pre_bash_dispatch.dispatch(raw, hooks=(allow,))
        check("allowed payload calls the reaper once", len(calls) == 1, "calls=%d" % len(calls))
        check("allowed payload relays the reaper line in additionalContext",
              "planted line" in (json.loads(out)["hookSpecificOutput"].get("additionalContext", "") if out else ""), out)
    finally:
        if saved is None:
            sys.modules.pop("runaway_scan_reaper", None)
        else:
            sys.modules["runaway_scan_reaper"] = saved

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
