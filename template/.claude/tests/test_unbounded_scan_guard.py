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
    if r.returncode not in (0, 2) or 'Traceback' in (r.stderr or ''):
        raise RuntimeError('hook crashed: ' + (r.stderr or ''))
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


def verdict(command, env, cwd, home=None):
    """(updatedInput command, additionalContext, permissionDecision) the hook emits for a Bash call."""
    child_env = dict(env, HOME=home, USERPROFILE=home) if home else env
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(
        {"tool_name": "Bash", "session_id": "usgrg", "cwd": cwd, "tool_input": {"command": command}}),
        capture_output=True, text=True, timeout=60, env=child_env)
    if r.returncode not in (0, 2) or "Traceback" in (r.stderr or ""):
        raise RuntimeError("hook crashed: " + (r.stderr or ""))
    out = json.loads(r.stdout).get("hookSpecificOutput") if r.stdout.strip() else {}
    out = out or {}
    return ((out.get("updatedInput") or {}).get("command", ""), out.get("additionalContext", ""),
            out.get("permissionDecision", ""))


def outside_git_cases(env):
    """A recursive grep outside any git tree runs as a bounded `rg`; shapes it cannot translate stay denied."""
    home = os.path.realpath(tempfile.mkdtemp(prefix="usghome_")).replace("\\", "/")
    target = home + "/.claude/projects/x"
    os.makedirs(target)
    with open(target + "/a.jsonl", "w", encoding="utf-8") as handle:
        handle.write("foo a.b\n")
    probe = subprocess.run(["git", "-C", home, "rev-parse", "--is-inside-work-tree"], capture_output=True)
    cases = [("the planted home is outside any git tree", probe.returncode != 0)]

    def rewritten(command, cwd=home):
        return verdict(command, env, cwd, home)

    cmd, note, decision = rewritten("grep -rn foo ~/.claude/projects/x")
    cases.append(("outside git, grep -rn is rewritten to a bounded rg through updatedInput",
                  cmd.startswith("rg ") and "--max-count" in cmd and " -uu" in cmd and " -n" in cmd
                  and "-e foo" in cmd and home + "/.claude/projects/x" in cmd and decision == ""))
    cases.append(("the rewrite note names rg and CLAUDE.core.md §9, not git grep",
                  "rg" in note and "CLAUDE.core.md §9" in note and "git grep" not in note))
    cmd, _, _ = rewritten("grep -rnF 'a.b' " + target)
    cases.append(("-F becomes rg -F", " -F" in cmd and cmd.startswith("rg ")))
    cmd, _, _ = rewritten("grep -rnE 'a|b' " + target)
    cases.append(("-E keeps rg's default regex (rg -E is an encoding flag) and the pattern",
                  cmd.startswith("rg ") and " -E" not in cmd and " -F" not in cmd and "-e 'a|b'" in cmd))
    cmd, _, _ = rewritten("grep -rniwl foo " + target + " | head -5")
    cases.append(("flags and the downstream filter survive; -l carries no --max-count",
                  cmd.startswith("rg ") and " -i" in cmd and " -w" in cmd and " -l" in cmd
                  and "--max-count" not in cmd and cmd.endswith("| head -5")))
    cmd, _, _ = rewritten("grep -rn -m 3 foo " + target)
    cases.append(("the caller's -m replaces the default bound", cmd.count("--max-count") == 1 and "--max-count 3" in cmd))

    for label, command in (("--include", "grep -r --include=*.jsonl foo " + target),
                           ("--exclude-dir", "grep -rn --exclude-dir=y foo " + target),
                           ("-P", "grep -rnP 'fo+' " + target),
                           ("-z", "grep -rnz foo " + target),
                           ("an unknown flag", "grep -rn --color=always foo " + target),
                           ("a BRE alternation", "grep -rn 'a\\|b' " + target),
                           ("a backreference", "grep -rnE '(a)\\1' " + target),
                           ("command substitution", "grep -rn foo $(echo " + target + ")"),
                           ("a missing path", "grep -rn foo " + home + "/nope"),
                           ("an unparsed subshell", "(grep -rn foo " + target + ")")):
        cmd, note, decision = rewritten(command)
        cases.append(("outside git, " + label + " stays denied with the current text",
                      cmd == "" and decision == "deny" and "RECURSIVE GREP DENIED" in (json.loads(subprocess.run(
                          [sys.executable, HOOK], input=json.dumps({"tool_name": "Bash", "cwd": home,
                          "tool_input": {"command": command}}), capture_output=True, text=True, env=env
                      ).stdout)["hookSpecificOutput"]["permissionDecisionReason"])))

    repo = os.path.normpath(os.path.join(HERE, "..", ".."))
    cmd, note, decision = verdict("grep -rn foo .claude/hooks", env, repo)
    cases.append(("a checkout grep still runs as git grep --untracked",
                  cmd == "git grep --untracked -n -e foo -- .claude/hooks" and "git grep" in note))
    for label, command in (("a quoted mention", 'echo "grep -r foo ' + target + '"'),
                           ("git grep", "git grep -n foo"),
                           ("a named-file grep", "grep -rn foo a.py b.md"),
                           ("a one-level glob grep", "grep -rn PROVIDER .claude/tests/*.py | head"),
                           ("a file with a redirect", "grep -rn \"s9_h\\|headless\" notes/ledger.md 2>/dev/null | head -5")):
        cmd, note, decision = verdict(command, env, repo)
        cases.append((label + " is neither rewritten nor denied", cmd == "" and decision == ""))
    return cases


def main():
    tmp =tempfile.mkdtemp(prefix="usgstate_")
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

    run(PRECOMPACT, {"session_id": SID, "trigger": "auto"},
        dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"]))
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

    # Quoted heredoc prose is data, not a scan or a bounding option for later code.
    for delimiter in ("'EOF'", '"EOF"'):
        command = 'cat >> notes.md <<' + delimiter + '\nFind where that path gets the tag ...\nEOF'
        cases.append(('quoted heredoc ' + delimiter + ' scan prose is silent',
                      scan(command, env, session=SID + '-doc-' + delimiter) == ''))
        out = scan(command + '\nfind .', env, session=SID + '-after-' + delimiter)
        cases.append(('real find after heredoc ' + delimiter + ' still warns on both axes',
                      'UNBOUNDED RECURSIVE SCAN' in out and 'GITIGNORE-BLIND SCAN' in out))
    command = "cat >> notes.md <<'EOF'\nfind .\n--max-count 1 -prune\nEOF\nfind ."
    out = scan(command, env, session=SID + '-bounds-data')
    cases.append(('heredoc bounding words cannot silence a real find',
                  'UNBOUNDED RECURSIVE SCAN' in out and 'GITIGNORE-BLIND SCAN' in out))
    cases.append(('tab-stripped quoted heredoc scan prose is silent',
                  scan("cat <<-'EOF'\n\tfind .\n\tEOF", env, session=SID + '-tabs') == ''))
    out = scan('find .', env, session=SID + '-real-find')
    cases.append(('real find dot still warns on both axes',
                  'UNBOUNDED RECURSIVE SCAN' in out and 'GITIGNORE-BLIND SCAN' in out))

    cases += outside_git_cases(env)

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
