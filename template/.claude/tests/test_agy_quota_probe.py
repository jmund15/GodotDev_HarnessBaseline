#!/usr/bin/env python3
"""Proof for `scripts/agy_quota_probe.py`: one Pro-plan bucket from agy's headless `/usage` table.

The table shape is cut from live agy 1.5.14 output (2026-11-21): `<bucket>\t<window>\t<N>%\t<reset ISO>`, with a
1970 reset on a bucket untouched this window.

    python3 .claude/tests/test_agy_quota_probe.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agy_quota_probe", os.path.join(HERE, "..", "scripts", "agy_quota_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

from datetime import datetime, timezone  # noqa: E402
NOW = datetime(2026, 11, 21, 23, 30, tzinfo=timezone.utc).timestamp()  # the capture's time
TABLE = ("Gemini Models\tWeekly Limit Remaining\t98%\t2026-11-28T19:09:16Z\n"
         "Gemini Models\tFive Hour Limit Remaining\t97%\t2026-11-22T00:09:16Z\n"
         "Claude and GPT models\tWeekly Limit Remaining\t100%\t1970-01-01T00:00:00Z\n"
         "Claude and GPT models\tFive Hour Limit Remaining\t100%\t1970-01-01T00:00:00Z\n"
         "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t99%\t2026-11-28T23:20:20Z\n")
fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f"   [{detail}]"))
    fails += not ok


def windows(out):
    return {w["name"]: w for w in (out.get("quota") or {}).get("windows", [])}


g = probe.summarize("antigravity", TABLE, now=NOW)
w = windows(g)
check("the Gemini bucket reads both windows as used percent",
      g["status"] == "available" and w["seven_day"]["usedPercent"] == 2.0 and w["five_hour"]["usedPercent"] == 3.0
      and w["five_hour"]["windowDurationMins"] == 300 and w["seven_day"]["resetsAt"] > NOW, g)
c = probe.summarize("antigravity-claude", TABLE, now=NOW)
check("an untouched bucket's 1970 reset is a dormant window, not malformed",
      c["status"] == "available" and all(x["resetsAt"] is None and x["band"] == "Surplus" for x in windows(c).values()), c)
a = probe.summarize("antigravity-argon", TABLE, now=NOW)
check("the Argon bucket has only its weekly window", list(windows(a)) == ["seven_day"], a)
spent = probe.summarize("antigravity", TABLE.replace("97%", "0%"), now=NOW)
check("a spent window stops the bucket", spent["status"] == "exhausted" and spent["quota"]["dispatchBand"] == "Exhausted"
      and spent["quota"]["bindingWindow"] == "five_hour", spent)
check("a table without the bucket is malformed", probe.summarize("antigravity-argon", TABLE.splitlines()[0], now=NOW)["status"] == "malformed")
used_dormant = probe.summarize("antigravity-claude", TABLE.replace("Claude and GPT models\tWeekly Limit Remaining\t100%", "Claude and GPT models\tWeekly Limit Remaining\t90%"), now=NOW)
check("use with no live reset is malformed, never full headroom", used_dormant["status"] == "malformed", used_dormant)

# Live `agy -p /usage`, 2026-11-24T23:50Z: the Claude bucket was spent and its five-hour window read
# `disabled`, a real allowance state rather than an unknown window. It poisoned a bucket whose weekly
# row named 0% left, which turned a spent plan into an unreadable one for every placement reader.
DISABLED_TABLE = ("Gemini Models\tWeekly Limit Remaining\t27%\t2026-11-30T22:57:28Z\n"
                  "Gemini Models\tFive Hour Limit Remaining\t72%\t2026-11-25T00:58:04Z\n"
                  "Claude and GPT models\tWeekly Limit Remaining\t0%\t2026-11-28T23:20:43Z\n"
                  "Claude and GPT models\tFive Hour Limit Remaining\tdisabled\t\n")
c = probe.summarize("antigravity-claude", DISABLED_TABLE, now=NOW)
check("a disabled window is not in force and leaves the bucket's spent weekly window readable",
      c["status"] == "exhausted" and c["quota"]["dispatchBand"] == "Exhausted"
      and list(windows(c)) == ["seven_day"] and windows(c)["seven_day"]["usedPercent"] == 100.0, c)
check("a bucket whose every window is disabled names no window and is malformed",
      probe.summarize("antigravity-claude",
                      "Claude and GPT models\tWeekly Limit Remaining\tdisabled\t\n", now=NOW)["status"] == "malformed")

daily = probe.summarize("antigravity", TABLE + "Gemini Models\tDaily Limit Remaining\t50%\t2026-11-22T00:00:00Z\n", now=NOW)
check("a window the reader does not know is malformed, never dropped", daily["status"] == "malformed", daily)

print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
