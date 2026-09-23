"""Re-runnable proof for hooks/workflow_provider_guard.py's deny branch.

instruction_quality §14: registration proves wiring, not matching. `decide()` is pure, so every
band × roster × payload cell is asserted directly; one live end-to-end case feeds the real hook a
PreToolUse payload and asserts on the emitted channel, so the wiring is proven too (its expected
value depends on the live band and is printed, not asserted, unless the band is conserving).

    python3 .claude/tests/test_workflow_provider_guard.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console defaults to cp1252
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env

HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
CHAIN_ENGINE = os.path.join(HERE, "..", "workflows", "dispatch_chains.js")
NODE_ENGINE_RUNNER = r"""
const fs = require('fs')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const src = fs.readFileSync(input.workflow, 'utf8')
const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const calls = []
const agent = async (_prompt, opts) => { calls.push(opts); return {ok: true} }
const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
const fn = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)
fn(input.args, agent, parallel, () => {}, () => {})
  .then(result => process.stdout.write(JSON.stringify({result, calls})))
  .catch(error => { console.error(error.stack || String(error)); process.exit(1) })
"""


def run_chain_engine(args):
    result = subprocess.run(
        ["node", "-e", NODE_ENGINE_RUNNER],
        input=json.dumps({"workflow": CHAIN_ENGINE, "args": args}),
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


spec = importlib.util.spec_from_file_location("wpg", HOOK)
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

SLACK = [{"transport": "codex", "band": "Surplus", "pressure": 0.06, "launchers": [".claude/scripts/codex_proxy_sidecar.sh"], "rank": 0}]
ROLES = {"sonnet": ["gpt-5.6-luna"], "haiku": ["gpt-5.6-luna"]}


def wf(jobs=None, agents=None, extra=None):
    args = {}
    if jobs is not None:
        args["jobs"] = jobs
    if agents is not None:
        args["agents"] = agents
    args.update(extra or {})
    return {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/dispatch.js", "args": args}}


def agent(model=None, prompt="do the thing"):
    ti = {"prompt": prompt, "subagent_type": "general-purpose"}
    if model:
        ti["model"] = model
    return {"tool_name": "Agent", "tool_input": ti}


CASES = [
    # (name, band, slackers, roles, payload, expected verdict)
    ("Surplus is silent", "Surplus", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "sonnet"}]), "silent"),
    ("On pace only advises", "On pace", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "sonnet"}]), "advise"),
    ("Ahead + sonnet job → deny", "Ahead", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "sonnet"}]), "deny"),
    ("Hot + haiku job → deny", "Hot", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "haiku"}]), "deny"),
    # An UNCLAIMED pin still denies: a pin is not its own justification, and the old advise printed
    # "Anthropic is the right currency" without ever asking whether the pin was earned.
    ("Ahead + opus job → deny (unclaimed pin still states its currency)", "Ahead", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "opus"}]), "deny"),
    ("Ahead + opus job + override → advise", "Ahead", SLACK, ROLES,
     wf(jobs=[{"label": "a", "model": "opus"}], extra={"currency": "anthropic",
        "currencyReason": "engine lock: the lens needs MCP tools the sidecar child does not carry"}), "advise"),
    ("On pace + opus job → advise (band gate, not the pin)", "On pace", SLACK, ROLES, wf(jobs=[{"label": "a", "model": "opus"}]), "advise"),
    ("Ahead + mixed panel → deny (one sonnet is enough)", "Ahead", SLACK, ROLES,
     wf(jobs=[{"label": "a", "model": "opus"}, {"label": "b", "model": "sonnet"}]), "deny"),
    ("review_fanout omitted pin defaults to sonnet → deny", "Ahead", SLACK, ROLES, wf(agents=[{"key": "x", "prompt": "p"}]), "deny"),
    ("chains jobs are read too → deny", "Ahead", SLACK, ROLES,
     {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/dispatch_chains.js",
      "args": {"chains": [{"jobs": [{"label": "c", "model": "sonnet"}]}]}}}, "deny"),
    ("args as a JSON string are parsed → deny", "Ahead", SLACK, ROLES,
     {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/dispatch.js",
      "args": json.dumps({"jobs": [{"label": "s", "model": "sonnet"}]})}}, "deny"),
    ("override with a real reason → advise", "Ahead", SLACK, ROLES,
     wf(jobs=[{"label": "a", "model": "sonnet"}], extra={"currency": "anthropic", "currencyReason": "the sidecar cannot run GdUnit4 under the engine lock"}), "advise"),
    ("override with a too-short reason → deny", "Ahead", SLACK, ROLES,
     wf(jobs=[{"label": "a", "model": "sonnet"}], extra={"currency": "anthropic", "currencyReason": "because"}), "deny"),
    ("no slacker transport → advise", "Ahead", [], ROLES, wf(jobs=[{"label": "a", "model": "sonnet"}]), "advise"),
    ("slacker comparison unreadable → advise (fail open, legible)", "Ahead", None, ROLES, wf(jobs=[{"label": "a", "model": "sonnet"}]), "advise"),
    ("roster unreadable → advise", "Ahead", SLACK, None, wf(jobs=[{"label": "a", "model": "sonnet"}]), "advise"),
    ("Agent tool with model=sonnet → deny", "Ahead", SLACK, ROLES, agent("sonnet"), "deny"),
    ("Agent tool inheriting the session model → advise", "Ahead", SLACK, ROLES, agent(None), "advise"),
    ("Agent tool with CURRENCY line → advise", "Ahead", SLACK, ROLES,
     agent("sonnet", "CURRENCY: anthropic — needs the session's MCP tools, which the sidecar child lacks\nTASK: x"), "advise"),
]

REC = {"band": "Ahead", "roles": ["sonnet"], "reason": "engine lock: these jobs run GdUnit4"}
RECORD_CASES = [
    ("record for this band + role -> advise, no restatement needed", "Ahead", REC, wf(jobs=[{"label": "a", "model": "sonnet"}]), "advise"),
    ("record from another band -> deny (new band, new decision)", "Hot", REC, wf(jobs=[{"label": "a", "model": "sonnet"}]), "deny"),
    ("record covers sonnet, dispatch pins haiku -> deny", "Ahead", REC, wf(jobs=[{"label": "a", "model": "haiku"}]), "deny"),
]


def main():
    fails = []
    for name, band, slack, roles, payload, want in CASES:
        got, body, _hits = wpg.decide(band, slack, roles, payload)
        ok = got == want
        print(("ok   " if ok else "FAIL ") + f"{name}: want {want}, got {got}")
        if not ok:
            fails.append(name)
        if got == "deny":
            for must in ("currencyReason", "sidecar_dispatch.md"):
                if must not in body:
                    print(f"FAIL {name}: deny reason lacks {must!r}")
                    fails.append(name + " reason")
    for name, band, rec, payload, want in RECORD_CASES:
        got, body, _hits = wpg.decide(band, SLACK, ROLES, payload, rec)
        ok = got == want
        print(("ok   " if ok else "FAIL ") + f"{name}: want {want}, got {got}")
        if not ok:
            fails.append(name)

    injection_data = {
        "transports": {
            "anthropic": {},
            "codex": {"serverSidePins": {
                "effortValues": ["none", "low", "medium", "high", "xhigh", "max"],
            }},
        },
        "models": [{"transport": "codex", "id": "gpt-test", "alias": "test"}],
    }
    chain_args = {"chains": [{"name": "lane", "jobs": [{
        "label": "handoff", "promptPath": "handoff.md", "model": "gpt-test",
        "effort": "max", "agentType": "Explore",
    }]}]}
    object_payload = {"tool_name": "Workflow", "tool_input": {
        "scriptPath": ".claude/workflows/dispatch_chains.js", "args": chain_args,
    }}
    object_update = wpg.transport_injection(object_payload, "codex", injection_data)
    object_args = object_update.get("args") if object_update else None
    object_run = run_chain_engine(object_args) if isinstance(object_args, dict) else {}
    object_ok = (isinstance(object_args, dict)
                 and object_args.get("__transport", {}).get("efforts")
                 == ["none", "low", "medium", "high", "xhigh", "max"]
                 and not object_run.get("result", {}).get("error")
                 and object_run.get("calls", [{}])[0].get("model") == "gpt-test"
                 and object_run.get("calls", [{}])[0].get("effort") == "max")
    print(("ok   " if object_ok else "FAIL ")
          + "object args survive provider-guard injection and run in the chain engine")
    if not object_ok:
        fails.append("object guard-to-engine handoff")

    string_payload = {"tool_name": "Workflow", "tool_input": {
        "scriptPath": ".claude/workflows/dispatch_chains.js", "args": json.dumps(chain_args),
    }}
    string_update = wpg.transport_injection(string_payload, "codex", injection_data)
    string_args = string_update.get("args") if string_update else None
    parsed_string_args = json.loads(string_args) if isinstance(string_args, str) else {}
    string_run = run_chain_engine(string_args) if isinstance(string_args, str) else {}
    string_ok = (isinstance(string_args, str)
                 and parsed_string_args.get("__transport", {}).get("efforts")
                 == ["none", "low", "medium", "high", "xhigh", "max"]
                 and not string_run.get("result", {}).get("error")
                 and string_run.get("calls", [{}])[0].get("model") == "gpt-test"
                 and string_run.get("calls", [{}])[0].get("effort") == "max")
    print(("ok   " if string_ok else "FAIL ")
          + "JSON-string args survive provider-guard injection and run in the chain engine")
    if not string_ok:
        fails.append("string guard-to-engine handoff")

    no_effort_data = {
        "transports": {"codex": {"serverSidePins": {"effortValues": []}}},
        "models": [{"transport": "codex", "id": "gpt-test", "alias": "test"}],
    }
    no_effort_update = wpg.transport_injection(object_payload, "codex", no_effort_data)
    no_effort_args = no_effort_update.get("args") if no_effort_update else None
    no_effort_run = run_chain_engine(no_effort_args) if isinstance(no_effort_args, dict) else {}
    no_effort_ok = (isinstance(no_effort_args, dict)
                    and bool(no_effort_run.get("result", {}).get("error"))
                    and not no_effort_run.get("calls"))
    print(("ok   " if no_effort_ok else "FAIL ")
          + "empty provider effort registry produces a loud engine failure")
    if not no_effort_ok:
        fails.append("empty effort registry handoff")

    no_model_data = {
        "transports": {"codex": {"serverSidePins": {"effortValues": ["max"]}}},
        "models": [],
    }
    no_model_update = wpg.transport_injection(object_payload, "codex", no_model_data)
    no_model_args = no_model_update.get("args") if no_model_update else None
    no_model_run = run_chain_engine(no_model_args) if isinstance(no_model_args, dict) else {}
    no_model_ok = (isinstance(no_model_args, dict)
                   and bool(no_model_run.get("result", {}).get("error"))
                   and not no_model_run.get("calls"))
    print(("ok   " if no_model_ok else "FAIL ")
          + "empty provider model registry produces a loud engine failure")
    if not no_model_ok:
        fails.append("empty model registry handoff")

    # Planted live-capacity refusal through the real PreToolUse output channel.
    capacity_payload = wf(jobs=[{"label": "capacity", "model": "gpt-5.6-luna",
                                 "promptPath": "x", "effort": "low",
                                 "agentType": "general-purpose"}])
    real_refusal = wpg.provider_capacity_guard.refusal
    real_resolve = wpg._session_transport.resolve
    real_stdin = sys.stdin
    capture = io.StringIO()
    try:
        wpg.provider_capacity_guard.refusal = lambda _transport: "Provider capacity exhausted for codex"
        wpg._session_transport.resolve = lambda **_kwargs: ("codex", "test")
        sys.stdin = io.StringIO(json.dumps(capacity_payload))
        with contextlib.redirect_stdout(capture):
            wpg.main()
    finally:
        wpg.provider_capacity_guard.refusal = real_refusal
        wpg._session_transport.resolve = real_resolve
        sys.stdin = real_stdin
    capacity_doc = json.loads(capture.getvalue())
    capacity_hook = capacity_doc.get("hookSpecificOutput") or {}
    capacity_ok = (capacity_hook.get("permissionDecision") == "deny"
                   and "exhausted" in capacity_hook.get("permissionDecisionReason", "").lower())
    print(("ok   " if capacity_ok else "FAIL ")
          + "native capacity refusal uses the real deny channel")
    if not capacity_ok:
        fails.append("native capacity channel")

    # Live wiring: the real hook, the real band. Deny only when the live band is conserving.
    payload = wf(jobs=[{"label": "live", "model": "sonnet", "promptPath": "x", "effort": "low", "agentType": "general-purpose"}])
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=hook_env())
    out = (r.stdout or "").strip()
    hook = (json.loads(out).get("hookSpecificOutput") if out else {}) or {}
    print(f"live hook: rc={r.returncode} decision={hook.get('permissionDecision', 'advise' if hook.get('additionalContext') else 'silent')}")
    if r.returncode != 0:
        fails.append("live hook exit")
    total = len(CASES) + len(RECORD_CASES) + 5
    print(f"\n{total - len(set(fails))}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
