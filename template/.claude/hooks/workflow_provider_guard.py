#!/usr/bin/env python3
"""PreToolUse(Workflow|Agent): every pinned dispatch names its tier, and its pin serves that tier.

A pin is any object anywhere in the Workflow `args` that carries a `model` key (`__`-prefixed injected
keys are not read), the Agent tool's `model`, or a literal pin in inline `script` text or the file
named by `scriptPath`. A pin computed at runtime inside a script is not statically visible and is not
checked. No engine fills an omitted model or effort: each refuses the call before any agent starts. An
engine's own verify and merge steps take `args.checker`; when the call names none, INJECTION fills it with
`computed_checker` (a choice the facts settle), and RULE 3 checks it as a verify-tier pin.

RULE -1 -- TOOL PROFILE. A job or Agent prompt header that declares a shape or a `tools` delta must
resolve against `guards/tool_profiles.json`; otherwise deny with the resolver's message. Resolved
profiles ride in `args.__toolProfiles`, recorded `enforcement: inherited`.
Proof: tests/test_workflow_tool_profiles.py.

RULE -0.5 -- BRIEF COVERAGE. An uncovered generated agentType becomes general-purpose, and a declared
tool profile gains each missing tool its brief runs (merged into `tools.add`, named in an advisory),
in this hook's single updatedInput, beside transport injection. Still denied: a tool the job's own
`tools.remove` names, a brief that needs Workflow or Agent, and any gap left when
HARNESS_WORKFLOW_ARGS_OWNER owns input rewriting.

RULE C -- CITER COVERAGE (process guard; denies while `citer_coverage.BLOCKING`). `citer_coverage`
validates current meta subject sweeps at the shared native boundary. Phase 2 of plan_check.md owns
materialization; the guard never creates a sweep.

RULE 0 -- a review_fanout pin at a rung its registry row lists in `effort.nonThinking` is denied.

RULE 1 -- VOCABULARY, every band. A pin must be a name the session's own endpoint serves: the
canonical ids of that transport's dispatchable registry rows, plus their aliases on the host
transport, whose runtime resolves its own aliases. An unidentified transport denies every pin.
Every visible Workflow model/effort pair also uses that model row's legal effort vocabulary, not
only the transport-wide list; unsupported rungs deny before an agent starts.
A pair at a rung the row lists in `effort.avoid` denies too, naming the row's `effort.avoidRef`. A
Workflow call is no observable comparison, so no `args.measurement` lifts it; the rung passes only in
`sidecar_fanout.py --compare` or a benchmark campaign.

RULE 2 -- CAPACITY. `provider_capacity_guard` reads the session PLAN (`_session_transport.session_plan`:
on the host route, the plan whose `configDir` is CLAUDE_CONFIG_DIR) and denies before the endpoint
receives work. The deny names the other plans with room that pay for a row of the call's
tiers, and their launchers: native dispatch waits for the reset, the job does not (orchestration
§5b). A session whose plan is unknown is denied, naming the observed CLAUDE_CONFIG_DIR and both ways
to identify it.

RULE 3 -- TIER, every band and every transport. A job's tier is the one its own data names, and the pin must
pass `_dispatch_checks.tier_fit` on the session's own transport, the check the sidecar hook applies on the
alias's transport (there `_dispatch_checks.job_tier`: written tier, else the shape row's):
  * A written tier -- a pin's `tier`, the nearest enclosing object's, the call-wide `args.tier`, the Agent
    tool's `TIER: <tier>` line, `args.tier` for script pins -- is checked. A dispatch.js job's own `tier`
    wins over its shape row's. Legal values are `model_registry.TIER_ORDER` plus `validation`, which folds
    onto `fanout`; an unknown value denies with the legal list. A missing tier never denies.
  * The implied tier is the tier the call's facts name: a review_fanout seat keyed by registry
    lenses takes `lens.seat_tier` (tools/lens.py), and a dispatch.js job that writes no `tier` takes its shape
    row's (guards/tool_profiles.json). The same maps ride to the engines as `args.__lensTiers` and
    `args.__shapeTiers`; a review_fanout call also gets `args.__lensMandates`, each seat's catalog keys
    (`lens.seat_mandates`), which the engine writes on its ledger rows. A lens seat's implied tier cannot be
    replaced by a `tier` written on the entry, on an enclosing object or call-wide. A Workflow
    alias naming exactly one id among an implied tier's rows is rewritten before checking; zero
    matches or ambiguity deny. Only an implied tier is repaired: a written tier whose pin is an alias
    outside its rows denies naming that tier's rows. The fix joins transport injection in the one updatedInput. Otherwise
    deny naming that tier's rows: raise effort before tier, and declare a higher tier only where
    effort cannot reach the work. `model_registry.py for-role <tier>` lists the rows that claim a tier
    before the call.
  An unreadable registry leaves tier fit unevaluated and says so; a missing ladder does not.

RULE 4 -- CURRENCY, PROBATION and CHEAPER CLAIMANTS are not hook output. Each plan's quota left against its
window left is the `[budget-posture]` line from a session's first prompt, a row's owner probation mark prints
on `model_registry.py for-role <tier>`, and the ladder carries each claimant's cost and `±` cell before the
doctrine gate admits a call (`orchestration` §5, §5b).

RULE 5 -- FIVE-HOUR WIDTH, on the host transport. When this session's 5-hour pressure is above
`quota_bands.FIVE_HOUR_NARROW_PRESSURE`, a dispatch is allowed only if this session would then have at
most one live native job, counting its own (`dispatch_width`). A live job is a started agent in one of
this session's `subagents/workflows/<run>/journal.jsonl` with no `result`/`failed`/`error` line, or a
`subagents/agent-<id>.jsonl` whose last message record is not an assistant `end_turn`/`stop_sequence`
and whose completion notification in the session transcript does not postdate its last write; either
counts only while its transcript changed in the last LIVE_SECONDS. The deny names quota left, window left and the UTC reset when Hot or Exhausted, the live jobs, and per job a launcher invocation serving its tier (`model_registry.for_role`):
another plan paying for this route's row first, since it runs the same model, else a cross-transport
row; or the wait (for a live run or the reset) where neither exists. A hot window that admits the
call adds nothing: the posture line states the rule before the call. An unreadable window or transcript tree adds no deny;
the guard log records it under `fiveHour`/`fiveHourLive`.

OWNER OVERRIDE: an `owner:` statement (`args.currency: "<session transport>"` with
`args.currencyReason: "owner: <words>"`, or an Agent prompt line `CURRENCY: <transport> — owner:
<words>`) is held on the session record (`ownerOverride`) for the rest of the session and lifts
RULE 5. It lifts only when an owner turn of this session holds the literal token `--<transport>` outside
a pasted block, or the owner answered an AskUserQuestion naming the transport with a lift
(`owner_lift_found`, over `hooks/_owner_text.py`); the stated words are display only. A held statement is
a verified one and this call's own text never lifts. A RULE 5 deny
opens with the instruction it enforces (orchestration §5b, read before the call) and closes by
naming the override as the owner's to grant, never the agent's.

INJECTION: every Workflow call on a known transport receives `args.__rails`, `args.__railsText`, `args.__driverNotes`
(registry `driverNotes` per pinned model, when any), `args.__toolProfileDefaults` and `args.__transport` = {name, ids (the legal pins), efforts (only when
the transport declares a vocabulary)}. `args.__coverage` lists a covered shape only when the session
can have loaded its generated agent type (`loadable_coverage`): the client reads `.claude/agents/` once,
at session start, so a type whose file is missing or not older than the session's first transcript
timestamp would fail as "agent type not found". An unknown start offers no shape. It carries no default pin. A call naming no `args.checker` gets
`computed_checker`'s pin there. The
engines hold no model vocabulary of their own.

FAIL POSTURE: a Workflow call the guard cannot finish checking is denied with the error and a retry
hint, because it would otherwise pass without `__transport` and the engine refuses it. An Agent call
has no vocabulary to receive and keeps its allow. The band read feeds only RULE 5: a timeout leaves
the 5-hour window unread and never costs the injection. Every invocation appends one record (outcome, phase
seconds, error) to logs/workflow_provider_guard.jsonl; HARNESS_PROVIDER_GUARD_LOG overrides the path.

DEATH RESUME: the hook does no resume stat work. The engine/rails staleness check is a step before the
call, owned by commands/agents/orchestrator_action_protocol.md#death-resume.

CHANNEL: hookSpecificOutput.permissionDecision=deny (block) or additionalContext (advice).
Doctrine: orchestration §5 and §5b. Proof: tests/test_workflow_provider_guard*.py.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import quota_bands  # band order SSOT; imports nothing from this package
import citer_coverage
try:
    import model_registry
except Exception:  # no registry reader: tier fit and currency advise, never guess
    model_registry = None
try:
    import _hook_state
except Exception:  # no session record → an owner statement is not held across calls
    _hook_state = None
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _owner_text  # noqa: E402  the one owner-row parser; this file holds only the projection
import _dispatch_checks  # noqa: E402  tier fit and currency, shared with the sidecar hook
from _dispatch_checks import shape_tiers  # noqa: E402
from _brief_coverage import coverage_fixes, coverage_map, coverage_refusal, skill_index  # noqa: E402
from _dispatch_pins import (  # noqa: E402
    _args, _is_review_fanout, _tp, agent_profile_request, dispatch_jobs, native_legal_pins,
    native_pin, profile_defaults, profile_request, resolve_request, script_blobs, tier_rows)
try:
    import _session_transport
    import provider_capacity_guard
except Exception:  # no resolver/capacity guard → vocabulary cannot run; main emits advisory
    _session_transport = None
    provider_capacity_guard = None

# Backticks included: `model: `x`` is legal JS and would otherwise pass through unmatched.
_QUOTE = r"['\"`]"
# The optional `IDENT(` prefix is load-bearing: `PIN()` is `(m) => m` in every engine, so
# `model: PIN('x')` is a literal pin and committed scripts carry only that shape.
SCRIPT_PIN_RE = re.compile(
    r"\bmodel\s*:\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*\(\s*)?(" + _QUOTE + r")([A-Za-z0-9._-]+)\1")
TIER_LINE_RE = re.compile(r"^\s*TIER:\s*`?([A-Za-z_-]+)`?", re.M)

MIN_REASON_CHARS = 20
EFFORT_NOT_TIER = ("raise effort before tier (orchestration §5 Effort); if the ladder's ± cell shows "
                   "this tier's rows cannot do the work at any rung, declare the higher tier.")
OWNER_CLASS = "owner"
# The one class a statement may open with: a verified owner quote. Any other text is not a lift.
_CLASS_RE = re.compile(r"^\s*(%s)\s*:\s*(.*)$" % OWNER_CLASS, re.S)
AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")
TRANSCRIPT_HEAD_LINES = 200
LOG_NAME = "workflow_provider_guard.jsonl"
LOG_ENV = "HARNESS_PROVIDER_GUARD_LOG"
# This invocation's diagnosis record: main() resets it, the phases fill it, main() appends it.
_TRACE = {}
# settings.json kills this hook at 60 s, and a killed hook leaves no verdict and no record. The capacity
# probe may spend up to 40 s, so the advisory band read only gets the time left before this mark; the
# rest of the window covers interpreter start and imports, which run before this clock starts.
DEADLINE_SECONDS = 43.0
_deadline = None
# A started agent whose transcript has not changed for this long is not counted as live.
LIVE_SECONDS = 15 * 60
# Bytes read from the end of a standalone agent transcript to find its last record.
TAIL_BYTES = 256 * 1024
# Bytes read from the end of the session transcript to find subagent completion notifications, and how
# far before an agent's last write a notification may be stamped and still mean that write was its last.
NOTIFY_TAIL_BYTES = 8 * 1024 * 1024
NOTIFY_SLACK_SECONDS = 5.0
# The 5-hour reading returned by this invocation's band read: {"pressure", "resets_at", "used"} or {}.
_five_hour = {}


def band_and_pressure(session_id=None):
    """Return (band, pressure) for THIS session's own quota, or (None, None).

    Resolved against THIS file rather than $CLAUDE_PROJECT_DIR: the env var can
    arrive as an MSYS path (/c/Users/...) that a native Windows python3 cannot
    open, which would silently disable the advisory. `session_id` makes the CLI read
    this session's capture instead of listing all of TEMP. A read that times out or
    cannot start is (None, None) with `band_error` traced: its 5-hour fields feed RULE 5.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget_posture.py")
    cmd = [sys.executable, script, "--band", "--pressure", "--five-hour", "--used"]
    if session_id:
        cmd += ["--session", str(session_id)]
    budget = 10.0 if _deadline is None else min(10.0, _deadline - time.monotonic())
    if budget < 0.5:
        _TRACE["band_error"] = "skipped: %.1f s left before the hook's timeout" % max(budget, 0.0)
        return None, None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=budget)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _TRACE["band_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        return None, None
    parts = out.stdout.strip("\r\n").split("\t")
    _five_hour.update(_five_hour_fields(parts[2:4], parts[5:6]))
    if out.returncode != 0:
        return None, None
    return (parts[0] or None), (parts[1] if len(parts) > 1 else None)


def _five_hour_fields(fields, used_field=()):
    """{"pressure", "resets_at", "used"} from the band read's 5h fields, or {} when they are absent or empty.
    `used` is the capture's 5h used percent from 0 to 100, None when the read did not carry it."""
    try:
        window = {"pressure": float(fields[0]), "resets_at": float(fields[1])}
    except (IndexError, ValueError):
        return {}
    try:
        window["used"] = float(used_field[0])
    except (IndexError, ValueError):
        window["used"] = None
    return window


def five_hour_window(*_a, **_k):
    """(pressure, resets_at, used) for the 5-hour window `band_and_pressure` read, or (None, None, None)."""
    return _five_hour.get("pressure"), _five_hour.get("resets_at"), _five_hour.get("used")


def _host():
    return _session_transport.HOST_TRANSPORT if _session_transport else None


# ---------------------------------------------------------------- the transport's own rows
# A transport's pin names and tier rows (`native_pin`, `tier_rows`) live in _dispatch_pins, which the
# dispatch table shares.

# The host client's context-window request modifier (`model_registry.row_for_model`); it selects no
# other model. Later rules read a pin without it; RULE 1 accepts it only where a row declares it.
_HOST_CONTEXT_SUFFIX = "[1m]"


def _suffix_names(transport, data):
    """{id or alias: suffix} for the host rows declaring `contextSuffix`; {} on any other transport."""
    if model_registry is None or transport != _host():
        return {}
    return model_registry.context_suffix_names(data, transport)


def _vocabulary_name(transport, model, data):
    """The name RULE 1 checks: `model` less the suffix its row declares, else `model` unchanged."""
    name = str(model or "")
    for base, suffix in _suffix_names(transport, data).items():
        if name == base + suffix:
            return base
    return name


def _legal_pins(transport, data):
    """Every name a native pin on `transport` may use (see `native_pin`)."""
    if transport == _host():
        return native_legal_pins(transport, data or _session_transport._registry())
    return {i for _, i in _session_transport.roster(transport, data, seat=transport) if i}


# ---------------------------------------------------------------- pins and their declared tiers

def _arg_entries(args):
    """[(site, entry)] for every job an engine's rails resolve per job, in engine order. Pins are
    read by `arg_pins`, which does not depend on these sites."""
    out = [("jobs", j) for j in args.get("jobs") or []]
    for c in args.get("chains") or []:
        out += [("chains", j) for j in (c or {}).get("jobs") or []]
    out += [("lenses", l) for l in args.get("lenses") or []]
    out += [("agents", a) for a in args.get("agents") or []]
    return [(site, e if isinstance(e, dict) else {}) for site, e in out]


def script_pins(payload):
    """[(label, model)] for pins written inside a workflow script, inline or at `scriptPath`.

    A scan that could not read its whole input reports a `scan-incomplete` pin, which the
    vocabulary rule denies: silence would read like a script with no pins at all.
    """
    blobs, incomplete = script_blobs(payload)
    out = [(label, m.group(2)) for label, text in blobs for m in SCRIPT_PIN_RE.finditer(text)]
    out += [("scan-incomplete", "<unscanned: %s>" % why) for why in incomplete]
    return out


def arg_pins(args, implied=()):
    """[(label, model, tier or None, tier_is_implied)] for every object in `args` carrying `model`.

    A pin's tier is its own `tier`, else the nearest enclosing object's, else `args.tier` (the
    root). `implied` holds the `id()` of every entry `with_implied_tiers` filled from the call's own
    data, so a reader can tell the tier the call implies from the tier a dispatcher wrote.
    `__`-prefixed keys are injected by hooks, never caller pins, and are not read.
    """
    out = []

    def walk(node, tier, path, from_implied):
        if isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, tier, "%s[%d]" % (path, i), from_implied)
            return
        if not isinstance(node, dict):
            return
        tier, from_implied = node.get("tier") or tier, from_implied or id(node) in implied
        model = node.get("model")
        if model:
            label = node.get("label") or node.get("key") or node.get("name") or node.get("id") or path or "args"
            out.append((str(label), str(model), tier, bool(from_implied)))
        for key, value in node.items():
            if isinstance(key, str) and not key.startswith("__") and isinstance(value, (dict, list)):
                walk(value, tier, "%s.%s" % (path, key) if path else key, from_implied)

    walk(args, None, "", False)
    return out


def lens_tiers(args):
    """{seat key: tier} for every `args.agents` seat whose key names registry lenses, or {}."""
    try:
        import lens
        return {a["key"]: t for a in args.get("agents") or []
                if isinstance(a, dict) and (t := lens.seat_tier(a.get("key")))}
    except (Exception, SystemExit):
        return {}


def lens_mandates(args):
    """{seat key: [catalog key]} for every `args.agents` seat whose key names registry lenses, or {}."""
    try:
        import lens
        return {a["key"]: m for a in args.get("agents") or []
                if isinstance(a, dict) and (m := lens.seat_mandates(a.get("key"))) is not None}
    except (Exception, SystemExit):
        return {}


def review_panel(payload):
    """{pin label: [catalog mandates]} for every review_fanout `args.agents` seat keyed by a registry lens, else {}.
    The label is the one `call_pins` reports for the seat."""
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") != "Workflow" or _engine_of(ti) != "review_fanout":
        return {}
    args = _args(ti)
    mandates = lens_mandates(args)
    return {str(a.get("label") or a["key"]): mandates[a["key"]] for a in args.get("agents") or []
            if isinstance(a, dict) and a.get("key") in mandates}


def checker_tier():
    """The verify shape's tier (guards/tool_profiles.json): the tier `args.checker` serves."""
    try:
        return resolve_request({"shape": "verify"}).get("tier") or "fanout"
    except Exception:
        return "fanout"


def computed_checker(transport, data):
    """{model, effort?} for an engine's own verify and merge steps when the call names no `args.checker`, else None.

    The step runs on this transport's plan, so every row costs from the same allowance, and its output is checked
    mechanically: the facts settle the pin (`instruction_quality` §3). It is the cheapest row by ladder `cost`
    claiming the verify tier on this transport, at its lowest legal rung; a tie keeps registry order."""
    try:
        rows = tier_rows(checker_tier(), transport, data)
        if not rows:
            return None
        costs = model_registry.ladder_costs(data)
        row = model_registry.cheapest_first(rows, costs=costs)[0]
        out = {"model": native_pin(row, transport)}
        rungs = model_registry.effort_values(row, data)
        if rungs:
            out["effort"] = rungs[0]
        return out
    except Exception:
        return None


def implied_tiers(tool_input, args):
    """(site, field, {value: tier}) an engine fills an omitted tier from, else (None, None, {}).

    review_fanout fills an `agents` seat from its `key`; dispatch.js fills a `jobs` entry from its
    `shape`. No other engine fills a tier.
    """
    engine = _engine_of(tool_input)
    if engine == "review_fanout":
        return "agents", "key", lens_tiers(args)
    if engine == "dispatch":
        return "jobs", "shape", shape_tiers()
    return None, None, {}


def with_implied_tiers(tool_input, args):
    """(args, implied entry ids) with each entry's tier set to the tier its engine implies.

    The implied tier is a fact the call carries -- a registry lens, a shape row -- so it is the tier
    the engines run and the tier the guard checks. A lens seat's WINS over a `tier` written on the
    entry or call-wide (`instruction_quality` §3). A shape row's is the default of a job that writes no
    `tier`: the job's own wins, as on the sidecar route (`_dispatch_checks.job_tier`). The
    returned ids let `arg_pins` report which pins carry an implied tier.
    """
    site, field, tiers = implied_tiers(tool_input, args)
    implied = set()
    if site and tiers:
        filled = []
        for entry in args.get(site) or []:
            tier = tiers.get(entry.get(field)) if isinstance(entry, dict) else None
            if tier and not (field == "shape" and entry.get("tier")):
                entry = dict(entry, tier=tier)
                implied.add(id(entry))
            filled.append(entry)
        args = dict(args, **{site: filled})
    # An engine's own verify and merge steps run on `args.checker`: fanout-tier work in every engine.
    if isinstance(args.get("checker"), dict):
        checker = dict(args["checker"], tier=checker_tier())
        implied.add(id(checker))
        args = dict(args, checker=checker)
    return args, frozenset(implied)


def call_pins(payload, raw=False):
    """[(label, model, tier or None, tier_is_implied)] for every pin this call dispatches:
    `arg_pins` over the Workflow args, the script's literal pins under `args.tier`, or the Agent
    tool's `model`.

    Each model comes without a trailing host context suffix, since it names the same row; only RULE 1 passes
    `raw=True`, because it alone decides whether the suffix is legal on the session's transport.
    """
    pins = _call_pins(payload)
    if raw:
        return pins
    return [(label, _strip_context_suffix(model), tier, implied) for label, model, tier, implied in pins]


_DOC_EXT = (".md", ".txt")


def fold_tier(payload):
    """The ladder tier of a review_fanout `args.fold` pin, else None. The fold transcribes findings into its
    subjects: fanout-tier work over documents, executor-tier work once a subject holds a code path
    (`commands/plan_check.md` Phase 4)."""
    ti = payload.get("tool_input") or {}
    fold = _args(ti).get("fold") if _engine_of(ti) == "review_fanout" else None
    if not (isinstance(fold, dict) and fold.get("model")):
        return None
    files = [f for s in fold.get("subjects") or [] if isinstance(s, dict) for f in s.get("files") or []]
    return "fanout" if files and all(str(f).lower().endswith(_DOC_EXT) for f in files) else "executor"


def call_tiers(payload):
    """The sorted canonical tiers this call's pins declare or imply (`call_pins`), else None. None when the call has
    no pin, a pin carries no tier, or a tier is not legal: the dispatch gate then cannot tell which ladder slice the
    call needs and asks for the full ladder. An untiered review_fanout fold pin reads as `fold_tier`; the provider
    rules do not check that tier, so it only names the ladder slice the gate wants."""
    if model_registry is None:
        return None
    pins = call_pins(payload)
    folded = fold_tier(payload)
    tiers = set()
    for label, _model, tier, _implied in pins:
        if tier is None and label == "fold" and folded:
            tier = folded
        try:
            tiers.add(model_registry.canonical_tier(str(tier)))
        except Exception:
            return None
    return sorted(tiers) if pins and tiers else None


def _strip_context_suffix(model):
    name = str(model or "")
    return name[:-len(_HOST_CONTEXT_SUFFIX)] if name.endswith(_HOST_CONTEXT_SUFFIX) else name


def _call_pins(payload):
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Agent":
        model = ti.get("model")
        if not model:
            return []
        m = TIER_LINE_RE.search(str(ti.get("prompt") or ""))
        # A `TIER:` line is the dispatcher's own words: no Agent argument implies a tier.
        return [(str(ti.get("description") or "agent"), str(model), m.group(1) if m else None, False)]
    if payload.get("tool_name") != "Workflow":
        return []
    a, implied = with_implied_tiers(ti, _args(ti))
    call_tier = a.get("tier") or None
    out = arg_pins(a, implied)
    out += [(label, model, call_tier, False) for label, model in script_pins(payload)
            if label != "scan-incomplete"]
    return out


def tier_alias_fixes(payload, transport, data):
    """Resolve Workflow aliases within the implied tier, before the single injection writer.

    Exactly one dispatchable id is computable; zero matches keep the existing deny, and ambiguity
    denies. Family aliases can name an older row outside the implied tier.
    """
    if (payload.get("tool_name") != "Workflow" or not data or model_registry is None
            or os.environ.get("HARNESS_WORKFLOW_ARGS_OWNER")):
        return payload, [], None
    ti = payload.get("tool_input") or {}
    original = _args(ti)
    inferred, implied = with_implied_tiers(ti, original)
    notes, ambiguous = [], []

    def walk(node, facts, tier=None, is_implied=False):
        if isinstance(node, list):
            return [walk(item, facts[i], tier, is_implied) for i, item in enumerate(node)]
        if not isinstance(node, dict):
            return node
        tier = facts.get("tier") or tier
        is_implied = is_implied or id(facts) in implied
        out = dict(node)
        pin = node.get("model")
        if is_implied and tier and isinstance(pin, str):
            try:
                rows = tier_rows(model_registry.canonical_tier(tier), transport, data)
            except Exception:
                rows = []
            if pin not in {r.get("id") for r in rows}:
                matches = {r["id"] for r in rows if r.get("id")
                           and pin in (r.get("alias"), r.get("family"))}
                if len(matches) == 1:
                    out["model"] = next(iter(matches))
                    notes.append(f"tier alias: {pin} → {out['model']} ({tier})")
                elif len(matches) > 1:
                    ambiguous.append(f"{pin} ({tier}): {', '.join(sorted(matches))}")
        for key, value in node.items():
            if isinstance(key, str) and not key.startswith("__") and isinstance(value, (dict, list)):
                out[key] = walk(value, facts[key], tier, is_implied)
        return out

    fixed = walk(original, inferred)
    if ambiguous:
        return payload, [], "Ambiguous implied-tier alias — " + "; ".join(ambiguous) + ". Pin an explicit tier-row id (orchestration §5)."
    if not notes:
        return payload, [], None
    args = json.dumps(fixed) if isinstance(ti.get("args"), str) else fixed
    return dict(payload, tool_input=dict(ti, args=args)), notes, None


def served_tiers(model, data):
    """The tier tokens a pin's registry row claims (`model_registry.row_tier_claims`), in order."""
    try:
        return model_registry.row_tier_claims(model, data) if model_registry else []
    except Exception:
        return []


def _legal_tiers_text():
    return "%s (plus `validation`, which folds onto fanout)" % ", ".join(model_registry.TIER_ORDER)


def measurement(payload):
    """The call's `args.measurement` declaration when it is long enough to be one, else None.

    The one configuration a call declares about itself that a guard reads: no observable fact tells
    a benchmark arm that pins a row on purpose from an ordinary job. It skips tier fit only; an
    `effort.avoid` rung stays denied, since the declaration is text the dispatcher writes.
    """
    value = _args((payload.get("tool_input") or {})).get("measurement")
    return value.strip() if isinstance(value, str) and len(value.strip()) >= MIN_REASON_CHARS else None


def decide_tiers(payload, transport, data):
    """RULE 3. Returns (verdict, body, pins): "deny" (body = reason) or None (body = advisory
    notes); pins = [(label, model, canonical tier or None, tier_is_implied)].

    The tier the call names for a pin, written or implied, is enforced against it. A missing tier never denies.

    A review_fanout seat keyed by a registry lens is judged with its panel through `_dispatch_checks.seat_fit`:
    a pin holding the lens tier only in registry `supplementary` is admitted when a qualified seat in the same
    call carries every mandate the seat does, and denied alone (ladder Role definitions). Every other pin goes
    through `tier_fit`. A declared `args.measurement` returns before either.
    """
    pins = call_pins(payload)
    if not pins:
        return None, [], []
    if model_registry is None:
        return None, ["registry reader unavailable — tier checks not evaluated"], pins
    resolved, unknown = [], []
    for label, model, raw, implied in pins:
        tier = None
        if raw not in (None, ""):
            try:
                tier = model_registry.canonical_tier(str(raw))
            except Exception:
                unknown.append(f"{label}→{model}: {raw!r}")
        resolved.append((label, model, tier, implied))
    if unknown:
        return "deny", (
            f"(orchestration §5) Unknown tier on {', '.join(unknown[:8])}. Legal tiers: {_legal_tiers_text()}. "
            "Set a legal tier and re-issue; the tier decides which rows may serve the job, so an unknown value fails closed."), resolved
    if data is None:
        return None, ["registry unreadable — tier fit and currency not evaluated"], resolved
    measured = measurement(payload)
    if measured:
        return None, [f"measurement: {measured[:160]} — tier fit skipped"], resolved
    panel = review_panel(payload)
    unfit, rule, seats = [], [], []
    for label, model, tier, implied in resolved:
        if not tier:
            continue
        if label in panel:
            seats.append({"label": label, "model": model, "tier": tier, "mandates": panel[label],
                          "transport": transport})
            continue
        try:
            why = _dispatch_checks.tier_fit(model, tier, transport, data,
                                            pin_name=lambda r: native_pin(r, transport))
        except Exception:
            why = f"{model} is not among the `{tier}` rows on {transport} (none on this transport)"
        if why:
            unfit.append(f"{label}→{why}")
    if seats:
        try:
            verdicts = _dispatch_checks.seat_verdicts(seats, data, pin_name=lambda r: native_pin(r, transport))
        except Exception as err:
            verdicts = []
            for seat in seats:
                try:
                    why = _dispatch_checks.tier_fit(seat["model"], seat["tier"], transport, data,
                                                    pin_name=lambda r: native_pin(r, transport))
                except Exception:
                    why = (f"{seat['model']} is not among the `{seat['tier']}` rows on {transport} "
                           "(none on this transport)")
                if why:
                    verdicts.append((seat, f"{why} (panel rule not evaluated: {err!r})", False))
        for seat, line, is_rule in verdicts:
            (rule if is_rule else unfit).append(line if is_rule else f"{seat['label']}→{line}")
    if unfit or rule:
        parts = []
        if unfit:
            parts.append(
                "Pin outside the tier this call implies — " + "; ".join(unfit[:8]) + ". A seat keyed by a "
                "registry lens takes that lens's tier (`lens.py index`); a dispatch.js job takes its own "
                "`tier`, else its shape row's (`guards/tool_profiles.json`). Pin one of "
                "the tier's rows (`model_registry.py for-role <tier>`); " + EFFORT_NOT_TIER)
        if rule:
            parts.append("Supplementary seat without a qualified seat beside it — " + "; ".join(rule[:8]))
        return "deny", " ".join(parts), resolved
    return None, [], resolved


# ---------------------------------------------------------------- the owner statement

def _currency_re(transport):
    return re.compile(r"^\s*CURRENCY:\s*%s\b[\s—–:-]*(.*)$" % re.escape(transport or ""), re.I | re.M)


def stated_reason(payload, transport):
    """The currency reason this call states for `transport`, stripped, or None."""
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Agent":
        m = _currency_re(transport).search(str(ti.get("prompt") or ""))
        return m.group(1).strip() if m else None
    a = _args(ti)
    if str(a.get("currency") or "").lower() != str(transport or "").lower():
        return None
    return str(a.get("currencyReason") or "").strip()


def override_class(reason):
    """`owner` when the reason opens with it and >= MIN_REASON_CHARS of words follow, else None."""
    m = _CLASS_RE.match(reason or "")
    return m.group(1) if m and len(m.group(2).strip()) >= MIN_REASON_CHARS else None


_LIFT_YES = re.compile(r"\b(?:yes|lift|allow|approve[ds]?|go ahead)\b", re.I)
_LIFT_NO = re.compile(r"\b(?:no|don'?t|decline|keep|deny|wait)\b", re.I)


def owner_lift_found(payload, transport):
    """Did the owner of this session name `transport` for the RULE 5 lift?

    The one place an `owner:` statement is verified. Two facts pass: an owner row
    (`_owner_text.owner_quote_rows`) holding the literal token `--<transport>` outside a pasted block, or an
    answered AskUserQuestion whose question names the transport and the 5h rule and whose picked label approves
    (`_owner_text.owner_consent`, where a later owner input replaces an earlier one). The words
    the call states are display only. An unreadable transcript verifies nothing: an unverifiable lift
    neither lifts nor is held.
    """
    path = str(payload.get("transcript_path") or "")
    if not path or not transport:
        return False
    texts = _owner_text.owner_quote_rows(path) or []
    if any(_owner_text.holds_token(text, transport) for text in texts):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False
    return _owner_text.owner_consent(lines, [transport, "5h"], _LIFT_YES, _LIFT_NO)


def _owner_statement(state, transport, reason, cls, payload):
    """(words, verified_here): the owner's words lifting RULE 5 for this session.

    This call's `owner:` words when the owner's token or answer names the transport (`owner_lift_found`),
    else the words already held on the session record (itself only ever a verified lift). `(None, False)`
    when neither exists, so the caller can persist exactly what this call verified and nothing else.
    """
    if cls == OWNER_CLASS:
        quote = _class_body(reason)
        if quote and owner_lift_found(payload, transport):
            return quote, True
    held = (state or {}).get("ownerOverride")
    if isinstance(held, dict) and held.get("transport") == transport:
        words = _class_body(str(held.get("reason") or ""))
        if words:
            return words, False
    return None, False


def _class_body(reason):
    """The text after a stated class, unquoted; the whole reason when no class opens it."""
    m = _CLASS_RE.match(reason or "")
    text = (m.group(2) if m else (reason or "")).strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------- the next step a deny names

def _owner_line(seat):
    return (f"Only the owner can lift this for the session; once they have, state `args.currency: \"{seat}\"` "
            f"with `args.currencyReason: \"owner: <their words>\"` (Agent tool: a prompt line "
            f"`CURRENCY: {seat} — owner: <their words>`). It passes only when an owner turn of this session "
            f"holds the literal token `--{seat}` outside a pasted block, or the owner answered an "
            f"AskUserQuestion that names `{seat}` with a lift.")


# ---------------------------------------------------------------- five-hour width

def dispatch_width(payload):
    """Native jobs this call can run at once: one per job, chain, lens or agent it declares, else one
    per pinned object in its args, at least 1.

    A chain runs its jobs in sequence, so it counts once. A script whose width lives in its own code
    counts as 1: nothing wider can be read from its args before it runs.
    """
    if payload.get("tool_name") != "Workflow":
        return 1
    a = _args(payload.get("tool_input") or {})
    width = sum(len(a.get(k) or []) for k in ("jobs", "chains", "lenses", "agents"))
    return width or max(1, len(arg_pins(a)))


def _session_dir(payload):
    """`<transcript dir>/<session id>`, or None when the payload names no transcript. A transcript
    path already inside the session's tree (a subagent's) is cut back to the session directory."""
    transcript = str(payload.get("transcript_path") or "")
    if not transcript:
        return None
    sid = str(payload.get("session_id") or os.path.splitext(os.path.basename(transcript))[0])
    norm = os.path.normpath(transcript)
    marker = os.sep + sid + os.sep
    cut = norm.find(marker)
    return norm[:cut + len(marker) - 1] if cut >= 0 else os.path.join(os.path.dirname(norm), sid)


_TERMINAL = ("result", "failed", "error")
_AGENT_FILE = re.compile(r"^agent-(a[0-9a-f]+)\.jsonl$")
_NOTIFY_ID = re.compile(r"<task-id>(a[0-9a-f]+)</task-id>")
_STAMP = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')


def _run_live(run_dir, cutoff):
    """[{run, agentId, label}] started in this run's journal, not ended there, and fresh: its transcript,
    or the journal while it has none yet, changed at or after `cutoff`."""
    seen = {}
    with os.scandir(run_dir) as entries:
        for e in entries:
            if e.name.startswith("agent-") and e.name.endswith(".jsonl"):
                seen[e.name[len("agent-"):-len(".jsonl")]] = e.stat().st_mtime
    journal = os.path.join(run_dir, "journal.jsonl")
    journal_mtime = os.path.getmtime(journal)
    if journal_mtime < cutoff and not any(t >= cutoff for t in seen.values()):
        return []
    started, ended = {}, set()
    with open(journal, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            aid = rec.get("agentId") if isinstance(rec, dict) else None
            if not aid:
                continue
            if rec.get("type") == "started":
                started[aid] = str(rec.get("label") or aid)
            elif rec.get("type") in _TERMINAL:
                ended.add(aid)
    run = os.path.basename(run_dir)
    return [{"run": run, "agentId": aid, "label": label} for aid, label in started.items()
            if aid not in ended and seen.get(aid, journal_mtime) >= cutoff]


def _ended(transcript):
    """Is the last message record of a standalone agent transcript an assistant turn that stopped?"""
    with open(transcript, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL_BYTES))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") in ("assistant", "user"):
            return rec["type"] == "assistant" and \
                (rec.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence")
    return False


def _notifications(transcript):
    """{agent id: epoch of its latest completion notification} from the session transcript's tail, or {}.

    A background agent's completion reaches the session as a `<task-notification>` naming its
    `<task-id>`; a resumed agent keeps writing after its old notification, so the caller compares times.
    """
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - NOTIFY_TAIL_BYTES))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        if "<task-notification>" not in line:
            continue
        stamp = _STAMP.search(line)
        try:
            when = datetime.datetime.fromisoformat(stamp.group(1).replace("Z", "+00:00")).timestamp()
        except (AttributeError, ValueError):
            continue
        for aid in _NOTIFY_ID.findall(line):
            out[aid] = max(out.get(aid, 0.0), when)
    return out


def _meta_label(meta_path, fallback):
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        return str(meta.get("description") or meta.get("agentType") or fallback)
    except (OSError, ValueError, AttributeError):
        return fallback


def live_native_jobs(payload, now=None):
    """([{run, agentId, label}], note): this session's live native jobs, and what could not be read.

    A Workflow agent is live while its run journal holds its `started` line and no terminal line. An
    agent under `subagents/` itself (an Agent-tool child, or a resumed agent) has no journal: it is live
    unless its last message record is a stopped assistant turn or the session transcript holds a
    completion notification for it stamped after its last write. Either counts only while its
    transcript changed in the last LIVE_SECONDS. No tree is none live, with the reason as `note`.
    """
    session = _session_dir(payload)
    if not session:
        return [], "no transcript_path in the payload"
    sub = os.path.join(session, "subagents")
    if not os.path.isdir(sub):
        return [], None if os.path.exists(session + ".jsonl") or os.path.isdir(session) else "session dir absent"
    cutoff = (time.time() if now is None else now) - LIVE_SECONDS
    live, seen, note = [], set(), None
    workflows = os.path.join(sub, "workflows")
    if os.path.isdir(workflows):
        with os.scandir(workflows) as runs:
            for run in runs:
                if _deadline is not None and time.monotonic() > _deadline - 1.0:
                    note = "scan stopped at the hook's time budget; runs after it were not read"
                    break
                if run.is_dir() and os.path.exists(os.path.join(run.path, "journal.jsonl")):
                    for job in _run_live(run.path, cutoff):
                        live.append(job)
                        seen.add(job["agentId"])
    notified = None
    with os.scandir(sub) as entries:
        for e in entries:
            m = _AGENT_FILE.match(e.name)
            if not m or m.group(1) in seen:
                continue
            aid, mtime = m.group(1), e.stat().st_mtime
            if mtime < cutoff or _ended(e.path):
                continue
            if notified is None:
                notified = _notifications(session + ".jsonl")
            if notified.get(aid, 0.0) >= mtime - NOTIFY_SLACK_SECONDS:
                continue
            live.append({"run": "agent-" + aid, "agentId": aid,
                         "label": _meta_label(os.path.join(sub, "agent-%s.meta.json" % aid), "(no description)")})
    return live, note


def _clock(reset):
    """(HH:MMZ, "HH:MMZ, in XhYYm") for the window's reset, or ("an unknown time",) twice."""
    if not reset:
        return "an unknown time", "an unknown time"
    left = max(0, int(reset - time.time()))
    hhmm = time.strftime("%H:%MZ", time.gmtime(reset))
    return hhmm, "%s, in %dh%02dm" % (hhmm, left // 3600, left % 3600 // 60)


def _width_steps(pins, live, hhmm, data, seat):
    """One next step per job in the call: a launcher invocation serving its tier
    (`model_registry.for_role`), another plan of this route ahead of a cross-transport row, or the
    wait where neither exists."""
    runs = list(dict.fromkeys(j["run"] for j in live))
    wait = (f"wait for {', '.join(runs[:4])} or the reset at {hhmm}, then re-issue it alone" if runs else
            f"keep it native only as the session's one live job (one per call, each after the last "
            f"finishes), or wait for the reset at {hhmm}")
    steps, labels = [], set()
    for label, _model, tier, _implied in pins or [("this call", None, None, False)]:
        if label in labels:
            continue
        labels.add(label)
        hops = []
        if tier:
            try:
                res = model_registry.for_role(tier, seat, data)
                hops = ([(r, launcher) for r, _plan, launcher in res["otherPlans"]]
                        + [(r, None) for r in res["crossTransport"]])
            except Exception:
                hops = []
        hop = next((h for r, launcher in hops if (h := _dispatch_checks.hop_line(r, data, label, launcher))), None)
        if hop:
            steps.append(f"`{label}` ({tier}) → run it off this window: `{hop}`, or {wait}")
        else:
            served = f"no cross-transport row serves {tier}" if tier else "no declared tier to route"
            steps.append(f"`{label}` ({tier or 'untiered'}) → {served}: {wait}")
        if len(steps) == 8:
            break
    return steps


def five_hour_rule(payload, pins, data, seat, owner=None, plan=None):
    """RULE 5. ("deny", reason) when a hot 5-hour window would gain native concurrency; (None, None)
    otherwise, a hot window with a lifting owner statement or one live native job included: the posture line
    states the rule before the call. `seat` is the route the
    owner override names; `plan` (default `seat`) seats each job's next step in `for_role`."""
    pressure, reset, used = five_hour_window(payload.get("session_id"))
    if pressure is None:
        _TRACE["fiveHour"] = "unreadable: the band read carried no 5h window"
        return None, None
    threshold = quota_bands.FIVE_HOUR_NARROW_PRESSURE
    _TRACE["fiveHour"] = round(pressure, 2)
    if pressure <= threshold:
        return None, None
    hhmm, when = _clock(reset)
    now = time.time()
    figures = "5h quota unknown, window unknown"
    if reset and used is not None and _dispatch_checks.provider_bands is not None:
        time_left = min(1.0, max(0.0, (reset - now) / quota_bands.FIVE_HOUR_SECONDS))
        used = min(100.0, max(0.0, used))
        _, band = quota_bands.compute_band(used, reset, quota_bands.FIVE_HOUR_SECONDS, now)
        band = "Exhausted" if quota_bands.is_exhausted(used) else band
        figures = _dispatch_checks.provider_bands.window_figures(
            {"name": "five_hour", "left": 100 - used, "timeLeft": time_left * 100,
             "band": band, "resetsAt": reset}, now)
    if owner:
        _TRACE["fiveHourLifted"] = "owner override"
        return None, None
    live, note = live_native_jobs(payload)
    if note:
        _TRACE["fiveHourLive"] = "unmeasured, counted none live: " + note
    width = dispatch_width(payload)
    _TRACE["fiveHourLiveCount"], _TRACE["fiveHourWidth"] = len(live), width
    if len(live) + width <= 1:
        return None, None
    running = "; ".join(f"{j['run']} {j['label']}" for j in live[:8]) or "none"
    stated = ("The currency reason this call states is not an `owner:` statement, so it does not lift this "
              "rule. " if stated_reason(payload, seat) else "")
    return "deny", (
        f"orchestration §5b: 5h used faster than its time: one native job at a time. "
        f"{figures}; this call would make {len(live) + width} live "
        f"native jobs ({len(live)} live + {width} here). Live: {running}. Next step per job in this call, "
        f"sidecar jobs one Bash call each (reference/sidecar_dispatch.md; {_dispatch_checks.EFFORT_HOP}): "
        + "; ".join(_width_steps(pins, live, hhmm, data, plan or seat)) + ". " + stated + _owner_line(seat))


# ---------------------------------------------------------------- injection

def _rails_map(transport, data):
    """{id or alias: rail tier} for every registry row on `transport`; absent `railTier` reads detailed.

    The engines cannot read files, so this is the only route by which a per-model rail tier reaches
    a delegate's guard section. `model_registry.rail_tier` owns the reading of the field.
    """
    out = {}
    for m in (data or {}).get("models") or []:
        if not isinstance(m, dict) or m.get("transport") != transport:
            continue
        tier = model_registry.rail_tier(m.get("id"), data) if model_registry else "detailed"
        for key in (m.get("id"), m.get("alias")):
            if key:
                out[key] = tier
    return out


_VALID_SHAPES = ("any", "survey", "review", "author")


def _suffix_pins(node, names):
    """A copy of `node` with every `model` string in `names` given its suffix, walking the sites
    `arg_pins` reads (`__`-prefixed keys are hook-injected and left alone)."""
    if isinstance(node, list):
        return [_suffix_pins(item, names) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "model" and isinstance(value, str):
            out[key] = value + names.get(value, "")
        elif isinstance(key, str) and key.startswith("__"):
            out[key] = value
        else:
            out[key] = _suffix_pins(value, names)
    return out


def _guard_text(shape, tier):
    """tools/guard_text.guard_text, the one assembly of the guard files; raises on a missing section."""
    import guard_text  # tools/ is on sys.path; imported late so a broken module costs only the inline text
    return guard_text.guard_text(shape, tier)


def _engine_of(tool_input):
    """Which fan-out engine a Workflow call runs, from `scriptPath` or `name` (`-` read as `_`), or None."""
    key = os.path.basename(str(tool_input.get("scriptPath") or tool_input.get("name") or "")).replace("-", "_")
    for engine in ("dispatch_chains", "explore_fanout", "review_fanout", "dispatch"):
        if engine in key:
            return engine
    return None


def _rails_text(payload, rails_map):
    """{"<shape>/<tier>": guard text} for exactly the pairs this call's jobs resolve to, or {}.

    Mirrors each engine's own resolution: dispatch/dispatch_chains take the job's shape (default
    `any`), explore_fanout is `survey`, review_fanout is `review`; the tier is the job's `railTier`
    (dispatch.js only), else the rail map, else `detailed`; `none` gets nothing. A review_fanout
    `args.fold` adds one `author` stage, the fold, on its `model`. A pair whose assembly fails is left out, so that job
    falls back to the engine's Read pointer.
    """
    engine = _engine_of(payload.get("tool_input") or {})
    if engine is None:
        return {}
    out = {}
    for site, job in _arg_entries(_args(payload.get("tool_input") or {})):
        if engine == "explore_fanout":
            shape = "survey"
        elif engine == "review_fanout":
            shape = "review"
        else:
            shape = job.get("shape") if job.get("shape") in _VALID_SHAPES else "any"
        model = job.get("model")
        tier = (job.get("railTier") if engine == "dispatch" else None) or rails_map.get(model) or "detailed"
        key = "%s/%s" % (shape, tier)
        if tier == "none" or key in out:
            continue
        try:
            out[key] = _guard_text(shape, tier)
        except Exception:
            continue
    fold = _args(payload.get("tool_input") or {}).get("fold") if engine == "review_fanout" else None
    if isinstance(fold, dict):
        for model in (fold.get("model"),):
            key = "author/%s" % (rails_map.get(model) or "detailed")
            if model is None or key in out:
                continue
            try:
                out[key] = _guard_text("author", rails_map.get(model) or "detailed")
            except Exception:
                continue
    return out


def _driver_notes(payload, data):
    """{pinned model string: notes block} for each job and fold model whose registry row has driverNotes."""
    engine = _engine_of(payload.get("tool_input") or {})
    if engine is None:
        return {}
    args = _args(payload.get("tool_input") or {})
    models = [job.get("model") for _site, job in _arg_entries(args)]
    if engine == "review_fanout" and isinstance(args.get("fold"), dict):
        models.append(args["fold"].get("model"))
    out = {}
    for model in models:
        if not isinstance(model, str) or model in out:
            continue
        try:
            import guard_text  # tools/ is on sys.path; a broken module costs only the notes
            block = guard_text.driver_notes_block(model, data)
        except Exception:
            continue
        if block:
            out[model] = block
    return out


def _tool_profiles(payload):
    """{label: resolved profile} for every job this call declared one for.

    Only a job that NAMED a shape or a delta appears: a job that named nothing has no profile
    anybody chose, and reporting the `any` row for it would state an inventory as applied that
    nobody requested. Raises ValueError on a declared profile the resolver rejects.
    """
    out = {}
    ti = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Agent":
        request = agent_profile_request(ti)
        if request:
            out[str(ti.get("description") or "agent")] = resolve_request(request)
        return out
    for job in dispatch_jobs(payload):
        request = profile_request(job)
        if request:
            out[job["label"]] = resolve_request(request)
    return out


def tool_profile_refusal(payload):
    """The deny reason for an unusable declared tool profile, or None when nothing is wrong."""
    try:
        _tool_profiles(payload)
    except ValueError as exc:
        return ("invalid tool profile: %s. A declared profile must resolve before any agent "
                "starts — guards/tool_profiles.json holds the shapes and the logical server "
                "names, and a `tools.add`/`tools.remove` item is a built-in name or "
                "`mcp:<server>`." % exc)
    except Exception as exc:
        return "could not resolve the declared tool profile (%s); refusing the dispatch" % exc
    return None


def _declared_efforts(transport, data):
    """The transport's effort vocabulary, or None when it declares none (the engines' native rungs)."""
    cfg = ((data or {}).get("transports") or {}).get(transport) or {}
    if not isinstance(cfg, dict):
        return None
    declared = "effortValues" in cfg or "effortValues" in (cfg.get("serverSidePins") or {})
    return _session_transport.legal_effort_values(transport, data) if declared else None


def required_returns(payload):
    """Required claims/findings blocks come from each job's authored brief, not caller pins."""
    out = {}
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for job in dispatch_jobs(payload):
        path = job.get('promptPath')
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.join(repo, path)
        try:
            with open(path, encoding='utf-8') as source:
                text = source.read()
        except (OSError, UnicodeError):
            continue
        blocks = re.findall(r'```(claims|findings)\b', text)
        if blocks:
            out[job['label']] = blocks[0]
    return out


def _payload_cwd(payload):
    """The hook payload's working directory as an absolute forward-slash path, or "" when it is missing,
    relative or holds a `..` segment (review_fanout.js refuses such a `fold.repoRoot`)."""
    cwd = payload.get("cwd")
    root = cwd.strip().replace("\\", "/").rstrip("/") if isinstance(cwd, str) else ""
    if not re.match(r"^([A-Za-z]:)?/", root) or re.search(r"(^|/)\.\.(/|$)", root):
        return ""
    return root


def _git_line(cwd, *argv):
    try:
        done = subprocess.run(["git", "-C", cwd, *argv], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip().replace("\\", "/").rstrip("/") if done.returncode == 0 else ""


def _repo_root(payload):
    """The checkout the payload cwd belongs to, absolute with forward slashes: the superproject working tree
    when the cwd sits inside a submodule, else the cwd's own top level (a linked worktree is its own root).
    "" when git cannot answer. An engine builds repository-relative paths and commands against this, because the
    agents that run them resolve relative paths against a session cwd that drifts."""
    cwd = _payload_cwd(payload)
    if not cwd:
        return ""
    return _git_line(cwd, "rev-parse", "--show-superproject-working-tree") or _git_line(cwd, "rev-parse", "--show-toplevel")


def session_started(payload):
    """Epoch seconds of the first `timestamp` in the head of the session transcript, or None.

    The payload carries `transcript_path` and `session_id`, not a start time; the transcript's first
    record stamp is the session's own clock. A resumed session keeps its original stamp, which is
    older than the reload, so a resume only withholds a swap it could have made. A transcript path
    inside the session tree (a subagent's) is cut back to the session's own transcript.
    """
    transcript = str(payload.get("transcript_path") or "")
    if not transcript:
        return None
    session_dir = _session_dir(payload)
    main = session_dir + ".jsonl" if session_dir else ""
    for path in (main, transcript):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for _ in range(TRANSCRIPT_HEAD_LINES):
                    match = _STAMP.search(handle.readline())
                    if match:
                        stamp = match.group(1).replace("Z", "+00:00")
                        return datetime.datetime.fromisoformat(stamp).timestamp()
        except (OSError, ValueError):
            continue
    return None


def loadable_coverage(payload, coverage, agents_dir=None):
    """`coverage` with each entry's `covered` narrowed to shapes whose generated agent file is older than
    the session start (`session_started`). A shape with no generated type, a missing file or an unknown
    start is dropped. Never raises: any failure withholds every swap."""
    try:
        started = session_started(payload)
        table = _tp().load_table()
        agents_dir = agents_dir or AGENTS_DIR
        loaded = set()
        for shape, row in table["shapes"].items():
            agent_type = row.get("agentType")
            if not (started and agent_type):
                continue
            try:
                if os.path.getmtime(os.path.join(agents_dir, agent_type + ".md")) < started:
                    loaded.add(shape)
            except OSError:
                pass
        return {label: dict(entry, covered=[s for s in entry.get("covered") or [] if s in loaded])
                for label, entry in coverage.items()}
    except Exception:
        return {label: dict(entry, covered=[]) for label, entry in coverage.items()}


def transport_injection(payload, transport, data=None):
    """`updatedInput` adding `args.__rails`, `args.__railsText`, `args.__driverNotes`, the tool profiles and
    `args.__transport`, or None when there is nothing to inject.

    `args.__repoRoot` is the checkout root (`_repo_root`), omitted when git cannot answer. An `args.fold`
    object with no `repoRoot` gets it too, else the payload cwd: the fold router resolves an absolute citation
    against it, and a Workflow script cannot read the cwd itself. A caller-supplied `repoRoot` is kept.

    `__transport` is the engines' only model vocabulary: `ids` (every legal pin, `_legal_pins`) and,
    when the transport declares one, `efforts`. It holds no default: the caller pins every agent. An engine that receives no `__transport` fails
    closed rather than guess a vocabulary.

    A host row that declares `contextSuffix` is dispatched with it: every pin in the args naming the
    row (id or alias) is rewritten to its suffixed form, and `ids` and the `__rails` keys carry the
    suffixed form, so the engines validate and resolve the rewritten pins
    unedited. Pins written as literals inside a workflow script (inline or at `scriptPath`) are not
    rewritten and reach the runtime bare; every other rule reads the bare id through `call_pins`.
    """
    if _session_transport is None or transport == _session_transport.UNKNOWN:
        return None
    if os.environ.get("HARNESS_WORKFLOW_ARGS_OWNER"):
        return None  # another hook rewrites this session's Workflow args; two updatedInputs never merge
    if payload.get("tool_name") != "Workflow":
        return None  # the Agent tool has no script-side resolver to feed
    data = _session_transport._registry() if data is None else data
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
    suffixes = _suffix_names(transport, data)
    if suffixes:
        parsed = _suffix_pins(parsed, suffixes)

    def suffixed(name):
        return name + suffixes.get(name, "") if isinstance(name, str) else name

    parsed["__rails"] = {suffixed(k): v for k, v in _rails_map(transport, data).items()}
    # Every declared job's resolved tool profile rides beside the rails: a Workflow script cannot
    # read `guards/tool_profiles.json` itself. Resolution happens HERE so an invalid delta fails
    # the dispatch before any agent call (main() turns the ValueError into a deny).
    profiles = _tool_profiles(payload)
    verifier = resolve_request({'shape': 'verify'})
    profiles['verify'] = {'agentType': verifier['agentType'], 'tier': verifier['tier']}
    parsed['__returns'] = required_returns(payload)
    if profiles:
        parsed["__toolProfiles"] = profiles
    # A declared shape settles the start: the engine swaps general-purpose to the shape's lean type
    # when the brief is covered, so a shaped job that omits agentType needs no second choice.
    if isinstance(parsed.get("jobs"), list):
        parsed["jobs"] = [dict(j, agentType="general-purpose")
                          if isinstance(j, dict) and j.get("label") in profiles and not j.get("agentType") else j
                          for j in parsed["jobs"]]
    # Which lean shapes cover each job's brief: an engine swaps a job to its shape's agent type only
    # when this says so (a script cannot read the brief itself). The names-only skill index rides
    # with it for the swapped child, which has no Skill tool.
    parsed["__coverage"] = loadable_coverage(payload, coverage_map(payload))
    try:
        parsed["__skillIndex"] = skill_index()
    except Exception:
        pass  # no index is a smaller brief, never a wrong one
    # The shape defaults ride beside them for the stages a script spawns on its OWN label; each row
    # is `inherited`, which is what such a child actually gets.
    try:
        parsed["__toolProfileDefaults"] = profile_defaults()
    except Exception:
        pass  # an unresolvable default leaves that stage's journal entry null, never a wrong profile
    rails_text = _rails_text({"tool_name": "Workflow", "tool_input": dict(ti, args=parsed)},
                             parsed["__rails"])
    if rails_text:
        parsed["__railsText"] = rails_text
    notes = _driver_notes({"tool_name": "Workflow", "tool_input": dict(ti, args=parsed)}, data)
    if notes:
        parsed["__driverNotes"] = notes
    vocabulary = {"name": transport, "ids": sorted(suffixed(i) for i in _legal_pins(transport, data))}
    efforts = _declared_efforts(transport, data)
    if efforts is not None:
        vocabulary["efforts"] = efforts
    parsed["__transport"] = vocabulary
    repo_root = _repo_root(payload)
    if repo_root:
        parsed["__repoRoot"] = repo_root
    fold = parsed.get("fold")
    fold_root = repo_root or _payload_cwd(payload)
    if isinstance(fold, dict) and "repoRoot" not in fold and fold_root:
        parsed["fold"] = dict(fold, repoRoot=fold_root)
    if not isinstance(parsed.get("checker"), dict):
        checker = computed_checker(transport, data)
        if checker:
            parsed["checker"] = dict(checker, model=suffixed(checker["model"]))
    site, _field, implied = implied_tiers(ti, parsed)
    if implied:
        parsed["__lensTiers" if site == "agents" else "__shapeTiers"] = implied
    if site == "agents":
        # review_fanout records each seat's catalog keys on its ledger rows (tools/review_ledger.py).
        mandates = lens_mandates(parsed)
        if mandates:
            parsed["__lensMandates"] = mandates
    # Preserve the wire form: an engine that received a STRING must keep receiving one.
    ti["args"] = json.dumps(parsed) if isinstance(raw, str) else parsed
    return ti


# ---------------------------------------------------------------- vocabulary

def _known_transports():
    """Registered transport names for the fail-closed message, or a placeholder. The message is
    only reachable when the resolver already failed, so this must not raise on the same cause."""
    try:
        return model_registry.transports()
    except Exception:
        return ["(registry unreadable)"]


def decide_vocabulary(transport, source, payload, data=None):
    """RULE 1 — is every pin a name this session's ENDPOINT can serve? ("deny", reason) | (None, None).

    One predicate over the transport family: `_legal_pins`, read from the registry rows of the
    session's own transport. A pin the endpoint cannot serve is wrong at any band, so this runs
    before every other pin rule and its message is the one the caller sees.
    """
    if _session_transport is None:
        return None, None
    data = _session_transport._registry() if data is None else data
    pins = [(label, model) for label, model, _tier, _implied in call_pins(payload, raw=True)]
    pins += [p for p in script_pins(payload) if p[0] == "scan-incomplete"]
    if not pins:
        return None, None

    if transport == _session_transport.UNKNOWN:
        who = ", ".join(f"{label}→{model}" for label, model in pins[:8])
        return "deny", (
            f"(orchestration §5) This session's transport could not be identified ({source}), so no pin can be "
            f"checked against a roster, and a pin on an unidentified endpoint reaches whatever "
            f"happens to be listening. Pins on this call: {who}. Set CLAUDE_CODE_TRANSPORT to one of "
            f"{', '.join(_known_transports())} "
            f"in the launcher or profile function that started this session, then re-issue."
        )

    legal = _legal_pins(transport, data) if data else set()
    bad = [(label, model) for label, model in pins if _vocabulary_name(transport, model, data) not in legal]
    if not bad:
        return None, None
    who = ", ".join(f"{label}→{model}" for label, model in bad[:8])
    return "deny", (
        f"(orchestration §5) Pin not serviceable on transport '{transport}' (identified by {source}) — {who}. A pin this "
        f"endpoint does not serve makes agent() return null, and a fan-out reads '0 findings' — a "
        f"clean-looking run that ran nothing. Legal pins here: {', '.join(sorted(legal)) or '(none registered)'}. "
        f"Pin one of those, or dispatch to another transport through its own sidecar launcher "
        f"(reference/sidecar_dispatch.md) — Workflow/Agent never cross transports. "
        f"`model_registry.py for-role <tier>` names the rows that serve a tier."
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


def _row_on(pin, transport, data):
    """The registry row on `transport` that `pin` names (id or alias), or None."""
    for m in (data or {}).get("models") or []:
        if isinstance(m, dict) and m.get("transport") == transport and pin in (m.get("id"), m.get("alias")):
            return m
    return None


def non_thinking_review_pins(payload, transport, data):
    """[(key, pin, effort, ref)] for review_fanout agents pinned at a rung their registry row lists in
    `effort.nonThinking` -- the one engine that only dispatches judgment."""
    ti = payload.get("tool_input") or {}
    if not _is_review_fanout(ti) or model_registry is None:
        return []
    out = []
    for job in dispatch_jobs(payload):
        row = _row_on(job.get("model"), transport, data)
        if row and job.get("effort") in model_registry.non_thinking(row):
            out.append((job["label"], job["model"], job["effort"],
                        str((row.get("effort") or {}).get("nonThinkingRef") or "")))
    return out


def _effort_pins(payload, transport, data):
    """[(label, pin, effort, row)] for each distinct visible Workflow model/effort pair whose model is a
    registry row on `transport`."""
    if payload.get("tool_name") != "Workflow" or model_registry is None or not data:
        return []
    args = _args(payload.get("tool_input") or {})
    pairs = []

    def walk(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            if value.get("model") and value.get("effort"):
                pairs.append((str(value.get("label") or value.get("key") or path or "args"),
                              value["model"], value["effort"]))
            for key, item in value.items():
                if isinstance(key, str) and not key.startswith("__") and isinstance(item, (dict, list)):
                    walk(item, f"{path}.{key}" if path else key)

    walk(args, "args")
    pairs.extend((job["label"], job["model"], job["effort"])
                 for job in dispatch_jobs(payload)
                 if job.get("model") and job.get("effort"))
    pins = []
    seen = set()
    for label, pin, effort in pairs:
        if not isinstance(pin, str) or not isinstance(effort, str):
            continue  # malformed input is handled by the vocabulary rule or engine
        identity = (label, pin, effort)
        if identity in seen:
            continue
        seen.add(identity)
        row = _row_on(pin, transport, data)
        if row is not None:  # RULE 1's model vocabulary owns an unknown pin
            pins.append((label, pin, effort, row))
    return pins


def invalid_effort_pins(payload, transport, data):
    """Visible Workflow pairs whose model row cannot serve their requested effort."""
    invalid = []
    for label, pin, effort, row in _effort_pins(payload, transport, data):
        legal = model_registry.effort_values(row, data)
        if legal and effort not in legal:
            invalid.append((label, pin, effort, legal))
    return invalid


def avoided_effort_pins(payload, transport, data):
    """[(label, pin, effort, evidence)] for visible Workflow pairs at a rung the pin's row lists in
    `effort.avoid`. No declaration in the call lifts it: a rung's waste is measured in a fan-out with
    `--compare` or a benchmark campaign."""
    return [(label, pin, effort, model_registry.avoid_evidence(row))
            for label, pin, effort, row in _effort_pins(payload, transport, data)
            if effort in model_registry.avoided_rungs(row)]


LADDER_ROLE_HEADING = "## Role guidance"
LADDER_ROLE_COLUMNS = ("model", "strengths", "effort", "evidence")


def _ladder_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reference", "model_ladder_evidence.md",
    )


def parse_ladder_role_lines(raw_lines, data=None):
    """model/strengths/effort per ROW of the ladder's Role guidance table ([] on any schema drift,
    including a removed `evidence` column).

    The table SCAN is `model_registry.parse_ladder_rows` -- one reader, so a column inserted into
    either ladder table cannot shift this projection and the registry's differently. Without the
    registry this returns nothing: an advisory injection is worth less than a second parser.
    `data` is the registry (default: the live one); an unloadable registry drops only the tiers it
    would have added.
    """
    if model_registry is None:
        return []
    raw_lines = list(raw_lines)
    if data is None:
        try:
            data = model_registry.load()
        except Exception:
            data = None
    rows = model_registry.parse_ladder_rows(raw_lines, LADDER_ROLE_COLUMNS, LADDER_ROLE_HEADING)
    # Ladder order: a listing sorted by any one axis reads as the order to pick by.
    costs = {r["model"]: r["cost"].strip() for r in
             model_registry.parse_ladder_rows(raw_lines, ("model", "cost"), LADDER_ROLE_HEADING)}
    return [_compact_role_row(r, data, costs.get(r["model"])) for r in rows]


# One header for every injection of these rows (here and hooks/skill_load_marker.py).
ROLE_LADDER_HEADER = ("[role ladder (orchestration §5)] model: the tiers its registry row claims, ladder cost (higher = "
                      "cheaper), in ladder order. A pin must claim its job's tier, except a tier marked supplementary, which fills a "
                      "review seat beside a qualified seat only (ladder §Role definitions); which claimant to use is your choice "
                      "by the ladder's selection line. "
                      "Efforts and `±` are in .claude/reference/model_ladder_evidence.md. ")
_PARENS = re.compile(r"\s*\([^)]*\)")


def _compact_role_row(row, data, cost=None):
    """`model: tier, cost N` from whole tokens of a Role guidance row, never a cut clause. The effort cell is
    not copied: its first rung names one work shape, and a copy reads as the row's rung for every shape.

    Every tier the covered registry rows claim in `roles` is printed (`architect/executor`); a tier they also
    list in `supplementary` is marked in place (`architect (supplementary)/executor`), and a tier they list
    only there follows the plain tiers (`executor +architect (supplementary)`). A row covering no claimant and
    no supplementary tier prints its strengths cell's description of its work instead."""
    model = _PARENS.sub("", row["model"]).replace("`", "").strip()
    excluded = model.endswith(" excluded")
    if excluded:
        model = model[: -len(" excluded")]
    claimed, supplementary = [], []
    if data is not None:
        try:
            claimed = model_registry.ladder_cell_tiers(row["model"], data)
            supplementary = model_registry.ladder_cell_supplementary(row["model"], data)
        except Exception:
            pass
    prose = row["strengths"].split(" — ")[0].replace("`", "").strip()
    plain = "/".join(f"{t} (supplementary)" if t in supplementary else t for t in claimed)
    extra = " ".join(f"+{t} (supplementary)" for t in supplementary if t not in claimed)
    tier = " ".join(part for part in (plain, extra) if part) or prose
    if excluded:
        model += " (excluded)"
    return "%s: %s, cost %s" % (model, tier, cost if cost and cost.isdigit() else "?")


def ladder_role_lines(path=None):
    """parse_ladder_role_lines over the live ladder SSOT (empty on any failure)."""
    try:
        with open(path or _ladder_path(), encoding="utf-8") as f:
            return parse_ladder_role_lines(f)
    except Exception:
        return []


def _emit_advice(parts, updated=None):
    out = {"hookEventName": "PreToolUse"}
    if parts:
        out["additionalContext"] = "[provider check] " + " | ".join(parts)
    if updated is not None:
        out["updatedInput"] = updated
    if len(out) > 1:
        print(json.dumps({"hookSpecificOutput": out}))
    _TRACE["outcome"] = "inject" if updated is not None else ("advise" if parts else "silent")


def _cross_plan_route(payload, transport, data, plan=None):
    """The §5b route for a job whose native plan refused on capacity: the other plans with room that
    pay for a row of the call's tiers, with their launchers. `plan` is the session's paying
    plan; it defaults to `transport`, the route."""
    plan = plan or transport
    tiers = []
    try:
        for _label, model, tier, _implied in call_pins(payload):
            claimed = [tier] if tier else served_tiers(model, data)
            tiers += [t for t in claimed if t not in tiers]
        got = _dispatch_checks.provider_bands.facts(tiers or list(model_registry.TIER_ORDER), plan,
                                                    data=data, read=_dispatch_checks.read_capacity)
    except Exception:
        got = {}
    transports = (data or {}).get("transports") or {}
    rows = [r for r in got.get("plans") or [] if r.get("room") is not None and r["transport"] != plan]
    if not rows:
        return ("Native dispatch waits for this transport's reset; no cross-transport row has quota and a "
                "readable capacity. Run the job inline, or wait for the reset.")
    names = "; ".join("%s via %s" % (_dispatch_checks.provider_bands.plan_entry(
        r["transport"], _dispatch_checks.read_capacity(r["transport"], data)), ", ".join(
        v for k in ("launcher", "launcherProxy") if (v := (transports.get(r["transport"]) or {}).get(k))))
        for r in rows)
    return ("Native dispatch waits for this plan's reset; the job does not. Launch it through another "
            "plan's launcher on a row that serves its tier (`model_registry.py for-role <tier>`, "
            "reference/sidecar_dispatch.md): " + names + ".")


def _session_plan():
    """The session's paying plan; `unknown` when the resolver fails, so RULE 2 denies with a named
    cause instead of gating the route's own plan. None without a resolver, whose capacity guard is
    missing too and denies."""
    if _session_transport is None:
        return None
    try:
        return _session_transport.session_plan()
    except Exception:
        return _session_transport.UNKNOWN


def unknown_plan_refusal(transport, data):
    """The deny for a host-route session whose CLAUDE_CONFIG_DIR names no registered plan."""
    observed = os.environ.get("CLAUDE_CONFIG_DIR") or "(unset)"
    plans = [n for n, c in ((data or {}).get("transports") or {}).items()
             if isinstance(c, dict) and (n == transport or c.get("planOf") == transport)]
    return (f"Unknown plan: CLAUDE_CONFIG_DIR={observed} matches no `configDir` among the {transport} "
            f"plans ({', '.join(sorted(plans)) or 'none'}), so no capacity gate can read this session's "
            f"quota and native Workflow/Agent dispatch is blocked. Add that directory as a plan's "
            f"`configDir` in .claude/reference/external_models.json.")


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    _TRACE["outcome"] = "deny"
    _TRACE["reason"] = reason[:300]


def _timed(key, fn, *args):
    """fn(*args), with its wall seconds traced under `key` whether it returns or raises."""
    started = time.monotonic()
    try:
        return fn(*args)
    finally:
        _TRACE[key] = round(time.monotonic() - started, 2)


def _unchecked(payload, exc):
    """A check raised. A Workflow call is denied with the error, since passing it on would reach
    the engine without `__transport`; an Agent call keeps the allow it has always had."""
    frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
    where = " at %s:%s in %s" % (os.path.basename(frame.filename), frame.lineno, frame.name) if frame else ""
    error = "%s: %s%s" % (type(exc).__name__, str(exc)[:300], where)
    _TRACE["error"] = error
    if payload.get("tool_name") != "Workflow" or "outcome" in _TRACE:
        _TRACE.setdefault("outcome", "error-allowed")
        return
    _deny(
        f"The provider guard failed while checking this Workflow call ({error}; orchestration §5), so it could not "
        f"inject `args.__transport`, the model vocabulary every engine refuses to run without. "
        f"Nothing was dispatched. Re-issue the same call once; if it fails again, the failure is "
        f"not transient: run `python3 .claude/tests/test_workflow_provider_guard.py` and read "
        f"logs/{LOG_NAME}.")


def _log(payload, elapsed):
    """Append this invocation's record to the guard log. Never raises: the log is diagnosis only."""
    if _hook_state is None:
        return
    ti = payload.get("tool_input") or {}
    record = {"t": round(time.time(), 3), "sid": str(payload.get("session_id") or "")[:8],
              "tool": payload.get("tool_name"),
              "engine": _engine_of(ti) if payload.get("tool_name") == "Workflow" else None,
              "elapsed": round(elapsed, 2)}
    record.update(_TRACE)
    _hook_state.append_jsonl_rotating(_hook_state.log_path(LOG_NAME, LOG_ENV), [record])


def _load_registry():
    try:
        return model_registry.load()
    except Exception:
        return None


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") not in ("Workflow", "Agent"):
        return
    global _deadline
    _TRACE.clear()
    _five_hour.clear()
    started = time.monotonic()
    _deadline = started + DEADLINE_SECONDS
    _dispatch_checks.set_deadline(_deadline - 2.0)
    try:
        check(payload)
    except Exception as exc:
        _unchecked(payload, exc)
    _log(payload, time.monotonic() - started)


def check(payload):
    """Every rule, in order, for one Workflow or Agent payload; emits exactly one verdict."""
    payload, coverage_notes = coverage_fixes(payload)
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    # RULE -1 — a declared tool profile that does not resolve. A property of the CALL, so first.
    profile_reason = tool_profile_refusal(payload)
    if profile_reason:
        _deny(profile_reason + " (orchestration §Pre-Dispatch Checklist) Fix the shape with a +Tool delta or a wider profile, then re-issue.")
        return

    # RULE -0.5 — what coverage_fixes could not grant (an explicit removal, a fan-out need): refused before the spend.
    coverage_reason = coverage_refusal(payload)
    if coverage_reason:
        _deny(coverage_reason + " (orchestration §Pre-Dispatch Checklist)")
        return
    guard_notes = list(coverage_notes)
    if citer_coverage.BLOCKING:
        try:
            citer_reason = citer_coverage.workflow_reason(payload)
        except Exception:
            citer_reason = "citer coverage could not be checked. " + citer_coverage.REMEDY
        if citer_reason:
            _deny(citer_reason)
            return

    transport, source = (_session_transport.resolve() if _session_transport
                         else (None, "resolver-unavailable"))
    _TRACE["transport"] = transport
    data = _load_registry() if model_registry else None
    payload, alias_notes, alias_reason = tier_alias_fixes(payload, transport, data)
    if alias_reason:
        _deny(alias_reason)
        return
    tool_input = payload.get("tool_input") or {}
    guard_notes += alias_notes

    # RULE 0 — a rung the pin's registry row declares non-thinking (`effort.nonThinking`), on
    # review_fanout.js, which dispatches review lenses and nothing else: illegal there at every band.
    if tool == "Workflow":
        hits = non_thinking_review_pins(payload, transport, data)
        if hits:
            _deny("(orchestration §5) Non-thinking review pin: " + "; ".join(
                f"{key}→{pin}·{effort} ({ref or 'registry effort.nonThinking'})" for key, pin, effort, ref in hits[:8])
                + ". The registry row lists that rung in `effort.nonThinking`, and a review lens needs judgment. "
                "Raise the effort to a rung the row thinks at, or pin another row of the declared tier.")
            return

    # RULE 1 — vocabulary, before any other pin question.
    v_verdict, v_reason = decide_vocabulary(transport, source, payload)
    if v_verdict == "deny":
        _deny(v_reason)
        return
    invalid_efforts = invalid_effort_pins(payload, transport, data)
    if invalid_efforts:
        _deny("(orchestration §5) Unsupported model-specific Workflow effort: " + "; ".join(
            f"{label}→{pin}·{effort} (legal: {', '.join(legal)})"
            for label, pin, effort, legal in invalid_efforts[:8])
            + ". Choose an effort the named model actually serves; the ladder selects its rung.")
        return
    avoided = avoided_effort_pins(payload, transport, data)
    if avoided:
        _deny("(orchestration §5) Avoided Workflow effort: " + "; ".join(
            f"{label}→{pin}·{effort} ({evidence})" for label, pin, effort, evidence in avoided[:8])
            + ". The registry row lists that rung in `effort.avoid`. Choose the ladder's rung.")
        return

    # RULE 2 — capacity, read for the plan paying for this session.
    plan = _session_plan()
    _TRACE["plan"] = plan
    if _session_transport is not None and plan == _session_transport.UNKNOWN:
        _deny(unknown_plan_refusal(transport, data))
        return
    capacity_reason = (_timed("capacitySeconds", provider_capacity_guard.refusal, plan)
                       if provider_capacity_guard else
                       "Provider capacity guard is unavailable; native dispatch is blocked")
    if capacity_reason:
        _deny(f"{capacity_reason} (orchestration §5b) " + _cross_plan_route(payload, transport, data, plan))
        return

    # RULE 3 — tier, every band and every transport.
    t_verdict, t_body, pins = decide_tiers(payload, transport, data)
    if t_verdict == "deny":
        _deny(t_body)
        return

    updated = transport_injection(payload, transport) if _session_transport else None
    if (coverage_notes or alias_notes) and updated is None:
        updated = tool_input
    if _session_transport and transport != _session_transport.HOST_TRANSPORT:
        _emit_advice(list(t_body) + guard_notes, updated)
        return

    band, _pressure = _timed("bandSeconds", band_and_pressure, payload.get("session_id"))
    if band is None and _TRACE.get("band_error"):
        t_body = list(t_body) + [f"band read failed ({_TRACE['band_error'][:120]}) — the 5-hour window was not read"]
    state_path, state, _record = _session_record(payload)
    reason = stated_reason(payload, transport) or ""
    cls = override_class(reason)
    owner, verified_here = _owner_statement(state, transport, reason, cls, payload)
    # Only a lift the owner's token or answer verified is held, never the dispatcher's claim to have one.
    if verified_here and state_path:
        held = {"transport": transport, "reason": reason[:400]}
        _hook_state.update_json_locked(state_path, lambda latest: latest.__setitem__("ownerOverride", held))

    # RULE 5 — the 5-hour window bounds the session's live native jobs.
    width_verdict, width_text = _timed("fiveHourSeconds", five_hour_rule, payload, pins, data,
                                       transport, owner, plan)
    if width_verdict == "deny":
        _deny(width_text)
        return
    _emit_advice(list(t_body) + guard_notes, updated)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # only stdin that is no hook payload reaches here; main() owns every check's failure
    sys.exit(0)
