"""Proof cases for hooks/dispatch_table.py — the user-facing dispatch pin table.

Launch (PostToolUse on Workflow | Agent | sidecar Bash) prints requested model / effort / agentType per
job; completion (Stop sweep) prints only contradictions (served != requested) and late-started agents. Every case feeds a real hook
payload on stdin against a planted session dir, ledger and state dir, and asserts the ONLY channel is
`systemMessage` (user-visible, zero model context). Negatives are the point: a Bash line that only
mentions a launcher, a computed Workflow pin, an Agent call with no model.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(CLAUDE_DIR)
HOOK = os.path.join(CLAUDE_DIR, "hooks", "dispatch_table.py")
SID = "dtproof1-0000-0000-0000-000000000000"


class Env:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="dispatch_table_")
        self.state = os.path.join(self.root, "state")
        self.session = os.path.join(self.root, "project", SID)
        self.ledger = os.path.join(self.root, "ledger.jsonl")
        os.makedirs(self.state)
        os.makedirs(self.session)
        self.transcript = self.session + ".jsonl"
        open(self.transcript, "w").close()

    def run(self, payload, mode, projects_root=None):
        payload = dict(payload)
        payload.setdefault("session_id", SID)
        payload.setdefault("transcript_path", self.transcript)
        payload.setdefault("cwd", PROJECT)
        out = subprocess.run([sys.executable, HOOK, mode], input=json.dumps(payload), capture_output=True,
                             text=True, timeout=30, encoding="utf-8",
                             env=hook_env(HARNESS_HOOK_STATE_DIR=self.state, SIDECAR_LEDGER_PATH=self.ledger,
                                          CLAUDE_PROJECTS_ROOT=projects_root or os.path.join(self.root, "none"),
                                          DISPATCH_TABLE_LAUNCH_WAIT="0", DISPATCH_TABLE_UNRESOLVED_WAIT="0"))
        assert out.returncode == 0 and "Traceback" not in out.stderr, "CRASH rc=%s %s" % (out.returncode, out.stderr)
        body = json.loads(out.stdout) if out.stdout.strip() else {}
        assert set(body) <= {"systemMessage"}, "model-facing channel emitted: %s" % sorted(body)
        return body.get("systemMessage", "")

    def pending(self):
        path = os.path.join(self.state, SID[:8] + ".json")
        if not os.path.exists(path):
            return []
        return json.load(open(path, encoding="utf-8")).get("dispatch_table_pending") or []

    def plant_run(self, run_id, status, agents):
        """agents: [(label, agentId, requested_model, served_model, recorded_effort)]"""
        os.makedirs(os.path.join(self.session, "workflows"), exist_ok=True)
        run_dir = os.path.join(self.session, "subagents", "workflows", run_id)
        os.makedirs(run_dir, exist_ok=True)
        progress = []
        with open(os.path.join(run_dir, "journal.jsonl"), "a", encoding="utf-8") as fh:
            for a in agents:
                fh.write(json.dumps({"type": "started", "agentId": a[1], "label": a[0]}) + "\n")
        for i, spec in enumerate(agents):
            label, aid, req, served, eff = spec[:5]
            agent_type = spec[5] if len(spec) > 5 else "general-purpose"
            with open(os.path.join(run_dir, "agent-%s.meta.json" % aid), "w", encoding="utf-8") as fh:
                json.dump({"agentType": agent_type, "description": label, "model": req}, fh)
            progress.append({"type": "workflow_agent", "index": i + 1, "label": label, "agentId": aid,
                             "model": req, "state": "done", "startedAt": 1790000000000})
            with open(os.path.join(run_dir, "agent-%s.jsonl" % aid), "w", encoding="utf-8") as fh:
                rec = {"type": "assistant", "timestamp": "2026-09-22T10:00:00Z", "uuid": "u-" + aid,
                       "message": {"id": "m-" + aid, "model": served, "usage": {}, "content": []}}
                if eff:
                    rec["effort"] = eff
                if served:  # an agent that has not responded yet has no assistant record
                    fh.write(json.dumps(rec) + "\n")
        journal = {"runId": run_id, "taskId": "t-" + run_id, "status": status, "workflowName": "probe",
                   "logs": [], "script": "", "workflowProgress": progress}
        if status != "running":  # Claude Code writes the per-run journal only when the run ends
            with open(os.path.join(self.session, "workflows", run_id + ".json"), "w", encoding="utf-8") as fh:
                json.dump(journal, fh)


def _row(msg, label):
    """The LAST table row whose first column is `label` ('' when absent): the completion row when a
    message carries both the launch and the completion table."""
    found = ""
    for line in msg.splitlines():
        if line.strip().startswith(label + " ") or line.strip() == label:
            found = line
    return found


def cells(msg, label):
    """[label, model, effort, type] for `label` from its group line `  model · effort · type: a, b`
    ([] when absent). The LAST match wins, so a message carrying two tables yields the later one."""
    found = []
    for line in msg.splitlines():
        if not line.startswith("  ") or ": " not in line:
            continue
        pin, labels = line.strip().split(": ", 1)
        if label in labels.split(", "):
            found = [label] + pin.split(" · ")
    return found


def agent_payload(tool_input, effort="medium", response=None):
    p = {"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": tool_input,
         "tool_response": response or {"status": "async_launched", "agentId": "a0001", "resolvedModel": "claude-sonnet-5"}}
    if effort:
        p["effort"] = {"level": effort}
    return p


def workflow_payload(tool_input, run_id="wf_proof-1"):
    return {"hook_event_name": "PostToolUse", "tool_name": "Workflow", "tool_input": tool_input,
            "effort": {"level": "high"},
            "tool_response": {"status": "async_launched", "runId": run_id, "taskId": "t-" + run_id}}


def bash_payload(command):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}


def stop_payload(background=()):
    return {"hook_event_name": "Stop", "stop_hook_active": False, "effort": {"level": "medium"},
            "background_tasks": list(background)}


# ---------------------------------------------------------------- Agent launch

def test_agent_no_model_prints_inherited_effort_level():
    e = Env()
    msg = e.run(agent_payload({"description": "doc lookup", "prompt": "x", "subagent_type": "Explore"}), "--launch")
    assert cells(msg, "doc lookup") == ["doc lookup", "sonnet-5", "medium", "Explore"], msg


def test_agent_without_effort_field_says_unknown():
    e = Env()
    msg = e.run(agent_payload({"description": "d", "prompt": "x"}, effort=None), "--launch")
    assert cells(msg, "d") == ["d", "sonnet-5", "?", "general-purpose"], msg


def test_agent_launch_shows_resolved_model():
    e = Env()
    msg = e.run(agent_payload({"description": "rm", "prompt": "x", "model": "sonnet"}), "--launch")
    assert cells(msg, "rm") == ["rm", "sonnet-5", "medium", "general-purpose"], msg


def test_haiku_effort_mismatch_row_says_none():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "hz", "model": "opus", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("hz", "hz1", "opus", "claude-haiku-4-5", None)])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ hz ran haiku-4-5 · none, asked opus · low", msg


def test_agent_with_model_prints_it():
    e = Env()
    msg = e.run(agent_payload({"description": "d", "prompt": "x", "model": "sonnet"}), "--launch")
    assert cells(msg, "d") == ["d", "sonnet-5", "medium", "general-purpose"], msg


def test_foreground_agent_prints_served_row_at_launch():
    e = Env()
    os.makedirs(os.path.join(e.session, "subagents"))
    with open(os.path.join(e.session, "subagents", "agent-a0002.jsonl"), "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium",
                             "message": {"id": "m1", "model": "claude-haiku-4-5", "content": []}}) + "\n")
    msg = e.run(agent_payload({"description": "fg", "prompt": "x", "model": "haiku"},
                               response={"status": "completed", "agentId": "a0002", "resolvedModel": "claude-haiku-4-5"}),
                "--launch")
    assert cells(msg, "fg") == ["fg", "haiku-4-5", "medium", "general-purpose"] and "⚠" not in msg, msg
    assert "finished" not in msg, "a matching finish must stay silent: " + msg
    assert e.pending() == [], e.pending()


def _agent_transcript(e, aid, stop_reason):
    os.makedirs(os.path.join(e.session, "subagents"), exist_ok=True)
    with open(os.path.join(e.session, "subagents", "agent-%s.jsonl" % aid), "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium",
                             "message": {"id": "m1", "model": "claude-haiku-4-5", "stop_reason": stop_reason,
                                         "content": []}}) + "\n")


def test_background_agent_running_at_launch_stays_pending():
    """Live replay 2026-09-22: a background agent's transcript exists at launch; that is not completion."""
    e = Env()
    _agent_transcript(e, "a0001", "tool_use")
    msg = e.run(agent_payload({"description": "bg", "prompt": "x", "model": "haiku"}), "--launch")
    assert "served" not in msg and len(e.pending()) == 1, (msg, e.pending())
    assert e.run(stop_payload(), "--complete") == "" and len(e.pending()) == 1, "mid-run transcript read as done"
    _agent_transcript(e, "a0001", "end_turn")
    msg = e.run(stop_payload(), "--complete")
    assert msg == "" and e.pending() == [], (msg, e.pending())


# ---------------------------------------------------------------- Workflow launch

def test_review_fanout_omitted_effort_is_engine_default():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/review_fanout.js",
                                  "args": {"agents": [{"key": "k1", "model": "opus", "effort": "low"}, {"key": "k2"}]}}),
                "--launch")
    assert msg.splitlines()[0] == "▶ review_fanout · 2 agents", msg
    assert cells(msg, "k1") == ["k1", "opus-5-5", "low", "general-purpose"], msg
    assert cells(msg, "k2") == ["k2", "sonnet-5", "medium", "general-purpose"], msg


def test_dispatch_jobs_rows():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "j1", "model": "haiku", "effort": "low", "agentType": "Explore"}]}}),
                "--launch")
    assert cells(msg, "j1") == ["j1", "haiku-4-5", "none", "Explore"], msg


def test_inline_script_literal_pins():
    e = Env()
    script = "export const meta = {name:'p'}\nconst r = await agent('go', {label: 'x1', model: 'opus', effort: 'high', agentType: 'Explore'})"
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg == "▶ Workflow · 0 started — agents listed as they start", msg


def test_workflow_versions_come_from_the_journal():
    """Claude Code's own per-agent resolution: `opus` ran as claude-opus-5-5 in this session."""
    e = Env()
    e.plant_run("wf_proof-1", "running", [("review:v1", "va1", "opus", "claude-opus-5-5", "low", "Explore")])
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "v1", "model": "opus", "effort": "low", "agentType": "Explore"}]}}), "--launch")
    assert cells(msg, "review:v1") == ["review:v1", "opus-5-5", "low", "Explore"], msg
    assert cells(msg, "v1") == [], "a started job must not print twice: " + msg


def test_model_without_effort_param_shows_none_at_launch_never_pending():
    """Live 2026-09-22: haiku agents printed `pending` — haiku takes no effort setting (registry
    effortParam: false), so its effort is `none` from the moment it starts."""
    e = Env()
    e.plant_run("wf_proof-1", "running", [("hk%d" % i, "hk%d" % i, "haiku", None, None, "Explore") for i in range(2)])
    msg = e.run(workflow_payload({"script": "export const meta={name:'x'}"}), "--launch")
    assert cells(msg, "hk0") == ["hk0", "haiku-4-5", "none", "Explore"] and "pending" not in msg, msg


def test_haiku_agent_and_sidecar_show_none_not_the_pin():
    e = Env()
    msg = e.run(agent_payload({"description": "hA", "prompt": "x", "model": "haiku"},
                              response={"status": "async_launched", "agentId": "h1", "resolvedModel": "claude-haiku-4-5"}), "--launch")
    assert cells(msg, "hA") == ["hA", "haiku-4-5", "none", "general-purpose"], msg
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m haiku -e low -l hS -f p.md"), "--launch")
    assert cells(msg, "hS") == ["hS", "haiku-4-5", "none", "sidecar"], msg


def test_template_labels_take_the_scripts_only_literal_effort_and_one_answer_versions_all():
    """Live 2026-09-22 (bench-wave1-audit): 7 agents from one agent() call with template labels and
    `effort: 'low'`; 1 had answered, so the table read `opus-5-5 · low` for it and `opus · pending`
    for the other 6. The declared effort and the pin's resolved version apply to every agent."""
    e = Env()
    labels = ["audit:%s:%s" % (p, l) for p in "ADF" for l in ("robustness", "design")] + ["audit:E:instruments"]
    e.plant_run("wf_proof-1", "running", [(labels[0], "au0", "opus", "claude-opus-5-5", "low")] +
                [(lb, "au%d" % i, "opus", None, None) for i, lb in enumerate(labels[1:], 1)])
    script = ("export const meta={name:'bench-wave1-audit'}\n"
              "const out = await parallel(JOBS.map(j => () => agent(j.prompt,\n"
              "  { label: `audit:${j.part}:${j.lens}`, model: 'opus', effort: 'low', agentType: 'general-purpose' })))")
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg == "▶ Workflow · 7 started\n  opus-5-5 · low · general-purpose: " + ", ".join(labels), msg


def test_table_driven_efforts_never_print_pending():
    """Live 2026-09-23 (bench-wave3-fix-and-k-audit): labels built as `'audit:K:' + l.lens` with
    effort read from a LENSES table; two agents had not answered, and their rows read `pending`.
    An unanswered agent without an exact pin shows the efforts the script declares."""
    e = Env()
    e.plant_run("wf_proof-1", "running", [("fix:C7", "fx7", "opus", "claude-opus-5-5", "medium"),
                                          ("audit:K:doctrine", "akd", "opus", None, None),
                                          ("fix:C9", "fx9", "opus", None, None)])
    script = ("export const meta={name:'bench-wave3'}\n"
              "const LENSES = [{ lens: 'robustness', effort: 'low' }, { lens: 'claims', effort: 'medium' }]\n"
              "await parallel(LENSES.map(l => () => agent('x', { label: 'audit:K:' + l.lens, model: 'opus', effort: l.effort })))\n"
              "await parallel(FIXES.map(j => () => agent('x', { label: j.label, model: 'opus', effort: 'medium' })))")
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert "pending" not in msg, msg
    assert cells(msg, "fix:C9")[2] == "low|medium" and cells(msg, "audit:K:doctrine")[2] == "low|medium", msg
    assert cells(msg, "fix:C7")[1:3] == ["opus-5-5", "medium"], msg
    # Once the late agents answer, the next Stop reports their recorded effort.
    e.plant_run("wf_proof-1", "running", [("audit:K:doctrine", "akd", "opus", "claude-opus-5-5", "low"),
                                          ("fix:C9", "fx9", "opus", "claude-opus-5-5", "medium")])
    msg = e.run(stop_payload([{"id": "t-wf_proof-1", "type": "workflow", "status": "running"}]), "--complete")
    assert cells(msg, "audit:K:doctrine")[2] == "low" and cells(msg, "fix:C9")[2] == "medium", msg


def test_bespoke_script_lists_every_started_agent_then_the_late_ones():
    """Regression, live 2026-09-22: a bespoke script with 2 agent() call sites started 6 agents;
    the launch table printed the 2 call sites as agents, labels 'unresolved', one effort '?'."""
    e = Env()
    early = [("exec:%s" % p, "ea%d" % i, "opus", "claude-opus-5-5", "medium") for i, p in enumerate("ADEF")]
    late = [("plc:r2a", "eb1", "opus", None, None), ("plc:r2b", "eb2", "opus", None, None)]
    e.plant_run("wf_proof-1", "running", early + late)
    script = "export const meta={name:'bench-wave1'}\nfor (const p of parts) await agent(p.prompt, {label: p.label, model: 'opus', effort: p.eff})"
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg.splitlines()[0] == "▶ Workflow · 6 started", msg
    assert [cells(msg, "exec:%s" % p) for p in "ADEF"] == [["exec:%s" % p, "opus-5-5", "medium", "general-purpose"] for p in "ADEF"], msg
    assert cells(msg, "plc:r2a") == ["plc:r2a", "opus-5-5", "?", "general-purpose"], msg
    assert msg == ("▶ Workflow · 6 started\n"
                   "  opus-5-5 · medium · general-purpose: exec:A, exec:D, exec:E, exec:F\n"
                   "  opus-5-5 · ? · general-purpose: plc:r2a, plc:r2b"), msg
    assert e.run(stop_payload(), "--complete") == "", "nothing new until the unsettled agents respond"
    e.plant_run("wf_proof-1", "running", [("plc:r2a", "eb1", "opus", "claude-opus-5-5", "low"),
                                         ("plc:r2b", "eb2", "opus", "claude-opus-5-5", "low")])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "▶ Workflow · update\n  opus-5-5 · low · general-purpose: plc:r2a, plc:r2b", msg
    assert cells(msg, "plc:r2a") == ["plc:r2a", "opus-5-5", "low", "general-purpose"], msg
    assert e.run(stop_payload(), "--complete") == "", "reported agents must not repeat"


def test_long_model_ids_are_shortened():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "l", "model": "claude-opus-5-5[1m]", "effort": "low"}]}}), "--launch")
    assert cells(msg, "l") == ["l", "opus-5-5", "low", "general-purpose"], msg


def test_inline_script_computed_pins_are_unresolved():
    e = Env()
    script = "export const meta = {name:'p'}\nawait agent(p, {label: 'y1', model: pick(role), effort: eff})"
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg == "▶ Workflow · 0 started — agents listed as they start", msg


def test_workflow_launch_registers_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "j1", "model": "haiku", "effort": "low"}]}}), "--launch")
    p = e.pending()
    assert len(p) == 1 and p[0]["kind"] == "workflow" and p[0]["id"] == "wf_proof-1" and p[0]["task_id"] == "t-wf_proof-1", p


# ---------------------------------------------------------------- sidecar launch

def test_sidecar_launch_row():
    e = Env()
    msg = e.run(bash_payload("bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l sc1 -R r.json -f p.md"), "--launch")
    assert msg.splitlines()[0] == "▶ deepseek sidecar", msg
    assert cells(msg, "sc1") == ["sc1", "DeepSeek-V4.1-Flash", "low", "sidecar"], msg


def test_sidecar_env_prefix_and_redirect_default_model():
    e = Env()
    msg = e.run(bash_payload("FOO=1 bash .claude/scripts/deepseek_sidecar.sh -e max -f p.md 2>/dev/null"), "--launch")
    assert cells(msg, "(unlabeled)") == ["(unlabeled)", "default", "max", "sidecar"], msg


def test_sidecar_fanout_rows():
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "fa", "alias": "muse", "effort": "max", "promptFile": "a.md"},
               {"label": "fb", "alias": "flash", "promptFile": "b.md"}], open(jobs, "w"))
    msg = e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/")), "--launch")
    assert cells(msg, "fa") == ["fa", "muse-spark-1.3-contributor-free", "max", "sidecar"], msg
    assert cells(msg, "fb") == ["fb", "DeepSeek-V4.1-Flash", "default", "sidecar"], msg


def test_sidecar_fanout_follows_a_leading_cd():
    """A relative jobs path resolves against the directory the command cd's into, not the
    session's cwd, which may be any subdirectory."""
    e = Env()
    os.makedirs(os.path.join(e.root, "sub", "deep"))
    os.makedirs(os.path.join(e.root, "rel"))
    json.dump([{"label": "fc", "alias": "muse", "effort": "max", "promptFile": "c.md"}],
              open(os.path.join(e.root, "rel", "jobs.json"), "w"))
    payload = bash_payload("cd %s && python3 .claude/tools/sidecar_fanout.py rel/jobs.json --out-dir o"
                           % e.root.replace("\\", "/"))
    payload["cwd"] = os.path.join(e.root, "sub", "deep")
    msg = e.run(payload, "--launch")
    assert cells(msg, "fc") == ["fc", "muse-spark-1.3-contributor-free", "max", "sidecar"], msg


def test_sidecar_fanout_expands_a_shell_variable_assigned_earlier():
    """`P=<dir>; python3 sidecar_fanout.py $P/jobs.json` names the jobs file through a variable the
    same command assigned; the table resolves it rather than printing unresolved."""
    e = Env()
    os.makedirs(os.path.join(e.root, "p"))
    json.dump([{"label": "fv", "alias": "muse", "effort": "max", "promptFile": "v.md"}],
              open(os.path.join(e.root, "p", "jobs.json"), "w"))
    root = e.root.replace("\\", "/")
    for cmd in ("cd %s && P=p; python3 .claude/tools/sidecar_fanout.py $P/jobs.json" % root,
                "cd %s && P=p && python3 .claude/tools/sidecar_fanout.py ${P}/jobs.json" % root):
        msg = e.run(bash_payload(cmd), "--launch")
        assert cells(msg, "fv") == ["fv", "muse-spark-1.3-contributor-free", "max", "sidecar"], (cmd, msg)


def test_sidecar_fanout_dry_run_prints_nothing():
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "fd", "alias": "muse", "effort": "max", "promptFile": "d.md"}], open(jobs, "w"))
    cmd = "python3 .claude/tools/sidecar_fanout.py %s --dry-run" % jobs.replace("\\", "/")
    assert e.run(bash_payload(cmd), "--launch") == "", cmd


def test_sidecar_mentions_print_nothing():
    e = Env()
    for cmd in ("grep -n x .claude/scripts/deepseek_sidecar.sh",
                "cat .claude/scripts/codex_proxy_sidecar.sh",
                "ls .claude/scripts/*_sidecar.sh",
                "echo bash .claude/scripts/deepseek_sidecar.sh -m flash",
                "bash other/foo_sidecar.sh -m flash"):
        assert e.run(bash_payload(cmd), "--launch") == "", cmd
    assert e.pending() == []


# ---------------------------------------------------------------- completion sweep

def test_stop_with_nothing_pending_is_silent():
    assert Env().run(stop_payload(), "--complete") == ""


def test_stop_prints_served_rows_and_flags_mismatch():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "ok1", "model": "haiku", "effort": "low"},
                                              {"label": "bad1", "model": "opus", "effort": "high"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("ok1", "aa1", "haiku", "claude-haiku-4-5", "low"),
                                           ("bad1", "aa2", "opus", "claude-sonnet-5", "medium")])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ bad1 ran sonnet-5 · medium, asked opus · high", msg
    assert e.pending() == []


def test_stop_missing_recorded_effort_says_not_recorded():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "ne", "model": "haiku", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("ne", "ab1", "haiku", "claude-haiku-4-5", None)])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "", "a model that takes no effort is not a mismatch: " + msg


def test_stop_running_workflow_stays_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "j1", "model": "haiku", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "running", [("j1", "ac1", "haiku", "claude-haiku-4-5", "low")])
    msg = e.run(stop_payload([{"id": "t-wf_proof-1", "type": "workflow", "status": "running"}]), "--complete")
    assert msg == "" and len(e.pending()) == 1, (msg, e.pending())


def test_stop_sidecar_record_served_and_codex_not_recorded():
    e = Env()
    os.makedirs(os.path.join(e.root, "rec"))
    r1 = os.path.join(e.root, "rec", "a.json").replace("\\", "/")
    r2 = os.path.join(e.root, "rec", "b.json").replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l sa -R %s -f p.md" % r1), "--launch")
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -m luna -e max -l sb -R %s -f p.md" % r2), "--launch")
    json.dump({"servedModel": "deepseek-v4.1-flash", "requestedModel": "flash", "effort": "low", "label": "sa"}, open(r1, "w"))
    json.dump({"servedModel": None, "requestedModel": "gpt-5.6-luna", "effort": "max", "label": "sb", "transport": "codex"}, open(r2, "w"))
    msg = e.run(stop_payload(), "--complete")
    sa, sb = _row(msg, "sa"), _row(msg, "sb")
    assert msg == "" and sa == "" and sb == "", "unreported is not a mismatch: " + msg
    assert e.pending() == []


def test_codex_attested_model_and_effort_are_shown():
    e = Env()
    rec = os.path.join(e.root, "cx.json").replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -m sol -e high -l cx -R %s -f p.md" % rec), "--launch")
    json.dump({"servedModel": None, "attestedModel": "gpt-5.6-sol", "attestedEffort": "xhigh",
               "requestedModel": "gpt-5.6-sol", "effort": "high", "label": "cx", "transport": "codex"}, open(rec, "w"))
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ cx ran gpt-5.6-sol · xhigh, asked sol · high", msg


def test_proxied_child_transcript_is_not_evidence():
    """Live 2026-09-22: a codex run that lost its attestation printed '⚠ ran gpt-5.6-luna · medium,
    asked luna · low' — `medium` was the proxied Claude Code child's own setting, not what codex
    served. Only the native anthropic transport's child transcript is served evidence."""
    e = Env()
    rec = os.path.join(e.root, "lx.json").replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -m luna -e low -l lx -R %s -f p.md" % rec), "--launch")
    child = os.path.join(e.root, "project", "child-sid-2.jsonl")
    with open(child, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"id": "m", "model": "gpt-5.6-luna", "content": []}}) + "\n")
    json.dump({"servedModel": None, "requestedModel": "gpt-5.6-luna", "effort": "low", "label": "lx",
               "transport": "codex", "sessionId": "child-sid-2"}, open(rec, "w"))
    msg = e.run(stop_payload(), "--complete", projects_root=os.path.dirname(os.path.dirname(child)))
    assert msg == "", "a proxied child's own effort is not a contradiction: " + msg


def test_sidecar_child_transcript_effort_is_read():
    e = Env()
    rec = os.path.join(e.root, "an.json").replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -l an -R %s -f p.md" % rec), "--launch")
    child = os.path.join(e.root, "project", "child-sid-1.jsonl")
    with open(child, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"id": "m", "model": "claude-sonnet-5", "content": []}}) + "\n")
    json.dump({"servedModel": "claude-sonnet-5", "requestedModel": "sonnet", "effort": "low", "label": "an",
               "sessionId": "child-sid-1"}, open(rec, "w"))
    msg = e.run(stop_payload(), "--complete", projects_root=os.path.dirname(os.path.dirname(child)))
    assert msg == "⚠ an ran sonnet-5 · medium, asked sonnet · low", msg


def test_stale_entry_reported_and_dropped():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "old", "model": "haiku", "effort": "low"}]}}, run_id="wf_old"), "--launch")
    path = os.path.join(e.state, SID[:8] + ".json")
    state = json.load(open(path))
    state["dispatch_table_pending"][0]["launched_at"] = time.time() - 25 * 3600
    json.dump(state, open(path, "w"))
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ workflow wf_old: no completion after 24 h" and e.pending() == [], (msg, e.pending())


# ---------------------------------------------------------------- session-audit regressions (2026-09-23)

def test_chain_and_lens_omitted_effort_is_the_engine_default_not_the_session_level():
    """dispatch_chains and a lens engine default an omitted effort to medium, as review_fanout does;
    the session's `high` there printed a false launch row and a false ⚠ at finish."""
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch_chains.js",
                                  "args": {"chains": [{"name": "c", "jobs": [{"label": "cj", "model": "sonnet"}]}]}}), "--launch")
    assert cells(msg, "cj") == ["cj", "sonnet-5", "medium", "general-purpose"], msg
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/any_lens_engine.js",
                                  "args": {"lenses": [{"key": "lz", "model": "sonnet", "agentType": "Explore"}]}},
                                 run_id="wf_proof-2"), "--launch")
    assert cells(msg, "lz") == ["lz", "sonnet-5", "medium", "Explore"], msg


def test_reused_sidecar_label_is_not_judged_by_the_previous_runs_ledger_row():
    e = Env()
    with open(e.ledger, "w") as fh:
        fh.write(json.dumps({"parentSessionId": SID, "label": "re", "timestamp": "2026-01-01T00:00:00Z",
                             "requestedModel": "sonnet", "servedModel": "claude-haiku-4-5", "effort": "low"}) + "\n")
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -l re -f p.md"), "--launch")
    assert "⚠" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_unlabeled_sidecar_without_record_prints_its_row_but_is_not_tracked():
    e = Env()
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -f p.md"), "--launch")
    assert cells(msg, "(unlabeled)") == ["(unlabeled)", "sonnet-5", "low", "sidecar"], msg
    assert e.pending() == [], e.pending()


def test_run_file_still_being_written_stays_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "pw", "model": "haiku", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("pw", "pw1", "haiku", "claude-haiku-4-5", None)])
    with open(os.path.join(e.session, "workflows", "wf_proof-1.json"), "w") as fh:
        fh.write('{"runId": "wf_proof-1", "sta')
    msg = e.run(stop_payload(), "--complete")
    assert "unreadable" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_background_agent_that_died_without_end_turn_finishes_once_gone_and_quiet():
    e = Env()
    _agent_transcript(e, "a0001", "tool_use")
    e.run(agent_payload({"description": "dead", "prompt": "x", "model": "haiku"}), "--launch")
    path = os.path.join(e.session, "subagents", "agent-a0001.jsonl")
    assert e.run(stop_payload([{"id": "a0001", "type": "subagent"}]), "--complete") == "" and len(e.pending()) == 1
    old = time.time() - 600
    os.utime(path, (old, old))
    assert e.run(stop_payload([{"id": "a0001", "type": "subagent"}]), "--complete") == "" and len(e.pending()) == 1, \
        "still listed as running"
    msg = e.run(stop_payload(), "--complete")
    assert msg == "" and e.pending() == [], (msg, e.pending())



def test_unresolvable_pins_still_print_the_launch_table():
    """`inherit`, `?` and a typo reach the registry lookup; an UnknownModel must never cost the table."""
    e = Env()
    os.makedirs(os.path.join(e.session, "subagents"))
    with open(os.path.join(e.session, "subagents", "agent-a0003.jsonl"), "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "low",
                             "message": {"id": "m1", "model": "claude-sonnet-5", "stop_reason": "end_turn",
                                         "content": []}}) + "\n")
    msg = e.run(agent_payload({"description": "inh", "prompt": "x"},
                              response={"status": "completed", "agentId": "a0003"}), "--launch")
    assert cells(msg, "inh") == ["inh", "sonnet-5", "low", "general-purpose"], msg
    script = "export const meta={name:'q'}\nawait agent(p, {label: 'q1', model: pick(r), effort: 'low'})"
    e.plant_run("wf_proof-1", "running", [("q1", "q1a", "?", None, None)])
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert cells(msg, "q1") == ["q1", "?", "low", "general-purpose"], msg
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m opsu -e low -l ty -f p.md"), "--launch")
    assert cells(msg, "ty") == ["ty", "opsu", "low", "sidecar"], msg

if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001 - proof runner reports every case
                fails += 1
                print("FAIL", name, "-", str(exc)[:300])
    sys.exit(1 if fails else 0)
