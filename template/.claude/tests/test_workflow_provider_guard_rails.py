"""Proof that hooks/workflow_provider_guard.py hands every Workflow call the registry's rail tiers.

The engines cannot read files, so `args.__rails` is the only route by which a per-model rail tier
(registry `railTier`) reaches a delegate. The host transport is the load-bearing case: before this
map existed, the host tiers lived in four hand-copied engine tables that had drifted. The roster is
neutral (`host`, `off`; rows `h-*`, `o-*`), so no case depends on a real model name.

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
sys.path.insert(0, HERE)
from _transport_fixture import planted_mcp_workdir  # noqa: E402

os.environ["SC_WORKDIR"] = planted_mcp_workdir()
spec = importlib.util.spec_from_file_location("wpg", os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py"))
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

DATA = {
    "transports": {
        "host": {},
        "off": {"serverSidePins": {"effortValues": ["low", "max"]}},
    },
    "models": [
        {"transport": "host", "id": "host-arch-1", "alias": "h-arch", "railTier": "condensed",
         "roles": ["architect", "executor"]},
        {"transport": "host", "id": "host-orch-1", "alias": "h-orch", "railTier": "minimal",
         "roles": ["orchestrator"]},
        {"transport": "host", "id": "host-fan-1", "alias": "h-fan", "roles": ["fanout", "scout"]},
        {"transport": "off", "id": "off-fan-1", "alias": "o-fan", "roles": ["fanout"]},
    ],
}
TIERS = {"orchestrator": ["h-orch"], "architect": ["h-arch"], "executor": ["h-arch"],
         "fanout": ["h-fan"], "scout": ["h-fan"]}
JOBS = {"jobs": [{"label": "a", "model": "h-arch", "promptPath": "x", "effort": "low",
                  "agentType": "general-purpose", "tier": "architect"}]}
wpg._session_transport.HOST_TRANSPORT = "host"
wpg.model_registry.role_tiers = lambda *_a, **_k: TIERS

fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok or not detail else "   [%s]" % detail))
    if not ok:
        fails.append(name)


def wf(args):
    return {"tool_name": "Workflow", "tool_input": {"name": "dispatch", "args": args}}


host = wpg.transport_injection(wf(dict(JOBS)), "host", DATA)
host_args = (host or {}).get("args") or {}
check("host Workflow call gets __rails keyed by id and alias",
      host_args.get("__rails") == {"host-arch-1": "condensed", "h-arch": "condensed",
                                   "host-orch-1": "minimal", "h-orch": "minimal",
                                   "host-fan-1": "detailed", "h-fan": "detailed"},
      repr(host_args.get("__rails")))
check("host Workflow call gets __transport too, with no default pin: the engines hold no model vocabulary of their own",
      bool((host_args.get("__transport") or {}).get("ids")) and "default" not in host_args.get("__transport"),
      repr(host_args.get("__transport")))

off = wpg.transport_injection(wf(dict(JOBS)), "off", DATA)
off_args = (off or {}).get("args") or {}
check("provider call gets __rails with absent railTier read as detailed",
      off_args.get("__rails") == {"off-fan-1": "detailed", "o-fan": "detailed"}, repr(off_args.get("__rails")))
check("provider call still gets __transport", bool(off_args.get("__transport")))

string_update = wpg.transport_injection(wf(json.dumps(JOBS)), "host", DATA)
string_args = (string_update or {}).get("args")
check("JSON-string args stay a string and carry __rails",
      isinstance(string_args, str) and json.loads(string_args).get("__rails", {}).get("h-arch") == "condensed")

check("an Agent call is not rewritten",
      wpg.transport_injection({"tool_name": "Agent", "tool_input": {"prompt": "x"}}, "host", DATA) is None)
check("an unknown transport is not rewritten",
      wpg.transport_injection(wf(dict(JOBS)), wpg._session_transport.UNKNOWN, DATA) is None)
# Two hooks' updatedInput never merge (one replaces the other), so a session whose Workflow args another hook
# rewrites (the score daemon's drift guard) must get no second writer.
os.environ["HARNESS_WORKFLOW_ARGS_OWNER"] = "drift_guard"
try:
    check("a session whose Workflow args another hook owns gets no rewrite from this guard",
          wpg.transport_injection(wf(dict(JOBS)), "host", DATA) is None)
finally:
    os.environ.pop("HARNESS_WORKFLOW_ARGS_OWNER", None)

# Through the real output channel on the host transport, in the Surplus band.
real = (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
        wpg._session_transport._registry, wpg.model_registry.load)
capture = io.StringIO()
real_stdin = sys.stdin
try:
    wpg._session_transport.resolve = lambda **_k: ("host", "test")
    wpg.band_and_pressure = lambda *_a, **_k: ("Surplus", None)
    wpg.provider_capacity_guard.refusal = lambda _t: None
    wpg._session_transport._registry = lambda: DATA
    wpg.model_registry.load = lambda *_a, **_k: DATA
    sys.stdin = io.StringIO(json.dumps(wf(dict(JOBS))))
    with contextlib.redirect_stdout(capture):
        wpg.main()
finally:
    (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
     wpg._session_transport._registry, wpg.model_registry.load) = real
    sys.stdin = real_stdin
out = capture.getvalue().strip()
doc = json.loads(out) if out else {}
updated = (doc.get("hookSpecificOutput") or {}).get("updatedInput") or {}
check("the host PreToolUse output carries updatedInput with __rails",
      ((updated.get("args") or {}).get("__rails") or {}).get("h-arch") == "condensed", out[:300])
check("...and __railsText with the assembled rail text",
      bool((updated.get("args") or {}).get("__railsText")) and all(isinstance(v, str) and v for v in ((updated.get("args") or {}).get("__railsText") or {}).values()), out[:300])

def guard_output(payload):
    """The PreToolUse document `main()` emits for `payload` on the host transport in the Surplus band."""
    real = (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
            wpg._session_transport._registry, wpg.model_registry.load)
    capture, real_stdin = io.StringIO(), sys.stdin
    try:
        wpg._session_transport.resolve = lambda **_k: ("host", "test")
        wpg.band_and_pressure = lambda *_a, **_k: ("Surplus", None)
        wpg.provider_capacity_guard.refusal = lambda _t: None
        wpg._session_transport._registry = lambda: DATA
        wpg.model_registry.load = lambda *_a, **_k: DATA
        sys.stdin = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(capture):
            wpg.main()
    finally:
        (wpg._session_transport.resolve, wpg.band_and_pressure, wpg.provider_capacity_guard.refusal,
         wpg._session_transport._registry, wpg.model_registry.load) = real
        sys.stdin = real_stdin
    text = capture.getvalue().strip()
    return (json.loads(text) if text else {}).get("hookSpecificOutput") or {}


def review_call(fold):
    args = {"agents": [{"key": "k", "model": "h-arch", "promptPath": "x"}], "fold": fold}
    return {"tool_name": "Workflow", "tool_input": {"scriptPath": ".claude/workflows/review_fanout.js", "args": args}}


for fold in ({"planPath": "p.md", "templatePath": "t.md", "tier": "architect"},
             {"planPaths": ["p.md", "q.md"], "templatePath": "t.md", "tier": "architect"}):
    out = guard_output(review_call(fold))
    check("the guard leaves retired args.fold." + next(iter(fold)) + " to the engine's own refusal "
          "(review_fanout_spill_test.js proves it names subjects)",
          "args.fold.subjects" not in (out.get("permissionDecisionReason") or ""), json.dumps(out)[:300])

verdict, reason = wpg.decide_vocabulary("off", "test", wf(dict(JOBS)), DATA)
check("a denied host alias on a provider seat points at for-role",
      verdict == "deny" and "for-role <tier>" in (reason or ""), (reason or "")[-200:])

# ---- __railsText: the assembled guard text for exactly the (shape, tier) pairs this call uses ----
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import guard_text as _gt  # noqa: E402


def rails_text(name, args, transport="host", key="name"):
    call = {"tool_name": "Workflow", "tool_input": {key: name, "args": args}}
    out = wpg.transport_injection(call, transport, DATA) or {}
    a = out.get("args") or {}
    a = json.loads(a) if isinstance(a, str) else a
    return a.get("__railsText"), a


txt, _ = rails_text("dispatch", {"jobs": [
    {"label": "a", "model": "h-arch", "shape": "review", "promptPath": "x", "effort": "low", "agentType": "Explore"},
    {"label": "b", "model": "h-fan", "promptPath": "x", "effort": "low", "agentType": "Explore"},
    {"label": "c", "model": "h-orch", "railTier": "none", "promptPath": "x", "effort": "low", "agentType": "Explore"}]})
check("dispatch: exactly the pairs its jobs use, each the guard_text assembly",
      txt == {"review/condensed": _gt.guard_text("review", "condensed"), "any/detailed": _gt.guard_text("any", "detailed")},
      sorted((txt or {}).keys()))
txt, _ = rails_text("dispatch", {"jobs": [{"label": "a", "model": "h-fan", "railTier": "minimal", "promptPath": "x",
                                           "effort": "low", "agentType": "Explore"}]})
check("dispatch: a job railTier override picks its pair", sorted((txt or {}).keys()) == ["any/minimal"], txt and list(txt))
txt, _ = rails_text("review-fanout", {"agents": [{"key": "k", "model": "h-arch", "promptPath": "x"}, {"key": "m", "promptPath": "x"}]})
check("review-fanout: review shape; an omitted model keys on the engine default's tier",
      sorted((txt or {}).keys()) == ["review/condensed", "review/detailed"], txt and sorted(txt))
txt, _ = rails_text("review-fanout", {"agents": [{"key": "k", "model": "h-arch", "promptPath": "x"}],
                                     "fold": {"subjects": [{"root": ".", "snapshot": "s.json", "files": ["p.md"]}],
                                              "templatePath": "t.md", "tier": "architect"}})
check("review-fanout with args.fold and no fold model: no tier pin stands in, so no author rails",
      sorted((txt or {}).keys()) == ["review/condensed"], txt and sorted(txt))
txt, _ = rails_text("review-fanout", {"agents": [{"key": "k", "model": "h-arch", "promptPath": "x"}],
                                     "fold": {"subjects": [{"root": ".", "snapshot": "s.json", "files": ["p.md"]}],
                                              "templatePath": "t.md", "model": "h-orch"}})
check("review-fanout with args.fold: an explicit fold model keys on its own tier",
      sorted((txt or {}).keys()) == ["author/minimal", "review/condensed"], txt and sorted(txt))
txt, _ = rails_text("explore-fanout", {"lenses": [{"key": "k", "model": "h-orch", "promptPath": "x"}]})
check("explore-fanout: survey shape", sorted((txt or {}).keys()) == ["survey/minimal"], txt and sorted(txt))
txt, _ = rails_text("dispatch-chains", {"chains": [{"name": "c", "jobs": [{"label": "a", "model": "h-arch", "shape": "author",
                                                                         "promptPath": "x", "effort": "low"}]}]})
check("dispatch-chains: job shape", sorted((txt or {}).keys()) == ["author/condensed"], txt and sorted(txt))
txt, _ = rails_text("C:/x/.claude/workflows/dispatch_chains.js",
                    {"chains": [{"name": "c", "jobs": [{"label": "a", "model": "h-arch", "promptPath": "x"}]}]}, key="scriptPath")
check("a scriptPath resume is identified by its basename", sorted((txt or {}).keys()) == ["any/condensed"], txt and sorted(txt))
txt, a = rails_text("harness-review-panel", {"jobs": [{"label": "a", "model": "h-arch"}]})
check("an unknown workflow gets __rails but no __railsText", txt is None and "__rails" in a, sorted(a))
txt, _ = rails_text("dispatch", {"jobs": [{"label": "a", "model": "off-fan-1", "promptPath": "x", "effort": "low",
                                           "agentType": "Explore"}]}, transport="off")
check("a provider-transport call gets __railsText too", sorted((txt or {}).keys()) == ["any/detailed"], txt and sorted(txt))
real = wpg._guard_text
try:
    def boom(_s, _t):
        raise ValueError("no section")
    wpg._guard_text = boom
    txt, a = rails_text("dispatch", {"jobs": [{"label": "a", "model": "h-arch", "promptPath": "x", "effort": "low",
                                               "agentType": "Explore"}]})
    check("a guard_text failure still emits __rails and no pair entry", "__rails" in a and not (txt or {}), (txt, sorted(a)))
finally:
    wpg._guard_text = real
txt, a = rails_text("dispatch", json.dumps({"jobs": [{"label": "a", "model": "h-arch", "promptPath": "x", "effort": "low",
                                                       "agentType": "Explore"}]}))
check("a string-args call stays a string and carries __railsText", sorted((txt or {}).keys()) == ["any/condensed"])

# ---- __driverNotes: the registry's per-model corrections, keyed by the model string a job pins ----
NOTE = {"text": "Keep going until the brief is done.", "evidenceRef": "ref-1", "weakness": "stops early",
        "removeWhen": "a pin_ab run shows no gap"}
DATA["models"][0]["driverNotes"] = [NOTE]


def notes_of(name, args, key="name"):
    _txt, a = rails_text(name, args, key=key)
    return a.get("__driverNotes"), a


block = "[session] Driver notes (h-arch):\n- Keep going until the brief is done. (ref-1)"
got, _ = notes_of("dispatch", {"jobs": [
    {"label": "a", "model": "h-arch", "promptPath": "x", "effort": "low", "agentType": "Explore"},
    {"label": "b", "model": "h-fan", "promptPath": "x", "effort": "low", "agentType": "Explore"}]})
check("dispatch: a job whose model row has driverNotes gets the sidecar-format block, keyed by its pin; a model without notes gets no key",
      got == {"h-arch": block}, got)
got, a = notes_of("dispatch", {"jobs": [{"label": "b", "model": "h-fan", "promptPath": "x", "effort": "low",
                                         "agentType": "Explore"}]})
check("dispatch: no model with notes means no __driverNotes key", "__driverNotes" not in a, got)
got, _ = notes_of("explore-fanout", {"lenses": [{"key": "k", "model": "h-arch", "promptPath": "x"}]})
check("explore-fanout: lens models resolve notes", got == {"h-arch": block}, got)
got, _ = notes_of("dispatch-chains", {"chains": [{"name": "c", "jobs": [{"label": "a", "model": "h-arch", "promptPath": "x",
                                                                      "effort": "low"}]}]})
check("dispatch-chains: chain job models resolve notes", got == {"h-arch": block}, got)
got, _ = notes_of("review-fanout", {"agents": [{"key": "k", "model": "h-fan", "promptPath": "x"}],
                                   "fold": {"subjects": [{"root": ".", "snapshot": "s.json", "files": ["p.md"]}],
                                            "templatePath": "t.md", "model": "h-arch"}})
check("review-fanout: the fold model resolves notes", got == {"h-arch": block}, got)
got, _ = notes_of("dispatch", {"jobs": [{"label": "a", "model": "h-arch", "promptPath": "x", "effort": "low",
                                         "agentType": "Explore"}]}, key="name")
check("the block is guard_text's own rendering with the same data (one format for every delivery)",
      got == {"h-arch": _gt.driver_notes_block("h-arch", DATA)}, got)
got, _ = notes_of("harness-review-panel", {"jobs": [{"label": "a", "model": "h-arch"}]})
check("an unknown workflow gets no __driverNotes", got is None, got)

total = 10 + 13 + 7
print("\n%d/%d passed" % (total - len(fails), total))
sys.exit(1 if fails else 0)
