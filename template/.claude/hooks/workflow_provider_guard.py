#!/usr/bin/env python3
"""PreToolUse(Workflow|Agent): PROVIDER choice — advise in every band, DENY in a conserving one.

TWO INDEPENDENT RULES over one payload, resolved against one transport (_session_transport.py):

RULE 1 — VOCABULARY. `Workflow`/`Agent` dispatch only on the session's own endpoint, so a pin
must be a name THAT endpoint can serve: the ids/aliases of that transport's registry rows, plus
role names on the host transport. Anything else is denied. A pin the endpoint cannot serve
returns a null agent that `.filter(Boolean)` swallows into "0 findings" — a clean-looking run
that ran nothing, which is why this denies rather than warns. Pins are read from args.jobs /
chains / agents, the Agent tool's `model`, inline script text, AND the file named by scriptPath
(orchestration §9 puts pins inside the script, so that is the shape most likely to be missed).
Coverage limit, stated in the rails rather than papered over: a pin computed at runtime inside a
script is not statically visible and is not denied.

RULE 2 — CURRENCY, host transport only. Off-Anthropic, launching the session IS the
authorization to spend that allowance. On the host transport the `[budget-posture]` line says
when Anthropic is the wrong currency, but a line is passive text: measured 2026-09-03, a session in the Ahead band with a
Surplus plan-quota sidecar on the roster dispatched 25 agents on plan quota — every sonnet-role
lens, fix executor and merge — without weighing the sidecar once. Nothing downstream detects
it: the dossier is identical either way. So under Ahead/Hot, EVERY pinned dispatch is denied
until the caller states the currency in the call.

The deny covers pins no roster model claims, not just claimed ones. Narrowing it to claimed
roles left an opus pin passing by construction, and the pass printed "Anthropic is the right
currency for these pins" — an affirmation the guard had not earned, because it never asked
whether the pin was a constraint or a table default (measured 2026-09-04: a 4-lens plan_check
fan-out at Ahead, 542K tokens, justified by the opus pin that command's own table supplied).

DECISION (see `decide`):
  band unknown / Surplus / On pace ...... advisory note only (unchanged behaviour)
  band Ahead / Hot, with a plan-quota transport that has more headroom than this session .....
  DENY every model pin on the call, unless it carries the override: Workflow
  `args.currency == "anthropic"` plus `args.currencyReason`
  (>= 20 chars naming why the sidecar loses); Agent prompt line `CURRENCY: anthropic — <why>`.
  The override is not a password — it is the written decision the band demands, and it is
  made ONCE per session and band: the first stated reason is recorded in the shared per-session
  state file (_hook_state), and later dispatches of the same roles under the same band pass
  with that reason cited. A band change voids the record — a new band is a new decision.

CHANNEL: hookSpecificOutput.permissionDecision=deny (block) or additionalContext (advice).

FAIL POSTURE: the DENY branch needs three facts — the band, the slacker transports, the roster
roles. Any one unreadable → advisory only, exit 0 (an enforcement hook must not brick every
dispatch on its own I/O), but the note says which fact was unreadable so the silence is legible.

ROLE-LADDER INJECTION: every Workflow call (all bands) also receives the role + effort cell of
each row in reference/model_ladder_evidence.md's ladder table, read live at fire time
(orchestration §5 "load the ladder whenever you pin"). Doctrine home for the deny:
orchestration §5b (Budget, Availability & Transport), CLAUDE.md §Model Delegation.
Proof: tests/test_workflow_provider_guard.py.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import quota_bands  # band order SSOT; imports nothing from this package
try:
    import provider_bands
    import model_registry
except Exception:  # advisory only without the comparison
    provider_bands = None
    model_registry = None
try:
    import _hook_state
except Exception:  # no session record → every conserving dispatch states its own currency
    _hook_state = None
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _session_transport
except Exception:  # no resolver → vocabulary rule cannot run; currency rule still can
    _session_transport = None

# Bare Anthropic role names, always legal on the host transport. `claude-*` ids are NOT matched
# by a wildcard here: `claude-[A-Za-z0-9._-]+` admitted a typo (`claude-sonnet-4`) or a retired id
# and returned the null agent this guard exists to stop, so they validate against the registry
# roster like every other transport's ids.
ANTHROPIC_NAMES_RE = re.compile(r"^(?:opus|sonnet|haiku|fable)$")
# Backticks included: `model: `sonnet`` is legal JS and would otherwise pass through unmatched.
_QUOTE = r"['\"`]"
# The optional `IDENT(` prefix is load-bearing. It USED to mark a resolver call worth SKIPPING,
# back when PIN() translated role names per endpoint. PIN() is now `(m) => m` in every engine, so
# `model: PIN('opus')` IS a literal Anthropic pin — and three committed scripts
# (doc_architecture_audit, doc_npc_all, test_skill_pressure) carry ONLY that shape, so skipping it
# made the guard blind to exactly the scripts it most needed to deny.
SCRIPT_PIN_RE = re.compile(
    r"\bmodel\s*:\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*\(\s*)?(" + _QUOTE + r")([A-Za-z0-9._-]+)\1")

# Fan-out engines where provider choice is load-bearing. Other workflows still get a
# nudge (they spend too), but these name the roster columns the caller should re-read.
FANOUT_ENGINES = ("explore_fanout", "review_fanout", "dispatch", "doc_architecture_audit")
# The floor comparison, not a restated list: quota_bands owns the band order.
CONSERVING_BANDS = tuple(b for b in quota_bands.BAND_NAMES if quota_bands.band_satisfies(b, "Ahead"))
REVIEW_FANOUT_DEFAULT_MODEL = "sonnet"   # review_fanout.js DEFAULT_MODEL — an omitted pin lands here
MIN_REASON_CHARS = 20
AGENT_OVERRIDE_RE = re.compile(r"^\s*CURRENCY:\s*anthropic\b.{%d,}" % MIN_REASON_CHARS, re.I | re.M)


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


def _slacker_transports(band, session_transport=None):
    """Rows from provider_bands.slacker_than, or None when the comparison itself failed.

    `session_transport` is passed through so the host transport is never advised to route to
    itself: since `anthropic` became a registry row it is a plan-quota transport like any other,
    and its band on an Anthropic session IS the session band.
    """
    if provider_bands is None:
        return None
    try:
        return provider_bands.slacker_than(band, session_transport=session_transport)
    except TypeError:  # older provider_bands without the parameter
        return provider_bands.slacker_than(band)
    except Exception:
        return None


def claimed_roles(slackers):
    """{role: [model ids]} claimed by AVAILABLE models on the slacker transports; None if unreadable."""
    if model_registry is None:
        return None
    try:
        data = model_registry.load()
        wanted = {r["transport"] for r in slackers or []}
        out = {}
        for m in model_registry.available_models(data):
            if m["transport"] not in wanted:
                continue
            for role in m.get("roles") or []:
                out.setdefault(role, []).append(m["id"])
        return out
    except Exception:
        return None


def _args(tool_input):
    a = tool_input.get("args")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            return {}
    return a if isinstance(a, dict) else {}


def pinned_models(payload):
    """[(label, model)] every pin this call will dispatch. An omitted review_fanout pin is the engine's default."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Agent":
        m = ti.get("model")
        return [(str(ti.get("description") or "agent"), str(m))] if m else []
    a = _args(ti)
    out = []
    # `dispatch.js` requires a pin per job, so an entry without one is malformed input, not a pin.
    # Emitting `(label, "")` denied it as "Pin not serviceable: ''" -- a message naming no model,
    # about a pin the caller never wrote.
    for j in a.get("jobs") or []:
        if (j or {}).get("model"):
            out.append((str(j.get("label") or "job"), str(j["model"])))
    for c in a.get("chains") or []:
        for j in (c or {}).get("jobs") or []:
            if (j or {}).get("model"):
                out.append((str(j.get("label") or "job"), str(j["model"])))
    for ag in a.get("agents") or []:
        out.append((str((ag or {}).get("key") or "agent"), str((ag or {}).get("model") or REVIEW_FANOUT_DEFAULT_MODEL)))
    return out


# Characters of a `scriptPath` file this hook will read. The committed workflows are all well under
# it; the cap exists so a caller-supplied path cannot set this hook's cost.
SCRIPT_READ_CAP = 512 * 1024

_COMMENT_RE = re.compile(r"/\*.*?\*/|(?<![:'\"`\\])//[^\n]*", re.S)


def _strip_comments(text):
    """Blank out JS comments before scanning for pins.

    Load-bearing, not tidiness: these scripts DOCUMENT their pin conventions in comments
    (`dispatch.js` explains its pin vocabulary in prose directly above the code), so a
    scanner that reads comments denies a script whose executable pins are all legal. A guard that
    misfires on its own doctrine spends the credibility that makes true positives land
    (`rules/harness_tooling.md`). The `//` arm refuses to match after `:` or a quote so a URL
    (`https://…`) is not read as a comment.
    """
    return _COMMENT_RE.sub(lambda m: " " * len(m.group(0)), text)


def script_pins(payload):
    """[(label, model)] for pins written INSIDE a workflow script, from inline `script` text or
    from the file named by `scriptPath`.

    orchestration §9 mandates that prompts and pins live inside the script, so without this the
    mandated shape is the one shape the guard cannot see: a committed workflow dispatches by
    scriptPath and its `agent({model: 'sonnet'})` literals never appear in tool_input.

    A pin computed at runtime (a variable, a map lookup, a template literal) is not statically
    visible and is NOT returned — that limit is stated in the rails rather than papered over.
    """
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") != "Workflow":
        return []
    incomplete = []
    blobs = []
    inline = ti.get("script")
    if isinstance(inline, str) and inline:
        blobs.append(("inline-script", inline))
    path = str(ti.get("scriptPath") or "")
    if path:
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            full = path if os.path.isabs(path) else os.path.join(os.path.dirname(root), path)
            # Bounded: this runs on EVERY Workflow PreToolUse, and `scriptPath` is caller-supplied.
            # A mispointed path at a multi-MB file would spend the hook's whole budget on a read
            # whose pins all sit in the first few KB anyway. But a TRUNCATED scan is an incomplete
            # one: a pin past the cap would be invisible and read to `decide_vocabulary` exactly
            # like a clean script, so the cap reports itself rather than silently passing.
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read(SCRIPT_READ_CAP + 1)
            if len(text) > SCRIPT_READ_CAP:
                incomplete.append("%s exceeds %dKB" % (os.path.basename(path),
                                                       SCRIPT_READ_CAP // 1024))
            blobs.append((os.path.basename(path), text[:SCRIPT_READ_CAP]))
        except Exception:
            # No claim from THAT FILE -- but pins already collected from the inline blob stand.
            # Returning [] here made an unreadable path ERASE real evidence, and an empty pin list
            # reads to decide_vocabulary exactly like a clean script (instruction_quality 14).
            #
            # NOT escalated to a deny, unlike truncation: this hook resolves `scriptPath` against
            # its own root guess, so "I could not open it" often means the runtime can and the
            # guess was wrong. Denying there would block valid dispatches on a path this hook
            # merely failed to resolve. Truncation is different -- the file WAS found, and the
            # part not read could hold a pin.
            pass
    out = []
    for label, text in blobs:
        text = _strip_comments(text)
        for m in SCRIPT_PIN_RE.finditer(text):
            out.append((label, m.group(2)))
    # A scan that could not read its whole input reports it as a pin the endpoint cannot serve,
    # which is the channel `decide_vocabulary` already has for "do not dispatch this". Silence
    # here would be indistinguishable from a script with no pins at all.
    for why in incomplete:
        out.append(("scan-incomplete", "<unscanned: %s>" % why))
    return out


def override_stated(payload):
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Agent":
        return bool(AGENT_OVERRIDE_RE.search(str(ti.get("prompt") or "")))
    a = _args(ti)
    return str(a.get("currency") or "").lower() == "anthropic" and len(str(a.get("currencyReason") or "").strip()) >= MIN_REASON_CHARS


def decide(band, slackers, roles, payload, record=None):
    """Pure decision. Returns (verdict, body, hits): verdict "deny" (body = reason) | "advise" (body = notes)
    | "silent"; hits = the [(label, model)] pins a slacker transport's model claims. `record` is the
    session's earlier currency statement, {"band", "roles", "reason"}, or None."""
    if not band or band == "Surplus":
        return "silent", [], []
    notes = [f"band={band}"]
    if band not in CONSERVING_BANDS:
        return "advise", notes, []
    if slackers is None:
        notes.append("provider-band comparison unreadable — deny not evaluated; read the roster yourself")
        return "advise", notes, []
    if not slackers:
        notes.append("no plan-quota transport has more headroom than this session — Anthropic is the right currency")
        return "advise", notes, []
    if roles is None:
        notes.append("roster unreadable — deny not evaluated; run model_registry.py available")
        return "advise", notes, []
    pins = pinned_models(payload)
    if not pins:
        notes.append("no model pins on this call — nothing to route")
        return "advise", notes, []
    hits = [(label, model) for label, model in pins if model in roles]
    if override_stated(payload):
        notes.append("currency=anthropic stated with a reason — allowed and recorded for this band")
        return "advise", notes, hits
    # An UNCLAIMED pin is not self-justifying: the deny covers every pin, and `stated` is what the
    # session record must already cover for this call to pass on a prior decision.
    stated = hits or pins
    if record and record.get("band") == band and all(m in (record.get("roles") or []) for _, m in stated):
        notes.append(f"currency=anthropic on record for {band} ({', '.join(sorted({m for _, m in stated}))}): {record.get('reason', '')[:160]}")
        return "advise", notes, hits
    launchers = sorted({l for r in slackers for l in r.get("launchers") or []})
    transports = ", ".join(r["transport"] + " " + r["band"] for r in slackers)
    override_ask = (
        f"re-issue THIS call with `args.currency: \"anthropic\"` and "
        f"`args.currencyReason: \"<why the sidecar loses for these jobs, >= {MIN_REASON_CHARS} chars>\"` "
        f"(Agent tool: a prompt line `CURRENCY: anthropic — <why>`). Stating it is the decision the band asks for."
    )
    if hits:
        who = ", ".join(f"{label}→{model} (claimed by {'/'.join(roles[model])})" for label, model in hits[:8])
        reason = (
            f"Band {band}: this dispatch pins a role an available plan-quota sidecar claims — {who}. "
            f"That transport ({transports}) spends an allowance that expires unused; this session's quota is "
            f"the scarce one (orchestration §5b, CLAUDE.md §Model Delegation). "
            f"Route those jobs through {' or '.join(launchers) or 'the sidecar launcher'} (one Bash call per job, "
            f"reference/sidecar_dispatch.md), or {override_ask}"
        )
        return "deny", reason, hits
    who = ", ".join(f"{label}→{model}" for label, model in pins[:8])
    reason = (
        f"Band {band}: no roster model claims these pins — {who}. That is a reason to STATE, never one that "
        f"states itself: a pin an engine default or a command's pin table supplied is not a constraint, and "
        f"citing the pin as the reason to stay on Anthropic is circular. An available plan-quota transport "
        f"({transports}) is spending an allowance that expires unused. "
        f"Either lower the pin to a role the roster claims and route it through "
        f"{' or '.join(launchers) or 'the sidecar launcher'} (reference/sidecar_dispatch.md), or name the "
        f"constraint independent of the pin — engine lock, MCP tools the child lacks, a capability the roster "
        f"genuinely lacks — and {override_ask}"
    )
    return "deny", reason, pins


def transport_injection(payload, transport, data=None):
    """`updatedInput` adding `args.__transport`, or None when there is nothing to inject.

    The dispatch engines hard-code `VALID_MODELS = ['opus','sonnet','haiku','fable']` — an
    Anthropic vocabulary. Without this, a codex session that pins its own ids has them rejected
    by `dispatch.js` and silently coerced to `sonnet` by `review_fanout.js`, so D1 would ship a
    capability that does not exist. Presence of `__transport` selects provider mode; incomplete ids,
    default, or efforts then fail loudly in the engine instead of reopening Anthropic vocabulary.

    Replaces the retired pin-translation injection, which carried a role→id MAP because the
    engines pinned roles and something had to translate. Nothing translates now, so what the
    engines need is not a mapping but a VOCABULARY.
    """
    if transport == _session_transport.HOST_TRANSPORT or transport == _session_transport.UNKNOWN:
        return None
    if payload.get("tool_name") != "Workflow":
        return None  # the Agent tool has no script-side resolver to feed
    ids = _session_transport.legal_model_ids(transport, data)
    ti = dict(payload.get("tool_input") or {})
    raw = ti.get("args")
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None  # unparseable args: inject nothing rather than corrupt the call
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return None  # an array payload has nowhere to hang the key
    parsed = dict(parsed)
    # `default` answers what `ids` cannot: which id an ENGINE-INTERNAL stage should use.
    # review_fanout's consolidation agent pins its OWN model, not a caller's, so it is invisible
    # to both the widening and the guard's scanner. First registry row for the transport, so the
    # answer is data-driven rather than a second hardcoded vocabulary.
    roster = _session_transport.roster(transport, data)
    efforts = _session_transport.legal_effort_values(transport, data)
    parsed["__transport"] = {
        "name": transport,
        "ids": ids,
        "default": (roster[0][1] if roster else None),
        "efforts": efforts,
    }
    # Preserve the wire form: an engine that received a STRING must keep receiving one.
    ti["args"] = json.dumps(parsed) if isinstance(raw, str) else parsed
    return ti


def _known_transports():
    """Registered transport names for the fail-closed message, or a placeholder. The message is
    only reachable when the resolver already failed, so this must not raise on the same cause."""
    try:
        return model_registry.transports()
    except Exception:
        return ["(registry unreadable)"]


def _host_alias_excluded(model, data):
    """Is `model` a host-transport row the registry has marked unavailable?

    False for a name the registry does not carry -- an absent row is not an exclusion, and treating
    it as one would deny every pin on any checkout whose registry predates the Anthropic rows.
    """
    # `data` arrives None from the one production call site (decide_vocabulary is called with
    # three args), and `None.get` used to raise straight into the `except` below -- which returns
    # the ALLOW answer, so this guard never fired outside its own proofs. Load the registry here.
    if data is None:
        data = _session_transport._registry()
    try:
        rows = [m for m in ((data or {}).get("models") or [])
                if m.get("transport") == _session_transport.HOST_TRANSPORT
                and model in (m.get("id"), m.get("alias"))]
        if not rows:
            return False
        legal = set(_session_transport.legal_model_ids(_session_transport.HOST_TRANSPORT, data))
        return model not in legal
    except Exception:
        return False


def decide_vocabulary(transport, source, payload, data=None):
    """RULE 1 — is every pin a name this session's ENDPOINT can serve? ("deny", reason) | (None, None).

    One predicate over the transport family, not a branch per provider: legal pins on transport T
    are the ids and aliases of T's registry rows. `anthropic` additionally accepts the four role
    names, because on the host transport a role name IS how you pin.

    This runs BEFORE the currency rule: a pin the endpoint cannot serve is wrong at any band, and
    saying so first gives the clearer message.
    """
    if _session_transport is None:
        return None, None
    pins = pinned_models(payload) + script_pins(payload)
    if not pins:
        return None, None

    if transport == _session_transport.UNKNOWN:
        who = ", ".join(f"{label}→{model}" for label, model in pins[:8])
        return "deny", (
            f"This session's transport could not be identified ({source}), so no pin can be "
            f"checked against a roster — and an unidentified endpoint is the one case where "
            f"guessing is worst: a role name silently reaches whatever the endpoint happens to "
            f"be. Pins on this call: {who}. Set CLAUDE_CODE_TRANSPORT to one of "
            f"{', '.join(_known_transports())} "
            f"in the launcher or profile function that started this session, then re-issue."
        )

    legal = set(_session_transport.legal_model_ids(transport, data))
    bad = []
    for label, model in pins:
        if model in legal:
            continue
        # The host carve-out admits the four bare Anthropic role names without a registry lookup --
        # the ladder owns them, and the registry carried no Anthropic rows at all until 2026-09-04.
        # But it ran before any availability check, so excluding an Anthropic row denied it from
        # every provider seat and left it fully pinnable from an Anthropic one: the same
        # message-vs-behaviour desync the roster filter closes. Deny only a name the registry knows
        # AND has excluded; an unregistered role name keeps passing, which is the ladder's case.
        if (transport == _session_transport.HOST_TRANSPORT and ANTHROPIC_NAMES_RE.match(model)
                and not _host_alias_excluded(model, data)):
            continue
        bad.append((label, model))
    if not bad:
        return None, None

    roster = _session_transport.roster(transport, data)
    listed = "; ".join(f"{a} = {i}" for a, i in roster) or "(none registered)"
    who = ", ".join(f"{label}→{model}" for label, model in bad[:8])
    if transport == _session_transport.HOST_TRANSPORT:
        why = ("a vendor id on an Anthropic session makes agent() return null, and the fan-out "
               "reads '0 findings' — a clean-looking run that ran nothing")
    else:
        why = ("Anthropic role names are ANTHROPIC-session vocabulary; on this transport they "
               "resolve against nothing, and a roster row claiming a role would route every "
               "unmatched role onto one model nobody chose")
    return "deny", (
        f"Pin not serviceable on transport '{transport}' (identified by {source}) — {who}. "
        f"{why.capitalize()}. Legal pins here: {listed}. "
        f"Pin one of those ids, or dispatch to another transport through its own sidecar "
        f"launcher (reference/sidecar_dispatch.md) — Workflow/Agent never cross transports."
    )


def _session_record(payload):
    """(path, state, record) for the shared per-session state file; (None, {}, None) without _hook_state."""
    if _hook_state is None:
        return None, {}, None
    sid = payload.get("session_id") or os.path.basename(str(payload.get("transcript_path") or "")).split(".")[0]
    path = _hook_state.state_path(sid)
    state = _hook_state.read_json_salvage(path) or {}
    rec = state.get("currency")
    return path, state, (rec if isinstance(rec, dict) else None)


def non_thinking_review_pins(payload):
    """Agent keys pinned sonnet·low on a review_fanout dispatch — the one cell the ladder marks non-thinking,
    on the one engine that only dispatches judgment."""
    ti = payload.get("tool_input") or {}
    target = os.path.basename(str(ti.get("scriptPath") or ti.get("name") or "")).replace("-", "_")
    if "review_fanout" not in target:
        return []
    return [str((a or {}).get("key") or "agent") for a in (_args(ti).get("agents") or [])
            if str((a or {}).get("model") or REVIEW_FANOUT_DEFAULT_MODEL) == "sonnet" and str((a or {}).get("effort") or "") == "low"]


LADDER_ROLE_HEADING = "## Role guidance"
LADDER_ROLE_COLUMNS = ("model", "role", "effort")


def _ladder_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reference", "model_ladder_evidence.md",
    )


def parse_ladder_role_lines(raw_lines):
    """model/role/effort per ROW of the ladder's Role guidance table ([] on any schema drift).

    The table SCAN is `model_registry.parse_ladder_rows` -- one reader, so a column inserted into
    either ladder table cannot shift this projection and the registry's differently. Without the
    registry this returns nothing: an advisory injection is worth less than a second parser.
    """
    if model_registry is None:
        return []
    rows = model_registry.parse_ladder_rows(raw_lines, LADDER_ROLE_COLUMNS, LADDER_ROLE_HEADING)
    return ["%s: %s (effort: %s)" % (r["model"], r["role"], r["effort"][:60]) for r in rows]


def ladder_role_lines(path=None):
    """parse_ladder_role_lines over the live ladder SSOT (empty on any failure)."""
    try:
        with open(path or _ladder_path(), encoding="utf-8") as f:
            return parse_ladder_role_lines(f)
    except Exception:
        return []


def _emit_advice(parts):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "[provider check] " + " | ".join(parts),
    }}))


def main():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name")
    if tool not in ("Workflow", "Agent"):
        return
    tool_input = payload.get("tool_input") or {}

    # RULE 0 — a non-thinking effort cell on a review engine. Home: reference/model_ladder_evidence.md,
    # sonnet row ("`low` effort does not think AT ALL, only for strict no-effort execution");
    # review_fanout.js dispatches review lenses and nothing else, so the cell is illegal there at every band.
    if tool == "Workflow":
        keys = non_thinking_review_pins(payload)
        if keys:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "sonnet·low is not a review pin (" + ", ".join(keys) + "): the ladder row for sonnet reads "
                    "\"`low` effort does not think AT ALL, only for strict no-effort execution\" "
                    "(reference/model_ladder_evidence.md §Role guidance). Pin sonnet·medium — its audit-lens cell — "
                    "or an executor-tier model at low."),
            }}))
            return

    # The ladder rows ride on the Workflow call only when Skill(orchestration) has not already injected them
    # this session (hooks/skill_load_marker.py) — a fallback for a pin chosen without the skill, never the primary path.
    roles_note = []
    if tool == "Workflow" and "orchestration" not in (_session_record(payload)[1].get("skills_loaded") or []):
        roles = ladder_role_lines()
        if roles:
            roles_note = ["[role ladder — pin against THESE rows, not the registry roster (orchestration §5)] "
                          + " ;; ".join(roles)]

    transport, source = (_session_transport.resolve() if _session_transport
                         else ("anthropic", "resolver-unavailable"))

    # RULE 1 — vocabulary. A pin the endpoint cannot serve is wrong at every band, so it is
    # decided before any currency question and its message is the one the caller sees.
    v_verdict, v_reason = decide_vocabulary(transport, source, payload)
    if v_verdict == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": v_reason,
        }}))
        return

    # RULE 2 — currency. Only on the host transport: on a provider session, launching that
    # session IS the authorization to spend its allowance, and this session's Anthropic quota is
    # not what the dispatch draws on.
    if _session_transport and transport != _session_transport.HOST_TRANSPORT:
        parts = [f"transport={transport} ({source})",
                 "currency gate N/A off the host transport — launching this session authorized its allowance",
                 f"legal pins: {', '.join(a for a, _ in _session_transport.roster(transport)) or 'none registered'}"]
        updated = transport_injection(payload, transport)
        out = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[provider check] " + " | ".join(parts + roles_note),
        }}
        if updated is not None:
            out["hookSpecificOutput"]["updatedInput"] = updated
        print(json.dumps(out))
        return

    band, pressure = band_and_pressure()
    slackers = _slacker_transports(band, transport) if band in CONSERVING_BANDS else []
    claimed = claimed_roles(slackers) if slackers else {}
    state_path, state, record = _session_record(payload)
    verdict, body, hits = decide(band, slackers, claimed, payload, record)
    if verdict == "advise" and hits and override_stated(payload) and state_path:
        reason = str(_args(tool_input).get("currencyReason") or "")
        if not reason:
            m = AGENT_OVERRIDE_RE.search(str(tool_input.get("prompt") or ""))
            reason = m.group(0).strip() if m else ""
        prior = (record.get("roles") if record and record.get("band") == band else None) or []
        state["currency"] = {"band": band, "roles": sorted(set(prior) | {m for _, m in hits}), "reason": reason[:400]}
        _hook_state.write_json_atomic(state_path, state)

    if verdict == "deny":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": body,
        }}))
        return
    if verdict == "silent":
        if roles_note:
            _emit_advice(roles_note)
        return

    note = list(body)
    if pressure:
        note[0] = f"{note[0]} (pressure {pressure})"
    if tool == "Workflow":
        target = str(tool_input.get("scriptPath") or tool_input.get("name") or "")
        note.append("Workflow is the ANTHROPIC transport — it cannot reach the sidecar at any agent count; provider is chosen by BAND, never by agent/lens count.")
        if any(e in target for e in FANOUT_ENGINES):
            note.append("Fan-out engine: every job whose role a sidecar model claims wants one launcher call instead, consolidated orchestrator-side.")
    for row in slackers or []:
        note.append(
            f"{row['transport']}: {row['band']}"
            + (f" (pressure {row['pressure']})" if row.get("pressure") is not None else "")
            + f" vs this session's {band} — its allowance expires unused; launcher: {' or '.join(row['launchers'])}"
        )
    note.extend(roles_note)
    _emit_advice(note)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a failure in the guard's own I/O must never brick a dispatch; the deny branch only fires on read facts
    sys.exit(0)
