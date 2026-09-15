#!/usr/bin/env python3
"""Re-runnable S4 proof for hooks/baseline_classification_guard.py -- every case runs through
the REAL `pre_bash_dispatch.py` subprocess channel (not a direct call into the guard), so the
proof also covers the guard's registration and its import-failure stub.

Channel contract: a deny is `hookSpecificOutput.permissionDecision == "deny"` on stdout
(exit 0) OR a bare stderr message with exit 2 -- both are the harness's own established deny
shapes (`git_guardrails.py` uses the exit-2 shape). An exit code outside {0, 2}, or ANY
stderr traceback, is a CRASH and fails its case outright: a hook that cannot import must not
be misread as "allowed".

    python3 .claude/tests/test_baseline_classification_guard.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS_DIR = HERE.parent / "hooks"
TOOLS_DIR = HERE.parent / "tools"
REPO = HERE.parent.parent  # template/
DISPATCH = HOOKS_DIR / "pre_bash_dispatch.py"
GUARD_PATH = HOOKS_DIR / "baseline_classification_guard.py"
BASELINE_SYNC = TOOLS_DIR / "baseline_sync.py"


def _env() -> dict:
    tmp = tempfile.mkdtemp(prefix="bcg_state_")
    return dict(os.environ, HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "state"),
                CLAUDE_PROJECT_DIR=str(REPO), PYTHONIOENCODING="utf-8",
                GIT_TERMINAL_PROMPT="0")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, indent=2) + "\n")


def _make_consumer_repo(files: dict | None = None) -> Path:
    """A minimal baseline-consumer repo: `.claude/baseline.lock.json` at its root, one seed
    commit. `files` seeds `lock["files"]` rows."""
    root = Path(tempfile.mkdtemp(prefix="bcg_consumer_"))
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    lock = {
        "schema": 2, "profile": "pure,coding,godot", "substitutions": {},
        "baseline_repo": "https://example.invalid/harness-baseline.git",
        "baseline_ref": "main", "synced_commit": "0" * 40,
        "identity": {"abbreviations": []},
        "files": dict(files or {}),
    }
    _write_json(root / ".claude" / "baseline.lock.json", lock)
    _write(root / "README.md", "seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _payload(cwd: str, command: str, tool_name: str = "Bash") -> str:
    return json.dumps({
        "tool_name": tool_name,
        "session_id": "bcg" + uuid.uuid4().hex[:8],
        "cwd": cwd,
        "tool_input": {"command": command},
    })


def _run_dispatch(cwd: str, command: str, env: dict, tool_name: str = "Bash") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DISPATCH)], input=_payload(cwd, command, tool_name),
                          capture_output=True, text=True, timeout=90, env=env, encoding="utf-8",
                          cwd=REPO)


def _hso(proc: subprocess.CompletedProcess) -> dict:
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}


def _classify(proc: subprocess.CompletedProcess) -> str:
    """ALLOW / DENY / CRASH, per the channel contract in the module docstring."""
    if "Traceback (most recent call last)" in proc.stderr:
        return "CRASH"
    if proc.returncode not in (0, 2):
        return "CRASH"
    if proc.returncode == 2:
        return "DENY"
    if _hso(proc).get("permissionDecision") == "deny":
        return "DENY"
    return "ALLOW"


def main() -> int:
    cases = []
    env = _env()

    def check(label, verdict, expected, proc):
        ok = verdict == expected
        cases.append((label, ok, "" if ok else "got %s, stdout=%r stderr=%r"
                      % (verdict, proc.stdout[:300], proc.stderr[:500])))

    # --- 1: a new unclassified .claude/ file, pathspec commit -> DENY -------------------
    repo = _make_consumer_repo()
    # A path outside .claude/{hooks,tools,scripts,workflows,tests} so `git_guardrails.py`'s
    # unrelated harness-stamp check (same HARNESS_DIRS, a different guard, a different
    # denial reason) never fires first and masks this guard's own verdict.
    rel = ".claude/commands/new_thing.md"
    _write(repo / rel, "# new command\n")
    _git(repo, "add", "-A")
    proc = _run_dispatch(str(repo), 'git commit -m "add thing" -- .claude/commands/new_thing.md', env)
    check("1: pathspec commit adding an unclassified .claude/ file -> deny",
          _classify(proc), "DENY", proc)

    # --- 2: after `classify`, the same commit is allowed --------------------------------
    classify_proc = subprocess.run(
        [sys.executable, str(BASELINE_SYNC), "classify", rel, "--status", "local"],
        cwd=repo, capture_output=True, text=True,
    )
    cases.append(("2 setup: classify succeeds", classify_proc.returncode == 0,
                  "" if classify_proc.returncode == 0 else classify_proc.stdout + classify_proc.stderr))
    proc = _run_dispatch(str(repo), 'git commit -m "add thing" -- .claude/commands/new_thing.md', env)
    check("2: the same commit is allowed once the row is classified",
          _classify(proc), "ALLOW", proc)

    # --- 3: a quoted mention through `echo` is not a commit ------------------------------
    proc = _run_dispatch(str(repo), 'echo "git commit -- x"', env)
    check("3: echo of a commit-shaped string is not judged", _classify(proc), "ALLOW", proc)

    # --- 4: read-only mentions of `git commit` / a tracked file are allowed -------------
    for label, command in (
        ("4a: grep for the phrase 'git commit'", 'grep -n "git commit" .claude/commands/does_not_exist.md'),
        ("4b: git show of a tracked file", "git show HEAD -- README.md"),
        ("4c: cat of a tool file", "cat .claude/tools/does_not_exist.py"),
    ):
        proc = _run_dispatch(str(repo), command, env)
        check(label + " -> allow", _classify(proc), "ALLOW", proc)

    # --- 5: `git -C <repo> commit -m m`, no pathspec, unclassified file -> DENY ----------
    repo5 = _make_consumer_repo()
    rel5 = ".claude/commands/new_hook.md"
    _write(repo5 / rel5, "# new command\n")
    _git(repo5, "add", "-A")
    proc = _run_dispatch(str(REPO), 'git -C "%s" commit -m m' % str(repo5).replace("\\", "/"), env)
    check("5: git -C <repo> commit, whole index, unclassified file -> deny",
          _classify(proc), "DENY", proc)

    # --- 6: `git mv` of an unclassified file from outside .claude/ into .claude/ -> DENY -
    repo6 = _make_consumer_repo()
    (repo6 / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
    _write(repo6 / "notes.txt", "hello\n")
    _git(repo6, "add", "-A")
    _git(repo6, "commit", "-q", "-m", "add notes")
    _git(repo6, "mv", "notes.txt", ".claude/commands/moved.md")
    proc = _run_dispatch(str(repo6), "git commit -m mv", env)
    check("6: git mv into .claude/ reads as an unclassified addition -> deny",
          _classify(proc), "DENY", proc)

    # --- 7: a guard import failure denies every git commit -------------------------------
    original = GUARD_PATH.read_bytes()
    try:
        GUARD_PATH.write_bytes(b"raise ImportError('planted S4 import failure')\n")
        repo7 = Path(tempfile.mkdtemp(prefix="bcg_importfail_"))
        _git(repo7, "init", "-q")
        proc = _run_dispatch(str(repo7), "git commit -m x", env)
        check("7: a guard import failure denies -- never allows -- a git commit",
              _classify(proc), "DENY", proc)
        cases.append(("7: the stub names the import error",
                      "planted S4 import failure" in proc.stderr, proc.stderr[:500]))
    finally:
        GUARD_PATH.write_bytes(original)

    # --- 8: a commit inside a baseline author worktree is not judged --------------------
    repo8 = _make_consumer_repo()
    wt = repo8 / ".claude" / ".cache" / "baseline-worktrees" / "abc12345"
    wt.mkdir(parents=True)
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    _write(wt / "template" / ".claude" / "hooks" / "new_thing.py", "x = 1\n")
    _git(wt, "add", "-A")
    proc = _run_dispatch(str(wt), 'git commit -m x', env)
    check("8: a commit inside a baseline author worktree is not judged -> allow",
          _classify(proc), "ALLOW", proc)

    # --- 9: an unparseable baseline.lock.json denies -------------------------------------
    repo9 = _make_consumer_repo()
    _write(repo9 / ".claude" / "baseline.lock.json", "{not json")
    _write(repo9 / "other.txt", "x\n")
    _git(repo9, "add", "-A")
    proc = _run_dispatch(str(repo9), 'git commit -m x', env)
    check("9: an unparseable baseline.lock.json denies the commit",
          _classify(proc), "DENY", proc)

    # --- 10: adjacent commands that are not commits are not judged ----------------------
    scratch = Path(tempfile.mkdtemp(prefix="bcg_scratch_"))
    for label, command in (
        ("10a: git commit-graph write is not a commit", "git commit-graph write"),
        ("10b: git log --grep=commit is not a commit", 'git log --grep="commit"'),
    ):
        proc = _run_dispatch(str(scratch), command, env)
        check(label + " -> allow", _classify(proc), "ALLOW", proc)

    failures = [(label, detail) for label, ok, detail in cases if not ok]
    for label, ok, detail in cases:
        print("%-4s %s%s" % ("ok" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
