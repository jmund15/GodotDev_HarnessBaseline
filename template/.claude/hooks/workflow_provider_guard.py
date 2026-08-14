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
--band (the threshold SSOT) rather than recomputed here.

STATE: none. Each Workflow dispatch spends independently, so each one is worth a nudge;
there is nothing to dedupe across and no file to grow.
"""
import json
import os
import subprocess
import sys

# Fan-out engines where provider choice is load-bearing. Other workflows still get a
# nudge (they spend too), but these name the roster columns the caller should re-read.
FANOUT_ENGINES = ("explore_fanout", "review_fanout", "dispatch", "doc_architecture_audit")


def band_and_pressure():
    """Return (band, pressure) from the threshold SSOT, or (None, None).

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


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Workflow":
        return

    band, pressure = band_and_pressure()
    # Surplus means plan quota is going unused — the engine IS the right call there.
    if not band or band == "Surplus":
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
            "the sidecar wants one `.claude/scripts/deepseek_sidecar.sh` call instead, consolidated "
            "orchestrator-side. Rows whose Anthropic fallback is `—` stay on the engine."
        )
    note.append("Deliberate Anthropic dispatch is fine — state which currency you intended.")

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
