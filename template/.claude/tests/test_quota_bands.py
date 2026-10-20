#!/usr/bin/env python3
"""Cases for tools/quota_bands.py pressure_for: a fresh window is not Hot.

Planted: 3% used with 2% of a weekly window elapsed (the 2026-09-08 refusal) must read below
the On-pace bound; a real burst (30% at 2%) must still read Hot; a mid-window reading is
unchanged by the floor; an uncomputable window still returns None (never a clean band).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import quota_bands as qb  # noqa: E402

WEEK = 7 * 24 * 3600
fails = 0


def case(name, ok, detail=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else " — " + detail))
    fails += 0 if ok else 1


now = 1_000_000.0
# 2% elapsed: resets_at is 98% of the window away
p, band = qb.compute_band(3, now + 0.98 * WEEK, WEEK, now=now)
case("3% used at 2% elapsed is not Hot (planted refusal)", p is not None and p < 1.15 and band in ("Surplus", "On pace"),
     "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(30, now + 0.98 * WEEK, WEEK, now=now)
case("30% used at 2% elapsed is still Hot (a real burst)", p is not None and p >= 1.5 and band == "Hot",
     "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(50, now + 0.5 * WEEK, WEEK, now=now)
case("50% at 50% elapsed is exactly on pace (floor inactive mid-window)", p is not None and abs(p - 1.0) < 1e-9,
     "pressure=%s" % p)

p, band = qb.compute_band(3, now + 0.90 * WEEK, WEEK, now=now)
case("3% at exactly the floor (10% elapsed) reads 0.3", p is not None and abs(p - 0.3) < 1e-9, "pressure=%s" % p)

p, band = qb.compute_band(None, now + 0.5 * WEEK, WEEK, now=now)
case("an absent usage field is None, never a band", p is None and band is None)

p, band = qb.compute_band(3, now - 10, WEEK, now=now)
case("a reset in the past is None, never a band", p is None and band is None)

p, band = qb.compute_band(99, now + 22 * 3600, WEEK, now=now)
case("99% with 22 hours left is Hot before the live dispatch floor",
     p is not None and round(p, 2) == 1.14 and band == "Hot", "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(15, now + 0.86 * WEEK, WEEK, now=now)
case("15% used at 14% elapsed stays On pace after early headroom smoothing",
     band == "On pace", "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(20, now + 0.82 * WEEK, WEEK, now=now)
case("20% used at 18% elapsed is Ahead before the reset",
     band == "Ahead", "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(90, now + 12 * 3600, WEEK, now=now)
case("90% used with 12 hours left keeps spendable plan quota On pace",
     band == "On pace", "pressure=%s band=%s" % (p, band))

p, band = qb.compute_band(5, now + 30 * 86400, WEEK, now=now)
case("reset beyond its declared window is unknown, not Surplus", p is None and band is None,
     "pressure=%s band=%s" % (p, band))

for value in (float("nan"), float("inf"), -1, 101):
    p, band = qb.compute_band(value, now + WEEK / 2, WEEK, now=now)
    case("out-of-range use %s is unknown, never a confident band" % value,
         p is None and band is None, "pressure=%s band=%s" % (p, band))

runout_for = getattr(qb, "runout_ratio_for", None)
ratio = runout_for(99, now + 22 * 3600, WEEK, now=now) if callable(runout_for) else None
case("99% weekly use runs out about 0.067 of the remaining time to reset",
     ratio is not None and 0.065 <= ratio <= 0.070, "runout ratio=%s" % ratio)
ratio = runout_for(15, now + 0.86 * WEEK, WEEK, now=now) if callable(runout_for) else None
case("early-week rate clamp lets 15% at 14% elapsed last through reset",
     ratio is not None and ratio > 1.3, "runout ratio=%s" % ratio)
ratio = runout_for(20, now + 0.82 * WEEK, WEEK, now=now) if callable(runout_for) else None
case("the rate clamp leaves the real reset interval unchanged",
     ratio is not None and 0.97 < ratio < 0.98, "runout ratio=%s" % ratio)
if callable(runout_for):
    case("zero burn has no projected run-out", runout_for(0, now + WEEK / 2, WEEK, now=now) is None)
    case("nullable use cannot become a projected rate", runout_for(None, now + WEEK / 2, WEEK, now=now) is None)
    case("out-of-window reset has no projected rate",
         runout_for(5, now + 30 * 86400, WEEK, now=now) is None)

most_constrained = getattr(qb, "most_constrained_window", None)
windows = [{"name": "weekly", "pressure": 1.14, "band": "Hot", "runoutRatio": 0.067},
           {"name": "five_hour", "pressure": 1.4, "band": "Ahead", "runoutRatio": 0.4}]
chosen = most_constrained(windows) if callable(most_constrained) else None
case("the Hot headroom window binds ahead of a higher-pace Ahead window",
     isinstance(chosen, dict) and chosen.get("name") == "weekly", "chosen=%s" % chosen)
windows = [{"name": "slow", "pressure": 1.1, "band": "Hot", "runoutRatio": 0.3},
           {"name": "fast", "pressure": 1.1, "band": "Hot", "runoutRatio": 0.05}]
chosen = most_constrained(windows) if callable(most_constrained) else None
case("equal-band and equal-pace windows bind by shorter run-out",
     isinstance(chosen, dict) and chosen.get("name") == "fast", "chosen=%s" % chosen)

windows = [{"name": "burning", "pressure": 0.6, "band": "Surplus", "runoutRatio": 2.0},
           {"name": "unused", "pressure": 0.0, "band": "Surplus", "runoutRatio": None}]
chosen = most_constrained(windows) if callable(most_constrained) else None
case("zero-use window cannot bind ahead of a burning window in the same band",
     isinstance(chosen, dict) and chosen.get("name") == "burning", "chosen=%s" % chosen)

for bad in (None, float("nan"), "1.0", True):
    try:
        got = qb.band_for(bad)
        raised = None
    except ValueError as exc:
        got, raised = None, str(exc)
    except Exception as exc:
        got, raised = None, "%s: %s" % (type(exc).__name__, exc)
    case("band_for(%r) raises a clear ValueError, never a band" % (bad,),
         raised is not None and "pressure" in raised and not raised.startswith("TypeError"),
         "got=%s raised=%s" % (got, raised))

dormant = getattr(qb, "dormant_band", None)
got = dormant(0, None) if callable(dormant) else "missing"
case("a window with 0% used and no reset is dormant: pressure 0, Surplus", got == (0.0, "Surplus"),
     "got=%s" % (got,))
for used, reset in ((0.5, None), (0, now + WEEK / 2), (None, None), (False, None)):
    got = dormant(used, reset) if callable(dormant) else "missing"
    case("used=%r reset=%r is not dormant" % (used, reset), got is None, "got=%s" % (got,))

# The live rows of 2026-09-25. Anthropic had spent 93% of the week at 74.2% elapsed: its pace
# (1.25) reads Ahead while the 7% left had to cover 25.8% of the window.
def row(used, elapsed):
    return qb.compute_band(used, now + (1.0 - elapsed) * WEEK, WEEK, now=now)


p, band = row(93.0, 0.742)
case("Anthropic 93% used / 74.2% elapsed keeps its raw pace and reads Hot, not Ahead",
     p is not None and abs(p - 1.2534) < 0.005 and band == "Hot", "pressure=%s band=%s" % (p, band))
p, band = row(62.0, 0.411)
case("codex 62% used / 41.1% elapsed is Hot", band == "Hot", "pressure=%s band=%s" % (p, band))
p, band = row(30.0, 0.10)
case("an early-window row whose pace already governs keeps its Hot band",
     p is not None and abs(p - 3.0) < 1e-9 and band == "Hot", "pressure=%s band=%s" % (p, band))
p, band = row(40.0, 0.40)
case("a window exactly on pace reads On pace", band == "On pace", "pressure=%s band=%s" % (p, band))
p, band = row(0, 0.005)
case("a fresh window (0% used) reads Surplus, not On pace", band == "Surplus",
     "pressure=%s band=%s" % (p, band))

# Live codex 2026-10-02: a window that has just reset reports 0% used and a reset a few seconds past
# now + its whole duration (provider clock ahead of ours). That is a fresh window, not a broken reading.
p, band = qb.compute_band(0, now + WEEK + 30, WEEK, now)
case("a fresh window whose reset sits seconds past now + duration reads Surplus", band == "Surplus" and p == 0.0,
     "pressure=%s band=%s" % (p, band))
p, band = qb.compute_band(0, now + WEEK * 1.5, WEEK, now)
case("a reset half a window past its duration stays uncomputable", p is None and band is None,
     "pressure=%s band=%s" % (p, band))

sys.exit(1 if fails else 0)
