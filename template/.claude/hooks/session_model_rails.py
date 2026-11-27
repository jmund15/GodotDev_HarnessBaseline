#!/usr/bin/env python3
"""SessionStart: inject session-effort visibility (all models), delegate guard
rails + tool-grant correction for sidecar CHILD sessions, dispatch-transport rails
(one transport-parameterized block for every non-Anthropic ORCHESTRATOR session, the
mirror for Anthropic ones, and a fail-closed block when the endpoint cannot be identified),
the re-pitch protocol for opus sessions, strict tool-routing rails for non-orchestrator
session models, and, on an orchestrator's compact start, the ladder Read the dispatch gates now owe.

Which transport a session is on is resolved ONCE, in hooks/_session_transport.py, and shared
with workflow_provider_guard.py. Adding a provider is a registry row plus a launcher that
exports CLAUDE_CODE_TRANSPORT -- never an edit here.

Delegate vs orchestrator is decided by CLAUDE_CODE_SIDECAR, which deepseek_sidecar.sh
and codex_proxy_sidecar.sh set on the child only — the two need opposite rails, and a
delegate reading "You ARE the orchestrator this session" is worse than none.

A delegate's rails carry `guards/common.md` AND its shape file, assembled by
tools/guard_text.py. `common` is concatenated rather than chained: it holds the rules
binding every delegate, so its delivery must not depend on the child choosing to follow
a pointer it finds inside another file. On the -D bare/pointer tiers no project hook fires,
so lib/sidecar_common.sh calls guard_text.py directly — which is why that parse is a shared
module rather than a function here. The child's registry driver notes are keyed to the
child's model and printed here, after the rails.

Tool-routing prohibitions are NOT injected here. They live once in CLAUDE.core.md §9,
in the NEVER-list form this hook used to supply (moved 2026-08-17): CLAUDE.md is
already injected into every session, so a second delivery path to the same audience
bought nothing — and the old model gate exempted fable, leaving the orchestrator tier
relying on §Tool Routing alone anyway. The form was the value, not the channel; §Tool Routing now carries it
for every model. Re-pitch guidance likewise lives in the `wait_what` skill, whose
description is auto-injected.

Channel: SessionStart stdout at exit 0 is model-visible (verified matrix,
archive_hook_gotchas.md). The `model` field is optional in SessionStart input
(absent after /clear and on conversation recovery) — absence fails TOWARD
injection: rails when in doubt.

Output: the client caps each hook command's output (`HOOK_TEXT_CAP`) and shows a 2,000-character preview past it, and
a delegate's rails exceed it. The hook is registered as `--part 1` .. `--part PARTS`; each part computes the same
whole text and prints its whole-line slice (`_hook_parts`). Only part 1 caches the session tier. A text that outgrows
PARTS ends its last part with an overflow note naming what is missing. With no `--part` it prints the whole text.

Fail posture: advisory context hook — fail open (exit 0, silent) on any error.
"""
import json
import os
import sys
import tempfile

# Windows consoles default stdout to cp1252; rails text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # LF only: a part's bytes are what the cap counts

# The guard tier parse and the machine-aware clause rendering live once, in tools/guard_text.py,
# because the sidecar bash lib calls that same module as a CLI on -D bare/pointer, where no hook
# fires and it must assemble the rails itself. Precedent: budget_posture.py.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
try:
    from guard_text import guard_text
except Exception:  # advisory hook: a broken import must not brick a delegate child
    guard_text = None
try:
    import autolearn_outcomes as _outcomes
except Exception:  # advisory hook: a broken import must not brick session start
    _outcomes = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_parts
import _model_tier
from _prompt_provenance import in_delegate
try:
    import _session_transport  # noqa: E402
except Exception:
    # Advisory hook, stated fail posture: fail open. An unguarded import here throws BEFORE
    # main()'s try/except and costs the session its whole rails block -- tier line and
    # delegate tool-grant included -- over a helper only the transport branch needs.
    _session_transport = None


def _ai_worker_absent() -> bool:
    """Is ai-worker registered on this machine? `False` whenever that cannot be read.

    `tools/workstation_profile.py` owns the walk and answers `None` for unknown; unknown is NOT
    absent, so an unreadable config keeps the authored grant rather than narrowing it on a guess.
    """
    try:
        import workstation_profile
        return workstation_profile.ai_worker() == "absent"
    except Exception:
        return False

# Model ids that run with the lean always-loaded surface (no rails injection).

# Correction printed AFTER RAILS to a sidecar child only. RAILS (and CLAUDE.core.md §9, injected into the
# same child) route some lookups to a tool the child's grant lacks — it obeys an instruction naming an
# absent tool and silently changes search behavior instead. The grant is decided by which MCP servers
# CONNECT, not by auth: semantic-search connects at init; ai-worker connects a turn or two later, so a short
# dispatch can still miss it.
# Canon: gotcha_sidecar_child_mcp_tool_grant.md (connection timing); gotcha_sidecar_mcp_deferral_by_route.md (route delivery).
#
# Both granted bullets read the same on every route and machine: whether a child's schemas are deferred behind
# ToolSearch is a fact the child sees in its own tool list, so each bullet says "if ToolSearch is among your tools,
# load the schema first" and no route lookup picks its wording. Only the ai-worker bullet depends on the machine and
# route: `tools/workstation_profile.py` reads whether the server is registered at all, and the route's `transportMcp`
# policy (`tool_profiles.transport_policy`) lists a server it does not grant (`dropServers`). Where ai-worker is
# absent or not granted, the bullet grants the route the child has instead: the same bounded Read it already named
# as the tool-not-found fallback, plus a subagent. Unknown keeps the granted bullet.
DELEGATE_TOOL_GRANT_HEAD = """\
[delegate tool grant — overrides tool routing wherever CLAUDE.core.md §9 or a rails block names an absent tool; canon: gotcha_sidecar_child_mcp_tool_grant]
"""
DELEGATE_SEARCH_GRANT = (
    "- semantic-search IS available here (mcp__plugin_semantic-search_semantic-search__search, same name as a normal session). "
    "If ToolSearch is among your tools, load its schema with "
    "`select:mcp__plugin_semantic-search_semantic-search__search` before the first call. "
    "Route to it exactly as CLAUDE.core.md §9 directs — do NOT substitute Grep.\n"
)
DELEGATE_AI_WORKER_GRANT = (
    "- ai-worker IS available too (mcp__ai-worker__read_files / write_doc), though it can connect "
    "a turn or two after init. If ToolSearch is among your tools, load a tool's schema with "
    "`select:mcp__ai-worker__<name>` before the first call. "
    "Route to it as `~/.claude/CLAUDE.md` §Local worker directs. Only if a "
    "call fails with tool-not-found: use bounded Read (offset/limit) of the named files and say so "
    "in your deliverable.\n"
)
DELEGATE_AI_WORKER_ABSENT = (
    "- ai-worker is NOT available to this child, so nothing here routes to it: substitute "
    "bounded Read (offset/limit) of the named files, or hand the extraction to a subagent, and "
    "say so in your deliverable.\n"
)
DELEGATE_TOOL_GRANT_TAIL = """\
- Verify a tool exists before routing to it. Never report "not found" on the strength of a call that never ran.
"""


def _mcp_policy() -> dict:
    """The MCP policy of this child's route (`dropServers` is the key the grant reads); `{}` when it names none or cannot be read."""
    try:
        import tool_profiles
        return tool_profiles.transport_policy(os.environ.get("CLAUDE_CODE_TRANSPORT"))
    except Exception:
        return {}


def delegate_tool_grant() -> str:
    """The grant block, with the ai-worker bullet rendered against THIS machine and THIS child's route."""
    absent = _ai_worker_absent() or "ai-worker" in _mcp_policy().get("dropServers", [])
    return (DELEGATE_TOOL_GRANT_HEAD + DELEGATE_SEARCH_GRANT
            + (DELEGATE_AI_WORKER_ABSENT if absent else DELEGATE_AI_WORKER_GRANT) + DELEGATE_TOOL_GRANT_TAIL)


# Injected when the session runs on ANY non-Anthropic endpoint. One block, filled from the
# registry — adding a provider is a registry row, never an edit here.
#
# Built at injection time rather than stored as a constant, because the session must state WHICH
# MODEL it is. A rails block that says "you are on codex" without naming the model is the exact
# ambiguity this feature exists to remove.
#
# The routing rule below is load-bearing and belongs HERE specifically. A Workflow fan-out on this
# session is NOT band-gated — launching the session was the authorization, and a PreToolUse hook
# cannot deny one agent without killing the whole dispatch. Enforcement is replaced by INFORMED
# CHOICE, and SessionStart is the only surface that precedes the first dispatch; in orchestration
# §5 or the registry it would arrive after the decision it governs.
#
# The ROSTER is deliberately not inlined. workflow_provider_guard.py names every legal pin in its
# deny message, at the moment a wrong pin is attempted — always-loaded bytes buy nothing a
# temptation-time message does not (instruction_quality §5 A3).
PROVIDER_RAILS_HEAD = """\
[{transport} session — rails; canon: orchestration §0 (pin vocabulary) + §5b; roster: python3 .claude/tools/model_registry.py available]
- THIS SESSION IS DRIVING {driver} on the `{transport}` transport (identified by {source}).{driver_extra}
- Unpinned subagent spawns go to {subagent} (CLAUDE_CODE_SUBAGENT_MODEL), regardless of what drives this session.
- PIN THIS TRANSPORT'S OWN MODEL IDS, never Anthropic role names: they are an Anthropic session's vocabulary, and workflow_provider_guard.py DENIES them here, naming the ids this endpoint serves. A proxy may map a role name onto a provider row of ITS OWN, silently choosing a model you did not select. Sibling models on this transport ARE reachable in-harness by Workflow pin — no sidecar needed.{effort_note}
- A committed Workflow filename does not choose a provider. Reusable engines take this transport's native model and effort from caller args; measured arms may pin a literal model. Check the script's actual pins: the provider guard denies incompatible literal pins, not the whole file because it is committed. [instruction_quality §12; orchestration §5b]
- COVERAGE LIMIT of that deny: it reads statically visible pins in args, the Agent `model`, and quoted literals in the script. A pin COMPUTED at runtime can escape that guard; use native IDs in caller args and validate them before spawning, not literal pins in reusable scripts.
- Reaching ANOTHER transport (including Anthropic itself) goes through its own sidecar launcher — `reference/sidecar_dispatch.md`, roster above. Workflow/Agent never cross transports.
- You are a top-level session on this transport. Gate decisions and the ideal-design verdict still warrant an Anthropic session OR an explicit user sign-off — surface them rather than settling them alone. That floor reserves those DECISIONS, not the work shape (orchestration §5b): a SCOPED judgment, review or architecting lens goes to the ladder's row for that shape, by sidecar hop when it is off-transport.
- ROLE -> MODEL: `python3 .claude/tools/model_registry.py for-role <orchestrator|architect|executor|fanout|scout>` prints one plan-filtered table: each surviving plan's quota left and each row fit for the tier, with its ladder cells. A plan it dropped is out. It picks no model and no effort: choose among the listed rows by rule 1's bar, and take the effort from the ladder's effort cell for the work shape.{lacked_tiers}
- DISPATCH ROUTING: `/delegate` places each job by the ladder's selection line (orchestration §0), and a job placed on this session's plan runs as a Workflow agent. A Workflow fan-out here is NOT band-gated, so state the intended model and cost before dispatching, then check the journal's model column against it.
- COST SHAPE — bound prompts, use args.spillDir, and prefer FEW LONG agents to many short ones: each agent pays a large cold-start toll, so width multiplies it while depth amortizes it.
"""

# The fail-closed rails. Every pin is denied until the session says what it is, so the block's
# whole job is to name the one fix.
UNKNOWN_RAILS = """\
[UNIDENTIFIED ENDPOINT — rails; canon: orchestration §0 + hooks/_session_transport.py]
- This session's ANTHROPIC_BASE_URL matches no registered transport and CLAUDE_CODE_TRANSPORT is unset or unrecognized, so the harness cannot tell which models it can serve.
- Consequence: workflow_provider_guard.py DENIES every Workflow/Agent model pin. That is deliberate — on an unidentified endpoint a role name silently reaches whatever happens to be listening.
- Fix: export CLAUDE_CODE_TRANSPORT=<name> in the launcher or profile function that started this session; `python3 .claude/tools/model_registry.py available` lists the registered names.
"""

# Injected when the session runs on an Anthropic endpoint (orchestrator sessions).
# Mirror of DEEPSEEK_RAILS: pin translation is a deepseek-session mechanism, so an
# Anthropic session's role-name pins dispatch claude-* agents — expected, not a
# failure. External-model work needs a separate transport; the sidecar script is
# the deepseek one. Canon: orchestration §0 (Workflow and Agent stay on the current endpoint).
ANTHROPIC_RAILS = (
    "[anthropic session] Workflow/Agent reach claude-* models only; every other model runs "
    "through its sidecar launcher (`reference/sidecar_dispatch.md`).\n"
)


def sidecar_delegate_shape() -> str | None:
    """Sidecar DELEGATE mode, distinguished from a user-launched `claude-deepseek`
    ORCHESTRATOR session by CLAUDE_CODE_SIDECAR, which deepseek_sidecar.sh sets on
    the child only. Returns the requested guard shape, or None when this is not a
    sidecar child. Unrecognized shape -> `common`, matching dispatch.js: a shape the
    caller did not assert is not an error, it just means no shape applies. The launcher exports its
    `-G` kind as CLAUDE_CODE_SIDECAR_KIND; guards/tool_profiles.json maps it to the guard file set."""
    if not os.environ.get("CLAUDE_CODE_SIDECAR"):
        return None
    kind = (os.environ.get("CLAUDE_CODE_SIDECAR_KIND") or "").strip().lower()
    try:
        import tool_profiles
        guard = tool_profiles.kind_guard(kind)
    except Exception:
        guard = None
    return guard if guard in ("common", "survey", "review", "author", "gate") else "common"


TIER_LINE_STRICT = (
    "[session] Session tier: `detailed` — read every `## detailed` section you encounter in a "
    "skill, command or guard file."
)
# Output shape (length, voice) is the output style's, which binds every driving session.
TIER_LINE_OPEN = "[session] Session tier: `{tier}` — skip `## detailed` sections."


def tier_line(tier: str) -> str:
    """The one tier line every session gets after the effort line."""
    if tier not in ("condensed", "minimal"):
        return TIER_LINE_STRICT
    return TIER_LINE_OPEN.format(tier=tier)


def driver_notes_block(model) -> str:
    """The registry's per-model corrections for the model DRIVING this session, or "".

    Per-model guidance lives in `driverNotes` on the model's row and its transport's row, each
    note carrying the evidence that earned it, so a correction reaches only the model that showed
    the failure (instruction_quality §3, a correction takes the scope of its evidence).
    """
    try:
        import model_registry
        notes = model_registry.driver_notes(model)
        row = model_registry.row_for_model(model) if notes else None
    except Exception:
        return ""
    if not notes:
        return ""
    lines = [f"[session] Driver notes ({row['id'] if row else model}):"]
    lines += [f"- {text} ({ref})" for text, ref in notes]
    return "\n".join(lines)


def provider_rails(transport: str, source: str, payload: dict) -> str:
    """Fill PROVIDER_RAILS_HEAD from the registry, for ANY non-Anthropic transport.

    Degrades rather than disappears: an unusable registry still yields rails with the
    model-specific facts replaced by an explicit 'unknown' -- a session running blind on which
    model it is must be TOLD it is blind, not handed rails that quietly omit the fact.
    """
    driver = (payload.get("model") or "").strip()
    driver_extra = ""
    effort_note = ""
    lacked_tiers = ""
    subagent = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or "this session's own model"
    try:
        tools = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
        )
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import model_registry as registry

        data = registry.load()
        if driver:
            try:
                entry = registry.row_for_model(driver, data)
                if entry is None:
                    raise ValueError("unknown registry model")
                driver = entry["id"]
                gate = entry.get("gate") or {}
                if entry.get("authTier") == "gated":
                    driver_extra = (
                        " This is a GATED row (floor: band %s, balance $%s) - you are on it "
                        "because the user launched it deliberately."
                        % (gate.get("minBand", "?"), gate.get("minBalanceUSD", "?"))
                    )
                if (entry.get("effort") or {}).get("evidence") == "unmeasured":
                    driver_extra += (
                        " Its effort behavior is UNMEASURED - another row's effort findings do "
                        "NOT carry over, so any -e here is a guess rather than a defensible pin."
                    )
            except Exception:
                driver_extra = " (not a registry model - verify what is actually serving.)"
    except Exception as exc:
        driver_extra = " Registry unreadable (%s), so the model facts above are unavailable." % exc

    if not driver:
        # `model` is absent after /clear and on recovery; the endpoint still holds.
        driver = "a model this session cannot see (absent after /clear)"
        driver_extra += " Check the statusline for the live model before any costly dispatch."

    # Codex forces model AND effort at the PROXY SERVER, read once at startup, and no
    # Anthropic-API field carries effort -- so per-agent model works and per-agent effort cannot
    # (measured 2026-09-04). A session that pins effort per agent here would believe a knob that
    # does nothing.
    # WHICH TIERS THIS SEAT CANNOT SERVE, computed rather than written down: a hardcoded list
    # goes stale the day a row is added or excluded, and the stale version reads as authoritative.
    # Named at SessionStart because that is the only surface preceding the first delegation
    # decision -- after it, the seat has already picked whatever was in-transport.
    try:
        # `route_rows` owns in-transport availability, including seat-aware exclusions.
        missing = [t for t in registry.TIER_ORDER
                   if not registry.route_rows(t, transport, data)["inTransport"]]
        if missing:
            lacked_tiers = (
                " NO AVAILABLE %s ROW SERVES: %s — every dispatch at those tiers is one sidecar "
                "hop, and taking the nearest in-transport row instead is a silent tier trade."
                % (transport, ", ".join("`%s`" % t for t in missing)))
    except Exception:
        lacked_tiers = (" Which tiers this seat lacks could not be computed (registry unreadable) "
                        "— run the command above before assuming this seat covers one.")

    if transport == "codex":
        effort_note = (
            " `/model` and `/effort` both change this session live and reach the model, so the "
            "statusline is accurate. Name a GPT id in `/model`: a Claude role name "
            "(opus/sonnet/haiku) gets you a GPT row the proxy picks, not the one you asked for. "
            "A proxy started with CCP_CODEX_MODEL or CCP_CODEX_EFFORT set overrides every request "
            "silently — `python3 .claude/scripts/codex_effort_probe.py` reads which regime this "
            "session is in."
            + (" This session was launched at effort `%s`." % os.environ["HARNESS_SESSION_EFFORT"]
               if os.environ.get("HARNESS_SESSION_EFFORT") else
               " This session's launcher exported no HARNESS_SESSION_EFFORT, so the effort is unknown "
               "here: `python3 .claude/scripts/codex_effort_probe.py` reads it from the wire.")
        )

    return PROVIDER_RAILS_HEAD.format(
        transport=transport, source=source, driver=driver, driver_extra=driver_extra,
        subagent=subagent, effort_note=effort_note, lacked_tiers=lacked_tiers,
    )


def _effort_from_status_line(session_id) -> str:
    """The effort level the status line last saw for this session, or "".

    Only the status line's payload carries `effort.level`; no hook payload does. statusline.py
    writes the level to this file so a session can be told its real effort instead of an
    assumption (owner question 2026-09-15)."""
    if not session_id:
        return ""
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    try:
        with open(os.path.join(tempfile.gettempdir(), f"cc-effort-{safe}.txt"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def effort_line(payload: dict, delegate: bool = False) -> str:
    """Session-effort visibility (user directive 2026-07-28): models cannot reliably
    see their own effort, and Agent-tool dispatches inherit it invisibly — so state
    it when the harness exposes it, and state the safe assumption when it doesn't.
    Printed for EVERY session model (the rails filter applies only to RAILS).

    A sidecar DELEGATE gets the opposite advice: the dispatch rule that routes a job to Workflow is
    unreachable for it (deepseek_sidecar.sh withholds Task/Agent), so telling it how
    to pin fan-outs invites it to attempt a spawn it cannot make."""
    if delegate:
        return (
            "[session] You are a DELEGATE executing a brief, not an orchestrator. Subagent spawning "
            "is outside your tool grant, so execute the brief yourself and close by naming what you "
            "could not satisfy (orchestration §11)."
        )
    effort = None
    for key in ("effort", "reasoningEffort", "reasoning_effort"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            effort = value
            break
    if not effort:
        effort = _effort_from_status_line(payload.get("session_id"))
    pins = ("A dispatch pins its own model and effort, chosen by the model ladder's selection line "
            "(§0 dispatch shape, §5 pins).")
    if effort:
        return f"[session] Your session effort is '{effort}'. {pins}"
    return f"[session] Your session effort is not visible to you; assume nothing about it. {pins}"


def compaction_lines(payload):
    """The SessionStart:compact state lines: the compaction ordinal and previous boundary time, which skills
    survive, and, for a session that dispatched, the dispatch doctrine as `model_ladder_gate` applies it. The
    client writes the re-attached skills after this hook runs, so the skill line states the rule the guards apply
    (`_hook_state.skill_in_context`) and never names a skill as kept or cleared. An unreadable transcript prints
    the doctrine line too, since its dispatch history is unknown."""
    lines = []
    try:
        import _compaction_record
        ordinal, previous, dispatches = _compaction_record.position(payload.get("transcript_path") or "")
        lines.append("[compaction] Compaction %d of this session%s." % (
            ordinal, "; the previous one ended %s" % str(previous)[:19] + "Z" if previous else ""))
    except Exception as exc:
        dispatches = None
        lines.append("[compaction] The transcript's compaction record could not be read (%s): this compaction's "
                     "ordinal and the session's dispatch history are unknown." % type(exc).__name__)
    # The edit guard (harness_edit_skill_reminder.py) and the dispatch gate deny on a skill not in context; this
    # line is the earlier surface those denies back up (instruction_quality §3).
    lines.append("[compaction] A skill the client re-attached whole and unchanged on disk is still loaded; a "
                 "truncated or changed copy is not. Invoke that skill again before an action that needs it "
                 "(editing agent-facing harness text needs instruction_quality).")
    if dispatches != 0:
        try:
            import model_ladder_gate
            lines.append("[compaction] This session has dispatched. " + model_ladder_gate.doctrine_rule(payload))
        except Exception:
            pass
    return lines


# The interactive layer: what only a session the owner works with faces (`instruction_quality` §5). A dispatched
# child (CLAUDE_CODE_SIDECAR, or a native job whose payload carries `agent_id`) never receives it. A project that
# has not pulled the file prints nothing in its place.
INTERACTIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CLAUDE.interactive.md")


def interactive_text() -> str:
    try:
        with open(INTERACTIVE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# The commands this hook is registered as in settings.base.json: `--part 1` .. `--part PARTS`. The client caps each
# command's output at HOOK_TEXT_CAP, so `_hook_parts` slices the one whole text across them.
PARTS = 2


def build_text(payload: dict, write_tier: bool = True) -> str:
    """The whole text this hook prints, the same for every part. Only part 1 caches the session tier."""
    out = []
    shape = sidecar_delegate_shape()
    out.append(effort_line(payload, delegate=bool(shape)))
    if shape:
        # The child's model row decides, as for any session (`_model_tier`) and as a native seat's rails do.
        tier = _model_tier.tier_of(payload.get("model"))
    elif write_tier:
        tier = _model_tier.write_session_tier(payload.get("session_id"), payload.get("model"))
    else:
        tier = _model_tier.tier_of(payload.get("model"))
    out.append(tier_line(tier))
    if not shape:
        notes = driver_notes_block(payload.get("model"))
        if notes:
            out.append(notes)
        if payload.get("source") == "compact":
            out.extend(compaction_lines(payload))
    if shape:
        # DELEGATE, not orchestrator: DEEPSEEK_RAILS opens "You ARE the orchestrator
        # this session", which is actively wrong for a child that cannot even spawn
        # (deepseek_sidecar.sh disallows Task/Agent by default).
        try:
            rails = guard_text(shape, tier) if guard_text else ""
            if rails:
                out.append(rails)
        except Exception:
            pass  # advisory hook — a missing/malformed guard file must not brick the child
        # Unconditional for a delegate: it corrects CLAUDE.core.md §9 as much as RAILS, so it
        # must survive the lean-model early return below.
        out.append(delegate_tool_grant())
        # A delegate's corrections are keyed to the CHILD's model, not the parent's: a dispatched
        # luna lane needs the luna notes, and the payload of its own session start carries them.
        # Printed here rather than in the `not shape` branch above, so the delegate gets its rails
        # and grant first and one notes block, not two.
        notes = driver_notes_block(payload.get("model"))
        if notes:
            out.append(notes)
    elif _session_transport is None:
        out.append(ANTHROPIC_RAILS)  # resolver unavailable: the host transport is the safe rails to emit
    else:
        transport, source = _session_transport.resolve()
        if transport == _session_transport.UNKNOWN:
            out.append(UNKNOWN_RAILS)
        elif transport == _session_transport.HOST_TRANSPORT:
            out.append(ANTHROPIC_RAILS)
        else:
            out.append(provider_rails(transport, source, payload))
    if not shape and not in_delegate(payload, os.environ):
        layer = interactive_text()
        if layer:
            out.append(layer)
        if _outcomes is not None:
            try:
                sweep = _outcomes.unswept_summary(_outcomes.DEFAULT_FILE)
                if sweep:
                    out.append(sweep)
            except Exception:
                pass
    return "".join(piece + "\n" for piece in out)


def _overflow_note(dropped: int, model="") -> str:
    """The line that ends the last part when the text outgrew PARTS: what is missing and where to read it."""
    shape = sidecar_delegate_shape()
    where = (" Read the whole rails: `python3 .claude/tools/guard_text.py %s auto %s`." % (shape, model or "<your model>")
             if shape else " Raise PARTS in hooks/session_model_rails.py and register the added parts.")
    return "[rails overflow] The last %d lines of this session-start text did not fit the registered parts.%s\n" % (
        dropped, where)


def part_text(text: str, n: int, model="") -> str:
    """Part `n` of `text` across PARTS registered commands, ending with the overflow note when it outgrew them."""
    return _hook_parts.part_of(text, n, PARTS, overflow=lambda dropped: _overflow_note(dropped, model))


def main(argv=None) -> None:
    """`--part N` prints part N of the whole text; no argument prints the whole text (a proof or a read by hand)."""
    argv = sys.argv[1:] if argv is None else argv
    part = int(argv[argv.index("--part") + 1]) if "--part" in argv else None
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    text = build_text(payload, write_tier=part in (None, 1))
    sys.stdout.write(text if part is None else part_text(text, part, payload.get("model") or ""))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
