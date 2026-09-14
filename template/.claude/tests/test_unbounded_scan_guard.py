"""Re-runnable proof for hooks/unbounded_scan_guard.py's advisory cadence (B5).

The full advisory teaches; the repeat only reminds. Each axis (volume, scope) delivers its
full text once, drops to a one-liner after, and is re-armed by a compaction. Cases feed real
PreToolUse payloads and assert on `additionalContext`.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_unbounded_scan_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "unbounded_scan_guard.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "usg00001"
UNBOUNDED = 'rg -n "SpawnImpact" Jmodot/'          # volume axis only (rg is gitignore-aware)
BLIND = 'grep -rn "pattern"'                        # both axes
BOUNDED = 'rg -n "SpawnImpact" Jmodot/ | head -20'  # neither


def run(hook, payload, env):
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, env=env)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ""
    try:
        return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")
    except ValueError:
        return out


def scan(command, env, session=SID, cwd="C:/repo"):
    return run(HOOK, {"tool_name": "Bash", "session_id": session, "cwd": cwd,
                      "tool_input": {"command": command}}, env)


def main():
    tmp = tempfile.mkdtemp(prefix="usgstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    out = scan(UNBOUNDED, env)
    cases.append(("first unbounded scan gets the full advisory",
                  "Output size is unknown before the call" in out))

    out = scan(UNBOUNDED, env)
    cases.append(("the repeat drops to the one-line form",
                  out.startswith("\u26a0 UNBOUNDED RECURSIVE SCAN")
                  and "Output size is unknown before the call" not in out))

    out = scan(BLIND, env)
    cases.append(("the scope axis carries its own first-fire, still full",
                  "261 " in out and "\u26a0 UNBOUNDED RECURSIVE SCAN \u2014 cap it" in out))

    out = scan(BLIND, env)
    cases.append(("both axes are short on the repeat",
                  "261 " not in out and "Use the Grep tool" in out))

    run(PRECOMPACT, {"session_id": SID, "trigger": "auto"}, env)
    out = scan(UNBOUNDED, env)
    cases.append(("a compaction re-arms the full advisory",
                  "Output size is unknown before the call" in out))

    # Negatives — a cadence change must not widen or narrow what fires.
    cases.append(("a bounded scan is silent", scan(BOUNDED, env) == ""))
    cases.append(("a non-scan command is silent", scan("git status", env) == ""))
    cases.append(("an adjacent tool is untouched",
                  run(HOOK, {"tool_name": "Read", "session_id": SID,
                             "tool_input": {"file_path": "x.md"}}, env) == ""))
    cases.append(("a worktree cwd still suppresses the scope axis",
                  "GITIGNORE-BLIND" not in scan(BLIND, env, session="usg00002",
                                                cwd="C:/repo/.claude/worktrees/w1")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
