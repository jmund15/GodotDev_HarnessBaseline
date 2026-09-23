#!/usr/bin/env python3
"""Proof for hooks/session_model_rails.py's PROVIDER rails block.

This text prints to SessionStart stdout in every provider session, so it is always-loaded in
effect: a contradiction here is caught nowhere downstream, and the model sees it before the first
delegation decision. Two things are proven -- that the block says what doctrine says (it CITES
orchestration §5b rather than legislating its own floor), and that the tiers-this-seat-lacks line
is COMPUTED, since a hardcoded list would read as authoritative the day it went stale.

The degraded path is a case, not an afterthought: a hook that silently omits the line when the
registry is unreadable and one that had nothing to say emit identical silence.

Run: python3 .claude/tests/test_session_model_rails_rails.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
MOD = os.path.join(HOOKS, "session_model_rails.py")
sys.path.insert(0, HOOKS)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
spec = importlib.util.spec_from_file_location("smr", MOD)
smr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smr)

import model_registry  # noqa: E402

CODEX = smr.provider_rails("codex", "env", {"model": "gpt-5.6-luna"})
NO_MODEL = smr.provider_rails("codex", "env", {})           # the shape after /clear
OPENCODE = smr.provider_rails("opencode", "baseUrl", {"model": "muse-spark-1.3-contributor-free"})


def _rails_with_effort(value):
    """Codex rails rendered with HARNESS_SESSION_EFFORT set to `value` (or absent when None)."""
    import os
    prev = os.environ.get("HARNESS_SESSION_EFFORT")
    if value is None:
        os.environ.pop("HARNESS_SESSION_EFFORT", None)
    else:
        os.environ["HARNESS_SESSION_EFFORT"] = value
    try:
        return smr.provider_rails("codex", "env", {"model": "gpt-5.6-luna"})
    finally:
        os.environ.pop("HARNESS_SESSION_EFFORT", None)
        if prev is not None:
            os.environ["HARNESS_SESSION_EFFORT"] = prev


def broken_registry():
    """Render with the registry lookup forced to fail, WITHOUT touching the real file."""
    real = model_registry.load

    def boom(*a, **k):
        raise RuntimeError("registry unreadable (planted)")

    model_registry.load = boom
    try:
        return smr.provider_rails("codex", "env", {"model": "gpt-5.6-luna"})
    finally:
        model_registry.load = real


BROKEN = broken_registry()

# What the live registry actually says codex lacks -- the assertion below compares against this
# rather than against a literal, so adding a codex row does not red the proof for the wrong reason.
TIERS = model_registry.role_tiers()
DATA = model_registry.load()
LACKED = [t for t in model_registry.TIER_ORDER
          if not [m for m in model_registry.rows_for_tier(t, DATA, TIERS)
                  if m["transport"] == "codex"]]

CASES = [
    # ---- the floor split cites its home, never replaces it ------------------
    ("the rails cite orchestration §5b for the reserved floor",
     lambda: "orchestration §5b" in CODEX),

    ("the floor still reserves gate decisions and the ideal-design verdict",
     lambda: "Gate decisions and the ideal-design verdict" in CODEX),

    ("...and the split says it reserves DECISIONS, not the work shape",
     lambda: "reserves those DECISIONS, not the work shape" in CODEX),

    ("a delegable lens is qualified as SCOPED, matching orchestration §5",
     lambda: "SCOPED judgment, review or architecting lens" in CODEX),

    # ---- the resolver is pitched before the first delegation decision -------
    ("the rails name the for-role command",
     lambda: "model_registry.py for-role" in CODEX),

    ("...with the closed tier set, so the reader needs no second lookup",
     lambda: all(t in CODEX for t in model_registry.TIER_ORDER)),

    # ---- the lacked-tiers line is COMPUTED ---------------------------------
    ("the lacked-tiers line names exactly what the live registry says codex lacks",
     lambda: (not LACKED) or all("`%s`" % t in CODEX.split("NO AVAILABLE codex ROW SERVES:")[1]
                                 .split("\n")[0] for t in LACKED)),

    ("...and states the consequence, not just the fact",
     lambda: (not LACKED) or "silent tier trade" in CODEX),

    ("a tier codex DOES serve is absent from the lacked list",
     lambda: "NO AVAILABLE codex ROW SERVES" not in CODEX
             or "`fanout`" not in CODEX.split("NO AVAILABLE codex ROW SERVES:")[1].split("\n")[0]),

    ("a different transport gets its OWN lacked set, not codex's",
     lambda: "NO AVAILABLE opencode ROW SERVES" in OPENCODE or not LACKED),

    # ---- the degraded path SAYS it is degraded ------------------------------
    ("an unreadable registry says the lacked tiers could not be computed",
     lambda: "could not be computed" in BROKEN),

    ("...rather than silently omitting the line",
     lambda: "NO AVAILABLE codex ROW SERVES" not in BROKEN),

    ("...and the rails still render rather than raising",
     lambda: BROKEN.startswith("[codex session")),

    # ---- the pre-existing contract still holds -----------------------------
    ("the transport and its identifying signal are still named",
     lambda: "`codex` transport (identified by env)" in CODEX),

    # Measured both regimes: with CCP_CODEX_EFFORT set, a client asking `low` reaches the model at
    # `medium`; unset, `low` reaches it as `low`. The launcher sets neither variable and hands the
    # launch rung to the child via `--effort`, so both controls are live and the statusline is
    # accurate. Asserted on BEHAVIOUR rather than an exact sentence: pinning the phrasing reddened
    # this proof once on a rewording that changed nothing.
    ("codex is told BOTH /model and /effort reach the model",
     lambda: "/model" in CODEX and "/effort" in CODEX and "reach the model" in CODEX),

    ("...and warned that a Claude role name in /model picks a GPT row it did not choose",
     lambda: "Claude role name" in CODEX and "GPT id" in CODEX),

    ("...and it no longer claims effort is fixed for the session",
     lambda: "EFFORT IS FIXED" not in CODEX and "Relaunch to change it" not in CODEX),

    ("...and it names the probe for a session started under the overriding regime",
     lambda: "codex_effort_probe.py" in CODEX),

    # The launcher knows the rung and now hands it down, so the rails state a FACT instead of
    # prescribing a probe. Both halves are asserted: the value when present, the fallback when not.
    ("HARNESS_SESSION_EFFORT is reported as this session's rung when the launcher set it",
     lambda: "launched at effort `high`" in _rails_with_effort("high")),

    ("...and without it the rails name the probe rather than guessing a rung",
     lambda: "codex_effort_probe.py" in _rails_with_effort(None)),

    ("...and it does NOT claim effort is inert unconditionally",
     lambda: "EFFORT IS FIXED FOR THIS SESSION" not in CODEX),

    ("...and opencode does not get it (it is a codex-proxy fact)",
     lambda: "reach the model" not in OPENCODE),

    ("a session that cannot see its own model is told so",
     lambda: "absent after /clear" in NO_MODEL),

    ("every format placeholder is filled -- no stray braces reach the model",
     lambda: "{" not in CODEX and "}" not in CODEX
             and "{" not in BROKEN and "{" not in OPENCODE),
    # Driver notes: registry data, keyed to the model that DRIVES the session.
    ("a fable driver gets the F12 note from the registry, with its evidence",
     lambda: "Recognizing a name" in smr.driver_notes_block("claude-fable-5-1")
             and "F12" in smr.driver_notes_block("claude-fable-5-1")),
    ("a codex sol driver gets no notes block (the -1m launcher banner owns that warning)",
     lambda: smr.driver_notes_block("gpt-5.6-sol[1m]") == ""),
    ("an opus driver gets no notes block", lambda: smr.driver_notes_block("claude-opus-5-5[1m]") == ""),
    ("an unknown or absent model gets no notes block",
     lambda: smr.driver_notes_block("mythos-x") == "" and smr.driver_notes_block(None) == ""),
    ("the hardcoded fable clause is gone from the hook", lambda: not hasattr(smr, "TIER_LINE_FABLE_EXTRA")),
    # Anthropic rail: one line, and it keeps the marker tools/ladder_ingest.py reads transport from.
    ("the Anthropic rail is one line opening with the transport marker",
     lambda: len(smr.ANTHROPIC_RAILS.strip().splitlines()) == 1
             and smr.ANTHROPIC_RAILS.startswith("[anthropic session")),
    ("the Anthropic rail names the sidecar route and no retired hook",
     lambda: "reference/sidecar_dispatch.md" in smr.ANTHROPIC_RAILS
             and "translate" not in smr.ANTHROPIC_RAILS),
    # The tier line carries no output-shape clause: that belongs to the output style.
    ("an open tier line is the tier alone",
     lambda: smr.tier_line("condensed") == "[session] Session tier: `condensed` — skip `## detailed` sections."
             and smr.tier_line("minimal") == "[session] Session tier: `minimal` — skip `## detailed` sections."),
    ("the detailed tier line names the detailed sections",
     lambda: "read every `## detailed` section" in smr.tier_line("detailed")),
]


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    if failed:
        print("\n--- codex rails ---\n%s" % CODEX)
        print("\n--- broken-registry rails ---\n%s" % BROKEN)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
