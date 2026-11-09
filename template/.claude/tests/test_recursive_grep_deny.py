#!/usr/bin/env python3
"""Proof for the recursive-grep deny in hooks/unbounded_scan_guard.py, fed through the real
PreToolUse entry hooks/pre_bash_dispatch.py.

Every recursive grep is denied; there is no rewrite. The deny names the bounded forms, and the
`rg -uu` form it names finds a tracked file a .gitignore rule matches. Deny arms cover the shapes
the parse must reach: plain tracked-directory greps, ignored trees, indirect runs (find, xargs, a
shell -c, PowerShell), unknown flags, GNU-only regex escapes. Allow arms mention a recursive grep
without running one, or name only files.
A dispatcher exit outside {0, 2}, or a traceback on stderr, is CRASH, never allow.

    python3 .claude/tests/test_recursive_grep_deny.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(REPO, ".claude", "hooks", "pre_bash_dispatch.py")

# Commands the retired rewrite converted to `git grep --untracked` or a bounded `rg -uu`. The
# second is a real logged call from the replay; each must deny now.
FORMER_REWRITES = [
    "grep -rn -- '-needle' .",
    "grep -rln ProjectSuite .",
    "grep -rn noclobber .claude/",
    "grep -rn foo",
    "grep -rn -e foo -e bar",
    "grep -rn foo .claude | head -5",
    "grep -rni 'Spawn Impact' .claude/hooks",
    "grep -rn 'a\\|b' .claude",
    "echo start\ngrep -rn foo .",
    "grep -rn foo --include=*.py .claude/hooks",
]

DENY = [
    ("Bash", "grep -rnz foo ."),
    ("Bash", "grep -R foo no_such_dir"),
    ("Bash", "cd x && grep -rn foo ."),
    ("Bash", "find . -type f | xargs grep -l foo"),
    ("Bash", "find . -name '*.md' -exec grep -l foo {} +"),
    ("PowerShell", "Get-ChildItem -Recurse | Select-String foo"),
    ("Bash", "egrep --recursive foo Tests"),
    ("Bash", "grep -d recurse foo ."),
    ("Bash", "grep --directories=recurse foo ."),
    ("Bash", "git status || fgrep -rl foo ."),
    ("Bash", "ls; grep -nr foo no_such_dir | wc -l"),
    ("Bash", "printf '%s\\n' a b | xargs -n1 grep -r foo"),
    ("Bash", "find . -exec grep -l foo {} \\;"),
    ("Bash", "bash -c 'grep -rn foo .'"),
    ("PowerShell", "gci -r -Filter *.cs | sls foo"),
    ("PowerShell", "Select-String -Pattern foo -Path (Get-ChildItem -Recurse -File)"),
    ("PowerShell", "grep -rn foo ."),
    # -r with no path operand walks the cwd; a directory operand among files walks it; xargs or
    # find appends operands the command text cannot show.
    ("Bash", "grep -rn foo a.py no_such_dir"),
    ("Bash", "grep -rn foo .claude/**/*.py"),
    ("Bash", "printf '%s\\n' a.py | xargs grep -rn foo b.py"),
    ("Bash", "find . -name x | xargs grep -rl foo a.py"),
]

ALLOW = [
    ("Bash", "rg -n foo"),
    ("Bash", "git grep -n foo"),
    ("Bash", "grep -n foo a.py"),
    ("Bash", "grep -c foo a b"),
    ("Bash", 'echo "grep -r"'),
    ("Bash", 'git commit -m "fix grep -r guard"'),
    ("Bash", "cat > notes.md <<'EOF'\ngrep -rn foo .\nEOF"),
    ("Bash", "printf '%s' 'grep -rln x .'"),
    ("Bash", "grep -e -r foo a.py"),
    ("Bash", "git ls-files | xargs grep -l foo"),
    ("PowerShell", "Get-ChildItem -Recurse -Filter *.cs"),
    ("PowerShell", "Select-String -Pattern foo -Path a.py"),
    ("PowerShell", "Write-Output 'Get-ChildItem -Recurse | Select-String foo'"),
    # Every path operand a named file or one-level file glob: -r walks no tree.
    ("Bash", "grep -rn foo a.py b.md"),
    ("Bash", "grep -rn PROVIDER .claude/tests/*.py | head"),
    ("Bash", "grep -rn \"s9_h\\|headless\" notes/ledger.md 2>/dev/null | head -5"),
    ("Bash", "grep -rn -e foo a.py"),
]


def dispatch(tool, command, env):
    payload = json.dumps({"tool_name": tool, "session_id": "rgd" + uuid.uuid4().hex[:8],
                          "hook_event_name": "PreToolUse", "cwd": REPO,
                          "tool_input": {"command": command}})
    p = subprocess.run([sys.executable, DISPATCH], input=payload, capture_output=True, text=True,
                       encoding="utf-8", timeout=90, env=env, cwd=REPO)
    if p.returncode not in (0, 2) or "Traceback" in (p.stderr or ""):
        return "CRASH", "exit %s: %s" % (p.returncode, (p.stderr or "")[-300:])
    if p.returncode == 2:
        return "deny", p.stderr
    try:
        hso = (json.loads(p.stdout) if p.stdout.strip() else {}).get("hookSpecificOutput") or {}
    except ValueError:
        return "CRASH", "unparseable stdout: " + p.stdout[:200]
    if hso.get("permissionDecision") == "deny":
        return "deny", hso.get("permissionDecisionReason") or ""
    if (hso.get("updatedInput") or {}).get("command") not in (None, command):
        return "rewrite", hso["updatedInput"]["command"] + "\n" + (hso.get("additionalContext") or "")
    return "allow", hso.get("additionalContext") or ""


def main():
    tmp = tempfile.mkdtemp(prefix="rgd_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8")
    failures = []

    def check(label, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (" :: " + detail[:300] if detail else ""))

    for command in FORMER_REWRITES:
        verdict, text = dispatch("Bash", command, env)
        check("deny (former rewrite) %r" % command, verdict == "deny", verdict + " " + text)
        if verdict == "deny":
            check("deny (former rewrite) %r tells the model what to run, never a rewrite" % command,
                  "Grep tool" in text and "rg -n" in text and "git grep -n" in text
                  and "CLAUDE.core.md §9" in text, text)

    for tool, command in DENY:
        verdict, text = dispatch(tool, command, env)
        label = "deny  %-10s %r" % (tool, command)
        check(label, verdict == "deny", verdict + " " + text)
        if verdict == "deny":
            check(label + " names the Grep tool, rg, git grep and CLAUDE.core.md §9, never `| head`",
                  "Grep tool" in text and "rg" in text and "git grep" in text
                  and "CLAUDE.core.md §9" in text and "head -" not in text and "| head" not in text, text)

    for tool, command in ALLOW:
        verdict, text = dispatch(tool, command, env)
        # Another guard may deny for its own reason (git_guardrails on a stale stamp); the arm
        # asserts only that the recursive-grep deny did not fire.
        check("allow %-10s %r (no recursive-grep deny; verdict %s)" % (tool, command, verdict),
              verdict != "CRASH" and "RECURSIVE GREP DENIED" not in text, verdict + " " + text)
        check("allow %-10s %r carries no `| head` advice" % (tool, command),
              "| head" not in text and "head -" not in text, text)

    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


def planted_ignored_repo(tmp_path):
    """A git repo with a tracked file under an ignored directory; `git grep` alone misses it."""
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / '.gitignore').write_text('scratch/\n', encoding='utf-8')
    (tmp_path / 'plain.txt').write_text('ti_needle\n', encoding='utf-8')
    (tmp_path / 'scratch').mkdir()
    (tmp_path / 'scratch' / 'forced.txt').write_text('ti_needle\n', encoding='utf-8')
    git = ['git', '-C', str(tmp_path)]
    subprocess.run(git + ['add', '.gitignore', 'plain.txt'], check=True, capture_output=True)
    subprocess.run(git + ['add', '-f', 'scratch/forced.txt'], check=True, capture_output=True)
    return tmp_path


def rg_available():
    """True when a real `rg` or Claude Code's embedded one (argv0 rg) can run here."""
    if shutil.which("rg"):
        return True
    exe = os.environ.get("CLAUDE_CODE_EXECPATH") or os.path.expanduser("~/.local/bin/claude.exe")
    return os.path.exists(exe)


def run_rg(*args, cwd=None):
    """ripgrep as the agents' search tools run it: a real `rg`, else Claude Code's embedded one (argv0 rg)."""
    if shutil.which("rg"):
        return subprocess.run(["rg"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=120)
    exe = os.environ.get("CLAUDE_CODE_EXECPATH") or os.path.expanduser("~/.local/bin/claude.exe")
    if not os.path.exists(exe):
        raise SystemExit("CANNOT-RUN: no ripgrep available for the deny-form check")
    return subprocess.run(["rg"] + list(args), cwd=cwd, executable=exe, capture_output=True, text=True, timeout=120)


def hook_verdict(command, cwd):
    hook = os.path.join(REPO, '.claude', 'hooks', 'unbounded_scan_guard.py')
    payload = {'tool_name': 'Bash', 'cwd': str(cwd), 'tool_input': {'command': command}}
    p = subprocess.run([sys.executable, hook], input=json.dumps(payload), capture_output=True,
                       text=True, encoding='utf-8', timeout=30)
    assert p.returncode == 0 and 'Traceback' not in p.stderr, p.stderr
    return json.loads(p.stdout)['hookSpecificOutput']


import pytest


@pytest.mark.parametrize('command', FORMER_REWRITES)
def test_former_rewrites_are_denied(command):
    hso = hook_verdict(command, REPO)
    assert hso.get('permissionDecision') == 'deny' and 'updatedInput' not in hso, (command, hso)
    assert '-uu' in hso['permissionDecisionReason'], hso['permissionDecisionReason']


def test_deny_names_the_bounded_forms():
    reason = hook_verdict('grep -rn foo .', REPO)['permissionDecisionReason']
    assert 'Grep tool' in reason and 'head_limit' in reason
    assert 'rg -n <pat> <dir> --max-count N' in reason and '-uu' in reason
    assert 'git grep -n <pat> -- <path>' in reason


def test_the_deny_command_finds_a_tracked_ignored_file(tmp_path):
    """The deny names `rg -uu`; that form must return a tracked file the gitignore rule covers,
    which plain `git grep` misses. This is the flip the rewrite used to make silently."""
    root = planted_ignored_repo(tmp_path)
    reason = hook_verdict('grep -rn ti_needle scratch', root)['permissionDecisionReason']
    assert 'deny' == hook_verdict('grep -rn ti_needle scratch', root)['permissionDecision']
    assert '-uu' in reason
    if not rg_available():
        pytest.skip("no ripgrep on PATH and no embedded rg: the deny's -uu form cannot be exercised here")
    done = run_rg('-uu', '-n', 'ti_needle', 'scratch', '--max-count', '20', cwd=root)
    assert 'forced.txt' in done.stdout, done.stdout + done.stderr


def test_a_tracked_operand_beside_an_ignored_one_is_denied(tmp_path):
    root = planted_ignored_repo(tmp_path)
    hso = hook_verdict('grep -rn ti_needle . scratch', root)
    assert hso.get('permissionDecision') == 'deny' and 'updatedInput' not in hso, hso


def test_unexpressed_shapes_stay_denied(tmp_path):
    root = planted_ignored_repo(tmp_path)
    for command in ('grep -rnz foo .', 'find . -exec grep foo {} +',
                    'bash -c "grep -rn foo ."', 'cd "$UNKNOWN" && grep -rn foo .',
                    "grep -rn 'a\\+b' .", 'grep -rn foo . > output.txt; grep -rnz foo .',
                    'grep -rn foo .claude/**', 'grep -rn foo .claude/nomatch*'):
        hso = hook_verdict(command, root)
        assert hso.get('permissionDecision') == 'deny' and 'updatedInput' not in hso, (command, hso)


if __name__ == "__main__":
    legacy_rc = main()
    import pytest
    pytest_rc = pytest.main([__file__, '-q', '-s'])
    sys.exit(legacy_rc or pytest_rc)
