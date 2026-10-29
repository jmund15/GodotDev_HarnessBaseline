#!/usr/bin/env python3
"""Proof for `provider_bands.room` and `provider_bands.pick`: the currency pick ranks plan windows by room.

Room is the share of a window's allowance left over the share of its time left. Every case plants
the registry rows (a dict, never the live registry) and the capacity readings (a reader function),
so no case depends on this machine's quota. The CLI case plants a fresh capacity cache for the
live registry's plan-quota transports and runs `provider_bands.py pick executor` as a process.
A Console credit plan (S5) is one more plan-quota window: its `credit_cycle` window takes the room
formula unchanged, and its dollars travel in `quota.credits`.

    python3 .claude/tests/test_provider_bands_pick.py
"""
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time

from _display_words import banned_in

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "provider_bands.py")
spec = importlib.util.spec_from_file_location("pb_pick", TOOL)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

NOW = 1_800_000_000.0
WEEK_MIN = 7 * 24 * 60


def reading(transport, used, left, window="seven_day", minutes=WEEK_MIN, status="available",
            cost="plan-quota"):
    """A normalized reading whose routing window has `used`% spent and `left` of its time to go."""
    win = {"name": window, "usedPercent": used, "resetsAt": NOW + left * minutes * 60,
           "windowDurationMins": minutes, "pressure": 1.0, "band": "Hot"}
    return {"schemaVersion": 1, "transport": transport, "costModel": cost, "sourceKind": "live",
            "observedAt": "2027-01-15T08:00:00Z", "status": status,
            "quota": {"windows": [win], "routingWindow": window, "bindingWindow": window,
                      "routingBand": "Hot", "dispatchBand": "Hot"}}


def registry(plans, pay=("paid",), tier="executor"):
    """Plan-quota transports `plans` and marginal-usd transports `pay`, one `tier` row each."""
    transports = {n: {"costModel": "plan-quota"} for n in plans}
    transports.update({n: {"costModel": "marginal-usd"} for n in pay})
    models = [{"transport": n, "id": n + "-model-1", "alias": n + "-m", "roles": [tier]}
              for n in list(plans) + list(pay)]
    return {"transports": transports, "models": models}


def reader(readings):
    def read(transport, _data=None):
        value = readings.get(transport)
        if isinstance(value, Exception):
            raise value
        return value
    return read


def main():
    cases = []
    reasons = []
    real_pick = pb.pick

    def recorded_pick(*args, **kwargs):
        got = real_pick(*args, **kwargs)
        reasons.append(got["reason"])
        return got

    pb.pick = recorded_pick
    saved_samples = getattr(pb, "_samples", None)
    if saved_samples is not None:
        pb._samples = lambda: []  # planted readings only: the live samples file never decides a case

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    has_pick = hasattr(pb, "pick") and hasattr(pb, "room")
    case("provider_bands exposes room and pick", has_pick)
    if not has_pick:
        return report(cases)

    # D1: today's readings (2026-09-28): Anthropic 22% used 6.1d left, Go 30% 6.3d, Codex 77% 5.1d.
    today = {"anthropic": reading("anthropic", 22, 6.1 / 7),
             "go": reading("go", 30, 6.3 / 7),
             "codex": reading("codex", 77, 5.1 / 7)}
    data = registry(["anthropic", "go", "codex"], pay=("deepseek",))
    got = pb.pick("executor", "anthropic", data=data, read=reader(today), now=NOW)
    rooms = {r["transport"]: r.get("room") for r in got.get("ranked") or []}
    case("today's readings pick anthropic at room 0.90",
         got.get("transport") == "anthropic" and round(got.get("room") or 0, 2) == 0.90, repr(got))
    case("go ranks second at 0.78 and codex last near 0.31",
         [r["transport"] for r in got["ranked"]] == ["anthropic", "go", "codex"]
         and round(rooms["go"], 2) == 0.78 and 0.30 <= rooms["codex"] <= 0.32, repr(rooms))
    case("the reason is one checkable line in rank order",
         got.get("reason", "").startswith("anthropic Hot: 7d 78% left, 87% to go, resets ")
         and " | go Hot: 7d 70% left, 90% to go, resets " in got["reason"]
         and " | codex Hot: 7d 23% left, 73% to go, resets " in got["reason"]
         and not banned_in(got["reason"])
         and "\n" not in got.get("reason", ""), repr(got.get("reason")))
    case("a plan pick leaves no pay options", got.get("payOptions") == [], repr(got.get("payOptions")))

    # A reading that names its plan size shows it in the reason, so two plans of one route differ.
    sized = {"anthropic": dict(reading("anthropic", 22, 6.1 / 7), planType="max_20x"),
             "second": dict(reading("second", 30, 6.1 / 7), planType="max_5x")}
    got = pb.pick("executor", "anthropic", data=registry(["anthropic", "second"]), read=reader(sized), now=NOW)
    case("the reason names each plan's size when its reading carries planType",
         "anthropic (max_20x) Hot: 7d 78% left" in got["reason"] and "second (max_5x) Hot: 7d 70% left, 87% to go" in got["reason"],
         repr(got.get("reason")))

    odd = {"anthropic": reading("anthropic", 22, 6.1 / 7)}
    odd["anthropic"]["quota"]["windows"][0]["band"] = "Mystery"
    try:
        shown = pb.display_windows(odd["anthropic"], NOW)
    except Exception as exc:  # noqa: BLE001
        shown = repr(exc)
    case("a cached band the ladder does not know sorts without raising", isinstance(shown, list) and shown, repr(shown))
    spent_w = {"band": "Hot", "resetsAt": NOW + 3600}
    case("reset_clause names a Hot window's reset and nothing for a cooler one",
         hasattr(pb, "reset_clause") and pb.reset_clause(spent_w, NOW).startswith("resets ")
         and pb.reset_clause(dict(spent_w, band="Surplus"), NOW) is None
         and pb.reset_clause({"band": "Hot"}, NOW) is None, repr(getattr(pb, "reset_clause", None)))

    # D1: an early burst is not punished; room reads what is left against the time left.
    burst = {"burst": reading("burst", 20, 0.95), "ahead": reading("ahead", 80, 0.40)}
    got = pb.pick("executor", None, data=registry(["burst", "ahead"]), read=reader(burst), now=NOW)
    case("an early burst (20% used, 95% left) outranks a plan with 20% left and 40% of its window",
         got.get("transport") == "burst" and round(got.get("room"), 2) == 0.84
         and round(next(r["room"] for r in got["ranked"] if r["transport"] == "ahead"), 2) == 0.5, repr(got))

    # D2.2: exhausted and unreadable plans drop out, named with why; unreadable is never roomy.
    mixed = {"full": reading("full", 100, 0.5), "spent": reading("spent", 60, 0.5, status="exhausted"),
             "blind": None, "broken": RuntimeError("probe failed"), "ok": reading("ok", 50, 0.5)}
    got = pb.pick("executor", None, data=registry(list(mixed)), read=reader(mixed), now=NOW)
    why = {r["transport"]: r.get("why") for r in got["ranked"] if r.get("room") is None}
    case("only the readable plan with allowance left is picked", got.get("transport") == "ok", repr(got))
    case("a 100%-used window and an exhausted status drop as exhausted",
         "exhausted" in str(why.get("full")) and "exhausted" in str(why.get("spent")), repr(why))
    case("a missing reading and a raising reader drop as unreadable",
         "unreadable" in str(why.get("blind")) and "unreadable" in str(why.get("broken")), repr(why))
    case("dropped plans are named in the reason", all(n in got["reason"] for n in ("full", "blind")),
         got["reason"])
    case("a spent window keeps its plain figures in the human reason",
         "full Exhausted: 7d 0% left, 50% to go, resets " in got["reason"], got["reason"])
    case("pick reasons contain no retired display dialect and each plan has one entry",
         not banned_in(got["reason"])
         and got["reason"].count("full Exhausted:") == 1, got["reason"])
    named = [pb.window_figures({"name": "thirty_day", "windowDurationMins": 43200, "left": 35, "timeLeft": 69}),
             pb.window_figures({"name": "five_hour", "left": 18, "timeLeft": 20})]
    case("a window is named by its length, any provider's; a known name without a length still reads short",
         named == ["30d 35% left, 69% to go", "5h 18% left, 20% to go"], repr(named))

    # A reset more than a day out carries its date: "resets 19:26Z" on a 30d window read as today.
    soon, far = NOW + 3 * 3600, NOW + 20 * 86400
    stamps = [pb.reset_text(soon, NOW), pb.reset_text(far, NOW)]
    case("a reset within a day reads HH:MMZ; one further out carries its date",
         stamps == [time.strftime("%H:%MZ", time.gmtime(soon)), time.strftime("%b %d %H:%MZ", time.gmtime(far))],
         repr(stamps))
    hot = pb.window_figures({"name": "thirty_day", "windowDurationMins": 43200, "left": 35, "timeLeft": 67,
                             "band": "Hot", "resetsAt": far}, now=NOW)
    case("a Hot window's far reset shows its date in the figures",
         hot.endswith(", resets " + time.strftime("%b %d %H:%MZ", time.gmtime(far))), hot)

    # D2 / C2: a plan with its own window name ranks with no code change.
    four = dict(today, fortnight=reading("fortnight", 10, 0.9, window="fortnight", minutes=2 * WEEK_MIN))
    got = pb.pick("executor", "anthropic", data=registry(list(four), pay=()), read=reader(four), now=NOW)
    case("a fourth plan whose routing window is named `fortnight` ranks by its own room",
         got.get("transport") == "fortnight" and round(got.get("room"), 2) == 1.0, repr(got.get("reason")))

    # D2.1: the ladder decides fitness; a plan with no row claiming the tier is no candidate.
    fit = registry(["anthropic"], pay=("deepseek",))
    fit["transports"]["idle"] = {"costModel": "plan-quota"}
    fit["models"].append({"transport": "idle", "id": "idle-1", "alias": "idle-m", "roles": ["scout"]})
    got = pb.pick("executor", "anthropic", data=fit,
                  read=reader({"anthropic": reading("anthropic", 90, 0.9), "idle": reading("idle", 0, 0.9)}),
                  now=NOW)
    case("a plan with no row serving the tier is not ranked",
         [r["transport"] for r in got["ranked"]] == ["anthropic"], repr(got["ranked"]))

    # D2.4: no plan left -> pay options, transport null.
    got = pb.pick("executor", "anthropic", data=registry(["anthropic"], pay=("deepseek",)),
                  read=reader({"anthropic": reading("anthropic", 100, 0.5)}), now=NOW)
    case("with no plan left the pick names the fit marginal-usd rows and no transport",
         got.get("transport") is None and [p.get("alias") for p in got.get("payOptions") or []] == ["deepseek-m"],
         repr(got))

    # D2.3: the session's own plan is a candidate like any other, and wins a tie.
    tie = {"a": reading("a", 50, 0.5), "b": reading("b", 50, 0.5)}
    got = pb.pick("executor", "b", data=registry(["a", "b"]), read=reader(tie), now=NOW)
    case("equal rooms rank the session's own plan first", got.get("transport") == "b", repr(got["ranked"]))

    # A list of tiers takes the union of their fit rows (a job whose shape implies no tier).
    multi = registry(["x"], pay=())
    multi["transports"]["y"] = {"costModel": "plan-quota"}
    multi["models"].append({"transport": "y", "id": "y-1", "alias": "y-m", "roles": ["fanout"]})
    got = pb.pick(["executor", "fanout"], None, data=multi,
                  read=reader({"x": reading("x", 50, 0.5), "y": reading("y", 10, 0.5)}), now=NOW)
    case("a tier list ranks every plan serving any of them", got.get("transport") == "y", repr(got))

    # D1: room itself, on the window fields a probe emits.
    win = {"usedPercent": 22, "resetsAt": NOW + 6.1 * 86400, "windowDurationMins": WEEK_MIN}
    case("room reads (100 - used) / (100 x fraction left)", round(pb.room(win, NOW), 2) == 0.90,
         repr(pb.room(win, NOW)))
    case("a window with no length has no room",
         pb.room({"usedPercent": 10, "resetsAt": NOW + 3600}, NOW) is None)
    case("a window whose reset has passed has no room (the reading predates the reset)",
         pb.room({"usedPercent": 10, "resetsAt": NOW - 60, "windowDurationMins": 300}, NOW) is None)

    spent_reset = getattr(pb, "spent_reset", None)
    both = reading("p", 100, 0.5)
    both["quota"]["windows"].append({"name": "five_hour", "usedPercent": 100, "resetsAt": NOW + 7200,
                                     "windowDurationMins": 300})
    live = reading("p", 40, 0.5)
    case("spent_reset is the latest reset among the windows that are 100% used, else None",
         callable(spent_reset) and spent_reset(both) == both["quota"]["windows"][0]["resetsAt"]
         and spent_reset(live) is None and spent_reset(None) is None and spent_reset({}) is None)

    # The projection: the share of the routing window's allowance the plan's recent rate leaves at its
    # reset. An even-pace figure hides a plan that was busy early and idles now, so recent use decides.
    projection = getattr(pb, "unused_at_reset", None)
    case("provider_bands exposes unused_at_reset and retires spare",
         callable(projection) and not hasattr(pb, "spare"))
    if callable(projection):
        week = {"name": "seven_day", "usedPercent": 20, "resetsAt": NOW + 2 * 86400,
                "windowDurationMins": WEEK_MIN}
        avg = projection("anthropic", week, NOW, samples=[])
        case("with no samples or shorter window the projection continues the window's average pace",
             avg and avg["basis"] == "average" and round(avg["pct"], 1) == 72.0
             and round(avg["daysLeft"], 2) == 2.0 and "from" not in avg, repr(avg))
        base = {"plan": "anthropic", "provider": "anthropic", "window": "seven_day",
                "resets_at": week["resetsAt"]}
        # A rate forecasts no further than the span it was measured over: recent samples decide only
        # while their span covers the time left, so the recent cases sit half a day before the reset.
        late = dict(week, resetsAt=NOW + 43200)
        late_base = dict(base, resets_at=late["resetsAt"])
        recent = [dict(late_base, ts=NOW - 86000, used=10.0), dict(late_base, ts=NOW - 600, used=15.0)]
        got_recent = projection("anthropic", late, NOW, samples=recent)
        case("two samples a day apart project the plan's own recent rate when they span the time left",
             got_recent and got_recent["basis"] == "recent" and round(got_recent["pct"], 1) == 77.5,
             repr(got_recent))
        hour = [dict(base, ts=NOW - 7200, used=10.0), dict(base, ts=NOW - 3600, used=10.4)]
        case("samples an hour apart do not forecast two days: the window's average decides",
             projection("anthropic", week, NOW, samples=hour)["basis"] == "average",
             repr(projection("anthropic", week, NOW, samples=hour)))
        # Two writers read one plan (the statusline capture and the capacity probe) and round apart, so
        # used dips a point between them inside one reset period.
        jitter = [dict(late_base, ts=NOW - h * 3600, used=u)
                  for h, u in ((20, 16.0), (10, 17.0), (2, 19.0), (1.9, 18.0), (1, 19.0), (0.9, 18.0))]
        got_jitter = projection("anthropic", late, NOW, samples=jitter)
        case("a one-point dip between two writers of one plan keeps the recent rate",
             got_jitter and got_jitter["basis"] == "recent" and 0 < got_jitter["pct"] < 81,
             repr(got_jitter))
        falling = [dict(late_base, ts=NOW - 86000, used=12.0), dict(late_base, ts=NOW - 600, used=11.0)]
        case("a used share that only falls in one reset period projects no draw",
             round(projection("anthropic", late, NOW, samples=falling)["pct"], 1) == 80.0,
             repr(projection("anthropic", late, NOW, samples=falling)))
        near = [dict(base, ts=NOW - 1800, used=10.0), dict(base, ts=NOW - 1700, used=10.4)]
        case("two samples less than an hour apart cannot state a rate",
             projection("anthropic", week, NOW, samples=near)["basis"] == "average",
             repr(projection("anthropic", week, NOW, samples=near)))
        stale = [dict(base, ts=NOW - 30 * 3600, used=10.0), dict(base, ts=NOW - 29 * 3600, used=60.0)]
        case("samples older than a day are not recent",
             projection("anthropic", week, NOW, samples=stale)["basis"] == "average",
             repr(projection("anthropic", week, NOW, samples=stale)))
        other = [dict(base, ts=NOW - 7200, used=10.0, resets_at=week["resetsAt"] + 86400),
                 dict(base, ts=NOW - 3600, used=10.4, resets_at=week["resetsAt"] + 86400)]
        case("samples from another reset period are not this window's",
             projection("anthropic", week, NOW, samples=other)["basis"] == "average",
             repr(projection("anthropic", week, NOW, samples=other)))
        stranger = [dict(base, plan="anthropic-secondary", ts=NOW - 7200, used=10.0),
                    dict(base, plan="anthropic-secondary", ts=NOW - 3600, used=10.4)]
        case("another plan's own samples never project this plan",
             projection("anthropic", week, NOW, samples=stranger)["basis"] == "average",
             repr(projection("anthropic", week, NOW, samples=stranger)))
        month = {"name": "thirty_day", "usedPercent": 50, "resetsAt": NOW + 12 * 86400,
                 "windowDurationMins": 30 * 1440}
        short = {"name": "seven_day", "usedPercent": 2, "resetsAt": NOW + 2 * 86400,
                 "windowDurationMins": WEEK_MIN}
        five = {"name": "five_hour", "usedPercent": 1, "resetsAt": NOW + 3600,
                "windowDurationMins": 300}
        bound = projection("go", month, NOW, samples=[], windows=[month, short])
        case("a shorter window's used share bounds the longer window's recent rate",
             bound and bound["basis"] == "bound" and round(bound["pct"], 1) == 45.2
             and bound.get("from") == "7d", repr(bound))
        # A shorter window caps the rate from above, so it replaces the window's own average only when
        # lower: a 5h burst stretched over days read a Surplus week as fully spent.
        surplus = {"name": "seven_day", "usedPercent": 31, "resetsAt": NOW + 3.4 * 86400,
                   "windowDurationMins": WEEK_MIN}
        burst = {"name": "five_hour", "usedPercent": 15, "resetsAt": NOW + 1.5 * 3600,
                 "windowDurationMins": 300}
        capped = projection("anthropic-secondary", surplus, NOW, samples=[], windows=[surplus, burst])
        case("a short burst above the window's own average leaves the average in charge",
             capped and capped["basis"] == "average" and round(capped["pct"]) == 40, repr(capped))
        widest = projection("go", month, NOW, samples=[], windows=[month, five, short])
        case("the closest shorter window bounds, not the shortest",
             round(widest["pct"], 1) == 45.2 and widest.get("from") == "7d", repr(widest))
        capacity_go = {"status": "available", "quota": {"routingWindow": "thirty_day",
                                                        "windows": [month, short]}}
        entry = pb.plan_entry("go", capacity_go, NOW)
        case("the plan entry appends the unused share to its routing window's figures",
             entry.startswith("go Surplus: 30d 50% left, 40% to go, ~45% goes unused at reset (this week's use)"),
             entry)
        tight = {"name": "seven_day", "usedPercent": 99, "resetsAt": NOW + 3600,
                 "windowDurationMins": WEEK_MIN}
        capacity_tight = {"status": "available", "quota": {"routingWindow": "seven_day",
                                                           "windows": [tight]}}
        def entry_with(samples):
            saved = pb._samples
            pb._samples = lambda: samples
            try:
                return pb.plan_entry("anthropic", {"status": "available", "quota": {
                    "routingWindow": "seven_day", "windows": [late]}}, NOW)
            finally:
                pb._samples = saved

        case("the entry names the plan's own recent use as the basis",
             "(recent use)" in entry_with(recent) and "~" in entry_with(recent), entry_with(recent))
        none = pb.plan_entry("x", capacity_tight, NOW)
        case("a projection under one percent adds no clause",
             "goes unused" not in none and "unused at reset" not in none, none)
        slow = {"transport": "slow", "room": 3.0,
                "unusedAtReset": {"pct": 40, "basis": "recent", "daysLeft": 5}}
        earlier = {"transport": "earlier", "room": 2.0,
                   "unusedAtReset": {"pct": 20, "basis": "recent", "daysLeft": 2}}
        even = {"transport": "even", "room": 9.0,
                "unusedAtReset": {"pct": 0.4, "basis": "average", "daysLeft": 1}}
        case("a plan whose quota will expire outranks a roomier one, the earliest reset first",
             max([slow, earlier, even], key=pb.pick_key)["transport"] == "earlier",
             repr([pb.pick_key(r) for r in (slow, earlier, even)]))
        plain = [{"transport": "a", "room": 1.2}, {"transport": "b", "room": 1.8}]
        case("with no projection, rank_room decides as before",
             max(plain, key=pb.pick_key)["transport"] == "b", repr(plain))
        case("pick_key ranks fitting plans only: it takes no placed count and has no overdrawn tier",
             "placed" not in inspect.signature(pb.pick_key).parameters
             and pb.pick_key({"transport": "x", "headroom": 1.0, "room": 9.0}) == (1, 0, 9.0),
             repr(inspect.signature(pb.pick_key)))

    # A short window binds the pick: a plan with weekly room but a nearly spent 5-hour window
    # cannot carry a job that starts now (observed: 83% of the 5-hour window used, 46% of its time left).
    five = 5 * 60

    def with_short(row, used, left):
        row["quota"]["windows"].append({"name": "five_hour", "usedPercent": used,
                                        "resetsAt": NOW + left * five * 60, "windowDurationMins": five})
        return row

    tight = {"secondary": with_short(reading("secondary", 32, 0.6), 83, 0.46),
             "codex": reading("codex", 40, 0.59)}   # weekly room 1.02, below the secondary's 1.13
    tight["secondary"]["quota"]["windows"][0]["band"] = "Surplus"
    got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(tight), now=NOW)
    sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
    case("a nearly spent 5-hour window ranks its plan below one with weekly room only",
         got.get("transport") == "codex" and sec.get("binding") == "five_hour"
         and round(sec.get("bindingRoom") or 0, 2) == 0.37, repr(got))
    case("the weekly room stays the plan's room, so the ahead-of-pace share still reads the weekly window",
         round(sec.get("room") or 0, 2) == 1.13, repr(sec))
    case("the pick's reason names the binding window",
         "5h 17% left, 46% to go" in (got.get("reason") or ""), got.get("reason"))
    full = {"secondary": with_short(reading("secondary", 32, 0.6), 100, 0.3),
            "codex": reading("codex", 90, 0.59)}
    got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(full), now=NOW)
    sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
    case("a spent 5-hour window drops its plan as exhausted",
         got.get("transport") == "codex" and sec.get("room") is None and "five_hour" in (sec.get("why") or ""),
         repr(got))

    credited = {'secondary': with_short(reading('secondary', 32, 0.6), 100, 0.3),
                'codex': reading('codex', 90, 0.59)}
    credited['secondary']['quota']['credits'] = {'unlimited': True}
    try:
        got = pb.pick('executor', None, data=registry(list(credited)), read=reader(credited), now=NOW)
        sec = next(r for r in got['ranked'] if r['transport'] == 'secondary')
        ok = got['transport'] == 'codex' and sec.get('bindingRoom') == 0 and sec.get('binding') == 'five_hour'
        detail = repr(got)
    except Exception as exc:
        ok, detail = False, repr(exc)
    case('a zero-room short window with credits gates the plan without raising', ok, detail)

    # A short window at or above even pace constrains nothing (observed 2026-10-05: a dormant
    # five_hour window held a plan with 32% weekly left and 0.39d to reset below codex at 1.29).
    def with_dormant(row):
        row["quota"]["windows"].append({"name": "five_hour", "usedPercent": 0, "windowDurationMins": five})
        return row

    for label, short in (("dormant", with_dormant), ("exactly even", lambda r: with_short(r, 50, 0.5))):
        expiring = {"secondary": short(reading("secondary", 68, 32 / 568)), "codex": reading("codex", 29, 0.55)}
        got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(expiring), now=NOW)
        sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
        case("a plan at room 5.68 with a %s 5-hour window outranks a plan at 1.29" % label,
             got.get("transport") == "secondary" and round(got.get("room") or 0, 2) == 5.68
             and sec.get("binding") is None, repr(got))
    below = {"secondary": with_short(reading("secondary", 68, 32 / 568), 70, 0.5), "codex": reading("codex", 29, 0.55)}
    got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(below), now=NOW)
    sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
    case("a 5-hour window below even pace (0.6) still ranks its plan by 0.6",
         got.get("transport") == "codex" and round(sec.get("bindingRoom") or 0, 2) == 0.6, repr(got))

    # A short window within the reading's whole-percent resolution of even pace gates nothing
    # (observed 2026-10-05: five_hour rooms 0.98 at 58% used and 0.994 bound a plan at 5.87/5.94
    # under codex at 1.30, while its weekly share expired unused within hours).
    for label, used, short_room in (("0.98 at 58% used", 58, 0.98), ("0.994", 50, 0.994)):
        near = {"secondary": with_short(reading("secondary", 68, 32 / 587), used, (100 - used) / (100 * short_room)),
                "codex": reading("codex", 35, 0.5)}
        got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(near), now=NOW)
        sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
        case("a plan at room 5.87 with a 5-hour room of %s outranks a plan at 1.30" % label,
             got.get("transport") == "secondary" and round(got.get("room") or 0, 2) == 5.87
             and sec.get("binding") is None and "bound by" not in (got.get("reason") or ""), repr(got))
    late = {"secondary": with_short(reading("secondary", 68, 32 / 587), 83, 0.2), "codex": reading("codex", 35, 0.5)}
    got = pb.pick("executor", "anthropic", data=registry(["secondary", "codex"]), read=reader(late), now=NOW)
    sec = next((r for r in got.get("ranked") or [] if r["transport"] == "secondary"), {})
    case("a 5-hour window 83% used with an hour to reset gates its plan below a plan at 1.30",
         got.get("transport") == "codex" and sec.get("binding") == "five_hour"
         and round(sec.get("bindingRoom") or 0, 2) == 0.85, repr(got))

    plan_cases(case)
    drain_cases(case)
    credit_cases(case)
    facts_cases(case)
    names = [pb.window_name(w) for w in ({"windowDurationMins": 43200}, {"windowDurationMins": 300},
                                         {"windowDurationMins": 90, "name": "ninety"}, {"name": "seven_day"},
                                         {"name": "five_hour"}, {"name": "odd"}, {})]
    case("window_name reads a whole-day or whole-hour length, else the known short name, else the name",
         names == ["30d", "5h", "ninety", "7d", "5h", "odd", "window"], repr(names))
    try:
        pb._lead({"transport": "codex", "planType": "pro"})
        lead_default = True
    except TypeError:
        lead_default = False
    case("a ranked row's lead line cannot be built without its capacity, so it never claims a cold cache",
         not lead_default)
    case("the window helpers state that left and timeLeft are percents from 0 to 100",
         all("0 to 100" in (fn.__doc__ or "")
             for fn in (pb.plan_entry, pb.window_figures, pb.display_windows)))
    cli_case(case)
    cli_plan_case(case)
    lean_cases(case)
    lean_cli_case(case)
    case("every generated pick reason avoids the retired display dialects",
         all(not banned_in(reason) for reason in reasons), reasons)
    pb.pick = real_pick
    if saved_samples is not None:
        pb._samples = saved_samples
    return report(cases)


def cli_plan_case(case):
    """From a `~/.claude-secondary` session, the CLI's tie goes to the secondary plan: the session
    PLAN, not the route, is the pick's seat. Registry and cache are planted copies."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    folder = tempfile.mkdtemp(prefix="pick_cli_plan_")
    reg = os.path.join(folder, "external_models.json")
    data = model_registry.load()
    data["transports"]["anthropic-secondary"]["status"] = {"state": "available"}
    with open(reg, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    now = time.time()
    blob = {}
    for name, cfg in data["transports"].items():
        if cfg.get("costModel") != "plan-quota":
            continue
        used, left = (22, 6.1 / 7) if name.startswith("anthropic") else (99, 0.5)
        entry = reading(name, used, left)
        entry["quota"]["windows"][0]["resetsAt"] = now + left * WEEK_MIN * 60
        entry["observedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        entry.update(error=None, liveFailure=None, balance=None)
        blob[name] = entry
    cache = os.path.join(folder, "provider_capacity.json")
    with open(cache, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    env = dict(os.environ, PROVIDER_CAPACITY_CACHE=cache, HARNESS_MODEL_REGISTRY=reg,
               CLAUDE_CONFIG_DIR=os.path.join(os.path.expanduser("~"), ".claude-secondary"))
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    r = subprocess.run([sys.executable, TOOL, "pick", "executor", "--json"], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env)
    try:
        got = json.loads(r.stdout)
    except ValueError:
        got = {}
    case("CLI `pick` from a secondary session gives the secondary plan the tie",
         got.get("transport") == "anthropic-secondary", (r.stdout + r.stderr)[-400:])


def plan_registry(secondary_state="available"):
    """anthropic rows paid by either Anthropic plan; `anthropic-secondary` owns no rows (planOf)."""
    data = registry(["anthropic", "go"], pay=())
    data["transports"]["anthropic"]["launcher"] = ".claude/scripts/anthropic_sidecar.sh"
    data["transports"]["anthropic-secondary"] = {
        "costModel": "plan-quota", "planOf": "anthropic",
        "launcher": ".claude/scripts/anthropic_secondary_sidecar.sh",
        "status": {"state": secondary_state, "reason": "planted"}}
    return data


def plan_cases(case):
    """Slice 1 of the second-plan contract: a plan sharing another transport's rows ranks by its own room."""
    roomier = {"anthropic": reading("anthropic", 80, 0.9), "anthropic-secondary": reading("anthropic-secondary", 10, 0.9),
               "go": reading("go", 70, 0.9)}
    got = pb.pick("executor", "anthropic", data=plan_registry(), read=reader(roomier), now=NOW)
    case("a roomier secondary plan wins an executor pick from a primary session",
         got.get("transport") == "anthropic-secondary"
         and [r["transport"] for r in got["ranked"]][:1] == ["anthropic-secondary"], repr(got.get("reason")))

    even = {"anthropic": reading("anthropic", 50, 0.5), "anthropic-secondary": reading("anthropic-secondary", 50, 0.5)}
    got = pb.pick("executor", "anthropic", data=plan_registry(), read=reader(even), now=NOW)
    back = pb.pick("executor", "anthropic-secondary", data=plan_registry(), read=reader(even), now=NOW)
    case("equal rooms go to the session plan, from either Anthropic seat",
         got.get("transport") == "anthropic" and back.get("transport") == "anthropic-secondary",
         repr((got.get("reason"), back.get("reason"))))

    spent = dict(roomier, **{"anthropic-secondary": reading("anthropic-secondary", 60, 0.5, status="exhausted")})
    got = pb.pick("executor", "anthropic", data=plan_registry(), read=reader(spent), now=NOW)
    why = {r["transport"]: r.get("why") for r in got["ranked"] if r.get("room") is None}
    case("an exhausted secondary drops out named with why",
         got.get("transport") != "anthropic-secondary" and why.get("anthropic-secondary") == "exhausted"
         and "anthropic-secondary Exhausted:" in got["reason"], repr(got))

    got = pb.pick("executor", "anthropic", data=plan_registry("unavailable"), read=reader(roomier), now=NOW)
    case("an unavailable secondary is no candidate",
         "anthropic-secondary" not in [r["transport"] for r in got["ranked"]], repr(got["ranked"]))

    case("plan_quota_transports lists a live plan and omits an unavailable one",
         "anthropic-secondary" in pb.plan_quota_transports(plan_registry())
         and "anthropic-secondary" not in pb.plan_quota_transports(plan_registry("unavailable")),
         repr((pb.plan_quota_transports(plan_registry()), pb.plan_quota_transports(plan_registry("unavailable")))))

    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    import shutil
    tmp = os.path.join(tempfile.mkdtemp(prefix="pick_plan_"), "external_models.json")
    shutil.copyfile(str(model_registry.find_registry()), tmp)
    try:
        model_registry.set_transport_state("anthropic-secondary", "available", path=tmp)
        on = pb.pick("executor", "anthropic", data=model_registry.load(tmp), read=reader(roomier), now=NOW)
        model_registry.set_transport_state("anthropic-secondary", "unavailable", path=tmp, reason="planted")
        off = pb.pick("executor", "anthropic", data=model_registry.load(tmp), read=reader(roomier), now=NOW)
        ok = (on.get("transport") == "anthropic-secondary"
              and "anthropic-secondary" not in [r["transport"] for r in off["ranked"]])
        detail = repr((on.get("reason"), off.get("reason")))
    except Exception as exc:
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    case("`set-status anthropic-secondary unavailable` on the shipped registry removes it from the pick", ok, detail)


LEDGER_ROW = {"label": "plan-board-home", "transport": "anthropic-secondary", "costModel": "plan-quota",
              "servedModel": "claude-sonnet-5-5", "requestedModel": "claude-sonnet-5-5", "effort": "high",
              "costUSD": 2.241479, "numTurns": 76, "durationMs": 611549, "stopReason": "provider-usage-limit",
              "timestamp": "2026-10-25T17:34:33Z", "toolProfile": {"shape": "author", "tier": "executor"}}


def history(plan="p", periods=3, per_period=6, usd=(4.0,), rate=2.0, model="m-1", effort="high",
            shape="author", window="five_hour"):
    """(jobs, samples) of `periods` reset periods: each holds `per_period` finished jobs of `usd` costs, wholly inside
    two samples two hours apart that differ by `rate` points per list dollar of those jobs."""
    jobs, samples = [], []
    for k in range(periods):
        t0 = NOW - (periods - k) * 20000.0
        costs = [usd[i % len(usd)] for i in range(per_period)]
        reset = t0 + 18000
        for i, c in enumerate(costs):
            jobs.append({"plan": plan, "start": t0 + 600 + i * 600, "end": t0 + 1000 + i * 600, "usd": c,
                         "model": model, "effort": effort, "shape": shape, "truncated": False})
        for ts, used in ((t0, 0.0), (t0 + 7200, rate * sum(costs))):
            samples.append({"ts": ts, "plan": plan, "provider": plan, "window": window, "used": used,
                            "resets_at": reset})
    return jobs, samples


def drain_cases(case):
    """The expected drain of one job on one plan: the plan's recorded points per list-price dollar times the
    0.75 quantile of what finished jobs of its model, effort and shape cost; `JOB_DRAIN_PCT` where no history says."""
    jobs, samples = history()
    got = pb.job_drain("p", "five_hour", "m-1", "high", "author", jobs=jobs, samples=samples)
    case("a job's drain is the plan's recorded rate times the 0.75 quantile of its group's cost",
         got.get("basis") == "measured" and round(got["pct"], 2) == 8.0 and round(got["usd"], 2) == 4.0
         and round(got["rate"], 2) == 2.0, repr(got))
    none = pb.job_drain("p", "five_hour", "m-1", "high", "author", jobs=[], samples=[])
    case("no history falls back to JOB_DRAIN_PCT and says so", none["pct"] == pb.JOB_DRAIN_PCT
         and none["basis"] == "default" and none.get("why"), repr(none))
    mixed_jobs, mixed_samples = history(per_period=8, usd=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0))
    mixed = pb.job_drain("p", "five_hour", "m-1", "high", "author", jobs=mixed_jobs, samples=mixed_samples)
    case("the cost quoted is the 0.75 quantile of the group, not its mean (4.5) or its maximum (8)",
         mixed["basis"] == "measured" and mixed["usd"] == 7.0 and round(mixed["rate"], 2) == 2.0, repr(mixed))
    short_jobs, short_samples = history(periods=pb.RATE_MIN_PERIODS - 1)
    short = pb.job_drain("p", "five_hour", "m-1", "high", "author", jobs=short_jobs, samples=short_samples)
    case("fewer reset periods than RATE_MIN_PERIODS state no rate", short["basis"] == "default"
         and "period" in short["why"], repr(short))
    other = pb.job_drain("p", "five_hour", "m-1", "high", "survey", jobs=jobs, samples=samples)
    case("a shape with no recorded group takes the model's wider group, never the default",
         other["basis"] == "measured" and round(other["usd"], 2) == 4.0, repr(other))
    thin_jobs = [dict(j, effort="low") for j in jobs[:pb.GROUP_MIN_JOBS - 1]] + jobs
    thin = pb.job_drain("p", "five_hour", "m-1", "low", "author", jobs=thin_jobs, samples=samples)
    case("a group under GROUP_MIN_JOBS widens to the next group instead of quoting an anecdote",
         thin["basis"] == "measured" and round(thin["usd"], 2) == 4.0, repr(thin))
    unknown = pb.job_drain("p", "five_hour", "m-2", "high", "author", jobs=jobs, samples=samples)
    case("a model with no finished jobs falls back to the default", unknown["basis"] == "default", repr(unknown))
    elsewhere = pb.job_drain("p", "seven_day", "m-1", "high", "author", jobs=jobs, samples=samples)
    case("a window with no samples of its own states no rate", elsewhere["basis"] == "default", repr(elsewhere))
    clipped = [dict(j, truncated=True) for j in jobs[:6]] + jobs[6:]
    case("jobs a usage limit cut short are excluded from a group's cost but not from the rate",
         pb.job_usd("m-1", "high", "author", [dict(j, usd=0.1, truncated=True) for j in jobs] + jobs) == 4.0
         and pb.job_drain("p", "five_hour", "m-1", "high", "author", jobs=clipped, samples=samples)["basis"] == "measured")
    case("a job whose period it never sat inside adds no cost to that period's rate",
         round(pb.plan_rate("p", "five_hour", jobs + [dict(jobs[0], start=NOW - 99999, end=NOW - 99000, usd=500)],
                            samples)[0], 2) == 2.0)

    def cached_drain_follows_reread():
        saved = dict(pb._LEDGER_CACHE), dict(pb._SAMPLES_CACHE), pb._samples
        pb._samples = lambda: pb._SAMPLES_CACHE["rows"]
        try:
            held_jobs, held_samples = history(rate=2.0)
            now = time.time()
            pb._LEDGER_CACHE.update(at=now, rows=held_jobs)
            pb._SAMPLES_CACHE.update(at=now, rows=held_samples)
            first = pb.job_drain("p", "five_hour", "m-1", "high", "author")["rate"]
            held_samples[:] = history(rate=3.0)[1]
            pb._SAMPLES_CACHE["at"] = now + 1
            second = pb.job_drain("p", "five_hour", "m-1", "high", "author")["rate"]
            return round(first, 2) == 2.0 and round(second, 2) == 3.0, repr((first, second))
        finally:
            pb._LEDGER_CACHE.update(saved[0])
            pb._SAMPLES_CACHE.update(saved[1])
            pb._samples = saved[2]
    ok, detail = cached_drain_follows_reread()
    case("a drain cached before the samples were re-read is recomputed after, whatever the lists' identity", ok, detail)

    row = pb.ledger_job(LEDGER_ROW)
    case("a ledger row becomes a finished job with its plan, span, list-price cost, group and truncation",
         row and row["plan"] == "anthropic-secondary" and row["truncated"] and row["shape"] == "author"
         and row["model"] == "claude-sonnet-5-5" and row["effort"] == "high" and row["usd"] == 2.241479
         and round(row["end"] - row["start"], 1) == 611.5, repr(row))
    case("a ledger row with no priced cost or not plan-quota is no job",
         pb.ledger_job(dict(LEDGER_ROW, costUSD=None)) is None
         and pb.ledger_job(dict(LEDGER_ROW, costModel="marginal-usd")) is None)

    case("fits: a plan holds a job while its headroom covers the jobs on it and this one",
         pb.fits({"headroom": 20.0}, 8.0, 12.0) and not pb.fits({"headroom": 20.0}, 8.1, 12.0)
         and not pb.fits({"headroom": 8.0}, 0.0, 12.6) and pb.fits({"headroom": 8.0}, 4.0, 4.0))
    case("fits: a row with no headroom reading fits, so the launcher's own gate decides",
         pb.fits({"transport": "x"}, 50.0, 50.0))

    five = 5 * 60
    soon, late = NOW + 3600, NOW + 7200
    two = reading("two", 50, 0.5)
    two["quota"]["windows"].append({"name": "five_hour", "usedPercent": 92, "resetsAt": soon,
                                    "windowDurationMins": five})
    two["quota"]["windows"].append({"name": "other", "usedPercent": 60, "resetsAt": late,
                                    "windowDurationMins": five})
    row = pb.plan_room("two", registry(["two"]), reader({"two": two}), NOW)
    case("a plan row names the window its headroom comes from and when that window resets",
         row.get("headroom") == 8.0 and row.get("headroomWindow") == "five_hour"
         and row.get("headroomResetsAt") == soon, repr(row))

    week_reset = NOW + 0.5 * WEEK_MIN * 60
    case("a plan row lists every window's own headroom and reset, not only the tightest",
         {w["name"]: (w["headroom"], w["resetsAt"]) for w in row.get("windows") or []}
         == {"seven_day": (50.0, week_reset), "five_hour": (8.0, soon), "other": (40.0, late)}, repr(row))

    floored = reading("floored", 50, 0.5)
    floored["quota"]["windows"].append({"name": "five_hour", "usedPercent": 95, "resetsAt": soon,
                                        "windowDurationMins": five, "dispatchFloorPercent": 98})
    row = pb.plan_room("floored", registry(["floored"]), reader({"floored": floored}), NOW)
    case("a plan's headroom ends at its window's dispatch floor, where the launcher refuses, not at 100%",
         row.get("headroom") == 3.0 and row.get("headroomWindow") == "five_hour", repr(row))


CONSOLE = "anthropic-console"
CONSOLE_SECOND = "anthropic-console-secondary"
CYCLE_MIN = 10 * 24 * 60


def credit_reading(transport, used, left_days, days=10, status="available", has_credits=True,
                   balance=None):
    """A reading in the shape `scripts/console_credit_probe.py` emits: the credit cycle is the plan's
    ONE window and the dollars left ride in `credits` beside it."""
    win = {"name": "credit_cycle", "usedPercent": used, "resetsAt": NOW + left_days * 86400,
           "windowDurationMins": days * 24 * 60, "pressure": 1.0, "band": "Hot", "runoutRatio": 1.0}
    return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": "2027-01-15T08:00:00Z", "status": status,
            "quota": {"windows": [win], "routingWindow": "credit_cycle",
                      "bindingWindow": "credit_cycle", "routingBand": "Hot", "dispatchBand": "Hot",
                      "credits": {"enabled": True, "available": has_credits,
                                  "hasCredits": has_credits,
                                  "balance": 100.0 if balance is None else balance}}}


def unknown_reading(transport):
    """The probe's fail-closed reading: no anchor, a stale cycle or unaccounted spend."""
    return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": "2027-01-15T08:00:00Z", "status": "malformed", "quota": None, "balance": None,
            "error": {"kind": "malformed", "message": "no anchor recorded"}, "liveFailure": None}


def credit_registry(status="available"):
    """The anthropic route with both Console credit plans beside the login plan, plus Go."""
    data = registry(["anthropic", "go"], pay=())
    data["transports"]["anthropic"]["launcher"] = ".claude/scripts/anthropic_sidecar.sh"
    for name, launcher in ((CONSOLE, ".claude/scripts/anthropic_console_sidecar.sh"),
                           (CONSOLE_SECOND, ".claude/scripts/anthropic_console_secondary_sidecar.sh")):
        data["transports"][name] = {"costModel": "plan-quota", "planOf": "anthropic",
                                    "launcher": launcher, "status": {"state": status}}
    return data


def credit_cases(case):
    """S5: a Console credit reading enters the pick as one more plan-quota window, with no
    credit-specific rank -- room decides, and a spent or unreadable credit drops out like any other."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    import quota_dashboard

    def with_credit(value, base=None):
        rows = dict(base or {"anthropic": reading("anthropic", 22, 6.1 / 7),
                             "go": reading("go", 30, 6.3 / 7)})
        rows[CONSOLE] = value
        return rows

    # A credit a day from its reset with 97% of a 10-day grant left: room 97 / (100 x 0.1) = 9.7.
    near = with_credit(credit_reading(CONSOLE, 3, 1.0))
    got = pb.pick("executor", "anthropic", data=credit_registry(), read=reader(near), now=NOW)
    rooms = {r["transport"]: r.get("room") for r in got.get("ranked") or []}
    case("a credit a day from its reset with 97% of its grant left outranks a Max week at 0.90",
         got.get("transport") == CONSOLE and round(got.get("room") or 0, 1) == 9.7
         and round(rooms.get("anthropic") or 0, 2) == 0.90 and rooms.get("go") is not None,
         repr(got))
    case("the reason names the credit window by its length and figures, like any other window",
         "anthropic-console Hot: 10d 97% left, 10% to go" in (got.get("reason") or ""),
         got.get("reason"))

    for label, spent in (("a spent status", credit_reading(CONSOLE, 60, 1.0, status="exhausted")),
                         ("a spent grant", credit_reading(CONSOLE, 100, 1.0, has_credits=False,
                                                          balance=0.0))):
        got = pb.pick("executor", "anthropic", data=credit_registry(),
                      read=reader(with_credit(spent)), now=NOW)
        why = {r["transport"]: r.get("why") for r in got.get("ranked") or [] if r.get("room") is None}
        case("a credit plan dropped by %s leaves the pick, named with why" % label,
             got.get("transport") == "anthropic" and why.get(CONSOLE) == "exhausted", repr(got))

    got = pb.pick("executor", "anthropic", data=credit_registry(),
                  read=reader(with_credit(unknown_reading(CONSOLE))), now=NOW)
    why = {r["transport"]: r.get("why") for r in got.get("ranked") or [] if r.get("room") is None}
    case("an unanchored credit reading drops out as unreadable, never ranked as room",
         got.get("transport") == "anthropic" and "unreadable" in str(why.get(CONSOLE)), repr(got))

    # The probe's own cause (the unpriced ledger row and the step that clears it) reaches the reader's why.
    cause = ("a console-secondary ledger row after the anchor (c3-exec, 2026-10-15T02:36:09Z) carries no priced "
             "costUSD, so the credit's use is unaccounted; Read the Console balance and re-anchor past it")
    stuck = unknown_reading(CONSOLE)
    stuck["error"]["message"] = cause
    got = pb.pick("executor", "anthropic", data=credit_registry(),
                  read=reader(with_credit(stuck)), now=NOW)
    why = {r["transport"]: r.get("why") for r in got.get("ranked") or [] if r.get("room") is None}
    case("an unanchored credit's why carries the probe's cause and its re-anchor step",
         cause in str(why.get(CONSOLE)), repr(why))

    case("the shipped registry offers both Console credit plans to the pick",
         all(n in model_registry.plans_of("anthropic") for n in (CONSOLE, CONSOLE_SECOND))
         and all(n in pb.plan_quota_transports() for n in (CONSOLE, CONSOLE_SECOND)),
         repr(pb.plan_quota_transports()))

    # The owner-facing view: both credits are plans in the table, in registry order, with no rank.
    dash = credit_registry()
    view = quota_dashboard.build(data=dash, read=reader(near), session_plan="anthropic", now=NOW)
    text = quota_dashboard.render(view)
    order = pb.plan_quota_transports(dash)
    credit_row = next((p for p in view["plans"] if p["plan"] == CONSOLE), {})
    case("the master view lists both credit plans in registry order, with no ranking column",
         [p["plan"] for p in view["plans"]] == order
         and all(p["plan"] in text for p in view["plans"])
         and CONSOLE in text and CONSOLE_SECOND in text
         and [w.get("left") for w in credit_row.get("windows") or []] == [97.0],
         repr(order) + " / " + repr([p["plan"] for p in view["plans"]]))


def facts_cases(case):
    """The facts layer: `facts` returns each fit plan in registry order with no choice; `pick` ranks it, and
    `room_facts` reads it without re-ranking, so a dispatch caller never un-ranks a pick."""
    facts = getattr(pb, "facts", None)
    if facts is None:
        case("provider_bands exposes a facts(tier, ...) layer beside pick", False, "no facts function")
        return
    # Registry order is alphabetical (plan_quota_transports); secondary has the room, so the pick runs against it.
    roomy = {"secondary": reading("secondary", 32, 0.6), "codex": reading("codex", 40, 0.59)}
    data = registry(["secondary", "codex"])
    got = facts("executor", "anthropic", data=data, read=reader(roomy), now=NOW)
    picked = pb.pick("executor", "anthropic", data=data, read=reader(roomy), now=NOW)
    names = [r["transport"] for r in got.get("plans") or []]
    case("facts lists each fit plan in registry order, not in room order",
         names == ["codex", "secondary"] and picked.get("transport") == "secondary", repr(got))
    case("facts names no paying plan and no reason: the choice stays in pick",
         "transport" not in got and "reason" not in got and "ranked" not in got, repr(sorted(got)))
    facts_view = pb.room_facts(got, read=reader(roomy), now=NOW)
    case("room_facts reads facts directly, in the same registry order",
         [p["transport"] for p in facts_view["plans"]] == names and not facts_view["low"], repr(facts_view))
    late = {"secondary": reading("secondary", 40, 0.4), "codex": reading("codex", 40, 0.4)}
    got_late = facts("executor", "anthropic", data=data, read=reader(late), now=NOW)
    view_late = pb.room_facts(got_late, read=reader(late), now=NOW)
    case("facts and room_facts carry each plan's projection, a fact and no new sort",
         all(isinstance(r.get("unusedAtReset"), dict) for r in got_late["plans"])
         and all(isinstance(p.get("unusedAtReset"), dict) for p in view_late["plans"])
         and "transport" not in facts_view, repr(view_late["plans"]))
    spare = getattr(pb, "spare_pct", None)
    at = {"unusedAtReset": {"pct": pb.USED_RESOLUTION, "daysLeft": 2}}
    below = {"unusedAtReset": {"pct": pb.USED_RESOLUTION - 0.6, "daysLeft": 2}}
    case("spare_pct reads a row or a projection: whole percents at the threshold, None below it",
         callable(spare) and spare(at) == round(pb.USED_RESOLUTION) and spare(at["unusedAtReset"]) == spare(at)
         and spare(below) is None and spare({"unusedAtReset": {"pct": 7.6}}) == 8,
         repr((spare and spare(at), spare and spare(below))))
    case("spare_pct is None for a row with no projection, a malformed one or a bool",
         callable(spare) and spare({}) is None and spare(None) is None and spare({"unusedAtReset": None}) is None
         and spare({"pct": "9"}) is None and spare({"pct": True}) is None, "")


def cli_case(case):
    """`provider_bands.py room|pick executor` on the live registry against a planted fresh cache."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    now = time.time()
    shares = {"anthropic": (22, 6.1 / 7), "go": (30, 6.3 / 7), "codex": (77, 5.1 / 7)}
    blob = {}
    for name, cfg in (model_registry.load().get("transports") or {}).items():
        if cfg.get("costModel") != "plan-quota":
            continue
        used, left = shares.get(name, (99, 0.5))
        entry = reading(name, used, left)
        win = entry["quota"]["windows"][0]
        win["resetsAt"] = now + left * WEEK_MIN * 60
        entry["observedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        entry.update(error=None, liveFailure=None, balance=None)
        blob[name] = entry
    folder = tempfile.mkdtemp(prefix="pick_cli_")
    cache = os.path.join(folder, "provider_capacity.json")
    with open(cache, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    env = dict(os.environ, PROVIDER_CAPACITY_CACHE=cache, CLAUDE_CODE_TRANSPORT="anthropic")
    env.pop("ANTHROPIC_BASE_URL", None)
    r = subprocess.run([sys.executable, TOOL, "room", "executor"], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env)
    crashed = r.returncode not in (0, 2) or "Traceback" in (r.stderr or "")
    lines = r.stdout.splitlines()
    case("CLI `room executor` prints one entry per plan and never a pick", not crashed and r.returncode == 0
         and any(l.startswith("anthropic Hot: 7d 78% left, 87% to go") for l in lines)
         and any(l.startswith("go Hot: 7d 70% left, 90% to go") for l in lines)
         and "pick" not in r.stdout.lower(), (r.stdout + r.stderr)[-400:])
    r = subprocess.run([sys.executable, TOOL, "room", "executor", "--json"], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env)
    try:
        got = json.loads(r.stdout)
    except ValueError:
        got = {}
    case("CLI `room executor --json` gives the plans' facts and `low`, no chosen transport",
         got.get("low") is False and isinstance(got.get("plans"), list) and "transport" not in got,
         (r.stdout + r.stderr)[-400:])
    r = subprocess.run([sys.executable, TOOL, "pick", "executor", "--json"], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env)
    try:
        got = json.loads(r.stdout)
    except ValueError:
        got = {}
    case("CLI `pick executor --json` still serves a tool choosing a plan for a fixed row",
         got.get("transport") == "anthropic" and isinstance(got.get("ranked"), list), (r.stdout + r.stderr)[-400:])
    r = subprocess.run([sys.executable, TOOL, "pick", "executor"], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env)
    case("CLI `pick` without --json prints no pick for a reader", r.returncode == 2 and not r.stdout.strip(),
         (r.stdout + r.stderr)[-300:])
    r = subprocess.run([sys.executable, TOOL, "room", "bogus"], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env)
    case("CLI `room` with an unknown tier exits 2 naming the legal tiers",
         r.returncode == 2 and "executor" in r.stderr and "Traceback" not in r.stderr, r.stderr[-300:])


LEAN_LADDER = """## Role guidance

| model | cost | effort | ± |
|---|---|---|---|
| opus | 5 | `high` to start | + the strongest architect; − the most expensive |
| sonnet | 7 | `xhigh` for a plan review | + the cheap architect; − not the best |
| go-m | 8 | `medium` | − no design verdicts |
| paid-m | 1 | `low` | − pay per token |
"""


def lean_registry():
    """Anthropic and its secondary plan, Go, and a marginal-usd API route, each with an architect row. `ghost` has
    no ladder row, so its cost is unknown."""
    return {"transports": {"anthropic": {"costModel": "plan-quota"},
                           "anthropic-secondary": {"costModel": "plan-quota", "planOf": "anthropic"},
                           "go": {"costModel": "plan-quota"},
                           "paid": {"costModel": "marginal-usd"}},
            "models": [
                {"id": "opus-id", "alias": "opus", "transport": "anthropic", "roles": ["architect"],
                 "probation": [{"tier": "architect", "text": "planted mark", "evidenceRef": "planted"}]},
                {"id": "go-model", "alias": "go-m", "transport": "go", "roles": ["architect"]},
                {"id": "sonnet-id", "alias": "sonnet", "transport": "anthropic", "roles": ["architect", "executor"],
                 "probation": [{"tier": "executor", "text": "other tier", "evidenceRef": "planted"}]},
                {"id": "paid-id", "alias": "paid-m", "transport": "paid", "roles": ["architect"]},
                {"id": "ghost-id", "alias": "ghost", "transport": "go", "roles": ["architect"]}]}


def lean_cases(case):
    """S2: `lean_candidates` states every row claiming a tier, per route, with its cost, `±` cell, effort cell, owner
    probation mark and each plan's room, in registry order. It never picks one, and an unknown seat is refused."""
    lean = getattr(pb, "lean_candidates", None)
    case("provider_bands exposes lean_candidates", callable(lean))
    if not callable(lean):
        return
    folder = tempfile.mkdtemp(prefix="lean_ladder_")
    ladder = os.path.join(folder, "model_ladder_evidence.md")
    with open(ladder, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(LEAN_LADDER)
    data = lean_registry()
    rooms = {"anthropic": reading("anthropic", 22, 6.1 / 7),
             "anthropic-secondary": reading("anthropic-secondary", 60, 0.5, status="exhausted"),
             "go": reading("go", 30, 6.3 / 7)}
    got = lean("architect", "anthropic", data=data, read=reader(rooms), now=NOW, ladder=ladder)
    routes = got.get("routes") or {}
    ids = {t: [r["id"] for r in rows] for t, rows in routes.items()}
    case("routes group by route with the session's route first, rows in registry order",
         list(routes) == ["anthropic", "go"] and ids.get("anthropic") == ["opus-id", "sonnet-id"]
         and ids.get("go") == ["go-model", "ghost-id"], repr(ids))
    flipped = lean("architect", "go", data=data, read=reader(rooms), now=NOW, ladder=ladder)
    case("the session's route leads when the session sits on the other route",
         list(flipped.get("routes") or {}) == ["go", "anthropic"], repr(list(flipped.get("routes") or {})))

    rows = {r["alias"]: r for rs in routes.values() for r in rs}
    opus = rows.get("opus", {})
    case("a row carries its ladder cost, ± cell, effort cell and the owner's mark for this tier",
         opus.get("cost") == 5 and "+ the strongest architect" in (opus.get("minus") or "")
         and opus.get("effort") == "`high` to start" and opus.get("probation") == "planted mark", repr(opus))
    case("a probation mark for another tier is not reported for this one",
         rows.get("sonnet", {}).get("probation") is None and rows.get("sonnet", {}).get("cost") == 7,
         repr(rows.get("sonnet")))
    ghost = rows.get("ghost", {})
    case("a row with no ladder cell is listed with cost None, never dropped",
         "ghost" in rows and ghost.get("cost") is None and ghost.get("minus") is None, repr(ghost))
    plans = [(p["plan"], None if p["room"] is None else round(p["room"], 2)) for p in opus.get("plans") or []]
    case("each row lists every plan of its route with its room, a dropped plan with room None",
         plans == [("anthropic", 0.9), ("anthropic-secondary", None)], repr(plans))
    case("each row's plans carry their projection beside their room",
         all("unusedAtReset" in p for p in opus.get("plans") or [])
         and next(p for p in opus["plans"] if p["plan"] == "anthropic")["unusedAtReset"] is None,
         repr(opus.get("plans")))
    case("a candidate set names no transport or room of its own, so it never picks",
         "transport" not in got and "room" not in got and not got.get("refused"), repr(sorted(got)))
    case("marginal-usd rows stay out of routes while a plan has room, and no pay option is offered",
         "paid" not in routes and got.get("payOptions") == [], repr(got.get("payOptions")))

    spent = {"anthropic": reading("anthropic", 100, 0.5, status="exhausted"),
             "anthropic-secondary": reading("anthropic-secondary", 60, 0.5, status="exhausted"),
             "go": reading("go", 100, 0.5, status="exhausted")}
    dry = lean("architect", "anthropic", data=data, read=reader(spent), now=NOW, ladder=ladder)
    case("marginal-usd rows appear only under payOptions, and only when no plan has room",
         dry.get("payOptions") == [{"alias": "paid-m", "id": "paid-id", "transport": "paid", "supplementary": False}]
         and "paid" not in (dry.get("routes") or {}), repr(dry.get("payOptions")))

    supp_data = lean_registry()
    supp_data["models"] += [
        {"id": "supp-id", "alias": "supp", "transport": "anthropic", "roles": ["fanout"], "supplementary": ["architect"]},
        {"id": "supp-go-id", "alias": "supp-go", "transport": "go", "roles": ["fanout"], "supplementary": ["architect"]}]
    for row in supp_data["models"]:
        if row["alias"] in ("sonnet", "paid-m"):
            row["supplementary"] = ["architect"]
    mixed = lean("architect", "anthropic", data=supp_data, read=reader(rooms), now=NOW, ladder=ladder)
    mixed_ids = {t: [r["id"] for r in rs] for t, rs in (mixed.get("routes") or {}).items()}
    case("a supplementary-only row is left out of routes: lean never offers it for a lone seat or authoring",
         "supp-id" not in sum(mixed_ids.values(), []) and "supp-go-id" not in sum(mixed_ids.values(), []),
         repr(mixed_ids))
    listed = {t: [r["id"] for r in rs] for t, rs in (mixed.get("supplementary") or {}).items()}
    case("a supplementary-only row is listed under `supplementary`, per route, in registry order",
         listed == {"anthropic": ["supp-id"], "go": ["supp-go-id"]}, repr(listed))
    case("a plain claimant that also holds the tier as supplementary stays in routes, flagged supplementary: true",
         {r["alias"]: r.get("supplementary") for rs in mixed["routes"].values() for r in rs}
         == {"opus": False, "sonnet": True, "go-m": False, "ghost": False}, repr(mixed["routes"]))
    case("a plain+supplementary row is not listed under `supplementary` (it is already a claimant)",
         "sonnet-id" not in sum(listed.values(), []), repr(listed))
    supp_dry = lean("architect", "anthropic", data=supp_data, read=reader(spent), now=NOW, ladder=ladder)
    alone = lean_registry()
    alone["transports"]["extra"] = {"costModel": "plan-quota"}
    alone["models"].append({"id": "supp-extra-id", "alias": "supp-extra", "transport": "extra",
                            "roles": ["fanout"], "supplementary": ["architect"]})
    for row in alone["models"]:
        if row["alias"] == "paid-m":
            row["supplementary"] = ["architect"]
    stranded = dict(spent, extra=reading("extra", 10, 0.9))
    lone_dry = lean("architect", "anthropic", data=alone, read=reader(stranded), now=NOW, ladder=ladder)
    case("a supplementary-only row on a route with room does not hide payOptions: room is judged over claimant routes",
         [r["alias"] for r in lone_dry.get("payOptions") or []] == ["paid-m"]
         and list(lone_dry.get("supplementary") or {}) == ["extra"], repr(lone_dry.get("payOptions")))
    case("payOptions flags a plain+supplementary row supplementary: true",
         supp_dry.get("payOptions") == [{"alias": "paid-m", "id": "paid-id", "transport": "paid", "supplementary": True}],
         repr(supp_dry.get("payOptions")))
    case("a candidate set with no supplementary rows carries an empty `supplementary`",
         got.get("supplementary") == {}, repr(got.get("supplementary")))

    stranger = lean("architect", "nowhere", data=data, read=reader(rooms), now=NOW, ladder=ladder)
    case("an unknown seat sets refused and returns no routes or pay options",
         bool(stranger.get("refused")) and stranger.get("routes") == {} and stranger.get("payOptions") == [],
         repr(stranger))


def lean_cli_case(case):
    """`provider_bands.py lean <tier>`: a copy of the live registry with a login dir on the Anthropic plan, the live
    ladder beside it, and a fresh capacity cache. Asserts invariants only, never a live value."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    import shutil
    folder = tempfile.mkdtemp(prefix="lean_cli_")
    login = os.path.join(folder, "login-anthropic")
    reg = os.path.join(folder, "a", "b", "external_models.json")
    ladder_copy = os.path.join(folder, ".claude", "reference", "model_ladder_evidence.md")
    data = model_registry.load()
    data["transports"]["anthropic"]["configDir"] = login
    os.makedirs(os.path.dirname(reg))
    with open(reg, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    os.makedirs(os.path.dirname(ladder_copy))
    shutil.copyfile(str(model_registry.ladder_path()), ladder_copy)
    now = time.time()
    blob = {}
    for name, cfg in data["transports"].items():
        if cfg.get("costModel") != "plan-quota":
            continue
        entry = reading(name, 22 if name == "anthropic" else 50, 0.5)
        entry["quota"]["windows"][0]["resetsAt"] = now + 0.5 * WEEK_MIN * 60
        entry["observedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        entry.update(error=None, liveFailure=None, balance=None)
        blob[name] = entry
    cache = os.path.join(folder, "provider_capacity.json")
    with open(cache, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    base = dict(os.environ, PROVIDER_CAPACITY_CACHE=cache, HARNESS_MODEL_REGISTRY=reg)
    base.pop("ANTHROPIC_BASE_URL", None)
    base.pop("CLAUDE_CODE_TRANSPORT", None)

    def run(argv, config_dir):
        env = dict(base, CLAUDE_CONFIG_DIR=config_dir)
        return subprocess.run([sys.executable, TOOL, *argv], capture_output=True, text=True,
                              encoding="utf-8", timeout=120, env=env)

    r = run(["lean", "architect"], login)
    lines = r.stdout.splitlines()
    case("CLI `lean architect` prints the session's route first, each row with its cost, plans, effort and ± cell",
         r.returncode == 0 and "Traceback" not in r.stderr and lines[:1] == ["anthropic"]
         and "cost" in r.stdout and "effort" in r.stdout, (r.stdout + r.stderr)[-400:])
    r = run(["lean", "architect", "--json"], login)
    try:
        got = json.loads(r.stdout)
    except ValueError:
        got = {}
    case("CLI `lean architect --json` names the seat, leads with its route, and picks no transport",
         r.returncode == 0 and got.get("seat") == "anthropic" and not got.get("refused")
         and list(got.get("routes") or {})[:1] == ["anthropic"] and "transport" not in got,
         (r.stdout + r.stderr)[-400:])
    r = run(["lean", "architect"], os.path.join(folder, "nowhere"))
    case("CLI `lean` from an unknown session exits 2 naming CLAUDE_CONFIG_DIR, with no stdout",
         r.returncode == 2 and "CLAUDE_CONFIG_DIR" in r.stderr and "Traceback" not in r.stderr
         and not r.stdout.strip(), (r.stdout + r.stderr)[-300:])
    r = run(["lean", "bogus"], login)
    case("CLI `lean` with an unknown tier exits 2 naming the legal tiers",
         r.returncode == 2 and "executor" in r.stderr and "Traceback" not in r.stderr, r.stderr[-300:])


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % detail[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
