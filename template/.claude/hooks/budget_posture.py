#!/usr/bin/env python3
"""UserPromptSubmit hook — budget-aware routing posture.

Bridges the Max plan's rate-limit telemetry (captured by ~/.claude/statusline.py
into its per-session state file) to the model-visible channel. Emits a posture
line on stdout (exit 0) telling the session model how delegation routing should
lean, keyed on BURN RATE against the reset clock, not raw utilization:

    elapsed  = 1 - (resets_at - now) / window_seconds
    pressure = used_pct / (elapsed * 100)     # >1 ahead of pace, <1 behind

The two windows govern different decisions and are never collapsed:
  seven_day  -> provider choice (Anthropic tier vs DeepSeek sidecar)
  five_hour  -> fan-out width (concurrent agents per dispatch)

Band table (7d pressure; SSOT for thresholds is BANDS below — this docstring
reproduces it so the mapping is checkable in one read):

    < 0.85     Surplus   nothing delegated by default — spend the plan; it expires
    0.85-1.15  On pace   Explore / read-heavy synthesis / doc write-ups
    1.15-1.5   Ahead     + scoped execution under a converged spec,
                          adversarial-review lenses at flash·max
    > 1.5      Hot       + deeper targeted exploration, architectural review passes

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

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

SEVEN_DAY_SECONDS = 7 * 24 * 3600
FIVE_HOUR_SECONDS = 5 * 3600
ELAPSED_FLOOR = 0.02          # first minutes after a reset cannot divide toward infinity
PRESSURE_DELTA = 0.15         # within-band re-emit threshold
TURNS_BETWEEN_EMITS = 10      # heartbeat re-emit even when nothing moved

# (upper_bound_exclusive, name, delegatable-to-sidecar description)
BANDS = [
    (0.85, "Surplus", "nothing by default - spend the plan; unused weekly capacity expires"),
    (1.15, "On pace", "Explore / read-heavy synthesis / doc write-ups"),
    (1.5, "Ahead", "+ scoped execution under a converged spec at flash low; execution on a looser spec at flash max;  adversarial-review lenses at flash max"),
    (float("inf"), "Hot", "+ deeper targeted exploration, planning and architectural review passes"),
]
NEVER = "never delegated: orchestration, ideal-design verdict, gate decisions, cross-system seams"


def band_for(pressure):
    for bound, name, desc in BANDS:
        if pressure < bound:
            return name, desc
    return BANDS[-1][1], BANDS[-1][2]


def pressure_for(window, window_seconds, now):
    """None when the window can't produce a trustworthy pressure."""
    if not isinstance(window, dict):
        return None
    used = window.get("used_percentage")
    resets = window.get("resets_at")
    if not isinstance(used, (int, float)) or not isinstance(resets, (int, float)):
        return None
    if resets < now:
        return None  # window already reset since capture — stale beyond usefulness
    elapsed = 1.0 - (resets - now) / window_seconds
    elapsed = max(ELAPSED_FLOOR, min(1.0, elapsed))  # clamp skew; emit rather than suppress
    return used / (elapsed * 100.0)


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

    Keeps ONE home for the band computation: callers must never re-derive pressure.

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
    if not state_file:
        return
    with open(state_file, encoding="utf-8") as fh:
        rl = (json.load(fh) or {}).get("rate_limits")
    if not isinstance(rl, dict):
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
        if p7 is not None:
            bits.append(f"7d pressure {p7:.2f} -> {band} band (sidecar-delegatable: {delegatable})")
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
    try:
        main()
    except Exception:
        pass  # fail open: advisory telemetry never blocks a prompt
    sys.exit(0)
