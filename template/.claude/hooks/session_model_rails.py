#!/usr/bin/env python3
"""SessionStart: inject session-effort visibility (all models), delegate guard
rails + tool-grant correction for sidecar CHILD sessions, dispatch-transport rails
(compat-endpoint rails for DeepSeek ORCHESTRATOR sessions, the mirror for
Anthropic ORCHESTRATOR sessions), the re-pitch protocol for opus sessions, and
strict tool-routing rails for non-orchestrator session models.

Delegate vs orchestrator is decided by CLAUDE_CODE_SIDECAR, which deepseek_sidecar.sh
and codex_proxy_sidecar.sh set on the child only — the two need opposite rails, and a
delegate reading "You ARE the orchestrator this session" is worse than none.

A delegate's rails carry `guards/any.md` AND its shape file, assembled by
tools/guard_text.py. `any` is concatenated rather than chained: it holds the rules
binding every delegate, so its delivery must not depend on the child choosing to follow
a pointer it finds inside another file. On the -D bare/pointer tiers no project hook fires,
so lib/sidecar_common.sh calls guard_text.py directly — which is why that parse is a shared
module rather than a function here.

Tool-routing prohibitions are NOT injected here. They live once in CLAUDE.md §9,
in the NEVER-list form this hook used to supply (moved 2026-08-17): CLAUDE.md is
already injected into every session, so a second delivery path to the same audience
bought nothing — and the old model gate exempted fable, leaving the orchestrator tier
relying on §9 alone anyway. The form was the value, not the channel; §9 now carries it
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

# Model ids that run with the lean always-loaded surface (no rails injection).

# Correction printed AFTER RAILS to a sidecar child only. RAILS (and CLAUDE.md §9, injected into the
# same child) route some lookups to a tool the child's grant lacks — it obeys an instruction naming an
# absent tool and silently changes search behavior instead. The grant is decided by which MCP servers
# CONNECT, not by auth: measured 2026-08-05, semantic-search connects normally in a sidecar child while
# ai-worker is still `pending` at init with no tools granted.
# Canon: gotcha_sidecar_child_mcp_tool_grant.md.
DELEGATE_TOOL_GRANT = """\
[delegate tool grant — overrides tool routing wherever CLAUDE.md §9 or a rails block names an absent tool; canon: gotcha_sidecar_child_mcp_tool_grant]
- semantic-search IS available here (mcp__plugin_semantic-search_semantic-search__search, same name as a normal session). Route to it exactly as CLAUDE.md §9 directs — do NOT substitute Grep.
- The ai-worker tools are NOT: no mcp__ai-worker__* tool is in this session's grant. Substitute bounded Read of the named files for read_files/read_web, and say so in your deliverable.
- Verify a tool exists before routing to it. Never report "not found" on the strength of a call that never ran.
"""

# Injected only when the session runs against DeepSeek's Anthropic-compatible
# endpoint (`claude-deepseek*`). Model/effort translation is MECHANICAL
# (hooks/model_pin_translate.py), so these rails cover only what translation
# cannot decide: WHICH ROLE to pin, and the reserved floor.
#
# Built at injection time rather than stored as a constant, because with two
# DeepSeek tiers the session must state WHICH ONE IT IS. A rails block that says
# "you are on DeepSeek" without naming the tier is the ambiguity this feature
# exists to remove.
#
# The pro-to-pro routing rule below is load-bearing and belongs HERE specifically.
# A Workflow fan-out on this session is NOT band-gated — launching the session was
# the authorization, and a PreToolUse hook cannot deny one agent without killing
# the whole dispatch. Enforcement is therefore replaced by INFORMED CHOICE, and
# SessionStart is the only surface that precedes the first dispatch. In orchestration
# §5 or the registry it would arrive after the decision it governs.
DEEPSEEK_RAILS_HEAD = """\
[compat-endpoint session — rails; canon: reference/model_ladder_evidence.md §Role guidance + orchestration §5b]
- THIS SESSION IS DRIVING {driver}.{driver_extra}
- Unpinned subagent spawns go to {subagent} (CLAUDE_CODE_SUBAGENT_MODEL), regardless of what drives this session. Reaching the expensive tier is always deliberate.
- Role pins resolve on this transport as: {rolemap}. Prices per 1M: {prices}.
- You ARE the orchestrator this session — the ladder row's "never the reserved floor" clause (reference/model_ladder_evidence.md §Role guidance) describes the I/O-worker tier as a DELEGATE, not this session. Gate decisions and the ideal-design verdict still warrant an Anthropic session OR an explicit user sign-off — surface them rather than settling them alone.
- DISPATCH ROUTING — pin `opus` (which resolves to the expensive tier) ONLY for large-scope architecting and complex cross-domain work. Everything else — surveys, checklist passes, mechanical authoring, validation verdicts, scoped execution under a converged spec — pins `sonnet` and resolves to the cheap tier. A Workflow fan-out here is NOT band-gated, so this is a judgment you own: state the intended tier and cost before dispatching, then check the journal's model column against it.
- COST SHAPE — the expensive tier's bill is dominated by FRESH tokens and output, not cache reads. Bound prompts, use args.spillDir, and prefer FEW LONG agents to many short ones: each agent pays a ~56K-token cold-start toll, so width multiplies it while depth amortizes it. Chasing cache hit rate has no headroom left (95.6% measured on cheap-tier delegate runs; the expensive tier's cache profile is UNMEASURED).
- Keep pinning ANTHROPIC role names (opus/sonnet/fable) on every dispatch. PreToolUse hooks/model_pin_translate.py resolves them per-role to the vendor model id and remaps effort to the vendor scale before the call runs. Do NOT hand-pin the vendor model id — the engines validate against the Anthropic vocabulary and will reject it.
- Translation covers Workflow (args + inline script). The Agent tool's `model` pin is STRIPPED instead: its enum carries BARE role names, which HARD-ERROR on this endpoint — a passed-through pin returns a null agent that .filter(Boolean) swallows into "0 findings". Agent spawns therefore fall through to {subagent} and are NOT tier-preserved; use Workflow when the tier matters.
- Translation CANNOT reach a workflow script that pins a literal without PIN(). If a dispatch report shows a claude-* model, or the hook warns of a surviving literal, stop and fix the script.
"""

# Injected when the session runs on an Anthropic endpoint (orchestrator sessions).
# Mirror of DEEPSEEK_RAILS: pin translation is a deepseek-session mechanism, so an
# Anthropic session's role-name pins dispatch claude-* agents — expected, not a
# failure. External-model work needs a separate transport; the sidecar script is
# the deepseek one. Canon: orchestration §0 *Dispatch is transport-bound*.
ANTHROPIC_RAILS = """\
[anthropic session — dispatch transport; canon: orchestration §5b]
- Workflow/Agent subagents run on this session's transport only: role-name pins (opus/sonnet/fable) dispatch claude-* agents — expected, not a pin-translate failure (the translate hook fires only on deepseek sessions).
- No external model (GPT, opencode, deepseek, local) is reachable via Workflow/Agent from here — every one dispatches through its transport's sidecar launcher: one recipe for all, `reference/sidecar_dispatch.md` (auto-injected on the launch call). Roster: `python3 .claude/tools/model_registry.py available`; excluded models are out — re-select under the ladder, never substitute by rule.
- A vendor-model literal in an agent() pin here returns null agents and a fan-out reading "0 findings" — the pin-translate hook warns on it; verify each dispatch's journal models against the provider you intended.
{sidecar_line}
"""


def sidecar_rail_line() -> str:
    """Transport-agnostic since the recipe moved to reference/sidecar_dispatch.md; kept as a
    format slot so the template needs no reshuffle. Returns nothing."""
    return ""


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
    model is a one-line export in its launcher, not a hook edit. No launcher sets it today:
    the roster's only external model is `luna` (registry roles sonnet,haiku) and DeepSeek sits
    in the same band, so `strict` is correct for every transport that currently dispatches.
    Anything unset or unrecognized therefore falls to `strict` — the safe direction, since an
    over-explicit rail costs bytes while an under-explicit one costs adherence.
    """
    tier = (os.environ.get("CLAUDE_CODE_SIDECAR_TIER") or "").strip().lower()
    return tier if tier in ("strict", "terse", "none") else "strict"


def deepseek_rails(payload: dict) -> str:
    """Fill DEEPSEEK_RAILS_HEAD from the registry.

    Degrades rather than disappears: an unusable registry still yields rails with
    the tier-specific facts replaced by an explicit 'unknown' — a session running
    blind on which model it is must be TOLD it is blind, not handed rails that
    quietly omit the fact.
    """
    driver = (payload.get("model") or "").strip()
    driver_extra = ""
    subagent = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or "the cheap tier"
    rolemap = "unknown (registry unreadable)"
    prices = "unknown (registry unreadable)"
    try:
        tools = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
        )
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import model_registry as registry

        data = registry.load()
        rolemap = ", ".join(
            "%s->%s" % (r, m) for r, m in sorted(registry.role_map("deepseek", data).items())
        )
        prices = "; ".join(
            "%s hit $%s / fresh $%s / out $%s"
            % (
                e["alias"],
                e["price"]["cacheHitPer1M"],
                e["price"]["cacheMissPer1M"],
                e["price"]["outputPer1M"],
            )
            for e in registry.models_for("deepseek", data)
        )
        if driver:
            try:
                entry = registry.resolve(driver, data)
                driver = "%s [%s]" % (entry["id"], entry["version"])
                gate = entry.get("gate") or {}
                if entry.get("authTier") == "gated":
                    driver_extra = (
                        " This is the GATED tier (floor: band %s, balance $%s) — you are on it "
                        "because the user launched it deliberately."
                        % (gate.get("minBand", "?"), gate.get("minBalanceUSD", "?"))
                    )
                if (entry.get("effort") or {}).get("evidence") == "unmeasured":
                    driver_extra += (
                        " Its effort behavior is UNMEASURED — the cheap tier's effort findings "
                        "do NOT carry over."
                    )
            except Exception:
                driver_extra = " (not a registry model — verify what is actually serving.)"
    except Exception as exc:
        driver_extra = " Registry unreadable (%s), so the tier facts below are unavailable." % exc

    if not driver:
        # `model` is absent after /clear and on recovery; the endpoint still holds.
        driver = "a DeepSeek model whose id this session cannot see (absent after /clear)"
        driver_extra += " Check the statusline for the live model before any costly dispatch."

    return DEEPSEEK_RAILS_HEAD.format(
        driver=driver, driver_extra=driver_extra,
        subagent=subagent, rolemap=rolemap, prices=prices,
    )


def is_deepseek_session(payload: dict) -> bool:
    """Endpoint is the load-bearing signal — the `model` field is absent after
    /clear and on recovery, where the base URL still holds. Either marker wins."""
    if "deepseek" in (payload.get("model") or "").lower():
        return True
    return "deepseek" in os.environ.get("ANTHROPIC_BASE_URL", "").lower()


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
            "pins (orchestration §5, Workflow-first)."
        )
    return (
        "[session] Your session effort is not visible to you — treat it as expensive/unknown. "
        "Fan-outs and judgment stages dispatch via Workflow with explicit model+effort pins; "
        "never let a dispatch inherit session effort (orchestration §5, Workflow-first)."
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    shape = sidecar_delegate_shape()
    print(effort_line(payload, delegate=bool(shape)))
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
        # Unconditional for a delegate: it corrects CLAUDE.md §9 as much as RAILS, so it
        # must survive the lean-model early return below.
        print(DELEGATE_TOOL_GRANT)
    elif is_deepseek_session(payload):
        print(deepseek_rails(payload))
    else:
        print(ANTHROPIC_RAILS.format(sidecar_line=sidecar_rail_line()))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
