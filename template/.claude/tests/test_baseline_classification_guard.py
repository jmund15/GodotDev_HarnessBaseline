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


def _seed_project_subsystems(root: Path) -> None:
    """A valid `project_subsystems` adaptation contract (Design §8), so this fixture's
    `classify` calls do not trip the new refusal incidentally. Baked into the seed commit
    (not a later staged add), so it never counts as an unclassified new `.claude/` file for
    this guard's own per-case checks."""
    _write(root / ".claude" / "skills" / "project_subsystems" / "adaptation.json", "{}\n")
    _write(
        root / ".claude" / "skills" / "project_subsystems" / "SKILL.md",
        "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: fixture-subsystem\n    paths: [fixture]\n```\n",
    )


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
    _seed_project_subsystems(root)
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

    # --- 11-15: a lock commit must not drop a row HEAD holds while its file still exists.
    # A peer's temp-index commit updates HEAD but not the shared working-tree lock, so the
    # next pathspec commit of the lock silently reverts the peer's rows.
    def dropped_row_repo():
        repo = _make_consumer_repo()
        for name in ("kept.md", "peer.md"):
            _write(repo / ".claude" / "commands" / name, "# %s\n" % name)
            _git(repo, "add", "-A")
            subprocess.run([sys.executable, str(BASELINE_SYNC), "classify",
                            ".claude/commands/" + name, "--status", "local"],
                           cwd=repo, capture_output=True, text=True, check=True)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "rows")
        lock_path = repo / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        del lock["files"][".claude/commands/peer.md"]
        _write_json(lock_path, lock)
        return repo

    repo11 = dropped_row_repo()
    proc = _run_dispatch(str(repo11), 'git commit -m m -- .claude/baseline.lock.json', env)
    check("11: pathspec lock commit dropping a HEAD row whose file exists -> deny",
          _classify(proc), "DENY", proc)
    cases.append(("11: the denial names the dropped row",
                  ".claude/commands/peer.md" in proc.stderr, proc.stderr[:500]))

    repo12 = dropped_row_repo()
    _git(repo12, "rm", "-q", ".claude/commands/peer.md")
    proc = _run_dispatch(
        str(repo12), 'git commit -m m -- .claude/baseline.lock.json .claude/commands/peer.md', env)
    check("12: the row leaves with its deleted file -> allow", _classify(proc), "ALLOW", proc)

    repo13 = dropped_row_repo()
    _git(repo13, "add", ".claude/baseline.lock.json")
    proc = _run_dispatch(str(repo13), "git commit -m m", env)
    check("13: whole-index commit of a staged lock dropping a row -> deny",
          _classify(proc), "DENY", proc)

    repo14 = dropped_row_repo()
    _write(repo14 / "other.txt", "x\n")
    _git(repo14, "add", "other.txt")
    proc = _run_dispatch(str(repo14), "git commit -m m", env)
    check("14: an unstaged lock edit is not in the commit -> allow", _classify(proc), "ALLOW", proc)

    repo15 = _make_consumer_repo()
    _write(repo15 / ".claude" / "commands" / "fresh.md", "# fresh\n")
    _git(repo15, "add", "-A")
    subprocess.run([sys.executable, str(BASELINE_SYNC), "classify", ".claude/commands/fresh.md",
                    "--status", "local"], cwd=repo15, capture_output=True, text=True, check=True)
    _git(repo15, "add", "-A")
    proc = _run_dispatch(
        str(repo15), 'git commit -m m -- .claude/baseline.lock.json .claude/commands/fresh.md', env)
    check("15: a lock commit that only adds rows -> allow", _classify(proc), "ALLOW", proc)

    # --- 16: a declined row (`absent`: the consumer declines an upstream-only file) has no file
    # by design, so a missing file is no evidence it left: dropping it still denies.
    declined = ".claude/commands/declined.md"
    repo16 = _make_consumer_repo({declined: {
        "status": "local", "layer": None, "absent": True,
        "judged": {"sha": "0" * 64, "verdict": "keep-local", "at": "2026-01-01T00:00:00Z",
                   "borderline": False, "confirmed_at": None}}})
    lock16 = repo16 / ".claude" / "baseline.lock.json"
    body16 = json.loads(lock16.read_text(encoding="utf-8"))
    del body16["files"][declined]
    _write_json(lock16, body16)
    proc = _run_dispatch(str(repo16), 'git commit -m m -- .claude/baseline.lock.json', env)
    check("16: dropping a declined row (no file by design) -> deny", _classify(proc), "DENY", proc)

    # --- 17b-18: `_dropped_lock_rows` alone, since git_guardrails' stamp check runs its own
    # `git diff HEAD` first and would decide both commits before this guard is reached.
    import importlib.util
    spec = importlib.util.spec_from_file_location("bcg_under_test", GUARD_PATH)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    lock_rel = ".claude/baseline.lock.json"
    rest = ["-m", "m", "--", lock_rel]

    repo17b = _make_consumer_repo()
    _git(repo17b, "rm", "-q", "--cached", lock_rel)
    _git(repo17b, "commit", "-q", "-m", "untrack lock")
    _git(repo17b, "add", lock_rel)
    cases.append(("17b: HEAD without a lock: adding the lock drops nothing",
                  guard._dropped_lock_rows(rest, str(repo17b), str(repo17b)) == ([], None), ""))

    repo18 = dropped_row_repo()
    real_run_git = guard.run_git
    guard.run_git = lambda args, cwd, env=None: (
        None if args[:1] == ["show"] and args[1].startswith("HEAD:") else real_run_git(args, cwd, env))
    try:
        result18 = guard._dropped_lock_rows(rest, str(repo18), str(repo18))
    finally:
        guard.run_git = real_run_git
    cases.append(("18: HEAD lists the lock but its read fails: unknown -> a failing step, never allow",
                  result18[0] is None and "HEAD:" + lock_rel in (result18[1] or ""), repr(result18)))

    failures = [(label, detail) for label, ok, detail in cases if not ok]
    for label, ok, detail in cases:
        print("%-4s %s%s" % ("ok" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
