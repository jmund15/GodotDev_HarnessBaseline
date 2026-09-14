#!/usr/bin/env python3
"""Proof for RULE 1 of workflow_provider_guard.py — pin vocabulary per transport.

Sibling of test_workflow_provider_guard.py (currency) and _roles.py. Split because the two rules
are independent: a vocabulary deny fires at every band, a currency deny only in a conserving one.

The negatives carry the weight. A vocabulary guard that denies everything looks identical to one
that works, so every transport gets a legal pin that MUST pass alongside the illegal one that
must deny — and the shapes that must never fire at all (a `model:` key inside a line or block
comment).

`PIN('opus')` is NOT one of them. PIN() was a translator and is now `(m) => m`, so a wrapped
literal is still a literal pin; the case asserting otherwise shipped a guard blind to the three
committed scripts that use only that shape.

Run: python3 .claude/tests/test_workflow_provider_guard_vocabulary.py
"""
import importlib.util
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "workflow_provider_guard.py")
spec = importlib.util.spec_from_file_location("wpg", HOOK)
wpg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wpg)

DATA = {
    "transports": {
        "anthropic": {"roleSource": "reference/model_ladder_evidence.md"},
        "codex": {"launcher": ".claude/scripts/codex_proxy_sidecar.sh"},
        "deepseek": {"baseUrl": "https://api.deepseek.com/anthropic"},
    },
    "models": [
        {"transport": "codex", "id": "gpt-5.6-terra", "alias": "terra"},
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna"},
        {"transport": "deepseek", "id": "deepseek-v4-pro", "alias": "pro"},
        {"transport": "anthropic", "id": "claude-opus-5", "alias": "opus"},
    ],
}


def _oversize_script():
    """A real script file just past the hook's scan cap, so the scan is genuinely truncated."""
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".js", prefix="oversize_")
    os.close(fd)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("// filler\n" * 60000)          # ~600KB, past SCRIPT_READ_CAP
        fh.write("agent(x, {model: 'opus'})\n")  # a pin the truncated read cannot see
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
    # A job with no model is malformed input, not a pin. It used to reach decide_vocabulary as an
    # empty-string pin and deny with "Pin not serviceable" naming no model at all.
    ("a jobs entry with NO model is not a pin, so nothing is denied", "codex",
     wf(jobs=[{"label": "a"}]), False),
    # A scriptPath the hook can FIND but not fully read leaves a blind spot: a pin past the cap
    # would be invisible and read exactly like a clean script. Truncation therefore denies, while
    # an unreadable path (below) stays silent — the hook may simply have guessed the root wrong.
    ("a scriptPath too large to scan whole is DENIED, not passed", "codex",
     wf(jobs=[{"label": "a", "model": "gpt-5.6-luna"}], script_path=_oversize_script()), True),
    ("...but a real bad pin beside it still denies", "codex",
     wf(jobs=[{"label": "a"}, {"label": "b", "model": "opus"}]), True),
    ("anthropic + role name → pass", "anthropic", wf(jobs=[{"label": "a", "model": "sonnet"}]), False),
    ("anthropic + a REGISTERED claude-* id → pass", "anthropic", wf(jobs=[{"label": "a", "model": "claude-opus-5"}]), False),
    ("anthropic + an UNREGISTERED claude-* id → DENY (wildcard admitted typos)", "anthropic",
     wf(jobs=[{"label": "a", "model": "claude-sonnet-4"}]), True),
    ("anthropic + GPT vendor id → DENY", "anthropic", wf(jobs=[{"label": "a", "model": "gpt-5.6-terra"}]), True),

    ("codex + its own id → pass", "codex", wf(jobs=[{"label": "a", "model": "gpt-5.6-terra"}]), False),
    ("codex + its own alias → pass", "codex", wf(jobs=[{"label": "a", "model": "terra"}]), False),
    ("codex + anthropic role name → DENY", "codex", wf(jobs=[{"label": "a", "model": "sonnet"}]), True),
    ("codex + another transport's id → DENY", "codex", wf(jobs=[{"label": "a", "model": "deepseek-v4-pro"}]), True),

    ("deepseek + its own id → pass", "deepseek", wf(jobs=[{"label": "a", "model": "deepseek-v4-pro"}]), False),
    ("deepseek + anthropic role name → DENY", "deepseek", wf(jobs=[{"label": "a", "model": "opus"}]), True),

    ("unknown transport denies every pin", "unknown", wf(jobs=[{"label": "a", "model": "sonnet"}]), True),
    ("unknown transport denies a vendor id too", "unknown", wf(jobs=[{"label": "a", "model": "gpt-5.6-terra"}]), True),

    ("Agent tool obeys the same rule", "codex", agent("sonnet"), True),
    ("Agent tool legal pin passes", "codex", agent("gpt-5.6-luna"), False),

    ("review_fanout omitted pin defaults to sonnet → DENY on codex", "codex",
     wf(agents=[{"key": "x", "prompt": "p"}]), True),

    # --- must not fire at all ---
    ("no pins on the call → silent", "codex", wf(jobs=[]), False),
    ("inline script: legal codex id → pass", "codex",
     wf(script="agent(p, {model: 'gpt-5.6-terra', effort: 'low'})"), False),
    ("inline script: role literal → DENY", "codex",
     wf(script="agent(p, {model: 'sonnet', effort: 'low'})"), True),
    ("inline script: backtick quotes are read", "codex",
     wf(script="agent(p, {model: `sonnet`})"), True),
    # PIN() WAS a translator; it is now `(m) => m` in every engine, so this is a literal pin and
    # must deny. The previous version of this case asserted the opposite and rewarded the bug:
    # three committed scripts carry ONLY this shape and sailed past the guard on a codex session.
    ("inline script: PIN('sonnet') is a literal pin, not a resolver call", "codex",
     wf(script="agent(p, {model: PIN('sonnet')})"), True),
    ("inline script: PIN() with a legal codex id still passes", "codex",
     wf(script="agent(p, {model: PIN('gpt-5.6-terra')})"), False),
    ("inline script: EFF()-style wrapper on a role name also denies", "codex",
     wf(script="agent(p, {model: SOMEFN('opus')})"), True),
    ("inline script: a line-comment pin does NOT fire", "codex",
     wf(script="// the model: 'sonnet' convention is described in the docs"), False),
    ("inline script: a block-comment pin does NOT fire", "codex",
     wf(script="/* pins look like model: 'opus' */\nagent(p, {model: 'terra'})"), False),
    ("inline script: a comment does not mask a real pin below it", "codex",
     wf(script="// model: 'terra' is legal here\nagent(p, {model: 'sonnet'})"), True),
    ("inline script: a URL is not read as a comment", "codex",
     wf(script="// see https://x.test/a\nagent(p, {model: 'sonnet'})"), True),
]


# A registry where a HOST row is excluded -- the only shape in which the carve-out should deny.
HOSTED_EXCLUDED = {
    "transports": dict(DATA["transports"]),
    "models": list(DATA["models"]) + [
        {"transport": "anthropic", "id": "claude-sonnet-5", "alias": "sonnet"},
        {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable",
         "status": {"state": "unavailable", "scope": "all", "reason": "test exclusion"}},
    ],
}


def main():
    failed = 0
    for name, transport, payload, want_deny in CASES:
        verdict, reason = wpg.decide_vocabulary(transport, "test", payload, DATA)
        got_deny = verdict == "deny"
        ok = got_deny == want_deny
        failed += not ok
        print("%s %s: want %s, got %s%s"
              % ("ok  " if ok else "FAIL", name,
                 "deny" if want_deny else "pass", "deny" if got_deny else "pass",
                 "" if ok or not got_deny else " | " + (reason or "")[:90]))

    # scriptPath: the guard must read pins out of a committed workflow file, because
    # orchestration section 9 mandates that shape and it never appears in tool_input.
    tmp = tempfile.mkdtemp()
    good = os.path.join(tmp, "good.js")
    bad = os.path.join(tmp, "bad.js")
    open(good, "w", encoding="utf-8").write("await agent(p, {model: 'gpt-5.6-terra'})\n")
    open(bad, "w", encoding="utf-8").write("await agent(p, {model: 'opus'})\n")

    v, _ = wpg.decide_vocabulary("codex", "test", wf(script_path=bad), DATA)
    ok = v == "deny"
    failed += not ok
    print("%s scriptPath file with a role literal → DENY" % ("ok  " if ok else "FAIL"))

    v, _ = wpg.decide_vocabulary("codex", "test", wf(script_path=good), DATA)
    ok = v != "deny"
    failed += not ok
    print("%s scriptPath file with a legal id → pass" % ("ok  " if ok else "FAIL"))

    v, _ = wpg.decide_vocabulary("codex", "test", wf(script_path=os.path.join(tmp, "gone.js")), DATA)
    ok = v != "deny"
    failed += not ok
    print("%s unreadable scriptPath makes no claim either way" % ("ok  " if ok else "FAIL"))

    # ---- S4: the host carve-out must respect an exclusion --------------------
    # `decide_vocabulary` short-circuits the four bare Anthropic role names on the host transport
    # BEFORE the legal set applies. Unguarded, excluding an Anthropic row denied it from every
    # provider seat and left it fully pinnable from an Anthropic one -- the asymmetry is the bug.
    HOSTED = {
        "transports": dict(DATA["transports"]),
        "models": list(DATA["models"]) + [
            {"transport": "anthropic", "id": "claude-sonnet-5", "alias": "sonnet"},
            {"transport": "anthropic", "id": "claude-fable-5-1", "alias": "fable",
             "status": {"state": "unavailable", "scope": "all", "reason": "test exclusion"}},
        ],
    }

    v, _ = wpg.decide_vocabulary("anthropic", "test", wf(jobs=[{"label": "a", "model": "sonnet"}]),
                                 HOSTED)
    ok = v != "deny"
    failed += not ok
    print("%s an AVAILABLE anthropic alias still passes on the host seat"
          % ("ok  " if ok else "FAIL"))

    v, reason = wpg.decide_vocabulary("anthropic", "test",
                                      wf(jobs=[{"label": "a", "model": "fable"}]), HOSTED)
    ok = v == "deny"
    failed += not ok
    print("%s an EXCLUDED anthropic alias is denied on the host seat too (no asymmetry)"
          % ("ok  " if ok else "FAIL"))

    # The carve-out exists because the ladder owns these names and the registry carried no
    # Anthropic rows at all until 2026-09-04. An absent row is not an exclusion.
    v, _ = wpg.decide_vocabulary("anthropic", "test", wf(jobs=[{"label": "a", "model": "haiku"}]),
                                 HOSTED)
    ok = v != "deny"
    failed += not ok
    print("%s a role name the registry does not carry keeps passing (an absence is not a deny)"
          % ("ok  " if ok else "FAIL"))

    # --- the PRODUCTION call shape: decide_vocabulary is called with THREE args ---------------
    # Every case above passes DATA explicitly, so all of them exercised a path production never
    # takes. With `data=None` the carve-out helper hit `None.get("models")`, its own `except`
    # swallowed it and returned the ALLOW answer -- the guard was inert everywhere but here.
    # "A clean exit proves the machinery ran, never that it engaged its target."
    import _session_transport as _st
    real_registry = _st._registry
    _st._registry = lambda: HOSTED_EXCLUDED
    try:
        v, reason = wpg.decide_vocabulary("anthropic", "test",
                                          wf(jobs=[{"label": "a", "model": "fable"}]))
        ok = v == "deny"
        failed += not ok
        print("%s an EXCLUDED anthropic alias denies with NO data arg (the production shape)"
              % ("ok  " if ok else "FAIL"))

        v, _ = wpg.decide_vocabulary("anthropic", "test",
                                     wf(jobs=[{"label": "a", "model": "sonnet"}]))
        ok = v != "deny"
        failed += not ok
        print("%s ...and an available one still passes with no data arg"
              % ("ok  " if ok else "FAIL"))

        ok = wpg._host_alias_excluded("fable", None) is True
        failed += not ok
        print("%s _host_alias_excluded loads the registry itself when handed None"
              % ("ok  " if ok else "FAIL"))
    finally:
        _st._registry = real_registry

    # An unreadable registry must not turn every host pin into a deny.
    _st._registry = lambda: None
    try:
        ok = wpg._host_alias_excluded("fable", None) is False
        failed += not ok
        print("%s an unreadable registry is not an exclusion (advisory fails open, per-name)"
              % ("ok  " if ok else "FAIL"))
    finally:
        _st._registry = real_registry

    total = len(CASES) + 3 + 3 + 4
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
