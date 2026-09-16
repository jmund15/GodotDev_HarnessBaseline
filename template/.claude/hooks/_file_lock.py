"""
Library (not a hook): a cross-process exclusive lock whose liveness evidence is the holder's
open handle, never the lock's age.

The lock is a file created with O_CREAT | O_EXCL. Its holder keeps the descriptor open for the
whole hold, and the OS closes it when the holder exits, however it exits. A contender reclaims
the lock only when that proof holds:

  Windows — the delete succeeds. A file another process still has open refuses deletion
            (ERROR_SHARING_VIOLATION), so a successful unlink proves no holder remains.
  POSIX   — a non-blocking flock on the named inode succeeds. The holder flocks its own
            descriptor at creation, and unlink alone would succeed under a live holder.

No process ID is probed, so PID reuse and signals cannot mislead it, and a live holder that
stalls keeps its lock for as long as it lives. A directory at the lock path is a legacy
`mkdir` lock from the pre-2026-09-14 `_hook_state`; it is removed with `rmdir` and retried.

Limit (Windows): a third-party process that holds the lock file open without delete sharing
blocks both release and reclaim until it closes the file, because both are unlinks; contenders
time out meanwhile. Mutual exclusion still holds. No harness process opens these files.

  acquire(path, timeout) -> handle | None   total: never raises
  release(handle)                           total: None is a no-op
  locked(path, timeout)                     context manager; TimeoutError on timeout,
                                            OSError when the lock path cannot exist
"""

import os
import time
from contextlib import contextmanager

if os.name != "nt":
    import fcntl

__all__ = ["acquire", "release", "locked"]

POLL_SECONDS = 0.01


class _Held(object):
    __slots__ = ("path", "fd")

    def __init__(self, path, fd):
        self.path = path
        self.fd = fd


def _same_file(fd, path):
    try:
        named = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(fd)
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


def _create(path):
    """Return a descriptor that owns `path`, or None when another owner may hold it."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    except PermissionError:
        # Windows reports a delete-pending lock file this way; it clears on the next poll.
        return None
    try:
        if os.name != "nt":
            fcntl.flock(fd, fcntl.LOCK_EX)
            if not _same_file(fd, path):
                os.close(fd)
                return None
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        _release_fd(path, fd)
        raise
    return fd


def _remove_abandoned(path):
    """Delete `path` only when no live process holds it; return whether to retry now."""
    if os.path.isdir(path):
        try:
            os.rmdir(path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
    if os.name == "nt":
        try:
            os.unlink(path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if _same_file(fd, path):
            os.unlink(path)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _release_fd(path, fd):
    if os.name == "nt":
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    try:
        if _same_file(fd, path):
            os.unlink(path)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _acquire(path, timeout):
    """Return a handle, None on timeout; raise OSError when the lock cannot exist."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        fd = _create(path)
        if fd is not None:
            return _Held(path, fd)
        retry_now = _remove_abandoned(path)
        if time.monotonic() >= deadline:
            return None
        if not retry_now:
            time.sleep(POLL_SECONDS)


def acquire(path, timeout):
    try:
        return _acquire(path, timeout)
    except OSError:
        return None


def release(handle):
    if handle is None:
        return
    _release_fd(handle.path, handle.fd)


@contextmanager
def locked(path, timeout):
    handle = _acquire(path, timeout)
    if handle is None:
        raise TimeoutError("timed out waiting for lock: " + path)
    try:
        yield
    finally:
        release(handle)
