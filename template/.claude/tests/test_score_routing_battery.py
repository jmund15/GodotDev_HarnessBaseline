#!/usr/bin/env python3
"""Proof for tools/score_routing_battery.py: the transcript signal (an agent id reads that
subagent's transcript, a session-only row reads the session's own transcript, a row with neither
reads nothing) and the E1/H1 shape grades (D7).

E1 grades a complete deterministic enumeration of the `.tres` population, H1 grades a qualified
investigation, and neither passes on a ranked search or a worker asked to find the bug. Fixtures
are planted below as the `tool_use` rows the scorer reads (name, input, cwd), so the proof carries
its own input and needs no per-project transcript file.

The transcript tree is planted under a temp login (`CLAUDE_CONFIG_DIR`); each case runs in a child
process so the extractor resolves its root from that environment.

    python3 .claude/tests/test_score_routing_battery.py
"""
import json
import os
import subprocess
import sys
import tempfile

TOOLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
ROOT = "C:/planted/project"
SID = "7b7b7b7b-0000-4000-8000-000000000003"
FSID = "7b7b7b7b-0000-4000-8000-000000000004"
AGENT = "a0a0a0a0a0a0a0a01"
failures = []


def ck(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "\n        " + str(detail)[-300:]))
    if not cond:
        failures.append(label)


def call(row, n):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": n, "input": {}}]}}


def row(name, tool_input, cwd=ROOT):
    return {"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": tool_input}]}}


FANOUT = "cd \"%s\" && python3 .claude/tools/sidecar_fanout.py " % ROOT
FIXTURES = {
    "routing_e1_complete.jsonl": [
        row("Grep", {"pattern": "SetupFreeze\\(|MaxIntegrity", "output_mode": "content", "glob": "*.cs", "head_limit": 120}),
        row("Grep", {"pattern": "freeze_duration", "output_mode": "files_with_matches", "glob": "*.tres"})],
    "routing_e1_ranked.jsonl": [
        row("mcp__plugin_semantic-search_semantic-search__search",
            {"query": "frozen body shatter deals damage", "searchDir": ROOT, "limit": 20})],
    "routing_h1_qualified.jsonl": [
        row("Read", {"file_path": ROOT + "/Core/StatusRunner.cs"}),
        row("Read", {"file_path": ROOT + "/Data/status_duration.tres"})],
    "routing_h1_worker.jsonl": [
        row("mcp__ai-worker__read_files", {
            "paths": [ROOT + "/Tools/Editor.cs", ROOT + "/Tools/Panel.cs"],
            "question": "I am diagnosing a crash; the root cause is my suspect. Return one section per file."},
            ROOT + "/Sub")],
    "routing_h1_skill_delegate.jsonl": [row("Skill", {"skill": "delegate"}, ROOT + "/Sub")],
    "routing_h1_fanout_launch.jsonl": [
        row("Bash", {"command": FANOUT + ".claude/scratch/jobs.json --detach"}, ROOT + "/Sub")],
    "routing_h1_fanout_help.jsonl": [
        row("Bash", {"command": FANOUT + "--help 2>&1 | head -30"}, ROOT + "/Sub")],
    "routing_unscorable.jsonl": [
        row("ToolSearch", {"query": "select:LSP", "max_results": 1}),
        row("LSP", {"operation": "workspaceSymbol", "filePath": ROOT + "/Inventory.cs", "query": "Inventory"})],
}


def score(login, info):
    code = ("import json, sys; sys.path.insert(0, %r); import score_routing_battery as s; "
            "print(json.dumps(s.extract_tools_used('x', %r)))" % (TOOLS, info))
    p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, CLAUDE_CONFIG_DIR=login),
                       capture_output=True, text=True, timeout=60)
    return p.returncode, json.loads(p.stdout or "null"), p.stderr


def grade(login, fixture, tid):
    """Plant a captured fixture as the session's transcript and return the row's grade."""
    dest = os.path.join(login, "projects", "C--planted-project", FSID + ".jsonl")
    with open(dest, "w", encoding="utf-8", newline="\n") as dst:
        dst.write("".join(json.dumps(r) + "\n" for r in FIXTURES[fixture]))
    code = ("import json, sys; sys.path.insert(0, %r); import score_routing_battery as s; "
            "print(json.dumps(s.score_one(%r, '', {'agent_id': None, 'session_id': %r})['grade']))"
            % (TOOLS, tid, FSID))
    p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, CLAUDE_CONFIG_DIR=login),
                       capture_output=True, text=True, timeout=60)
    return p.returncode, (p.stdout or "").strip(), p.stderr


def main():
    with tempfile.TemporaryDirectory() as login:
        proj = os.path.join(login, "projects", "C--planted-project")
        os.makedirs(os.path.join(proj, SID, "subagents"))
        os.makedirs(proj, exist_ok=True)
        with open(os.path.join(proj, SID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(call(None, "Grep")) + "\n")
        with open(os.path.join(proj, SID, "subagents", "agent-%s.jsonl" % AGENT), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(call(None, "Read")) + "\n")

        rc, out, err = score(login, {"agent_id": AGENT, "session_id": SID})
        ck("an agent id reads that subagent's transcript", rc == 0 and out == [["Read"], "transcript"], (rc, out, err))
        rc, out, err = score(login, {"agent_id": None, "session_id": SID})
        ck("a session-only row reads the session's own transcript", rc == 0 and out == [["Grep"], "transcript"], (rc, out, err))
        rc, out, err = score(login, {"agent_id": None, "session_id": None})
        ck("a row with neither id reads nothing", rc == 0 and out == [[], "none"], (rc, out, err))

        for fixture, tid, want in (
            ("routing_e1_complete.jsonl", "E1", "PASS"),
            ("routing_e1_ranked.jsonl", "E1", "FAIL"),
            ("routing_h1_qualified.jsonl", "H1", "PASS"),
            ("routing_h1_worker.jsonl", "H1", "FAIL"),
            ("routing_h1_skill_delegate.jsonl", "H1", "PASS"),
            ("routing_h1_fanout_launch.jsonl", "H1", "PASS"),
            ("routing_h1_fanout_help.jsonl", "H1", "REVIEW-REQUIRED"),
            ("routing_unscorable.jsonl", "E1", "REVIEW-REQUIRED"),
        ):
            rc, out, err = grade(login, fixture, tid)
            ck("%s grades %s" % (fixture, want), rc == 0 and out == json.dumps(want), (rc, out, err))
    print("test_score_routing_battery: %d failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
