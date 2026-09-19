#!/usr/bin/env python3
"""Re-runnable proof for the SessionStart stray `.search-index` sweep (Lane E2).

`semantic_search_scope_guard.py` denies new subdirectory-`searchDir` calls, but four indexes
built before the guard shipped (`.claude/.search-index`, `.claude/auto-memory/.search-index`,
`.claude/hooks/.search-index`, `Tests/.search-index`) are residue nothing else removes. Two of
them stay locked while the semantic-search MCP server holds their SQLite files open, so the
sweep must tolerate a busy delete rather than treat it as failure.

Every case runs against a temp `git init` repository, never the real tree. Root/submodule/
worktree indexes must survive; only a stray under a non-git-toplevel directory is removed.

    python3 .claude/tests/test_stray_search_index_sweep.py
"""
import contextlib
import io
import json
import os
import platform
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import session_context_loader as scl  # noqa: E402


def _git(root, *args):
    result = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout


def _init_repo(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text(".search-index/\n", encoding="utf-8")
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def _make_index(dir_path: Path, sentinel_name="search.db"):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / sentinel_name).write_text("data", encoding="utf-8")


class StraySearchIndexSweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        _init_repo(self.root)

    def test_stray_subdirectory_index_is_removed(self):
        stray = self.root / "sub" / ".search-index"
        _make_index(stray)
        scl.sweep_stray_search_indexes(self.root)
        self.assertFalse(stray.exists())

    def test_root_index_is_kept(self):
        root_index = self.root / ".search-index"
        _make_index(root_index)
        scl.sweep_stray_search_indexes(self.root)
        self.assertTrue(root_index.exists())
        self.assertTrue((root_index / "search.db").exists())

    def test_nested_git_toplevel_index_is_kept(self):
        # A submodule/worktree root: its parent carries its own `.git` (file or dir), so its
        # .search-index is NOT a stray even though it sits below the outer repo root.
        nested = self.root / "vendor" / "Nested"
        nested.mkdir(parents=True)
        (nested / ".git").write_text("gitdir: ../.git/modules/Nested\n", encoding="utf-8")
        nested_index = nested / ".search-index"
        _make_index(nested_index)
        scl.sweep_stray_search_indexes(self.root)
        self.assertTrue(nested_index.exists())
        self.assertTrue((nested_index / "search.db").exists())

    def test_symlink_or_junction_named_search_index_is_never_followed(self):
        target = self.root / "real_target"
        _make_index(target, sentinel_name="sentinel.txt")
        link = self.root / "sub2" / ".search-index"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system() == "Windows":
                if hasattr(os, "symlink"):
                    try:
                        os.symlink(str(target), str(link), target_is_directory=True)
                    except OSError:
                        try:
                            subprocess.run(
                                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True, timeout=10, check=True,
                            )
                        except Exception:
                            self.skipTest("platform cannot create a symlink or junction without elevation")
            else:
                os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("platform cannot create a symlink or junction without elevation")

        scl.sweep_stray_search_indexes(self.root)
        self.assertTrue((target / "sentinel.txt").exists(), "link target sentinel must survive")

    def test_held_open_file_inside_a_stray_does_not_raise(self):
        stray = self.root / "sub3" / ".search-index"
        _make_index(stray)
        handle = open(stray / "search.db", "r", encoding="utf-8")
        try:
            try:
                scl.sweep_stray_search_indexes(self.root)  # must not raise
            except Exception as exc:  # pragma: no cover - proof of the failure mode
                self.fail("sweep raised on a locked file: %r" % exc)
        finally:
            handle.close()
        # No assertion on survival: whether the OS lets a rmtree past an open handle is
        # platform-dependent. The proof is that the sweep itself never raises.

    def test_stray_with_a_recent_sqlite_sidecar_file_is_kept(self):
        # A `-wal`/`-shm` file means a connection is open or recently was: on POSIX an rmtree would
        # unlink a database a live semantic-search server is still using.
        for sidecar in ("search.db-wal", "search.db-shm"):
            stray = self.root / ("live_" + sidecar.split("-")[-1]) / ".search-index"
            _make_index(stray)
            (stray / sidecar).write_text("wal", encoding="utf-8")
            scl.sweep_stray_search_indexes(self.root)
            self.assertTrue((stray / "search.db").exists(), sidecar)

    def test_stray_with_an_old_sqlite_sidecar_file_is_removed(self):
        stray = self.root / "abandoned" / ".search-index"
        _make_index(stray)
        shm = stray / "search.db-shm"
        shm.write_text("shm", encoding="utf-8")
        old = time.time() - 3 * 24 * 3600
        os.utime(shm, (old, old))
        scl.sweep_stray_search_indexes(self.root)
        self.assertFalse(stray.exists())

    def test_discovery_asks_git_only_for_search_index_paths(self):
        # The whole-repo ignored listing is 36K lines on this checkout; a pathspec keeps discovery
        # proportional to the strays, not to every ignored build artifact.
        deep = self.root / "a" / "b" / "c" / ".search-index"
        _make_index(deep)
        for i in range(50):
            _make_index(self.root / "noise" / ("n%d" % i) / "ignored_dir", sentinel_name="x.txt")
        (self.root / ".gitignore").write_text(".search-index/\nnoise/\n", encoding="utf-8")
        calls = []
        real_run = subprocess.run

        def spy(args, *a, **k):
            calls.append(list(args))
            return real_run(args, *a, **k)

        with patch.object(scl.subprocess, "run", side_effect=spy):
            found = scl.find_stray_search_indexes(self.root)
        self.assertEqual([p.relative_to(self.root).as_posix() for p in found], ["a/b/c/.search-index"])
        status_calls = [c for c in calls if c[:2] == ["git", "status"]]
        self.assertTrue(status_calls and any(".search-index" in part for part in status_calls[0]), status_calls)

    def test_non_git_directory_is_a_no_op(self):
        plain = Path(self.tmp.name) / "not_a_repo"
        plain.mkdir()
        stray = plain / "sub" / ".search-index"
        _make_index(stray)
        scl.sweep_stray_search_indexes(plain)  # must not raise
        self.assertTrue(stray.exists())

    def test_main_output_is_byte_identical_with_and_without_strays(self):
        def run_main(root):
            with contextlib.ExitStack() as stack:
                values = {
                    "get_project_root": root, "is_worktree": False, "is_cloud": False,
                    "setup_submodule": "OK", "verify_lsp_plugin": "UNAVAILABLE",
                    "sidecar_launchers": [], "get_git_branch": "main", "get_uncommitted_count": 0,
                    "get_recent_commits": [], "get_jmodot_commits": [], "get_godot_bin": "",
                    "godot_docs_cache_issue": None, "roster_health": "UNKNOWN (no logs dir)",
                    "build_status": "SKIPPED (submodule not ready)",
                    "setup_import_cache": "SKIPPED (submodule not ready)",
                }
                for name, value in values.items():
                    stack.enter_context(patch.object(scl, name, return_value=value))
                stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps({"source": "startup"}))))
                out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                with self.assertRaises(SystemExit) as exited:
                    scl.main()
                self.assertEqual(0, exited.exception.code)
                return out.getvalue()

        clean_root = Path(self.tmp.name) / "clean"
        _init_repo(clean_root)
        before = run_main(clean_root)

        stray_root = Path(self.tmp.name) / "strayed"
        _init_repo(stray_root)
        _make_index(stray_root / "sub" / ".search-index")
        after = run_main(stray_root)

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
