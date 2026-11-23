"""Shared pytest setup for the harness tests: a child process never inherits the process's stale standard handles.

Under pytest's capture (and any runner with no console) Windows can hand a child an invalid standard handle: an
unnamed stdin fails with `[WinError 6]`, and an unnamed stdout or stderr intermittently fails with `[WinError 50]`
when `subprocess` duplicates the Win32 standard handle. Here, in one place, an unnamed stdin becomes DEVNULL (a test
that needs a payload passes `input=`), and an unnamed stdout or stderr becomes fd 1 or 2, the descriptors pytest's
capture already points at its own files, so the child's output still lands in the captured output.
"""
import os
import subprocess

if os.name == "nt":
    _popen_init = subprocess.Popen.__init__

    def _popen_with_handles(self, *args, **kwargs):
        if len(args) < 4 and kwargs.get("stdin") is None and kwargs.get("input") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        if len(args) < 5 and kwargs.get("stdout") is None:
            kwargs["stdout"] = 1
        if len(args) < 6 and kwargs.get("stderr") is None:
            kwargs["stderr"] = 2
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_with_handles
