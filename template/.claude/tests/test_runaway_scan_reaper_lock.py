#!/usr/bin/env python3
"""Proof: runaway_scan_reaper's throttle lock is a `_file_lock` lock with no age-based reclaim.

A claimant that stalls inside its window (simulated by a `throttled` stub) keeps its lock while a
contender arrives with an injected clock 60 s later: the contender skips silently and the lock file
survives. The stalled claimant then finishes and wins; a later claim inside the throttle window is
refused. The `_file_lock` handle is the liveness evidence, as in `test_file_lock.py`.

    python -m pytest .claude/tests/test_runaway_scan_reaper_lock.py -q
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")))
import runaway_scan_reaper as rsr  # noqa: E402


@pytest.fixture(scope="module")
def stalled_claim(tmp_path_factory):
    """One claim whose claimant stalls while a contender arrives 60 s later: the facts every case reads."""
    directory = str(tmp_path_factory.mktemp("rsr_lock"))
    lock = os.path.join(directory, rsr.LOCK_NAME)
    now = time.time()
    seen = {}
    real_throttled = rsr.throttled

    def stalled_throttled(d, window, t):
        # Runs while the first claimant holds the lock: a contender arrives 60 s later.
        if "contender" not in seen:
            if os.path.exists(lock):
                os.utime(lock, (now - 60, now - 60))
            seen["contender"] = rsr.claim(d, window, now + 60)
            seen["lock_survived"] = os.path.exists(lock)
        return real_throttled(d, window, t)

    rsr.throttled = stalled_throttled
    try:
        seen["won"] = rsr.claim(directory, 120, now)
    finally:
        rsr.throttled = real_throttled
    seen.update(lock_exists=os.path.exists(lock), second=rsr.claim(directory, 120, now + 1))
    return seen


def test_the_contender_arriving_60_s_later_skips_silently(stalled_claim):
    assert stalled_claim["contender"] is False


def test_the_live_claimants_lock_is_not_reclaimed_by_age(stalled_claim):
    assert stalled_claim["lock_survived"] is True


def test_the_stalled_claimant_still_wins_its_window(stalled_claim):
    assert stalled_claim["won"] is True


def test_the_lock_file_is_released_after_the_claim(stalled_claim):
    assert not stalled_claim["lock_exists"]


def test_a_second_claim_inside_the_throttle_window_is_refused(stalled_claim):
    assert stalled_claim["second"] is False
