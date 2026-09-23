#!/usr/bin/env python3
"""Proof for the compaction-scoped model-ladder readiness marker."""
import importlib.util
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"


def load(name):
    path = HOOKS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    gate_path = HOOKS / "model_ladder_gate.py"
    if not gate_path.is_file():
        print("FAIL model_ladder_gate.py exists")
        return 1
    state = tempfile.mkdtemp(prefix="ladder_gate_state_")
    project = Path(tempfile.mkdtemp(prefix="ladder_gate_project_"))
    ladder = project / ".claude" / "reference" / "model_ladder_evidence.md"
    ladder.parent.mkdir(parents=True)
    ladder.write_text("# Model Ladder\n", encoding="utf-8")
    old = dict(os.environ)
    os.environ["HARNESS_HOOK_STATE_DIR"] = state
    os.environ["CLAUDE_PROJECT_DIR"] = str(project)
    try:
        gate = load("model_ladder_gate")
        hook_state = load("_hook_state")
        sid = "ladder01"

        def payload(tool="Read", path=None, **extra):
            tool_input = {"file_path": str(path or ladder)}
            tool_input.update(extra)
            return {"tool_name": tool, "session_id": sid, "tool_input": tool_input}

        cases = []
        cases.append(("full exact-path Read arms the marker", gate.mark_loaded(payload())))
        cases.append(("a loaded ladder authorizes a dispatch", gate.claim_loaded(sid)))
        cases.append(("a dispatch does not consume the marker", gate.claim_loaded(sid)))
        cases.append(("offset Read cannot arm", not gate.mark_loaded(payload(offset=1))))
        cases.append(("limited Read cannot arm", not gate.mark_loaded(payload(limit=20))))
        cases.append(("Grep cannot substitute for a full load", not gate.mark_loaded(payload(tool="Grep"))))
        cases.append(("a prose-adjacent file cannot arm", not gate.mark_loaded(
            payload(path=project / ".claude" / "plans" / "mentions-model-ladder.md"))))
        failed_read = payload()
        failed_read["tool_response"] = {"is_error": True}
        cases.append(("a failed Read cannot arm", not gate.mark_loaded(failed_read)))

        gate.mark_loaded(payload())
        hook_state.clear_compaction_keys(sid)
        cases.append(("PreCompact clears readiness", not gate.claim_loaded(sid)))

        other = dict(payload())
        other["session_id"] = "subagent02"
        gate.mark_loaded(other)
        cases.append(("a subagent session cannot arm the parent", not gate.claim_loaded(sid)
                      and gate.claim_loaded("subagent02")))

        gate.mark_loaded(payload())
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _n: gate.claim_loaded(sid), range(2)))
        cases.append(("two concurrent dispatches both see the loaded ladder", claims == [True, True]))

        worktree = project / ".claude" / "worktrees" / "wt1"
        (worktree / ".claude" / "reference").mkdir(parents=True)
        wt_ladder = worktree / ".claude" / "reference" / "model_ladder_evidence.md"
        wt_ladder.write_text("# stale copy\n", encoding="utf-8")
        wt_sid = "ladderwt"
        wt_payload = {"tool_name": "Read", "session_id": wt_sid, "cwd": str(worktree),
                      "tool_input": {"file_path": ".claude/reference/model_ladder_evidence.md"}}
        named = getattr(gate, "expected_path", lambda _p: "")(wt_payload)
        cases.append(("expected_path names the primary ladder absolutely from a worktree cwd",
                      os.path.isabs(named) and os.path.normcase(named)
                      == os.path.normcase(os.path.realpath(ladder))))
        abs_sid = "ladderab"
        abs_payload = dict(wt_payload, session_id=abs_sid, tool_input={"file_path": named or "x"})
        cases.append(("a Read of the path the denial names arms the gate",
                      gate.mark_loaded(abs_payload) and gate.claim_loaded(abs_sid)))
        stale_payload = dict(wt_payload, session_id="ladderst", tool_input={"file_path": str(wt_ladder)})
        cases.append(("a Read of the worktree's stale copy cannot arm",
                      not gate.mark_loaded(stale_payload)))

        path = Path(hook_state.state_path(sid))
        path.write_text("{not-json", encoding="utf-8")
        cases.append(("corrupt state fails closed", not gate.claim_loaded(sid)))
    finally:
        os.environ.clear()
        os.environ.update(old)

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
