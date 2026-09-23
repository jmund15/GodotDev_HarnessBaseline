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

# ---- __railsText: the assembled guard text for exactly the (shape, tier) pairs this call uses ----
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import guard_text as _gt  # noqa: E402


def rails_text(name, args, transport="anthropic", key="name"):
    call = {"tool_name": "Workflow", "tool_input": {key: name, "args": args}}
    out = wpg.transport_injection(call, transport, DATA) or {}
    a = out.get("args") or {}
    a = json.loads(a) if isinstance(a, str) else a
    return a.get("__railsText"), a


txt, _ = rails_text("dispatch", {"jobs": [
    {"label": "a", "model": "opus", "shape": "review", "promptPath": "x", "effort": "low", "agentType": "Explore"},
    {"label": "b", "model": "sonnet", "promptPath": "x", "effort": "low", "agentType": "Explore"},
    {"label": "c", "model": "fable", "railTier": "none", "promptPath": "x", "effort": "low", "agentType": "Explore"}]})
check("dispatch: exactly the pairs its jobs use, each the guard_text assembly",
      txt == {"review/condensed": _gt.guard_text("review", "condensed"), "any/detailed": _gt.guard_text("any", "detailed")},
      sorted((txt or {}).keys()))
txt, _ = rails_text("dispatch", {"jobs": [{"label": "a", "model": "sonnet", "railTier": "minimal", "promptPath": "x",
                                           "effort": "low", "agentType": "Explore"}]})
check("dispatch: a job railTier override picks its pair", sorted((txt or {}).keys()) == ["any/minimal"], txt and list(txt))
txt, _ = rails_text("review-fanout", {"agents": [{"key": "k", "model": "opus", "promptPath": "x"}, {"key": "m", "promptPath": "x"}]})
check("review-fanout: review shape; an omitted model keys on the engine default's tier",
      sorted((txt or {}).keys()) == ["review/condensed", "review/detailed"], txt and sorted(txt))
txt, _ = rails_text("explore-fanout", {"lenses": [{"key": "k", "model": "fable", "promptPath": "x"}]})
check("explore-fanout: survey shape", sorted((txt or {}).keys()) == ["survey/minimal"], txt and sorted(txt))
txt, _ = rails_text("dispatch-chains", {"chains": [{"name": "c", "jobs": [{"label": "a", "model": "opus", "shape": "author",
                                                                         "promptPath": "x", "effort": "low"}]}]})
check("dispatch-chains: job shape", sorted((txt or {}).keys()) == ["author/condensed"], txt and sorted(txt))
txt, _ = rails_text("C:/x/.claude/workflows/dispatch_chains.js",
                    {"chains": [{"name": "c", "jobs": [{"label": "a", "model": "opus", "promptPath": "x"}]}]}, key="scriptPath")
check("a scriptPath resume is identified by its basename", sorted((txt or {}).keys()) == ["any/condensed"], txt and sorted(txt))
txt, a = rails_text("harness-review-panel", {"jobs": [{"label": "a", "model": "opus"}]})
check("an unknown workflow gets __rails but no __railsText", txt is None and "__rails" in a, sorted(a))
txt, _ = rails_text("dispatch", {"jobs": [{"label": "a", "model": "gpt-5.6-luna", "promptPath": "x", "effort": "low",
                                           "agentType": "Explore"}]}, transport="codex")
check("a codex-transport call gets __railsText too", sorted((txt or {}).keys()) == ["any/detailed"], txt and sorted(txt))
real = wpg._guard_text
try:
    def boom(_s, _t):
        raise ValueError("no section")
    wpg._guard_text = boom
    txt, a = rails_text("dispatch", {"jobs": [{"label": "a", "model": "opus", "promptPath": "x", "effort": "low",
                                               "agentType": "Explore"}]})
    check("a guard_text failure still emits __rails and no pair entry", "__rails" in a and not (txt or {}), (txt, sorted(a)))
finally:
    wpg._guard_text = real
txt, a = rails_text("dispatch", json.dumps({"jobs": [{"label": "a", "model": "opus", "promptPath": "x", "effort": "low",
                                                       "agentType": "Explore"}]}))
check("a string-args call stays a string and carries __railsText", sorted((txt or {}).keys()) == ["any/condensed"])

total = 10 + 11
print("\n%d/%d passed" % (total - len(fails), total))
sys.exit(1 if fails else 0)
