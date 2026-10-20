#!/usr/bin/env python3
"""Re-runnable proof for hooks/pre_workflow_dispatch.py, the single PreToolUse entry for Workflow|Agent.

instruction_quality §3: a guard with several preconditions names every unmet one in a single deny.
The client surfaces one deny reason per tool call, so five separate hooks showed only one of them and
the retry met the next. Real PreToolUse payloads go to the dispatcher on stdin, against a planted
world: a capacity cache (PROVIDER_CAPACITY_CACHE) holding an exhausted or available anthropic plan, a
per-session doctrine state (HARNESS_HOOK_STATE_DIR) that is armed or empty, and a fresh HOME. Member
verdicts that cannot be planted through a subprocess (a crash, two input rewriters, a retired
import) run in-process through `dispatch(raw, hooks=...)` with real member contracts.

A run is classified CRASH on an exit code outside {0, 2} or a traceback, never as an allow.

    python3 .claude/tests/test_pre_workflow_dispatch.py
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
HOOKS_DIR = REPO / ".claude" / "hooks"
DISPATCH = HOOKS_DIR / "pre_workflow_dispatch.py"
FAILURES = []

sys.path.insert(0, str(HOOKS_DIR))


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:400]))


def classify(proc):
    """CRASH for an exit outside {0, 2} or a traceback; else the verdict the channel carries."""
    if proc.returncode not in (0, 2) or "Traceback (most recent call last)" in (proc.stderr or ""):
        return "CRASH"
    if proc.returncode == 2:
        return "deny"
    out = hso(proc.stdout)
    return "deny" if out.get("permissionDecision") == "deny" else "allow"


def hso(stdout):
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


REGISTRY = json.loads((REPO / ".claude" / "reference" / "external_models.json").read_text(encoding="utf-8"))


def capacity_cache(path, status):
    """A capacity cache for every registered transport: the host plan reads `status`, the rest read as a
    failed probe, so no other plan has room and no live probe runs."""
    now = time.time()
    cache = {}
    for name, cfg in REGISTRY["transports"].items():
        base = {"schemaVersion": 1, "transport": name, "costModel": cfg.get("costModel"),
                "sourceKind": "live", "observedAt": iso(now)}
        if name == "anthropic":
            spent = status == "exhausted"
            band = "Exhausted" if spent else "On pace"
            window = {"name": "five_hour", "usedPercent": 100.0 if spent else 10.0, "resetsAt": now + 3600,
                      "band": band, "pressure": 2.0 if spent else 0.3, "runoutRatio": 0.5,
                      "windowDurationMins": 300, "dispatchFloorPercent": 99}
            base.update(status=status, balance=None, error=None, quota={
                "dispatchBand": band, "routingBand": band, "bindingWindow": "five_hour",
                "routingWindow": "five_hour", "windows": [window]})
        else:
            base.update(status="network-error", quota=None, balance=None,
                        error={"kind": "network", "message": "planted"})
        cache[name] = base
    path.write_text(json.dumps(cache), encoding="utf-8")


def arm_doctrine(state_dir, session_id):
    """The state `skill_load_marker` and `model_ladder_gate` record once the doctrine is in context."""
    import _hook_state
    import model_ladder_gate
    with patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir):
        _hook_state.mark_skill_in_context(session_id, "orchestration")
        model_ladder_gate.update_json_locked(
            model_ladder_gate.state_path(session_id), lambda s: s.__setitem__(model_ladder_gate.STATE_KEY, True))


class World:
    """One planted environment: capacity, doctrine, a fresh HOME. `run` feeds a payload to the dispatcher."""

    def __init__(self, capacity, doctrine, session_id):
        self.root = Path(tempfile.mkdtemp(prefix="pwd_proof_"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = str(self.root / "state")
        os.makedirs(self.state)
        self.session = session_id
        cache = self.root / "capacity.json"
        capacity_cache(cache, capacity)
        if doctrine:
            arm_doctrine(self.state, session_id)
        self.env = dict(
            os.environ, HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_PROJECT_DIR=str(REPO),
            CLAUDE_CODE_TRANSPORT="anthropic", PROVIDER_CAPACITY_CACHE=str(cache),
            HARNESS_HOOK_STATE_DIR=self.state, HARNESS_PROVIDER_GUARD_LOG=str(self.root / "guard.jsonl"))
        for name in ("CLAUDE_CODE_SIDECAR", "HARNESS_WORKFLOW_ARGS_OWNER"):
            self.env.pop(name, None)

    def payload(self, **fields):
        base = {"tool_name": "Workflow", "session_id": self.session, "cwd": str(REPO), "transcript_path": "",
                "tool_input": {"script": "await agent(p, {effort: 'high'})", "args": {}}}
        base.update(fields)
        return base

    def run(self, payload=None, raw=None):
        text = raw if raw is not None else json.dumps(payload or self.payload())
        return subprocess.run([sys.executable, str(DISPATCH)], input=text, capture_output=True, text=True,
                              encoding="utf-8", env=self.env, cwd=str(REPO), timeout=120)

    def marker(self):
        return self.home / ".claude" / ".routing_state" / ("readonly-%s.json" % self.session[:8])


def reason_of(proc):
    return hso(proc.stdout).get("permissionDecisionReason", "")


CAPACITY = "Provider capacity exhausted for anthropic"
DOCTRINE = "the dispatch doctrine is not in context"


def real_payload_cases():
    both = World("exhausted", False, "pwdboth1")
    proc = both.run()
    reason = reason_of(proc)
    check("capacity exhausted + doctrine unloaded -> ONE deny naming both",
          classify(proc) == "deny" and CAPACITY in reason and DOCTRINE in reason, (proc.stdout, proc.stderr[-300:]))
    check("the doctrine reason precedes the capacity reason (stable member order)",
          reason.find(DOCTRINE) != -1 and reason.find(DOCTRINE) < reason.find(CAPACITY), reason[:300])
    check("each reason appears once", reason.count(CAPACITY) == 1 and reason.count(DOCTRINE) == 1, reason)
    check("exactly one JSON verdict is emitted", len(proc.stdout.strip().splitlines()) == 1, proc.stdout)

    cap_only = World("exhausted", True, "pwdcap01")
    proc = cap_only.run()
    reason = reason_of(proc)
    check("capacity exhausted alone -> its own reason only",
          classify(proc) == "deny" and CAPACITY in reason and DOCTRINE not in reason, (proc.stdout, proc.stderr[-300:]))

    doctrine_only = World("available", False, "pwddoc01")
    proc = doctrine_only.run()
    reason = reason_of(proc)
    check("doctrine unloaded alone -> its own reason only",
          classify(proc) == "deny" and DOCTRINE in reason and "capacity" not in reason.lower(),
          (proc.stdout, proc.stderr[-300:]))

    clean = World("available", True, "pwdclean")
    proc = clean.run()
    out = hso(proc.stdout)
    check("clean call -> no deny", classify(proc) == "allow", (proc.stdout[:300], proc.stderr[-300:]))
    check("clean call keeps the provider guard's updatedInput (args.__rails injected)",
          "__rails" in ((out.get("updatedInput") or {}).get("args") or {}), proc.stdout[:300])
    check("the updatedInput keeps the caller's script", (out.get("updatedInput") or {}).get("script", "").startswith(
        "await agent(p"), proc.stdout[:300])

    nested = World("exhausted", True, "pwdnest1")
    brief = nested.root / "brief.md"
    brief.write_text("Dispatch via review_fanout.js.\n", encoding="utf-8")
    proc = nested.run(nested.payload(tool_input={"script": "await agent(p, {effort: 'high'})",
                                                 "args": {"jobs": [{"promptPath": str(brief)}]}}))
    reason = reason_of(proc)
    check("a nested fan-out and an exhausted plan surface in one deny",
          classify(proc) == "deny" and "dispatch a fan-out" in reason and CAPACITY in reason, proc.stdout[:400])

    delegate = World("exhausted", False, "pwdsub01")
    proc = delegate.run(delegate.payload(transcript_path="C:/x/subagents/agent-1.jsonl"))
    reason = reason_of(proc)
    check("a delegate's dispatch denies as orchestrator-only and names no orchestrator-side precondition",
          classify(proc) == "deny" and "orchestrator-only" in reason and CAPACITY not in reason
          and DOCTRINE not in reason, proc.stdout[:400])

    tool = World("exhausted", False, "pwdbash1")
    proc = tool.run(tool.payload(tool_name="Bash", tool_input={"command": "ls"}))
    check("a non-dispatch tool call is silent", classify(proc) == "allow" and not proc.stdout.strip(), proc.stdout)
    proc = tool.run(raw="not json")
    check("malformed stdin exits 0 silent", proc.returncode == 0 and not proc.stdout.strip(), proc.stderr[-200:])

    engine = clean.root / ".claude" / "workflows" / "engine.js"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"// READONLY-FANOUT-ENGINE\r\nconst x = 1;\r\n")
    proc = clean.run(clean.payload(tool_input={"scriptPath": str(engine), "args": {}}))
    check("a read-only engine dispatch rewrites CRLF to LF through the lf member",
          classify(proc) == "allow" and b"\r" not in engine.read_bytes(), (proc.stdout[:300], proc.stderr[-300:]))
    check("a read-only engine dispatch that passes arms the marker", clean.marker().is_file(), proc.stdout[:300])

    blocked = World("exhausted", False, "pwdarm002")
    engine = blocked.root / ".claude" / "workflows" / "engine.js"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"// READONLY-FANOUT-ENGINE\r\nconst x = 1;\r\n")
    proc = blocked.run(blocked.payload(tool_input={"scriptPath": str(engine), "args": {}}))
    check("a denied engine dispatch arms no marker, since no lens will run", classify(proc) == "deny"
          and not blocked.marker().exists(), (proc.stdout[:300], blocked.marker()))
    check("a denied engine dispatch still gets its CRLF normalized (the permission handler reads it)",
          b"\r" not in engine.read_bytes(), engine.read_bytes())

    crash = subprocess.run([sys.executable, "-c", "raise RuntimeError('planted')"], capture_output=True, text=True)
    check("the proof classifies a crashing hook process as CRASH, never allow", classify(crash) == "CRASH")


# ---- in-process cases over stub members ------------------------------------------------------------------
def member(name, out=None, code=0, err="", raises=None, seen=None):
    """A member module: prints `out` (a hookSpecificOutput dict) and returns `code`, or raises."""
    def main():
        if seen is not None:
            seen.append(json.loads(sys.stdin.read()))
        if raises:
            raise raises
        if out is not None:
            sys.stdout.write(json.dumps({"hookSpecificOutput": dict(out, hookEventName="PreToolUse")}))
        if err:
            sys.stderr.write(err)
        return code
    return SimpleNamespace(__name__=name, __file__=name + ".py", main=main)


def deny(reason):
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def run_chain(members, tool="Workflow", tool_input=None):
    import pre_workflow_dispatch as pwd
    raw = json.dumps({"tool_name": tool, "session_id": "pwdchain", "tool_input": tool_input or {"args": {"a": 1}}})
    code, out, err = pwd.dispatch(raw, hooks=tuple((m, ()) for m in members))
    parsed = json.loads(out)["hookSpecificOutput"] if out else {}
    return code, parsed, err


def in_process_cases():
    import pre_workflow_dispatch as pwd
    code, out, _ = run_chain([member("dispatch_mechanism_guard", deny("A unmet")),
                              member("workflow_provider_guard", deny("B unmet"))])
    reason = out.get("permissionDecisionReason", "")
    check("two member denies join into one reason, in member order",
          code == 0 and out.get("permissionDecision") == "deny" and 0 <= reason.find("A unmet") < reason.find("B unmet"),
          reason)
    _, out, _ = run_chain([member("dispatch_mechanism_guard", deny("same")), member("workflow_provider_guard", deny("same"))])
    check("an identical reason from two members appears once", out["permissionDecisionReason"].count("same") == 1, out)
    _, out, _ = run_chain([member("dispatch_mechanism_guard", deny("only"))])
    check("a single reason is emitted verbatim", out.get("permissionDecisionReason") == "only", out)

    _, out, _ = run_chain([member("workflow_script_lf_guard", {"additionalContext": "note one"}),
                           member("dispatch_mechanism_guard", {"additionalContext": "note two"})])
    check("additionalContext notes concatenate", out.get("additionalContext") == "note one\n\nnote two"
          and "permissionDecision" not in out, out)

    _, out, _ = run_chain([member("dispatch_mechanism_guard", code=2, err="blocked by stderr")])
    check("a member's exit 2 stderr joins the deny", out.get("permissionDecision") == "deny"
          and "blocked by stderr" in out.get("permissionDecisionReason", ""), out)

    seen = []
    _, out, _ = run_chain([
        member("first_rewriter", {"updatedInput": {"args": {"a": 1, "first": True}}}),
        member("second_rewriter", {"updatedInput": {"args": {"a": 1, "first": True, "second": True}, "extra": 1}},
               seen=seen)])
    check("two input rewriters chain: the later member reads the earlier rewrite",
          seen and seen[0]["tool_input"]["args"].get("first") is True, seen)
    check("two input rewriters merge: the later rewrite is a superset and the output keeps both",
          out["updatedInput"]["args"] == {"a": 1, "first": True, "second": True} and out["updatedInput"]["extra"] == 1, out)
    _, out, _ = run_chain([member("partial", {"updatedInput": {"extra": 2}})])
    check("a partial updatedInput overlays the call's own input, keeping its other fields",
          out["updatedInput"] == {"args": {"a": 1}, "extra": 2}, out)
    _, out, _ = run_chain([member("dispatch_mechanism_guard", deny("blocked")),
                           member("workflow_provider_guard", {"updatedInput": {"extra": 3}})])
    check("a denied call carries no updatedInput", "updatedInput" not in out, out)

    ran = []
    _, out, _ = run_chain([
        member("subagent_dispatch_guard", deny("orchestrator-only")),
        member("workflow_provider_guard", deny("capacity"), seen=ran)])
    check("a role deny ends the chain: the delegate cannot meet the orchestrator's preconditions",
          not ran and out["permissionDecisionReason"] == "orchestrator-only", (ran, out))

    ran = []
    _, out, _ = run_chain([member("dispatch_mechanism_guard", deny("doctrine")),
                           member("readonly_marker_arm", seen=ran)])
    check("readonly_marker_arm does not run on a denied call", not ran, ran)
    ran = []
    run_chain([member("dispatch_mechanism_guard"), member("readonly_marker_arm", seen=ran)])
    check("readonly_marker_arm runs on an allowed call", len(ran) == 1, ran)

    boom = RuntimeError("planted crash")
    code, out, err = run_chain([member("dispatch_mechanism_guard", raises=boom),
                                member("workflow_provider_guard", deny("capacity"))])
    check("an advisory member's crash names the lost check and repair; later members still decide",
          code == 0 and "dispatch_mechanism_guard" in out.get("additionalContext", "")
          and "did not run" in out.get("additionalContext", "")
          and "Repair .claude/hooks/dispatch_mechanism_guard.py" in out.get("additionalContext", "")
          and out.get("permissionDecisionReason") == "capacity" and "planted crash" in err, (out, err[-200:]))

    code, out, _ = run_chain([member("workflow_provider_guard", raises=boom)])
    check("a provider-guard crash on a Workflow call denies: no args.__transport means the engine cannot run",
          out.get("permissionDecision") == "deny" and "planted crash" in out.get("permissionDecisionReason", "")
          and "__transport" in out.get("permissionDecisionReason", ""), out)
    code, out, _ = run_chain([member("workflow_provider_guard", raises=boom)], tool="Agent")
    check("a provider-guard crash on an Agent call is an advisory, as the guard's own posture is",
          out.get("permissionDecision") is None and "workflow_provider_guard" in out.get("additionalContext", ""), out)
    code, out, _ = run_chain([member("workflow_provider_guard", code=1, err="Traceback boom")])
    check("a non-zero non-2 exit is a fault, never an allow", out.get("permissionDecision") == "deny", out)

    stub = pwd._failed_import("workflow_provider_guard", ImportError("planted"))
    _, out, _ = run_chain([stub])
    check("an import fault in the provider guard denies a Workflow call and names the module",
          out.get("permissionDecision") == "deny" and "workflow_provider_guard" in out.get("permissionDecisionReason", "")
          and "planted" in out.get("permissionDecisionReason", ""), out)
    stub = pwd._failed_import("workflow_script_lf_guard", ImportError("planted"))
    _, out, _ = run_chain([stub])
    check("an import fault in an advisory member names the lost check without denying",
          out.get("permissionDecision") is None and "workflow_script_lf_guard" in out.get("additionalContext", ""), out)

    names = [getattr(m, "__name__", "") for m, _ in pwd.HOOKS]
    check("the five Workflow|Agent members are wired, role gate first and the marker arm last",
          names == ["subagent_dispatch_guard", "workflow_script_lf_guard", "dispatch_mechanism_guard",
                    "workflow_provider_guard", "readonly_marker_arm"], names)


def main():
    check("hooks/pre_workflow_dispatch.py exists", DISPATCH.is_file(), str(DISPATCH))
    real_payload_cases()
    try:
        in_process_cases()
    except ImportError as exc:
        check("pre_workflow_dispatch imports for the in-process cases", False, exc)
    print("\n%d failed" % len(FAILURES) if FAILURES else "\nall pass")
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
