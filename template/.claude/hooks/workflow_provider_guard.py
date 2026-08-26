#!/usr/bin/env python3
"""PreToolUse(Workflow): advise on PROVIDER choice when the band says spend is delegatable.

WHY THIS EXISTS: `Workflow` dispatches only on the session's own endpoint, so it is
always the Anthropic transport. Command prose that keys dispatch on LENS COUNT
("3+ lenses -> the engine") reads as a transport rule, and the whole fan-out then
bills to plan quota in a band where the sidecar was the cheaper currency. Nothing
downstream detects it: the dossier is identical either way. This fires at call time,
where the decision is still reversible.

CHANNEL: hookSpecificOutput.additionalContext on stdout (PreToolUse convention shared
with model_pin_translate.py).

FAIL POSTURE: fail-open and silent. A budget advisory must never block a dispatch, so
every failure path exits 0 with no output. The band is read from budget_posture.py
--band rather than recomputed here; the thresholds themselves live in
.claude/tools/quota_bands.py, which that flag reduces Claude's own telemetry through.

STATE: none of its own. Each Workflow dispatch spends independently, so each one is worth
a nudge; there is nothing to dedupe across. The provider-band readings this hook cites are
cached inside provider_bands.py, whose TTL keeps a multi-agent dispatch from re-spawning a
provider CLI once per agent.

ROLE-LADDER INJECTION: every Workflow call (all bands) also receives the role + effort cell
of each row in reference/model_ladder_evidence.md's ladder table, read live at fire time.
The rule this enforces lives in orchestration §5 ("load the ladder whenever you pin");
measured 2026-08-23: a pin made off the registry's coarse `roles` roster mis-declared a
spec-tight executor as unavailable. Injecting the SSOT rows at the decision moment is the
fix — nothing is restated, so nothing drifts.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
try:
    import provider_bands
except Exception:  # advisory only; the band nudge still stands without the comparison
    provider_bands = None

# Fan-out engines where provider choice is load-bearing. Other workflows still get a
# nudge (they spend too), but these name the roster columns the caller should re-read.
FANOUT_ENGINES = ("explore_fanout", "review_fanout", "dispatch", "doc_architecture_audit")


def band_and_pressure():
    """Return (band, pressure) for THIS session's own quota, or (None, None).

    Resolved against THIS file rather than $CLAUDE_PROJECT_DIR: the env var can
    arrive as an MSYS path (/c/Users/...) that a native Windows python3 cannot
    open, which would silently disable the advisory.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget_posture.py")
    out = subprocess.run(
        [sys.executable, script, "--band", "--pressure"],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0:
        return None, None
    parts = out.stdout.strip().split("\t")
    return (parts[0] or None), (parts[1] if len(parts) > 1 else None)


def _slacker_transports(band):
    if provider_bands is None:
        return []
    try:
        return provider_bands.slacker_than(band)
    except Exception:
        return []


def ladder_role_lines():
    """Role + effort cell per ladder-table row, read live from the SSOT (empty on any failure)."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reference", "model_ladder_evidence.md",
    )
    lines = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                if not raw.startswith("| "):
                    continue
                cells = [c.strip() for c in raw.strip().strip("|").split("|")]
                # header / separator rows have no role prose worth injecting
                if len(cells) < 4 or set(cells[0]) <= {"-", " ", ":"} or cells[0].lower() in ("model", "row"):
                    continue
                lines.append(f"{cells[0]}: {cells[1]} (effort: {cells[-2][:60]})")
    except Exception:
        return []
    return lines


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Workflow":
        return

    roles = ladder_role_lines()
    role_note = (
        ["[role ladder — pin against THESE rows, not the registry roster (orchestration §5)] "
         + " ;; ".join(roles)]
        if roles else []
    )

    band, pressure = band_and_pressure()
    # Surplus means plan quota is going unused — the engine IS the right call there,
    # but the role rows still govern WHICH tier the pin lands on.
    if not band or band == "Surplus":
        if role_note:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "[provider check] " + " | ".join(role_note),
                }
            }))
        return

    tool_input = payload.get("tool_input") or {}
    target = str(tool_input.get("scriptPath") or tool_input.get("name") or "")
    is_fanout = any(e in target for e in FANOUT_ENGINES)

    note = [
        f"band={band}" + (f" (pressure {pressure})" if pressure else ""),
        "Workflow is the ANTHROPIC transport — it cannot reach the sidecar at any agent count.",
        "Provider is chosen by BAND, never by agent/lens count.",
    ]
    if is_fanout:
        note.append(
            "This is a fan-out engine: re-read the roster's Primary-pin column — every row naming "
            "a sidecar wants one launcher call instead, consolidated orchestrator-side. Rows whose "
            "Anthropic fallback is `—` stay on the engine."
        )

    # A plan-quota transport with more headroom than this session is the strongest advice this
    # hook can give, because neither side bills marginally: routing there spends an allowance
    # that would otherwise expire, instead of one already burning too fast. Silent when no
    # such transport exists or its probe fails — the same fail-open posture as the band read.
    for row in _slacker_transports(band):
        note.append(
            f"{row['transport']}: {row['band']}"
            + (f" (pressure {row['pressure']})" if row.get("pressure") is not None else "")
            + f" vs this session's {band} — its allowance expires unused too, and no dollars are "
            f"spent either way. Delegatable work can go to: {' or '.join(row['launchers'])}"
        )

    note.append("Deliberate Anthropic dispatch is fine — state which currency you intended.")
    note.extend(role_note)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[provider check] " + " | ".join(note),
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # advisory only — never block or noise a dispatch over a budget hint
    sys.exit(0)
