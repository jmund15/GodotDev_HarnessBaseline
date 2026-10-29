#!/usr/bin/env python3
"""Read every plan-quota transport's OWN band and room: the facts a dispatch weighs to choose who pays.

A plan's ROOM is the share of its routing window's allowance left over the share of that window's
time left: `(100 - usedPercent) / (100 x fraction of the window left)`. 1.0 is even pace, the rate
the plan can spend at until its reset. `pick` ranks a plan by its routing window's room (the fan-out's `pick_key` ranks first by the quota projected unused at the reset): that
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
a dispatch-facing caller reads `facts` (every plan in registry order, no choice): `room_facts`,
`hooks/workflow_provider_guard._cross_plan_route` and the `room` CLI.

FAIL POSTURE: every failure returns an empty/None reading. A budget advisory that raises
would block a dispatch over a hint, and a probe that spawns a subprocess has many ways to
fail that have nothing to do with the allowance it was asked about.

Provider-capacity execution, cache freshness and fallback live in `provider_capacity.py`.

Human-readable plan entries show quota left, time left, and for the routing window the share its recent
rate leaves unused at the reset (`unused_at_reset`), never a bare ratio.

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
try:
    import usage_samples  # noqa: E402
except Exception:  # no sample reader: every projection falls back to a bound or the average
    usage_samples = None
try:
    import transcript_usage  # noqa: E402
except Exception:  # no ledger reader: every job's drain is the default
    transcript_usage = None

DEFAULT_TTL_S = 600  # compatibility parameter; provider_capacity owns freshness
USED_RESOLUTION = 1.0  # percentage points: providers report usedPercent in whole percents
RECENT_HORIZON_S = 86400.0  # recent use is the plan's last day of samples, never its early burst
RECENT_MIN_SPAN_S = 3600.0  # two samples closer than this cannot state a rate
# A day of use, to the sampler's hour, holds its rhythm of work and sleep, so a rate over that span forecasts any
# time left; a rate over less forecasts no further than its span.
RATE_FULL_SPAN_S = RECENT_HORIZON_S - RECENT_MIN_SPAN_S
RESET_TOLERANCE_S = 900.0  # samples of one period agree on its reset to within this
SAMPLES_TTL_S = 30.0  # one prompt reads many plans, so the samples file is read once per turn
_SAMPLES_CACHE = {"at": None, "rows": []}
# What one sidecar job is assumed to take from one window of its plan, in percentage points, when no history
# measures it. Room is a pace ratio and says nothing about a burst: on 2026-10-14 a 5h window at 24% left read room
# 0.97, 13 jobs went to it, and 6+ hit the limit within 15 minutes (about 4 points a job). Thin basis: one burst.
JOB_DRAIN_PCT = float(os.environ.get("SIDECAR_JOB_DRAIN_PCT") or 4)
# A job's expected drain is its plan's recorded points per list-price dollar (`plan_rate`) times what finished jobs of
# its model, effort and shape cost at DRAIN_QUANTILE (`job_usd`). The 0.75 quantile held a job cluster's observed total in 53% to
# 83% of the clusters of the four plans backtested and reads high (figures: commit b78984a). Thin: one day of dense history.
DRAIN_QUANTILE = 0.75
# Fewer than five jobs make the 0.75 quantile their maximum, which is an anecdote; the group widens instead.
GROUP_MIN_JOBS = 5
# A rate from fewer reset periods is one burst's rate. Thin: the plans with the fewest each have four.
RATE_MIN_PERIODS = 3
_LEDGER_CACHE = {"at": None, "rows": []}
_drain_cache = {}

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


def _samples():
    """The plan-usage samples, read at most once per SAMPLES_TTL_S; [] when the reader cannot load."""
    now = time.time()
    if _SAMPLES_CACHE["at"] is not None and now - _SAMPLES_CACHE["at"] < SAMPLES_TTL_S:
        return _SAMPLES_CACHE["rows"]
    rows = []
    if usage_samples is not None:
        try:
            rows = usage_samples.read()
        except Exception:
            rows = []
    _SAMPLES_CACHE.update(at=now, rows=rows)
    return rows


def _recent_rate(transport, window, now, samples):
    """(rate in percentage points per second, span in seconds the samples cover) from the plan's own samples
    of `window` in the current reset period over the last day; None when the samples cannot state one.

    The rate is the least-squares slope of `used` over time. The reset filter already confines the
    samples to one period, so a dip is two writers rounding one plan apart, never a roll."""
    reset = provider_capacity._epoch(window.get("resetsAt"))
    plan_of = usage_samples.plan_of if usage_samples is not None else (
        lambda s: s.get("plan") or s.get("provider"))
    rows = []
    for s in samples or []:
        if not isinstance(s, dict) or (window.get("name") and s.get("window") != window.get("name")):
            continue
        if plan_of(s) != transport:
            continue
        ts, used = s.get("ts"), s.get("used")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        if not now - RECENT_HORIZON_S <= ts <= now + 300:
            continue
        seen_reset = provider_capacity._epoch(s.get("resets_at"))
        if reset is None or seen_reset is None or abs(seen_reset - reset) > RESET_TOLERANCE_S:
            continue
        rows.append((float(ts), float(used)))
    span = max(r[0] for r in rows) - min(r[0] for r in rows) if rows else 0.0
    if len(rows) < 2 or span < RECENT_MIN_SPAN_S:
        return None
    mean_ts = sum(r[0] for r in rows) / len(rows)
    mean_used = sum(r[1] for r in rows) / len(rows)
    spread = sum((r[0] - mean_ts) ** 2 for r in rows)
    return max(0.0, sum((r[0] - mean_ts) * (r[1] - mean_used) for r in rows) / spread), span


def _bound_rate(window, windows, now):
    """(rate, source window name) capped by the closest shorter window with an hour of elapsed time,
    or None. A shorter window's cap is never larger than a longer one's, so its used share over its
    elapsed time bounds how fast the longer window can be draining."""
    longer = window.get("windowDurationMins")
    if isinstance(longer, bool) or not isinstance(longer, (int, float)) or longer <= 0:
        return None
    best = None
    for other in windows or []:
        if not isinstance(other, dict) or other is window:
            continue
        minutes, used = other.get("windowDurationMins"), other.get("usedPercent")
        reset = provider_capacity._epoch(other.get("resetsAt"))
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 0 < minutes < longer:
            continue
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            continue
        if reset is None or reset <= now:
            continue
        elapsed = minutes * 60.0 - (reset - now)
        if elapsed < RECENT_MIN_SPAN_S:
            continue
        if best is None or minutes > best[0]:
            best = (minutes, used / elapsed, other)
    return None if best is None else (best[1], window_name(best[2]))


def unused_at_reset(transport, window, now=None, samples=None, windows=None):
    """{pct, basis, daysLeft} for `window`: the share of its allowance the plan's recent rate leaves
    at its reset, or None when the window cannot say (no used share, length or future reset).

    `basis` is the shortest honest one that applies. A rate needs a span of `RATE_FULL_SPAN_S` or the time left,
    whichever is shorter: with a shorter span the next basis is tried, and with the window's own elapsed time also
    shorter the answer is None. `recent`: the plan's own samples of this window,
    in its current reset period, over the last day (`RECENT_HORIZON_S`), at least two an hour apart;
    rate is their change in `used` over time. With no such samples, `average` is the window's own
    used share over its elapsed time, and `bound` replaces it when `_bound_rate`'s cap from the
    closest shorter window (`from` names it) is lower: a cap above the average says nothing. `pct` is `left - rate x time left`, clamped to the allowance left.
    `samples` defaults to `_samples()`; `windows` defaults to the plan's cached reading."""
    if not isinstance(window, dict):
        return None
    now = time.time() if now is None else now
    used = window.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return None
    reset = provider_capacity._epoch(window.get("resetsAt"))
    minutes = window.get("windowDurationMins")
    if reset is None or reset <= now:
        return None
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    if windows is None:
        try:
            windows = ((provider_capacity.cached(transport) or {}).get("quota") or {}).get("windows")
        except Exception:
            windows = None
    left = 100.0 - used
    time_left = reset - now
    if minutes * 60.0 - time_left < RECENT_MIN_SPAN_S:
        return None  # a window that just rolled says nothing about the rate it will spend at
    elapsed = minutes * 60.0 - time_left
    recent = _recent_rate(transport, window, now, _samples() if samples is None else samples)
    basis, source = "recent", None
    needed = min(RATE_FULL_SPAN_S, time_left)
    if recent is not None and recent[1] >= needed:
        rate = recent[0]
    elif elapsed < needed:
        return None
    else:
        rate, basis = used / elapsed, "average"
        bound = _bound_rate(window, windows, now)
        if bound is not None and bound[0] < rate:
            rate, source, basis = bound[0], bound[1], "bound"
    projection = {"pct": min(left, max(0.0, left - rate * time_left)), "basis": basis,
                  "daysLeft": time_left / 86400.0}
    if source:
        projection["from"] = source
    return projection


def _basis_text(projection):
    """The readout's name for a projection's basis."""
    if projection.get("basis") == "recent":
        return "recent use"
    if projection.get("basis") == "average":
        return "this window's average"
    return "this week's use" if projection.get("from") == "7d" else "this %s window's use" % projection.get("from")


def spare_pct(row_or_projection):
    """The whole-percent share of a plan's allowance projected unused at its reset, or None below
    `USED_RESOLUTION`. Takes a `unused_at_reset` projection or a row carrying one as `unusedAtReset`."""
    projection = row_or_projection
    if isinstance(projection, dict) and "unusedAtReset" in projection:
        projection = projection["unusedAtReset"]
    pct = projection.get("pct") if isinstance(projection, dict) else None
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or round(pct) < USED_RESOLUTION:
        return None
    return int(round(pct))


def _unused_clause(plan, capacity, window, now):
    """`, ~N% goes unused at reset (<basis>)` for the routing window, else ""."""
    quota = (capacity or {}).get("quota") or {}
    name = quota.get("routingWindow")
    if not name or window.get("name") != name:
        return ""
    projection = unused_at_reset(plan, window, now, windows=quota.get("windows") or [])
    pct = spare_pct(projection)
    if pct is None:
        return ""
    return ", ~%d%% goes unused at reset (%s)" % (pct, _basis_text(projection))


def read_capacity(transport, data=None, live=True):
    """A transport's normalized reading: the fresh cache first, else `provider_capacity.reading` when `live`
    (itself cache-first, one live read per transport at a time)."""
    got = provider_capacity.cached(transport, data=data)
    if got is None and live:
        got = provider_capacity.reading(transport, data=data)
    return got


def plan_room(transport, data, read, now):
    """{transport, room, window, left, headroom, headroomWindow, headroomResetsAt, windows, daysLeft, planType, unusedAtReset when it projects} for a plan with allowance left, else
    {transport, room: None, why}. `headroom` is the window with the fewest points left; `windows` lists every window's own
    {name, headroom, resetsAt}, because points of two windows are different units and a job's draw is priced per window."""
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
        cause = str((got.get("error") or {}).get("message") or "") if isinstance(got.get("error"), dict) else ""
        return {"transport": transport, "room": None,
                "why": "unreadable: status %s" % status + (": %s" % cause if cause else "")}
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
    def usable(w):
        # The launcher refuses a job once a window reaches its dispatch floor, so room ends there.
        floor = w.get("dispatchFloorPercent")
        return (floor if isinstance(floor, (int, float)) and not isinstance(floor, bool) else 100.0) - w["usedPercent"]

    tightest_window = min((w for w in windows
                           if isinstance(w.get("usedPercent"), (int, float)) and not isinstance(w["usedPercent"], bool)),
                          key=usable)
    row = {"transport": transport, "room": value, "window": name,
           "left": 100.0 - window["usedPercent"],
           "headroom": usable(tightest_window), "headroomWindow": tightest_window.get("name"),
           "headroomResetsAt": provider_capacity._epoch(tightest_window.get("resetsAt")),
           "windows": [{"name": w.get("name"), "headroom": usable(w),
                        "resetsAt": provider_capacity._epoch(w.get("resetsAt"))}
                       for w in windows
                       if isinstance(w.get("usedPercent"), (int, float)) and not isinstance(w["usedPercent"], bool)],
           "daysLeft": (reset - now) / 86400.0 if reset is not None else None,
           "planType": got.get("planType")}
    projection = unused_at_reset(transport, window, now, windows=windows)
    if projection is not None:
        row["unusedAtReset"] = projection
    gates = [(room(w, now), w.get("name")) for w in windows if w is not window and _gates(w, now)]
    tightest = min((g for g in gates if g[0] < value), default=None)
    if tightest:
        row["bindingRoom"], row["binding"] = tightest
    return row


def spent_reset(capacity):
    """Epoch of the latest reset among a reading's windows that are 100% used, else None: when a spent plan has
    room again."""
    windows = ((capacity or {}).get("quota") or {}).get("windows") or []
    resets = [provider_capacity._epoch(w.get("resetsAt")) for w in windows
              if isinstance(w, dict) and isinstance(w.get("usedPercent"), (int, float))
              and not isinstance(w["usedPercent"], bool) and w["usedPercent"] >= 100]
    return max((r for r in resets if r is not None), default=None)


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


def row_windows(row):
    """The `{name, headroom, resetsAt}` of each window a plan row can price a job on: its `windows`, else the one
    `headroom` it states, else none (the launcher's own gate decides)."""
    if isinstance(row.get("windows"), list):
        return row["windows"]
    headroom = row.get("headroom")
    if isinstance(headroom, bool) or not isinstance(headroom, (int, float)):
        return []
    return [{"name": row.get("headroomWindow"), "headroom": headroom, "resetsAt": row.get("headroomResetsAt")}]


def fits(row, committed_pct, drain_pct):
    """True while a window's `headroom` holds the `committed_pct` points of the jobs already on the plan and this
    job's `drain_pct`; a plan holds a job while each of its windows does (`row_windows`). A row with no `headroom` reading fits: its launcher's own gate decides."""
    headroom = row.get("headroom")
    return not isinstance(headroom, (int, float)) or headroom - committed_pct - drain_pct >= 0


def pick_key(row):
    """Sort key (best last) among the plans that hold the job (`fits`). A plan whose quota projects
    `USED_RESOLUTION` or more unused at its reset ranks above one that does not -- that quota is lost at the reset
    either way -- and those rank by the earliest reset; the rest keep `rank_room`. Percentages are never compared
    across plans, whose allowances differ."""
    if spare_pct(row) is not None:
        days = row["unusedAtReset"].get("daysLeft")
        return (1, 1, -days if isinstance(days, (int, float)) else float("-inf"))
    return (1, 0, rank_room(row))


def ledger_job(row):
    """A finished job from a spend-ledger row: {plan, start, end, usd, model, effort, shape, truncated}, else None
    for a row that is not plan-quota or carries no priced list cost. `usd` is list price, which carries each token
    type's relative cost and is never spend; `truncated` marks a job a usage limit cut short."""
    if not isinstance(row, dict) or row.get("costModel") != "plan-quota" or not isinstance(row.get("transport"), str):
        return None
    usd = row.get("costUSD")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd <= 0 or transcript_usage is None:
        return None
    end = transcript_usage.parse_ts(row.get("timestamp"))
    if end is None:
        return None
    duration = row.get("durationMs")
    span = duration / 1000.0 if isinstance(duration, (int, float)) and not isinstance(duration, bool) else 0.0
    model = next((row[k] for k in ("requestedModel", "servedModel") if isinstance(row.get(k), str)), None)
    return {"plan": row["transport"], "start": end - span, "end": end, "usd": float(usd), "model": model,
            "effort": row.get("effort"), "shape": (row.get("toolProfile") or {}).get("shape"),
            "truncated": row.get("stopReason") == "provider-usage-limit"}


def _ledger_jobs():
    """Every finished plan-quota job in the sidecar spend ledger, read at most once per SAMPLES_TTL_S; [] when the
    ledger cannot be read."""
    now = time.time()
    if _LEDGER_CACHE["at"] is not None and now - _LEDGER_CACHE["at"] < SAMPLES_TTL_S:
        return _LEDGER_CACHE["rows"]
    rows = []
    if transcript_usage is not None:
        try:
            records = transcript_usage.iter_records(transcript_usage.sidecar_ledger_path())
            rows = [job for job in map(ledger_job, records) if job]
        except Exception:
            rows = []
    _LEDGER_CACHE.update(at=now, rows=rows)
    return rows


def plan_rate(plan, window, jobs, samples):
    """(points per list-price dollar, reset periods used) of `plan`'s `window`: over each reset period of its
    samples that spans `RECENT_MIN_SPAN_S` and rose, the rise in `used` against the list cost of the plan's jobs that
    began and ended between the period's first and last sample. The rate is None below `RATE_MIN_PERIODS` periods.
    It reads high: a use of the plan with no ledger row (an interactive session) lands in the rise."""
    plan_of = usage_samples.plan_of if usage_samples is not None else (lambda s: s.get("plan") or s.get("provider"))
    points = sorted((s["ts"], s["used"], s["resets_at"]) for s in samples or []
                    if isinstance(s, dict) and s.get("window") == window and plan_of(s) == plan
                    and all(isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool)
                            for k in ("ts", "used", "resets_at")))
    periods = []
    for ts, used, reset in points:
        if periods and abs(periods[-1]["reset"] - reset) <= RESET_TOLERANCE_S:
            periods[-1]["points"].append((ts, used))
        else:
            periods.append({"reset": reset, "points": [(ts, used)]})
    mine = [j for j in jobs or [] if j["plan"] == plan]
    rise = cost = 0.0
    counted = 0
    for period in periods:
        (first, low), (last, high) = period["points"][0], period["points"][-1]
        usd = sum(j["usd"] for j in mine if j["start"] >= first and j["end"] <= last)
        if last - first >= RECENT_MIN_SPAN_S and high > low and usd > 0:
            rise, cost, counted = rise + (high - low), cost + usd, counted + 1
    return (rise / cost if counted >= RATE_MIN_PERIODS else None), counted


def job_usd(model, effort, shape, jobs):
    """The `DRAIN_QUANTILE` list-price cost of finished jobs of `model`, `effort` and `shape`: the narrowest group
    of at least `GROUP_MIN_JOBS` jobs, widening to the model and effort, then the model. None when no group is that
    large. A job a usage limit cut short is left out: its cost is the part it ran."""
    done = [j for j in jobs or [] if not j.get("truncated") and j.get("model") == model]
    for group in ([j for j in done if j.get("effort") == effort and j.get("shape") == shape],
                  [j for j in done if j.get("effort") == effort], done):
        if len(group) >= GROUP_MIN_JOBS:
            ordered = sorted(j["usd"] for j in group)
            return ordered[min(len(ordered) - 1, int(DRAIN_QUANTILE * len(ordered)))]
    return None


def reset_drain_cache():
    _drain_cache.clear()


def job_drain(plan, window, model, effort, shape, jobs=None, samples=None):
    """{pct, basis, usd, rate, why} for one job on `plan`: the percentage points it is expected to take from the
    plan's `window`. basis `measured` is `plan_rate` x `job_usd`. basis `default` is `JOB_DRAIN_PCT`, and `why`
    names the missing measure. `jobs` and `samples` default to the ledger and the samples file, each read once per
    SAMPLES_TTL_S; a plan's rate is computed once per reading of them."""
    explicit = jobs is not None or samples is not None
    jobs = _ledger_jobs() if jobs is None else jobs
    samples = _samples() if samples is None else samples
    if explicit:
        rate, counted = plan_rate(plan, window, jobs, samples)
    else:
        stamp = (_LEDGER_CACHE["at"], _SAMPLES_CACHE["at"])
        if _drain_cache.get("key") != stamp:
            _drain_cache.clear()
            _drain_cache["key"] = stamp
        if (plan, window) not in _drain_cache:
            _drain_cache[(plan, window)] = plan_rate(plan, window, jobs, samples)
        rate, counted = _drain_cache[(plan, window)]
    default = {"pct": JOB_DRAIN_PCT, "basis": "default", "usd": None, "rate": rate}
    if not window:
        return dict(default, why="no window to measure")
    if rate is None:
        return dict(default, why="%d reset period%s of %s samples with finished %s jobs, %d needed"
                    % (counted, "" if counted == 1 else "s", window, plan, RATE_MIN_PERIODS))
    usd = job_usd(model, effort, shape, jobs)
    if usd is None:
        return dict(default, why="fewer than %d finished jobs of %s" % (GROUP_MIN_JOBS, model))
    return {"pct": rate * usd, "basis": "measured", "usd": usd, "rate": rate, "why": None}


def plan_rows(data=None, read=None, now=None):
    """One `plan_room` row per plan-quota transport, in registry order: the owner-facing view."""
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    return [plan_room(name, data, read or read_capacity, now) for name in plan_quota_transports(data)]


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
    `display_windows` row's `window_figures` (`left` and `timeLeft` are percents from 0 to 100). The routing
    window's figures carry `_unused_clause` when its projection reaches `USED_RESOLUTION`. A reading with no
    readable window is `band unknown`, naming the cold cache."""
    windows = display_windows(capacity, now)
    heading = plan + (" (%s)" % label if label else "")
    if (capacity or {}).get("status") == "dispatch-floor":
        return provider_capacity.floor_refusal(capacity, now) + (" (%s)" % label if label else "")
    if not windows:
        return heading + " band unknown (cache cold; preflight probes before spend)"
    return "%s %s: %s" % (heading, windows[0]["band"],
                           "; ".join(window_figures(w, now) + _unused_clause(plan, capacity, w, now)
                                     for w in windows))


def _lead(row, capacity, now=None):
    return plan_entry(row["transport"], capacity, now, row.get("planType"))


def facts(tier, session_plan=None, data=None, read=None, now=None):
    """{tiers, plans, payOptions, capacities}: every plan that can pay for a `tier` job, in registry order, with its
    room facts and no choice among them. `tier` is a tier name or a list of them (their union: a job whose shape
    implies no tier is fit wherever its row's claimed tiers are served).

    Candidates are the live plans able to pay for an available row whose `roles` claim it, availability judged from
    `session_plan`'s route. `plans` holds each readable plan's `plan_room` row and each dropped one with `why`
    (`room` None). `payOptions` lists the fit marginal-usd rows as {alias, id, transport}, and only when no plan
    has room. `read(transport, data)` returns a normalized reading; the default is `read_capacity`.
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

    rows = registry_order([plan_room(name, data, captured_read, now) for name in plans], data)
    options = [] if any(r["room"] is not None for r in rows) else [
        {"alias": r.get("alias"), "id": r.get("id"), "transport": r.get("transport")} for r in pay]
    return {"tiers": tiers, "plans": rows, "payOptions": options, "capacities": capacities}


def pick(tier, session_plan=None, data=None, read=None, now=None):
    """{transport, room, ranked, payOptions, reason}: which plan pays for a `tier` job, the computed choice over `facts`.

    `ranked` holds every fit plan, readable ones by `rank_room` descending (the returned `room` is the pick's
    `rank_room`; each ranked row's `room` stays its routing window's), then the dropped ones with `why`. The returned
    `transport` is the PAYING PLAN, which can differ from the row's transport; it is None when no fit plan has room.
    """
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    got = facts(tier, session_plan, data=data, read=read, now=now)
    capacities = got["capacities"]
    ready = sorted((r for r in got["plans"] if r["room"] is not None),
                   key=lambda r: (-round(rank_room(r), 2), r["transport"] != session_plan, r["transport"]))
    dropped = [r for r in got["plans"] if r["room"] is None]
    top = ready[0] if ready else None
    dropped_text = "".join(" | " + (plan_entry(r["transport"], capacities.get(r["transport"]), now)
                                  if r["why"].startswith("exhausted") else
                                  "%s %s" % (r["transport"], r["why"])) for r in dropped)
    if top:
        reason = " | ".join([_lead(top, capacities.get(top["transport"]), now)] +
                            [_lead(r, capacities.get(r["transport"]), now) for r in ready[1:]])
    else:
        reason = "no fit plan has quota for %s" % "/".join(got["tiers"])
        if got["payOptions"]:
            reason += "; pay per token: " + ", ".join(o["alias"] or o["id"] for o in got["payOptions"])
    return {"transport": top["transport"] if top else None, "room": rank_room(top) if top else None,
            "ranked": ready + dropped, "payOptions": got["payOptions"], "reason": reason + dropped_text}


def registry_order(rows, data=None):
    """`rows` (plan rows with a `transport`) in registry order: a dispatch-facing list never ranks plans."""
    order = {name: i for i, name in enumerate(plan_quota_transports(data))}
    return sorted(rows, key=lambda r: order.get(r.get("transport"), len(order)))


def room_facts(got, read=None, now=None):
    """{plans, entries, low, payOptions} from a `facts` result: each plan's room facts and its owner-facing
    entry in registry order, with no choice among them. `low` is True when no plan paying for the tier's rows
    has allowance."""
    read = read or (lambda name: read_capacity(name, live=False))
    rows = got.get("plans") or []
    plans = [{k: r.get(k) for k in ("transport", "room", "bindingRoom", "left", "daysLeft", "why",
                                    "unusedAtReset") if k in r} for r in rows]
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
                plan_rooms[name] = plan_room(name, data, read, now)
            found.append({"plan": name, "room": rank_room(plan_rooms[name]),
                          "unusedAtReset": plan_rooms[name].get("unusedAtReset")})
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
        if argv[0] == "pick":
            print(json.dumps(pick(tier, session_plan()), indent=2))
            return 0
        view = room_facts(facts(tier, session_plan()))
        if "--json" in argv:
            print(json.dumps(dict(view, tier=tier), indent=2))
        else:
            for line in view["entries"]:
                print(line)
            if view["low"]:
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
