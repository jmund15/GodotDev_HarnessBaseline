"""Proof that hooks/workflow_provider_guard.py hands every Workflow call the registry's rail tiers.

The engines cannot read files, so `args.__rails` is the only route by which a per-model rail tier
(registry `railTier`) reaches a delegate. The host transport is the load-bearing case: before this
map existed, the Anthropic tiers lived in four hand-copied engine tables that had drifted.

    python3 .claude/tests/test_workflow_provider_guard_rails.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("wpg", os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py"))
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

DATA = {
    "transports": {
        "anthropic": {},
        "codex": {"serverSidePins": {"effortValues": ["low", "max"]}},
    },
    "models": [
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus", "railTier": "condensed"},
        {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable", "railTier": "minimal"},
        {"transport": "anthropic", "id": "claude-sonnet-5", "alias": "sonnet"},
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna"},
    ],
}
JOBS = {"jobs": [{"label": "a", "model": "opus", "promptPath": "x", "effort": "low",
                  "agentType": "general-purpose"}]}

fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok or not detail else "   [%s]" % detail))
    if not ok:
        fails.append(name)


def wf(args):
    return {"tool_name": "Workflow", "tool_input": {"name": "dispatch", "args": args}}


host = wpg.transport_injection(wf(dict(JOBS)), "anthropic", DATA)
host_args = (host or {}).get("args") or {}
check("host Workflow call gets __rails keyed by id and alias",
      host_args.get("__rails") == {"claude-opus-5": "condensed", "opus": "condensed",
                                   "claude-fable-5-1": "minimal", "fable": "minimal",
                                   "claude-sonnet-5": "detailed", "sonnet": "detailed"},
      repr(host_args.get("__rails")))
check("host Workflow call gets no __transport (host vocabulary stays the engines' own)",
      "__transport" not in host_args)

codex = wpg.transport_injection(wf(dict(JOBS)), "codex", DATA)
codex_args = (codex or {}).get("args") or {}
check("codex call gets __rails with absent railTier read as detailed",
      codex_args.get("__rails") == {"gpt-5.6-luna": "detailed", "luna": "detailed"}, repr(codex_args.get("__rails")))
check("codex call still gets __transport", bool(codex_args.get("__transport")))

string_update = wpg.transport_injection(wf(json.dumps(JOBS)), "anthropic", DATA)
string_args = (string_update or {}).get("args")
check("JSON-string args stay a string and carry __rails",
      isinstance(string_args, str) and json.loads(string_args).get("__rails", {}).get("opus") == "condensed")

check("an Agent call is not rewritten",
      wpg.transport_injection({"tool_name": "Agent", "tool_input": {"prompt": "x"}}, "anthropic", DATA) is None)
check("an unknown transport is not rewritten",
      wpg.transport_injection(wf(dict(JOBS)), wpg._session_transport.UNKNOWN, DATA) is None)

# Through the real output channel on the host transport, in a non-conserving band.
real = (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
        wpg._session_transport._registry)
capture = io.StringIO()
real_stdin = sys.stdin
try:
    wpg._session_transport.resolve = lambda **_k: ("anthropic", "test")
    wpg.band_and_pressure = lambda: ("Surplus", None)
    wpg.provider_capacity_guard.refusal = lambda _t: None
    wpg._session_transport._registry = lambda: DATA
    sys.stdin = io.StringIO(json.dumps(wf(dict(JOBS))))
    with contextlib.redirect_stdout(capture):
        wpg.main()
finally:
    (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
     wpg._session_transport._registry) = real
    sys.stdin = real_stdin
out = capture.getvalue().strip()
doc = json.loads(out) if out else {}
updated = (doc.get("hookSpecificOutput") or {}).get("updatedInput") or {}
check("the host PreToolUse output carries updatedInput with __rails",
      ((updated.get("args") or {}).get("__rails") or {}).get("opus") == "condensed", out[:300])

verdict, reason = wpg.decide_vocabulary("codex", "test", wf(dict(JOBS)), DATA)
check("a denied Anthropic pin on a provider seat points at for-role",
      verdict == "deny" and "for-role <tier>" in (reason or ""), (reason or "")[-200:])

print("\n%d/%d passed" % (10 - len(fails), 10))
sys.exit(1 if fails else 0)
