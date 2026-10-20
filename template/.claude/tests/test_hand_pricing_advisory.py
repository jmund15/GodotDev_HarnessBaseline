"""Proof for hooks/hand_pricing_advisory.py: an inline script that reads a run's token or cost fields gets the
run_cost.py advisory; a command that only names those fields, or runs run_cost.py itself, gets nothing.

Each case feeds a real PreToolUse payload on stdin, standalone and through pre_bash_dispatch.py, and asserts on
the emitted channel. Run: python3 .claude/tests/test_hand_pricing_advisory.py
"""
import json
import subprocess
import sys
import os
import tempfile
import uuid
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
FAILS = []

INLINE_PARSE = ("python3 - <<'EOF'\nimport json\nfor l in open('x.stream.jsonl'):\n"
                "    o=json.loads(l); print(o.get('modelUsage'))\nEOF")
CASES = [
    ("Bash", INLINE_PARSE, True, "an inline heredoc reading modelUsage"),
    ("Bash", "python3 -c \"import json; print(json.load(open('r.json'))['total_cost_usd'])\"", True,
     "python3 -c reading total_cost_usd"),
    ("PowerShell", "python -c \"import json; d=json.load(open('r.json')); print(d['costUSD'])\"", True,
     "PowerShell python -c reading costUSD"),
    ("Bash", "node -e \"const r=require('./r.json'); console.log(r.usage.cache_read_input_tokens)\"", True,
     "node -e reading cache_read_input_tokens"),
    ("Bash", "git grep -n modelUsage -- .claude", False, "a search that names the field"),
    ("Bash", "cat run.record.json | head -5  # costUSD", False, "a read that names the field"),
    ("Bash", "python3 .claude/tools/run_cost.py --a a.stream.jsonl --b b.stream.jsonl", False, "run_cost.py itself"),
    ("Bash", "python3 - <<'EOF'\nimport run_cost\nprint(run_cost.pair([], []))  # modelUsage\nEOF", False,
     "an inline script that calls run_cost"),
    ("Bash", "python3 -c \"print(1+1)\"", False, "an inline script with no cost field"),
    ("Bash", "cat >> notes.md <<'EOF'\npython3 -c 'print(costUSD)'\nEOF", False,
     "a data-only heredoc containing a script mention"),
    ("Bash", "printf '%s' \"python3 -c 'print(costUSD)'\"", False,
     "a quoted script mention is data"),
    ("Bash", "cat <<'EOF'\ncostUSD\nEOF\npython3 -c 'print(1)'", False,
     "a real script cannot borrow a cost field from unrelated data"),
    ("Bash", "python3 - <<'EOF'\nprint(costUSD)\nEOF", True,
     "a Python stdin reader executes its heredoc"),
    ("Bash", "python3 -c 'print(costUSD)'", True,
     "an actual inline script reads a cost field"),
    ("Read", INLINE_PARSE, False, "another tool"),
]


def check(ok, label):
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        FAILS.append(label)


def run(script, tool, command):
    payload = {"tool_name": tool, "session_id": uuid.uuid4().hex,
               "tool_input": {"command": command}, "hook_event_name": "PreToolUse"}
    with tempfile.TemporaryDirectory() as state:
        return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload), text=True,
                              capture_output=True, env=dict(os.environ, HARNESS_HOOK_STATE_DIR=state))


def advisory(stdout):
    texts = []
    for line in stdout.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        out = doc.get("hookSpecificOutput") or {}
        if out.get("permissionDecision") == "deny":
            return "DENY"
        texts.append(out.get("additionalContext") or "")
    return " ".join(texts)


def main():
    for tool, command, fires, label in CASES:
        r = run("hand_pricing_advisory.py", tool, command)
        crashed = r.returncode not in (0, 2) or "Traceback" in r.stderr
        check(not crashed, f"standalone does not crash: {label} ({r.stderr[-200:]})")
        said = advisory(r.stdout)
        check(r.returncode == 0 and said != "DENY", f"never denies or blocks (exit 2): {label}")
        check(("run_cost.py" in said) == fires, f"{'fires' if fires else 'silent'}: {label}")
    r = run("pre_bash_dispatch.py", "Bash", INLINE_PARSE)
    check(r.returncode == 0 and "Traceback" not in r.stderr and "run_cost.py --a" in advisory(r.stdout),
          "the Bash dispatcher carries the advisory and does not block (%s)" % r.stderr[-200:])
    print("\ntest_hand_pricing_advisory: %d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
