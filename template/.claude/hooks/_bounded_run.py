"""
Library (not a hook): run a command under a deadline that actually holds.

`subprocess.run(..., capture_output=True, timeout=N)` does not bound a command whose children outlive
it: on timeout it kills the direct child, then waits for end-of-file on pipes a grandchild still holds,
so a launcher script whose probes start other programs overruns its timeout by as long as they run.
Here output goes to files, never pipes, and when the call returns (in time or not) every process
the command started ends with it (a Windows Job Object with kill-on-close; a POSIX process group).

  run_bounded(cmd, timeout, cwd=None, env=None)
      -> SimpleNamespace(returncode, stdout, stderr); returncode None when the deadline passed.
  ProcessTree(proc).end()   ends a Popen and every process it started.
  detach_options(env=None)  Popen keywords for a worker that outlives the hook that starts it: its console shape
                            and its environment (`job_env(env)`, carrying the REAP_JOB_ID reap.py's `queued` reads).
"""

import os
import subprocess
import tempfile
from types import SimpleNamespace

__all__ = ["run_bounded", "ProcessTree", "detach_options"]

CLEANUP_SECONDS = 10  # the most `end()` spends on each of its taskkill and final wait


def detach_options(env=None):
    """Windows: a hidden console the worker's children share; DETACHED_PROCESS (console-less) makes each
    child allocate its own through the default terminal (test_hook_detach_console). POSIX: its own session.
    `env` is the worker's base environment (default: this process's); the result carries it with a job id."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP, "env": job_env(env)}
    return {"start_new_session": True, "env": job_env(env)}


def job_env(env=None):
    """A detached worker's environment carrying REAP_JOB_ID: every process it starts, detached ones included,
    inherits it, so reap.py's `queued` sees the job's work outside its process tree. A worker started inside a job
    keeps that job's id, so nested detached work counts toward its root."""
    import uuid
    env = dict(os.environ if env is None else env)
    env.setdefault("REAP_JOB_ID", "job-" + uuid.uuid4().hex[:12])
    return env


class ProcessTree:
    """Every process a command starts, ended together. Windows: a Job Object with kill-on-close;
    POSIX: the command's own session. On Windows the process joins the job only after Popen returns,
    so a grandchild it starts before that assignment stays outside the job and survives `end()`."""

    def __init__(self, proc):
        self.proc, self.job = proc, None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)

            class _Limits(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64 * 2), ("LimitFlags", wintypes.DWORD),
                            ("b", ctypes.c_size_t * 2), ("c", wintypes.DWORD), ("d", ctypes.c_size_t),
                            ("e", wintypes.DWORD), ("f", wintypes.DWORD), ("io", ctypes.c_uint64 * 6),
                            ("g", ctypes.c_size_t * 4)]
            limits = _Limits()
            limits.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits))
            handle = k32.OpenProcess(0x0001 | 0x0100, False, proc.pid)  # TERMINATE | SET_QUOTA
            if job and handle and k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
                self.job = (k32, wintypes.HANDLE(job))
            if handle:
                k32.CloseHandle(wintypes.HANDLE(handle))
            if job and self.job is None:
                k32.CloseHandle(wintypes.HANDLE(job))

    def end(self):
        if os.name == "nt":
            # Before the job termination: a grandchild started through Git Bash escapes the job object.
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                               timeout=CLEANUP_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        if self.job:
            k32, job = self.job
            k32.TerminateJobObject(job, 1)
            k32.CloseHandle(job)
            self.job = None
        elif os.name != "nt":
            try:
                os.killpg(self.proc.pid, 9)
            except OSError:
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            pass


def _read(fh):
    fh.seek(0)
    return fh.read().decode("utf-8", "replace")


def run_bounded(cmd, timeout, cwd=None, env=None):
    """Run `cmd` for at most `timeout` seconds, plus a cleanup bounded by CLEANUP_SECONDS per step; its whole
    process tree ends by then. Raises what Popen
    raises when the command cannot start (FileNotFoundError, OSError)."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, cwd=cwd, env=env,
                                start_new_session=(os.name != "nt"))
        tree = ProcessTree(proc)
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            returncode = None
        finally:
            tree.end()
        return SimpleNamespace(returncode=returncode, stdout=_read(out), stderr=_read(err))
