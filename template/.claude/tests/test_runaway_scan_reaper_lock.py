#!/usr/bin/env python3
"""Proof: runaway_scan_reaper's throttle lock is a `_file_lock` lock with no age-based reclaim.

A claimant that stalls inside its window (simulated by a `throttled` stub) keeps its lock while a
contender arrives with an injected clock 60 s later: the contender skips silently and the lock file
survives. The stalled claimant then finishes and wins; a later claim inside the throttle window is
refused. The `_file_lock` handle is the liveness evidence, as in `test_file_lock.py`.

    python3 .claude/tests/test_runaway_scan_reaper_lock.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "hooks")))
import runaway_scan_reaper as rsr  # noqa: E402


def main():
    failures = []

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + str(detail)[:300] if detail else ""))

    directory = tempfile.mkdtemp(prefix="rsr_lock_")
    lock = os.path.join(directory, rsr.LOCK_NAME)
    now = time.time()
    seen = {}
    real_throttled = rsr.throttled

    def stalled_throttled(d, window, t):
        # Runs while the first claimant holds the lock: a contender arrives 60 s later.
        if "contender" not in seen:
            os.utime(lock, (now - 60, now - 60)) if os.path.exists(lock) else None
            seen["contender"] = rsr.claim(d, window, now + 60)
            seen["lock_survived"] = os.path.exists(lock)
        return real_throttled(d, window, t)

    rsr.throttled = stalled_throttled
    try:
        won = rsr.claim(directory, 120, now)
    finally:
        rsr.throttled = real_throttled

    check("the contender arriving 60 s later skips silently", seen.get("contender") is False, seen)
    check("the live claimant's lock is not reclaimed by age", seen.get("lock_survived") is True, seen)
    check("the stalled claimant still wins its window", won is True, won)
    check("the lock file is released after the claim", not os.path.exists(lock))
    check("a second claim inside the throttle window is refused", rsr.claim(directory, 120, now + 1) is False)

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
