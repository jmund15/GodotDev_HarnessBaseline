"""Shared pytest setup for the harness tests: a child process never inherits pytest's invalid stdin handle.

Under pytest's capture (and any runner with no console stdin) Windows hands children an invalid stdin, so every
`subprocess.run` that does not name its stdin fails with `[WinError 6]`. Defaulting stdin to DEVNULL here, in one
place, is the whole fix; a test that needs a stdin payload passes `input=` (which sets stdin itself).
"""
import os
import subprocess

if os.name == "nt":
    _popen_init = subprocess.Popen.__init__

    def _popen_with_stdin(self, *args, **kwargs):
        if len(args) < 5 and kwargs.get("stdin") is None and kwargs.get("input") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_with_stdin
