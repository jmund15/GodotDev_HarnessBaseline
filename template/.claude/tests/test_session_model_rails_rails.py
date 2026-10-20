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
import subprocess
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
SUFFIXED = smr.provider_rails("codex", "env", {"model": "gpt-6-sol[1m]"})
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


def _profile_env(tier, ai_worker):
    """This machine's tier and ai-worker registration, planted where the hook reads them live."""
    import json
    import tempfile
    root = tempfile.mkdtemp(prefix="smrprofile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    profile = os.path.join(root, "workstation_profile.json")
    if tier is not None:
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    return {"WORKSTATION_PROFILE_PATH": profile, "CLAUDE_CONFIG_DIR": cfg,
            "SC_WORKDIR": os.path.join(root, "nowhere")}


def _session_output(model, delegate=False, planted=None, extra=None):
    import json
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SIDECAR", None)
    env.pop("CLAUDE_CODE_SIDECAR_SHAPE", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env["CLAUDE_CODE_TRANSPORT"] = "anthropic"
    if delegate:
        env.update(CLAUDE_CODE_SIDECAR="1", CLAUDE_CODE_SIDECAR_SHAPE="any")
    if planted:
        env.update(planted)
    result = subprocess.run([sys.executable, MOD], input=json.dumps(dict({"model": model}, **(extra or {}))),
                            capture_output=True, text=True, env=env, timeout=30)
    return result.stdout


def _delegate_output(model, planted=None):
    return _session_output(model, delegate=True, planted=planted)


# What the live registry actually says codex lacks -- the assertion below compares against this
# rather than against a literal, so adding a codex row does not red the proof for the wrong reason.
TIERS = model_registry.role_tiers()
DATA = model_registry.load()
LACKED = [t for t in model_registry.TIER_ORDER
          if not [m for m in model_registry.rows_for_tier(t, DATA)
                  if m["transport"] == "codex"]]

def missing_ladder():
    real = model_registry.role_tiers
    model_registry.role_tiers = lambda *_a, **_k: (_ for _ in ()).throw(
        model_registry.RegistryError("missing ladder"))
    try:
        return smr.provider_rails("codex", "env", {"model": "gpt-5.6-luna"})
    finally:
        model_registry.role_tiers = real


CASES = [
    ("T1 missing ladder still computes registry tier gaps",
     lambda: "NO AVAILABLE codex ROW SERVES:" in missing_ladder()
             and "could not be computed" not in missing_ladder()),
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

    ("a committed arg-driven workflow is not described as Anthropic-only",
     lambda: "Committed `.claude/workflows/*.js` scripts pin Anthropic" not in CODEX
             and "native" in CODEX and "literal" in CODEX),
    ("startup rails do not freeze a moving proxy model mapping",
     lambda: "gpt-5.6-sol" not in CODEX and "proxy" in CODEX),
    ("a driver with a client window suffix resolves to its registry row",
     lambda: "gpt-6-sol" in SUFFIXED and "not a registry model" not in SUFFIXED),

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
    # Delegates receive notes keyed to the child model, not the parent/orchestrator.
    ("a luna delegate receives its registry driver notes exactly once, through the rails",
     lambda: _delegate_output("gpt-6-luna").count("Work unattended: never stop to ask.") == 1
             and _delegate_output("gpt-6-luna").index("[delegate rails")
                 < _delegate_output("gpt-6-luna").index("[session] Driver notes (gpt-6-luna)")),
    ("a non-delegate session still prints its separate notes block before transport rails",
     lambda: _session_output("gpt-6-luna").count("Work unattended: never stop to ask.") == 1
             and _session_output("gpt-6-luna").index("[session] Driver notes (gpt-6-luna)")
                 < _session_output("gpt-6-luna").index("[anthropic session")),
    ("a delegate whose model has no notes gets no driver-notes heading",
     lambda: "Driver notes (" not in _delegate_output("claude-opus-5-5")),
    # C7: the delegate tool grant names the worker MCP, so on a machine without it the grant must
    # name the route that IS there. The grant block itself still prints either way.
    ("a delegate on a machine with no ai-worker is granted the local route instead",
     lambda: (lambda out: "[delegate tool grant" in out and "mcp__ai-worker__" not in out
                          and "bounded Read" in out)(
         _delegate_output("claude-opus-5-5", _profile_env("full", "absent")))),
    ("a delegate on a machine with ai-worker keeps the worker grant",
     lambda: "mcp__ai-worker__read_files" in _delegate_output(
         "claude-opus-5-5", _profile_env("full", "present"))),
    ("...and no profile keeps it too",
     lambda: "mcp__ai-worker__read_files" in _delegate_output(
         "claude-opus-5-5", _profile_env(None, "present"))),
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
    # Compaction drops the ladder from context; the resumed session learns at start where it is,
    # before it chooses a pin.
    ("a compact start names the owed ladder read by its absolute path",
     lambda: "model_ladder_evidence.md" in _session_output("claude-opus-5-5", extra={"source": "compact"})
             and os.path.isabs(next(w.strip("`") for w in _session_output(
                 "claude-opus-5-5", extra={"source": "compact"}).split() if "model_ladder_evidence.md" in w))),
    ("a fresh start does not name a ladder read",
     lambda: "model_ladder_evidence.md" not in _session_output("claude-opus-5-5", extra={"source": "startup"})),
    ("a delegate's compact start does not name a ladder read",
     lambda: "model_ladder_evidence.md" not in _delegate_output_compact("claude-opus-5-5")),
    # The client writes the re-attached skills after SessionStart runs, so the compact start states the gate's rule
    # (skill, or a whole re-attached copy; slice or full read) rather than naming the skill as kept or cleared.
    ("a compact start with unknown dispatch history states the gate's doctrine rule, skill and ladder steps",
     lambda: _compact_notice() is not None
             and "Skill(skill=\"orchestration\")" in _compact_notice()
             and "re-attached whole" in _compact_notice()
             and "ladder-slice <tier>" in _compact_notice()
             and "in full" in _compact_notice()),
    # The edit guard denies a harness edit while instruction_quality is not in context (6 denies in session 9c1a9920,
    # 2026-10-14), so the compact start names it in the re-attach rule, and never claims a skill was cleared.
    ("a compaction notice names instruction_quality in the re-attach rule and names no skill cleared",
     lambda: any("instruction_quality" in line and "truncated for compaction" in line
                 for line in _compact_lines())
             and not any("Compaction cleared" in line for line in _compact_lines())
             and not any("instruction_quality" in line for line in _startup_lines())),
    # The ladder's selection line alone states how a dispatch's row and plan are chosen; the effort line points there.
    ("the effort line points at the selection line for the route and never restates its condition",
     lambda: all("selection line" in line and "session's plan" not in line and "launcher" not in line
                 for line in (smr.effort_line({"effort": "high"}), smr.effort_line({"session_id": None})))),
]


def _compact_lines():
    """Every `[compaction]` line of one compact SessionStart whose payload names no transcript."""
    import tempfile
    with tempfile.TemporaryDirectory() as state:
        env = {"HARNESS_HOOK_STATE_DIR": state}
        out = _session_output("claude-opus-5-5", planted=env, extra={"source": "compact", "session_id": "cmpl0001"})
    return [l for l in out.splitlines() if l.startswith("[compaction]")]


def _compact_notice():
    """The dispatch-doctrine line of one compact SessionStart, or None."""
    lines = [l for l in _compact_lines() if "dispatch doctrine" in l]
    return lines[0] if lines else None


def _startup_lines():
    """A startup SessionStart's lines: the compaction notices never print there."""
    return _session_output("claude-opus-5-5", extra={"source": "startup", "session_id": "strt0001"}).splitlines()


def _delegate_output_compact(model):
    return _session_output(model, delegate=True, extra={"source": "compact"})


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
