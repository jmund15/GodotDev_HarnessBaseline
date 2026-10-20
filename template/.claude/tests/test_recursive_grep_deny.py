#!/usr/bin/env python3
"""Proof for the recursive-grep deny in hooks/unbounded_scan_guard.py, fed through the real
PreToolUse entry hooks/pre_bash_dispatch.py.

Rewrite arms are plain recursive greps whose every path is tracked repo content: they run as
`git grep --untracked` (the same files minus gitignored ones). Deny arms are the shapes the facts do
not settle: ignored trees, indirect runs (find, xargs, a shell -c, PowerShell), unknown flags, GNU-only
regex escapes. Allow arms mention a recursive grep without running one.
A dispatcher exit outside {0, 2}, or a traceback on stderr, is CRASH, never allow.

    python3 .claude/tests/test_recursive_grep_deny.py
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(REPO, ".claude", "hooks", "pre_bash_dispatch.py")

REWRITE = [
    ("grep -rn -- '-needle' .", "git grep --untracked -n -e -needle -- ."),
    ("grep -rln ProjectSuite .", "git grep --untracked -l -n -e ProjectSuite -- ."),
    ("grep -rn noclobber .claude/", "git grep --untracked -n -e noclobber -- .claude/"),
    ("grep -rn foo", "git grep --untracked -n -e foo -- ."),
    ("grep -rn -e foo -e bar", "git grep --untracked -n -e foo -e bar -- ."),
    ("grep -rn foo .claude | head -5", "git grep --untracked -n -e foo -- .claude | head -5"),
    ("grep -rni 'Spawn Impact' .claude/hooks", "git grep --untracked -n -i -e 'Spawn Impact' -- .claude/hooks"),
    ("grep -rn 'a\\|b' .claude", "git grep --untracked -n -e 'a\\|b' -- .claude"),
    ("echo start\ngrep -rn foo .", "echo start\ngit grep --untracked -n -e foo -- ."),
    ("grep -rn foo --include=*.py .claude/hooks",
     "MSYS_NO_PATHCONV=1 "  # bash_backslash_fidelity prefixes a ref:path-shaped operand on every host
     + "git grep --untracked -n -e foo -- ':(glob).claude/hooks/**/*.py'"),
]

DENY = [
    ("Bash", "grep -rnz foo ."),
    ("Bash", "grep -R foo src"),
    ("Bash", "cd x && grep -rn foo ."),
    ("Bash", "find . -type f | xargs grep -l foo"),
    ("Bash", "find . -name '*.md' -exec grep -l foo {} +"),
    ("PowerShell", "Get-ChildItem -Recurse | Select-String foo"),
    ("Bash", "egrep --recursive foo Tests"),
    ("Bash", "grep -d recurse foo ."),
    ("Bash", "grep --directories=recurse foo ."),
    ("Bash", "git status || fgrep -rl foo ."),
    ("Bash", "ls; grep -nr foo src | wc -l"),
    ("Bash", "printf '%s\\n' a b | xargs -n1 grep -r foo"),
    ("Bash", "find . -exec grep -l foo {} \\;"),
    ("Bash", "bash -c 'grep -rn foo .'"),
    ("PowerShell", "gci -r -Filter *.cs | sls foo"),
    ("PowerShell", "Select-String -Pattern foo -Path (Get-ChildItem -Recurse -File)"),
    ("PowerShell", "grep -rn foo ."),
    # -r with no path operand walks the cwd; a directory operand among files walks it; xargs or
    # find appends operands the command text cannot show.
    ("Bash", "grep -rn foo a.py src"),
    ("Bash", "grep -rn foo .claude/**/*.py"),
    ("Bash", "printf '%s\\n' a.py | xargs grep -rn foo b.py"),
    ("Bash", "find . -name x | xargs grep -rl foo a.py"),
]

# An ignored tree denies only where the checkout ignores it.
if subprocess.run(["git", "-C", REPO, "check-ignore", "-q", ".claude/scratch/x"]).returncode == 0:
    DENY.append(("Bash", "grep -rn foo .claude/scratch"))

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

    for command, want in REWRITE:
        verdict, text = dispatch("Bash", command, env)
        got = text.split("\n[scan]", 1)[0]
        check("rewrite %r -> %r" % (command, want), verdict == "rewrite" and got == want, verdict + " " + text)
        if verdict == "rewrite":
            check("rewrite %r tells the model what ran and cites CLAUDE.core.md §9" % command,
                  "git grep --untracked" in text and "CLAUDE.core.md §9" in text.split("\n", 1)[-1], text)

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


# Captured corpus commands, trimmed to the grep-bearing statements. Fixtures supply their paths.
CORPUS_SHAPES = [
    "grep -rln 'private index\\|private-index\\|stale base\\|stale index' .claude/auto-memory | head",
    'grep -rn "AskUserQuestion" .claude/commands .claude/skills | wc -l; echo ---',
    'grep -rln ".git" .claude/hooks 2>/dev/null | head',
    'git status 2>&1 | head; grep -rn "new .claude/ file\\|lock row\\|no lock row\\|missing a lock" .claude/hooks/ .claude/tools/baseline_sync.py 2>/dev/null | head -5 | cut -c1-200',
    'grep -rn --include=*.py --include=*.sh --include=*.md -E "worst_of|compute_band\\(|band_for\\(" .claude --exclude-dir=worktrees --exclude-dir=cache',
]


def planted_repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    for directory in ('auto-memory', 'commands', 'skills', 'hooks', 'worktrees', 'cache'):
        path = tmp_path / '.claude' / directory
        path.mkdir(parents=True)
        (path / 'fixture.py').write_text('private index AskUserQuestion .git worst_of\n', encoding='utf-8')
    (tmp_path / '.claude' / 'tools').mkdir()
    (tmp_path / '.claude' / 'tools' / 'baseline_sync.py').write_text('lock row\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    return tmp_path


def hook_rewrite(command, cwd):
    hook = os.path.join(REPO, '.claude', 'hooks', 'unbounded_scan_guard.py')
    payload = {'tool_name': 'Bash', 'cwd': str(cwd), 'tool_input': {'command': command}}
    p = subprocess.run([sys.executable, hook], input=json.dumps(payload), capture_output=True,
                       text=True, encoding='utf-8', timeout=30)
    assert p.returncode == 0 and 'Traceback' not in p.stderr, p.stderr
    return json.loads(p.stdout)['hookSpecificOutput']


import pytest


@pytest.mark.parametrize('command', CORPUS_SHAPES)
def test_corpus_shapes_rewrite_and_preserve_results(tmp_path, command):
    root = planted_repo(tmp_path)
    hso = hook_rewrite(command, root)
    assert 'updatedInput' in hso, (command, hso)
    rewritten = hso['updatedInput']['command']
    assert 'CLAUDE.core.md §9' in hso['additionalContext']
    old = subprocess.run(['bash', '-c', command], cwd=root, capture_output=True, stdin=subprocess.DEVNULL)
    new = subprocess.run(['bash', '-c', rewritten], cwd=root, capture_output=True, stdin=subprocess.DEVNULL)
    # grep walks in directory order and git grep in sorted order; the lines must agree, not their order.
    assert (new.returncode, sorted(new.stdout.splitlines()), new.stderr) == (old.returncode, sorted(old.stdout.splitlines()), old.stderr), rewritten


def test_separate_include_exclude_and_literal_cd(tmp_path):
    root = planted_repo(tmp_path)
    command = 'cd .claude && grep -rn --include "*.py" --exclude-dir cache --exclude fixture.md worst_of .'
    hso = hook_rewrite(command, root)
    assert 'updatedInput' in hso, hso
    rewritten = hso['updatedInput']['command']
    assert ':(glob)**/*.py' in rewritten and ':(exclude,glob)**/cache/**' in rewritten
    assert ':(exclude,glob)**/fixture.md' in rewritten
    old = subprocess.run(['bash', '-c', command], cwd=root, capture_output=True, stdin=subprocess.DEVNULL)
    new = subprocess.run(['bash', '-c', rewritten], cwd=root, capture_output=True, stdin=subprocess.DEVNULL)
    # git's filenames omit grep's leading './' and grep walks in directory order, git grep in sorted order; the selected files and matching lines must agree.
    assert sorted(new.stdout.splitlines()) == sorted(line.removeprefix(b'./') for line in old.stdout.splitlines())
    assert new.returncode == old.returncode


def test_unexpressed_shapes_stay_denied(tmp_path):
    root = planted_repo(tmp_path)
    for command in ('grep -rnz foo .', 'find . -exec grep foo {} +',
                    'bash -c "grep -rn foo ."', 'cd "$UNKNOWN" && grep -rn foo .',
                    "grep -rn 'a\\+b' .", 'grep -rn foo . > output.txt; grep -rnz foo .'):
        hso = hook_rewrite(command, root)
        assert hso.get('permissionDecision') == 'deny' and 'updatedInput' not in hso, (command, hso)


if __name__ == "__main__":
    legacy_rc = main()
    import pytest
    pytest_rc = pytest.main([__file__, '-q', '-s'])
    sys.exit(legacy_rc or pytest_rc)
