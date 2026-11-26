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
# The owner-path cases run as a session a person drives: a proof launched from a delegate (entrypoint sdk-cli) must not
# hand that fact to the hook under test. A case that wants a delegate sets it (`_with_entrypoint`, CLAUDE_CODE_SIDECAR).
os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

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
    env.pop("CLAUDE_CODE_SIDECAR_KIND", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env["CLAUDE_CODE_TRANSPORT"] = "anthropic"
    if delegate:
        env.update(CLAUDE_CODE_SIDECAR="1", CLAUDE_CODE_SIDECAR_KIND="judge")
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

# ---- the rails reach the client in parts, each under its per-hook text cap ---------------------------------
import json  # noqa: E402,F811
import re  # noqa: E402
import tempfile  # noqa: E402

from amend_delivery import HOOK_TEXT_CAP  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
UNREGISTERED = "unregistered-model-for-the-detailed-tier"  # no registry row: the longest (detailed) rails
KINDS = ("scout", "reviewer", "author")
NL = chr(10)


def _registered_commands():
    """The argument list of every `session_model_rails.py` command `settings.base.json` registers on SessionStart."""
    with open(os.path.join(REPO, ".claude", "settings.base.json"), encoding="utf-8") as fh:
        groups = json.load(fh)["hooks"]["SessionStart"]
    found = []
    for group in groups:
        for hook in group["hooks"]:
            m = re.search(r"session_model_rails\.py\"(.*)$", hook["command"])
            if m:
                found.append(m.group(1).split())
    return found


def _run_args(args, kind, model=UNREGISTERED, planted=None, extra=None):
    """The hook's stdout, decoded as UTF-8, for one registered argument list under one sidecar kind."""
    env = dict(os.environ)
    for name in ("CLAUDE_CODE_SIDECAR", "CLAUDE_CODE_SIDECAR_KIND", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)
    env["CLAUDE_CODE_TRANSPORT"] = "anthropic"
    env["CLAUDE_PROJECT_DIR"] = REPO
    if kind:
        env.update(CLAUDE_CODE_SIDECAR="1", CLAUDE_CODE_SIDECAR_KIND=kind)
    env.update(planted or {})
    done = subprocess.run([sys.executable, MOD] + list(args), input=json.dumps(dict({"model": model}, **(extra or {}))).encode(),
                          capture_output=True, env=env, timeout=60)
    return done.stdout.decode("utf-8")


def _parts(kind):
    """Each registered command's output for `kind`, in registration order."""
    return [_run_args(args, kind) for args in _registered_commands()]


def _whole(kind):
    """The unsplit text: the hook run with no part argument."""
    return _run_args([], kind)


def _parts_fit_and_equal_the_whole(kind):
    parts, whole = _parts(kind), _whole(kind)
    return (bool(parts) and all(len(p.encode("utf-8")) <= HOOK_TEXT_CAP for p in parts)
            and "".join(parts) == whole and len(whole) > 0)


def _registers_exactly_the_parts():
    cmds = _registered_commands()
    return cmds == [["--part", str(n)] for n in range(1, smr.PARTS + 1)]


def _text_fits_the_registered_parts(kind):
    return "[rails overflow]" not in "".join(_parts(kind))


def _a_delegate_env(kind):
    return {"CLAUDE_CODE_SIDECAR": "1", "CLAUDE_CODE_SIDECAR_KIND": kind, "CLAUDE_CODE_TRANSPORT": "anthropic"}


def _overflow_with_one_part():
    """The author text through a one-part hook: the part says what did not fit, and what it kept is a whole-line prefix."""
    saved = {k: os.environ.get(k) for k in _a_delegate_env("author")}
    os.environ.update(_a_delegate_env("author"))
    real_parts = smr.PARTS
    try:
        whole = smr.build_text({"model": UNREGISTERED}, write_tier=False)
        # A profile lane's rails can be shorter than the hook cap; pad past it so the overflow path is reached.
        if len(whole.encode("utf-8")) <= HOOK_TEXT_CAP:
            whole += "\n" + "padding line for the overflow case\n" * 400
        smr.PARTS = 1
        part = smr.part_text(whole, 1)
    finally:
        smr.PARTS = real_parts
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    kept, _, note = part.partition("[rails overflow]")
    return (len(part.encode("utf-8")) <= HOOK_TEXT_CAP and whole.startswith(kept) and kept.endswith(NL)
            and 0 < len(kept) < len(whole) and "guard_text.py" in note)


def _later_parts_are_empty_and_write_no_tier():
    """Only part 1 caches the session tier, and a text that fits part 1 leaves later parts empty."""
    import _hook_parts
    with tempfile.TemporaryDirectory() as state:
        planted = {"HARNESS_HOOK_STATE_DIR": state}
        extra = {"session_id": "parts0001"}
        for n in range(2, smr.PARTS + 1):
            _run_args(["--part", str(n)], None, planted=planted, extra=extra)
        wrote_early = os.path.exists(os.path.join(state, "parts000.json"))
        first = _run_args(["--part", "1"], None, planted=planted, extra=extra)
        wrote = os.path.exists(os.path.join(state, "parts000.json"))
    short_p2 = _hook_parts.part_of("short line\n", 2, smr.PARTS)
    return short_p2 == "" and not wrote_early and wrote and "[session] Session tier" in first


def _pack_cases():
    """The helper packs whole lines by UTF-8 bytes and never splits a line across parts."""
    import _hook_parts
    text = NL.join(["aaaa", "bb—b", "cccc", "dd", ""])  # the em dash is 3 bytes: line 2 is 7 bytes with its newline, so 3 parts at cap 10
    parts = [_hook_parts.part_of(text, n, 3, cap=10) for n in (1, 2, 3, 4)]
    return ("".join(parts[:3]) == text and parts[3] == "" and all(len(p.encode("utf-8")) <= 10 for p in parts)
            and all(p.endswith(NL) for p in parts[:3]))


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
    # CLAUDE_CODE_SIDECAR_KIND carries the launcher's -G kind; the table maps it to the guard file set.
    ("a scout delegate gets the survey rails", lambda: "shape 'survey'" in _delegate_output(
        "claude-opus-5-5", {"CLAUDE_CODE_SIDECAR_KIND": "scout"})),
    ("a reviewer delegate gets the review rails", lambda: "shape 'review'" in _delegate_output(
        "claude-opus-5-5", {"CLAUDE_CODE_SIDECAR_KIND": "reviewer"})),
    ("an author delegate gets the author rails", lambda: "shape 'author'" in _delegate_output(
        "claude-opus-5-5", {"CLAUDE_CODE_SIDECAR_KIND": "author"})),
    ("a gate delegate gets the gate rails", lambda: "shape 'gate'" in _delegate_output(
        "claude-opus-5-5", {"CLAUDE_CODE_SIDECAR_KIND": "gate"})),
    ("the retired CLAUDE_CODE_SIDECAR_SHAPE is no longer read", lambda: "shape 'survey'" not in _delegate_output(
        "claude-opus-5-5", {"CLAUDE_CODE_SIDECAR_SHAPE": "survey"})),
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
    ("a fresh start does not name a compaction ladder read",
     lambda: not any(l.startswith("[compaction]") for l in _startup_lines())),
    ("a delegate's compact start does not name a ladder read",
     lambda: "model_ladder_evidence.md" not in _delegate_output_compact("claude-opus-5-5")),
    # The client writes the re-attached skills after SessionStart runs, so the compact start states the gate's rule
    # (skill, or a whole re-attached copy; slice or full read) rather than naming the skill as kept or cleared.
    ("a compact start with unknown dispatch history states the gate's doctrine rule, skill and ladder steps",
     lambda: _compact_notice() is not None
             and "Skill(skill=\"orchestration\")" in _compact_notice()
             and "re-attached whole" in _compact_notice()
             and "for-role <tier>..." in _compact_notice()
             and "in full" in _compact_notice()),
    # The edit guard denies a harness edit while instruction_quality is not in context (6 denies in session 9c1a9920,
    # 2026-10-14), so the compact start names it in the re-attach rule, and never claims a skill was cleared.
    ("a compaction notice names instruction_quality in the re-attach rule and names no skill cleared",
     lambda: any("instruction_quality" in line for line in _compact_lines())
             and not any("Compaction cleared" in line for line in _compact_lines())
             and not any(l.startswith("[compaction]") for l in _startup_lines())),
    # The ladder's selection line alone states how a dispatch's row and plan are chosen; the effort line points there.
    ("the effort line points at the selection line for the route and never restates its condition",
     lambda: all("selection line" in line and "session's plan" not in line and "launcher" not in line
                 for line in (smr.effort_line({"effort": "high"}), smr.effort_line({"session_id": None})))),
    # ---- a delegate's rails reach it whole: parts under the client's per-hook cap --------------------------
    ("every part for a scout delegate fits the cap and the parts joined are the whole text",
     lambda: _parts_fit_and_equal_the_whole("scout")),
    ("...a reviewer", lambda: _parts_fit_and_equal_the_whole("reviewer")),
    ("...an author", lambda: _parts_fit_and_equal_the_whole("author")),
    ("settings register part 1..PARTS once each and no unsplit command", _registers_exactly_the_parts),
    ("the longest rails still fit the registered parts without the overflow notice",
     lambda: all(_text_fits_the_registered_parts(k) for k in KINDS)),
    ("a text past the last part says what did not fit and keeps a whole-line prefix", _overflow_with_one_part),
    ("a fitting session prints in part 1 only, and only part 1 caches the session tier",
     _later_parts_are_empty_and_write_no_tier),
    ("the helper packs whole lines by bytes", _pack_cases),
    # ---- E3: interactive layer in top-level sessions only -------------------------------------------------
    ("a top-level payload's joined parts contain # Working with the owner",
     lambda: "# Working with the owner" in "".join(_parts(None))),
    ("with CLAUDE_CODE_SIDECAR=1 the joined parts do not contain # Working with the owner",
     lambda: "# Working with the owner" not in "".join(_parts("author"))),
    ("a payload carrying agent_id does not contain # Working with the owner",
     lambda: "# Working with the owner" not in "".join(
         _run_args(args, None, extra={"agent_id": "sub-1"}) for args in _registered_commands())),
    ("with CLAUDE.interactive.md absent the hook still exits 0",
     lambda: (lambda saved: (
         setattr(smr, "INTERACTIVE", os.path.join(REPO, ".claude", "NO_SUCH_INTERACTIVE.md")),
         len(smr.build_text({"model": "claude-opus-5-5"}, write_tier=False)) > 0,
         setattr(smr, "INTERACTIVE", saved),
     )[1])(smr.INTERACTIVE)),
    ("a top-level start prints the sweep fact line when recorded rows are unswept",
     lambda: _sweep_text({"model": "claude-opus-5-5"}, swept=None).count("correction row(s) no sweep has read, oldest 2026-11-01") == 1),
    ("an all-swept ledger and an absent ledger print no sweep line",
     lambda: "no sweep has read" not in _sweep_text({"model": "claude-opus-5-5"}, swept="2026-11-05")
     and "no sweep has read" not in _sweep_text({"model": "claude-opus-5-5"}, ledger=False)),
    ("a payload with agent_id gets no sweep line",
     lambda: "no sweep has read" not in _sweep_text({"model": "claude-opus-5-5", "agent_id": "a"}, swept=None)),
    ("a claude -p child (entrypoint sdk-cli) gets no sweep line",
     lambda: _with_entrypoint("sdk-cli", lambda: "no sweep has read" not in _sweep_text({"model": "claude-opus-5-5"}, swept=None))),
    ("with CLAUDE_CODE_SIDECAR set no sweep line prints",
     lambda: "no sweep has read" not in _sweep_text({"model": "claude-opus-5-5"}, swept=None, sidecar="1")),
    ("codex with gpt-6.2-sol after compaction fits in two parts with no overflow and keeps the file's last line",
     lambda: (lambda joined, last_line: (
         "[rails overflow]" not in joined and bool(last_line) and last_line in joined
     ))(
         "".join(_run_args(
             args, None, model="gpt-6.2-sol",
             planted={"CLAUDE_CODE_TRANSPORT": "codex"},
             extra={"source": "compact", "session_id": "codex-compact-1"},
         ) for args in _registered_commands()),
         (open(os.path.join(REPO, ".claude", "CLAUDE.interactive.md"), encoding="utf-8").read().strip().splitlines()[-1]
          if os.path.isfile(os.path.join(REPO, ".claude", "CLAUDE.interactive.md")) else ""),
     )),
    ("ROLE -> MODEL names rule 1's bar and not the job's priority",
     lambda: "rule 1's bar" in CODEX and "job's priority" not in CODEX),
]


def _with_entrypoint(value, run):
    """`run()` with CLAUDE_CODE_ENTRYPOINT set to `value`, restored after."""
    saved = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = value
    try:
        return run()
    finally:
        os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None) if saved is None else os.environ.__setitem__("CLAUDE_CODE_ENTRYPOINT", saved)


def _sweep_text(payload, swept=None, ledger=True, sidecar=None):
    """build_text with the ledger pointed at a fixture: two recorded rows (2026-11-01, 2026-11-05), optional marker."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "autolearn_outcomes.jsonl")
    if ledger:
        with open(path, "w", encoding="utf-8") as fh:
            for day in ("2026-11-01", "2026-11-05"):
                fh.write(json.dumps({"date": day, "session": "s", "target": "t", "signal": "g", "recommended": "r",
                                     "outcome": "recorded", "reason": "x"}) + "\n")
    if swept:
        with open(path + ".swept", "w", encoding="utf-8") as fh:
            fh.write(swept + "\n")
    saved = smr._outcomes.DEFAULT_FILE
    prev_sidecar = os.environ.get("CLAUDE_CODE_SIDECAR")
    smr._outcomes.DEFAULT_FILE = path
    if sidecar is None:
        os.environ.pop("CLAUDE_CODE_SIDECAR", None)
    else:
        os.environ["CLAUDE_CODE_SIDECAR"] = sidecar
    try:
        return smr.build_text(payload, write_tier=False)
    finally:
        smr._outcomes.DEFAULT_FILE = saved
        if prev_sidecar is None:
            os.environ.pop("CLAUDE_CODE_SIDECAR", None)
        else:
            os.environ["CLAUDE_CODE_SIDECAR"] = prev_sidecar


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
