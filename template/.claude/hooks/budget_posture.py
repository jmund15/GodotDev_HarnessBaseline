#!/usr/bin/env python3
"""UserPromptSubmit hook — budget-aware routing posture.

Bridges the Max plan's rate-limit telemetry (captured by ~/.claude/statusline.py
into its per-session state file) to the model-visible channel. Emits a posture
line on stdout (exit 0) telling the session model how delegation routing should
lean, keyed on BURN RATE against the reset clock, not raw utilization.

The pressure formula and the band table are NOT defined here: they live in
.claude/tools/quota_bands.py, which is their SSOT, because a plan-quota provider's
own usage endpoint reduces through the same arithmetic and a second copy of the
thresholds would drift from the gate enforcing them. This module is one of two
CALLERS — Claude's own telemetry is its data source; codex_quota_probe.py is the other.

The two windows govern different decisions and are never collapsed:
  seven_day  -> provider choice (Anthropic tier vs paid/plan-quota sidecar)
  five_hour  -> fan-out width (concurrent agents per dispatch)

What each band opens to the sidecar lives in quota_bands.BANDS and nowhere else — an
earlier version of this docstring reproduced that column "so the mapping is checkable in
one read" and had drifted from BANDS by two clauses before anyone noticed.

Never delegated at ANY pressure: orchestration itself, the ideal-design verdict,
gate decisions, cross-system seams. Bands widen the delegatable set; they never
shrink the reserved one -- with ONE narrow exception, enforced rather than
advisory: agent-initiated SIDECAR dispatch of a model marked `gated` in
.claude/reference/external_models.json is refused below its gate.minBand
(deepseek_sidecar.sh exit 5, `-A` override). A Workflow fan-out is NOT gated;
launching a DeepSeek session is itself the authorization. Policy home:
skills/orchestration/SKILL.md section 5.

Fail open, always: missing file, absent rate_limits, malformed JSON, or a
resets_at in the past all exit 0 silently. Advisory telemetry must never block
a prompt. The `--band` CLI mode below is the one exception to fail-open: it is a
gate INPUT, so an unreadable band exits 3 rather than pretending to a value.

Emission is deduped: first turn of the session, first turn after a compaction, a band
change, or a ±0.15 pressure crossing. An unchanged posture is never re-injected.
"""

import glob
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import fire_once_since_compaction  # noqa: E402
import _session_transport  # noqa: E402 - HOST_TRANSPORT/UNKNOWN are read in main()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
# The formula and the band table, imported rather than restated. quota_bands imports nothing
# from this package, so this direction is safe where the model_registry import below is not.
import quota_bands  # noqa: E402
from quota_bands import BANDS, ELAPSED_FLOOR, band_for  # noqa: E402,F401 - re-exported

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

SEVEN_DAY_SECONDS = 7 * 24 * 3600
FIVE_HOUR_SECONDS = 5 * 3600
PRESSURE_DELTA = 0.15         # within-band re-emit threshold

# The reserved floor and the tier policy are constant, so the line points at their owner rather
# than restating them every prompt.
POLICY = "reserved floor + tier policy: orchestration §5/§5b"

# Tier-within-quota, emitted beside the band because a band name alone is inert: it says how
# much room is left, never what to spend it on. That gap is total while the sidecar is out of
# the roster -- provider choice is moot, so the tier is the ONLY thing the band still governs.
#
# Deliberately a separate map rather than a 4th BANDS field: model_registry unpacks BANDS as
# 3-tuples, and the sidecar-delegatable set and the tier default are different concerns that
# happen to share a key. Role names only, never models (orchestration SKILL names roles).
#
# This is the DEFAULT. The by-dispatch-shape refinement -- which lenses stay at the executor
# tier even under pressure -- is the orchestration SKILL's Tier-within-quota table, which is
# strictly more information rather than a second copy of this one.
# Conserving is GRADED, and it never names what an external model is good for.
#
# Graded, because the conserving moves cost DIFFERENT currencies and there are three, not two:
# Anthropic quota, a provider's own plan quota, and real dollars. Trading the Anthropic tier down
# spends quota already bought; a dollar-billed transport spends money; a plan-quota transport
# spends a second prepaid allowance that ALSO expires unused. That third case is a band-vs-band
# comparison rather than a spend decision -- route to whichever allowance is going to waste faster.
# It stays a COST tiebreaker: it ranks below intelligence and taste, so a slack provider band never
# promotes a model into work it cannot do.
#
# At Ahead the weekly window is not actually in danger, so a dollar move is a preference, not a
# default; at Hot Anthropic quota is the expensive currency and off-quota leads.
#
# Agnostic, because WHICH work belongs off-quota is a property of the roster, not of this file.
# Every registry row carries a `roles` array, so a model claiming a role IS the off-quota route
# for that role -- including roles this file would never have guessed. Naming work classes here
# ("send execution to the sidecar") freezes today's roster into doctrine and silently blocks a
# future model that is strong somewhere else. Read the roster instead:
#   .claude/tools/model_registry.py available   ->  roles, effort rungs, price
_ROSTER = "roster: model_registry.py available"
TIER_SPEND = (
    "tier: spend - converged-spec execution and closed lenses stay executor-tier at low; "
    "the rest keep their ladder rung"
)
TIER_LEAN = (
    "tier: conserve - converged-spec execution and closed lenses to fan-out tier at medium; "
    "favor off-quota for a role a roster model claims; weigh a plan-quota transport's band "
    "against this one (dollar billing stays a spend decision)"
)
TIER_OFFQUOTA_FIRST = (
    "tier: off-quota FIRST - route each role a roster model claims off-quota, preferring the "
    "slackest plan-quota band; trade the Anthropic tier down only for unclaimed roles"
)
TIER_NO_TRANSPORT = (
    "tier: conserve, no off-quota transport - converged-spec execution and closed lenses to "
    "fan-out tier at medium"
)
# Band-independent and stated in both conserving lines because it is the one route that spends
# NEITHER currency. Legality is COPYABLE-vs-DERIVED, never budget pressure.
LOCAL = "copyable I/O to the free local tier"


def tier_for(band, off_quota_available=None):
    """The band's default dispatch advice, graded by band and by what the roster actually holds.

    Never the whole answer: the by-dispatch-shape refinement is the orchestration SKILL's
    Tier-within-quota table, and what each available model is FOR is the registry's `roles`.
    """
    if band not in ("Ahead", "Hot"):
        return TIER_SPEND
    if off_quota_available is None:
        off_quota_available = sidecar_available()
    if not off_quota_available:
        return f"{TIER_NO_TRANSPORT}; {LOCAL}"
    lead = TIER_OFFQUOTA_FIRST if band == "Hot" else TIER_LEAN
    return f"{lead}; {LOCAL}; {_ROSTER}"


def sidecar_available():
    """Is ANY off-quota transport selectable? Unknown counts as available (fail open).

    Asks the roster rather than naming a transport. An earlier version checked `deepseek` by
    name, which was indistinguishable from the general question while deepseek was the only
    row -- and became a false negative the moment a second transport landed, in the one band
    where the answer changes what gets dispatched.

    Read from the registry rather than restated here, so the advisory cannot disagree with the
    gate that enforces it. The import is late and guarded because an advisory hook must fail
    OPEN: an unreadable registry should cost a routing hint, never the turn.
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        import model_registry  # noqa: WPS433 - deliberate late import, see above

        # OFF-QUOTA means "not this seat's own currency". The registry gained the four
        # `anthropic` host rows on 2026-09-04, so a bare `any available row` test became
        # unconditionally true and the no-transport branch below went dead.
        seat = seat_transport()
        return any(m.get("transport") != seat for m in model_registry.available_models())
    except Exception:
        return True


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
        band, pressure = provider_band(seat)
        if band:
            own = (f" {seat}'s OWN band is {band}"
                   + (f" (pressure {pressure:.2f})" if isinstance(pressure, (int, float)) else "")
                   + "; route on it.")
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


def pending_debt_count(session_id=None):
    """This session's unrated-dispatch count for the [rating-debt] clause, or -1 when unknown.
    `session_id` is the payload's — without it the metrics module falls back to an mtime scan
    that can pick a concurrent peer's session.

    HARNESS_PENDING_COUNT short-circuits the orchestration_metrics import -- a proof can drive N
    without a real session dir; unset in production. Import or call failure is silent: advisory
    telemetry must never block a prompt.
    """
    seam = os.environ.get("HARNESS_PENDING_COUNT")
    if seam is not None:
        try:
            return int(seam)
        except ValueError:
            return -1
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        from orchestration_metrics import pending_count

        return pending_count(session_id=session_id)
    except Exception:
        return -1


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


def provider_band(transport):
    """(band, pressure) from provider_capacity's fresh CACHE ONLY, or (None, None).

    The UserPromptSubmit hook has a five-second deadline, so it never starts a provider request.
    Live dispatch preflight owns authority; this advisory names a cold cache as unknown.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import provider_capacity
        r = provider_capacity.cached(transport) or {}
        quota = r.get("quota") or {}
        band = "Exhausted" if r.get("status") == "exhausted" else quota.get("routingBand")
        windows = quota.get("windows") or []
        routing = next((w for w in windows if w.get("name") == "seven_day"), None)
        binding = next((w for w in windows if w.get("name") == quota.get("bindingWindow")), None)
        return band, (routing or binding or {}).get("pressure")
    except Exception:
        return None, None


QUOTA_CLAUSE_CAP = 4


def other_quota_bits(seat):
    """One clause per OTHER plan-quota transport the roster knows, from cache only.

    A plan-quota transport a session can sidecar to is a currency it may spend, and naming only the
    seat and anthropic leaves a spent one invisible. Cache only, for the same 5s-budget reason
    `provider_band` is: this hook must never spawn a probe.

    A cold cache says `unknown` and NAMES the transport rather than omitting the clause — an absent
    clause reads as "no such currency", which is the failure mode this prevents.
    """
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import model_registry
        names = sorted(t for t in model_registry.transports()
                       if t not in (seat, "anthropic")
                       and model_registry.cost_model_for(t) == "plan-quota")
    except Exception:
        return []
    out = []
    for name in names[:QUOTA_CLAUSE_CAP]:
        band, _p = provider_band(name)
        out.append(f"{name} {band} band" if band else f"{name} band unknown (cache cold)")
    if len(names) > QUOTA_CLAUSE_CAP:
        out.append(f"+{len(names) - QUOTA_CLAUSE_CAP} more plan-quota transports")
    return out


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


def currency_bits(seat, p7, band, delegatable, off_quota):
    """(clauses, own_band) for a NON-host seat: its own currency first, the hop's second.

    A provider session's in-harness dispatches spend the PROVIDER's allowance; the Anthropic band
    the host path prints is the currency of a sidecar hop, not of anything this seat dispatches.
    Printing one number under one label is what makes a provider seat read the wrong budget.
    """
    # Named from the registry's `costModel`, never assumed to be quota: the shipped `opencode` seat
    # is `marginal-usd`, and telling it to "spend its allowance first, it expires" advises spending
    # dollars on the grounds that they would otherwise go to waste.
    spend = "its own quota"
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import model_registry            # late + guarded, as everywhere else in this hook
        if (model_registry.transport_meta(seat) or {}).get("costModel") == "marginal-usd":
            spend = "marginal dollars"
    except Exception:
        pass
    bits = [f"seat: {seat} (Workflow/Agent dispatch spends {spend})"]
    own_band, own_p = provider_band(seat)
    if own_band:
        # `pressure` is optional in a probe's reading; an unguarded format here raises into
        # main()'s blanket `except`, which deletes the entire posture line rather than one clause.
        shown = f"pressure {own_p:.2f} -> " if isinstance(own_p, (int, float)) else ""
        bits.append(f"{seat} {shown}{own_band} band (governs THIS seat)")
    else:
        bits.append(f"{seat} band unknown (cache cold; preflight probes before spend)")
    # The Anthropic side is a real currency here too -- it is what `anthropic_sidecar.sh` spends --
    # but it is the HOP's, so it is labelled rather than left to read as this seat's.
    if p7 is not None:
        hop = f"anthropic pressure {p7:.2f} -> {band} band (CROSS-HOP currency"
        hop += f"; sidecar: {delegatable})" if off_quota else ")"
        bits.append(hop)
    else:
        bits.append("anthropic band unknown (cache cold; preflight probes before a cross-hop)")
    return bits, own_band


def band_cli():
    """`--band`: print the current ANTHROPIC 7d band name for a caller with no session id.

    `--band --transport <name>` reads that transport through normalized live-first provider
    capacity. An unregistered name prints `unregistered`/4; an unavailable reading prints
    `unknown`/3. Bare `--band` keeps the local Anthropic telemetry compatibility contract.

    Callers must never re-derive pressure; `.claude/tools/quota_bands.py` owns the formula.

    Exit 0 with the band name on stdout; exit 3 printing `unknown` when no state is
    findable. Exit 3 is NOT a band — a gate treats it as not-satisfied, and says the
    band was unreadable rather than low.

    `--band --pressure` additionally prints the raw pressure as a second
    tab-separated field. Band NAMES are counterintuitive on their own: they are
    ordered by burn rate, so `Surplus` is the LOW-pressure band ("plan quota is
    going unused - spend it, don't delegate") and `Ahead` is a HIGH-pressure one
    ("quota is tight - delegating to paid transport is now the cheaper currency").
    A refusal message quoting only the name reads as though a low band meant
    plenty of room; quoting the number with it removes the ambiguity.
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

    state_file = find_state(None)
    if not state_file:
        print("unknown")
        return 3
    try:
        with open(state_file, encoding="utf-8") as fh:
            rl = (json.load(fh) or {}).get("rate_limits")
        p7 = pressure_for(rl.get("seven_day"), SEVEN_DAY_SECONDS, time.time())
    except Exception:
        p7 = None
    if p7 is None:
        print("unknown")
        return 3
    if "--pressure" in sys.argv[1:]:
        print(f"{band_for(p7)[0]}\t{p7:.2f}")
    else:
        print(band_for(p7)[0])
    return 0


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id", "unknown")

    state_file = find_state(session_id)
    rl = None
    if state_file:
        with open(state_file, encoding="utf-8") as fh:
            rl = (json.load(fh) or {}).get("rate_limits")
    if not isinstance(rl, dict):
        emit_gap_notice(session_id)
        return

    now = time.time()
    p7 = pressure_for(rl.get("seven_day"), SEVEN_DAY_SECONDS, now)
    p5 = pressure_for(rl.get("five_hour"), FIVE_HOUR_SECONDS, now)
    if p7 is None and p5 is None:
        return

    # Dedupe state — per session, same lifecycle as the capture files.
    dpath = dedupe_path(session_id)
    if not os.path.exists(dpath):
        prune_stale()
    try:
        with open(dpath, encoding="utf-8") as fh:
            dstate = json.load(fh)
    except Exception:
        dstate = {}
    band = None
    delegatable = None
    if p7 is not None:
        band, delegatable = band_for(p7)

    # Emit when the posture is NEW to the model: first time this session, again after a
    # compaction dropped it, and whenever the band or the pressure actually moved. A
    # turn-count heartbeat re-sent text the model still had.
    should_emit = fire_once_since_compaction(session_id, "budget_posture")
    if band is not None and dstate.get("band") != band:
        should_emit = True
    last_p7 = dstate.get("p7")
    if p7 is not None and isinstance(last_p7, (int, float)) and abs(p7 - last_p7) >= PRESSURE_DELTA:
        should_emit = True
    # On a provider seat the band that GOVERNS dispatch is the seat's own, and it was absent from
    # the dedupe key: the seat could cross from Surplus into Hot and this hook stayed silent
    # because the Anthropic hop band had not moved. Cache-read only, same as the emission path.
    seat = seat_transport()
    seat_band = None
    if seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN):
        seat_band = provider_band(seat)[0]
        if dstate.get("seatBand") != seat_band:
            should_emit = True
    # The OTHER spendable plan-quota transports belong in the key for the same reason seatBand
    # does: one of them can cross into Exhausted while the Anthropic hop has not moved, and then
    # nothing would say so -- which is exactly how a fully spent Codex account stayed invisible.
    quota = other_quota_bits(seat)
    if dstate.get("quota") != quota:
        should_emit = True

    if should_emit:
        captured = rl.get("captured_at")
        age_min = (now - captured) / 60.0 if isinstance(captured, (int, float)) else None
        age_txt = f"capture {age_min:.0f}m old" if age_min is not None else "capture age unknown"
        if age_min is not None and age_min > 120:
            age_txt += " (stale: a hint only)"
        bits = ["[budget-posture]"]
        # The band's delegatable list describes what the SIDECAR takes, so it is dropped when the
        # sidecar is not in the roster. Dropped, not annotated: an option that cannot be chosen costs
        # attention every turn it is explained, and the band itself is still true and still governs
        # fan-out width.
        # Resolved once and threaded into tier_for: it decides both whether the delegatable
        # set is worth printing and which conserving move is actually available.
        off_quota = sidecar_available()
        if p7 is not None and off_quota:
            bits.append(f"7d pressure {p7:.2f} -> {band} band (sidecar: {delegatable})")
        elif p7 is not None:
            bits.append(f"7d pressure {p7:.2f} -> {band} band")
        # The tier default rides with the band in BOTH cases. It is the only thing the band
        # still governs once the sidecar leaves the roster, and it is what makes the band name
        # actionable without loading a skill.
        if band is not None:
            bits.append(tier_for(band, off_quota))
        if p5 is not None:
            bits.append(f"5h pressure {p5:.2f} (>1.3: narrow fan-out)")
        # Every OTHER spendable plan-quota transport, on BOTH paths: a host session can sidecar to
        # Codex just as a Codex session can hop to Anthropic, and the incident was a spent account
        # no session could see. New information, not a second copy of a figure already printed.
        bits.extend(quota)
        # A NON-host seat gets both currencies, each labelled. This branch ADDS the seat's own
        # currency and re-labels the host's 7d clause; it does not change what the host path
        # already emitted, and the clause above is the only part both paths share.
        if seat not in (_session_transport.HOST_TRANSPORT, _session_transport.UNKNOWN):
            cur, own_band = currency_bits(seat, p7, band, delegatable, off_quota)
            # Drop the host 7d clause: `currency_bits` already printed that number, labelled as the
            # HOP's currency. Two clauses carrying one figure under two names is how a seat reads
            # the wrong budget -- the exact defect this slice removes.
            tail = [b for b in bits[1:] if not b.startswith("7d pressure")]
            # Tier follows the band that governs THIS seat's dispatches. Tiering off the Anthropic
            # band would advise spending an allowance a Workflow pin here cannot reach.
            if own_band:
                tail = [f"{tier_for(own_band, off_quota)} (from the {seat} band)"
                        if b.startswith("tier: ") else b for b in tail]
            else:
                # No band governs this seat, so no tier default does either. An Anthropic-derived
                # tier left in place reads as advice for a currency this seat cannot spend.
                tail = [b for b in tail if not b.startswith("tier: ")]
            # The 5h window is Anthropic's too. It still governs a HOP's width, so it stays --
            # labelled, which is the whole point of the per-currency split.
            tail = [f"anthropic {b} [cross-hop]" if b.startswith("5h pressure") else b
                    for b in tail]
            bits = ["[budget-posture]"] + cur + tail
        elif seat == _session_transport.UNKNOWN:
            bits.append("seat UNIDENTIFIED (the 7d band is Anthropic's; it may not be this "
                        "session's currency)")
        bits.append(POLICY)
        bits.append(age_txt)
        print(bits[0] + " " + "; ".join(bits[1:]))
        # keep `unrated`: the debt clause dedupes on it
        dstate.update({"band": band, "p7": p7, "seatBand": seat_band, "quota": quota})

    n = pending_debt_count(session_id)
    if n > 0 and n != dstate.get("unrated"):
        print(f"[rating-debt] unrated dispatches: {n} — record verdicts in "
              ".claude/orchestration_verdicts.json (/orchestration_metrics §Incremental rating)")
        dstate["unrated"] = n

    try:
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump(dstate, fh)
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
