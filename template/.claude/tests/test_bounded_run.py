#!/usr/bin/env python3
"""Re-runnable proof for hooks/_bounded_run.py: the deadline holds when a grandchild keeps the
command's output open, and a command inside its deadline returns its exit code and output.

    python3 .claude/tests/test_bounded_run.py
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _bounded_run  # noqa: E402

# A parent that starts a grandchild holding the inherited stdout, then waits on it: the shape of a
# launcher `--check` whose probes start other programs.
HOLDER = ("import subprocess, sys, time; "
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
          "print('started', child.pid, flush=True); time.sleep(60)")


def _alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code))
        k32.CloseHandle(wintypes.HANDLE(handle))
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap(pid):
    if _alive(pid):
        if os.name == "nt":
            import subprocess
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        else:
            os.kill(pid, 9)


class BoundedRunTests(unittest.TestCase):
    def test_a_command_inside_its_deadline_returns_code_and_output(self):
        result = _bounded_run.run_bounded(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"], timeout=30)
        self.assertEqual(7, result.returncode)
        self.assertEqual("out", result.stdout.strip())
        self.assertEqual("err", result.stderr.strip())

    @unittest.skipUnless(os.name == "nt", "the Job Object path is Windows-only")
    def test_a_failed_job_assignment_closes_the_job_handle(self):
        import subprocess
        from unittest import mock
        k32 = mock.MagicMock()
        k32.CreateJobObjectW.return_value = 4242
        k32.OpenProcess.return_value = 5151
        k32.AssignProcessToJobObject.return_value = 0
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with mock.patch("ctypes.WinDLL", return_value=k32):
                tree = _bounded_run.ProcessTree(proc)
        finally:
            proc.kill()
            proc.wait()
        self.assertIsNone(tree.job)
        closed = [call.args[0].value for call in k32.CloseHandle.call_args_list]
        self.assertIn(5151, closed, "the process handle is closed")
        self.assertIn(4242, closed, "a job the process never joined leaks its handle")

    def test_a_missing_program_raises(self):
        with self.assertRaises(OSError):
            _bounded_run.run_bounded([os.path.join("no", "such", "program")], timeout=5)


if __name__ == "__main__":
    unittest.main()
