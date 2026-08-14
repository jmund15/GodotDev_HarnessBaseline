#!/usr/bin/env python3
"""PreToolUse(Workflow|Agent): translate Anthropic model pins to the session's
actual endpoint, PRESERVING TIER.

Anthropic model names are the harness's canonical role vocabulary — CLAUDE.md's
ladder, every command, every workflow script, and Claude's own dispatch reasoning
speak `sonnet`/`opus`/`fable` regardless of which endpoint is serving. DeepSeek is
a COMPILE TARGET resolved here, at one point, so no second vocabulary exists
anywhere else in the harness.

Role -> model resolution comes from .claude/reference/external_models.json, never
from a constant in this file. Today that means opus -> V4 Pro and
sonnet/haiku/fable -> V4 Flash; tomorrow it means whatever the registry says. A
hardcoded model id here was the pre-2026-08-12 behavior and it collapsed every
tier onto flash, silently downgrading every opus-pinned lens in the harness.

Why a hook and not the scripts themselves: the Workflow sandbox exposes exactly
[log, phase, console, budget, setTimeout, clearTimeout, Date, agent, parallel,
pipeline, workflow, args] — no `process`, no env, no `require` (probed 2026-08-03).
A script cannot detect its endpoint; `args` is its only inbound channel. This hook
has env and can rewrite tool input via `hookSpecificOutput.updatedInput`, so it is
the only place the two facts can meet.

Why INJECT rather than rewrite args: committed workflows dispatch by `name:`, so
their source never appears in the tool input — no amount of text rewriting reaches
them. Injecting `args.__pin` does, and the scripts' one-line `PIN()` resolver
applies it at the `agent()` call. `__pin.roles` is the per-role map; `__pin.model`
is retained only as a fallback for a resolver not yet updated to read `roles`.

MEASURED ENDPOINT BEHAVIOR (2026-08-12) — the two facts that shape this file:
  * BARE role names HARD-ERROR. `opus`, `sonnet`, `haiku`, `fable` are all rejected
    with invalid_request_error. A bare role that survives to the wire produces a
    null agent, which `.filter(Boolean)` swallows into "0 findings" — a fan-out
    that looks clean and ran nothing. This is why the Agent pin is STRIPPED.
  * FULL `claude-*` ids alias BY TIER: claude-opus-* -> V4 Pro,
    claude-sonnet-*/haiku-*/fable-* -> V4 Flash. So a `claude-*` capture must be
    NORMALIZED to its role before registry lookup; treating it as an unknown role
    and falling through to the cheap tier would silently downgrade a pin that the
    vendor alias was already resolving correctly.

The silent-failure symmetry this exists to kill (both probed 2026-08-03):
  deepseek session + Anthropic literal -> bare role hard-errors (null agent, "0
    findings"); a full claude-* id aliases by tier, unpriced and unstated.
  Anthropic session + deepseek literal -> `agent()` returns None rather than
    throwing; `.filter(Boolean)` in the engines swallows it into "0 findings".
Neither direction raises. Convention cannot fix a failure with no signal; the
translation must be automatic.

On an Anthropic session this hook emits NO updatedInput and never rewrites —
normal sessions are byte-identical to having no hook at all. It still warns when
an agent() `model:` literal or args.__pin names a vendor model this endpoint
cannot serve: the fix there is a mechanism change — the sidecar script — never a
pin. Canon: CLAUDE.md §Model Delegation *Dispatch is transport-bound*.

Fail posture: cost/routing advisory — fail OPEN (exit 0) on any error, INCLUDING an
unusable registry, but NEVER fail silent. A degraded run falls back to flash-for-
everything and says so in additionalContext. Silent permanent degradation is the
same unsignalled downgrade this file exists to remove.

CHANNEL: every note goes to hookSpecificOutput.additionalContext on stdout with
exit 0. On PreToolUse, stderr is a dead channel — the model never sees it.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TRANSPORT = "deepseek"
FALLBACK_MODEL = "deepseek-v4-flash"  # used ONLY when the registry is unusable

ROLES = ("opus", "sonnet", "haiku", "fable")

# Anthropic role names in a `model:` position. `claude-*` covers the compat
# layer's tier-alias surface; those captures are normalized to a role below.
ANTHROPIC_NAMES = r"opus|sonnet|haiku|fable|claude-[A-Za-z0-9._-]+"
# Backticks included: `model: `sonnet`` is legal JS and would otherwise pass through
# unrewritten AND unwarned (caught by the channel test, 2026-08-03).
QUOTE = r"['\"`]"
SCRIPT_PIN_RE = re.compile(
    r"(\bmodel\s*:\s*)(" + QUOTE + r")(" + ANTHROPIC_NAMES + r")\2"
)
SCRIPT_EFFORT_RE = re.compile(
    r"(\beffort\s*:\s*)(" + QUOTE + r")(low|medium|high|xhigh)\2"
)

# Anthropic-session mirror: a `model:` literal this endpoint cannot serve makes
# `agent()` return None and the engines read "0 findings". `PIN('sonnet')` is safe
# (an identifier precedes the quote); `claude-*` ids are valid Anthropic models.
VENDOR_PIN_RE = re.compile(
    r"(\bmodel\s*:\s*)(" + QUOTE + r")(?!sonnet|opus|haiku|fable|claude-)([A-Za-z0-9._-]+)\2"
)


def is_deepseek_session() -> bool:
    """Endpoint is the only reliable signal. The sidecar exports ANTHROPIC_BASE_URL
    for the child process, so it holds for the whole session including after /clear."""
    return "deepseek" in os.environ.get("ANTHROPIC_BASE_URL", "").lower()


def role_of(name: str):
    """Normalize a captured pin to an Anthropic ROLE, or None.

    Load-bearing: ANTHROPIC_NAMES captures full `claude-*` ids as well as bare
    roles, and `claude-opus-5` is not a role_map key. Looking it up raw and
    falling back to the open tier would turn a correct pro resolution into flash —
    a silent DOWNGRADE introduced by the very code meant to remove ambiguity.
    """
    if name in ROLES:
        return name
    low = name.lower()
    if low.startswith("claude-"):
        for role in ROLES:
            if low.startswith("claude-" + role):
                return role
    return None


class Resolution:
    """Registry-backed role resolution, with an explicit degraded mode."""

    def __init__(self):
        self.degraded = None
        self.meta = {}
        self.roles = {r: FALLBACK_MODEL for r in ROLES}
        self.default = FALLBACK_MODEL
        self.effort = {"low": "low", "medium": "low", "high": "max", "xhigh": "max"}
        try:
            tools = Path(__file__).resolve().parents[1] / "tools"
            if str(tools) not in sys.path:
                sys.path.insert(0, str(tools))
            import model_registry as registry

            data = registry.load()
            self.roles = registry.role_map(TRANSPORT, data)
            self.default = registry.open_tier_model(TRANSPORT, data) or FALLBACK_MODEL
            self.meta = {e["id"]: e for e in registry.models_for(TRANSPORT, data)}
            # Anthropic's four rungs collapse onto the vendor's two-cell doctrine.
            # Derived from the open-tier entry, whose effort evidence is `measured`.
            cheap = self.meta.get(self.default, {}).get("effort") or {}
            conv, opn = cheap.get("converged", "low"), cheap.get("open", "max")
            self.effort = {"low": conv, "medium": conv, "high": opn, "xhigh": opn}
        except Exception as exc:
            self.degraded = (
                "REGISTRY UNAVAILABLE (%s) — falling back to %s for EVERY role. "
                "Tier preservation is OFF: an opus-pinned lens is now running on the "
                "cheap tier. This is a degraded run, not normal operation."
                % (exc, FALLBACK_MODEL)
            )

    def model_for(self, name):
        """(model_id, note_or_None) for a captured pin."""
        role = role_of(name)
        if role is None:
            return self.default, (
                "'%s' normalized to no known role — using the open tier (%s)."
                % (name, self.default)
            )
        target = self.roles.get(role)
        if not target:
            return self.default, (
                "role '%s' is absent from the registry's %s role map — using the "
                "open tier (%s)." % (role, TRANSPORT, self.default)
            )
        return target, None

    def cheapest_fresh(self):
        rates = [
            e["price"]["cacheMissPer1M"]
            for e in self.meta.values()
            if isinstance(e.get("price"), dict)
        ]
        return min(rates) if rates else None

    def describe(self, model_id):
        """`deepseek-v4-pro (fresh $0.435/1M, 3.1x flash)` — the cost echo."""
        entry = self.meta.get(model_id)
        if not entry:
            return model_id
        fresh = entry["price"]["cacheMissPer1M"]
        base = self.cheapest_fresh()
        ratio = ""
        if base and fresh > base:
            ratio = ", %.1fx the cheap tier" % (fresh / base)
        return "%s (fresh $%s/1M%s)" % (model_id, fresh, ratio)

    def gated_note(self, model_ids):
        """Warn when a resolution lands on a `gated` model.

        A Workflow dispatch does NOT run the sidecar's preflight — this hook is
        advisory and cannot deny one agent without killing the whole call. So the
        band/balance gate does not apply here, by design: launching a DeepSeek
        session IS the authorization. What replaces enforcement is this note plus
        the SessionStart routing rule.
        """
        gated = [
            m for m in sorted(set(model_ids))
            if (self.meta.get(m) or {}).get("authTier") == "gated"
        ]
        if not gated:
            return None
        parts = []
        for model_id in gated:
            gate = (self.meta.get(model_id) or {}).get("gate") or {}
            unmeasured = (self.meta[model_id].get("effort") or {}).get("evidence") == "unmeasured"
            parts.append(
                "%s is GATED (floor: band %s, balance $%s). A Workflow dispatch does "
                "NOT run the sidecar preflight, so nothing blocks this — pro-to-pro is "
                "for large-scope architecting and complex cross-domain work only; "
                "everything else pins sonnet.%s"
                % (
                    model_id,
                    gate.get("minBand", "?"),
                    gate.get("minBalanceUSD", "?"),
                    " Its effort behavior is UNMEASURED — do not assume the cheap "
                    "tier's effort findings carry over." if unmeasured else "",
                )
            )
        return " ".join(parts)


def inject_pin(args_value, res: Resolution):
    """Add `__pin` to the args payload, preserving its wire form (string vs object).

    Returns (new_value, ok). ok=False when args is an array — JSON.stringify drops
    non-index properties off an array, so injection cannot survive the trip and the
    caller warns instead of silently doing nothing.
    """
    pin = {"model": res.default, "roles": dict(res.roles), "effort": dict(res.effort)}

    was_string = isinstance(args_value, str)
    if was_string:
        try:
            parsed = json.loads(args_value)
        except Exception:
            return args_value, False
    elif args_value is None:
        parsed = {}
    else:
        parsed = args_value

    if not isinstance(parsed, dict):
        return args_value, False

    parsed["__pin"] = pin
    return (json.dumps(parsed) if was_string else parsed), True


def anthropic_warnings(tool: str, tool_input: dict) -> list:
    """Advisory-only scan for Anthropic sessions — notes, never updatedInput."""
    if tool != "Workflow":
        return []
    notes = []
    script = tool_input.get("script")
    if isinstance(script, str) and script:
        match = VENDOR_PIN_RE.search(script)
        if match:
            notes.append(
                "agent() `model:` literal '%s' cannot be served by this session's "
                "endpoint — the agent returns null and a fan-out reads '0 findings'. "
                "External models are unreachable via Workflow from an Anthropic "
                "session; deepseek work goes through .claude/scripts/deepseek_sidecar.sh "
                "(-m pro|flash; pro is band-gated and balance-floored)."
                % match.group(3)
            )
    args = tool_input.get("args")
    parsed = None
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:
            parsed = None
    elif isinstance(args, dict):
        parsed = args
    pin = parsed.get("__pin") if isinstance(parsed, dict) else None
    if isinstance(pin, dict) and isinstance(pin.get("model"), str):
        pin_model = pin["model"]
        if not re.match(r"(?:sonnet|opus|haiku|fable|claude-)", pin_model):
            notes.append(
                "args.__pin carries model '%s', which this session's endpoint cannot "
                "serve — the PIN() resolver applies it and the agent returns null. "
                "External models are unreachable via Workflow from an Anthropic "
                "session; deepseek work goes through .claude/scripts/deepseek_sidecar.sh."
                % pin_model
            )
    return notes


def main() -> None:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if not is_deepseek_session():
        # Anthropic session: never rewrite, no updatedInput — normal sessions stay
        # byte-identical to having no hook. The docstring's other silent failure
        # (vendor-model pin -> agent() returns None -> "0 findings") still earns a
        # named warning: the fix is a mechanism change, not a pin.
        notes = anthropic_warnings(tool, tool_input)
        if notes:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "additionalContext": "[dispatch transport] " + " | ".join(notes),
                        }
                    }
                )
            )
        return

    res = Resolution()
    updated = dict(tool_input)
    notes = []
    if res.degraded:
        notes.append(res.degraded)

    if tool == "Agent":
        # The Agent tool's `model` is a CLOSED enum carrying BARE role names, and
        # every bare role hard-errors on this endpoint (measured 2026-08-12). A
        # passed-through pin therefore yields a null agent and a "0 findings" clean
        # sweep. Dropping the field lets the spawn fall through to the sidecar's
        # CLAUDE_CODE_SUBAGENT_MODEL — a known, cheap, VISIBLE downgrade instead.
        dropped = updated.pop("model", None)
        if dropped is not None:
            notes.append(
                "Stripped the Agent `model` pin ('%s'): the enum carries bare role "
                "names, which hard-error on this endpoint — passing one through "
                "returns a null agent that .filter(Boolean) swallows into "
                "'0 findings'. The spawn inherits CLAUDE_CODE_SUBAGENT_MODEL "
                "(%s) instead. Tier is NOT preserved for Agent spawns; use "
                "Workflow when the tier matters." % (dropped, FALLBACK_MODEL)
            )
        emit(updated, notes)
        return

    if tool != "Workflow":
        return

    # (1) Injection — the ONLY channel that reaches `name:`-resolved committed
    # scripts, whose source is not present in the tool input.
    new_args, ok = inject_pin(updated.get("args"), res)
    if ok:
        updated["args"] = new_args
        notes.append(
            "Injected args.__pin.roles: "
            + ", ".join("%s->%s" % (r, m) for r, m in sorted(res.roles.items()))
        )
    else:
        notes.append(
            "Could not inject args.__pin (args is an array or unparseable). Any "
            "PIN()-based script falls back to its bare Anthropic literal, which "
            "HARD-ERRORS on this endpoint: agent() returns null and the fan-out "
            "reads '0 findings'. Pass args as an object."
        )

    resolved_models = list(res.roles.values()) if ok else []

    # (2) Inline-script rewrite — covers ad-hoc scripts written fresh in-session,
    # which have no PIN() resolver. Disjoint from layer (1) by syntax: `PIN('sonnet')`
    # is not a `model: 'sonnet'` position, so no double-translation is possible.
    script = updated.get("script")
    if isinstance(script, str) and script:
        seen = {}
        lookup_notes = []

        def _sub(match):
            captured = match.group(3)
            target, note = res.model_for(captured)
            if note and note not in lookup_notes:
                lookup_notes.append(note)
            seen[captured] = target
            return match.group(1) + match.group(2) + target + match.group(2)

        rewritten, n_model = SCRIPT_PIN_RE.subn(_sub, script)
        rewritten, n_effort = SCRIPT_EFFORT_RE.subn(
            lambda m: m.group(1) + m.group(2) + res.effort[m.group(3)] + m.group(2),
            rewritten,
        )
        if n_model or n_effort:
            updated["script"] = rewritten
            notes.append(
                "Rewrote %d inline model literal(s) [%s] and %d effort literal(s) to "
                "the deepseek scale."
                % (
                    n_model,
                    ", ".join("%s->%s" % (k, res.describe(v)) for k, v in sorted(seen.items())),
                    n_effort,
                )
            )
        notes.extend(lookup_notes)
        resolved_models.extend(seen.values())

        # (3) Backstop — warn, never deny. Re-scan the SAME `model:` position rather than
        # any quoted role name: `PIN('opus')` is correct code and must not warn (it did,
        # before the channel test caught it). A hit here means a pin shape the rewrite
        # missed; blocking would stop legitimate dispatch, so a named warning is enough.
        if SCRIPT_PIN_RE.search(updated["script"]):
            notes.append(
                "An Anthropic model name still sits in an agent() `model:` pin after "
                "translation. A bare role HARD-ERRORS here (null agent -> '0 findings'); "
                "a full claude-* id aliases by tier, unpriced. Fix the pin."
            )

    # (4) Cost echo + gated warning — what this dispatch will actually cost, and on
    # which tier. Stated every time, so no dispatch is priced by assumption.
    if resolved_models:
        notes.append(
            "Resolves to: "
            + ", ".join(res.describe(m) for m in sorted(set(resolved_models)))
        )
        gated = res.gated_note(resolved_models)
        if gated:
            notes.append(gated)

    emit(updated, notes)


def emit(updated_input: dict, notes: list) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated_input,
        }
    }
    if notes:
        out["hookSpecificOutput"]["additionalContext"] = (
            "[deepseek pin translation] " + " | ".join(notes)
        )
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: a crash here must never block dispatch
    sys.exit(0)
