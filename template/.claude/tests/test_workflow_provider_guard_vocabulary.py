#!/usr/bin/env python3
"""Proof for RULE 1 of workflow_provider_guard.py — pin vocabulary per transport.

Sibling of test_workflow_provider_guard.py (tier and currency) and _roles.py. Split because the
rules are independent: a vocabulary deny fires at every band and needs no tier.

The legal set is read from the planted registry alone: the host transport accepts its rows' ids and
aliases (its runtime resolves its own aliases), a proxied transport its canonical ids only. The
roster is neutral (`host`, `off`, `far`; rows `h-*`, `o-*`, `f-*`), so no case depends on a real
model name. The negatives carry the weight: every transport gets a legal pin that MUST pass beside
the illegal one that must deny, and the shapes that must never fire at all (a `model:` key inside a
comment).

`PIN('x')` is a literal pin: PIN() is `(m) => m` in every engine.

Run: python3 .claude/tests/test_workflow_provider_guard_vocabulary.py
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
spec = importlib.util.spec_from_file_location("wpg", HOOK)
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)
import _session_transport as _st  # noqa: E402  the module the guard reads, patched per run

DATA = {
    "transports": {
        "host": {},
        "off": {"launcher": ".claude/scripts/off_sidecar.sh"},
        "far": {"baseUrl": "https://far.example.test/v1"},
    },
    "models": [
        {"transport": "off", "id": "off-alt-1", "alias": "o-alt"},
        {"transport": "off", "id": "off-long-1", "alias": "o-long", "contextSuffix": "[1m]"},
        {"transport": "off", "id": "off-fan-1", "alias": "o-fan",
         "limits": {"contextTokens": 272000, "maxContextTokens": 872000}},
        {"transport": "far", "id": "far-pro-1", "alias": "f-pro"},
        {"transport": "host", "id": "host-arch-1", "alias": "h-arch"},
        {"transport": "host", "id": "host-fan-1", "alias": "h-fan"},
        {"transport": "host", "id": "host-long-1", "alias": "h-long", "contextSuffix": "[1m]", "roles": ["fanout"]},
        {"transport": "host", "id": "host-orch-1", "alias": "h-orch",
         "status": {"state": "unavailable", "scope": "all", "reason": "test exclusion"}},
    ],
}


def _oversize_script():
    """A real script file just past the hook's scan cap, so the scan is genuinely truncated."""
    fd, p = tempfile.mkstemp(suffix=".js", prefix="oversize_")
    os.close(fd)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("// filler\n" * 60000)           # ~600KB, past SCRIPT_READ_CAP
        fh.write("agent(x, {model: 'h-arch'})\n")  # a pin the truncated read cannot see
    return p


def wf(jobs=None, agents=None, script=None, script_path=None):
    ti = {"scriptPath": script_path or ".claude/workflows/dispatch.js"}
    args = {}
    if jobs is not None:
        args["jobs"] = jobs
    if agents is not None:
        args["agents"] = agents
    ti["args"] = args
    if script is not None:
        ti["script"] = script
        ti.pop("scriptPath", None)
    return {"tool_name": "Workflow", "tool_input": ti}


def agent(model):
    return {"tool_name": "Agent", "tool_input": {"prompt": "go", "model": model}}


CASES = [
    # (name, transport, payload, expect_deny)
    # A job with no model is malformed input, not a pin: nothing to deny, and no message naming no model.
    ("a jobs entry with NO model is not a pin, so nothing is denied", "off", wf(jobs=[{"label": "a"}]), False),
    # A scriptPath the hook can FIND but not fully read leaves a blind spot, so truncation denies,
    # while an unreadable path (below) stays silent — the hook may simply have guessed the root wrong.
    ("a scriptPath too large to scan whole is DENIED, not passed", "off",
     wf(jobs=[{"label": "a", "model": "off-fan-1"}], script_path=_oversize_script()), True),
    ("...but a real bad pin beside it still denies", "off",
     wf(jobs=[{"label": "a"}, {"label": "b", "model": "h-arch"}]), True),

    ("host + its own alias → pass", "host", wf(jobs=[{"label": "a", "model": "h-fan"}]), False),
    ("host + its own canonical id → pass", "host", wf(jobs=[{"label": "a", "model": "host-arch-1"}]), False),
    ("host + an unregistered id in the house style → DENY", "host",
     wf(jobs=[{"label": "a", "model": "host-arch-2"}]), True),
    ("host + a name no registry row carries → DENY (no literal list admits it)", "host",
     wf(jobs=[{"label": "a", "model": "retired-name"}]), True),
    ("host + an EXCLUDED host alias → DENY", "host", wf(jobs=[{"label": "a", "model": "h-orch"}]), True),
    ("host + another transport's id → DENY", "host", wf(jobs=[{"label": "a", "model": "off-alt-1"}]), True),

    ("off + its own id → pass", "off", wf(jobs=[{"label": "a", "model": "off-alt-1"}]), False),
    ("off + long-context suffix → DENY; native endpoint accepts only canonical registry ids", "off",
     wf(jobs=[{"label": "a", "model": "off-fan-1[1m]"}]), True),
    ("off + registry alias → DENY; native agent API needs the canonical id", "off",
     wf(jobs=[{"label": "a", "model": "o-alt"}]), True),
    ("off + long-context alias → DENY; modifier does not canonicalize the alias", "off",
     wf(jobs=[{"label": "a", "model": "o-fan[1m]"}]), True),
    ("off + a host alias → DENY", "off", wf(jobs=[{"label": "a", "model": "h-fan"}]), True),
    ("off + another transport's id → DENY", "off", wf(jobs=[{"label": "a", "model": "far-pro-1"}]), True),

    ("far + its own id → pass", "far", wf(jobs=[{"label": "a", "model": "far-pro-1"}]), False),
    ("far + a host alias → DENY", "far", wf(jobs=[{"label": "a", "model": "h-arch"}]), True),

    ("unknown transport denies every pin", "unknown", wf(jobs=[{"label": "a", "model": "h-fan"}]), True),
    ("unknown transport denies a provider id too", "unknown", wf(jobs=[{"label": "a", "model": "off-alt-1"}]), True),

    ("Agent tool obeys the same rule", "off", agent("h-fan"), True),
    ("Agent tool legal pin passes", "off", agent("off-fan-1"), False),


    # A `[1m]` context suffix is legal only on a host row that declares `contextSuffix`.
    ("host: the declaring row's alias with [1m] → pass", "host", wf(jobs=[{"label": "a", "model": "h-long[1m]"}]), False),
    ("host: the declaring row's id with [1m] → pass", "host", wf(jobs=[{"label": "a", "model": "host-long-1[1m]"}]), False),
    ("host: the declaring row's bare alias → pass (the guard appends the suffix)", "host",
     wf(jobs=[{"label": "a", "model": "h-long"}]), False),
    ("host: a row without contextSuffix + [1m] → DENY", "host", wf(jobs=[{"label": "a", "model": "h-fan[1m]"}]), True),
    ("host: a full id without contextSuffix + [1m] → DENY", "host",
     wf(jobs=[{"label": "a", "model": "host-fan-1[1m]"}]), True),
    ("host: an excluded alias stays illegal with [1m] → DENY", "host",
     wf(jobs=[{"label": "a", "model": "h-orch[1m]"}]), True),
    ("host: an unknown suffix on the declaring row → DENY", "host", wf(jobs=[{"label": "a", "model": "h-long[2m]"}]), True),
    ("host: [1m] mid-string is not a suffix → DENY", "host", wf(jobs=[{"label": "a", "model": "h-[1m]long"}]), True),
    ("host: the suffix twice → DENY", "host", wf(jobs=[{"label": "a", "model": "h-long[1m][1m]"}]), True),
    ("host: another transport's declaring row + [1m] → DENY", "host",
     wf(jobs=[{"label": "a", "model": "off-long-1[1m]"}]), True),
    ("off transport: [1m] is not a native modifier there → DENY", "off",
     wf(jobs=[{"label": "a", "model": "off-fan-1[1m]"}]), True),
    ("off transport: even a row that declares contextSuffix → DENY", "off",
     wf(jobs=[{"label": "a", "model": "off-long-1[1m]"}]), True),

    # --- must not fire at all ---
    ("no pins on the call → silent", "off", wf(jobs=[]), False),
    ("inline script: legal id → pass", "off", wf(script="agent(p, {model: 'off-alt-1', effort: 'low'})"), False),
    ("inline script: a host alias → DENY", "off", wf(script="agent(p, {model: 'h-fan', effort: 'low'})"), True),
    ("inline script: backtick quotes are read", "off", wf(script="agent(p, {model: `h-fan`})"), True),
    ("inline script: PIN('h-fan') is a literal pin, not a resolver call", "off",
     wf(script="agent(p, {model: PIN('h-fan')})"), True),
    ("inline script: PIN() with a legal id still passes", "off", wf(script="agent(p, {model: PIN('off-alt-1')})"), False),
    ("inline script: any IDENT() wrapper on an illegal name also denies", "off",
     wf(script="agent(p, {model: SOMEFN('h-arch')})"), True),
    ("inline script: a line-comment pin does NOT fire", "off",
     wf(script="// the model: 'h-fan' convention is described in the docs"), False),
    ("inline script: a block-comment pin does NOT fire", "off",
     wf(script="/* pins look like model: 'h-arch' */\nagent(p, {model: 'off-alt-1'})"), False),
    ("inline script: a comment does not mask a real pin below it", "off",
     wf(script="// model: 'o-alt' is legal here\nagent(p, {model: 'h-fan'})"), True),
    ("inline script: a URL is not read as a comment", "off",
     wf(script="// see https://x.test/a\nagent(p, {model: 'h-fan'})"), True),
]


def main():
    failed = 0
    extra = 0
    real = (_st.HOST_TRANSPORT, _st.resolve, _st._registry)
    _st.HOST_TRANSPORT = "host"
    try:
        for name, transport, payload, want_deny in CASES:
            _st.resolve = lambda data=None, env=None, _t=transport: (_t, "test")
            verdict, reason = wpg.decide_vocabulary(transport, "test", payload, DATA)
            got_deny = verdict == "deny"
            ok = got_deny == want_deny
            failed += not ok
            print("%s %s: want %s, got %s%s"
                  % ("ok  " if ok else "FAIL", name, "deny" if want_deny else "pass",
                     "deny" if got_deny else "pass", "" if ok or not got_deny else " | " + (reason or "")[:90]))

        suffixed = wf(jobs=[{"label": "a", "model": "h-long[1m]"}])
        ok = ([p[1] for p in wpg.call_pins(suffixed)] == ["h-long"]
              and [p[1] for p in wpg.call_pins(suffixed, raw=True)] == ["h-long[1m]"])
        failed += not ok
        print("%s later rules read the pin without its context suffix; RULE 1 reads it raw" % ("ok  " if ok else "FAIL"))

        injected = wpg.transport_injection(wf(jobs=[{"label": "a", "model": "off-fan-1"}]), "off", DATA)
        t = ((injected or {}).get("args") or {}).get("__transport", {})
        ok = t.get("ids") == ["off-alt-1", "off-fan-1", "off-long-1"] and "default" not in t
        failed += not ok
        print("%s provider injection exposes canonical ids and no omitted-pin default: %r"
              % ("ok  " if ok else "FAIL", t))

        injected = wpg.transport_injection(wf(jobs=[{"label": "a", "model": "h-fan"}]), "host", DATA)
        t = ((injected or {}).get("args") or {}).get("__transport", {})
        ok = t.get("ids") == ["h-arch", "h-fan", "h-long[1m]", "host-arch-1", "host-fan-1", "host-long-1[1m]"]
        failed += not ok
        print("%s host injection exposes ids and aliases (a contextSuffix row suffixed), the excluded row absent: %r"
              % ("ok  " if ok else "FAIL", t.get("ids")))

        # A row that declares contextSuffix is dispatched with it: the guard rewrites every pin naming
        # the row (id or alias) so callers keep the bare name and the engines need no edit.
        def rewritten(args, transport="host", data=DATA):
            out = wpg.transport_injection({"tool_name": "Workflow", "tool_input": {
                "scriptPath": ".claude/workflows/dispatch.js", "args": args}}, transport, data)
            return (out or {}).get("args") or {}

        def check(label, ok, detail=""):
            nonlocal failed, extra
            extra += 1
            failed += not ok
            print("%s %s%s" % ("ok  " if ok else "FAIL", label, "" if ok else " | %r" % (detail,)))

        v = rewritten({'jobs': [{'label': 'a', 'model': 'off-fan-1'}]}, 'off')['__transport']
        check('__transport carries no default, tier pin or fanout rung: the caller pins every agent',
              not {'default', 'tierPins', 'fanoutEfforts'} & set(v), v)
        checked = wf(jobs=[{'label': 'a', 'model': 'h-fan'}])
        checked['tool_input']['args']['checker'] = {'model': 'h-fan', 'effort': 'low'}
        pins = wpg.call_pins(checked)
        check('args.checker is an implied fanout-tier pin the guard reads',
              ('checker', 'h-fan', 'fanout', True) in pins, pins)

        a = rewritten({"jobs": [{"label": "a", "model": "h-long"}, {"label": "b", "model": "host-long-1"},
                                {"label": "c", "model": "h-fan"}, {"label": "d", "model": "h-long[1m]"}]})
        got = [j["model"] for j in a["jobs"]]
        check("suffix: args.jobs pins naming the row (alias, id) gain it; another row and an already-suffixed pin do not",
              got == ["h-long[1m]", "host-long-1[1m]", "h-fan", "h-long[1m]"], got)
        a = rewritten({"chains": [{"jobs": [{"label": "a", "model": "h-long"}]}, {"jobs": [{"label": "b", "model": "h-fan"}]}]})
        got = [j["model"] for c in a["chains"] for j in c["jobs"]]
        check("suffix: args.chains[].jobs pins are rewritten", got == ["h-long[1m]", "h-fan"], got)
        a = rewritten({"agents": [{"key": "x", "model": "h-long"}, {"key": "y"}]})
        got = [g.get("model") for g in a["agents"]]
        check("suffix: args.agents pins are rewritten; an agent with no model stays without one", got == ["h-long[1m]", None], got)
        a = rewritten({"agents": [{"key": "x", "model": "h-fan"}], "fold": {"model": "host-long-1", "tier": "fanout"}})
        check("suffix: args.fold.model is rewritten", a["fold"]["model"] == "host-long-1[1m]", a.get("fold"))
        a = rewritten({"lenses": [{"key": "l", "model": "h-long"}]})
        check("suffix: args.lenses pins are rewritten", a["lenses"][0]["model"] == "h-long[1m]", a.get("lenses"))
        a = rewritten({"model": "h-long", "jobs": [{"label": "a", "model": "h-fan"}]})
        check("suffix: a root args.model is rewritten", a["model"] == "h-long[1m]" and a["jobs"][0]["model"] == "h-fan", a)
        a = rewritten({"jobs": [{"label": "a", "model": "h-fan"}]})
        check("suffix: a call naming no declaring row is left byte-for-byte", a["jobs"] == [{"label": "a", "model": "h-fan"}], a["jobs"])
        v = a["__transport"]
        check("suffix: __transport.ids carries the suffixed forms and drops the bare ones",
              "h-long[1m]" in v["ids"] and "host-long-1[1m]" in v["ids"]
              and "h-long" not in v["ids"] and "host-long-1" not in v["ids"] and "h-fan" in v["ids"], v["ids"])
        check("suffix: __rails keys are suffixed for id and alias, an unsuffixed row keeps its keys",
              {"h-long[1m]", "host-long-1[1m]", "h-fan", "host-fan-1"} <= set(a["__rails"])
              and "h-long" not in a["__rails"] and "host-long-1" not in a["__rails"], sorted(a["__rails"]))
        a = rewritten({"jobs": [{"label": "a", "model": "off-long-1"}]}, transport="off")
        check("suffix: another transport's declaring row is left bare in the pins and in ids",
              a["jobs"][0]["model"] == "off-long-1" and "off-long-1" in a["__transport"]["ids"]
              and "off-long-1[1m]" not in a["__transport"]["ids"], a["__transport"]["ids"])
        raw_args = json.dumps({"jobs": [{"label": "a", "model": "h-long"}]})
        out = wpg.transport_injection({"tool_name": "Workflow", "tool_input": {"args": raw_args}}, "host", DATA)
        check("suffix: string args stay a string with the rewrite inside",
              isinstance(out["args"], str) and json.loads(out["args"])["jobs"][0]["model"] == "h-long[1m]", out.get("args"))
        pl = wf(jobs=[{"label": "a", "model": "h-long"}])
        wpg.transport_injection(pl, "host", DATA)
        check("suffix: the caller's payload is not mutated", pl["tool_input"]["args"]["jobs"][0]["model"] == "h-long")
        check("suffix: later rules read the bare id of a suffixed pin",
              [p[1] for p in wpg.call_pins(wf(jobs=[{"label": "a", "model": "h-long[1m]"}]))] == ["h-long"])

        # The shipped registry: the claude-sonnet-5-5 row declares the suffix, the other rows do not.
        _st.HOST_TRANSPORT = "anthropic"
        live = wpg.model_registry.load()
        sonnet = rewritten({"jobs": [{"label": "a", "model": "claude-sonnet-5-5"}, {"label": "b", "model": "claude-opus-5-5"}]},
                           transport="anthropic", data=live)
        got = [j["model"] for j in sonnet["jobs"]]
        check("live registry: claude-sonnet-5-5 is dispatched suffixed, claude-opus-5-5 is not",
              got == ["claude-sonnet-5-5[1m]", "claude-opus-5-5"], got)
        v = sonnet["__transport"]
        check("live registry: ids never offer the bare sonnet-5-5 pin",
              "claude-sonnet-5-5[1m]" in v["ids"] and "claude-sonnet-5-5" not in v["ids"], v["ids"])
        check("live registry: __rails carries the suffixed id key", "claude-sonnet-5-5[1m]" in sonnet["__rails"]
              and "claude-sonnet-5-5" not in sonnet["__rails"], sorted(k for k in sonnet["__rails"] if "sonnet" in k))
        for model, want in (("claude-sonnet-5-5[1m]", False), ("claude-opus-5-5[1m]", True), ("haiku[1m]", True),
                            ("claude-sonnet-5[1m]", True), ("claude-sonnet-5-5[2m]", True)):
            verdict, _ = wpg.decide_vocabulary("anthropic", "test", wf(jobs=[{"label": "a", "model": model}]), live)
            check("live registry: RULE 1 on %s -> %s" % (model, "DENY" if want else "pass"), (verdict == "deny") == want, verdict)
        _st.HOST_TRANSPORT = "host"

        # scriptPath: the guard reads pins out of a committed workflow file, because orchestration §9
        # mandates that shape and it never appears in tool_input.
        tmp = tempfile.mkdtemp()
        good = os.path.join(tmp, "good.js")
        bad = os.path.join(tmp, "bad.js")
        open(good, "w", encoding="utf-8").write("await agent(p, {model: 'off-alt-1'})\n")
        open(bad, "w", encoding="utf-8").write("await agent(p, {model: 'h-arch'})\n")
        _st.resolve = lambda data=None, env=None: ("off", "test")
        for label, path, want in (("scriptPath file with a host alias → DENY", bad, True),
                                  ("scriptPath file with a legal id → pass", good, False),
                                  ("unreadable scriptPath makes no claim either way", os.path.join(tmp, "gone.js"), False)):
            v, _ = wpg.decide_vocabulary("off", "test", wf(script_path=path), DATA)
            ok = (v == "deny") == want
            failed += not ok
            print("%s %s" % ("ok  " if ok else "FAIL", label))

        # The PRODUCTION call shape passes no data: the guard must load the registry itself.
        _st._registry = lambda: DATA
        _st.resolve = lambda data=None, env=None: ("host", "test")
        v, _ = wpg.decide_vocabulary("host", "test", wf(jobs=[{"label": "a", "model": "h-orch"}]))
        ok = v == "deny"
        failed += not ok
        print("%s an EXCLUDED host alias denies with NO data arg (the production shape)" % ("ok  " if ok else "FAIL"))
        v, _ = wpg.decide_vocabulary("host", "test", wf(jobs=[{"label": "a", "model": "h-fan"}]))
        ok = v != "deny"
        failed += not ok
        print("%s ...and an available one passes with no data arg" % ("ok  " if ok else "FAIL"))
    finally:
        _st.HOST_TRANSPORT, _st.resolve, _st._registry = real

    total = len(CASES) + 2 + 3 + 2 + extra
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
