"""Shared cases for every PreToolUse guard that gates `git commit` through hooks/_git_commit.py.

A guard's own domain scan is its own proof; these cases prove the DETECTION half every guard
shares: a non-Bash tool is ignored, a `git commit` quoted inside a heredoc body does not fire,
and a real commit of a non-domain file in a clean temp repo passes. Each `test_<guard>_commit_
detection.py` is one line: `run_for("<guard>.py")`.
"""
import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_repo():
    repo = tempfile.mkdtemp(prefix="cgcases_")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    with open(os.path.join(repo, "README.md"), "a") as fh:
        fh.write("more\n")
    _git(repo, "add", "README.md")
    return repo


def _run(hook, payload, cwd):
    r = subprocess.run([sys.executable, os.path.join(HOOKS, hook), "--hook"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       timeout=120, cwd=cwd)
    # A crashed hook exits 1 with a traceback and the harness treats it as non-blocking —
    # the same silence as an allow. Name it, or a broken guard reads as a clean proof.
    if r.returncode not in (0, 2) or "Traceback" in (r.stderr or ""):
        return "crash", (r.stdout + r.stderr)[-300:]
    denied = r.returncode == 2 or '"deny"' in (r.stdout or "")
    return "deny" if denied else "allow", (r.stdout + r.stderr)[-300:]


def run_for(hook):
    repo = _make_repo()
    base = {"tool_name": "Bash", "session_id": "s1", "cwd": repo}
    cases = [
        ("a non-Bash tool is ignored",
         dict(base, tool_name="Read", tool_input={"file_path": "x"})),
        ("`git commit` inside a heredoc body does not fire the guard",
         dict(base, tool_input={"command": "cat > m <<'EOF'\ngit commit -am x\nEOF\necho done"})),
        ("a real commit of a non-domain file in a clean repo passes",
         dict(base, tool_input={"command": "git commit -q -F m -- README.md"})),
    ]
    failures = []
    for label, payload in cases:
        verdict, tail = _run(hook, payload, repo)
        ok = verdict == "allow"
        print("%-4s %s: %s" % ("ok" if ok else "FAIL", hook, label))
        if not ok:
            failures.append("%s: %s -> %r" % (hook, label, tail))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0
