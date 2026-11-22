#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- a wide test run starts only in a `gate` job.

Class: process guard, blocking from its first commit (the plan's A1/A2). The instruction it backs is
`orchestration` §4 and `guards/author.md`: a job runs the checks its rails name, and a wider run is
the gate kind's. Weakness compensated: writer jobs looping the whole regression gate or an entire
spec suite for a one-line fix, which text fixes for the same choice did not stop.

Wide is data: `gate_cadence.wide` in the adaptation files holds one row per runner (an invocation
regex, its exempt arguments, an optional narrow rule), read through `hooks/_wide_runs.py`. The match
is the invocation, never the noun, so `grep`, `cat` or a bare name in prose never denies. A malformed
row fails closed: its validation error is the deny reason.

Admitted callers: the root session (no `agent_id`, no `CLAUDE_CODE_SIDECAR`), a native job whose
payload `agent_type` is the gate kind's `agentType`, and a sidecar whose `CLAUDE_CODE_SIDECAR_KIND` is
`gate`. The `agentType` is read from `guards/tool_profiles.json`'s gate row, and an unreadable table
refuses a native job with that cause. Every other caller, an unknown type included, is refused.

Reach: `hooks/_wide_runs.py`'s -- shell segments, `bash -c`/`pwsh -Command` bodies, Python `-c` and
`.py` literal process calls, and launched scripts to depth 2. Not seen from the hook: a command built
at run time, and a script chain deeper than the scan.

`--command "<invocation>"` asks the same question from a runner script's entry check, for runs that
host no Claude hooks: exit 2 and the reason on stdout when refused, else 0.

Fail posture: a crash here reports the lost check through additionalContext and admits the call.
Retirement: delegate transcripts show no non-gate wide-run attempt and no denial naming
`wide_test_guard` since the `Your checks` line shipped.
Proof: `.claude/tests/test_wide_test_guard.py`.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))

import _wide_runs  # noqa: E402
import adaptation  # noqa: E402
from _command_text import executable_text  # noqa: E402


def _table():
    """The kinds table. Raises when it cannot be read: a native job's gate type comes from its gate row."""
    import tool_profiles
    return tool_profiles.load_table()


def _checks(root):
    data = adaptation.get(os.path.join(root, ".claude"), "gate_cadence")
    return [row for row in data.get("slice") or [] if isinstance(row, str) and row.strip()]


def deny_text(wide, checks):
    shown = "; ".join(run["runner"] for run in wide[:3]) + ("; ..." if len(wide) > 3 else "")
    text = ["Wide test run refused: %s -- %s." % (shown, wide[0]["reason"])]
    if checks:
        text.append("Your checks: " + " | ".join(checks) + ".")
    text.append("A wide run starts only in a `gate` job: dispatch one. Canon: `orchestration` §4; "
                "guards/author.md.")
    return " ".join(text)


def hook(payload):
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return None
    command = executable_text((payload.get("tool_input") or {}).get("command") or "")
    root = ROOT
    wide = _wide_runs.classify(command, payload.get("cwd") or os.getcwd(), root)
    if not wide:
        return None
    sidecar = str(os.environ.get("CLAUDE_CODE_SIDECAR") or "").strip()
    native = not sidecar and str(payload.get("agent_id") or "").strip()
    table = None
    if native:
        try:
            table = _table()
        except Exception as exc:  # noqa: BLE001 - a native job's admission needs the table; refuse, not guess
            return ("Wide test run refused: the gate kind's `agentType` is read from "
                    "`guards/tool_profiles.json`, which could not be read (%s: %s). Repair the table; a "
                    "native job is admitted only as that row's agent type." % (type(exc).__name__, exc))
    if _wide_runs.gate_caller(payload, os.environ, table):
        return None
    return deny_text(wide, _checks(root))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--command"] and len(argv) == 2:
        # A runner script's entry check, for runs that host no Claude hooks: the same verdict,
        # as exit 2 with the reason on stdout.
        reason = hook({"tool_name": "Bash", "tool_input": {"command": argv[1]}, "cwd": os.getcwd()})
        if reason:
            print(reason)
            return 2
        return 0
    try:
        reason = hook(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - a lost check is reported, not silent
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
              "wide_test_guard failed (%s: %s); the wide-run check did not run." % (type(exc).__name__, exc)}}))
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                  "permissionDecisionReason": reason}}))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
