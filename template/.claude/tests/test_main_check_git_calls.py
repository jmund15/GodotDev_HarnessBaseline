"""A main-check git call whose work scales with the repository carries no fixed 30 s cap.

`main_check.py` fetches, checks out the landed tree and updates submodules; each cost grows with the repository
(measured on a 14.7k-file repo: `git worktree add` 8.7 s, `submodule update --init` 7.8 s on a quiet machine, and
slower under load). `_git_run.run`'s 30 s default bounds a call whose cost is fixed by its answer, so these calls
pass `timeout=None`. The case observes the timeout the pipe really gets, not the call-site keyword.

    python3 .claude/tests/test_main_check_git_calls.py
"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "main_check.py"


class GitCallTimeoutTests(unittest.TestCase):
    def test_git_try_carries_no_fixed_timeout(self):
        spec = importlib.util.spec_from_file_location("main_check_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(SCRIPT.parent))
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        git_module = importlib.import_module(module.git_run.__module__)
        with patch.object(git_module.subprocess, "run", fake_run):
            module.git_try(HERE.parent.parent, "fetch", "origin", "main")
        self.assertTrue(seen, "git_try spawned no git")
        self.assertIsNone(seen[0].get("timeout"))


if __name__ == "__main__":
    unittest.main()
