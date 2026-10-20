"""Re-runnable proof for hooks/harness_shell_write_guard.py: a shell command that writes a governed
harness file is denied before it runs; a command that only names one, or writes elsewhere, is allowed.

Cases feed real PreToolUse payloads on stdin and assert on the emitted channel. An exit outside {0}
or a traceback is CRASH, never allow.

    python3 .claude/tests/test_harness_shell_write_guard.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "harness_shell_write_guard.py")
DISPATCH = os.path.join(HERE, "..", "hooks", "pre_bash_dispatch.py")
REPO = "C:/repo"

ALLOW, DENY, CRASH = "allow", "deny", "crash"


def run(command, tool="Bash", cwd=REPO, hook=HOOK):
    payload = {"session_id": "hswg0001", "tool_name": tool, "cwd": cwd,
               "tool_input": {"command": command}, "hook_event_name": "PreToolUse"}
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60)
    if r.returncode not in (0, 2) or "Traceback" in r.stderr:
        return CRASH, r.stderr
    out = (r.stdout or "").strip()
    if r.returncode == 2:
        return DENY, r.stderr
    if not out or out == "{}":
        return ALLOW, ""
    hook_out = json.loads(out).get("hookSpecificOutput") or {}
    if hook_out.get("permissionDecision") == "deny":
        return DENY, hook_out.get("permissionDecisionReason", "")
    return ALLOW, ""


HEREDOC_WRITE = (
    "python3 - <<'EOF'\n"
    "from pathlib import Path\n"
    "p = Path('.claude/hooks/sidecar_dispatch_context.py')\n"
    "s = p.read_text()\n"
    "p.write_text(s.replace('a', 'b'))\n"
    "EOF"
)
HEREDOC_READ = (
    "python3 - <<'EOF'\n"
    "print(open('.claude/hooks/sidecar_dispatch_context.py').read())\n"
    "EOF"
)
HEREDOC_READ_HARNESS_WRITE_SCRATCH = (
    "python3 - <<'EOF'\n"
    "src = open('.claude/hooks/x.py').read()\n"
    "open('.claude/scratch/out.txt', 'w').write(src)\n"
    "EOF"
)

REBOUND = (
    "python3 - <<'EOF'\n"
    "p = '.claude/hooks/x.py'\n"
    "src = open(p).read()\n"
    "p = '.claude/scratch/out.py'\n"
    "open(p, 'w').write(src)\n"
    "EOF"
)
RELATIVE_BINDING = (
    "python3 - <<'EOF'\n"
    "p = 'reference/model_ladder_evidence.md'\n"
    "s = open(p).read()\n"
    "open(p, 'w').write(s)\n"
    "EOF"
)

HELPER_WRITE = (
    "python3 - <<'EOF'\n"
    "def rep(p, a, b):\n"
    "    s = open(p, encoding='utf-8').read()\n"
    "    open(p, 'w', encoding='utf-8').write(s.replace(a, b))\n"
    "rep('.claude/skills/video_frames/SKILL.md', 'x', 'y')\n"
    "EOF"
)
CODE_TEXT = (
    "python3 - <<'EOF'\n"
    "p = 'tests/test_model_registry.py'\n"
    "s = open(p).read()\n"
    "new = '''\n"
    "        path = os.path.join(td, \"ladder.md\")\n"
    "        with open(path, \"w\") as handle:\n"
    "'''\n"
    "open(p, 'w').write(s + new)\n"
    "EOF"
)

CASES = [
    # The incident's shape and every other write form on a governed path.
    ("python heredoc write_text to a hook", HEREDOC_WRITE, "Bash", DENY),
    ("cat heredoc redirect to a skill", "cat > .claude/skills/testing/SKILL.md <<'EOF'\nbody\nEOF", "Bash", DENY),
    ("append redirect to a command", "echo x >> .claude/commands/codify.md", "Bash", DENY),
    ("sed -i on a rail", "sed -i 's/a/b/' .claude/guards/author.md", "Bash", DENY),
    ("tee to a rule", "printf x | tee .claude/rules/harness_tooling.md", "Bash", DENY),
    ("cp from scratch into hooks", "cp .claude/scratch/new.py .claude/hooks/new_guard.py", "Bash", DENY),
    ("cp into hooks dir", "cp .claude/scratch/new_guard.py .claude/hooks/", "Bash", DENY),
    ("python -c open w", "python3 -c \"open('.claude/hooks/x.py','w').write('y')\"", "Bash", DENY),
    ("absolute settings redirect", "echo '{}' > C:/repo/.claude/settings.json", "Bash", DENY),
    ("worktree hook redirect", "echo x > .claude/worktrees/wt1/.claude/hooks/y.py", "Bash", DENY),
    ("relative write from inside .claude", "echo x > guards/any.md", "Bash", DENY, REPO + "/.claude"),
    ("PowerShell Set-Content", "Set-Content -Path .claude/skills/x/SKILL.md -Value 'y'", "PowerShell", DENY),
    ("PowerShell redirect", "'y' > .claude/commands/x.md", "PowerShell", DENY),
    ("pwsh -Command from Bash", "pwsh -NoProfile -Command \"Set-Content .claude/guards/any.md 'y'\"", "Bash", DENY),
    # Mentions, reads and writes elsewhere.
    ("grep a hook", "grep -n foo .claude/hooks/x.py", "Bash", ALLOW),
    ("cat a hook into scratch", "cat .claude/hooks/x.py > .claude/scratch/copy.py", "Bash", ALLOW),
    ("sed -n a rail", "sed -n 5p .claude/guards/author.md", "Bash", ALLOW),
    ("tool run naming a command", "python3 .claude/tools/autolearn_outcomes.py history --target .claude/commands/codify.md", "Bash", ALLOW),
    ("python heredoc read", HEREDOC_READ, "Bash", ALLOW),
    ("python heredoc reads harness, writes scratch", HEREDOC_READ_HARNESS_WRITE_SCRATCH, "Bash", ALLOW),
    ("scratch message", "echo x > .claude/scratch/msg.md", "Bash", ALLOW),
    ("test file heredoc", "cat > .claude/tests/test_x.py <<'EOF'\nx\nEOF", "Bash", ALLOW),
    ("commit naming a hook", "git commit -F .claude/scratch/msg -- .claude/hooks/x.py", "Bash", ALLOW),
    ("git checkout of a hook", "git checkout -- .claude/hooks/x.py", "Bash", ALLOW),
    ("cp a hook out to scratch", "cp .claude/hooks/x.py .claude/scratch/", "Bash", ALLOW),
    ("mv between hooks", "mv .claude/hooks/a.py .claude/hooks/b.py", "Bash", ALLOW),
    ("quoted mention of a write", "echo \"echo x > .claude/hooks/y.py\"", "Bash", ALLOW),
    ("stderr redirect", "cat .claude/hooks/x.py 2>&1 | head", "Bash", ALLOW),
    ("PowerShell read", "Get-Content .claude/guards/any.md", "PowerShell", ALLOW),
    ("memory write", "echo x > .claude/auto-memory/gotcha_x.md", "Bash", ALLOW),
    ("Monitor tool", "tail -f x > .claude/hooks/y.py", "Monitor", ALLOW),
    # Replay of real commands: the forms that first read as writes and are not.
    ("cd into scratch, relative write", "cd .claude/scratch/t1 && echo x > guards.md", "Bash", ALLOW),
    ("cd into .claude, relative write", "cd .claude && echo x >> guards/any.md", "Bash", DENY),
    ("unresolved variable path", "D=.claude/scratch/x && cat > $D/brief.md <<'EOF'\nx\nEOF", "Bash", ALLOW),
    ("git show restore to HEAD", "git show HEAD:.claude/hooks/x.py > .claude/hooks/x.py", "Bash", ALLOW),
    ("rebound name writes only its last binding", REBOUND, "Bash", ALLOW),
    ("relative binding written from inside .claude", RELATIVE_BINDING, "Bash", DENY, REPO + "/.claude"),
    ("helper function writes its path parameter", HELPER_WRITE, "Bash", DENY),
    ("joined path inside written code text", CODE_TEXT, "Bash", ALLOW, REPO + "/.claude"),
]


def main():
    failures = []
    for case in CASES:
        label, command, tool, want = case[:4]
        cwd = case[4] if len(case) > 4 else REPO
        got, reason = run(command, tool, cwd)
        if got != want:
            failures.append("%s: want %s got %s %s" % (label, want, got, reason[:300]))
        elif got == DENY and ("Edit" not in reason or "CLAUDE.core.md" not in reason):
            failures.append("%s: deny names no next step or rule home: %s" % (label, reason[:300]))

    # Wired: the Bash dispatcher reaches the guard.
    got, reason = run(HEREDOC_WRITE, hook=DISPATCH)
    if got != DENY:
        failures.append("dispatcher: want deny got %s %s" % (got, reason[:300]))

    total = len(CASES) + 1
    for line in failures:
        print("FAIL " + line)
    print("%d/%d passed" % (total - len(failures), total))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
