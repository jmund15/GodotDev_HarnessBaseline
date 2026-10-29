"""Re-runnable proof for hooks/workflow_provider_guard.py: declared tier, tier fit and currency.

instruction_quality §14: registration proves wiring, not matching. Every case feeds a real
PreToolUse payload to the hook's `main()` on stdin against a PLANTED synthetic registry, ladder,
band, capacity readings and session-state directory, and asserts on the emitted channel
(`permissionDecision` vs `additionalContext` vs nothing). The roster is neutral on purpose: host
transport `host`, off-quota transport `off`, rows `h-*` and `o-*`. No case depends on a real model
name; engine-specific override cases plant the script path and per-job shape they check.

    python3 .claude/tests/test_workflow_provider_guard.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console defaults to cp1252
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import quota_bands  # noqa: E402  (the band table the 5h width threshold is read from)
from _display_words import banned_in
from _transport_fixture import hook_env, planted_mcp_workdir

os.environ["SC_WORKDIR"] = planted_mcp_workdir()
HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
WORKFLOWS = os.path.join(HERE, "..", "workflows")
NODE_ENGINE_RUNNER = r"""
const fs = require('fs')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const src = fs.readFileSync(input.workflow, 'utf8')
const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const calls = []
const logs = []
const agent = async (_prompt, opts) => { calls.push(opts); return {ok: true, findings: [], checked: {}, candidates: [{name: 'n', shape: 's'}]} }
const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
const pipeline = async (items, ...stages) => Promise.all(items.map(async it => { let v; for (const s of stages) v = await s(it, v); return v }))
const fn = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', body)
fn(input.args, agent, parallel, pipeline, () => {}, line => logs.push(String(line)))
  .then(result => process.stdout.write(JSON.stringify({result, calls, logs})))
  .catch(error => process.stdout.write(JSON.stringify({thrown: String(error && error.stack || error), calls, logs})))
"""


def run_engine(name, args):
    result = subprocess.run(
        ["node", "-e", NODE_ENGINE_RUNNER],
        input=json.dumps({"workflow": os.path.join(WORKFLOWS, name + ".js"), "args": args}),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"thrown": result.stderr or result.stdout, "calls": [], "logs": []}
    return json.loads(result.stdout)


# The hook runs with hooks/ as sys.path[0]; without it `_hook_state` imports as None and no session
# record could be read or written in-process.
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
spec = importlib.util.spec_from_file_location("wpg", HOOK)
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

# ---- The planted world -------------------------------------------------------------------------
# `h-*` rows sit on the host transport and are the ladder rows of their tier; `o-*` rows sit on an
# off-quota transport and serve a tier by naming it (`o-arch`) or by claiming a host row (`o-fan`).
# No off row serves executor, orchestrator or scout.
SYN = {
    "transports": {
        "host": {"costModel": "plan-quota", "launcher": ".claude/scripts/host_sidecar.sh"},
        "off": {"costModel": "plan-quota", "launcher": ".claude/scripts/off_sidecar.sh",
                "serverSidePins": {"effortValues": ["low", "medium", "high", "max"]}},
    },
    "models": [
        {"transport": "host", "id": "host-orch-1", "alias": "h-orch", "roles": ["orchestrator"]},
        {"transport": "host", "id": "host-arch-1", "alias": "h-arch", "roles": ["architect"]},
        {"transport": "host", "id": "host-exec-1", "alias": "h-exec", "roles": ["executor"]},
        {"transport": "host", "id": "host-fan-1", "alias": "h-fan", "roles": ["fanout"],
         "effort": {"evidence": "see-ladder", "nonThinking": ["low"], "nonThinkingRef": "planted ladder cell"}},
        {"transport": "host", "id": "host-scout-1", "alias": "h-scout", "roles": ["scout"]},
        {"transport": "off", "id": "off-arch-1", "alias": "o-arch", "roles": ["architect"]},
        {"transport": "off", "id": "off-fan-1", "alias": "o-fan", "roles": ["fanout"]},
    ],
}
TIERS = {"orchestrator": ["h-orch"], "architect": ["h-arch"], "executor": ["h-exec"],
         "fanout": ["h-fan"], "scout": ["h-scout"]}


def plan_reading(used, left):
    """A normalized plan reading whose weekly routing window has `used`% spent, `left` of its time to go."""
    return {"status": "available", "quota": {"routingWindow": "seven_day", "windows": [
        {"name": "seven_day", "usedPercent": used, "resetsAt": time.time() + left * 604800,
         "windowDurationMins": 10080}]}}


# Capacity readings `_dispatch_checks.read_capacity` returns. EVEN: both plans spend at their window's pace,
# so no advisory. OFF_ROOMIER: host Hot: 7d 20% left, 90% to go, off On pace: 7d 90% left, 90% to go.
EVEN = {"host": plan_reading(50, 0.5), "off": plan_reading(50, 0.5)}
OFF_ROOMIER = {"host": plan_reading(80, 0.9), "off": plan_reading(10, 0.9)}
CURRENCY_LINE = "currency (orchestration §5b): host Hot: 7d 20% left, 90% to go, resets "
LEGAL = "orchestrator, architect, executor, fanout, scout"
EFFORT_LINE = "raise effort before tier (orchestration §5 Effort)"
# OFF_SPARE: host Hot: 7d 20% left, 90% to go; off Surplus: 7d 90% left, 30% to go.
OFF_SPARE = {"host": plan_reading(80, 0.9), "off": plan_reading(10, 0.3)}
# A retired RULE 4 lift: the guard no longer reads it, so it changes no verdict.
CAPABILITY = 'capability: "multi-file plan review is outside this row"'
OWNER_WORDS = "use anthropic only for orchestration this session"
OWNER_QUOTE = 'owner: "%s"' % OWNER_WORDS

_ABSENT = object()  # a patched attribute the module lacks is removed again, not left behind


def run_channel(payload, band="On pace", rooms=EVEN, registry_ok=True, state_dir=None,
                transport="host", data=SYN, tiers=TIERS, patches=()):
    """Feed one PreToolUse payload to the real main() on stdin; return (hookSpecificOutput, raw).

    `rooms` = {transport: normalized reading} planted behind `_dispatch_checks.read_capacity`.
    `patches` = [(owner, attribute, value)] planted after the standard world, so a case can plant a
    failure in one dependency or restore a real function the world stubs."""
    checks = getattr(wpg, "_dispatch_checks", None)
    if not hasattr(checks, "read_capacity"):
        return {"crash": "workflow_provider_guard has no _dispatch_checks.read_capacity seam"}, ""
    state_dir = state_dir or tempfile.mkdtemp(prefix="wpg_state_")
    patches = ((checks, "read_capacity", lambda t, *_a, **_k: rooms.get(t)),) + tuple(patches)
    sid = str(payload.get("session_id") or "default")
    path = os.path.join(state_dir, sid[:8] + ".json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"skills_loaded": ["orchestration"]}, fh)  # no ladder injection noise

    def load(*_a, **_k):
        if not registry_ok:
            raise RuntimeError("planted: registry unreadable")
        return data

    st, mr = wpg._session_transport, wpg.model_registry
    saved = (st.resolve, st._registry, st.HOST_TRANSPORT, wpg.band_and_pressure,
             wpg.provider_capacity_guard.refusal, mr.load, mr.role_tiers,
             wpg._hook_state.state_path, sys.stdin)
    saved_patches = [(owner, attr, getattr(owner, attr, _ABSENT)) for owner, attr, _v in patches]
    capture = io.StringIO()
    try:
        st.resolve = lambda **_k: (transport, "test")
        st._registry = lambda: data
        st.HOST_TRANSPORT = "host"
        wpg.band_and_pressure = lambda *_a, **_k: (band, None)
        wpg.provider_capacity_guard.refusal = lambda _t: None
        mr.load = load
        mr.role_tiers = lambda *_a, **_k: tiers
        wpg._hook_state.state_path = lambda s: os.path.join(state_dir, str(s or "default")[:8] + ".json")
        for owner, attr, value in patches:
            setattr(owner, attr, value)
        sys.stdin = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(capture):
            wpg.main()
    finally:
        for owner, attr, value in reversed(saved_patches):
            if value is _ABSENT:
                delattr(owner, attr)
            else:
                setattr(owner, attr, value)
        (st.resolve, st._registry, st.HOST_TRANSPORT, wpg.band_and_pressure,
         wpg.provider_capacity_guard.refusal, mr.load, mr.role_tiers,
         wpg._hook_state.state_path, sys.stdin) = saved
    raw = capture.getvalue().strip()
    return ((json.loads(raw).get("hookSpecificOutput") if raw else {}) or {}), raw


# ---- A non-host seat ---------------------------------------------------------------------------
# Route `off` has two plans, `off` and `off-b`, told apart by login dir; `far` is a third route. Each
# plan has its own launcher, so a hop names the plan that pays.
SEAT_DIRS = {"off": os.path.join(tempfile.gettempdir(), "wpg_seat_off_a"),
             "off-b": os.path.join(tempfile.gettempdir(), "wpg_seat_off_b")}
SEAT_SYN = {
    "transports": dict(
        SYN["transports"],
        off=dict(SYN["transports"]["off"], configDir=SEAT_DIRS["off"]),
        **{"off-b": {"costModel": "plan-quota", "planOf": "off", "configDir": SEAT_DIRS["off-b"],
                     "launcher": ".claude/scripts/off_b_sidecar.sh"},
           "far": {"costModel": "plan-quota", "launcher": ".claude/scripts/far_sidecar.sh"}}),
    "models": SYN["models"] + [{"transport": "far", "id": "far-arch-1", "alias": "f-arch", "roles": ["architect"]}],
}
SEAT_SYN_NO_FAR_LAUNCHER = dict(SEAT_SYN, transports=dict(SEAT_SYN["transports"], far={"costModel": "plan-quota"}))


def seat_run(payload, transport, env, rooms, data=SEAT_SYN):
    """run_channel on route `transport` with the session's plan read from `env`, as a launcher sets it."""
    keys = ("CLAUDE_CODE_TRANSPORT", "CLAUDE_CONFIG_DIR")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(env)
        return run_channel(payload, rooms=rooms, transport=transport, data=data)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


NOTE_FREE = ("dispatcher's own claim", "currency (orchestration §5b)", "probation (orchestration", "cheaper claimants",
             "transport=", "legal native pins", "[role ladder", "Death resume", "5h used faster")


# ---- Pin sites, as arg shapes ------------------------------------------------------------------
def jobs(pins, extra=None, sid="s0000001", script="dispatch.js", effort="high"):
    """`jobs` entries; each pin is `model` or `(model, tier)`."""
    return _entries("jobs", "label", pins, extra, sid, script, effort)


def agents(pins, extra=None, sid="s0000001", effort="high"):
    return _entries("agents", "key", pins, extra, sid, "review_fanout.js", effort)


def lenses(pins, extra=None, sid="s0000001"):
    return _entries("lenses", "key", pins, extra, sid, "some_engine.js")


def anywhere(args, sid="s0000001"):
    """A Workflow call whose args hold their pins wherever the caller put them."""
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/some_engine.js", "args": args}}


def _entries(key, label, pins, extra, sid, script, effort="high"):
    rows = []
    for i, pin in enumerate(pins):
        model, tier = pin if isinstance(pin, tuple) else (pin, None)
        row = {label: "j%d" % i, "promptPath": "x", "effort": effort, "agentType": "Explore"}
        if model is not None:
            row["model"] = model
        if tier is not None:
            row["tier"] = tier
        rows.append(row)
    args = {key: rows}
    args.update(extra or {})
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/" + script, "args": args}}


def chains(pins, extra=None, sid="s0000001"):
    p = _entries("jobs", "label", pins, None, sid, "dispatch_chains.js")
    args = {"chains": [{"name": "lane", "jobs": p["tool_input"]["args"]["jobs"]}]}
    args.update(extra or {})
    p["tool_input"]["args"] = args
    return p


def job(label, model, shape=None, tier="architect", **extra):
    """One dispatch.js job with exactly the fields the case cares about."""
    row = {"label": label, "promptPath": "x", "model": model, "effort": "high",
           "agentType": "general-purpose", "tier": tier}
    if shape:
        row["shape"] = shape
    row.update(extra)
    return row


def arm_job(label, model="h-arch"):
    """A rail-battery job: its built-ins come from the benchmark task row, so it declares `arm`."""
    return job(label, model, shape="arm", tools={"base": ["Read", "Glob", "Grep"]})


def job_call(rows, extra=None, sid="s0000001", script="dispatch.js"):
    args = {"jobs": rows}
    args.update(extra or {})
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/" + script, "args": args}}


def chain_call(rows, extra=None, sid="s0000001"):
    args = {"chains": [{"name": "lane", "jobs": rows}]}
    args.update(extra or {})
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/dispatch_chains.js", "args": args}}


def inline(model, extra=None, sid="s0000001"):
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"script": "await agent(p, {model: '%s', effort: 'low'})" % model,
                           "args": dict(extra or {})}}


def script_path(model, extra=None, sid="s0000001"):
    fd, path = tempfile.mkstemp(suffix=".js", prefix="wpg_script_")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("await agent(p, {model: '%s', effort: 'low'})\n" % model)
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": path, "args": dict(extra or {})}}


def agent_tool(model, lines=(), sid="s0000009"):
    return {"tool_name": "Agent", "session_id": sid, "tool_input": {
        "model": model, "subagent_type": "general-purpose",
        "prompt": "\n".join(list(lines) + ["TASK: x"])}}


def currency(reason, transport="host"):
    return {"currency": transport, "currencyReason": reason}


def channel_cases():
    """[(name, ok, detail)] for every case fed through the real output channel."""
    out = []

    def decision(hook):
        if hook.get("crash"):
            return "crash"
        return hook.get("permissionDecision") or ("advise" if hook.get("additionalContext") else "silent")

    def case(name, run, want, musts=(), absent=()):
        hook, raw = run
        text = (hook.get("permissionDecisionReason") or "") + (hook.get("additionalContext") or "")
        got = decision(hook)
        missing = [m for m in musts if m not in text]
        present = [a for a in absent if a in text]
        if "currency (orchestration §5b)" in text or "5h used faster" in text:
            present += banned_in(text)
        allowed = got == want or (want in ("allow", "advise") and got in ("advise", "silent"))
        out.append((name, allowed and not missing and not present,
                    "got %s, missing %s, unwanted %s: %s" % (got, missing, present, raw[:600])))

    # -- slice 2: a written tier binds and is checked with tier_fit, the sidecar route's rule ---
    case("On pace: jobs h-arch declared fanout -> denied: a written tier the pin does not serve",
         run_channel(jobs([("h-arch", "fanout")])), "deny", ("h-arch", "`fanout`", EFFORT_LINE))
    case("a written tier the pin serves passes",
         run_channel(jobs([("h-arch", "architect")])), "allow")
    case("a shape-implied job whose own tier the pin serves passes, the written tier winning over the shape's",
         run_channel(job_call([job("w-arch", "h-arch", shape="survey", tier="architect")])), "allow")
    case("a shape-implied job whose own tier the pin does not serve is denied",
         run_channel(job_call([job("w-fan", "h-arch", shape="survey", tier="fanout")])), "deny", ("w-fan", "`fanout`"))
    case("Hot with a roomier plan: a fitting call is allowed and carries no currency note",
         run_channel(jobs([("h-arch", "architect")]), band="Hot", rooms=OFF_ROOMIER), "allow", absent=NOTE_FREE)
    case("unknown band: a fitting call, still allowed with no note",
         run_channel(jobs([("h-arch", "architect")]), band=None), "allow", absent=NOTE_FREE + (EFFORT_LINE,))
    case("unknown band: the unfit call is still denied",
         run_channel(jobs([("h-arch", "fanout")]), band=None), "deny", ("`fanout`", EFFORT_LINE))
    case("On pace: jobs h-fan declared fanout -> allow",
         run_channel(jobs([("h-fan", "fanout")])), "allow")
    case("args.tier is the call-wide default; an entry's own tier wins",
         run_channel(jobs([("h-arch", "architect"), "h-fan"], {"tier": "fanout"})), "allow")
    case("validation folds onto fanout",
         run_channel(jobs([("h-fan", "validation")])), "allow")
    case("an Agent pin with no TIER line passes RULE 3",
         run_channel(agent_tool("h-fan")), "allow")
    case("an Agent TIER the pin does not serve -> denied",
         run_channel(agent_tool("h-arch", ["TIER: fanout"])), "deny", ("`fanout`", EFFORT_LINE))

    # -- tier declared and legal ------------------------------------------------------------------
    case("On pace: a pinned entry with no tier -> allow; a missing tier never denies",
         run_channel(jobs(["h-fan"])), "allow", absent=("No tier declared",))
    case("an unknown tier value -> deny with cite and the legal-tier next step",
         run_channel(jobs([("h-fan", "bogus")])), "deny",
         ("bogus", LEGAL, "orchestration §5", "Set a legal tier"))
    case("an unknown args.tier -> deny with cite and the legal-tier next step",
         run_channel(jobs(["h-fan"], {"tier": "bogus"})), "deny",
         ("bogus", LEGAL, "orchestration §5", "Set a legal tier"))

    # -- one case per pin site, each carrying its tier the same way -------------------------------
    sites = [
        ("jobs", lambda m: jobs([(m, "fanout")])),
        ("agents", lambda m: agents([(m, "fanout")])),
        ("lenses", lambda m: lenses([(m, "fanout")])),
        ("chains", lambda m: chains([(m, "fanout")])),
        ("inline script", lambda m: inline(m, {"tier": "fanout"})),
        ("scriptPath", lambda m: script_path(m, {"tier": "fanout"})),
        ("Agent model + TIER line", lambda m: agent_tool(m, ["TIER: fanout"])),
    ]
    for site, build in sites:
        case("site %s: fitting pin -> allow" % site, run_channel(build("h-fan")), "allow")
        case("site %s: off-tier pin -> denied naming the tier" % site,
             run_channel(build("h-arch")), "deny", ("`fanout`", EFFORT_LINE))
    case("site agents: an omitted model takes the transport's fanout pin, then fits",
         run_channel(agents([(None, "fanout")])), "allow")

    # -- any object in args carrying `model` is a pin, wherever it sits -------------------------
    for site, build in [
        ("an arg site no engine names", lambda m, t: anywhere({"panels": [{"seats": [{"model": m, "tier": t}]}]})),
        ("generators", lambda m, t: anywhere({"generators": [{"key": "g", "model": m, "tier": t}]})),
        ("critics", lambda m, t: anywhere({"critics": [{"key": "c", "model": m, "tier": t}]})),
        ("args.model", lambda m, t: anywhere({"scope": "x", "model": m, "effort": "low", "tier": t})),
        ("fixtures", lambda m, t: anywhere({"fixtures": [{"id": "f", "model": m, "tier": t}]})),
    ]:
        case("generic site %s: fitting pin -> allow" % site, run_channel(build("h-fan", "fanout")), "allow")
        case("generic site %s: off-tier pin -> denied naming the tier" % site,
             run_channel(build("h-arch", "fanout")), "deny", ("`fanout`", EFFORT_LINE))
    case("generic site: an untiered pin at an unnamed site -> allow; a missing tier never denies",
         run_channel(anywhere({"fixtures": [{"id": "f", "model": "h-fan"}]})), "allow",
         absent=("No tier declared",))
    case("generic site: a name no host row carries -> vocabulary deny cites §5 and names the retry",
         run_channel(anywhere({"generators": [{"model": "retired-name", "tier": "architect"}]})), "deny",
         ("retired-name", "h-fan", "orchestration §5", "Pin one of those"))
    case("generic site: the nearest enclosing object's tier is checked against a nested pin",
         run_channel(anywhere({"panel": {"tier": "fanout", "seats": [{"model": "h-arch"}]}})), "deny",
         ("`fanout`", EFFORT_LINE))
    case("generic site: a pin's own tier wins over its enclosing object's",
         run_channel(anywhere({"tier": "fanout", "panel": {"tier": "fanout",
                                                           "seats": [{"model": "h-arch", "tier": "architect"}]}})),
         "allow")
    case("generic site: `__`-prefixed injected keys are not read",
         run_channel(anywhere({"tier": "fanout", "model": "h-fan", "__probe": {"model": "retired-name"}})), "allow")

    # -- RULE 0: a review pin at an effort its registry row marks non-thinking ------------------
    case("RULE 0: review pin at a non-thinking rung cites §5 and names the effort fix",
         run_channel(agents([("h-fan", "fanout")], sid="s0000011", effort="low")), "deny",
         ("h-fan", "low", "effort.nonThinking", "orchestration §5", "Raise the effort"))
    case("RULE 0: the same row at a thinking rung -> allow",
         run_channel(agents([("h-fan", "fanout")], effort="medium")), "allow")
    case("RULE 0: a row that declares no non-thinking rung, at low -> allow",
         run_channel(agents([("h-arch", "architect")], effort="low")), "allow")
    case("RULE 0: a non-review engine at the non-thinking rung -> allow",
         run_channel(jobs([("h-fan", "fanout")], effort="low")), "allow")
    case("site Agent: no model pin is no dispatch pin -> nothing to decide",
         run_channel({"tool_name": "Agent", "tool_input": {"prompt": "x"}}), "allow")

    # -- currency: the plan rooms are the posture line's, so an admitted call carries no currency note -------
    for band in ("On pace", "Hot", None):
        case("band %s: a host job while off has more room -> allowed, no currency note" % band,
             run_channel(jobs([("h-arch", "architect")]), band=band, rooms=OFF_ROOMIER), "allow",
             absent=NOTE_FREE)
    case("a host job on the pick's own transport gets no currency line, in the Hot band too",
         run_channel(jobs([("h-arch", "architect")]), band="Hot"), "allow",
         absent=("currency (orchestration §5b)",))
    case("a retired currency statement lifts nothing, denies nothing and adds no note",
         run_channel(jobs([("h-arch", "architect")], currency(CAPABILITY)), band="Hot", rooms=OFF_ROOMIER),
         "allow", absent=NOTE_FREE)
    case("an Agent call gets no currency note either",
         run_channel(agent_tool("h-arch", ["TIER: architect"]), band="Hot", rooms=OFF_ROOMIER), "allow",
         absent=NOTE_FREE)
    case("an untiered pin with a roomier plan elsewhere gets no currency note",
         run_channel(jobs(["h-arch"]), rooms=OFF_ROOMIER), "allow", absent=NOTE_FREE)
    case("a tier only the host serves has no pick elsewhere -> no currency line",
         run_channel(jobs([("h-exec", "executor")]), rooms=OFF_ROOMIER), "allow",
         absent=("currency (orchestration §5b)",))
    case("an exhausted off plan is no pick",
         run_channel(jobs([("h-arch", "architect")]), rooms=dict(OFF_ROOMIER, off={"status": "exhausted"})),
         "allow", absent=("currency (orchestration §5b)",))
    case("an unreadable off plan is never ranked as roomy",
         run_channel(jobs([("h-arch", "architect")]), rooms=dict(OFF_ROOMIER, off=None)), "allow",
         absent=("currency (orchestration §5b)",))

    # -- the plan rooms are the posture line's: a plan spending faster than its window adds no note -----------
    no_deny_text = ("owner: <their words>", "`tools:`", "lifted", "off_sidecar.sh", "sidecar_fanout.py", "pick")
    case("a native job on a plan spending faster than its window -> allowed, no note and no deny text",
         run_channel(jobs([("h-arch", "architect")]), rooms=OFF_SPARE), "allow", absent=NOTE_FREE + no_deny_text)
    case("an Agent call on such a plan is allowed with no note",
         run_channel(agent_tool("h-arch", ["TIER: architect"]), rooms=OFF_SPARE), "allow", absent=NOTE_FREE)
    case("an untiered pin on such a plan is allowed with no note",
         run_channel(jobs(["h-arch"]), rooms=OFF_SPARE), "allow", absent=NOTE_FREE)
    for label, reason in (("an unclassed reason", "please let this dispatch through right now"),
                          ("a tools: reason", 'tools: "the job drives the Godot MCP tools only this session holds"'),
                          ("an owner: quote the transcript lacks", OWNER_QUOTE)):
        case("%s changes nothing: no note, no deny" % label,
             run_channel(jobs([("h-arch", "architect")], currency(reason)), rooms=OFF_SPARE), "allow",
             absent=NOTE_FREE + ("lifted",))
    case("the session plan spends slower than its window -> no currency line",
         run_channel(jobs([("h-arch", "architect")]),
                     rooms={"host": plan_reading(10, 0.3), "off": plan_reading(80, 0.9)}), "allow",
         absent=("currency (orchestration §5b)",))
    checks = getattr(wpg, "_dispatch_checks", wpg)
    case("a raising capacity read is silent on currency",
         run_channel(jobs([("h-arch", "architect")]), rooms=OFF_SPARE,
                     patches=[(checks, "read_capacity", _raiser("planted capacity read failure"))]), "allow",
         absent=("currency (orchestration §5b)",))

    # -- the session's plan comes from session_plan(), on any seat --------------------------------
    off_seat = {"CLAUDE_CODE_TRANSPORT": "off", "CLAUDE_CONFIG_DIR": SEAT_DIRS["off"]}
    case("seat off: plan far has more quota for its window -> allowed, no note",
         seat_run(jobs([("off-arch-1", "architect")]), "off", off_seat,
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(80, 0.9), "far": plan_reading(10, 0.3)}),
         "allow", absent=NOTE_FREE + ("host_sidecar.sh", "far_sidecar.sh"))
    case("seat far: plan off-b has more quota for its window -> allowed, no note",
         seat_run(jobs([("far-arch-1", "architect")]), "far", {"CLAUDE_CODE_TRANSPORT": "far"},
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(10, 0.3), "far": plan_reading(80, 0.9)}),
         "allow", absent=NOTE_FREE + ("host_sidecar.sh",))
    case("seat far: the session's own plan spends slower than its window -> no currency line",
         seat_run(jobs([("far-arch-1", "architect")]), "far", {"CLAUDE_CODE_TRANSPORT": "far"},
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(80, 0.9), "far": plan_reading(10, 0.3)}),
         "allow", absent=("currency (orchestration §5b)",))
    case("seat off on plan off: its sibling plan off-b has more quota for its window -> allowed, no note",
         seat_run(jobs([("off-arch-1", "architect")]), "off", off_seat,
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(10, 0.3), "far": plan_reading(80, 0.9)}),
         "allow", absent=NOTE_FREE)
    case("a sidecar child whose CLAUDE_CONFIG_DIR is the roomiest plan (off-b) -> no currency line",
         seat_run(jobs([("off-arch-1", "architect")]), "off",
                  {"CLAUDE_CODE_TRANSPORT": "off", "CLAUDE_CONFIG_DIR": SEAT_DIRS["off-b"]},
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(10, 0.3), "far": plan_reading(80, 0.9)}),
         "allow", absent=("currency (orchestration §5b)",))
    case("seat off: a plan with no launcher adds no note and names no hop",
         seat_run(jobs([("off-arch-1", "architect")]), "off", off_seat,
                  {"off": plan_reading(80, 0.9), "off-b": plan_reading(80, 0.9), "far": plan_reading(10, 0.3)},
                  data=SEAT_SYN_NO_FAR_LAUNCHER),
         "allow", absent=NOTE_FREE + ("far_sidecar.sh", "sidecar_fanout.py"))
    case("RULE 3 decides fit through _dispatch_checks.tier_fit",
         run_channel(job_call([job("j0", "h-fan", shape="review")]), patches=[
             (checks, "tier_fit", lambda *_a, **_k: "planted tier_fit refusal")]), "deny",
         ("planted tier_fit refusal", "Pin outside the tier this call implies"))
    case("an unreadable ladder does not suspend an implied tier",
         run_channel(job_call([job("j0", "h-fan", shape="review")]), patches=[
             (wpg, "ladder_role_lines", lambda *_a, **_k: [])]), "deny",
         ("Pin outside the tier this call implies",))

    # -- a declared measurement skips tier fit only ----------------------------------------------
    measure = {"measurement": "A/B of h-arch against h-fan on the review lens"}
    case("measurement: a declared measurement skips tier fit, and says so",
         run_channel(jobs([("h-arch", "fanout")], measure)), "advise", ("tier fit skipped",),
         ("currency still applies",))
    case("measurement: an untiered pin is allowed as any untiered pin",
         run_channel(jobs(["h-arch"], measure)), "allow")
    case("measurement: a roomier plan elsewhere still adds no currency note",
         run_channel(jobs([("h-arch", "architect")], measure), band="Hot", rooms=OFF_ROOMIER), "advise",
         ("tier fit skipped",), ("currency (orchestration §5b)",))

    # -- kept behaviour ---------------------------------------------------------------------------
    case("registry unreadable -> advisory",
         run_channel(jobs([("h-arch", "architect")]), registry_ok=False), "advise", ("registry unreadable",))
    case("vocabulary: a name no host row carries -> deny naming the legal host pins",
         run_channel(jobs([("retired-name", "fanout")])), "deny", ("retired-name", "h-fan"))
    case("vocabulary: another transport's id on the host -> deny",
         run_channel(jobs([("off-fan-1", "fanout")])), "deny", ("off-fan-1",))
    case("unknown session transport -> cite §5 and set the launcher transport",
         run_channel(jobs([("h-fan", "fanout")]), transport=wpg._session_transport.UNKNOWN), "deny",
         ("orchestration §5", "Set CLAUDE_CODE_TRANSPORT", "then re-issue"))
    case("tool-profile refusal cites the pre-dispatch checklist and names the profile fix",
         run_channel(jobs([("h-fan", "fanout")]), patches=[
             (wpg, "tool_profile_refusal", lambda _p: "invalid tool profile: missing write")]), "deny",
         ("orchestration §Pre-Dispatch Checklist", "+Tool delta or a wider profile"))
    case("brief-coverage refusal cites the pre-dispatch checklist and names the coverage fix",
         run_channel(jobs([("h-fan", "fanout")]), patches=[
             (wpg, "coverage_refusal", lambda _p: "brief coverage: ask for general-purpose")]), "deny",
         ("orchestration §Pre-Dispatch Checklist", "ask for general-purpose"))
    host_exhausted = (wpg.provider_capacity_guard, "refusal",
                      lambda t: "Provider capacity exhausted for host" if t == "host" else None)
    case("capacity refusal routes the job to the currency pick among the other plans",
         run_channel(jobs([("h-fan", "fanout")]), patches=[host_exhausted]),
         "deny", ("orchestration §5b", "off On pace: 7d 50% left, 50% to go", ".claude/scripts/off_sidecar.sh"),
         absent=("re-issue only after the provider reports availability",))
    case("capacity refusal with no other plan that has room says so",
         run_channel(jobs([("h-fan", "fanout")]), rooms=dict(EVEN, off=None), patches=[host_exhausted]),
         "deny", ("orchestration §5b", "no cross-transport row"))
    multi = json.loads(json.dumps(SYN))
    multi["models"].append({"transport": "host", "id": "host-multi-1", "alias": "h-multi",
                            "roles": ["fanout", "executor"]})
    routed = []
    real_facts = wpg._dispatch_checks.provider_bands.facts
    run_channel(jobs([("h-multi", "fanout")]), data=multi, patches=[
        host_exhausted,
        (wpg._dispatch_checks.provider_bands, "facts",
         lambda tiers, *a, **k: (routed.append(list(tiers)), real_facts(tiers, *a, **k))[1])])
    out.append(("capacity route is keyed to the written tier, not every tier the pin serves",
                routed == [["fanout"]], repr(routed)))

    # -- a sibling hook's precondition deny can be the one the client shows, so every deny here
    # leads with the dispatch preconditions when the transcript proves them unmet.
    import dispatch_mechanism_guard as dmg  # the same module object the hook imports
    tdir = tempfile.mkdtemp(prefix="wpg_tx_")
    bare_tx, loaded_tx = os.path.join(tdir, "bare.jsonl"), os.path.join(tdir, "loaded.jsonl")
    with open(bare_tx, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"type":"user","message":"hi"}\n')
    with open(loaded_tx, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"type": "tool_result", "content": "orchestration skill text"}) + "\n")
    ladder_read = (dmg.model_ladder_gate, "claim_loaded", lambda _s: True)
    ladder_unread = (dmg.model_ladder_gate, "claim_loaded", lambda _s: False)

    def with_tx(path):
        payload = jobs([("h-fan", "fanout")])
        payload["transcript_path"] = path
        return payload

    hook, raw = run_channel(with_tx(bare_tx), patches=[host_exhausted, ladder_read])
    reason = hook.get("permissionDecisionReason") or ""
    out.append(("S14: a capacity deny carries no skill-load precondition lead",
                hook.get("permissionDecision") == "deny" and "Skill(orchestration)" not in reason
                and "Provider capacity" in reason, raw[:600]))
    hook, raw = run_channel(with_tx(loaded_tx), patches=[host_exhausted, ladder_unread])
    reason = hook.get("permissionDecisionReason") or ""
    out.append(("ladder unread: the capacity deny carries no ladder lead (an unread ladder is a note)",
                hook.get("permissionDecision") == "deny" and "model_ladder_evidence" not in reason
                and "Provider capacity" in reason, raw[:600]))
    case("preconditions met: the deny carries no precondition lead",
         run_channel(with_tx(loaded_tx), patches=[host_exhausted, ladder_read]), "deny",
         ("Provider capacity",), absent=("Skill(orchestration)", "model_ladder_evidence"))
    case("no transcript: an unprovable precondition is not asserted",
         run_channel(jobs([("h-fan", "fanout")]), patches=[host_exhausted, ladder_unread]), "deny",
         ("Provider capacity",), absent=("Skill(orchestration)",))
    case("an allowed call gains no precondition text (the sibling guard owns that deny)",
         run_channel(with_tx(bare_tx), patches=[ladder_unread]), "allow", (),
         absent=("Skill(orchestration)",))
    case("provider seat: the tier checks bind there too (fitting id)",
         run_channel(jobs([("off-fan-1", "fanout")]), transport="off"), "allow")
    case("provider seat: an off-tier id -> denied naming the tier",
         run_channel(jobs([("off-arch-1", "fanout")]), transport="off"), "deny", ("`fanout`", EFFORT_LINE))
    case("provider seat: a roomier plan elsewhere adds no currency note, nor transport and pin lists",
         run_channel(jobs([("off-arch-1", "architect")]), transport="off",
                     rooms={"host": plan_reading(10, 0.9), "off": plan_reading(80, 0.9)}), "allow", absent=NOTE_FREE)
    return out


# ---- Omitted tier: a registry lens seat takes its lens's tier, a dispatch.js job its shape's --------
# Both mappings are read live (tools/lens.py, guards/tool_profiles.json); the expected tier is read
# from the same owner, and the pin is the planted host row of that tier.
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import lens as LENS  # noqa: E402
import tool_profiles as TOOL_PROFILES  # noqa: E402

OTHER_PIN = {"architect": "h-fan", "executor": "h-fan", "fanout": "h-arch"}


def seats(rows, extra=None, sid="s0000001"):
    """review_fanout `agents`; each row is (key, model) or (key, model, tier)."""
    out = []
    for row in rows:
        entry = {"key": row[0], "prompt": "p", "model": row[1], "effort": "high", "mustRead": [{"path": "x", "lines": 1}]}
        if len(row) > 2:
            entry["tier"] = row[2]
        out.append(entry)
    args = {"agents": out}
    args.update(extra or {})
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/review_fanout.js", "args": args}}


def shaped(shape, model, script="dispatch.js", tier=None, sid="s0000001"):
    job = {"label": "j0", "promptPath": "x", "model": model, "effort": "high", "agentType": "Explore"}
    if shape:
        job["shape"] = shape
    if tier:
        job["tier"] = tier
    args = {"jobs": [job]} if script == "dispatch.js" else {"chains": [{"name": "c", "jobs": [job]}]}
    return {"tool_name": "Workflow", "session_id": sid,
            "tool_input": {"scriptPath": ".claude/workflows/" + script, "args": args}}


def shape_tier(shape):
    return TOOL_PROFILES.load_table()["shapes"].get(shape, {}).get("tier")


STRA_REGISTRY = """# Structure audit

## Agent Templates

### stra-layout-hygiene (planted layout lens)

tier: fanout

```
planted
```

### stra-domain-coherence (planted domain lens)

```
planted
```
"""


@contextlib.contextmanager
def planted_agents_dir():
    """`lens.AGENTS` pointed at a copy of the shipped registries whose structure-audit file is the planted
    one above: a consumer that did not adopt the godot layer holds no real one, and every profile
    then judges the same stra seats."""
    import shutil
    saved = LENS.AGENTS
    with tempfile.TemporaryDirectory(prefix="wpg_agents_") as tmp:
        for name in os.listdir(saved):
            if name.endswith("_agents.md"):
                shutil.copy(os.path.join(saved, name), os.path.join(tmp, name))
        with open(os.path.join(tmp, LENS.REGISTRIES["structure_audit"][0]), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(STRA_REGISTRY)
        LENS.AGENTS = type(saved)(tmp)
        try:
            yield
        finally:
            LENS.AGENTS = saved


def registry_shipped(key):
    """True when the registry file holding lens `key` is installed: plan-check, session-audit and review lenses ship in the
    coding layer, so a pure project has no lens to imply a tier."""
    reg = LENS.registry_of(key.split("+")[0])
    return reg is not None and (LENS.AGENTS / LENS.REGISTRIES[reg][0]).is_file()


def implied_tier_cases():
    out = []

    def case(name, run, want, musts=(), absent=()):
        hook, raw = run
        text = (hook.get("permissionDecisionReason") or "") + (hook.get("additionalContext") or "")
        got = hook.get("permissionDecision") or "allow"
        missing = [m for m in musts if m not in text]
        present = [a for a in absent if a in text]
        if "currency (orchestration §5b)" in text or "5h used faster" in text:
            present += banned_in(text)
        out.append((name, got == want and not missing and not present,
                    "got %s, missing %s, unwanted %s: %s" % (got, missing, present, raw[:500])))

    seat_tier = getattr(LENS, "seat_tier", lambda _k: None)
    with planted_agents_dir():
        out.append(("a planted stra-layout-hygiene lens takes the tier its header `tier:` line declares",
                    seat_tier("stra-layout-hygiene") == "fanout", repr(seat_tier("stra-layout-hygiene"))))
        for key in ("plc-memory-alignment", "sa-design-semantics", "stra-layout-hygiene",
                    "stra-layout-hygiene+stra-domain-coherence"):
            if not registry_shipped(key):
                case("review seat %s: its registry is not installed, so no tier is implied -> allow" % key,
                     run_channel(seats([(key, "h-fan")])), "allow", absent=("No tier declared",))
                continue
            tier = seat_tier(key) or "executor"
            case("review seat %s with no tier takes its lens's tier (%s) -> fitting pin allowed" % (key, tier),
                 run_channel(seats([(key, TIERS[tier][0])])), "allow")
            case("review seat %s with no tier: a pin outside its lens's tier -> deny naming %s" % (key, tier),
                 run_channel(seats([(key, OTHER_PIN[tier])])), "deny", (tier, EFFORT_LINE))
    case("review seat keyed by no registry lens and no tier -> allow: a missing tier never denies",
         run_channel(seats([("zz-boundary", "h-arch")])), "allow", absent=("No tier declared",))
    lens_tier = seat_tier("plc-memory-alignment") or "executor"
    plan_check_shipped = registry_shipped("plc-memory-alignment")
    case("a seat tier written on the seat cannot replace its lens's tier",
         run_channel(seats([("plc-memory-alignment", "h-fan", "fanout")])),
         "deny" if plan_check_shipped else "allow", (lens_tier, EFFORT_LINE) if plan_check_shipped else ())
    case("a call-wide args.tier cannot replace a seat's lens tier either",
         run_channel(seats([("plc-memory-alignment", "h-fan")], {"tier": "fanout"})),
         "deny" if plan_check_shipped else "allow", (lens_tier, EFFORT_LINE) if plan_check_shipped else ())
    other_engine = seats([("exp-memory", "h-fan")])
    # A synthetic engine name: the guard resolves the engine from the basename alone, so this case
    # needs no shipped workflow.
    other_engine["tool_input"]["scriptPath"] = ".claude/workflows/explore_fanout_probe.js"
    case("a lens tier fills only review_fanout: a registry lens key on another engine implies nothing",
         run_channel(other_engine), "allow", absent=("No tier declared",))

    for shape in ("review", "survey", "author"):
        tier = shape_tier(shape) or "executor"
        case("dispatch.js job with shape %s and no tier takes the shape's tier (%s)" % (shape, tier),
             run_channel(shaped(shape, TIERS[tier][0])), "allow")
        case("dispatch.js job with shape %s and no tier: a pin outside the shape's tier -> deny" % shape,
             run_channel(shaped(shape, OTHER_PIN[tier])), "deny", (tier, EFFORT_LINE))
    case("a shape that declares no tier fills nothing -> allow: a missing tier never denies",
         run_channel(shaped(None, "h-fan")), "allow", absent=("No tier declared",))
    case("a written job tier replaces its shape's tier: the pin must serve the written one",
         run_channel(shaped("review", "h-fan", tier="fanout")), "allow")
    case("...and a pin the written tier does not serve is denied even when the shape's tier would take it",
         run_channel(shaped("review", "h-exec", tier="fanout")), "deny", ("`fanout`", EFFORT_LINE))
    case("a shape tier fills only dispatch.js: a chain job with a shape implies nothing",
         run_channel(shaped("review", "h-exec", script="dispatch_chains.js")), "allow",
         absent=("No tier declared",))
    case("a shape-implied tier is checked even when args.tier names the pin's tier",
         run_channel(job_call([job("j0", "h-fan", shape="review", tier=None)], {"tier": "fanout"})), "deny",
         (shape_tier("review") or "executor", EFFORT_LINE))

    # Data: every declared shape tier is legal, and the tiered shapes are the ones dispatch.js runs.
    rows = TOOL_PROFILES.load_table()["shapes"]
    declared = {s: r.get("tier") for s, r in rows.items() if r.get("tier") is not None}
    out.append(("every shape tier in guards/tool_profiles.json is a legal tier",
                bool(declared) and all(t in wpg.model_registry.TIER_ORDER for t in declared.values()),
                repr(declared)))

    # Guard -> engine: the same maps ride in as injected args, and each engine logs the filled tier.
    st = wpg._session_transport
    saved = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
    try:
        st.HOST_TRANSPORT = "host"
        wpg.model_registry.role_tiers = lambda *_a, **_k: TIERS
        upd = wpg.transport_injection(seats([("sa-design-semantics", "h-exec")]), "host", SYN) or {}
        injected = upd.get("args", {}).get("__lensTiers")
        sa_shipped = registry_shipped("sa-design-semantics")
        out.append(("review_fanout call gets __lensTiers for its registry seats"
                    + ("" if sa_shipped else " (none where the session-audit registry is not installed)"),
                    (injected == {"sa-design-semantics": seat_tier("sa-design-semantics")}) if sa_shipped
                    else not injected, repr(injected)))
        import lens as _lens
        # The two catalog cases run when the project's registries declare `mandates:` keys; a project with
        # no review catalog has nothing for the injection to carry.
        if _lens.seat_mandates("sa-design-semantics"):
            mandates = upd.get("args", {}).get("__lensMandates")
            out.append(("review_fanout call gets __lensMandates: each registry seat's catalog keys",
                        mandates == {"sa-design-semantics": _lens.seat_mandates("sa-design-semantics")}
                        and bool(mandates["sa-design-semantics"]), repr(mandates)))
            merged = "sa-architecture-sweep+sa-design-semantics"
            union = _lens.seat_mandates(merged)
            merged_upd = wpg.transport_injection(seats([(merged, "h-exec")]), "host", SYN) or {}
            merged_run = run_engine("review_fanout", dict(merged_upd.get("args") or {}, __transport=dict(
                TRANSPORT, ids=TRANSPORT["ids"] + ["h-exec"]), checker={"model": "h-fan", "effort": "low"}))
            row = ((merged_run.get("result") or {}).get("perAgent") or [{}])[0]
            out.append(("a merged seat's injected union reaches the engine: each of its catalog keys gets an outcome",
                        merged_upd.get("args", {}).get("__lensMandates") == {merged: union} and bool(union)
                        and sorted(row.get("mandates") or {}) == sorted(union), repr(row or merged_run)[:300]))
        run = run_engine("review_fanout", dict(upd.get("args") or {}, __transport=dict(
            TRANSPORT, ids=TRANSPORT["ids"] + ["h-exec"]), checker={"model": "h-fan", "effort": "low"}))
        pins = [l for l in run.get("logs") or [] if l.startswith("PINS ")]
        if sa_shipped:
            out.append(("review_fanout logs the lens tier for a seat that declared none",
                        any("tier:%s" % seat_tier("sa-design-semantics") in l for l in pins), repr(pins or run)[:300]))
        upd = wpg.transport_injection(shaped("review", "h-exec"), "host", SYN) or {}
        injected = upd.get("args", {}).get("__shapeTiers")
        out.append(("dispatch.js call gets __shapeTiers from the shape table",
                    injected == declared, repr(injected)))
        run = run_engine("dispatch", dict(upd.get("args") or {}, __transport=dict(
            TRANSPORT, ids=TRANSPORT["ids"] + ["h-exec"])))
        pins = [l for l in run.get("logs") or [] if l.startswith("PINS ")]
        out.append(("dispatch.js logs the shape tier for a job that declared none",
                    any("tier:%s" % shape_tier("review") in l for l in pins), repr(pins or run)[:300]))

        # The tier the guard checked is the tier the PINS row records: a lens seat's implied tier even when the
        # entry writes another one, and a dispatch.js job's own written tier over its shape's.
        upd = wpg.transport_injection(seats([("sa-design-semantics", "h-exec", "fanout")]), "host", SYN) or {}
        run = run_engine("review_fanout", dict(upd.get("args") or {}, __transport=dict(
            TRANSPORT, ids=TRANSPORT["ids"] + ["h-exec"]), checker={"model": "h-fan", "effort": "low"}))
        pins = [l for l in run.get("logs") or [] if l.startswith("PINS ")]
        if sa_shipped:
            out.append(("review_fanout logs the lens tier for a seat that writes another tier",
                        any("tier:%s" % seat_tier("sa-design-semantics") in l for l in pins),
                        repr(pins or run)[:300]))
        upd = wpg.transport_injection(shaped("review", "h-exec", tier="fanout"), "host", SYN) or {}
        run = run_engine("dispatch", dict(upd.get("args") or {}, __transport=dict(
            TRANSPORT, ids=TRANSPORT["ids"] + ["h-exec"])))
        pins = [l for l in run.get("logs") or [] if l.startswith("PINS ")]
        out.append(("dispatch.js logs the written tier for a job that writes another tier",
                    any("tier:fanout" in l for l in pins), repr(pins or run)[:300]))
    finally:
        st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved
    return out


def supplementary_seat_cases():
    """RULE 3 on a review panel: a pin holding the lens tier only in registry `supplementary` is admitted beside a
    qualified seat that carries its mandates, and denied alone. The registry is planted through `data=`."""
    out = []
    data = json.loads(json.dumps(SYN))
    data["models"].append({"transport": "host", "id": "host-supp-1", "alias": "h-supp",
                           "roles": ["executor", "fanout"], "supplementary": ["architect"]})
    merged = "plc-pattern-fit+plc-architecture-quality"

    def verdict(payload):
        try:
            return wpg.decide_tiers(payload, "host", data)[:2]
        except Exception as exc:
            return "raised", repr(exc)

    def case(name, payload, want, musts=(), absent=()):
        got, body = verdict(payload)
        text = body if isinstance(body, str) else " ".join(body)
        missing = [m for m in musts if m not in text]
        present = [a for a in absent if a in text]
        out.append((name, got == want and not missing and not present,
                    "got %s, missing %s, unwanted %s: %s" % (got, missing, present, text[:400])))

    plc = registry_shipped("plc-pattern-fit")

    def denied_where_shipped(musts, absent=()):
        """The deny the plan-check registry's lens earns, else the allow of a key no installed registry names."""
        return ("deny", musts, absent) if plc else (None, (), ())

    case("supplementary: a haiku-like seat beside a qualified merged seat carrying its mandate is allowed",
         seats([("plc-pattern-fit", "h-supp"), (merged, "h-arch")]), None)
    case("supplementary: the lone supplementary seat is denied, naming the rule and the next step",
         seats([("plc-pattern-fit", "h-supp")]),
         *denied_where_shipped(("supplementary review seat", "plc-pattern-fit", "for-role architect", "host-arch-1"),
                               ("Pin one of the tier's rows", "raise effort before tier")))
    case("supplementary: a qualified seat on other mandates does not back it",
         seats([("plc-pattern-fit", "h-supp"), ("plc-architecture-quality", "h-arch")]),
         *denied_where_shipped(("carries plc-pattern-fit.",)))
    case("supplementary: a lone supplementary seat on an executor lens is a plain claim, allowed",
         seats([("lane-code-reviewer", "h-supp")]), None)
    case("supplementary: a lone qualified architect seat is allowed",
         seats([("plc-pattern-fit", "h-arch")]), None)
    case("supplementary: a call with no lens key is untouched",
         seats([("zz-boundary", "h-supp")]), None)
    case("supplementary: a pin outside the tier still gets the tier_fit line and its tail",
         seats([("plc-pattern-fit", "h-fan")]),
         *denied_where_shipped(("Pin outside the tier this call implies", "Pin one of"), ("supplementary review seat",)))
    real = wpg._dispatch_checks.seat_verdicts
    wpg._dispatch_checks.seat_verdicts = _raiser("planted seat_verdicts failure")
    try:
        case("supplementary: a seat_verdicts failure keeps the per-seat fallback and carries the exception text",
             seats([("plc-pattern-fit", "h-supp")]), *denied_where_shipped(("planted seat_verdicts failure",)))
    finally:
        wpg._dispatch_checks.seat_verdicts = real
    return out


def injection_cases():
    """[(name, ok, detail)] for the args the guard injects and the engines consume."""
    out = []
    st = wpg._session_transport
    saved = (st.HOST_TRANSPORT, wpg.model_registry.role_tiers)
    try:
        st.HOST_TRANSPORT = "host"
        wpg.model_registry.role_tiers = lambda *_a, **_k: TIERS
        host = wpg.transport_injection(jobs([("h-fan", "fanout")]), "host", SYN)
        t = (host or {}).get("args", {}).get("__transport") or {}
        out.append(("host call gets __transport: its ids and aliases, and no default or tier pin",
                    set(t.get("ids") or []) == {r[k] for r in SYN["models"] if r["transport"] == "host"
                                                  for k in ("id", "alias")}
                    and not {"default", "tierPins", "fanoutEfforts"} & set(t)
                    and "efforts" not in t, repr(t)))
        off = wpg.transport_injection(jobs([("off-fan-1", "fanout")]), "off", SYN)
        t = (off or {}).get("args", {}).get("__transport") or {}
        out.append(("provider call gets canonical ids only and its declared efforts, no default",
                    t.get("ids") == ["off-arch-1", "off-fan-1"]
                    and t.get("efforts") == ["low", "medium", "high", "max"]
                    and not {"default", "tierPins", "fanoutEfforts"} & set(t), repr(t)))
        string_payload = jobs([("off-fan-1", "fanout")])
        string_payload["tool_input"]["args"] = json.dumps(string_payload["tool_input"]["args"])
        s = wpg.transport_injection(string_payload, "off", SYN)
        out.append(("JSON-string args stay a string and carry __transport",
                    isinstance((s or {}).get("args"), str) and "__transport" in json.loads(s["args"]), repr(s)[:200]))

        # Guard -> engine handoff, both wire forms, through the real chain engine.
        chain_args = {"chains": [{"name": "lane", "jobs": [{"label": "handoff", "promptPath": "h.md",
                                                            "model": "off-fan-1", "effort": "max",
                                                            "agentType": "Explore", "tier": "fanout"}]}]}
        for form in ("object", "string"):
            raw = chain_args if form == "object" else json.dumps(chain_args)
            upd = wpg.transport_injection({"tool_name": "Workflow", "tool_input": {
                "scriptPath": ".claude/workflows/dispatch_chains.js", "args": raw}}, "off", SYN)
            run = run_engine("dispatch_chains", upd.get("args"))
            call = (run.get("calls") or [{}])[0]
            out.append(("%s args survive injection and run in the chain engine" % form,
                        not (run.get("result") or {}).get("error") and call.get("model") == "off-fan-1"
                        and call.get("effort") == "max", repr(run)[:300]))
        declared_empty = {"transports": {"off": {"serverSidePins": {"effortValues": []}}},
                          "models": [{"transport": "off", "id": "off-fan-1", "alias": "o-fan"}]}
        upd = wpg.transport_injection({"tool_name": "Workflow", "tool_input": {
            "scriptPath": ".claude/workflows/dispatch_chains.js", "args": chain_args}}, "off", declared_empty)
        run = run_engine("dispatch_chains", upd.get("args"))
        out.append(("a declared-but-empty effort vocabulary fails loudly in the engine",
                    bool((run.get("result") or {}).get("error")) and not run.get("calls"), repr(run)[:300]))
        no_rows = {"transports": {"off": {"serverSidePins": {"effortValues": ["max"]}}}, "models": []}
        upd = wpg.transport_injection({"tool_name": "Workflow", "tool_input": {
            "scriptPath": ".claude/workflows/dispatch_chains.js", "args": chain_args}}, "off", no_rows)
        run = run_engine("dispatch_chains", upd.get("args"))
        out.append(("an empty model roster fails loudly in the engine",
                    bool((run.get("result") or {}).get("error")) and not run.get("calls"), repr(run)[:300]))

        # A fold's repoRoot is the session's working directory unless the caller wrote one.
        absent = object()

        def fold_args(fold, cwd, wire="object"):
            call = agents([("off-fan-1", "fanout")], extra=None if fold is absent else {"fold": fold})
            if cwd is not None:
                call["cwd"] = cwd
            if wire == "string":
                call["tool_input"]["args"] = json.dumps(call["tool_input"]["args"])
            upd = wpg.transport_injection(call, "off", SYN) or {}
            args = upd.get("args")
            return json.loads(args) if isinstance(args, str) else (args or {})

        subjects = [{"root": ".", "snapshot": "s.json", "files": ["a.md"]}]
        got = fold_args({"subjects": subjects}, "C:\\Users\\dev\\Repo").get("fold") or {}
        out.append(("a fold with no repoRoot gets the payload cwd as an absolute forward-slash path",
                    got.get("repoRoot") == "C:/Users/dev/Repo" and got.get("subjects") == subjects, repr(got)))
        got = fold_args({"subjects": subjects}, "/home/dev/repo/").get("fold") or {}
        out.append(("a POSIX cwd with a trailing separator loses the separator",
                    got.get("repoRoot") == "/home/dev/repo", repr(got)))
        got = fold_args({"subjects": subjects, "repoRoot": "D:/other/checkout"}, "C:/Users/dev/Repo").get("fold") or {}
        out.append(("a caller-supplied repoRoot wins over the cwd",
                    got.get("repoRoot") == "D:/other/checkout", repr(got)))
        got = fold_args({"subjects": subjects}, "C:/Users/dev/Repo", "string").get("fold") or {}
        out.append(("JSON-string args get the fold repoRoot too",
                    got.get("repoRoot") == "C:/Users/dev/Repo", repr(got)))
        args = fold_args(absent, "C:/Users/dev/Repo")
        out.append(("a call with no fold gains no fold key", "fold" not in args, repr(sorted(args))))
        for label, fold in (("null", None), ("a string", "x"), ("an array", [])):
            args = fold_args(fold, "C:/Users/dev/Repo")
            out.append(("a %s fold is left as written" % label, args.get("fold") == fold, repr(args.get("fold"))))
        for label, cwd in (("missing", None), ("relative", "work/repo"), ("holding ..", "C:/a/../b"), ("blank", "  ")):
            got = fold_args({"subjects": subjects}, cwd).get("fold") or {}
            out.append(("a %s cwd injects no repoRoot, so the engine keeps its loud flag" % label,
                        "repoRoot" not in got, repr(got)))
    finally:
        st.HOST_TRANSPORT, wpg.model_registry.role_tiers = saved
    return out


# ---- A guard that cannot build the vocabulary refuses loudly; a band read never costs it -------
REAL_BAND = wpg.band_and_pressure


def _crash_safe(run):
    """run() -> (hook, raw), or a CRASH marker: an exception out of main() is not an allow."""
    try:
        return run()
    except BaseException as exc:  # noqa: BLE001 - the proof classifies, it does not recover
        return {"crash": "%s: %s" % (type(exc).__name__, exc)}, ""


def _raiser(message):
    def boom(*_a, **_k):
        raise RuntimeError(message)
    return boom


def _band_timeout(real_run):
    """subprocess.run that times out on the band read and runs everything else for real."""
    def run(cmd, *a, **k):
        if any("budget_posture" in str(part) for part in (cmd if isinstance(cmd, (list, tuple)) else [cmd])):
            raise subprocess.TimeoutExpired(cmd, k.get("timeout") or 10)
        return real_run(cmd, *a, **k)
    return run


def _band_recorder(seen):
    def run(cmd, *a, **k):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "Surplus" + chr(9) + "0.50" + chr(10), "")
    return run


def failure_cases():
    """[(name, ok, detail)] for the vocabulary path under a planted transient failure.

    Each case plants ONE failure the guard met in production or can meet (a band read that times
    out under load, a read that raises while the injection is built) and asserts the channel: a
    Workflow call gets its vocabulary or a deny naming the error, never silence.
    """
    out = []
    log = os.path.join(tempfile.mkdtemp(prefix="wpg_log_"), "workflow_provider_guard.jsonl")
    saved_env = os.environ.get("HARNESS_PROVIDER_GUARD_LOG")
    os.environ["HARNESS_PROVIDER_GUARD_LOG"] = log

    def records():
        try:
            with open(log, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []

    def injected(hook):
        args = (hook.get("updatedInput") or {}).get("args") or {}
        return "__transport" in (json.loads(args) if isinstance(args, str) else args)

    try:
        call = jobs([("h-exec", "executor")])
        hook, raw = _crash_safe(lambda: run_channel(call, band="Surplus"))
        out.append(("normal Workflow call injects __transport through main()",
                    injected(hook) and not hook.get("permissionDecision"), (hook.get("crash") or raw)[:300]))
        rec = (records() or [{}])[-1]
        out.append(("each invocation appends one diagnosable record (tool, outcome, elapsed)",
                    rec.get("tool") == "Workflow" and rec.get("outcome") == "inject"
                    and isinstance(rec.get("elapsed"), (int, float)), repr(rec)[:300]))

        hook, raw = _crash_safe(lambda: run_channel(call, band="Surplus", patches=[
            (wpg, "band_and_pressure", REAL_BAND),
            (wpg.subprocess, "run", _band_timeout(subprocess.run))]))
        out.append(("a band read that times out still injects __transport",
                    injected(hook) and not hook.get("permissionDecision"), (hook.get("crash") or raw)[:300]))
        rec = (records() or [{}])[-1]
        out.append(("...and its record names the band timeout",
                    "TimeoutExpired" in str(rec.get("band_error") or ""), repr(rec)[:300]))

        seen = []
        _crash_safe(lambda: run_channel(dict(call, session_id="bandsid1"), band="Surplus", patches=[
            (wpg, "band_and_pressure", REAL_BAND), (wpg.subprocess, "run", _band_recorder(seen))]))
        cmd = next((c for c in seen if any("budget_posture" in str(part) for part in c)), [])
        out.append(("the band read names this session, so it reads one capture, not the TEMP scan",
                    "--session" in cmd and "bandsid1" in cmd, repr(cmd)[:300]))

        for spent in (38.0, 42.8):
            clock, timeouts = [1000.0], []

            def slow_capacity(_t, spent=spent):
                clock[0] += spent
                return None

            def band_run(cmd, *_a, **k):
                timeouts.append(k.get("timeout"))
                return subprocess.CompletedProcess(cmd, 0, "Surplus" + chr(9) + "0.50" + chr(10), "")

            hook, raw = _crash_safe(lambda: run_channel(call, band="Surplus", patches=[
                (wpg, "band_and_pressure", REAL_BAND), (wpg.subprocess, "run", band_run),
                (wpg.time, "monotonic", lambda: clock[0]),
                (wpg.provider_capacity_guard, "refusal", slow_capacity)]))
            left = wpg.DEADLINE_SECONDS - spent if hasattr(wpg, "DEADLINE_SECONDS") else None
            bounded = (not timeouts and "skipped" in str((records() or [{}])[-1].get("band_error"))) \
                if left is not None and left < 0.5 else bool(timeouts) and timeouts[-1] <= (left or 0) + 0.01
            out.append(("a %.1f s capacity probe leaves the band read only the time before the hook's kill"
                        % spent, injected(hook) and bounded, repr((timeouts, left, (records() or [{}])[-1]))[:300]))

        for site, owner, attr in (("brief coverage", wpg, "coverage_map"),
                                  ("rail map", wpg, "_rails_map"),
                                  ("legal pins", wpg._session_transport, "_dispatchable_rows")):
            planted = "planted transient read in %s" % site
            hook, raw = _crash_safe(lambda: run_channel(call, band="Surplus",
                                                        patches=[(owner, attr, _raiser(planted))]))
            reason = hook.get("permissionDecisionReason") or ""
            out.append(("a transient failure in the %s denies with the §5 cite and retry hint" % site,
                        hook.get("permissionDecision") == "deny" and planted in reason
                        and "orchestration §5" in reason and "Re-issue the same call once" in reason,
                        (hook.get("crash") or raw)[:300]))
        rec = (records() or [{}])[-1]
        out.append(("...and its record carries the error text",
                    rec.get("outcome") == "deny" and "planted transient read" in str(rec.get("error") or ""),
                    repr(rec)[:300]))

        agent = agent_tool("h-exec", ["TIER: executor"])
        hook, raw = _crash_safe(lambda: run_channel(agent, band="Surplus", patches=[
            (wpg, "decide_tiers", _raiser("planted agent-path failure"))]))
        out.append(("an Agent call (no vocabulary to inject) keeps its allow path on a guard failure",
                    not hook.get("crash") and hook.get("permissionDecision") != "deny",
                    (hook.get("crash") or raw)[:300]))
    finally:
        if saved_env is None:
            os.environ.pop("HARNESS_PROVIDER_GUARD_LOG", None)
        else:
            os.environ["HARNESS_PROVIDER_GUARD_LOG"] = saved_env
    return out


# ---- Engines: no injected vocabulary -> fail closed; injected -> run and log the tier ----------
TRANSPORT = {"name": "host", "ids": ["h-fan", "h-arch", "host-fan-1", "host-arch-1"]}
HOT_5H = 1.44
COOL_5H = 1.0


def plant_session(runs=(), top=(), notes=(), owner=(), queued=(), cross=(), answers=(), sid="s5h00001"):
    """A planted transcript tree; returns the `transcript_path` a hook payload would carry.

    `runs` = [(run id, [(agent id, label, state, age seconds)])] where state is `live` (a started
    line only), `result` or `failed`; `top` = [(agent id, description, ended, age seconds)] for
    `<session>/subagents/agent-<id>.jsonl`, whose last record is an end_turn when `ended`;
    `notes` = [(agent id, age seconds)] for a completion notification in the parent transcript.
    `owner`/`queued` plant owner rows the `owner:` lift may be verified in; `answers` = [(question, picked label)] plant answered AskUserQuestions; `cross` plants the
    same words as a cross-session message, which is a peer talking, not the owner."""
    root = tempfile.mkdtemp(prefix="wpg_5h_")
    session = os.path.join(root, sid)
    now = time.time()
    for run, agents_ in runs:
        run_dir = os.path.join(session, "subagents", "workflows", run)
        os.makedirs(run_dir)
        lines = [{"type": "launched"}]
        for aid, label, state, age in agents_:
            lines.append({"type": "started", "key": "k" + aid, "agentId": aid, "label": label, "phase": "P"})
            if state != "live":
                lines.append({"type": state, "key": "k" + aid, "agentId": aid})
            path = os.path.join(run_dir, "agent-%s.jsonl" % aid)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"stop_reason": "tool_use"}}) + "\n")
            os.utime(path, (now - age, now - age))
        with open(os.path.join(run_dir, "journal.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(l) + "\n" for l in lines))
    for aid, description, ended, age in top:
        sub = os.path.join(session, "subagents")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "agent-%s.meta.json" % aid), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"description": description}, fh)
        path = os.path.join(sub, "agent-%s.jsonl" % aid)
        last = {"type": "assistant", "message": {"stop_reason": "end_turn" if ended else "tool_use"}}
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"type": "user", "message": {}}) + "\n" + json.dumps(last) + "\n")
        os.utime(path, (now - age, now - age))
    transcript = os.path.join(root, sid + ".jsonl")
    with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        for i, words in enumerate(owner):
            fh.write(json.dumps({"type": "user", "uuid": "own%d" % i,
                                 "message": {"role": "user", "content": words}}) + "\n")
        for i, words in enumerate(queued):
            fh.write(json.dumps({"type": "attachment", "uuid": "que%d" % i,
                                 "attachment": {"type": "queued_command", "prompt": words}}) + "\n")
        for i, words in enumerate(cross):
            fh.write(json.dumps({"type": "user", "uuid": "xse%d" % i,
                                 "message": {"role": "user",
                                             "content": '<cross-session-message from="peer">%s'
                                                        '</cross-session-message>' % words}}) + "\n")
        for i, (question, label) in enumerate(answers):
            fh.write(json.dumps({"type": "user", "uuid": "ans%d" % i,
                                 "message": {"role": "user", "content": [
                                     {"type": "tool_result", "tool_use_id": "toolu_a%d" % i, "content": label}]},
                                 "toolUseResult": {"questions": [{"question": question}],
                                                   "answers": {question: label}}}) + "\n")
        for aid, age in notes:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - age))
            fh.write(json.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": stamp,
                                 "content": "<task-notification>\n<task-id>%s</task-id>\n"
                                            "<status>completed</status>" % aid}) + "\n")
    return transcript


def with_transcript(payload, transcript, sid="s5h00001"):
    return dict(payload, session_id=sid, transcript_path=transcript)


def five_hour_cases():
    """[(name, ok, detail)] for the 5-hour rule: a hot window admits no added native concurrency."""
    out = []
    log = os.path.join(tempfile.mkdtemp(prefix="wpg_log_"), "workflow_provider_guard.jsonl")
    saved_env = os.environ.get("HARNESS_PROVIDER_GUARD_LOG")
    os.environ["HARNESS_PROVIDER_GUARD_LOG"] = log
    reset = time.time() + 2 * 3600

    def window(p, used=None):
        # The capture's own used percent rides with the pace; at HOT_5H and 2h left it is 86.4%.
        used = p * 60.0 if used is None and p is not None else used
        return [(wpg, "five_hour_window",
                 lambda *_a, **_k: (p, reset if p is not None else None, used))]

    def verdict(run):
        hook, raw = run
        if hook.get("crash"):
            return "crash", hook["crash"]
        text = (hook.get("permissionDecisionReason") or "") + (hook.get("additionalContext") or "")
        return (hook.get("permissionDecision") or "allow"), text or raw

    def case(name, run, want, musts=(), absent=()):
        got, text = verdict(run)
        missing = [m for m in musts if m not in text]
        present = [a for a in absent if a in text]
        if "currency (orchestration §5b)" in text or "5h used faster" in text:
            present += banned_in(text)
        out.append((name, got == want and not missing and not present,
                    "got %s, missing %s, unwanted %s: %s" % (got, missing, present, text[:700])))

    def run(payload, p5, used=None, **k):
        return _crash_safe(lambda: run_channel(payload, patches=window(p5, used) + list(k.pop("patches", ())), **k))

    one_live = plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])])
    none_live = plant_session()
    arch = jobs([("h-arch", "architect")])
    exec_ = jobs([("h-exec", "executor")])
    try:
        case("5h hot, one live native job -> a new single-job dispatch is denied",
             run(with_transcript(arch, one_live), HOT_5H), "deny",
             ("5h 14% left, 40% to go", "wf_live-001", "review:alpha", "o-arch", ".claude/scripts/off_sidecar.sh",
              "or wait for wf_live-001 or the reset at"))
        case("5h hot, no live job -> a single-job dispatch is allowed",
             run(with_transcript(arch, none_live), HOT_5H), "allow",
             absent=("5h used faster", "one native job", "cross-transport row"))
        case("the 5h figures print the capture's used percent, never one back-derived from the pace",
             run(with_transcript(arch, one_live), HOT_5H, used=50.0), "deny",
             ("5h 50% left, 40% to go",), ("5h 14% left",))
        case("a missing provider_bands module degrades the 5h figures to unknown, never raises",
             run(with_transcript(arch, one_live), HOT_5H,
                 patches=[(wpg._dispatch_checks, "provider_bands", None)]), "deny",
             ("5h quota unknown, window unknown",))
        out.append(("the 5h window length is one named constant shared with the posture hook",
                    getattr(quota_bands, "FIVE_HOUR_SECONDS", None) == 5 * 3600,
                    repr(getattr(quota_bands, "FIVE_HOUR_SECONDS", None))))
        case("5h hot, no live job -> a two-job dispatch is denied",
             run(with_transcript(jobs([("h-arch", "architect"), ("h-exec", "executor")]), none_live), HOT_5H),
             "deny", ("5h 14% left, 40% to go", "o-arch", "wait"))
        case("5h cool -> any width is allowed, live jobs or not",
             run(with_transcript(jobs([("h-arch", "architect"), ("h-exec", "executor"), ("h-fan", "fanout")]),
                                 one_live), COOL_5H), "allow", absent=("wf_live-001",))
        # The width threshold is the band table's `On pace` ceiling — past it the window reads
        # Ahead or Hot — so it is read from BANDS rather than authored again beside it, and the
        # guard's comparison must turn over exactly there.
        on_pace = next(bound for bound, name, _desc in quota_bands.BANDS if name == "On pace")
        out.append(("the narrow width threshold is the On pace ceiling read from BANDS",
                    quota_bands.FIVE_HOUR_NARROW_PRESSURE == on_pace,
                    "threshold=%r on-pace ceiling=%r" % (quota_bands.FIVE_HOUR_NARROW_PRESSURE, on_pace)))
        case("a 5h pressure just past the On pace ceiling denies added width",
             run(with_transcript(jobs([("h-arch", "architect"), ("h-exec", "executor")]), none_live),
                 on_pace + 0.01), "deny")
        case("a 5h pressure exactly at the On pace ceiling admits width",
             run(with_transcript(jobs([("h-arch", "architect"), ("h-exec", "executor")]), none_live),
                 on_pace), "allow")
        case("5h unreadable -> no deny", run(with_transcript(arch, one_live), None), "allow")
        rec = next((r for r in reversed(_records(log)) if "fiveHour" in r), {})
        out.append(("...and the guard log records that the window was unreadable",
                    "unreadable" in str(rec.get("fiveHour")), repr(rec)[:300]))
        finished = plant_session(runs=[("wf_done-001", [("b1", "done:one", "result", 30),
                                                        ("b2", "done:two", "failed", 30)]),
                                       ("wf_stale-01", [("c1", "stale:one", "live", 20 * 60)])],
                                 top=[("a0d1000000000001", "ended agent", True, 30),
                                      ("a0d2000000000002", "stale agent", False, 20 * 60)])
        case("5h hot: a finished run, an ended agent and stale transcripts are not live",
             run(with_transcript(arch, finished), HOT_5H), "allow")
        top_live = plant_session(top=[("a0e1000000000001", "resumed builder", False, 45)])
        case("5h hot: a resumed agent whose transcript is fresh and has no end_turn counts as live",
             run(with_transcript(agent_tool("h-exec", ["TIER: executor"]), top_live), HOT_5H), "deny",
             ("resumed builder", "wait"), absent=("o-arch",))
        case("deny text: an executor-tier job has no cross-transport row and can only wait",
             run(with_transcript(exec_, one_live), HOT_5H), "deny", ("executor", "wait"), absent=("o-arch",))
        case("5h hot, missing transcript dir -> none live, a single job is allowed",
             run(dict(arch, transcript_path=os.path.join(none_live + "x")), HOT_5H), "allow")
        case("5h hot on a provider seat -> the host's 5h window does not gate its native dispatch",
             run(with_transcript(jobs([("off-arch-1", "architect")]), one_live), HOT_5H, transport="off"), "allow")
        case("a failure inside the 5h check denies a Workflow call with the error, never drops __transport",
             run(with_transcript(arch, one_live), HOT_5H,
                 patches=[(wpg, "live_native_jobs", _raiser("planted live-job scan failure"))]),
             "deny", ("planted live-job scan failure", "Re-issue"))

        # An `owner:` statement lifts only when an owner turn holds the `--host` token; no answered question lifts.
        owned_live = plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                   owner=[OWNER_WORDS + " --host"])
        owner = {"currency": "host", "currencyReason": OWNER_QUOTE}
        shared = tempfile.mkdtemp(prefix="wpg_state_")
        case("the 5h deny cites the rule it enforces and names the owner override as the owner's to grant",
             run(with_transcript(arch, one_live), HOT_5H), "deny",
             ("orchestration §5b", "Only the owner", "owner: <their words>", "-l j0"), absent=("AskUserQuestion",))
        case("an owner: statement lifts the 5h rule",
             run(with_transcript(jobs([("h-arch", "architect")], extra=owner), owned_live), HOT_5H,
                 state_dir=shared), "allow", absent=("lifted for this session", "5h used faster"))
        case("...and holds for the rest of the session without being restated",
             run(with_transcript(arch, one_live), HOT_5H, state_dir=shared), "allow")
        case("an unclassed reason does not lift the 5h rule, and the deny says why",
             run(with_transcript(jobs([("h-arch", "architect")], extra={
                 "currency": "host", "currencyReason": "please let this dispatch through right now"}), one_live),
                 HOT_5H), "deny", ("is not an `owner:` statement",))

        noted = plant_session(top=[("a0f1000000000001", "notified agent", False, 60),
                                   ("a0f2000000000002", "resumed agent", False, 30)],
                              notes=[("a0f1000000000001", 20), ("a0f2000000000002", 600)])
        case("a top-level agent with a completion notification after its last write is not live; "
             "one resumed after its notification is",
             run(with_transcript(arch, noted), HOT_5H), "deny", ("resumed agent",), absent=("notified agent",))

        owner_quote = OWNER_QUOTE
        shared = tempfile.mkdtemp(prefix="wpg_state_")
        case("owner: a verified owner statement lifts the 5h rule and is held",
             run(with_transcript(jobs([("h-arch", "architect")], currency(owner_quote)), owned_live), HOT_5H,
                 band="Hot", state_dir=shared), "allow",
             absent=("owner: owner:", "lifted for this session", "5h used faster"))
        try:
            with open(os.path.join(shared, "s5h00001.json"), encoding="utf-8") as fh:
                held = json.load(fh).get("ownerOverride") or {}
        except OSError:
            held = {}
        out.append(("owner: ...and the session record holds it for the rest of the session",
                    held.get("transport") == "host" and "use anthropic only" in str(held.get("reason")),
                    repr(held)[:300]))
        def lift_case(name, transcript, verdict, **kw):
            case(name, run(with_transcript(jobs([("h-arch", "architect")], currency(OWNER_QUOTE)), transcript),
                           HOT_5H, state_dir=tempfile.mkdtemp(prefix="wpg_state_")), verdict, **kw)
        lift_case("owner: a quote that is a substring of an owner turn without the --host token does not lift",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                owner=[OWNER_WORDS + " please"]), "deny", musts=("5h 14% left, 40% to go",))
        lift_case("owner: --host inside a pasted block does not lift",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                owner=['<pasted_content id="p1">use --host</pasted_content> ' + OWNER_WORDS]),
                  "deny", musts=("5h 14% left, 40% to go",))
        lift_case("owner: --host in a cross-session message does not lift",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                cross=["--host"]), "deny", musts=("5h 14% left, 40% to go",))
        lift_case("owner: --hostile is not the --host token",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                owner=["--hostile"]), "deny", musts=("5h 14% left, 40% to go",))
        lift_case("owner: an answered question naming the transport and approving the lift does not lift (only the token does)",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                answers=[("Lift the 5h one-job rule on host for this session?", "Yes, lift it")]),
                  "deny", musts=("5h 14% left, 40% to go",))
        lift_case("owner: the --host token still lifts beside an answered question",
                  plant_session(runs=[("wf_live-001", [("a1", "review:alpha", "live", 60)])],
                                owner=[OWNER_WORDS + " --host"],
                                answers=[("Lift the 5h one-job rule on host for this session?", "No, keep it")]),
                  "allow")
        case("owner: a too-short owner reason does not lift",
             run(with_transcript(jobs([("h-arch", "architect")], currency('owner: "use host"')), one_live),
                 HOT_5H), "deny", ("5h 14% left, 40% to go",))
        case("a capability: reason does not lift the 5h rule",
             run(with_transcript(jobs([("h-arch", "architect")],
                                      currency(CAPABILITY)),
                                 one_live), HOT_5H, band="Hot"), "deny", ("5h 14% left, 40% to go", "wf_live-001"))
        case("a cool 5h window in the Hot weekly band denies nothing",
             run(with_transcript(jobs([("h-arch", "architect")]), none_live), COOL_5H, band="Hot"), "allow")

        def band_run(cmd, *_a, **_k):
            out.append(("the band read asks for the 5h used percent", "--used" in cmd, repr(cmd)[:200]))
            return subprocess.CompletedProcess(
                cmd, 0, "On pace\t1.00\t%.2f\t%d\t\t%.1f\n" % (HOT_5H, int(reset), HOT_5H * 60), "")
        hook, raw = _crash_safe(lambda: run_channel(
            with_transcript(arch, one_live),
            patches=[(wpg, "band_and_pressure", REAL_BAND), (wpg.subprocess, "run", band_run)]))
        out.append(("the real band read carries the 5h window: a hot reading from budget_posture denies",
                    hook.get("permissionDecision") == "deny" and "5h 14% left, 40% to go" in hook.get("permissionDecisionReason", ""),
                    (hook.get("crash") or raw)[:400]))
    finally:
        if saved_env is None:
            os.environ.pop("HARNESS_PROVIDER_GUARD_LOG", None)
        else:
            os.environ["HARNESS_PROVIDER_GUARD_LOG"] = saved_env
    return out


def _records(log):
    try:
        with open(log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []


ENGINE_ARGS = {
    "dispatch": {"jobs": [{"label": "a", "promptPath": "x", "model": "h-fan", "effort": "low",
                           "agentType": "Explore", "tier": "fanout"}]},
    "dispatch_chains": {"chains": [{"name": "c", "jobs": [{"label": "a", "promptPath": "x", "model": "h-fan",
                                                          "effort": "low", "agentType": "Explore", "tier": "fanout"}]}]},
    "review_fanout": {"agents": [{"key": "a", "prompt": "p", "model": "h-fan", "tier": "fanout",
                                  "mustRead": [{"path": "x", "lines": 1}]}],
                      "checker": {"model": "h-fan", "effort": "low"}},
    "idea_fanout": {"contextPrefix": "ctx", "generators": [{"key": "a", "instr": "i", "model": "h-arch", "effort": "low"},
                                                           {"key": "b", "instr": "j", "model": "h-fan", "effort": "low"}],
                    "critic": {"model": "h-fan", "effort": "low"}},
    "worklog_relevance": {"scope": "x", "model": "h-fan", "effort": "low"},
    "test_agents_fixtures": {"fixtures": [], "checklists": {}},
}
TIER_LOGGED = ("dispatch", "dispatch_chains", "review_fanout")


def engine_cases():
    out = []
    for name, args in ENGINE_ARGS.items():
        run = run_engine(name, args)
        err = str((run.get("result") or {}).get("error") or run.get("thrown") or "")
        out.append(("%s without injected vocabulary -> fails closed naming __transport" % name,
                    "__transport" in err and not run.get("calls"), err[:300] or repr(run)[:300]))
    for name in TIER_LOGGED:
        run = run_engine(name, dict(ENGINE_ARGS[name], __transport=TRANSPORT))
        pins = [l for l in run.get("logs") or [] if l.startswith("PINS ")]
        err = (run.get("result") or {}).get("error") or run.get("thrown")
        out.append(("%s with injected vocabulary accepts `tier` and logs it beside the pin" % name,
                    not err and any("tier:fanout" in l for l in pins)
                    and (run.get("calls") or [{}])[0].get("model") == "h-fan",
                    repr(err or pins)[:300]))
    run = run_engine("idea_fanout", dict(ENGINE_ARGS["idea_fanout"], __transport=TRANSPORT))
    models = sorted({c.get("model") for c in run.get("calls") or []})
    out.append(("idea_fanout runs each generator on the caller's own pin",
                models == ["h-arch", "h-fan"], repr(models or run.get("result") or run.get("thrown"))[:300]))
    unpinned = dict(ENGINE_ARGS["idea_fanout"], __transport=TRANSPORT,
                    generators=[{"key": "a", "instr": "i"}, {"key": "b", "instr": "j", "model": "h-fan", "effort": "low"}])
    run = run_engine("idea_fanout", unpinned)
    err = str((run.get("result") or {}).get("error") or "")
    out.append(("idea_fanout refuses an unpinned generator before any agent starts",
                "orchestration §5" in err and not run.get("calls"), err[:300] or repr(run)[:300]))
    return out


def live_case():
    """The real hook on the real registry, band and ladder: wiring only, never a verdict."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import model_registry
    import _session_transport
    host = _session_transport.HOST_TRANSPORT
    rows = [m for m in model_registry.rows_for_tier("fanout", seat=host) if m["transport"] == host]
    pin = rows[0].get("alias") or rows[0]["id"]
    payload = jobs([(pin, "fanout")])
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=hook_env())
    raw = (r.stdout or "").strip()
    hook = (json.loads(raw).get("hookSpecificOutput") if raw else {}) or {}
    got = hook.get("permissionDecision", "advise" if hook.get("additionalContext") else "silent")
    print("live hook: rc=%s decision=%s" % (r.returncode, got))
    return r.returncode == 0 and "Traceback" not in (r.stderr or "")


def test_generated_agent_types_rewrite_in_one_injection():
    from copy import deepcopy
    payload = {"tool_name": "Workflow", "session_id": "s11agent", "tool_input": {
        "scriptPath": ".claude/workflows/dispatch.js", "args": {"jobs": [
            {"label": "fix", "model": "h-fan", "tier": "fanout", "effort": "high",
             "agentType": "lane-review", "prompt": "Run `git diff HEAD`."},
            {"label": "keep", "model": "h-fan", "tier": "fanout", "effort": "high",
             "agentType": "lane-review", "prompt": "Review this text."}]}}}
    original = deepcopy(payload)
    out, raw = run_channel(payload)
    assert out.get("permissionDecision") != "deny", raw
    args = out["updatedInput"]["args"]
    assert [j["agentType"] for j in args["jobs"]] == ["general-purpose", "lane-review"], raw
    assert "__transport" in args and "__coverage" in args, raw
    assert "general-purpose" in out.get("additionalContext", ""), raw
    assert payload == original
    payload["tool_input"]["args"] = json.dumps(payload["tool_input"]["args"])
    out, raw = run_channel(payload)
    assert json.loads(out["updatedInput"]["args"])["jobs"][0]["agentType"] == "general-purpose", raw


def main():
    fails = []
    results = (channel_cases() + implied_tier_cases() + supplementary_seat_cases() + injection_cases()
               + failure_cases()
               + five_hour_cases() + engine_cases())
    try:
        test_generated_agent_types_rewrite_in_one_injection()
        results.append(('S11: uncovered agentType is corrected beside transport injection', True, ''))
    except Exception as exc:
        results.append(('S11: uncovered agentType is corrected beside transport injection', False, repr(exc)))
    for name, ok, detail in results:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % detail))
        if not ok:
            fails.append(name)

    # Planted live-capacity refusal through the real PreToolUse output channel.
    st = wpg._session_transport
    saved = (wpg.provider_capacity_guard.refusal, st.resolve, st._registry, sys.stdin)
    capture = io.StringIO()
    try:
        wpg.provider_capacity_guard.refusal = lambda _t: "Provider capacity exhausted for off"
        checks = getattr(wpg, "_dispatch_checks", None)
        if checks is not None:
            saved_read = checks.read_capacity
            checks.read_capacity = lambda *_a, **_k: None  # a live provider read is not this case's input
        st.resolve = lambda **_k: ("off", "test")
        st._registry = lambda: SYN
        sys.stdin = io.StringIO(json.dumps(jobs([("off-fan-1", "fanout")])))
        with contextlib.redirect_stdout(capture):
            wpg.main()
    finally:
        (wpg.provider_capacity_guard.refusal, st.resolve, st._registry, sys.stdin) = saved
        if checks is not None:
            checks.read_capacity = saved_read
    raw = capture.getvalue().strip()
    cap = (json.loads(raw).get("hookSpecificOutput") if raw else {}) or {}
    ok = cap.get("permissionDecision") == "deny" and "exhausted" in cap.get("permissionDecisionReason", "").lower()
    print(("ok   " if ok else "FAIL ") + "native capacity refusal uses the real deny channel")
    if not ok:
        fails.append("capacity")

    if not live_case():
        fails.append("live hook exit")
    total = len(results) + 2
    print("\n%d/%d passed" % (total - len(fails), total))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
