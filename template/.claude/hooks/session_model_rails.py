#!/usr/bin/env python3
"""SessionStart: inject session-effort visibility (all models), delegate guard
rails + tool-grant correction for sidecar CHILD sessions, dispatch-transport rails
(one transport-parameterized block for every non-Anthropic ORCHESTRATOR session, the
mirror for Anthropic ones, and a fail-closed block when the endpoint cannot be identified),
the re-pitch protocol for opus sessions, and strict tool-routing rails for non-orchestrator
session models.

Which transport a session is on is resolved ONCE, in hooks/_session_transport.py, and shared
with workflow_provider_guard.py. Adding a provider is a registry row plus a launcher that
exports CLAUDE_CODE_TRANSPORT -- never an edit here.

Delegate vs orchestrator is decided by CLAUDE_CODE_SIDECAR, which deepseek_sidecar.sh
and codex_proxy_sidecar.sh set on the child only — the two need opposite rails, and a
delegate reading "You ARE the orchestrator this session" is worse than none.

A delegate's rails carry `guards/any.md` AND its shape file, assembled by
tools/guard_text.py. `any` is concatenated rather than chained: it holds the rules
binding every delegate, so its delivery must not depend on the child choosing to follow
a pointer it finds inside another file. On the -D bare/pointer tiers no project hook fires,
so lib/sidecar_common.sh calls guard_text.py directly — which is why that parse is a shared
module rather than a function here.

Tool-routing prohibitions are NOT injected here. They live once in CLAUDE.md §Tool Routing,
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

Fail posture: advisory context hook — fail open (exit 0, silent) on any error.
"""
import json
import os
import sys

# Windows consoles default stdout to cp1252; rails text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

# The guard tier parse lives once, in tools/guard_text.py, because the sidecar bash lib
# calls that same module as a CLI on the -D bare/pointer tiers, where no hook fires and it
# must assemble the rails itself. Precedent: budget_posture.py.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
try:
    from guard_text import guard_text
except Exception:  # advisory hook: a broken import must not brick a delegate child
    guard_text = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _model_tier
try:
    import _session_transport  # noqa: E402
except Exception:
    # Advisory hook, stated fail posture: fail open. An unguarded import here throws BEFORE
    # main()'s try/except and costs the session its whole rails block -- tier line and
    # delegate tool-grant included -- over a helper only the transport branch needs.
    _session_transport = None

# Model ids that run with the lean always-loaded surface (no rails injection).

# Correction printed AFTER RAILS to a sidecar child only. RAILS (and CLAUDE.md §Tool Routing, injected into the
# same child) route some lookups to a tool the child's grant lacks — it obeys an instruction naming an
# absent tool and silently changes search behavior instead. The grant is decided by which MCP servers
# CONNECT, not by auth: semantic-search connects at init; ai-worker connects a turn or two later, so a short
# dispatch can still miss it.
# Canon: gotcha_sidecar_child_mcp_tool_grant.md.
DELEGATE_TOOL_GRANT = """\
[delegate tool grant — overrides tool routing wherever CLAUDE.md §Tool Routing or a rails block names an absent tool; canon: gotcha_sidecar_child_mcp_tool_grant]
- semantic-search IS available here (mcp__plugin_semantic-search_semantic-search__search, same name as a normal session). Route to it exactly as CLAUDE.md §Tool Routing directs — do NOT substitute Grep.
- ai-worker IS available too (mcp__ai-worker__read_files / write_doc), though it can connect a turn or two after init — a short dispatch may not see it yet. Route to it exactly as CLAUDE.md §Worker Model Delegation directs. Only if a call fails with tool-not-found: substitute bounded Read (offset/limit) of the named files and say so in your deliverable.
- Verify a tool exists before routing to it. Never report "not found" on the strength of a call that never ran.
"""

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
- PIN THIS TRANSPORT'S OWN MODEL IDS, never Anthropic role names. `opus`/`sonnet`/`haiku`/`fable` are ANTHROPIC-session vocabulary and workflow_provider_guard.py DENIES them here, naming the legal ids. The proxy would in fact map them onto a tier of ITS OWN (measured: `opus` -> `gpt-5.6-sol`) — which is the reason for the deny, not an argument against it: the pin would silently name a model you did not choose. Sibling models on this transport ARE reachable in-harness by Workflow pin — no sidecar needed.{effort_note}
- Committed `.claude/workflows/*.js` scripts pin Anthropic role names and are Anthropic-session tools: dispatching one here is denied. Write the pins for this transport, or run that workflow from an Anthropic session.
- COVERAGE LIMIT of that deny: it reads pins that are statically visible — args, the Agent `model`, and quoted literals in the script (a `PIN('x')` wrapper included). A pin COMPUTED at runtime (a variable, a map lookup, a template literal) is not seen and not denied, so it reaches the endpoint and returns a null agent. Write pins as literals in scripts you run here.
- Reaching ANOTHER transport (including Anthropic itself) goes through its own sidecar launcher — `reference/sidecar_dispatch.md`, roster above. Workflow/Agent never cross transports.
- You ARE the orchestrator this session. Gate decisions and the ideal-design verdict still warrant an Anthropic session OR an explicit user sign-off — surface them rather than settling them alone. That floor reserves those DECISIONS, not the work shape (orchestration §5b): a SCOPED judgment, review or architecting lens goes to the ladder's row for that shape, by sidecar hop when it is off-transport.
- ROLE -> MODEL: `python3 .claude/tools/model_registry.py for-role <orchestrator|executor|fanout|scout>` prints what serves a tier here, what serves it one hop away with the launcher line, and the nearest in-transport row when nothing here does.{lacked_tiers}
- DISPATCH ROUTING is a judgment you own: a Workflow fan-out here is NOT band-gated, so state the intended model and cost before dispatching, then check the journal's model column against it.
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
# the deepseek one. Canon: orchestration §0 *Dispatch is transport-bound*.
ANTHROPIC_RAILS = """\
[anthropic session — dispatch transport; canon: orchestration §0 + §5b]
- Workflow/Agent subagents run on this session's transport only: role-name pins (opus/sonnet/fable) dispatch claude-* agents — expected, not a pin-translate failure (the translate hook fires only on deepseek sessions).
- No external model (GPT, opencode, deepseek, local) is reachable via Workflow/Agent from here — every one dispatches through its transport's sidecar launcher: one recipe for all, `reference/sidecar_dispatch.md` (auto-injected on the launch call). Roster: `python3 .claude/tools/model_registry.py available`; excluded models are out — re-select under the ladder, never substitute by rule.
- A vendor-model literal in an agent() pin here returns null agents and a fan-out reading "0 findings", so workflow_provider_guard.py DENIES one outright; verify each dispatch's journal models against the provider you intended.
"""


def sidecar_delegate_shape() -> str | None:
    """Sidecar DELEGATE mode, distinguished from a user-launched `claude-deepseek`
    ORCHESTRATOR session by CLAUDE_CODE_SIDECAR, which deepseek_sidecar.sh sets on
    the child only. Returns the requested guard shape, or None when this is not a
    sidecar child. Unrecognized shape -> `any`, matching dispatch.js: a shape the
    caller did not assert is not an error, it just means no shape applies."""
    if not os.environ.get("CLAUDE_CODE_SIDECAR"):
        return None
    shape = (os.environ.get("CLAUDE_CODE_SIDECAR_SHAPE") or "").strip().lower()
    return shape if shape in ("any", "survey", "review", "author") else "any"


def sidecar_delegate_tier() -> str:
    """How verbosely this delegate's rails are spelled out, mirroring dispatch.js TIER_OF.

    Read from the environment rather than fixed here so that adding an opus-class external
    model is a one-line export in its launcher, not a hook edit. No launcher sets it today, and
    no currently-selectable external row claims a tier above the cheap one -- terra and sol are
    registered `roles: []` (unmeasured), so `strict` stays correct until a battery says otherwise.
    Check `model_registry.py available` rather than trusting this sentence's roster.
    Anything unset or unrecognized therefore falls to `strict` — the safe direction, since an
    over-explicit rail costs bytes while an under-explicit one costs adherence.
    """
    tier = (os.environ.get("CLAUDE_CODE_SIDECAR_TIER") or "").strip().lower()
    return tier if tier in ("strict", "terse", "none", "fable") else "strict"


# Guard-text tier (how verbosely a delegate's rails are spelled) -> session tier (which
# `## strict` bodies it reads). `none` means the launcher already judged the child capable
# enough to need no spelled-out rails, so it reads at the top tier.
_DELEGATE_SESSION_TIER = {"fable": "fable", "none": "fable", "terse": "opus", "strict": "strict"}

TIER_LINE_STRICT = (
    "[session] Session tier: `strict` — read every `## strict` section you encounter in a "
    "skill, command or guard file."
)
TIER_LINE_OPEN = (
    "[session] Session tier: `{tier}` — skip `## strict` sections.\n"
    "- Default to the shortest response that fully answers, with two exemptions: a line up front "
    "saying what you're about to do, and a closing recap that stands on its own for a reader who "
    "saw none of the work.\n"
    "- Voice is word choice inside a sentence you already needed — no aphorism, no rhetorical "
    "question, no sentence whose job is to sound good."
)
TIER_LINE_FABLE_EXTRA = (
    "\n- Recognizing a name is not knowing its current state — verify before answering, and "
    "include the name as written in at least one search."
)


def tier_line(tier: str) -> str:
    """The one tier line (plus its tier-only clauses) every session gets after the effort line."""
    if tier not in ("opus", "fable"):
        return TIER_LINE_STRICT
    text = TIER_LINE_OPEN.format(tier=tier)
    return text + TIER_LINE_FABLE_EXTRA if tier == "fable" else text


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
                entry = registry.resolve(driver, data)
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
        tiers = registry.role_tiers()
        # `for_role`, not a second in-transport filter: that resolver already owns what counts as
        # in-transport (seat-aware availability included), and a copy here would drift from it
        # silently -- this line would keep printing a tier the resolver had stopped offering.
        missing = [t for t in registry.TIER_ORDER
                   if not registry.for_role(t, transport, data, tiers)["inTransport"]]
        if missing:
            lacked_tiers = (
                " NO AVAILABLE %s ROW SERVES: %s — every dispatch at those tiers is one sidecar "
                "hop, and taking the nearest in-transport row instead is a silent tier trade."
                % (transport, ", ".join("`%s`" % t for t in missing)))
    except Exception:
        lacked_tiers = (" Which tiers this seat lacks could not be computed (registry or ladder "
                        "unreadable) — run the command above before assuming this seat covers one.")

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
               " This session's launcher exported no HARNESS_SESSION_EFFORT, so the rung is unknown "
               "here: `python3 .claude/scripts/codex_effort_probe.py` reads it from the wire.")
        )

    return PROVIDER_RAILS_HEAD.format(
        transport=transport, source=source, driver=driver, driver_extra=driver_extra,
        subagent=subagent, effort_note=effort_note, lacked_tiers=lacked_tiers,
    )


def effort_line(payload: dict, delegate: bool = False) -> str:
    """Session-effort visibility (user directive 2026-07-28): models cannot reliably
    see their own effort, and Agent-tool dispatches inherit it invisibly — so state
    it when the harness exposes it, and state the safe assumption when it doesn't.
    Printed for EVERY session model (the rails filter applies only to RAILS).

    A sidecar DELEGATE gets the opposite advice: the Workflow-first dispatch rule is
    unreachable for it (deepseek_sidecar.sh withholds Task/Agent), so telling it how
    to pin fan-outs invites it to attempt a spawn it cannot make."""
    if delegate:
        return (
            "[session] You are a DELEGATE executing a brief, not an orchestrator. Your effort "
            "was chosen by the dispatcher — do not reason about it, and do not try to fan out: "
            "subagent spawning is outside your tool grant. Execute the brief yourself and close "
            "by naming what you could not satisfy (orchestration §11)."
        )
    effort = None
    for key in ("effort", "reasoningEffort", "reasoning_effort"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            effort = value
            break
    if effort:
        return (
            f"[session] Your session effort is '{effort}'. Delegates never inherit it: "
            "fan-outs and judgment stages dispatch via Workflow with explicit model+effort "
            "pins (orchestration §0 Workflow-first, §5 pins)."
        )
    return (
        "[session] Your session effort is not visible to you — assume nothing about it. "
        "Fan-outs and judgment stages dispatch via Workflow with explicit model+effort pins; "
        "never let a dispatch inherit session effort (orchestration §0 Workflow-first, §5 pins)."
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    shape = sidecar_delegate_shape()
    print(effort_line(payload, delegate=bool(shape)))
    if shape:
        tier = _DELEGATE_SESSION_TIER.get(sidecar_delegate_tier(), "strict")
    else:
        tier = _model_tier.write_session_tier(payload.get("session_id"), payload.get("model"))
    print(tier_line(tier))
    if shape:
        # DELEGATE, not orchestrator: DEEPSEEK_RAILS opens "You ARE the orchestrator
        # this session", which is actively wrong for a child that cannot even spawn
        # (deepseek_sidecar.sh disallows Task/Agent by default).
        try:
            rails = guard_text(shape, sidecar_delegate_tier()) if guard_text else ""
            if rails:
                print(rails)
        except Exception:
            pass  # advisory hook — a missing/malformed guard file must not brick the child
        # Unconditional for a delegate: it corrects CLAUDE.md §Tool Routing as much as RAILS, so it
        # must survive the lean-model early return below.
        print(DELEGATE_TOOL_GRANT)
    elif _session_transport is None:
        print(ANTHROPIC_RAILS)  # resolver unavailable: the host transport is the safe rails to emit
    else:
        transport, source = _session_transport.resolve()
        if transport == _session_transport.UNKNOWN:
            print(UNKNOWN_RAILS)
        elif transport == _session_transport.HOST_TRANSPORT:
            print(ANTHROPIC_RAILS)
        else:
            print(provider_rails(transport, source, payload))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
