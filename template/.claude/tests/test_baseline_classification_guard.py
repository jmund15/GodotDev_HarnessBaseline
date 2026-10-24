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

sys.path.insert(0, str(HERE))
from _case_pool import emit_section, run_sections  # noqa: E402
from _must_allow_cases import must_allow_cases  # noqa: E402
sys.path.insert(0, str(TOOLS_DIR))
from sidecar_launch import git_bash  # noqa: E402


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


def _consumer_seed(files: dict | None = None) -> dict:
    """A consumer's committed `.claude/` files: `baseline.lock.json`, whose `lock["files"]` rows
    `files` seeds, and a valid `project_subsystems` adaptation contract (Design §8), so this
    fixture's `classify` calls do not trip the new refusal incidentally. Baked into the seed commit
    (not a later staged add), so none counts as an unclassified new `.claude/` file for this
    guard's own per-case checks."""
    lock = {
        "schema": 2, "profile": "pure,coding,godot", "substitutions": {},
        "baseline_repo": "https://example.invalid/harness-baseline.git",
        "baseline_ref": "main", "synced_commit": "0" * 40,
        "identity": {"abbreviations": []},
        "files": dict(files or {}),
    }
    return {
        ".claude/baseline.lock.json": json.dumps(lock, indent=2) + "\n",
        ".claude/skills/project_subsystems/adaptation.json": "{}\n",
        ".claude/skills/project_subsystems/SKILL.md":
            "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: fixture-subsystem\n    paths: [fixture]\n```\n",
    }


# A consumer whose merged branch adds an unclassified `.claude/` file.
CONSUMER_PLANTED = {"head": _consumer_seed(),
                    "offend": {".claude/commands/from_upstream.md": "# from the merged ref\n"}}


def _make_consumer_repo(files: dict | None = None) -> Path:
    """A minimal baseline-consumer repo holding `_consumer_seed(files)`, one seed commit."""
    root = Path(tempfile.mkdtemp(prefix="bcg_consumer_"))
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    for rel, text in _consumer_seed(files).items():
        _write(root / rel, text)
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


_IMPORT_FAILURE_RUNNER = (
    "import runpy, sys\n"
    "class _Plant:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'baseline_classification_guard':\n"
    "            raise ImportError('planted S4 import failure')\n"
    "sys.meta_path.insert(0, _Plant())\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


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


SHELL_WORD_CASES = (
    ('variable file', 'msg="message file.txt"; git commit -F "$msg"', '-F "$msg"'),
    ('variable message', 'message="intended shell message"; git commit -m "$message"', '-m "$message"'),
    ('variable global value',
     'config="commit.cleanup=verbatim"; git -c "$config" commit -m "intended shell message"',
     '-c "$config"'),
)


def _shell_words_case(spec, env, cases):
    """A raw shell expansion survives the commit rewrite, runs, and Git reads the intended message."""
    label, command, expansion = spec
    shell_repo = _make_consumer_repo()
    _write(shell_repo / 'message file.txt', 'intended shell message\n')
    _write(shell_repo / '.claude/commands/shell.md', '# shell words\n')
    _git(shell_repo, 'add', '--', '.claude/commands/shell.md')
    proc = _run_dispatch(str(shell_repo), command, env)
    rewritten = (_hso(proc).get('updatedInput') or {}).get('command', '')
    cases.append((label + ': rewrite keeps raw expansion',
                  _classify(proc) == 'ALLOW' and expansion in rewritten and 'GIT_INDEX_FILE=' in rewritten,
                  proc.stderr + rewritten))
    executed = subprocess.run([git_bash(), '-c', rewritten], cwd=shell_repo, env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=90)
    cases.append((label + ': rewritten command succeeds', executed.returncode == 0,
                  executed.stdout + executed.stderr))
    message = subprocess.run(['git', 'log', '-1', '--format=%B'], cwd=shell_repo,
                             capture_output=True, text=True, encoding='utf-8', check=True).stdout.strip()
    cases.append((label + ': Git reads the intended message',
                  message == 'intended shell message', repr(message)))


def _must_allow_section(planted, env, check):
    """26: must-allow cases (`_must_allow_cases`): a merge in a fresh worktree, the commit closing it, and a
    commit in a fresh clone lacking the gitignored inputs."""
    allow_cases, cleanup = must_allow_cases("baseline_classification_guard.py", planted=planted)
    try:
        for label, payload, cwd, expected in allow_cases:
            proc = _run_dispatch(cwd, payload["tool_input"]["command"], env)
            check("26 " + label, _classify(proc), expected.upper(), proc)
    finally:
        cleanup()


def main(only: int | None = None) -> int:
    cases = []
    sections = []

    def section(fn):
        sections.append(fn)
        return fn
    env = _env()

    def check(label, verdict, expected, proc):
        ok = verdict == expected
        cases.append((label, ok, "" if ok else "got %s, stdout=%r stderr=%r"
                      % (verdict, proc.stdout[:300], proc.stderr[:500])))

    @section
    def _s0():
        # --- 1: a new unclassified .claude/ file, pathspec commit -> DENY -------------------
        repo = _make_consumer_repo()
        # A path outside .claude/{hooks,tools,scripts,workflows,tests} so `git_guardrails.py`'s
        # unrelated harness-stamp check (same HARNESS_DIRS, a different guard, a different
        # denial reason) never fires first and masks this guard's own verdict.
        rel = ".claude/commands/new_thing.md"
        _write(repo / rel, "# new command\n")
        _git(repo, "add", "-A")
        proc = _run_dispatch(str(repo), 'git commit -m "add thing" -- .claude/commands/new_thing.md', env)
        check("1: pathspec commit adding an unclassified .claude/ file -> private-index rewrite",
              _classify(proc), "ALLOW", proc)
        cases.append(("1: the rewrite names its private index and resync",
                      'GIT_INDEX_FILE=' in (_hso(proc).get('updatedInput') or {}).get('command', '')
                      and 'resync --name' in (_hso(proc).get('updatedInput') or {}).get('command', ''), proc.stdout))

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

    @section
    def _s1():
        # --- 5: `git -C <repo> commit -m m`, no pathspec, unclassified file -> DENY ----------
        repo5 = _make_consumer_repo()
        rel5 = ".claude/commands/new_hook.md"
        _write(repo5 / rel5, "# new command\n")
        _git(repo5, "add", "-A")
        proc = _run_dispatch(str(REPO), 'git -C "%s" commit -m m' % str(repo5).replace("\\", "/"), env)
        check("5: git -C <repo> commit, whole index, unclassified file -> rewrite",
              _classify(proc), "ALLOW", proc)

    @section
    def _s2():
        # --- 6: `git mv` of an unclassified file from outside .claude/ into .claude/ -> DENY -
        repo6 = _make_consumer_repo()
        (repo6 / ".claude" / "commands").mkdir(parents=True, exist_ok=True)
        _write(repo6 / "notes.txt", "hello\n")
        _git(repo6, "add", "-A")
        _git(repo6, "commit", "-q", "-m", "add notes")
        _git(repo6, "mv", "notes.txt", ".claude/commands/moved.md")
        proc = _run_dispatch(str(repo6), "git commit -m mv", env)
        check("6: git mv into .claude/ reads as an unclassified addition -> rewrite",
              _classify(proc), "ALLOW", proc)

    @section
    def _s3():
        # --- 7: a guard import failure denies every git commit -------------------------------
        # The failure is planted in the child's import system, never in the live hook file: every
        # concurrent session's Bash runs that file, and a timeout kill would leave the plant behind.
        live_before = GUARD_PATH.stat().st_mtime_ns
        repo7 = Path(tempfile.mkdtemp(prefix="bcg_importfail_"))
        _git(repo7, "init", "-q")
        proc = subprocess.run([sys.executable, "-c", _IMPORT_FAILURE_RUNNER, str(DISPATCH)],
                              input=_payload(str(repo7), "git commit -m x", "Bash"),
                              capture_output=True, text=True, timeout=90, env=env, encoding="utf-8", cwd=REPO)
        check("7: a guard import failure denies -- never allows -- a git commit",
              _classify(proc), "DENY", proc)
        cases.append(("7: the stub names the import error",
                      "planted S4 import failure" in proc.stderr, proc.stderr[:500]))
        cases.append(("7: the live guard file is never written",
                      GUARD_PATH.stat().st_mtime_ns == live_before, "mtime changed"))

    @section
    def _s4():
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

    @section
    def _s5():
        # --- 9: an unparseable baseline.lock.json denies -------------------------------------
        repo9 = _make_consumer_repo()
        _write(repo9 / ".claude" / "baseline.lock.json", "{not json")
        _write(repo9 / "other.txt", "x\n")
        _git(repo9, "add", "-A")
        proc = _run_dispatch(str(repo9), 'git commit -m x', env)
        check("9: an unparseable baseline.lock.json denies the commit",
              _classify(proc), "DENY", proc)

    @section
    def _s6():
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

    @section
    def _s7():
        repo11 = dropped_row_repo()
        proc = _run_dispatch(str(repo11), 'git commit -m m -- .claude/baseline.lock.json', env)
        check("11: pathspec lock commit dropping a HEAD row whose file exists -> deny",
              _classify(proc), "DENY", proc)
        cases.append(("11: the denial names the dropped row",
                      ".claude/commands/peer.md" in proc.stderr, proc.stderr[:500]))

    @section
    def _s8():
        repo12 = dropped_row_repo()
        _git(repo12, "rm", "-q", ".claude/commands/peer.md")
        proc = _run_dispatch(
            str(repo12), 'git commit -m m -- .claude/baseline.lock.json .claude/commands/peer.md', env)
        check("12: the row leaves with its deleted file -> allow", _classify(proc), "ALLOW", proc)

    @section
    def _s9():
        repo13 = dropped_row_repo()
        _git(repo13, "add", ".claude/baseline.lock.json")
        proc = _run_dispatch(str(repo13), "git commit -m m", env)
        check("13: whole-index commit of a staged lock dropping a row -> deny",
              _classify(proc), "DENY", proc)

    @section
    def _s10():
        repo14 = dropped_row_repo()
        _write(repo14 / "other.txt", "x\n")
        _git(repo14, "add", "other.txt")
        proc = _run_dispatch(str(repo14), "git commit -m m", env)
        check("14: an unstaged lock edit is not in the commit -> allow", _classify(proc), "ALLOW", proc)

    @section
    def _s11():
        repo15 = _make_consumer_repo()
        _write(repo15 / ".claude" / "commands" / "fresh.md", "# fresh\n")
        _git(repo15, "add", "-A")
        subprocess.run([sys.executable, str(BASELINE_SYNC), "classify", ".claude/commands/fresh.md",
                        "--status", "local"], cwd=repo15, capture_output=True, text=True, check=True)
        _git(repo15, "add", "-A")
        proc = _run_dispatch(
            str(repo15), 'git commit -m m -- .claude/baseline.lock.json .claude/commands/fresh.md', env)
        check("15: a lock commit that only adds rows -> allow", _classify(proc), "ALLOW", proc)

    @section
    def _s12():
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

    @section
    def _s12b():
        # --- 16b: the same drop in a commit that moves the lock's `synced_commit` is the engine consuming
        # a baseline publication that deleted the declined file, so the row has left upstream.
        declined = ".claude/commands/declined.md"
        repo16b = _make_consumer_repo({declined: {
            "status": "local", "layer": None, "absent": True,
            "judged": {"sha": "0" * 64, "verdict": "keep-local", "at": "2026-01-01T00:00:00Z",
                       "borderline": False, "confirmed_at": None}}})
        lock16b = repo16b / ".claude" / "baseline.lock.json"
        body16b = json.loads(lock16b.read_text(encoding="utf-8"))
        del body16b["files"][declined]
        body16b["synced_commit"] = "1" * 40
        _write_json(lock16b, body16b)
        proc = _run_dispatch(str(repo16b), 'git commit -m m -- .claude/baseline.lock.json', env)
        check("16b: dropping a declined row while the pin moves -> allow", _classify(proc), "ALLOW", proc)

    @section
    def _s13():
        # --- 19: a lock whose text repeats a row key. json.loads keeps the last copy silently, so a
        # merge that re-adds a row a peer already committed elsewhere passes every parsed check.
        dup = ".claude/commands/dup.md"
        repo19 = _make_consumer_repo({dup: {"status": "local", "layer": None, "judged": None}})
        lock19 = repo19 / ".claude" / "baseline.lock.json"
        text19 = lock19.read_text(encoding="utf-8")
        row19 = '"%s": {' % dup
        at = text19.index(row19)
        end = text19.index("}", at) + 1
        lock19.write_text(text19[:end] + ",\n    " + text19[at:end] + text19[end:], encoding="utf-8")
        proc = _run_dispatch(str(repo19), 'git commit -m m -- .claude/baseline.lock.json', env)
        check("19: a lock commit that repeats a row key -> deny", _classify(proc), "DENY", proc)
        cases.append(("19: the denial names the repeated row", dup in proc.stderr, proc.stderr[:500]))

    # --- 20-21: the row check reads the lock the commit publishes, through the index it names ---
    def private_index_repo():
        repo = _make_consumer_repo()
        rel = ".claude/commands/private.md"
        _write(repo / rel, "# private\n")
        lock_path = repo / ".claude" / "baseline.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"][rel] = {"status": "local"}
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=json.dumps(lock, indent=2) + "\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        idx = Path(tempfile.mkdtemp(prefix="bcg_idx_")) / "private.index"
        ienv = dict(os.environ, GIT_INDEX_FILE=str(idx))
        subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=ienv, check=True)
        subprocess.run(["git", "add", "--", rel], cwd=repo, env=ienv, check=True)
        subprocess.run(["git", "update-index", "--cacheinfo", "100644,%s,.claude/baseline.lock.json" % blob],
                       cwd=repo, env=ienv, check=True)
        return repo, idx

    @section
    def _s14():
        # 20-21 call the guard directly: git_guardrails' revert check judges a fixture private index first.
        import importlib.util
        spec20 = importlib.util.spec_from_file_location("bcg_under_test_20", GUARD_PATH)
        guard20 = importlib.util.module_from_spec(spec20)
        spec20.loader.exec_module(guard20)
        repo20, idx20 = private_index_repo()
        verdict20 = guard20._judge_commit(["-m", "m"], str(repo20), {"GIT_INDEX_FILE": str(idx20).replace("\\", "/")})
        cases.append(("20: a private-index commit whose committed lock carries the new row -> allow (the working lock lacks it)",
                      verdict20 is None, repr(verdict20)))

        repo21, idx21 = private_index_repo()
        msys = str(idx21).replace("\\", "/")
        if len(msys) > 1 and msys[1] == ":":
            msys = "/" + msys[0].lower() + msys[2:]
        verdict21 = guard20._judge_commit(["-m", "m"], str(repo21), {"GIT_INDEX_FILE": msys})
        cases.append(("21: the same commit naming its index as an MSYS /c/... path -> allow", verdict21 is None, repr(verdict21)))

    # --- 22-25: a merge commit re-commits its parents, so only content differing from BOTH
    # parents is authored here. The PR-sync job merges the upstream ref in a fresh worktree, where
    # main's own `.claude/` additions arrive through MERGE_HEAD and are not this commit's rows to
    # classify. `.claude/commands/` keeps `git_guardrails.py`'s unrelated stamp check out of the way.
    def merge_repo():
        """`upstream` adds an unclassified `.claude/` file; HEAD adds none. A clean
        `--no-commit` merge stages it with MERGE_HEAD set."""
        repo = _make_consumer_repo()
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                              text=True, check=True).stdout.strip()
        _git(repo, "checkout", "-q", "-b", "upstream")
        _write(repo / ".claude" / "commands" / "from_main.md", "# from the merged ref\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "upstream adds a .claude/ file")
        _git(repo, "checkout", "-q", base)
        _git(repo, "checkout", "-q", "-b", "work")
        _git(repo, "merge", "-q", "--no-commit", "--no-ff", "upstream")
        return repo

    @section
    def _s15():
        repo22 = merge_repo()
        proc = _run_dispatch(str(repo22), "git commit -m merge", env)
        check("22: a merge bringing in an unclassified .claude/ file from MERGE_HEAD -> allow",
              _classify(proc), "ALLOW", proc)

    @section
    def _s16():
        repo23 = merge_repo()
        _write(repo23 / ".claude" / "commands" / "mine.md", "# authored during the merge\n")
        _git(repo23, "add", "-A")
        proc = _run_dispatch(str(repo23), "git commit -m merge", env)
        check("23: a merge whose resolution authors a new .claude/ file -> rewrite",
              _classify(proc), "ALLOW", proc)
        context = _hso(proc).get('additionalContext', '')
        cases.append(("23: the context names the authored file, never the one from MERGE_HEAD",
                      "mine.md" in context and "from_main.md" not in context,
                      context[:500]))

    @section
    def _s17():
        repo24 = merge_repo()
        _write(repo24 / ".claude" / "commands" / "mine.md", "# authored during the merge\n")
        _git(repo24, "add", "-A")
        proc = _run_dispatch(str(repo24), "git merge --continue", env)
        check("24: `git merge --continue` is judged like the commit closing the merge -> deny",
              _classify(proc), "DENY", proc)

    @section
    def _s18():
        repo25 = merge_repo()
        proc = _run_dispatch(str(repo25), "git merge --continue", env)
        check("25: `git merge --continue` with nothing authored -> allow",
              _classify(proc), "ALLOW", proc)

    @section
    def _s19():
        _must_allow_section(None, env, check)

    @section
    def _s20():
        _must_allow_section(CONSUMER_PLANTED, env, check)

    @section
    def _s21():
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

    @section
    def _s25():
        # --- 27: a pathspec wrapped by a backslash-newline reads its path, not a lone `\` -------
        repo27 = _make_consumer_repo()
        rel27 = ".claude/commands/wrapped_thing.md"
        _write(repo27 / rel27, "# wrapped command\n")
        _git(repo27, "add", "-A")
        classified27 = subprocess.run(
            [sys.executable, str(BASELINE_SYNC), "classify", rel27, "--status", "local"],
            cwd=repo27, capture_output=True, text=True,
        )
        cases.append(("27 setup: classify succeeds", classified27.returncode == 0,
                      classified27.stdout + classified27.stderr))
        proc = _run_dispatch(str(repo27), 'git commit -m "add thing" -- \\\n  ' + rel27, env)
        check("27: a backslash-newline wrapped pathspec commit reads the path -> allow",
              _classify(proc), "ALLOW", proc)

    @section
    def _s22():
        _shell_words_case(SHELL_WORD_CASES[0], env, cases)

    @section
    def _s23():
        _shell_words_case(SHELL_WORD_CASES[1], env, cases)

    @section
    def _s24():
        _shell_words_case(SHELL_WORD_CASES[2], env, cases)

    if only is not None:
        sections[only]()
        emit_section(cases)
        return 0
    for _, found in run_sections(os.path.abspath(__file__), "--section", len(sections),
                                 names=["section %d" % k for k in range(len(sections))]):
        cases.extend(found)

    failures = [(label, detail) for label, ok, detail in cases if not ok]
    for label, ok, detail in cases:
        print("%-4s %s%s" % ("ok" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[2])) if sys.argv[1:2] == ["--section"] else main())
