#!/usr/bin/env python3
"""Re-runnable proof for the subagent exemption in hooks/routing_audit.py.

Drives `routing_audit.process` in-process with HARNESS_HOOK_STATE_DIR and
HARNESS_ROUTING_AUDIT_LOG_PATH redirected to a temp dir, then reads the appended
JSONL row to assert on the emitted `classification`/`rule`/`reason`.

    python3 .claude/tests/test_routing_audit_subagent_exempt.py
"""
import importlib
import json
import os
import sys
import tempfile

HOOKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, HOOKS_DIR)

# Synthesis-shaped Obsidian path that classify_call flags nudge-warranted
# under the "native-read-synthesis-doc" rule with a prompt carrying no
# audit/edit-intent cue.
SYNTHESIS_PATH = (
    "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/"
    "Design/some_design_doc.md"
)


def _fresh_routing_audit(state_dir, log_path):
    os.environ["HARNESS_HOOK_STATE_DIR"] = state_dir
    os.environ["HARNESS_ROUTING_AUDIT_LOG_PATH"] = log_path
    if "routing_audit" in sys.modules:
        del sys.modules["routing_audit"]
    return importlib.import_module("routing_audit")


def _read_last_row(log_path):
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def run(label, agent_id, expected_classification, expected_rule_present):
    tmp = tempfile.mkdtemp(prefix="routing_audit_proof_")
    state_dir = os.path.join(tmp, "state")
    log_path = os.path.join(tmp, "routing_audit.jsonl")
    module = _fresh_routing_audit(state_dir, log_path)

    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": SYNTHESIS_PATH},
        "session_id": "sess0001",
        "agent_id": agent_id,
    }
    module.process(payload)

    ok = True
    reason = ""
    if not os.path.exists(log_path):
        ok, reason = False, "no audit log written"
    else:
        row = _read_last_row(log_path)
        if row.get("classification") != expected_classification:
            ok = False
            reason = "classification=%r want=%r" % (row.get("classification"), expected_classification)
        elif expected_rule_present and row.get("rule") != "native-read-synthesis-doc":
            ok = False
            reason = "rule=%r" % row.get("rule")
        elif expected_classification == "cue-exempt" and "[subagent: bundling delegate]" not in (row.get("reason") or ""):
            ok = False
            reason = "reason missing subagent tag: %r" % row.get("reason")
        elif expected_classification == "nudge-warranted" and "[subagent: bundling delegate]" in (row.get("reason") or ""):
            ok = False
            reason = "nudge-warranted row unexpectedly tagged subagent"

    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def run_other_rule_unchanged():
    """A different rule with agent_id set must NOT be exempted."""
    tmp = tempfile.mkdtemp(prefix="routing_audit_proof_other_")
    state_dir = os.path.join(tmp, "state")
    log_path = os.path.join(tmp, "routing_audit.jsonl")
    module = _fresh_routing_audit(state_dir, log_path)

    # Bare-Grep of a PascalCase identifier on a .cs file -> a different
    # nudge-warranted rule ("pascal-grep-on-cs" family), not the synthesis-doc rule.
    payload = {
        "tool_name": "Grep",
        "tool_input": {"pattern": "DomainCore", "glob": "*.cs"},
        "session_id": "sess0002",
        "agent_id": "agent0001",
    }
    module.process(payload)

    label = "a different rule with agent_id set is unchanged (not cue-exempt via this exemption)"
    ok = True
    reason = ""
    # The control arm must have exposure: a payload that logs nothing proves nothing.
    if not os.path.exists(log_path):
        ok, reason = False, "control payload logged no audit row — the arm had no exposure"
    else:
        row = _read_last_row(log_path)
        if row.get("rule") == "native-read-synthesis-doc":
            ok, reason = False, "unexpectedly matched the synthesis-doc rule"
        elif row.get("classification") != "nudge-warranted":
            ok, reason = False, "control row classification=%r, want nudge-warranted" % row.get("classification")
        elif "[subagent: bundling delegate]" in (row.get("reason") or ""):
            ok, reason = False, "wrongly tagged subagent exemption on an unrelated rule"
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def run_nudge_agrees():
    """The exemption lives in the classifier, so the PreToolUse nudge and the audit log agree:
    the same subagent payload that logs cue-exempt must receive NO advisory, and the main-loop
    payload must still receive one."""
    tmp = tempfile.mkdtemp(prefix="routing_nudge_proof_")
    os.environ["HARNESS_HOOK_STATE_DIR"] = os.path.join(tmp, "state")
    if "tool_routing_nudge" in sys.modules:
        del sys.modules["tool_routing_nudge"]
    nudge = importlib.import_module("tool_routing_nudge")

    base = {"tool_name": "Read", "tool_input": {"file_path": SYNTHESIS_PATH}}
    _, main_msg = nudge.process(dict(base, session_id="sessnud1", agent_id=""))
    _, sub_msg = nudge.process(dict(base, session_id="sessnud2", agent_id="agent0001"))

    label = "same payload: main loop is nudged, subagent is not (classifier is the one home)"
    ok, reason = True, ""
    if not main_msg:
        ok, reason = False, "main-loop payload received no advisory — the control arm had no exposure"
    elif sub_msg:
        ok, reason = False, "subagent payload still received the advisory: %r" % sub_msg[:120]
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    return ok, reason


def main():
    failures = []

    ok, reason = run(
        "same payload WITHOUT agent_id -> classifier's verdict (nudge-warranted)",
        agent_id="",
        expected_classification="nudge-warranted",
        expected_rule_present=True,
    )
    if not ok:
        failures.append(("without agent_id", reason))

    ok, reason = run(
        "same payload WITH agent_id -> cue-exempt, subagent-tagged",
        agent_id="agent0001",
        expected_classification="cue-exempt",
        expected_rule_present=True,
    )
    if not ok:
        failures.append(("with agent_id", reason))

    ok, reason = run_other_rule_unchanged()
    if not ok:
        failures.append(("other rule unchanged", reason))

    ok, reason = run_nudge_agrees()
    if not ok:
        failures.append(("nudge agrees with the log", reason))

    total = 4
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for label, reason in failures:
        print("  FAIL %s: %s" % (label, reason))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
