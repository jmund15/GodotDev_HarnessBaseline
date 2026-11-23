"""
Library (not a hook): the one git subprocess. Stdlib only, below shell parsing and policy, so every hook and
tool spawns git here and nothing here decides anything.

    run(args, *, cwd=None, env=None, input=None, timeout=30, prefix=(), optional_locks=True, creationflags=0) -> GitResult
    soft_text(call, **run_kwargs) -> str | None    stdout when git exits 0, else None
    deadline(seconds) -> Clock                     context: every `run` inside ends by then; `.spent` says one was cut
    remaining(default) -> float                    seconds left on the active deadline, else `default`
    git_environ(overlay) -> dict | None            os.environ with the overlay applied
    GitResult.text / .err_text / .check(args)      decoded streams; raise-on-nonzero

`GitResult` holds bytes, so a blob is never altered by a text pipe; `.text` and `.err_text` decode them as
UTF-8 with `"replace"` and strip nothing, and `.check(args)` returns the result or raises the `CalledProcessError`
a must-succeed call raises. A launch failure raises the native
`OSError` and a timeout the native `subprocess.TimeoutExpired`; a nonzero exit is a result, not an exception.
`env` is an overlay: a `None` value unsets that variable, and an empty overlay inherits the caller's
environment unchanged. `prefix` sits between `git` and `args` in the order given; `optional_locks=False`
puts `--no-optional-locks` first, so a read-only `status` never leaves an index lock behind a killed git.
`creationflags` is passed through to `subprocess.run` for a console-less launcher that must not flash a
console window; it is 0 elsewhere.
"""
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self):
        return self.stdout.decode("utf-8", "replace")

    @property
    def err_text(self):
        return self.stderr.decode("utf-8", "replace")

    def check(self, args):
        """`self` when git exited 0; otherwise the CalledProcessError a must-succeed call raises."""
        if self.returncode:
            raise subprocess.CalledProcessError(self.returncode, ["git", *args], self.stdout, self.stderr)
        return self


def git_environ(overlay):
    """os.environ with `overlay` applied (a None value unsets), or None when `overlay` is empty."""
    if not overlay:
        return None
    env = dict(os.environ)
    for key, value in overlay.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


class Clock:
    """One decision's time budget. `spent` turns True when a git call ran out of it, so a caller that treats a
    failed read as an answer can still tell that its answer was cut short."""

    def __init__(self, seconds):
        self.ends = time.monotonic() + seconds
        self.spent = False

    def left(self):
        return self.ends - time.monotonic()


_clock = None


@contextmanager
def deadline(seconds):
    """Inside the block, every `run` ends by `seconds` from entry: its timeout shrinks to the time left, and a call
    started with none left times out at once. Nested blocks keep the outer, earlier deadline."""
    global _clock
    outer = _clock
    clock = Clock(seconds)
    if outer is not None and outer.ends < clock.ends:
        clock = outer
    _clock = clock
    try:
        yield clock
    finally:
        _clock = outer


def remaining(default):
    """Seconds left on the active deadline, capped at `default`; `default` when no deadline is active."""
    return default if _clock is None else max(0.0, min(default, _clock.left()))


def run(args, *, cwd=None, env=None, input=None, timeout=30, prefix=(), optional_locks=True, creationflags=0):
    argv = ["git"] + ([] if optional_locks else ["--no-optional-locks"]) + list(prefix) + list(args)
    clock, clamped = _clock, False
    if clock is not None and clock.left() < (timeout or float("inf")):
        timeout, clamped = clock.left(), True
        if timeout <= 0:
            clock.spent = True
            raise subprocess.TimeoutExpired(argv, 0)
    try:
        done = subprocess.run(argv, cwd=cwd, input=input, capture_output=True, timeout=timeout,
                              env=git_environ(env), creationflags=creationflags)
    except subprocess.TimeoutExpired:
        if clamped:
            clock.spent = True
        raise
    return GitResult(done.returncode, done.stdout or b"", done.stderr or b"")


def soft_text(call, **run_kwargs):
    """Decoded stdout of a `GitResult`, or of `run(call, **run_kwargs)` when `call` is an argv; None for a
    nonzero exit, a missing git or a timeout. An empty success is "", never None."""
    try:
        res = call if isinstance(call, GitResult) else run(call, **run_kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.text if res.returncode == 0 else None
