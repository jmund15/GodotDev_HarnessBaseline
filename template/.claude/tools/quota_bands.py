#!/usr/bin/env python3
"""SSOT for raw burn-rate pressure and the quota-risk band built from pace and headroom.

One formula, many quota sources. Claude's own Max-plan telemetry (budget_posture.py)
and every plan-quota provider's own usage endpoint (codex_quota_probe.py) describe the
same thing in different field names: how much of a window is spent, and how much of the
window is left. Both reduce here, so a Codex band and a Claude band are the SAME scale
and a routing comparison between them is meaningful rather than two parallel vocabularies
a reader has to hold apart.

    elapsed  = 1 - (resets_at - now) / window_seconds     rejected outside [0, 1]
    pressure = used_percentage / (max(PACE_FLOOR, elapsed) * 100)   >1 ahead of a linear pace
    run-out  = ((100 - used) / used) * max(RUNOUT_RATE_FLOOR, elapsed) / (1 - elapsed)

`pressure` answers how fast the window is burning; the run-out ratio answers whether what is
left survives to the reset, and it RAISES a band the pace alone would read as spendable. The
two are separate numbers on purpose: the five-hour width rule reads pace, and a raw pace
compared against another window's raw pace means nothing when the windows differ in length.

The `/100.0` is load-bearing: the input is a PERCENTAGE, so without it `pressure > 1.0`
would not mean "ahead of pace".

UNCOMPUTABLE is a third value, not a band. A provider may report `usedPercent` while
leaving the window duration or reset time null (OpenAI's `RateLimitWindow` declares both
nullable), and elapsed needs both. `compute_band` returns `(None, None)` there and the
caller renders it as unknown -- collapsing it onto a band would invent a pressure reading
from a payload that never carried one, in the one direction that matters: a null window
would silently read as Surplus and open a gate nothing measured.

CLI:
    quota_bands.py bands                              print the band table
    quota_bands.py band <used_pct> <resets_at> <window_seconds>    pressure + band
"""

import math
import sys
import time

# A burn RATE needs a run-up: with less than PACE_FLOOR of the window elapsed, the denominator is
# PACE_FLOOR. Measured 2026-09-08: 3% of a weekly window used, 2% elapsed, read as pressure 1.5
# (Hot) and refused a dispatch; the same 3% at 10% elapsed is 0.3 (Surplus), which is what a
# fresh window is. A real burst still shows: 30% used at 2% elapsed is 3.0 either way.
PACE_FLOOR = 0.10

# An early-week projection needs more history than pace: 15% used at 14% elapsed projects
# 1.32x the time to reset with this floor, but 0.92x without it. Only the rate term is
# clamped; the time left to reset is not.
RUNOUT_RATE_FLOOR = 0.20
# Projected run-out at or under this fraction of the time left means the allowance does not last
# even half way to its own reset, which outranks what the average burn rate alone can say.
HOT_RUNOUT_RATIO = 0.5

# (upper_bound_exclusive, name, what the band says about the window's pace)
#
# A band is the pacing signal: how fast a window spends against its own reset. It never says who
# pays for a job -- the session weighs each plan's quota left against its window left for that -- nor which model
# does the work, which is a roster property (`roles` and `effort` in external_models.json).
BANDS = [
    (0.85, "Surplus", "spending below even pace; unused allowance expires at the reset"),
    (1.15, "On pace", "spending near even pace"),
    (1.5, "Ahead", "spending faster than the window lasts at this rate"),
    (float("inf"), "Hot", "spending far ahead of pace, or projected to run out before the reset"),
]

BAND_NAMES = [name for _bound, name, _desc in BANDS]

# The five-hour window's length, the one value `budget_posture` and `workflow_provider_guard` both grade it by.
FIVE_HOUR_SECONDS = 5 * 3600

# The five-hour window governs fan-out WIDTH, never provider choice. Above this pressure a native
# dispatch may not add concurrency: budget_posture prints the threshold and workflow_provider_guard
# denies a dispatch that would leave more than one native job live. The threshold IS the top of the
# `On pace` band, read from BANDS: past it the window reads Ahead or Hot, and the owner's rule
# there is to slow parallelization until the reset or move the work to another model.
FIVE_HOUR_NARROW_PRESSURE = next(bound for bound, name, _desc in BANDS if name == "On pace")

# The SPENT state, which no burn rate can express: pace measures how fast you are spending, never
# whether anything is left, so a spent window grades mid-band and a rate-only gate permits it.
# Deliberately NOT a rung of BANDS: a rank would satisfy the floor shape and violate the ceiling
# shape, or the reverse, so whichever comparison a gate uses, one direction would silently invert.
# Both comparators reject it by name.
EXHAUSTED_BAND = "Exhausted"


def is_exhausted(used_percentage, credits=None):
    """True when the allowance is spent, so no dispatch can proceed on plan quota.

    `credits` is the modifier the Codex probe already documents: purchased credits keep work
    running after the plan allowance is spent, so exhaustion blocks only when they are absent,
    empty, or a zero balance. A plan-quota account with no credit balance is the NORMAL case, so
    `used_percentage` is the trigger and credits only ever excuse it -- never the reverse.
    """
    if not isinstance(used_percentage, (int, float)):
        return False
    if used_percentage < 100:
        return False
    credits = credits or {}
    if credits.get("unlimited"):
        return False
    if credits.get("hasCredits"):
        try:
            return float(credits.get("balance")) <= 0
        except (TypeError, ValueError):
            return False
    return True


def band_for(pressure, runout_ratio=None):
    """(name, description) for a pressure, raised by a projected run-out.

    Ascending quota risk: Surplus is the LOW band. `runout_ratio` is projected time to run out
    over time left to reset, and it can only RAISE the band: at or above 1 the allowance lasts
    to its own reset, which is nothing the pace reading did not already say, so a ratio at or
    above 1 contributes no band and Surplus pace stays Surplus. This is a risk band and never a
    refusal -- a spent window is `EXHAUSTED_BAND`, decided separately from the use percentage.

    `pressure` must be a finite number; anything else raises ValueError. An uncomputable window
    is the caller's unknown, never a band.
    """
    if (isinstance(pressure, bool) or not isinstance(pressure, (int, float))
            or not math.isfinite(pressure)):
        raise ValueError("band_for needs a finite pressure, got %r" % (pressure,))
    name, desc = BANDS[-1][1], BANDS[-1][2]
    for bound, band_name, band_desc in BANDS:
        if pressure < bound:
            name, desc = band_name, band_desc
            break
    if (runout_ratio is None or isinstance(runout_ratio, bool)
            or not isinstance(runout_ratio, (int, float)) or not math.isfinite(runout_ratio)):
        return name, desc
    if runout_ratio >= 1.0 or runout_ratio < 0:
        return name, desc
    headroom = "Hot" if runout_ratio <= HOT_RUNOUT_RATIO else "Ahead"
    if band_rank(headroom) > band_rank(name):
        index = BAND_NAMES.index(headroom)
        return BANDS[index][1], BANDS[index][2]
    return name, desc


def band_rank(name):
    """Index into the ascending-risk band order. Raises on an unknown name."""
    if name not in BAND_NAMES:
        raise ValueError(f"unknown band {name!r}; legal bands: {', '.join(BAND_NAMES)}")
    return BAND_NAMES.index(name)


def band_satisfies(current, required):
    """FLOOR: is `current` at or above `required` quota risk?

    This is the shape a paid-transport gate wants. deepseek's `On pace` floor refuses
    dispatch at Surplus -- spending dollars while prepaid quota expires unused is waste --
    and permits it at Hot, where dollars are the cheaper currency.
    """
    if current == EXHAUSTED_BAND:
        return False
    return band_rank(current) >= band_rank(required)


def band_within_ceiling(current, ceiling):
    """CEILING: is `current` at or below `ceiling` quota risk?

    The shape a PROVIDER'S OWN quota gate wants, and the exact inverse of the floor above.
    A plan-quota provider running Hot is the case to refuse; a floor would permit dispatch
    precisely then. Two names because one comparison cannot serve both directions and a
    single misread `min`/`max` prefix silently inverts a gate.
    """
    if current == EXHAUSTED_BAND:
        return False
    return band_rank(current) <= band_rank(ceiling)


# A provider reports a fresh window's reset as its own now + the window; our clock may trail it. The one
# observed skew is a few seconds (live codex, 2026-10-02); 300 s is a ceiling chosen to cover it with room,
# not a measured skew. A reset further past now + the window stays uncomputable.
CLOCK_SKEW_SECONDS = 300


def pressure_for(used_percentage, resets_at, window_seconds, now=None):
    """Burn rate, or None when the inputs cannot produce a trustworthy one.

    None on: a non-numeric or absent field (a nullable provider window), a non-positive
    window, a reset at or already in the past -- a capture older than its own window says
    nothing about the window now current -- or an elapsed fraction outside [0, 1], which is a
    reset further away than the window it claims to belong to.

    `bool` remains accepted for compatibility with the original pressure adapter; provider probes
    validate their own reported percentages before using this shared arithmetic.
    """
    if (not isinstance(used_percentage, (int, float)) or not 0 <= used_percentage <= 100
            or not math.isfinite(used_percentage)):
        return None
    if not isinstance(resets_at, (int, float)) or not math.isfinite(resets_at):
        return None
    if (not isinstance(window_seconds, (int, float)) or not math.isfinite(window_seconds)
            or window_seconds <= 0):
        return None
    now = time.time() if now is None else now
    if not isinstance(now, (int, float)) or not math.isfinite(now) or resets_at <= now:
        return None
    elapsed = 1.0 - (resets_at - now) / window_seconds
    if -CLOCK_SKEW_SECONDS / window_seconds <= elapsed < 0.0:
        elapsed = 0.0   # a window that just reset, read on a clock slightly behind the provider's
    if not 0.0 <= elapsed <= 1.0:
        return None
    return used_percentage / (max(PACE_FLOOR, elapsed) * 100.0)


def runout_ratio_for(used_percentage, resets_at, window_seconds, now=None):
    """Projected time to run out, as a fraction of the time left to reset.

    Above 1 the allowance lasts past its own reset. None on the same numeric guards as
    `pressure_for` -- plus zero use, which has no next unit to project from and is the LEAST
    urgent window rather than a good one.

    The observed rate divides by `max(RUNOUT_RATE_FLOOR, elapsed)` while the reset interval
    below divides by the REAL `1 - elapsed`: clamping the projection's denominator is what
    keeps one day's average from reading a fresh week as doomed, and clamping the reset
    interval instead would quietly shrink the time the allowance actually has.
    """
    if (not isinstance(used_percentage, (int, float)) or not 0 < used_percentage <= 100
            or not math.isfinite(used_percentage)):
        return None
    if not isinstance(resets_at, (int, float)) or not math.isfinite(resets_at):
        return None
    if (not isinstance(window_seconds, (int, float)) or not math.isfinite(window_seconds)
            or window_seconds <= 0):
        return None
    now = time.time() if now is None else now
    if not isinstance(now, (int, float)) or not math.isfinite(now) or resets_at <= now:
        return None
    elapsed = 1.0 - (resets_at - now) / window_seconds
    if not 0.0 <= elapsed <= 1.0:
        return None
    remaining = 1.0 - elapsed
    if remaining <= 0:
        return None
    rate_elapsed = max(RUNOUT_RATE_FLOOR, elapsed)
    return ((100.0 - used_percentage) / used_percentage) * rate_elapsed / remaining


def compute_band(used_percentage, resets_at, window_seconds, now=None):
    """(pressure, band_name), or (None, None) when the window is uncomputable.

    `pressure` stays the RAW pace -- the five-hour width rule reads that number and headroom
    must not relabel it. Only the band takes the projected run-out into account.
    """
    now = time.time() if now is None else now
    pressure = pressure_for(used_percentage, resets_at, window_seconds, now)
    if pressure is None:
        return None, None
    ratio = runout_ratio_for(used_percentage, resets_at, window_seconds, now)
    return pressure, band_for(pressure, ratio)[0]


def dormant_band(used_percentage, resets_at):
    """(0.0, "Surplus") for a dormant window, else None.

    Dormant means 0% used and no reset time: no use has started the window yet, so its whole
    allowance is left. That is known full headroom, not a missing reading. A window with any use
    and no reset stays uncomputable.
    """
    if (resets_at is None and not isinstance(used_percentage, bool)
            and isinstance(used_percentage, (int, float)) and used_percentage == 0):
        return 0.0, BAND_NAMES[0]
    return None


def most_constrained_window(windows):
    """The normalized window row that should bind, or None when there are no rows.

    Effective band first: a headroom-Hot week 22 hours from reset binds ahead of a five-hour
    window with a higher raw pace, because the week is what will actually stop work. Equal
    bands then fall to the shorter projected run-out (a row whose use is zero has no
    projection and is the LEAST urgent, never the tightest), and equal run-outs to the higher
    pace. The WHOLE row comes back, so `bindingWindow` names the cap that supplied the band.
    """
    best = None
    best_key = None
    for row in windows:
        if not isinstance(row, dict):
            continue
        band = row.get("band")
        rank = BAND_NAMES.index(band) if band in BAND_NAMES else -1
        if rank < 0:
            continue
        ratio = row.get("runoutRatio")
        ratio_known = (isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
                       and math.isfinite(ratio) and ratio >= 0)
        pressure = row.get("pressure")
        pressure_known = (isinstance(pressure, (int, float)) and not isinstance(pressure, bool)
                          and math.isfinite(pressure))
        key = (rank,
               1 if ratio_known else 0,
               -ratio if ratio_known else 0.0,
               pressure if pressure_known else float("-inf"))
        if best is None or key > best_key:
            best, best_key = row, key
    return best


def _main(argv):
    if not argv or argv[0] == "bands":
        for bound, name, desc in BANDS:
            print(f"{name:8s} <{bound:<6} {desc}")
        return 0
    if argv[0] == "band":
        if len(argv) != 4:
            print("band needs: <used_pct> <resets_at> <window_seconds>", file=sys.stderr)
            return 2
        p, b = compute_band(float(argv[1]), float(argv[2]), float(argv[3]))
        if p is None:
            print("unknown")
            return 3
        print(f"{b}\t{p:.2f}")
        return 0
    print(f"unknown command {argv[0]!r}; try: bands, band", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
