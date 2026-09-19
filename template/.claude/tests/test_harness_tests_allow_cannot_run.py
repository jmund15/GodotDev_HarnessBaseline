#!/usr/bin/env python3
"""Re-runnable S6 proof for `harness_tests.py --allow-cannot-run FILE`, its base INCOMPLETE
branch, and its tested-inputs integrity check.

    python3 .claude/tests/test_harness_tests_allow_cannot_run.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "harness_tests.py"

_OK_PROOF = "import sys\nsys.exit(0)\n"
_CANNOT_RUN_PROOF = "import sys\nsys.exit(2)\n"
# Appends to a sibling proof file while it runs, so `tree_entries()` differs before and after
# the run — the shape of a peer editing harness code while this proof executes.
_MUTATOR_PROOF = (
    "import os\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "with open(os.path.join(here, 'test_ok.py'), 'a', encoding='utf-8', newline='\\n') as fh:\n"
    "    fh.write('# mutated\\n')\n"
    "import sys\n"
    "sys.exit(0)\n"
)


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for current, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            target = Path(current) / name
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass


def _remove(path: Path) -> None:
    _make_writable(path)
    shutil.rmtree(path, ignore_errors=True)


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    subprocess.run(
        ["git", *args], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


def _init_repo(root: Path) -> None:
    """A minimal git repo — needed only by the cases that reach `_write_stamp`."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "keep.txt").write_text("keep\n", encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def _fixture(root: Path) -> Path:
    tests_dir = root / ".claude" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_ok.py").write_text(_OK_PROOF, encoding="utf-8", newline="\n")
    (tests_dir / "test_cannotrun.py").write_text(
        _CANNOT_RUN_PROOF, encoding="utf-8", newline="\n"
    )
    return tests_dir


def _mutator_fixture(root: Path) -> Path:
    tests_dir = root / ".claude" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_ok.py").write_text(_OK_PROOF, encoding="utf-8", newline="\n")
    (tests_dir / "test_mutator.py").write_text(_MUTATOR_PROOF, encoding="utf-8", newline="\n")
    return tests_dir


def _run(root: Path, stamp: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HARNESS_TEST_STAMP"] = str(stamp)
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent)
    return subprocess.run(
        [sys.executable, str(RUNNER), "--repo", str(root), *args],
        cwd=root, env=env, capture_output=True, text=True,
    )


def _allow_file(root: Path, entries: list) -> Path:
    path = root / "platform_only.json"
    path.write_text(json.dumps(entries) + "\n", encoding="utf-8", newline="\n")
    return path


def test_without_flag_cannot_run_is_incomplete() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        result = _run(root, stamp)
        out = result.stdout + result.stderr
        assert result.returncode == 2, out
        assert "INCOMPLETE" in out, out
        assert not stamp.exists(), "an unflagged cannot-run must not write a stamp"
    finally:
        _remove(root)


def test_input_changed_during_run_exits_1() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _mutator_fixture(root)
        stamp = root / "stamp.json"
        result = _run(root, stamp)
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "changed during verification" in out, out
        # The changed input stays unstamped; the unchanged inputs keep the entries this run proved.
        files = json.loads(stamp.read_text(encoding="utf-8"))["files"] if stamp.exists() else {}
        assert ".claude/tests/test_ok.py" not in files, files
        assert files, "the unchanged inputs keep their stamp entries"
    finally:
        _remove(root)


def test_listed_cannot_run_does_not_incomplete() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(root, [
            {"proof": "test_cannotrun.py", "platform": "ubuntu-latest", "reason": "no runner"}
        ])
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "1 cannot-run exception(s) allowed" in out, out
        assert stamp.is_file(), "a fully-listed run must still write a stamp"
    finally:
        _remove(root)


def test_unlisted_cannot_run_is_incomplete() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(root, [])
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        out = result.stdout + result.stderr
        assert result.returncode == 2, out
        assert "INCOMPLETE" in out, out
        assert not stamp.exists(), "an unlisted cannot-run must not write a stamp"
    finally:
        _remove(root)


def test_missing_allow_file_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        result = _run(root, stamp, "--allow-cannot-run", str(root / "does_not_exist.json"))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_unparseable_allow_file_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        bad = root / "platform_only.json"
        bad.write_text("not json", encoding="utf-8", newline="\n")
        result = _run(root, stamp, "--allow-cannot-run", str(bad))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_allow_file_not_a_list_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        bad = root / "platform_only.json"
        bad.write_text(json.dumps({"not": "a list"}), encoding="utf-8", newline="\n")
        result = _run(root, stamp, "--allow-cannot-run", str(bad))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_entry_missing_proof_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(root, [{"platform": "ubuntu-latest", "reason": "no runner"}])
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_entry_missing_platform_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(root, [{"proof": "test_cannotrun.py", "reason": "no runner"}])
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_entry_missing_reason_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(
            root, [{"proof": "test_cannotrun.py", "platform": "ubuntu-latest"}]
        )
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def test_listed_proof_not_discovered_exits_2() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness_tests_allow_"))
    try:
        _init_repo(root)
        _fixture(root)
        stamp = root / "stamp.json"
        allow = _allow_file(root, [
            {"proof": "test_never_existed.py", "platform": "ubuntu-latest", "reason": "typo"}
        ])
        result = _run(root, stamp, "--allow-cannot-run", str(allow))
        assert result.returncode == 2, result.stdout + result.stderr
        assert not stamp.exists()
    finally:
        _remove(root)


def main() -> int:
    cases = [
        test_without_flag_cannot_run_is_incomplete,
        test_input_changed_during_run_exits_1,
        test_listed_cannot_run_does_not_incomplete,
        test_unlisted_cannot_run_is_incomplete,
        test_missing_allow_file_exits_2,
        test_unparseable_allow_file_exits_2,
        test_allow_file_not_a_list_exits_2,
        test_entry_missing_proof_exits_2,
        test_entry_missing_platform_exits_2,
        test_entry_missing_reason_exits_2,
        test_listed_proof_not_discovered_exits_2,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
