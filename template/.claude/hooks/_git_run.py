"""
Library (not a hook): the one git subprocess. Stdlib only, below shell parsing and policy, so every hook and
tool spawns git here and nothing here decides anything.

    run(args, *, cwd=None, env=None, input=None, timeout=30, prefix=(), optional_locks=True, creationflags=0) -> GitResult
    soft_text(call, **run_kwargs) -> str | None    stdout when git exits 0, else None
    deadline(seconds) -> Clock                     context: every `run` inside ends by then; `.spent` says one was cut
    remaining(default) -> float                    seconds left on the active deadline, else `default`
    git_environ(overlay) -> dict | None            os.environ with the overlay applied
    GitResult.text / .err_text / .check(args)      decoded streams; raise-on-nonzero

`timeout=30` bounds a call whose work is fixed by the answer it returns (a status, a rev, one file's diff);
a call whose work scales with the repository — `worktree add`/`remove`, `submodule update`, `clone`, `fetch`,
`checkout` of a tree, `gc` — passes `timeout=None`, because no fixed cap fits a cost that grows with the repo
(measured on a 14.7k-file repo: `worktree add` 8.7 s, `submodule update --init` 7.8 s on a quiet machine).
A hook that must answer inside its own budget keeps an explicit cap.

`GitResult` holds bytes, so a blob is never altered by a text pipe; `.text` and `.err_text` decode them as
UTF-8 with `"replace"` and strip nothing, and `.check(args)` returns the result or raises the `CalledProcessError`
a must-succeed call raises. A launch failure raises the native
`OSError` and a timeout the native `subprocess.TimeoutExpired`; a nonzero exit is a result, not an exception.
`env` is an overlay: a `None` value unsets that variable, and an empty overlay inherits the caller's
environment unchanged. `prefix` sits between `git` and `args` in the order given; `optional_locks=False`
puts `--no-optional-locks` first, so a read-only `status` never leaves an index lock behind a killed git.
`creationflags` is passed through to `subprocess.run` for a console-less launcher that must not flash a
console window; it is 0 elsewhere.

On Windows, the `git` PATH finds first may be Git for Windows' launcher (`cmd\\git.exe`, `bin\\git.exe`; a pwsh
runner's PATH holds only `cmd`), which prepends `mingw64\\bin`, `usr\\bin` and `$HOME\\bin` to PATH, sets MSYSTEM
and spawns `mingw64\\bin\\git.exe`: one more process per call, 1.8-2.8x a direct spawn. A read in `_READS`
spawns that binary itself with that environment, so git and every program it starts see what they would through
the launcher (`tests/test_git_run.py` compares both live). Everything else keeps the launcher.
"""
import os
import shutil
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


_WINDOWS = os.name == "nt"
# Builtins that update no ref and take no alias (git ignores an alias named like a builtin), so the launcher adds
# nothing to them but its environment. A value names the only tokens that may follow the builtin ("" for none).
_ANY = None
_READS = {"rev-parse": _ANY, "cat-file": _ANY, "hash-object": _ANY, "ls-files": _ANY, "ls-tree": _ANY,
          "status": _ANY, "diff": _ANY, "for-each-ref": _ANY, "merge-base": _ANY, "rev-list": _ANY, "show": _ANY,
          "log": _ANY, "blame": _ANY, "cherry": _ANY, "patch-id": _ANY, "check-ignore": _ANY,
          "worktree": frozenset({"list"}), "remote": frozenset({"", "-v", "get-url"})}
_GLOBAL_VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree"})
_GLOBAL_FLAGS = frozenset({"--no-optional-locks", "--literal-pathspecs", "--no-pager", "-P", "--no-replace-objects"})
_launchers = {}


def _is_read(argv):
    """Whether `argv` (after `git`) runs a `_READS` row; an unknown global option before the builtin says no."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUED:
            i += 2
        elif tok in _GLOBAL_FLAGS or tok.split("=", 1)[0] in _GLOBAL_VALUED and "=" in tok:
            i += 1
        else:
            if tok not in _READS:
                return False
            follow = _READS[tok]
            return follow is _ANY or (argv[i + 1] if i + 1 < len(argv) else "") in follow
    return False


def _launcher_root(path_value):
    """The Git for Windows install whose launcher is the `git` `path_value` finds first, else None."""
    if path_value not in _launchers:
        found, root = shutil.which("git", path=path_value), None
        if found and os.path.basename(os.path.dirname(found)).lower() in ("cmd", "bin"):
            root = os.path.dirname(os.path.dirname(found))
            if not os.path.isfile(os.path.join(root, "mingw64", "bin", "git.exe")):
                root = None
        _launchers[path_value] = root
    return _launchers[path_value]


def _launcher_env(env, root):
    """`env` as the launcher hands it to `mingw64\\bin\\git.exe`."""
    env = dict(env)
    home = env.get("HOME")
    if not home:
        drive_home = env.get("HOMEDRIVE", "") + env.get("HOMEPATH", "")
        home = drive_home if drive_home and os.path.isdir(drive_home) else env.get("USERPROFILE")
    front = [os.path.join(root, "mingw64", "bin"), os.path.join(root, "usr", "bin")]
    env["PATH"] = os.pathsep.join(front + ([os.path.join(home, "bin")] if home else []) + [env.get("PATH", "")])
    env["MSYSTEM"] = "MINGW64"
    return env


def run(args, *, cwd=None, env=None, input=None, timeout=30, prefix=(), optional_locks=True, creationflags=0):
    argv = ["git"] + ([] if optional_locks else ["--no-optional-locks"]) + list(prefix) + list(args)
    executable, child_env = None, git_environ(env)
    root = _launcher_root(os.environ.get("PATH", "")) if _WINDOWS and _is_read(argv[1:]) else None
    if root:
        executable = os.path.join(root, "mingw64", "bin", "git.exe")
        child_env = _launcher_env(os.environ if child_env is None else child_env, root)
    clock, clamped = _clock, False
    if clock is not None and clock.left() < (timeout or float("inf")):
        timeout, clamped = clock.left(), True
        if timeout <= 0:
            clock.spent = True
            raise subprocess.TimeoutExpired(argv, 0)
    try:
        done = subprocess.run(argv, executable=executable, cwd=cwd, input=input, capture_output=True,
                              timeout=timeout, env=child_env, creationflags=creationflags)
    except subprocess.TimeoutExpired:
        if clamped:
            clock.spent = True
        raise
    return GitResult(done.returncode, done.stdout or b"", done.stderr or b"")


def root_relative(prefix, names):
    """{name: name below `prefix`} for each toplevel-relative `names` entry inside the harness root, which may sit
    below the toplevel; `prefix` is that root's `git rev-parse --show-prefix`, fetched through the caller's own git
    seam. For output `--relative` leaves alone: a merge's combined diff (`--cc`, `--diff-merges=dense-combined`)
    names toplevel paths whatever the flag."""
    return {name: name[len(prefix):] for name in names if name.startswith(prefix)}


def soft_text(call, **run_kwargs):
    """Decoded stdout of a `GitResult`, or of `run(call, **run_kwargs)` when `call` is an argv; None for a
    nonzero exit, a missing git or a timeout. An empty success is "", never None."""
    try:
        res = call if isinstance(call, GitResult) else run(call, **run_kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.text if res.returncode == 0 else None
