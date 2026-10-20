#!/usr/bin/env python3
"""Read every plan-quota transport's OWN band and room: the facts a dispatch weighs to choose who pays.

A plan's ROOM is the share of its routing window's allowance left over the share of that window's
time left: `(100 - usedPercent) / (100 x fraction of the window left)`. 1.0 is even pace, the rate
the plan can spend at until its reset. The pick ranks a plan by its routing window's room: that
window's unused share is what expires at its reset. Every other window is a capacity gate, not a rank.
A job that starts now spends from every window, so a non-routing window GATES the plan when its
remaining share cannot carry even-pace work to its own reset: its shortfall, the share of its time
left minus the share of its allowance left, exceeds `USED_RESOLUTION`, the one whole percent that
providers report `usedPercent` in. A gated plan ranks by that window's room (`rank_room`; the row's
`binding`/`bindingRoom`), which demotes it. A shortfall within `USED_RESOLUTION` sits inside the
reading's error, so it never gates.
A spent window of any length drops the plan as exhausted. Unlike pace or run-out, room does not
punish an early burst:
20% used in the first 5% of a week leaves room 0.84. Bands (`quota_bands.py`) stay the pacing
signal; room is weighed with fit and cost in the model ladder's selection line, never read as a pick.

`pick(tier, session_plan)` ranks by room every live plan that can pay for a row claiming the tier:
the row's transport and each plan whose `planOf` names it (`model_registry.plans_of`). An exhausted
or unreadable plan drops out, named with why: unreadable is never ranked as roomy. The session's own
plan is a candidate like any other and wins a tie, since it saves a hop and a cold start. With no
plan left, the fit `marginal-usd` rows come back as `payOptions`. Its top row is a computed choice,
so only a tool choosing which plan pays for one fixed row reads it (the bench's launcher choice);
a dispatch-facing caller reads `ranked`, the facts: `hooks/_dispatch_checks.currency`,
`hooks/workflow_provider_guard._pick_route` and the `room` CLI.

FAIL POSTURE: every failure returns an empty/None reading. A budget advisory that raises
would block a dispatch over a hint, and a probe that spawns a subprocess has many ways to
fail that have nothing to do with the allowance it was asked about.

Provider-capacity execution, cache freshness and fallback live in `provider_capacity.py`.

Human-readable plan entries show quota left and time left, never a derived ratio or gap.

CLI:
    provider_bands.py list                      each plan-quota transport's band and pressure
    provider_bands.py probe <transport>          one transport's routing-band view as JSON
    provider_bands.py room <tier> [--json]       each plan paying for a <tier> row: quota left, window left; `low` when none has allowance
    provider_bands.py pick <tier> --json         the most-room plan, for a tool choosing a plan for a fixed row
    provider_bands.py lean <tier> [--json]       each row claiming a <tier>, per route: cost, ± cell, effort cell, owner mark, each plan's room; facts, never a pick
    provider_bands.py warm <transport>...        read each transport, refreshing a missing or expired cache entry
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cli_usage import usage  # noqa: E402
import model_registry  # noqa: E402
import provider_capacity  # noqa: E402
import quota_bands  # noqa: E402

DEFAULT_TTL_S = 600  # compatibility parameter; provider_capacity owns freshness
USED_RESOLUTION = 1.0  # percentage points: providers report usedPercent in whole percents
# What one sidecar job is assumed to take from its plan's tightest window, in percentage points. Room is a
# pace ratio and says nothing about a burst: on 2026-10-14 a 5h window at 24% left read room 0.97, 13 jobs
# went to it, and 6+ hit the limit within 15 minutes (about 4 points a job). Thin basis: one burst.
JOB_DRAIN_PCT = float(os.environ.get("SIDECAR_JOB_DRAIN_PCT") or 4)

def probe(transport, data=None):
    """Read normalized capacity (`provider_capacity.reading`: cache first) and return its routing-band view.

    Two pressures, because two decisions read them: `pressure` is the BINDING window's pace (the
    cap that will actually stop work) and stays the displayed number, while `routingPressure` and
    `runoutRatio` come from the window that supplied `routingBand`, which is the only window whose
    numbers a routing comparison may weigh. They are the same row on Go (its routing band follows
    its tightest window) and differ on Anthropic/Codex (weekly routing, five-hour binding).
    """
    try:
        capacity = provider_capacity.reading(transport, data=data)
    except Exception:
        return None
    quota = capacity.get("quota") if isinstance(capacity, dict) else None
    if not isinstance(quota, dict):
        return None
    view = provider_capacity.routing_view(capacity)
    if not view:
        return None
    return {
        "transport": transport,
        "band": view["band"],
        "pressure": view["binding"].get("pressure"),
        "routingWindow": quota.get("routingWindow"),
        "routingPressure": view["routing"].get("pressure"),
        "runoutRatio": view["routing"].get("runoutRatio"),
        "planType": capacity.get("planType"),
        "sourceKind": capacity.get("sourceKind"),
        "observedAt": capacity.get("observedAt"),
    }


def reading(transport, ttl=DEFAULT_TTL_S, data=None):
    """Compatibility entry point; provider_capacity owns the only cache and freshness policy."""
    _ = ttl
    return probe(transport, data)


def plan_quota_transports(data=None):
    """Plans that spend a plan allowance, whose route has at least one selectable model and whose
    own status is not unavailable.

    A plan with no available model is out of consideration entirely -- advising a route
    to it would name a launcher that refuses at its own availability gate.
    """
    data = data if data is not None else model_registry.load()
    live = {m["transport"] for m in model_registry.available_models(data)}
    out = []
    for name, cfg in (data.get("transports") or {}).items():
        if model_registry.route_of(name, data) not in live:
            continue
        if ((cfg or {}).get("status") or {}).get("state") == "unavailable":
            continue
        try:
            if model_registry.cost_model_for(name, data) == "plan-quota":
                out.append(name)
        except Exception:
            continue
    return sorted(out)


def room(window, now=None):
    """The window's room, `(100 - usedPercent) / (100 x fraction of its time left)`, or None.

    None when the window cannot say: no used share, no length, no reset, or a reset already
    passed (the reading predates the window it describes). A dormant window (0% used, no reset)
    has its whole allowance and its whole time ahead, so its room is 1.0.
    """
    if not isinstance(window, dict):
        return None
    now = time.time() if now is None else now
    used = window.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return None
    reset = provider_capacity._epoch(window.get("resetsAt"))
    minutes = window.get("windowDurationMins")
    if reset is None:
        return 1.0 if used == 0 else None
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    left = (reset - now) / (minutes * 60.0)
    if left <= 0:
        return None
    return (100.0 - used) / (100.0 * min(left, 1.0))


def read_capacity(transport, data=None, live=True):
    """A transport's normalized reading: the fresh cache first, else `provider_capacity.reading` when `live`
    (itself cache-first, one live read per transport at a time)."""
    got = provider_capacity.cached(transport, data=data)
    if got is None and live:
        got = provider_capacity.reading(transport, data=data)
    return got


def _plan_room(transport, data, read, now):
    """{transport, room, window, left, daysLeft, planType} for a plan with allowance left, else
    {transport, room: None, why}."""
    try:
        got = read(transport, data)
    except Exception as exc:
        return {"transport": transport, "room": None,
                "why": "unreadable: %s" % (str(exc)[:120] or type(exc).__name__)}
    if not isinstance(got, dict):
        return {"transport": transport, "room": None, "why": "unreadable: no fresh reading"}
    status = got.get("status")
    if status == "dispatch-floor":
        return {"transport": transport, "room": None, "why": provider_capacity.floor_refusal(got, now)}
    if status == "exhausted":
        return {"transport": transport, "room": None, "why": "exhausted"}
    if status != "available":
        return {"transport": transport, "room": None, "why": "unreadable: status %s" % status}
    quota = got.get("quota") if isinstance(got.get("quota"), dict) else {}
    name = quota.get("routingWindow")
    window = next((w for w in quota.get("windows") or []
                   if isinstance(w, dict) and w.get("name") == name), None)
    if window is None:
        return {"transport": transport, "room": None, "why": "unreadable: no routing window"}
    windows = [w for w in quota.get("windows") or [] if isinstance(w, dict)]
    for w in [window] + [w for w in windows if w is not window]:
        if quota_bands.is_exhausted(w.get("usedPercent"), quota.get("credits")):
            return {"transport": transport, "room": None,
                    "why": "exhausted" if w is window else "exhausted: %s window" % w.get("name")}
    value = room(window, now)
    if value is None:
        return {"transport": transport, "room": None,
                "why": "unreadable: routing window %s has no used share, length or future reset" % name}
    reset = provider_capacity._epoch(window.get("resetsAt"))
    row = {"transport": transport, "room": value, "window": name,
           "left": 100.0 - window["usedPercent"],
           "headroom": min(100.0 - w["usedPercent"] for w in windows
                           if isinstance(w.get("usedPercent"), (int, float)) and not isinstance(w["usedPercent"], bool)),
           "daysLeft": (reset - now) / 86400.0 if reset is not None else None,
           "planType": got.get("planType")}
    gates = [(room(w, now), w.get("name")) for w in windows if w is not window and _gates(w, now)]
    tightest = min((g for g in gates if g[0] < value), default=None)
    if tightest:
        row["bindingRoom"], row["binding"] = tightest
    return row


def _gates(window, now):
    """True when a non-routing window's allowance left falls short of its time left by more than `USED_RESOLUTION`."""
    value = room(window, now)
    if value is None or value >= 1.0:
        return False
    left = 100.0 - window["usedPercent"]
    return value == 0 or left / value - left > USED_RESOLUTION


def rank_room(row):
    """The room a plan ranks by: a gating non-routing window's (below the routing room), else the routing window's."""
    return row["bindingRoom"] if row.get("bindingRoom") is not None else row["room"]


def pick_key(row, placed=0):
    """Sort key (best last) for a fan-out pick of the plan `row` describes, with `placed` jobs already on it.

    A plan fits while its tightest window, `headroom`, still holds the `placed` jobs and this one at
    `JOB_DRAIN_PCT` each; fitting plans rank by `rank_room`. A plan that no longer fits ranks below every
    one that does, the least overdrawn first: it is picked only when no plan fits. A row with no
    `headroom` reading fits, so it keeps the pace ranking it had."""
    headroom = row.get("headroom")
    if not isinstance(headroom, (int, float)):
        return (1, rank_room(row))
    spare = headroom - (placed + 1) * JOB_DRAIN_PCT
    return (1, rank_room(row)) if spare >= 0 else (0, spare)


def plan_rows(data=None, read=None, now=None):
    """One `_plan_room` row per plan-quota transport, in registry order: the owner-facing view."""
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    return [_plan_room(name, data, read or read_capacity, now) for name in plan_quota_transports(data)]


def spare(row):
    """The % of a plan's window that even-pace spending leaves unused at its reset, or None.

    Even pace spends the share of the window that time has left, `left / room`, so the rest,
    `left x (1 - 1/room)`, expires. None at or below even pace, and for an unreadable row.
    """
    value, left = row.get("room"), row.get("left")
    if not isinstance(value, (int, float)) or not isinstance(left, (int, float)) or value <= 1.0:
        return None
    return left * (1.0 - 1.0 / value)


def display_windows(capacity, now=None):
    """Readable windows, worst band first and the tighter window first within a band; equal bands need
    only one set of figures, the tighter window's.

    Each row is the capacity's window plus `band`, `left` (quota left, a percent from 0 to 100) and
    `timeLeft` (window time left, a percent from 0 to 100, 100 when no reset is known).
    """
    now = time.time() if now is None else now
    quota = (capacity or {}).get("quota") or {}
    rows = []
    for window in quota.get("windows") or []:
        value = room(window, now)
        if value is None:
            continue
        used = window["usedPercent"]
        reset = provider_capacity._epoch(window.get("resetsAt"))
        band = window.get("band")
        if not band:
            _, band = quota_bands.compute_band(used, reset, window.get("windowDurationMins", 0) * 60, now)
        if quota_bands.is_exhausted(used, quota.get("credits")):
            band = quota_bands.EXHAUSTED_BAND
        elif ((capacity or {}).get("status") == "dispatch-floor"
              and window.get("name") == quota.get("bindingWindow")):
            band = "Dispatch floor"
        if band is None:
            if used != 0 or reset is not None:
                continue
            band = "On pace"
        rows.append(dict(window, band=band, left=100 - used, _room=value,
                         timeLeft=100 if reset is None else min(100, max(0, (reset - now) /
                                  (window["windowDurationMins"] * 60) * 100))))
    rows.sort(key=lambda w: (-(len(quota_bands.BANDS) if w["band"] not in quota_bands.BAND_NAMES else
                              quota_bands.band_rank(w["band"])), w["_room"]))
    if rows and (capacity or {}).get("status") == "exhausted":
        rows[0]["band"] = quota_bands.EXHAUSTED_BAND
    if rows and len({w["band"] for w in rows}) == 1:
        rows = rows[:1]
    return rows


def window_name(window):
    """A window's display name, by its length: 30d, 7d, 5h."""
    mins = window.get("windowDurationMins") or 0
    if mins and mins % 1440 == 0:
        return "%dd" % (mins // 1440)
    if mins and mins % 60 == 0:
        return "%dh" % (mins // 60)
    return {"seven_day": "7d", "five_hour": "5h"}.get(window.get("name"), window.get("name", "window"))


def reset_text(reset, now=None):
    """HH:MMZ for a reset within a day; one further out carries its date (Oct 26 19:26Z)."""
    now = time.time() if now is None else now
    return time.strftime("%H:%MZ" if reset - now < 86400 else "%b %d %H:%MZ", time.gmtime(reset))


def reset_clause(window, now=None):
    """`resets <time>` for a Hot or Exhausted window with a known reset, else None."""
    reset = provider_capacity._epoch(window.get("resetsAt"))
    if window.get("band") not in ("Hot", "Dispatch floor", quota_bands.EXHAUSTED_BAND) or reset is None:
        return None
    return "resets " + reset_text(reset, now)


def window_figures(window, now=None):
    """One window's figures: `<name> <left>% left, <timeLeft>% to go`, plus its reset time when the band is
    Hot or Exhausted. `window` carries `left` and `timeLeft` as percents from 0 to 100, `band`, `resetsAt`
    and a name or `windowDurationMins`; a `display_windows` row has them all."""
    now = time.time() if now is None else now
    text = "%s %.0f%% left, %.0f%% to go" % (window_name(window), window["left"], window["timeLeft"])
    clause = reset_clause(window, now)
    return text + ", " + clause if clause else text


def plan_entry(plan, capacity, now=None, label=None):
    """`<plan> (<label>) <band>: <figures>` for a plan's capacity reading: the worst window's band, then each
    `display_windows` row's `window_figures` (`left` and `timeLeft` are percents from 0 to 100). A reading
    with no readable window is `band unknown`, naming the cold cache."""
    windows = display_windows(capacity, now)
    heading = plan + (" (%s)" % label if label else "")
    if (capacity or {}).get("status") == "dispatch-floor":
        return provider_capacity.floor_refusal(capacity, now) + (" (%s)" % label if label else "")
    if not windows:
        return heading + " band unknown (cache cold; preflight probes before spend)"
    return "%s %s: %s" % (heading, windows[0]["band"],
                           "; ".join(window_figures(w, now) for w in windows))


def _lead(row, capacity, now=None):
    return plan_entry(row["transport"], capacity, now, row.get("planType"))


def pick(tier, session_plan=None, data=None, read=None, now=None):
    """{transport, room, ranked, payOptions, reason}: which plan pays for a `tier` job.

    `tier` is a tier name or a list of them (their union: a job whose shape implies no tier is
    fit wherever its row's claimed tiers are served). Candidates are the live plans able to pay
    for an available row whose `roles` claim it, availability judged from `session_plan`'s route.
    `read(transport, data)` returns a normalized reading; the default is `read_capacity` (fresh
    cache, else live). `ranked` holds every fit plan, readable ones by `rank_room` descending (the returned `room` is the pick's `rank_room`; each ranked row's `room` stays its routing window's), then the
    dropped ones with `why`. The returned `transport` is the PAYING PLAN, which can differ from
    the row's transport; it is None when no fit plan has room, and `payOptions` then lists the
    fit marginal-usd rows as {alias, id, transport}, and is empty otherwise.
    """
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    read = read or read_capacity
    tiers = [tier] if isinstance(tier, str) else [t for t in (tier or []) if t]
    transports = data.get("transports") or {}
    seat = model_registry.route_of(session_plan, data) if session_plan in transports else None
    fit = []
    for name in tiers:
        fit += model_registry.rows_for_tier(name, data, seat=seat)
    plans, pay = [], []
    for row in fit:
        cost = (transports.get(row.get("transport")) or {}).get("costModel")
        if cost == "marginal-usd" and row not in pay:
            pay.append(row)
        if cost != "plan-quota":
            continue
        for plan in model_registry.plans_of(row["transport"], data):
            if (transports.get(plan) or {}).get("costModel") == "plan-quota" and plan not in plans:
                plans.append(plan)
    capacities = {}

    def captured_read(name, _data=None):
        capacities[name] = read(name, data)
        return capacities[name]

    rows = [_plan_room(name, data, captured_read, now) for name in sorted(plans)]
    ready = sorted((r for r in rows if r["room"] is not None),
                   key=lambda r: (-round(rank_room(r), 2), r["transport"] != session_plan,
                                  r["transport"]))
    dropped = [r for r in rows if r["room"] is None]
    top = ready[0] if ready else None
    dropped_text = "".join(" | " + (plan_entry(r["transport"], capacities.get(r["transport"]), now)
                                  if r["why"].startswith("exhausted") else
                                  "%s %s" % (r["transport"], r["why"])) for r in dropped)
    if top:
        reason = " | ".join([_lead(top, capacities.get(top["transport"]), now)] +
                            [_lead(r, capacities.get(r["transport"]), now) for r in ready[1:]])
        options = []
    else:
        options = [{"alias": r.get("alias"), "id": r.get("id"), "transport": r.get("transport")}
                   for r in pay]
        reason = "no fit plan has quota for %s" % "/".join(tiers)
        if options:
            reason += "; pay per token: " + ", ".join(o["alias"] or o["id"] for o in options)
    return {"transport": top["transport"] if top else None, "room": rank_room(top) if top else None,
            "ranked": ready + dropped, "payOptions": options, "reason": reason + dropped_text}


def registry_order(rows, data=None):
    """`rows` (plan rows with a `transport`) in registry order: a dispatch-facing list never ranks plans."""
    order = {name: i for i, name in enumerate(plan_quota_transports(data))}
    return sorted(rows, key=lambda r: order.get(r.get("transport"), len(order)))


def room_facts(got, read=None, now=None, data=None):
    """{plans, entries, low, payOptions} from a `pick` result: each plan's room facts and its owner-facing
    entry in registry order, with no choice among them. `low` is True when no plan paying for the tier's rows
    has allowance."""
    read = read or (lambda name: read_capacity(name, live=False))
    rows = registry_order(got.get("ranked") or [], data)
    plans = [{k: r.get(k) for k in ("transport", "room", "bindingRoom", "left", "daysLeft", "why") if k in r}
             for r in rows]
    entries = [plan_entry(r["transport"], read(r["transport"]), now, r.get("planType")) if r.get("room") is not None
               else "%s %s" % (r["transport"], r.get("why")) for r in rows]
    return {"plans": plans, "entries": entries, "low": not any(r.get("room") is not None for r in plans),
            "payOptions": got.get("payOptions") or []}


def _ladder_cells(column, data, ladder=None):
    """{registry id: the ladder's `column` cell} for every row a ladder row covers; {} when the ladder cannot be read."""
    try:
        with open(ladder or model_registry.ladder_path(), encoding="utf-8") as fh:
            rows = model_registry.parse_ladder_rows(fh, ("model", column))
    except OSError:
        return {}
    out = {}
    for row in rows:
        for m in model_registry.ladder_rows(row["model"], data):
            out.setdefault(m["id"], row[column.lower()])
    return out


def _owner_mark(row, tier):
    """The owner's probation text for `tier` on this row, or None: reported as a fact, never an exclusion."""
    for mark in row.get("probation") or []:
        if isinstance(mark, dict) and mark.get("tier") == tier:
            return mark.get("text")
    return None


def lean_candidates(tier, session_plan, data=None, read=None, now=None, ladder=None):
    """{tier, seat, routes, payOptions, refused}: every row claiming `tier` that the session can dispatch to, grouped by
    the route it pays on, each with its ladder cost, `±` cell, effort cell, owner probation mark and each plan's room.

    Facts only, in registry order and never sorted: a listing sorted by one axis reads as the pick. Plan-quota rows
    fill `routes`, the session's route first. A marginal-usd row is listed in `payOptions` only when no plan on any
    route has room. Each `routes` and `payOptions` entry carries `supplementary`: true when the row also holds the
    tier in registry `supplementary`, so its review seats need a qualified seat beside them. A row holding the tier
    only as `supplementary` is no claimant: it is listed apart, under `supplementary` by route, and only a plan-quota
    row, since it fills an added review seat beside a qualified one and never a lone seat or authoring. An unknown
    seat is refused, with no routes and no pay options, so it fails closed."""
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    read = read or read_capacity
    tier = model_registry.canonical_tier(tier)
    transports = data.get("transports") or {}
    out = {"tier": tier, "seat": None, "routes": {}, "supplementary": {}, "payOptions": [], "refused": None}
    if session_plan not in transports:
        out["refused"] = ("unknown seat: CLAUDE_CODE_TRANSPORT names no plan and CLAUDE_CONFIG_DIR matches no plan's "
                          "configDir, so this session's route is unreadable")
        return out
    seat = model_registry.route_of(session_plan, data)
    out["seat"] = seat
    costs = model_registry.ladder_costs(data, ladder)
    minus = _ladder_cells("±", data, ladder)
    effort = _ladder_cells("effort", data, ladder)
    plan_rooms = {}

    def plans_on(route):
        found = []
        for name in model_registry.plans_of(route, data):
            if (transports.get(name) or {}).get("costModel") != "plan-quota":
                continue
            if name not in plan_rooms:
                plan_rooms[name] = _plan_room(name, data, read, now)
            found.append({"plan": name, "room": rank_room(plan_rooms[name])})
        return found

    routes, only, plans_by_route, marginal, claimant_routes = {}, {}, {}, [], set()
    claimants = model_registry.rows_for_tier(tier, data, seat=seat)
    ids = {r["id"] for r in claimants}
    added = [r for r in model_registry.supplementary_rows(tier, data, seat=seat) if r["id"] not in ids]
    for row in claimants + added:
        cost_model = (transports.get(row.get("transport")) or {}).get("costModel")
        holds = tier in model_registry.supplementary_tiers(row)
        if cost_model == "marginal-usd":
            if row["id"] in ids:
                marginal.append(row)
            continue
        if cost_model != "plan-quota":
            continue
        route = row["transport"]
        if route not in plans_by_route:
            plans_by_route[route] = plans_on(route)
        if row["id"] in ids:
            claimant_routes.add(route)
        entry = {"alias": row.get("alias"), "id": row["id"], "cost": costs.get(row["id"]),
                 "plans": plans_by_route[route], "minus": minus.get(row["id"]),
                 "effort": effort.get(row["id"]), "probation": _owner_mark(row, tier), "supplementary": holds}
        (routes if row["id"] in ids else only).setdefault(route, []).append(entry)
    order = lambda found: {r: found[r] for r in sorted(found, key=lambda r: r != seat)}
    out["routes"], out["supplementary"] = order(routes), order(only)
    if not any(p["room"] is not None for r in claimant_routes for p in plans_by_route[r]):
        out["payOptions"] = [{"alias": r.get("alias"), "id": r.get("id"), "transport": r.get("transport"),
                              "supplementary": tier in model_registry.supplementary_tiers(r)} for r in marginal]
    return out


def session_plan():
    """This session's paying plan through `hooks/_session_transport.py`, or None when unreadable."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "hooks"))
        import _session_transport
        name = _session_transport.session_plan()
        return None if name == _session_transport.UNKNOWN else name
    except Exception:
        return None


def main(argv):
    if not argv or argv[0] == "list":
        for name in plan_quota_transports():
            got = reading(name) or {}
            print(f"{name}\t{got.get('band') or 'uncomputable'}\t{got.get('pressure')}"
                  f"\t{got.get('planType') or '?'}")
        return 0
    if argv[0] in ("pick", "room"):
        rest = [a for a in argv[1:] if a != "--json"]
        if len(rest) != 1 or (argv[0] == "pick" and "--json" not in argv):
            print(f"usage: provider_bands.py room <tier> [--json] | pick <tier> --json", file=sys.stderr)
            return 2
        try:
            tier = model_registry.canonical_tier(rest[0])
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        got = pick(tier, session_plan())
        if argv[0] == "pick":
            print(json.dumps(got, indent=2))
            return 0
        facts = room_facts(got)
        if "--json" in argv:
            print(json.dumps(dict(facts, tier=tier), indent=2))
        else:
            for line in facts["entries"]:
                print(line)
            if facts["low"]:
                print("low: no plan paying for a %s row has allowance left" % tier)
        return 0
    if argv[0] == "lean":
        rest = [a for a in argv[1:] if a != "--json"]
        if len(rest) != 1:
            print("usage: provider_bands.py lean <tier> [--json]", file=sys.stderr)
            return 2
        try:
            tier = model_registry.canonical_tier(rest[0])
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2
        got = lean_candidates(tier, session_plan())
        if got["refused"]:
            print(got["refused"], file=sys.stderr)
            return 2
        if "--json" in argv:
            print(json.dumps(got, indent=2))
            return 0
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        def show(groups):
            for route, rows in groups.items():
                print(route)
                for r in rows:
                    plans = ", ".join("%s room %s" % (p["plan"], "none" if p["room"] is None else "%.2f" % p["room"])
                                      for p in r["plans"])
                    print("  %s (cost %s) | plans: %s | effort: %s | ±: %s | probation: %s%s"
                          % (r["alias"] or r["id"], "?" if r["cost"] is None else r["cost"], plans,
                             r["effort"] or "no effort cell", r["minus"] or "no ± cell",
                             r["probation"] or "none",
                             " | supplementary: its review seats need a qualified seat beside them"
                             if r.get("supplementary") else ""))

        show(got["routes"])
        for option in got["payOptions"]:
            print("pay per token: %s" % (option["alias"] or option["id"]))
        if got["supplementary"]:
            print("added review seats only: never lone, never author")
            show(got["supplementary"])
        if not got["routes"] and not got["payOptions"] and not got["supplementary"]:
            print("no row claims %s" % tier)
        return 0
    if argv[0] == "warm":
        for name in argv[1:]:
            try:
                provider_capacity.reading(name)
            except Exception:
                continue
        return 0
    if argv[0] == "probe":
        if len(argv) != 2:
            print("usage: provider_bands.py probe <transport>", file=sys.stderr)
            return 2
        got = probe(argv[1])
        print(json.dumps(got, indent=2) if got else "null")
        return 0 if got else 1
    print(usage(__doc__), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
