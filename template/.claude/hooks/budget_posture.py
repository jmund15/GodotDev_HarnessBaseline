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

Emission is deduped (band change, ±0.15 pressure crossing, or 10 turns) so an
unchanged posture is not re-injected every turn.
"""

import glob
import json
import os
import sys
import tempfile
import time

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
TURNS_BETWEEN_EMITS = 10      # heartbeat re-emit even when nothing moved

NEVER = "never delegated: orchestration, ideal-design verdict, gate decisions, cross-system seams"

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
_ROSTER = "roster: model_registry.py available (roles/effort/price)"
TIER_SPEND = "tier: executor at low - spend quota on judgment"
TIER_LEAN = (
    "tier: conserve - trade the Anthropic tier down first (converged-spec execution and closed "
    "lenses to the fan-out tier at medium); weigh an off-quota route above its usual bar for any "
    "dispatch whose ROLE a roster model claims. A plan-quota transport spends no dollars and its "
    "allowance expires unused too, so compare ITS band with this one; a dollar-billed transport "
    "is still a spend decision"
)
TIER_OFFQUOTA_FIRST = (
    "tier: off-quota FIRST - for every dispatch, if an available model claims that ROLE, route "
    "there; Anthropic quota is now the expensive currency. Prefer a plan-quota transport whose own "
    "band is slacker than this one - neither side is dollars, and both allowances expire unused. "
    "Trade the Anthropic tier down only for roles nothing in the roster claims"
)
TIER_NO_TRANSPORT = (
    "tier: conserve, no off-quota transport in the roster - converged-spec execution and closed "
    "lenses to the fan-out tier at medium"
)
# Band-independent and stated in both conserving lines because it is the one route that spends
# NEITHER currency. Legality is COPYABLE-vs-DERIVED, never budget pressure.
LOCAL = "copyable reads/synthesis/prose to the free local tier (spends neither currency)"


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

        return bool(model_registry.available_models())
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
        return (f"entrypoint '{ep}' sends no rate_limits in the statusline payload - "
                "unreadable by construction, not a broken writer")
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
    print(f"[budget-posture] band UNREADABLE - {reason}. Not a low band and not a defect: "
          "pick tier/effort on work shape (orchestration SKILL, Tier-within-quota); "
          "sidecar band gates will refuse and need -A.")
    try:
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump({"gap_notified": True, "turns_since_emit": 0}, fh)
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
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


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


def band_cli():
    """`--band`: print the current 7d band name for a caller with no session id.

    deepseek_sidecar.sh is a child Bash process — it has no Claude Code session id,
    and the dedupe record this hook writes is session-keyed AND written only on an
    emission turn. So the gate cannot read that record. It reads this instead, which
    reuses find_state()'s glob fallback over the cc-cachestat files statusline writes
    every turn. Rate limits are account-wide, so any session's snapshot is valid.

    Callers must never re-derive pressure; the one home for the computation is
    .claude/tools/quota_bands.py, which this module reads Claude's telemetry into.

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
    turns = dstate.get("turns_since_emit", TURNS_BETWEEN_EMITS) + 1

    band = None
    if p7 is not None:
        band, delegatable = band_for(p7)

    should_emit = turns >= TURNS_BETWEEN_EMITS
    if band is not None and dstate.get("band") != band:
        should_emit = True
    last_p7 = dstate.get("p7")
    if p7 is not None and isinstance(last_p7, (int, float)) and abs(p7 - last_p7) >= PRESSURE_DELTA:
        should_emit = True

    if should_emit:
        captured = rl.get("captured_at")
        age_min = (now - captured) / 60.0 if isinstance(captured, (int, float)) else None
        age_txt = f"capture {age_min:.0f}m old" if age_min is not None else "capture age unknown"
        if age_min is not None and age_min > 120:
            age_txt += " - treat as a hint, not a fact"
        bits = ["[budget-posture]"]
        # The band's delegatable list describes what the SIDECAR takes, so it is dropped when the
        # sidecar is not in the roster. Dropped, not annotated: an option that cannot be chosen costs
        # attention every turn it is explained, and the band itself is still true and still governs
        # fan-out width.
        # Resolved once and threaded into tier_for: it decides both whether the delegatable
        # set is worth printing and which conserving move is actually available.
        off_quota = sidecar_available()
        if p7 is not None and off_quota:
            bits.append(f"7d pressure {p7:.2f} -> {band} band (sidecar-delegatable: {delegatable})")
        elif p7 is not None:
            bits.append(f"7d pressure {p7:.2f} -> {band} band")
        # The tier default rides with the band in BOTH cases. It is the only thing the band
        # still governs once the sidecar leaves the roster, and it is what makes the band name
        # actionable without loading a skill.
        if band is not None:
            bits.append(tier_for(band, off_quota))
        if p5 is not None:
            bits.append(f"5h pressure {p5:.2f} (governs fan-out width; >1.3 means narrow concurrent dispatches)")
        bits.append(NEVER)
        bits.append(age_txt)
        print("; ".join(bits))
        dstate = {"band": band, "p7": p7, "turns_since_emit": 0}
    else:
        dstate["turns_since_emit"] = turns

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
