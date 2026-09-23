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
BLIND = 'find . -name "*.md"'                       # both axes (recursive grep is now a deny)
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
                  "output size is unknown" in out and "--max-count N" in out and "head_limit" in out))

    out = scan(UNBOUNDED, env)
    cases.append(("the repeat drops to the one-line form",
                  out.startswith("\u26a0 UNBOUNDED RECURSIVE SCAN")
                  and "output size is unknown" not in out))

    out = scan(BLIND, env)
    cases.append(("the scope axis carries its own first-fire, still full",
                  "capping output does not fix" in out and "-path ./.claude -prune" in out
                  and "\u26a0 UNBOUNDED RECURSIVE SCAN \u2014 cap it" in out))

    out = scan(BLIND, env)
    cases.append(("both axes are short on the repeat",
                  "capping output does not fix" not in out and "Use the Grep tool" in out
                  and "-path ./.claude -prune" in out))

    run(PRECOMPACT, {"session_id": SID, "trigger": "auto"}, env)
    out = scan(UNBOUNDED, env)
    cases.append(("a compaction re-arms the full advisory",
                  "output size is unknown" in out))

    # Negatives — a cadence change must not widen or narrow what fires.
    cases.append(("a bounded scan is silent", scan(BOUNDED, env) == ""))
    # Live misfire 2026-09-14: `rg -n <pat> <one file>` walks no tree, yet drew the full volume advisory.
    cases.append(("a scan of one named file is silent",
                  scan("rg -n foo .claude/scripts/codex_proxy_sidecar.sh", env, session=SID + "-file") == ""))
    cases.append(("a scan of a one-level file glob is silent",
                  scan("rg -n foo .claude/hooks/*.py", env, session=SID + "-glob") == ""))
    # Live misfire 2026-09-14: `python3 proof.py | rg 'FAIL'` reads stdin and walks no tree.
    cases.append(("rg reading a pipe is silent",
                  scan("python3 t.py 2>&1 | rg 'FAIL|cases pass'", env, session=SID + "-pipe") == ""))
    # Live misfire 2026-09-14: `ls .claude/tests | rg 'git'` — the first scan-shaped word was `ls`.
    cases.append(("rg fed by a plain ls is silent",
                  scan("ls .claude/tests | rg 'git_guardrails|git_commit'", env, session=SID + "-ls") == ""))
    # Live misfire 2026-09-14: a `;` glued to the first rg's quoted pattern hid the list boundary.
    cases.append(("two pipe-fed rg calls joined by a glued ; are silent",
                  scan("python3 a.py | rg 'FAIL|cases pass'; python3 b.py | rg 'FAIL|passed'", env,
                       session=SID + "-semi") == ""))
    # Live misfire 2026-09-14: a pipe-fed rg, then an rg naming one file; neither walks a tree.
    cases.append(("a pipe-fed rg then a one-file rg is silent",
                  scan("python3 a.py 2>&1 | rg -n 'listing'; rg -n 'x' .claude/tools/guard_text.py", env,
                       session=SID + "-mixed") == ""))
    cases.append(("a pipe-fed rg then an rg walking a directory still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("python3 a.py | rg -n 'listing'; rg -n 'x' Jmodot/", env,
                                                     session=SID + "-mixdir")))
    cases.append(("an unbounded find feeding rg still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("find . -name x | rg y", env, session=SID + "-find")))
    cases.append(("rg heading its own pipeline still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("rg -n foo | sort", env, session=SID + "-head")))
    cases.append(("a globstar operand still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("rg -n foo src/**/*.md", env, session=SID + "-star")))
    cases.append(("a scan of a dot-named directory still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("rg -n foo .claude", env, session=SID + "-dir")))
    # Review F3: a directory whose name looks like `name.ext` is walked, not read as one file.
    dotted_root = tempfile.mkdtemp(prefix="usgdotted_")
    os.makedirs(os.path.join(dotted_root, "config.d"))
    cases.append(("a scan of an existing dotted directory still advises",
                  "UNBOUNDED RECURSIVE SCAN" in scan("rg -n foo config.d", env, session=SID + "-dotdir",
                                                     cwd=dotted_root)))
    cases.append(("a non-scan command is silent", scan("git status", env) == ""))
    cases.append(("an adjacent tool is untouched",
                  run(HOOK, {"tool_name": "Read", "session_id": SID,
                             "tool_input": {"file_path": "x.md"}}, env) == ""))
    cases.append(("a worktree cwd still suppresses the scope axis",
                  "GITIGNORE-BLIND" not in scan(BLIND, env, session="usg00002",
                                                cwd="C:/repo/.claude/worktrees/w1")))
    for layout in ("task-worktrees", "baseline-worktrees"):
        cases.append(("a .claude/.cache/%s cwd suppresses the scope axis too" % layout,
                      "GITIGNORE-BLIND" not in scan(BLIND, env, session="usg-" + layout,
                                                    cwd="C:/repo/.claude/.cache/%s/w1" % layout)))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
