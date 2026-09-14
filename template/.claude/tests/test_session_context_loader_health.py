#!/usr/bin/env python3
"""Startup probes preserve unknowns and never overwrite an existing checkout."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import session_context_loader as scl


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_failed_submodule_probe_is_unknown(self):
        for result in (SimpleNamespace(returncode=1, stdout=""), OSError("unavailable")):
            with self.subTest(result=result):
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch.object(scl.subprocess, "run", **kwargs):
                    self.assertIsNone(scl.submodule_status(self.root))

    def test_malformed_submodule_status_is_unknown(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="unexpected output")):
            self.assertIsNone(scl.submodule_status(self.root))

    def test_empty_successful_probe_means_no_submodules(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            self.assertEqual([], scl.submodule_status(self.root))

    def test_unknown_status_never_reports_ok_or_updates(self):
        with patch.object(scl, "submodule_status", return_value=None), patch.object(scl.subprocess, "run") as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("UNKNOWN"))
            run.assert_not_called()

    def test_divergent_checkout_is_preserved_even_when_clean(self):
        with patch.object(scl, "submodule_status", return_value=[("+", "a" * 40, "Jmodot")]), \
                patch.object(scl, "_submodule_is_dirty", return_value=False), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("BROKEN"))
            run.assert_not_called()

    def test_safe_initialization_is_independent_of_preserved_divergence(self):
        before = [("+", "a" * 40, "Jmodot"), ("-", "b" * 40, "Empty")]
        after = [("+", "a" * 40, "Jmodot"), (" ", "b" * 40, "Empty")]
        with patch.object(scl, "submodule_status", side_effect=[before, after]), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            result = scl.setup_submodule(self.root)
        self.assertEqual(["git", "submodule", "update", "--init", "--recursive", "--", "Empty"], run.call_args.args[0])
        self.assertTrue(result.startswith("BROKEN"))
        self.assertIn("initialized Empty", result)
        self.assertIn("Jmodot", result)
        self.assertIn("preserved", result)

    def test_dirty_probe_failure_is_unknown(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
            self.assertIsNone(scl._submodule_is_dirty(self.root, "Jmodot"))

    def test_uninitialized_nonempty_directory_is_preserved(self):
        target = self.root / "Jmodot"
        target.mkdir()
        (target / "unsaved.txt").write_text("work", encoding="utf-8")
        with patch.object(scl, "submodule_status", return_value=[("-", "a" * 40, "Jmodot")]), \
                patch.object(scl.subprocess, "run") as run:
            self.assertTrue(scl.setup_submodule(self.root).startswith("BROKEN"))
            run.assert_not_called()

    def test_successful_init_requires_a_successful_followup_probe(self):
        for after in (None, [("-", "a" * 40, "Jmodot")]):
            with self.subTest(after=after), \
                    patch.object(scl, "submodule_status", side_effect=[[("-", "a" * 40, "Jmodot")], after]), \
                    patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
                self.assertFalse(scl.setup_submodule(self.root).startswith("FIXED"))

    def test_dirty_tree_bypasses_same_head_cache(self):
        with patch.object(scl, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scl.store_build_result(self.root, "OK")
            for dirty in (" M file.cs", "?? untracked.cs"):
                with self.subTest(dirty=dirty), patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=dirty)):
                    self.assertIsNone(scl.cached_build_result(self.root))

    def test_failed_worktree_probe_bypasses_cache(self):
        with patch.object(scl, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scl.store_build_result(self.root, "OK")
            with patch.object(scl.subprocess, "run", side_effect=OSError("unavailable")):
                self.assertIsNone(scl.cached_build_result(self.root))

    def test_stored_success_is_visibly_previous_not_current_health(self):
        with patch.object(scl, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scl.store_build_result(self.root, "OK")
            for source in ("startup", "resume", "compact"):
                with self.subTest(source=source):
                    self.assertTrue(scl.build_status(self.root, source, True).startswith("PREVIOUS: OK"))

    def test_git_probe_failure_does_not_report_clean(self):
        with patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")):
            self.assertIsNone(scl.get_uncommitted_count())
            self.assertEqual("", scl._git_head(self.root))

    def test_empty_import_output_is_not_success(self):
        with patch.object(scl, "get_godot_bin", return_value="fake-godot"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
            self.assertTrue(scl.setup_import_cache(self.root).startswith("FAILED"))

    def test_clean_initialization_is_verified(self):
        entries = [("-", "a" * 40, "Jmodot")]
        after = [(" ", "a" * 40, "Jmodot")]
        with patch.object(scl, "submodule_status", side_effect=[entries, after]), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run:
            self.assertEqual("FIXED (initialized Jmodot)", scl.setup_submodule(self.root))
            self.assertEqual(["git", "submodule", "update", "--init", "--recursive", "--", "Jmodot"], run.call_args.args[0])

    def test_invalid_cache_time_is_not_fresh(self):
        with patch.object(scl, "_git_head", return_value="abc"), \
                patch.object(scl.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
            scl.store_build_result(self.root, "OK")
            cache_path = scl._build_cache_path(self.root)
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            for stamp in (float("nan"), float("inf"), 99999999999):
                with self.subTest(stamp=stamp):
                    cache["ts"] = stamp
                    cache_path.write_text(json.dumps(cache), encoding="utf-8")
                    self.assertIsNone(scl.cached_build_result(self.root))

    def test_main_does_not_build_or_import_when_submodule_is_not_ready(self):
        with contextlib.ExitStack() as stack:
            values = {"get_project_root": self.root, "is_worktree": False, "is_cloud": False,
                      "setup_submodule": "BROKEN (not auto-fixed)", "verify_lsp_plugin": "UNAVAILABLE",
                      "sidecar_launchers": [], "get_git_branch": "main", "get_uncommitted_count": None,
                      "get_recent_commits": [], "get_jmodot_commits": [], "get_godot_bin": "",
                      "godot_docs_cache_issue": None}
            for name, value in values.items():
                stack.enter_context(patch.object(scl, name, return_value=value))
            build = stack.enter_context(patch.object(scl, "verify_build"))
            imports = stack.enter_context(patch.object(scl, "setup_import_cache", return_value="FIXED"))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(json.dumps({"source": "startup"}))))
            out = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as exited:
                scl.main()
            self.assertEqual(0, exited.exception.code)
            build.assert_not_called()
            imports.assert_not_called()
            self.assertIn("working tree UNKNOWN", out.getvalue())
            self.assertNotIn("Ready to develop", out.getvalue())


if __name__ == "__main__":
    unittest.main()
