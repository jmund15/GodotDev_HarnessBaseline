#!/usr/bin/env python3
"""The wall-clock cases of the `hooks/_bounded_run.py` proof: a 2-3 s deadline holds within a bound
and nothing outlives it. Each fails when another proof shares the machine, so this file runs alone;
the rest of the proof is `test_bounded_run.py`.

    python3 .claude/tests/test_bounded_run_timing.py
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_bounded_run import HOLDER, _alive, _bounded_run, _reap


class BoundedRunTimingTests(unittest.TestCase):
    def test_deadline_holds_when_a_grandchild_holds_the_output(self):
        started = time.monotonic()
        result = _bounded_run.run_bounded([sys.executable, "-c", HOLDER], timeout=3)
        elapsed = time.monotonic() - started
        self.assertIsNone(result.returncode)
        self.assertIn("started", result.stdout)
        self.assertLess(elapsed, 20, "the run outlived its 3 s deadline by %.1f s" % (elapsed - 3))
        grandchild = int(result.stdout.split()[1])
        try:
            for _ in range(50):
                if not _alive(grandchild):
                    break
                time.sleep(0.1)
            self.assertFalse(_alive(grandchild), "the deadline left the grandchild running")
        finally:
            _reap(grandchild)

    def test_a_git_bash_grandchild_does_not_outlive_the_deadline(self):
        # A check run through Git Bash: its grandchild escapes the Job Object unless the tree is taskkill'd first.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import sidecar_launch
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            marker = Path(tmp) / "grandchild.marker"
            grand = Path(tmp) / "grand.py"
            grand.write_text("import time\ntime.sleep(4)\nopen(%r, 'w').write('x')\n" % marker.as_posix(),
                             encoding="utf-8")
            spawner = Path(tmp) / "spawn.py"
            spawner.write_text("import subprocess, sys, time\n"
                               "subprocess.Popen([sys.executable, %r])\n"
                               "time.sleep(30)\n" % grand.as_posix(), encoding="utf-8")
            result = _bounded_run.run_bounded(
                [sidecar_launch.git_bash(), "-c", 'python3 "%s"' % spawner.as_posix()], timeout=2)
            time.sleep(5)
            self.assertIsNone(result.returncode)
            self.assertFalse(marker.exists(), "a Git Bash grandchild wrote after the deadline")

    def test_the_dispatch_preflight_refuses_at_its_deadline(self):
        # sidecar_dispatch_context runs `<launcher> --check` inside the PreToolUse dispatcher, whose own
        # timeout kills the whole hook: a check that overruns its deadline takes every guard with it.
        import sidecar_dispatch_context as sdc
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            scripts = Path(tmp) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            holder = Path(tmp) / "holder.py"
            holder.write_text("import subprocess, sys, time\n"
                              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                              "print('probing', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
            (scripts / "hung_sidecar.sh").write_text(
                '"%s" "%s"\n' % (Path(sys.executable).as_posix(), holder.as_posix()), encoding="utf-8")
            real = sdc.PREFLIGHT_TIMEOUT
            sdc.PREFLIGHT_TIMEOUT = 3
            started = time.monotonic()
            try:
                state, refusal = sdc.run_preflight("bash .claude/scripts/hung_sidecar.sh -m x", tmp)
            finally:
                sdc.PREFLIGHT_TIMEOUT = real
            elapsed = time.monotonic() - started
        self.assertEqual("refused", state, refusal)
        self.assertIn("timed out", refusal or "")
        self.assertLess(elapsed, 20, "the preflight outlived its 3 s deadline by %.1f s" % (elapsed - 3))

    def test_a_launch_preflight_waits_past_the_hook_deadline(self):
        # A launcher's own launch (a fan-out job, a study ruler) runs its --check outside any PreToolUse budget: a
        # check slower than the hook's deadline under load is a slow answer, never a refusal that fails the job.
        import sidecar_dispatch_context as sdc
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            scripts = Path(tmp) / ".claude" / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "slow_sidecar.sh").write_text('sleep 3\necho OK\n', encoding="utf-8")
            real = sdc.PREFLIGHT_TIMEOUT
            sdc.PREFLIGHT_TIMEOUT = 1
            try:
                hook_state, _ = sdc.run_preflight("bash .claude/scripts/slow_sidecar.sh -m x", tmp)
                launch_state, refusal = sdc.launch_preflight("bash .claude/scripts/slow_sidecar.sh -m x", tmp)
            finally:
                sdc.PREFLIGHT_TIMEOUT = real
        self.assertEqual("refused", hook_state, "the planted check no longer outlasts the hook deadline")
        self.assertEqual("passed", launch_state, refusal)

    @unittest.skipUnless(os.name == "nt", "the taskkill fallback is Windows-only")
    def test_a_stalled_fallback_cleanup_stays_bounded(self):
        import subprocess
        from unittest import mock
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = 4242
        k32.OpenProcess.return_value = 5151
        k32.AssignProcessToJobObject.return_value = 0
        stalled = []

        def stalled_taskkill(*args, timeout=None, **kwargs):
            stalled.append(timeout)
            time.sleep(timeout if timeout is not None else 20)
            raise subprocess.TimeoutExpired(args[0], timeout or 20)

        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with mock.patch("ctypes.WinDLL", return_value=k32):
                tree = _bounded_run.ProcessTree(proc)
            with mock.patch.object(_bounded_run, "CLEANUP_SECONDS", 1), \
                    mock.patch.object(_bounded_run.subprocess, "run", stalled_taskkill):
                started = time.monotonic()
                tree.end()
                elapsed = time.monotonic() - started
        finally:
            proc.kill()
            proc.wait()
        self.assertEqual([1], stalled, "taskkill ran once, under the cleanup bound")
        self.assertLess(elapsed, 10, "the fallback cleanup ran %.1f s past its bound" % elapsed)


if __name__ == "__main__":
    unittest.main()
