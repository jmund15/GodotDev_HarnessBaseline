#!/usr/bin/env python3
"""Re-runnable proof for prompt-aware Read verdicts in hooks/routing_audit.py."""
import importlib
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
FOCUSED = "Read this one file for the exact paragraph that defines the current contract."
DERIVED = "Compare these modules, judge the architecture, and recommend which design should remain."
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


def _fresh_routing_audit(state_dir, log_path):
    os.environ["HARNESS_HOOK_STATE_DIR"] = state_dir
    os.environ["HARNESS_ROUTING_AUDIT_LOG_PATH"] = log_path
    if "routing_audit" in sys.modules:
        del sys.modules["routing_audit"]
    return importlib.import_module("routing_audit")


def _read_rows(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run_read(label, prompt, agent_id="", expected=None):
    tmp = tempfile.mkdtemp(prefix="routing_audit_read_")
    state_dir = os.path.join(tmp, "state")
    log_path = os.path.join(tmp, "routing_audit.jsonl")
    session_id = "sess0001"
    _write_state(state_dir, session_id, prompt)
    module = _fresh_routing_audit(state_dir, log_path)
    module.process({
        "tool_name": "Read",
        "tool_input": {"file_path": READ_PATH},
        "session_id": session_id,
        "agent_id": agent_id,
    })
    rows = _read_rows(log_path)

    ok = True
    reason = ""
    if expected is None:
        if rows:
            ok, reason = False, "unexpected audit row: %r" % rows[-1]
    elif not rows:
        ok, reason = False, "no audit row written"
    else:
        row = rows[-1]
        if row.get("classification") != expected:
            ok, reason = False, "classification=%r want=%r" % (row.get("classification"), expected)
        elif row.get("rule") != "native-read-bulk-copyable":
            ok, reason = False, "rule=%r" % row.get("rule")
        elif expected == "cue-exempt" and "[subagent: bundling delegate]" not in (row.get("reason") or ""):
            ok, reason = False, "reason missing subagent tag: %r" % row.get("reason")
        elif agent_id and row.get("prompt_excerpt") != prompt:
            ok, reason = False, "subagent row did not inherit parent prompt: %r" % row.get("prompt_excerpt")

    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def run_other_rule_unchanged():
    tmp = tempfile.mkdtemp(prefix="routing_audit_other_")
    state_dir = os.path.join(tmp, "state")
    log_path = os.path.join(tmp, "routing_audit.jsonl")
    session_id = "sess0002"
    _write_state(state_dir, session_id, "Find the DomainCore symbol definition and references.")
    module = _fresh_routing_audit(state_dir, log_path)
    module.process({
        "tool_name": "Grep",
        "tool_input": {"pattern": "DomainCore", "glob": "*.cs"},
        "session_id": session_id,
        "agent_id": "agent0001",
    })
    rows = _read_rows(log_path)
    label = "agent_id does not exempt the PascalCase C# Grep rule"
    ok = bool(rows and rows[-1].get("classification") == "nudge-warranted"
              and rows[-1].get("rule") == "pascal-grep-on-cs"
              and "[subagent: bundling delegate]" not in (rows[-1].get("reason") or ""))
    reason = "" if ok else "control row missing or changed: %r" % (rows[-1] if rows else None)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def run_torn_parent_state():
    tmp = tempfile.mkdtemp(prefix="routing_audit_torn_parent_")
    state_dir = os.path.join(tmp, "state")
    log_path = os.path.join(tmp, "routing_audit.jsonl")
    session_id = "sess0003"
    _write_state(state_dir, session_id, BULK_COPYABLE)
    with open(_state_path(state_dir, session_id), "a", encoding="utf-8") as fh:
        fh.write(" trailing debris")
    module = _fresh_routing_audit(state_dir, log_path)
    module.process({
        "tool_name": "Read",
        "tool_input": {"file_path": READ_PATH},
        "session_id": session_id,
        "agent_id": "agent0002",
    })
    rows = _read_rows(log_path)
    label = "subagent salvages a torn parent state before classifying its inherited prompt"
    ok = bool(rows and rows[-1].get("classification") == "cue-exempt"
              and rows[-1].get("prompt_excerpt") == BULK_COPYABLE)
    reason = "" if ok else "row missing or wrong: %r" % (rows[-1] if rows else None)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def run_nudge_agrees():
    tmp = tempfile.mkdtemp(prefix="routing_nudge_read_")
    state_dir = os.path.join(tmp, "state")
    os.environ["HARNESS_HOOK_STATE_DIR"] = state_dir
    if "tool_routing_nudge" in sys.modules:
        del sys.modules["tool_routing_nudge"]
    nudge = importlib.import_module("tool_routing_nudge")

    _write_state(state_dir, "sessnud1", BULK_COPYABLE)
    _write_state(state_dir, "sessnud2", BULK_COPYABLE)
    base = {"tool_name": "Read", "tool_input": {"file_path": READ_PATH}}
    _, main_msg = nudge.process(dict(base, session_id="sessnud1", agent_id=""))
    _, sub_msg = nudge.process(dict(base, session_id="sessnud2", agent_id="agent0001"))
    state = json.load(open(_state_path(state_dir, "sessnud1"), encoding="utf-8"))

    label = "PreToolUse advice and audit use the same bulk-copyable rule and subagent exemption"
    ok = bool(main_msg and "read_files" in main_msg and not sub_msg
              and "native-read-bulk-copyable" in state.get("pre_nudges_fired_this_turn", []))
    reason = "" if ok else "main=%r sub=%r state=%r" % (main_msg, sub_msg, state)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def main():
    checks = [
        run_read("path alone emits no routing-audit verdict", "", expected=None),
        run_read("focused Read emits no worker-routing verdict", FOCUSED, expected=None),
        run_read("derived judgment emits no copyable-worker verdict", DERIVED, expected=None),
        run_read("explicit bulk-copyable Read is nudge-warranted", BULK_COPYABLE,
                 expected="nudge-warranted"),
        run_read("subagent inherits parent bulk prompt and is cue-exempt", BULK_COPYABLE,
                 agent_id="agent0001", expected="cue-exempt"),
        run_other_rule_unchanged(),
        run_torn_parent_state(),
        run_nudge_agrees(),
    ]
    failures = [(index + 1, reason) for index, (ok, reason) in enumerate(checks) if not ok]
    print("\n%d/%d cases pass" % (len(checks) - len(failures), len(checks)))
    for index, reason in failures:
        print("  FAIL case %d: %s" % (index, reason))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
