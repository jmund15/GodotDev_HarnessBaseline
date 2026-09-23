#!/usr/bin/env python3
"""SSOT for burn-rate pressure and the band vocabulary built on it.

One formula, many quota sources. Claude's own Max-plan telemetry (budget_posture.py)
and every plan-quota provider's own usage endpoint (codex_quota_probe.py) describe the
same thing in different field names: how much of a window is spent, and how much of the
window is left. Both reduce here, so a Codex band and a Claude band are the SAME scale
and a routing comparison between them is meaningful rather than two parallel vocabularies
a reader has to hold apart.

    elapsed  = 1 - (resets_at - now) / window_seconds     clamped to [0.02, 1.0]
    pressure = used_percentage / (max(PACE_FLOOR, elapsed) * 100)   >1 ahead of a linear pace

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

import sys
import time

ELAPSED_FLOOR = 0.02          # first minutes after a reset cannot divide toward infinity
# A burn RATE needs a run-up: with less than PACE_FLOOR of the window elapsed, the denominator is
# PACE_FLOOR. Measured 2026-09-08: 3% of a weekly window used, 2% elapsed, read as pressure 1.5
# (Hot) and refused a dispatch; the same 3% at 10% elapsed is 0.3 (Surplus), which is what a
# fresh window is. A real burst still shows: 30% used at 2% elapsed is 3.0 either way.
PACE_FLOOR = 0.10

# (upper_bound_exclusive, name, what this band AUTHORIZES paid transport to take)
#
# The seam: a band says which CLASS OF WORK becomes buyable as pressure rises. It never says
# which model does it or at what rung -- that is a roster property (`roles` and `effort` in
# external_models.json), and encoding it here froze one roster's strengths into the band table.
# Descriptions therefore name work, never models and never effort rungs.
BANDS = [
    (0.85, "Surplus", "nothing by default - spend the plan; unused weekly capacity expires"),
    (1.15, "On pace", "Explore / read-heavy synthesis / doc write-ups"),
    (1.5, "Ahead", "+ scoped execution under a converged spec; execution on a looser spec; adversarial-review lenses"),
    (float("inf"), "Hot", "+ deeper targeted exploration, planning and architectural review passes"),
]

BAND_NAMES = [name for _bound, name, _desc in BANDS]

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


def band_for(pressure):
    """(name, description) for a pressure. Ascending burn rate: Surplus is the LOW one."""
    for bound, name, desc in BANDS:
        if pressure < bound:
            return name, desc
    return BANDS[-1][1], BANDS[-1][2]


def band_rank(name):
    """Index into the ascending-pressure band order. Raises on an unknown name."""
    if name not in BAND_NAMES:
        raise ValueError(f"unknown band {name!r}; legal bands: {', '.join(BAND_NAMES)}")
    return BAND_NAMES.index(name)


def band_satisfies(current, required):
    """FLOOR: is `current` at or above `required` burn rate?

    This is the shape a paid-transport gate wants. deepseek's `On pace` floor refuses
    dispatch at Surplus -- spending dollars while prepaid quota expires unused is waste --
    and permits it at Hot, where dollars are the cheaper currency.
    """
    if current == EXHAUSTED_BAND:
        return False
    return band_rank(current) >= band_rank(required)


def band_within_ceiling(current, ceiling):
    """CEILING: is `current` at or below `ceiling` burn rate?

    The shape a PROVIDER'S OWN quota gate wants, and the exact inverse of the floor above.
    A plan-quota provider running Hot is the case to refuse; a floor would permit dispatch
    precisely then. Two names because one comparison cannot serve both directions and a
    single misread `min`/`max` prefix silently inverts a gate.
    """
    if current == EXHAUSTED_BAND:
        return False
    return band_rank(current) <= band_rank(ceiling)


def pressure_for(used_percentage, resets_at, window_seconds, now=None):
    """Burn rate, or None when the inputs cannot produce a trustworthy one.

    None on: a non-numeric or absent field (a nullable provider window), a non-positive
    window, or a reset already in the past -- a capture older than its own window says
    nothing about the window now current.

    `bool` is deliberately NOT excluded even though it satisfies `isinstance(x, int)`: this
    function is a byte-for-byte extraction of budget_posture's own guard, and tightening it
    during the move would make the extraction a behavior change wearing a refactor's name.
    The window guard below IS additive -- the original divided by a caller-supplied constant
    that could never be zero, while a provider's `windowDurationMins` can be.
    """
    if not isinstance(used_percentage, (int, float)):
        return None
    if not isinstance(resets_at, (int, float)):
        return None
    if not isinstance(window_seconds, (int, float)) or window_seconds <= 0:
        return None
    now = time.time() if now is None else now
    if resets_at < now:
        return None
    elapsed = 1.0 - (resets_at - now) / window_seconds
    # Upper clamp only: the lower bound is PACE_FLOOR below, which subsumes ELAPSED_FLOOR.
    elapsed = min(1.0, elapsed)
    return used_percentage / (max(PACE_FLOOR, elapsed) * 100.0)


def compute_band(used_percentage, resets_at, window_seconds, now=None):
    """(pressure, band_name), or (None, None) when the window is uncomputable."""
    pressure = pressure_for(used_percentage, resets_at, window_seconds, now)
    if pressure is None:
        return None, None
    return pressure, band_for(pressure)[0]


def worst_of(readings):
    """The highest-pressure computable reading from several windows.

    A provider publishes more than one cap (OpenAI: a 5-hour beside a 7-day) and the tightest
    one is what actually blocks work, so the gate reduces over ALL of them rather than reading
    one. Uncomputable windows are skipped, not treated as zero -- but if EVERY window is
    uncomputable the result is (None, None), which reaches the caller as unknown.
    """
    best = None
    for pressure, band in readings:
        if pressure is None:
            continue
        if best is None or pressure > best[0]:
            best = (pressure, band)
    return best if best is not None else (None, None)


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
