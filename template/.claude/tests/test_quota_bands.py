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

sys.exit(1 if fails else 0)
