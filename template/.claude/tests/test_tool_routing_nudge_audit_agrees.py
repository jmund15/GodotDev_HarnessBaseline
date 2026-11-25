#!/usr/bin/env python3
"""Re-runnable proof that tool_routing_nudge.py's PreToolUse advice and hooks/routing_audit.py share one
bulk-copyable rule and one subagent exemption. It sits with the nudge, a coding-layer hook; the audit's own
verdicts are proven in test_routing_audit_subagent_exempt.py, which ships in the pure layer with it."""
import json
import os
import sys
import tempfile

HOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, HOOKS_DIR)

READ_PATH = (
    "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/"
    "Design/some_design_doc.md"
)
BULK_COPYABLE = (
    "Extract the same raw fields from every file and return one copyable entry per input path."
)


def _state_path(state_dir, session_id, agent_id=""):
    stem = session_id[:8] if session_id else "default"
    if agent_id:
        stem += "_" + agent_id[:8]
    return os.path.join(state_dir, stem + ".json")


def _write_state(state_dir, session_id, prompt, agent_id=""):
    os.makedirs(state_dir, exist_ok=True)
    with open(_state_path(state_dir, session_id, agent_id), "w", encoding="utf-8") as fh:
        json.dump({"last_prompt": prompt}, fh)


def run_nudge_agrees():
    tmp = tempfile.mkdtemp(prefix="routing_nudge_read_")
    state_dir = os.path.join(tmp, "state")
    os.environ["HARNESS_HOOK_STATE_DIR"] = state_dir
    import tool_routing_nudge as nudge

    _write_state(state_dir, "sessnud1", BULK_COPYABLE)
    _write_state(state_dir, "sessnud2", BULK_COPYABLE)
    base = {"tool_name": "Read", "tool_input": {"file_path": READ_PATH}}
    _, main_msg = nudge.process(dict(base, session_id="sessnud1", agent_id=""))
    _, sub_msg = nudge.process(dict(base, session_id="sessnud2", agent_id="agent0001"))
    state = json.load(open(_state_path(state_dir, "sessnud1"), encoding="utf-8"))

    label = "PreToolUse advice and audit use the same bulk-copyable rule and subagent exemption"
    # The route the advice names is a per-machine fact (ai-worker registered or not); the case is
    # about the rule and the exemption, so it accepts either rendering.
    ok = bool(main_msg and ("read_files" in main_msg or "bounded Read" in main_msg) and not sub_msg
              and "native-read-bulk-copyable" in state.get("pre_nudges_fired_this_turn", []))
    reason = "" if ok else "main=%r sub=%r state=%r" % (main_msg, sub_msg, state)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def main():
    ok, reason = run_nudge_agrees()
    print("\n%d/1 cases pass" % (1 if ok else 0))
    if not ok:
        print("  FAIL case 1: %s" % reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
