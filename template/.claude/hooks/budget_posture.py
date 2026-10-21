#!/usr/bin/env python3
"""UserPromptSubmit hook — budget-aware routing posture.

Bridges the Max plan's rate-limit telemetry (captured by ~/.claude/statusline.py
into its per-session state file) to the model-visible channel. Emits a posture
line on stdout (exit 0) telling the session model how delegation routing should
lean, keyed on QUOTA RISK: burn rate against the reset clock, raised when what is
left will not survive to that reset. Raw utilization alone says neither.

The pressure formula and the band table are NOT defined here: they live in
.claude/tools/quota_bands.py, which is their SSOT, because a plan-quota provider's
own usage endpoint reduces through the same arithmetic and a second copy of the
thresholds would drift from the gate enforcing them. This module is one of its
CALLERS -- Claude's own telemetry is its data source; the provider probes are the
others.

The two windows govern different decisions and are never collapsed:
  seven_day  -> the routing band used by policy
  five_hour  -> the session's live native jobs: above quota_bands.FIVE_HOUR_NARROW_PRESSURE,
                workflow_provider_guard admits a dispatch only if the session then holds at most
                one live native job, counting the dispatch's own jobs

Each plan's room is a fact on the line, never a pick: which row and plan pay is the session's
choice (the model ladder's selection line). Plans are read from provider_capacity's cache only
(this hook never starts a probe itself). A plan with no fresh cached reading reads `unknown`; the
hook then starts one detached `provider_bands.py warm` for the cold plans, at most once per plan
per provider_capacity.CACHE_TTL_SECONDS, and prints `<transport> warming`.
HARNESS_CAPACITY_WARM=0 starts nothing (proofs set it). Each plan entry shows quota left and time
left for its worst-band window, and both windows when their bands differ.

Never delegated at ANY pressure: orchestration itself, the ideal-design verdict,
gate decisions, cross-system seams. No quota reading moves the reserved set.
One band gate is enforced rather than advisory: agent-initiated SIDECAR dispatch of a
model marked `gated` in .claude/reference/external_models.json is refused below its
gate.minBand (deepseek_sidecar.sh exit 5, `-A` override). Policy home:
skills/orchestration/SKILL.md section 5.

Fail open, always: missing file, absent rate_limits, malformed JSON, or a
resets_at in the past all exit 0 silently. Advisory telemetry must never block
a prompt. The `--band` CLI mode below is the one exception to fail-open: it is a
gate INPUT, so an unreadable band exits 3 rather than pretending to a value.

Delegates are silent. The first posture speaks at the session's first prompt, so the plan rooms reach the
session before its first dispatch; the hook reads no transcript to decide that. After that it is deduped: first turn after a compaction, a band
change, a ±0.15 pressure crossing, a window reaching its dispatch floor, or the free pool's ready count moving. An other-plan band
change on the prompt a plan goes cold re-emits nothing: a reading aged past the cache TTL reads
`unknown` until its warm lands. An unchanged posture is never re-injected. The floor belongs in that key because the reading that most
needs saying moves nothing else: 98.9% to 99% is the same band at the same pace.

Plan-usage sampling. After the posture step, on every prompt whether or not the line was deduped,
`sample_usage` appends both Anthropic windows of the capture ({ts: captured_at, provider, window,
used, resets_at}) through tools/usage_samples.py, which skips a capture older than 10 minutes or
no newer than the last sample. The samples file is HARNESS_USAGE_SAMPLES, else
~/.claude/usage_samples.jsonl; tools/plan_usage_calc.py reads it. Samples land only at prompts.
The sampler runs in its own try/except and prints nothing: a failure costs only the sample.

Spend line. The owner's lean condition (tools/spend_mode.py) prints on its own path, ahead of the rate_limits
early return: `spend: lean (owner, <scope>, until <time>)` while lean is set, `spend: lean expired <time>` once
after its expiry, and `spend: lean available — owner only` while the session's own plan is Hot or worse and spend
is normal. Its last value is the dedupe key `spend`, and the first prompt after a compaction re-injects an unchanged line once. Normal with a plan that is not Hot says nothing.
"""

import glob
import json
import math
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import fire_once_since_compaction, read_json_salvage, state_path, update_json_locked  # noqa: E402
import _session_transport  # noqa: E402 - HOST_TRANSPORT/UNKNOWN are read in main()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
# The formula and the band table, imported rather than restated. quota_bands imports nothing
# from this package, so this direction is safe where the model_registry import below is not.
import quota_bands  # noqa: E402
from quota_bands import BANDS, band_for  # noqa: E402,F401 - re-exported

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
# The per-window dispatch floor comes from the probe that enforces it: the warning below is about
# that probe's refusal, and a second copy of 98/99% would drift from the gate it describes. The
# import is guarded because an advisory must not cost the turn; an unreadable floor warns nothing.
try:
    from anthropic_capacity_probe import DISPATCH_FLOOR_PERCENT  # noqa: E402
except Exception:
    DISPATCH_FLOOR_PERCENT = None

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

SEVEN_DAY_SECONDS = 7 * 24 * 3600
FIVE_HOUR_SECONDS = quota_bands.FIVE_HOUR_SECONDS
PRESSURE_DELTA = 0.15         # within-band re-emit threshold
# The reserved floor and the tier policy are constant, so the line points at their owner rather
# than restating them every prompt.
POLICY = "rules: orchestration §5/§5b"
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


def cold_plans():
    """The plan-quota transports with no fresh cached reading, from provider_capacity's cache only."""
    try:
        sys.path.insert(0, TOOLS)
        import model_registry
        import provider_bands
        import provider_capacity
        data = model_registry.load()
        return sorted(t for t in provider_bands.plan_quota_transports(data)
                      if provider_capacity.cached(t, data=data) is None)
    except Exception:
        return []


def start_warm(transports):
    """Start one detached `provider_bands.py warm <transports>`; True when it started.

    HARNESS_CAPACITY_WARM=0 starts nothing. The warm process writes provider_capacity's cache,
    which the next prompt reads; this hook never waits on it.
    """
    if os.environ.get("HARNESS_CAPACITY_WARM") == "0" or not transports:
        return False
    try:
        from _bounded_run import detach_options, job_env
        subprocess.Popen([sys.executable, os.path.join(TOOLS, "provider_bands.py"), "warm", *transports],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, env=job_env(), **detach_options())
    except Exception:
        return False
    return True


def warming(cold, dstate, now):
    """The cold transports a warm is in flight for; starts one warm for those not warmed lately.

    A transport counts as warming for provider_capacity.CACHE_TTL_SECONDS after its warm started:
    a warm that fails (auth, network) writes no cache, and re-spawning it every prompt would
    probe on every turn.
    """
    try:
        sys.path.insert(0, TOOLS)
        import provider_capacity
        ttl = provider_capacity.CACHE_TTL_SECONDS
    except Exception:
        return []
    started = dict(dstate.get("warmAt") or {})
    due = [t for t in cold if now - float(started.get(t) or 0) > ttl]
    if due and start_warm(due):
        started.update({t: now for t in due})
    dstate["warmAt"] = {t: at for t, at in started.items() if now - float(at) <= ttl}
    return [t for t in cold if t in dstate["warmAt"]]


def pressure_for(window, window_seconds, now):
    """None when the window can't produce a trustworthy pressure.

    Adapter only: unwraps Claude's telemetry field NAMES and hands the numbers to the shared
    formula. Every provider spells the same three quantities differently, so the unwrapping
    stays with the caller that knows its own schema and the arithmetic stays in one place.
    """
    if not isinstance(window, dict):
        return None
    return quota_bands.pressure_for(
        window.get("used_percentage"), window.get("resets_at"), window_seconds, now)


def headroom_ratio(seven, now):
    """The weekly row's projected run-out ratio when it is a HEADROOM fact, else None.

    Only a ratio below 1 is headroom: at or above it the allowance outlasts its own reset, which
    raw pace has already said, so there is nothing for a routing comparison or a warning to add.
    `quota_bands.runout_ratio_for` owns the arithmetic and every numeric guard behind it.
    """
    ratio = quota_bands.runout_ratio_for(seven.get("used_percentage"), seven.get("resets_at"),
                                         SEVEN_DAY_SECONDS, now)
    if ratio is None or not math.isfinite(ratio) or ratio >= 1.0:
        return None
    return ratio


def dispatch_floor(rl, now):
    """`(window, used_percentage)` for a window at or past its dispatch floor, or None.

    The floor belongs to `anthropic_capacity_probe.DISPATCH_FLOOR_PERCENT`, whose live probe
    refuses dispatch at those numbers. This hook only says a capture has reached one: a capture
    cannot show whether extra credit is available, so availability stays the live probe's call.
    A window whose reset clock is unreadable is skipped -- a floor, like a band, is a statement
    about the window now current, and an expired capture makes none.
    """
    if not DISPATCH_FLOOR_PERCENT:
        return None
    for window, seconds in (("five_hour", FIVE_HOUR_SECONDS), ("seven_day", SEVEN_DAY_SECONDS)):
        floor = DISPATCH_FLOOR_PERCENT.get(window)
        row = rl.get(window) if isinstance(rl, dict) else None
        if floor is None or not isinstance(row, dict):
            continue
        used = row.get("used_percentage")
        if (not isinstance(used, (int, float)) or isinstance(used, bool)
                or quota_bands.pressure_for(used, row.get("resets_at"), seconds, now) is None):
            continue
        if used >= floor:
            return window, used
    return None


# Entrypoints whose statusline payload carries no `rate_limits` block at all. The band is
# unreadable there BY CONSTRUCTION, not broken: statusline.py still runs and still writes
# cc-cachestat every turn; only the field is absent. Confirmed 2026-08-23 for claude-desktop.
TELEMETRY_LESS_ENTRYPOINTS = ("claude-desktop",)


def telemetry_gap_reason():
    """One line naming why rate_limits is absent, or None when the cause is unknown.

    Separates "this entrypoint never sends it" from "the writer stopped". Different
    problems with different fixes — and the sidecar's refusal text names the wrong one
    (`restore the statusline`) whenever the writer is in fact healthy.
    """
    ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if ep in TELEMETRY_LESS_ENTRYPOINTS:
        return f"entrypoint '{ep}' sends no rate_limits by design, not a broken writer"
    # A provider seat launches with entrypoint 'cli', so it matched nothing above and the whole
    # posture line went silent -- on the one seat where the Anthropic band is not the governing one
    # anyway. Its own quota IS readable (`provider_bands.py`), so name the gap and hand over the
    # band that actually decides, rather than reporting the absent one.
    seat = seat_transport()
    if seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN):
        return f"{seat} seat sends no Anthropic rate_limits by design; not the governing band here"
    return None


def emit_gap_notice(session_id):
    """Say ONCE per session that the band is structurally unreadable.

    Silence is what costs: an orchestrator seeing no posture line cannot tell "no data"
    from "nothing changed", and CLAUDE.md makes reading the band mandatory before any
    fan-out. Deduped through the same per-session state file as the posture line.
    """
    reason = telemetry_gap_reason()
    if not reason:
        return
    dpath = dedupe_path(session_id)
    try:
        with open(dpath, encoding="utf-8") as fh:
            if json.load(fh).get("gap_notified"):
                return
    except Exception:
        pass
    # On a provider seat the seat's OWN band is the governing one, so report it instead of stopping
    # at "unreadable". Cache-read only: this hook runs on a 5s budget and `reading()` spawns a
    # provider CLI, which would kill the whole line (see `provider_band`).
    seat = seat_transport()
    own = ""
    if seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN):
        if provider_band(seat):
            own = " " + provider_entry(seat, label="this session") + "; route on it."
        else:
            own = (f" {seat}'s band is uncached; read it with "
                   f"`python3 .claude/tools/provider_bands.py list`, never call it unknown.")
    print(f"[budget-posture] Anthropic session telemetry UNREADABLE ({reason}).{own} "
          "Do not infer a band; dispatch preflight checks live capacity.")
    try:
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump({"gap_notified": True}, fh)
    except Exception:
        pass


def find_state(session_id):
    tmp = tempfile.gettempdir()
    if session_id:
        safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
        own = os.path.join(tmp, f"cc-cachestat-{safe}.json")
        if os.path.exists(own):
            return own
    candidates = glob.glob(os.path.join(tmp, "cc-cachestat-*.json"))
    # stat per candidate, not `max(key=getmtime)`: these files are written and reaped by other
    # sessions, so one can vanish between the glob and the stat. That raised out of an advisory
    # hook and cost the whole posture line over a file nobody needed.
    dated = []
    for c in candidates:
        try:
            dated.append((os.path.getmtime(c), c))
        except OSError:
            continue
    return max(dated)[1] if dated else None


def dedupe_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(tempfile.gettempdir(), f"cc-budgetposture-{safe or 'unknown'}.json")


def prune_stale(keep_days=7):
    """One dedupe file per session accumulates otherwise; swept on each
    session's first write, mirroring statusline.py's cc-cachestat sweep."""
    cutoff = time.time() - keep_days * 86400
    try:
        tmp = tempfile.gettempdir()
        for name in os.listdir(tmp):
            if not (name.startswith("cc-budgetposture-") and name.endswith(".json")):
                continue
            full = os.path.join(tmp, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass
    except Exception:
        pass


def seat_transport():
    """This session's transport, or UNKNOWN when it cannot be identified.

    UNKNOWN, never the host: `resolve()` itself already fails closed here, and converting a resolver
    crash into `anthropic` tells a provider seat its dispatches spend the host allowance. Main's
    UNKNOWN branch prints that the band shown may not be this session's currency, which is the
    honest answer to a question the hook could not resolve.
    """
    try:
        return _session_transport.resolve()[0]
    except Exception:
        return _session_transport.UNKNOWN


def seat_plan():
    """The plan paying for this session (`_session_transport.session_plan`), or UNKNOWN.

    `seat_transport` answers whether the capture's rate_limits are this session's own (any plan of
    the host route); this answers who pays, which the other-plan clauses key on.
    """
    try:
        return _session_transport.session_plan()
    except Exception:
        return _session_transport.UNKNOWN


def provider_band(transport):
    """The band from provider_capacity's fresh CACHE ONLY, or None.

    Model-facing plan entries use `provider_entry`, which prints the plan's windows, not this name.

    The UserPromptSubmit hook has a five-second deadline, so it never starts a provider request.
    Live dispatch preflight owns authority; this advisory names a cold cache as unknown.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import provider_capacity
        view = provider_capacity.routing_view(provider_capacity.cached(transport) or {})
        return view["band"] if view else None
    except Exception:
        return None


QUOTA_CLAUSE_CAP = 4


def binding_fingerprint(transport, band):
    """Legacy emission fingerprint only; never printed. Preserve the existing re-emit conditions."""
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import provider_capacity
        view = provider_capacity.routing_view(provider_capacity.cached(transport) or {}) or {}
        binding, routing = view.get("binding") or {}, view.get("routing") or {}
        if not binding.get("band") or binding.get("name") == routing.get("name"):
            return ""
        if quota_bands.band_rank(binding["band"]) <= quota_bands.band_rank(band):
            return ""
        reset = binding.get("resetsAt")
        when = time.strftime("%H:%MZ", time.gmtime(float(reset))) if reset else "unknown"
        return " (%s %s %d%%, resets %s)" % (binding.get("name"), binding["band"],
                                             round(float(binding.get("usedPercent") or 0)), when)
    except Exception:
        return ""


def other_quota_bits(plan):
    """One row per OTHER live plan-quota plan, from cache only: `{"name", "band", "fingerprint"}`, with
    `band` None on a cold cache. A list longer than `QUOTA_CLAUSE_CAP` ends in `{"more": <count>}`.
    `quota_key` is a row's emission key and `quota_clause` its printed clause.

    A plan a session can sidecar to is a currency it may spend, and naming only the session's own
    plan leaves a spent one invisible. The session's plan is left out; so is `anthropic` when the
    session's route is another transport, since that seat prints the capture as the hop's band. Cache only, for the same 5s-budget reason
    `provider_band` is: this hook must never spawn a probe.

    A cold cache says `unknown` and NAMES the transport rather than omitting the clause — an absent
    clause reads as "no such currency", which is the failure mode this prevents.

    A plan with room is named before one that is spent or unreadable. The cap is what keeps the line
    short, and a slot spent on a currency nobody can spend is exactly the trade the line must not
    make: with the Console plans in the roster, `go` -- warmed, with room, and the plan the router
    reaches for -- was the row that fell behind `+N more` while an Exhausted `codex` was named. The
    split is stable, so registry order still decides WITHIN each group; only the group moves.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import model_registry
        import provider_bands
        data = model_registry.load()
        route = model_registry.route_of(plan, data)
        skip = {plan} if route == _session_transport.HOST_TRANSPORT else {plan, _session_transport.HOST_TRANSPORT}
        names = [t for t in provider_bands.plan_quota_transports(data) if t not in skip]
    except Exception:
        return []
    rows = [{"name": name, "band": provider_band(name)} for name in names]
    rows.sort(key=lambda row: 0 if row["band"] and row["band"] not in
              (quota_bands.EXHAUSTED_BAND, "Dispatch floor") else 1)
    out = [{"name": row["name"], "band": row["band"],
            "fingerprint": binding_fingerprint(row["name"], row["band"]) if row["band"] else ""}
           for row in rows[:QUOTA_CLAUSE_CAP]]
    if len(names) > QUOTA_CLAUSE_CAP:
        out.append({"more": len(names) - QUOTA_CLAUSE_CAP})
    return out


def quota_key(row):
    """The row's emission-dedupe text. Its wording is the key a running session already saved."""
    if "more" in row:
        return f"+{row['more']} more plan-quota transports"
    if row["band"]:
        return f"{row['name']} {row['band']} band{row['fingerprint']}"
    return f"{row['name']} band unknown (cache cold; python3 .claude/tools/provider_bands.py reads and warms it)"


def quota_clause(row, warm, now):
    """The posture line's clause for one `other_quota_bits` row; `warm` names the plans a warm is running for."""
    if "more" in row:
        return quota_key(row)
    if row["band"] is None:
        return f"{row['name']} band unknown (cache cold)" + ("; warming" if row["name"] in warm else "")
    return provider_entry(row["name"], now=now)


def free_pool_text():
    """The `free pool: <ready>/<members> ready` clause (free_pool.ready_text), or None when the pool cannot be
    read: an advisory clause never costs the posture line."""
    try:
        sys.path.insert(0, TOOLS)
        import free_pool
        return free_pool.ready_text()
    except Exception:
        return None


def provider_band_live(transport):
    """(band, pressure) for a CLI caller, PROBING on a cache miss. Returns (None, None) on failure.

    The hook path must stay on `provider_band` (cache-only, 5s budget). This is the CLI twin: a
    command a session is told to run for its own band has to answer, and a cold cache is the normal
    state on a seat where nothing else probes. It also warms the cache for the next hook turn.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import provider_bands
        r = provider_bands.reading(transport) or {}
        return r.get("band"), r.get("pressure")
    except Exception:
        return None, None


def registered_transports():
    """Transport names the registry knows, or None when it cannot be read.

    None is not an empty set: an unreadable registry must not turn every name into a typo.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import model_registry
        return set((model_registry.load().get("transports") or {}))
    except Exception:
        return None


def provider_entry(plan, *, capacity=None, now=None, label=None):
    """`provider_bands.plan_entry` for `plan`, from `capacity` or else the plan's cached reading.

    The printed `left` and `timeLeft` figures are percents from 0 to 100.
    """
    import provider_bands
    import provider_capacity
    return provider_bands.plan_entry(plan, capacity if capacity is not None else
                                     provider_capacity.cached(plan), now, label)


def capture_capacity(rl, now):
    """A provider-capacity reading of the session's own capture: one window per readable `seven_day` and
    `five_hour` row, `usedPercent` the capture's used percent from 0 to 100."""
    windows = []
    for name, seconds in (("seven_day", SEVEN_DAY_SECONDS), ("five_hour", FIVE_HOUR_SECONDS)):
        row = rl.get(name) or {}
        _, band = quota_bands.compute_band(row.get("used_percentage"), row.get("resets_at"), seconds, now)
        if band:
            windows.append({"name": name, "usedPercent": row["used_percentage"],
                            "resetsAt": row["resets_at"], "windowDurationMins": seconds / 60,
                            "band": band})
    return {"quota": {"windows": windows, "routingWindow": "seven_day"}}



def band_cli():
    """`--band`: print the current ANTHROPIC 7d band name for a caller with no session id.

    `--band --transport <name>` reads that transport through normalized live-first provider
    capacity. An unregistered name prints `unregistered`/4; an unavailable reading prints
    `unknown`/3. Bare `--band` keeps the local Anthropic telemetry compatibility contract.

    Callers must never re-derive pressure; `.claude/tools/quota_bands.py` owns the formula.

    Exit 0 with the band name on stdout; exit 3 printing `unknown` when no state is
    findable. Exit 3 is NOT a band — a gate treats it as not-satisfied, and says the
    band was unreadable rather than low.

    `--band --session <id>` reads that session's own capture first and falls back to the
    newest one: the fallback lists all of TEMP, which on a crowded TEMP took past the provider
    guard's 10-second budget.

    `--band --pressure` additionally prints the raw pressure as a second
    tab-separated field. Band NAMES are counterintuitive on their own: they are
    ordered by quota risk, the pace band raised when the projected run-out falls
    before reset, so `Surplus` is the LOW-risk band (plan quota is going unused)
    and `Ahead` is a HIGH-risk one (the window is spending faster than it lasts). Which
    plan pays is weighed from room, never from the band name. The pressure field
    stays the raw pace, so a headroom-raised band can print beside a modest pace.
    A refusal message quoting only the name reads as though a low band meant
    plenty of room; quoting the number with it removes the ambiguity.

    `--used` after `--five-hour` adds a sixth field, the 5h window's used percent (0 to 100, one decimal),
    after a fifth that is empty unless the weekly run-out ratio is present. The pace formula stays with
    `quota_bands`; a caller that prints the window's quota left reads this field and never inverts the pace.

    `--band --pressure --five-hour` prints four tab-separated fields: band, 7d
    pressure, 5h pressure, 5h `resets_at` (epoch seconds), and appends a fifth
    carrying the weekly run-out ratio when it is headroom (below 1: the week will
    not last to its own reset). An unreadable 5h window leaves its two fields
    empty; an unreadable 7d window prints `unknown` with an empty pressure and still
    exits 3, with the 5h fields filled when they are readable. A 100%-used week
    prints `Exhausted` rather than the pace band its burn rate alone would grade.
    """
    # `--band --transport <name>` reads that transport's OWN band. Bare `--band` keeps its
    # contract exactly: sc_gate_band gates ANTHROPIC-currency spend and must keep reading the
    # Anthropic band, whatever seat the calling session runs on.
    argv = sys.argv[1:]
    if "--transport" in argv:
        i = argv.index("--transport")
        name = argv[i + 1] if i + 1 < len(argv) else ""
        if not name:
            print("unknown")
            return 3
        known = registered_transports()
        if known is not None and name not in known:
            # Distinct from `unknown`: a typo cannot be fixed by warming a cache, and a caller that
            # cannot tell them apart retries the wrong one.
            print("unregistered")
            return 4
        b, p = provider_band_live(name)
        if not b:
            print("unknown")
            return 3
        print(f"{b}\t{p:.2f}" if "--pressure" in argv and p is not None else b)
        return 0

    session = argv[argv.index("--session") + 1] if "--session" in argv[:-1] else None
    five = "--five-hour" in argv
    state_file = find_state(session)
    if not state_file:
        print("unknown\t\t\t" if five else "unknown")
        return 3
    now = time.time()
    rl = None
    try:
        with open(state_file, encoding="utf-8") as fh:
            rl = (json.load(fh) or {}).get("rate_limits")
    except Exception:
        rl = None
    seven = rl.get("seven_day") if isinstance(rl, dict) else None
    seven = seven if isinstance(seven, dict) else {}
    try:
        p7, band = quota_bands.compute_band(seven.get("used_percentage"), seven.get("resets_at"),
                                            SEVEN_DAY_SECONDS, now)
        ratio = headroom_ratio(seven, now)
    except Exception:
        p7, band, ratio = None, None, None
    if p7 is not None and quota_bands.is_exhausted(seven.get("used_percentage")):
        # A spent week grades mid-band late in its window, so the pace band reads as room left.
        # The gate is told Exhausted, which is what the injected line says too.
        band = quota_bands.EXHAUSTED_BAND
    tail = ""
    if five:
        window = rl.get("five_hour") if isinstance(rl, dict) else None
        try:
            p5 = pressure_for(window, FIVE_HOUR_SECONDS, now)
        except Exception:
            p5 = None
        reset = window.get("resets_at") if p5 is not None else None
        tail = f"\t{p5:.2f}\t{int(reset)}" if p5 is not None else "\t\t"
        # Field 5 is the SAME comparison input the guard needs, so a caller cannot be handed a
        # band raised by headroom with no way to weigh it. Absent when the week lasts to its reset,
        # which keeps the four-field shape every other consumer parses.
        if ratio is not None:
            tail += f"\t{ratio:.4f}"
        if "--used" in argv:
            used = float(window["used_percentage"]) if p5 is not None else None
            tail += ("\t" if ratio is None else "") + (f"\t{used:.1f}" if used is not None else "\t")
    if p7 is None:
        print("unknown\t" + tail if five else "unknown")
        return 3
    if "--pressure" in argv or five:
        print(f"{band}\t{p7:.2f}{tail}")
    else:
        print(band)
    return 0


USAGE_WINDOWS = ("five_hour", "seven_day")


def sample_usage(rl, now):
    """Append this capture's Anthropic windows to the plan-usage samples (tools/usage_samples.py)."""
    import usage_samples  # noqa: WPS433 - late, so a broken sampler costs only the sample

    samples = []
    for window in USAGE_WINDOWS:
        w = rl.get(window)
        if isinstance(w, dict) and w.get("used_percentage") is not None:
            samples.append({"ts": rl.get("captured_at"), "provider": "anthropic", "window": window,
                            "used": w.get("used_percentage"), "resets_at": w.get("resets_at")})
    usage_samples.record(samples, now)


def _scan_dispatch(path):
    import sidecar_argv
    with open(path, encoding='utf-8') as fh:
        for raw in fh:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if row.get('type') != 'assistant':
                continue
            content = (row.get('message') or {}).get('content') or []
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get('type') != 'tool_use':
                    continue
                name = item.get('name') or ''
                if name in ('Agent', 'Workflow') or name.endswith('__Workflow'):
                    return True
                if name in ('Bash', 'PowerShell') and sidecar_argv.launcher_invocations(
                        (item.get('input') or {}).get('command') or ''):
                    return True
    return False


def dispatch_seen(payload):
    """Cache actual dispatch sightings, including misses, until the transcript changes."""
    try:
        path = os.path.abspath(payload.get('transcript_path') or '')
        stat = os.stat(path)
        fingerprint = [path, stat.st_size, stat.st_mtime_ns]
        cache_path = state_path(payload.get('session_id') or '')
        cached = read_json_salvage(cache_path).get('budget_dispatch_cache')
        if (isinstance(cached, dict) and cached.get('fingerprint') == fingerprint
                and isinstance(cached.get('seen'), bool)):
            return cached['seen']
        seen = _scan_dispatch(path)
        update_json_locked(cache_path, lambda state: state.update(
            budget_dispatch_cache={'fingerprint': fingerprint, 'seen': seen}))
        return seen
    except (OSError, TypeError, ValueError):
        return False


def spend_posture(session_id):
    """This session's spend posture (`spend_mode.current`). Normal when it cannot be read: a posture that
    cannot be read is never lean."""
    try:
        import spend_mode
        return spend_mode.current(session_id)
    except Exception:
        return {"mode": "normal", "scope": None, "until": None, "expired": False}


def spend_line(posture, own_band):
    """The owner's spend line for a posture, or None when it says nothing. `own_band` is the session's own plan band."""
    if posture.get("mode") == "lean":
        return ("spend: lean (owner, %s, until %s) — pin rule: orchestration reference spend_lean.md; "
                "rows: provider_bands.py lean <tier>" % (posture.get("scope"), posture.get("until")))
    if posture.get("expired"):
        return "spend: lean expired %s" % posture.get("until")
    if own_band in (quota_bands.EXHAUSTED_BAND, "Dispatch floor", "Hot"):
        return "spend: lean available — owner only"
    return None


def emit_spend(session_id, dstate, dpath, own_band):
    """Print the spend line when it changed since the last prompt or the session compacted. Its last value is
    `dstate["spend"]`, so a set, an expiry or a band change re-injects once, with or without rate_limits telemetry;
    a compaction dropped the line from context, so the first prompt after one re-injects it unchanged."""
    line = spend_line(spend_posture(session_id), own_band)
    previous = dstate.get("spend")
    compacted = fire_once_since_compaction(session_id, "budget_posture_spend")
    if previous == line and not (line and compacted):
        return
    dstate["spend"] = line
    if str(previous or "").startswith("spend: lean (") and not str(line or "").startswith("spend: lean"):
        # The lean line already in context would keep steering pins; a clear says so once.
        print("[budget-posture] spend: normal (lean cleared)")

    def updater(state):
        state["spend"] = line

    update_json_locked(dpath, updater)
    if line:
        print("[budget-posture] " + line)


def main():
    payload = json.load(sys.stdin)
    if payload.get('agent_id') or os.environ.get('CLAUDE_CODE_ENTRYPOINT') == 'sdk-cli':
        return
    session_id = payload.get("session_id", "unknown")

    # Dedupe state — per session, same lifecycle as the capture files.
    dpath = dedupe_path(session_id)
    if not os.path.exists(dpath):
        prune_stale()
    try:
        with open(dpath, encoding="utf-8") as fh:
            dstate = json.load(fh)
    except Exception:
        dstate = {}

    state_file = find_state(session_id)
    rl = None
    if state_file:
        with open(state_file, encoding="utf-8") as fh:
            rl = (json.load(fh) or {}).get("rate_limits")
    has_rl = isinstance(rl, dict)
    now = time.time()
    seven = rl.get("seven_day") if has_rl and isinstance(rl.get("seven_day"), dict) else {}
    # The band comes from the shared risk reduction, not from the pace alone: a week whose
    # projected run-out will not reach its reset is the case the pace number understates.
    p7, band = (quota_bands.compute_band(seven.get("used_percentage"), seven.get("resets_at"),
                                         SEVEN_DAY_SECONDS, now) if has_rl else (None, None))
    # A spent allowance grades mid-band late in its window, so pressure alone would print a pace
    # band while every native dispatch is refused. Exhaustion overrides the band, as it already
    # does for every other transport's clause -- but only from a weekly row whose reset clock is
    # readable, because an expired capture cannot prove that anything is spent now.
    exhausted = band is not None and quota_bands.is_exhausted(seven.get("used_percentage"))
    if exhausted:
        band = quota_bands.EXHAUSTED_BAND
    # The spend line speaks on its own path, ahead of the rate_limits early return: its "available"
    # offer reads the session's own band, which on a provider seat is the seat's and elsewhere the capture's.
    seat = seat_transport()
    on_seat = seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN)
    seat_band = provider_band(seat) if on_seat else None
    emit_spend(session_id, dstate, dpath, seat_band if on_seat else band)
    if not has_rl:
        if dispatch_seen(payload):
            emit_gap_notice(session_id)
        return

    p5 = pressure_for(rl.get("five_hour"), FIVE_HOUR_SECONDS, now)
    if p7 is None and p5 is None:
        return
    floor = dispatch_floor(rl, now)

    # Emit when the posture is NEW to the model: first time this session, again after a
    # compaction dropped it, and whenever the band or the pressure actually moved. A
    # turn-count heartbeat re-sent text the model still had.
    should_emit = fire_once_since_compaction(session_id, "budget_posture")
    if band is not None and dstate.get("band") != band:
        should_emit = True
    last_p7 = dstate.get("p7")
    if p7 is not None and isinstance(last_p7, (int, float)) and abs(p7 - last_p7) >= PRESSURE_DELTA:
        should_emit = True
    # A window reaching its dispatch floor is its own reason to speak: 98.9% -> 99% moves neither
    # band nor pace by a printable amount, and it is the reading a session about to launch work on
    # that currency has to see.
    if dstate.get("floor") != (floor[0] if floor else None):
        should_emit = True
    # On a provider seat the band that GOVERNS dispatch is the seat's own, and it was absent from
    # the dedupe key: the seat could cross from Surplus into Hot and this hook stayed silent
    # because the Anthropic hop band had not moved. Cache-read only, same as the emission path.
    if on_seat and dstate.get("seatBand") != seat_band:
        should_emit = True
    # The OTHER spendable plan-quota transports belong in the key for the same reason seatBand
    # does: one of them can cross into Exhausted while the Anthropic hop has not moved, and then
    # nothing would say so -- which is exactly how a fully spent Codex account stayed invisible.
    plan = seat_plan()
    quota_rows = other_quota_bits(plan)
    quota = [quota_key(row) for row in quota_rows]
    # A plan whose cached reading just aged out reads `unknown` until its warm lands; that moves the
    # key and says nothing new.
    cold = cold_plans()
    went_cold = set(cold) - set(dstate.get("cold") or [])
    dstate["cold"] = cold
    if not went_cold and dstate.get("quota") != quota:
        should_emit = True
    # The free pool's ready count is in the key too: a member's cooldown ends on the clock, and nothing
    # else in this hook would say the pool grew back.
    pool = free_pool_text()
    if dstate.get("pool") != pool:
        should_emit = True
    warm = warming(cold, dstate, now)

    if should_emit:
        captured = rl.get("captured_at")
        age_min = (now - captured) / 60.0 if isinstance(captured, (int, float)) else None
        age_txt = f"capture {age_min:.0f}m old" if age_min is not None else "capture age unknown"
        if age_min is not None and age_min > 120:
            age_txt += " (stale: a hint only)"
        own_capacity = capture_capacity(rl, now)
        on_provider_seat = seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN)
        if on_provider_seat:
            bits = [provider_entry(plan, now=now, label="this session"),
                    provider_entry("anthropic", capacity=own_capacity, now=now, label="hop")]
        else:
            host_plan = plan if plan != _session_transport.UNKNOWN else "anthropic"
            bits = [provider_entry(host_plan, capacity=own_capacity, now=now, label="this session")]
        bits += [quota_clause(row, warm, now) for row in quota_rows]
        if pool:
            bits.append(pool)
        if on_provider_seat:
            own_blocked = seat_band in (quota_bands.EXHAUSTED_BAND, "Dispatch floor")
        else:
            import provider_bands
            own_windows = provider_bands.display_windows(own_capacity, now)
            own_blocked = bool(own_windows) and own_windows[0]["band"] == quota_bands.EXHAUSTED_BAND
        if own_blocked:
            bits.append("native Workflow/Agent dispatch is blocked until the reset; route each job to "
                        "another plan serving its tier (orchestration §5b)")
        if p5 is not None and p5 > quota_bands.FIVE_HOUR_NARROW_PRESSURE:
            trigger = ("5h used faster than its time: one native job at a time; the next waits for it or goes to "
                       "a row on another plan (orchestration §5b)")
            if on_provider_seat:
                trigger += " [cross-hop]"
            bits.append(trigger)
        if _session_transport.UNKNOWN in (seat, plan):
            bits.append("seat UNIDENTIFIED (the Anthropic band may not be this session's currency)")
        # A capture can reach a hard dispatch floor while the risk band still reads as spendable,
        # and a capture cannot say whether extra credit would cover the job. An exhausted week
        # already says more than this, so the warning fires only where nothing else does;
        # availability stays the live probe's call.
        if floor and not exhausted:
            bits.append(f"preflight: anthropic {floor[0]} at the "
                        f"{DISPATCH_FLOOR_PERCENT[floor[0]]:g}% dispatch floor ({floor[1]:.1f}% used); "
                        "live capacity preflight decides - the capture cannot prove extra-credit "
                        "availability")
        bits.append(POLICY)
        bits.append(age_txt)
        print("[budget-posture] " + " | ".join(bits))
        dstate.update({"band": band, "p7": p7, "seatBand": seat_band, "quota": quota, "pool": pool,
                       "floor": floor[0] if floor else None})

    try:
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump(dstate, fh)
    except Exception:
        pass

    try:
        sample_usage(rl, now)
    except Exception:
        pass


if __name__ == "__main__":
    # --band branches BEFORE the fail-open wrapper on purpose: that wrapper
    # collapses every outcome to exit 0, which would erase the exit-3 "band
    # unreadable" signal the sidecar gate depends on. It also reads no stdin.
    if "--band" in sys.argv[1:]:
        sys.exit(band_cli())
    # `--why`: one line explaining an unreadable band, for a caller that already got
    # exit 3 from --band. Exit 1 when the cause is not one this file can name, so a
    # consumer can tell "no explanation" from an empty explanation.
    if "--why" in sys.argv[1:]:
        reason = telemetry_gap_reason()
        if not reason:
            sys.exit(1)
        print(reason)
        sys.exit(0)
    try:
        main()
    except Exception:
        pass  # fail open: advisory telemetry never blocks a prompt
    sys.exit(0)
