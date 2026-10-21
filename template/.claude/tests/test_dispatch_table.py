"""Proof cases for hooks/dispatch_table.py — the user-facing dispatch pin table.

Launch (PostToolUse on Workflow | Agent | sidecar Bash) prints requested model / effort / agentType per
job; completion (Stop sweep) prints only contradictions (served != requested) and late-started agents,
and additionally BLOCKS (Stop `decision: block`) while a running sidecar has no live watcher. Every
case feeds a real hook payload on stdin against a planted session dir, ledger and state dir, and
asserts the emitted channel: `--launch` is `systemMessage` only (user-visible, zero model context),
`--complete` may add `decision`/`reason`. Negatives are the point: a Bash line that only mentions a
launcher, a computed Workflow pin, an Agent call with no model, a covered or Workflow-only pending set.
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
WATCH_COMMAND = "python3 .claude/tools/watch_records.py --session --wake"
sys.path.insert(0, os.path.join(CLAUDE_DIR, "tools"))
import model_registry
FLASH_VERSION = model_registry.resolve("flash")["version"]


def test_stop_verification_audit_contract():
    import test_dispatch_table_verification
    assert not test_dispatch_table_verification.run_tests(), "Stop verification audit proof failed"


def live_process():
    """A liveness line naming this proof process, in the watcher's identity format."""
    pid = os.getpid()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


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
        self.body = {}
        self.extra = {"BENCH_ROOTS": json.dumps({"BenchmarkArms": os.path.join(self.root, "arms")})}

    def state_path(self):
        return os.path.join(self.state, SID[:8] + ".json")

    def watch(self, records=(), session=True, pid=None):
        """Plant one watcher in this session's state: live unless `pid` names a dead process."""
        state = json.load(open(self.state_path(), encoding="utf-8")) if os.path.exists(self.state_path()) else {}
        watch = {"process": live_process() if pid is None else
                 "2026-01-01T00:00:00Z pid=%d" % pid, "records": [str(r) for r in records]}
        if session:
            watch["session"] = True
        state["sidecar_watchers"] = [watch]
        json.dump(state, open(self.state_path(), "w", encoding="utf-8"))

    def run(self, payload, mode, projects_root=None, issue=True):
        payload = dict(payload)
        payload.setdefault("session_id", SID)
        payload.setdefault("transcript_path", self.transcript)
        payload.setdefault("cwd", PROJECT)
        if issue and mode == "--launch" and payload.get("tool_name") == "Bash":
            self.issue(payload)
        out = subprocess.run([sys.executable, HOOK, mode], input=json.dumps(payload), capture_output=True,
                             text=True, timeout=30, encoding="utf-8",
                             env=hook_env(HARNESS_HOOK_STATE_DIR=self.state, SIDECAR_LEDGER_PATH=self.ledger,
                                          CLAUDE_PROJECTS_ROOT=projects_root or os.path.join(self.root, "none"),
                                          DISPATCH_TABLE_LAUNCH_WAIT="0", DISPATCH_TABLE_UNRESOLVED_WAIT="0",
                                          CC_CACHESTAT_DIR=self.root, **self.extra))
        assert out.returncode == 0 and "Traceback" not in out.stderr, "CRASH rc=%s %s" % (out.returncode, out.stderr)
        body = json.loads(out.stdout) if out.stdout.strip() else {}
        allowed = {"systemMessage"} if mode == "--launch" else {"systemMessage", "decision", "reason"}
        assert set(body) <= allowed, "unexpected channel emitted: %s" % sorted(body)
        self.body = body
        return body.get("systemMessage", "")

    def issue(self, payload):
        """The harness writes a call's tool_use row before any hook runs; plant it unless present."""
        call = payload.setdefault("tool_use_id", "toolu_auto_%d" % time.monotonic_ns())
        with open(self.transcript, encoding="utf-8") as fh:
            if call in fh.read():
                return
        _plant_call(self, call, time.time())

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


def bash_payload(command, tool_use_id=None):
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command},
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    if tool_use_id:
        payload["tool_use_id"] = tool_use_id
    return payload


def stop_payload(background=(), stop_hook_active=False):
    return {"hook_event_name": "Stop", "stop_hook_active": stop_hook_active, "effort": {"level": "medium"},
            "background_tasks": list(background)}


def test_launch_stores_writer_flag():
    for shape, expected in (("author", True), ("survey", False)):
        e = Env()
        e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                               "args": {"jobs": [{"label": "job", "model": "sonnet",
                                                  "effort": "medium", "shape": shape}]}}), "--launch")
        assert e.pending()[0].get("writes") is expected, e.pending()


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
    msg = e.run(agent_payload({"description": "fg", "prompt": "x", "model": "haiku45"},
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
    msg = e.run(agent_payload({"description": "bg", "prompt": "x", "model": "haiku45"}), "--launch")
    assert "served" not in msg and len(e.pending()) == 1, (msg, e.pending())
    msg = e.run(stop_payload(), "--complete")
    assert msg.startswith("⏳ still running · 1") and "bg" in msg and len(e.pending()) == 1, \
        "mid-run transcript read as done: " + msg
    _agent_transcript(e, "a0001", "end_turn")
    msg = e.run(stop_payload(), "--complete")
    assert msg == "" and e.pending() == [], (msg, e.pending())


# ---------------------------------------------------------------- Workflow launch

def test_review_fanout_omitted_pins_show_unknown():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/review_fanout.js",
                                  "args": {"agents": [{"key": "k1", "model": "opus", "effort": "low"}, {"key": "k2"}]}}),
                "--launch")
    assert msg.splitlines()[0] == "▶ review_fanout · 2 agents", msg
    assert cells(msg, "k1") == ["k1", "opus-5-5", "low", "general-purpose"], msg
    assert "high" not in msg, msg  # the session's level is never shown for a pin the engine will refuse


def test_dispatch_jobs_rows():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "j1", "model": "haiku45", "effort": "low", "agentType": "Explore"}]}}),
                "--launch")
    assert cells(msg, "j1") == ["j1", "haiku-4-5", "none", "Explore"], msg


def test_inline_script_literal_pins():
    """Live 2026-10-19 (cwe-review-fixes): an inline script pinned its one job in literal agent()
    options, its worktree agent had not started inside the launch wait, and the table printed only
    'agents listed as they start'. Literal pins are known at launch, so they print as rows."""
    e = Env()
    script = "export const meta = {name:'p'}\nconst r = await agent('go', {label: 'x1', model: 'opus', effort: 'high', agentType: 'Explore'})"
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg.splitlines()[0] == "▶ Workflow · 1 agent", msg
    assert cells(msg, "x1") == ["x1", "opus-5-5", "high", "Explore"], msg


def test_inline_script_job_table_literals():
    """A JOBS array of literal {label, model, effort} objects read through agent(j.prompt, {label:
    j.label, ...}) prints one row per job; a computed label or a template prompt before the pins
    adds none."""
    e = Env()
    script = ("export const meta = {name:'w'}\n"
              "const JOBS = [\n { label: 'a1', model: 'opus', effort: 'medium', prompt: `x ${C}` },\n"
              " { label: 'a2', model: 'sonnet', effort: 'high', prompt: `y` },\n]\n"
              "await parallel(JOBS.map(j => () => agent(j.prompt, { label: j.label, model: j.model, effort: j.effort })))\n"
              "await agent('z', { label: 'k:' + n, model: 'opus', effort: 'low' })")
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg.splitlines()[0] == "▶ Workflow · 2 agents", msg
    assert cells(msg, "a1")[1:3] == ["opus-5-5", "medium"] and cells(msg, "a2")[2] == "high", msg
    assert "k:" not in msg, msg


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
    e.plant_run("wf_proof-1", "running", [("hk%d" % i, "hk%d" % i, "haiku45", None, None, "Explore") for i in range(2)])
    msg = e.run(workflow_payload({"script": "export const meta={name:'x'}"}), "--launch")
    assert cells(msg, "hk0") == ["hk0", "haiku-4-5", "none", "Explore"] and "pending" not in msg, msg


def test_haiku_agent_and_sidecar_show_none_not_the_pin():
    e = Env()
    msg = e.run(agent_payload({"description": "hA", "prompt": "x", "model": "haiku45"},
                              response={"status": "async_launched", "agentId": "h1", "resolvedModel": "claude-haiku-4-5"}), "--launch")
    assert cells(msg, "hA") == ["hA", "haiku-4-5", "none", "general-purpose"], msg
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m haiku45 -e low -l hS -f p.md"), "--launch")
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
    msg = e.run(stop_payload(), "--complete")
    assert "▶" not in msg and _block(msg), "nothing new until the unsettled agents respond: " + msg
    e.plant_run("wf_proof-1", "running", [("plc:r2a", "eb1", "opus", "claude-opus-5-5", "low"),
                                         ("plc:r2b", "eb2", "opus", "claude-opus-5-5", "low")])
    msg = e.run(stop_payload(), "--complete")
    assert msg.split("\n\n")[0] == "▶ Workflow · update\n  opus-5-5 · low · general-purpose: plc:r2a, plc:r2b", msg
    assert cells(msg, "plc:r2a") == ["plc:r2a", "opus-5-5", "low", "general-purpose"], msg
    msg = e.run(stop_payload(), "--complete")
    assert "▶" not in msg and _block(msg), "reported agents must not repeat: " + msg


def test_long_model_ids_are_shortened():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "l", "model": "claude-opus-5-5[1m]", "effort": "low"}]}}), "--launch")
    assert cells(msg, "l") == ["l", "opus-5-5", "low", "general-purpose"], msg


def test_inline_script_computed_pins_are_unresolved():
    e = Env()
    script = "export const meta = {name:'p'}\nawait agent(p, {label: 'y1', model: pick(role), effort: eff})"
    msg = e.run(workflow_payload({"script": script}), "--launch")
    assert msg == "▶ Workflow · 0 started — the script computes its pins; each agent's row prints when it starts", msg


def test_workflow_launch_registers_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "j1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    p = e.pending()
    assert len(p) == 1 and p[0]["kind"] == "workflow" and p[0]["id"] == "wf_proof-1" and p[0]["task_id"] == "t-wf_proof-1", p


# ---------------------------------------------------------------- sidecar launch

def test_sidecar_launch_row():
    e = Env()
    msg = e.run(bash_payload("bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l sc1 -R r.json -f p.md"), "--launch")
    assert msg.splitlines()[0] == "▶ deepseek sidecar", msg
    assert cells(msg, "sc1") == ["sc1", FLASH_VERSION, "low", "sidecar"], msg


def test_sidecar_env_prefix_and_redirect_default_model():
    e = Env()
    msg = e.run(bash_payload("FOO=1 bash .claude/scripts/deepseek_sidecar.sh -e max -f p.md 2>/dev/null"), "--launch")
    assert cells(msg, "(unlabeled)") == ["(unlabeled)", "default", "max", "sidecar"], msg


def test_fanout_attempt_two_uses_inventory_record():
    e = Env()
    out_dir = os.path.join(e.root, "out")
    os.makedirs(out_dir)
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "retry", "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    first = os.path.join(out_dir, "retry.record.json")
    second = os.path.join(out_dir, "retry.attempt2.record.json")
    open(first + ".exit", "w").write("0")
    stale_inventory = os.path.join(out_dir, "fanout-stale.inventory.json")
    json.dump({"parentSessionId": SID, "jobs": [{"label": "retry", "recordPath": first}]},
              open(stale_inventory, "w"))
    old_time = time.time() - 60
    os.utime(stale_inventory, (old_time, old_time))
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s" %
                           (jobs.replace("\\", "/"), out_dir.replace("\\", "/")))
    e.run(payload, "--launch")
    assert e.pending()[0]["id"] != first, "fan-out identity reconstructed attempt 1"
    e.watch()
    before = e.run(stop_payload(), "--complete")
    assert "still running" in before and e.pending()[0]["record"] is None, (before, e.pending())
    json.dump({"parentSessionId": SID, "fanoutProcess": _pid_line(os.getpid()),
               "jobs": [{"label": "retry", "recordPath": second}]},
              open(os.path.join(out_dir, "fanout-run.inventory.json"), "w"))
    msg = e.run(stop_payload(), "--complete")
    assert "still running" in msg and len(e.pending()) == 1, (msg, e.pending())
    assert e.pending()[0]["record"] == second, e.pending()


def test_overlapping_fanouts_stay_two_entries_and_an_inventory_is_the_later_calls():
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "shared", "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    command = "python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/")
    e.run(bash_payload(command, "toolu_first"), "--launch")
    e.run(bash_payload(command, "toolu_second"), "--launch")
    assert len(e.pending()) == 2, "one fan-out replaced another pending run"
    out_dir = os.path.join(os.path.dirname(jobs), "fanout")
    os.makedirs(out_dir, exist_ok=True)
    record = os.path.join(out_dir, "shared.record.json")
    open(record + ".exit", "w").write("0")
    json.dump({"parentSessionId": SID, "jobs": [{"label": "shared", "recordPath": record}]},
              open(os.path.join(out_dir, "fanout-one.inventory.json"), "w"))
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    # The inventory was written after both calls were issued, so it is the later call's alone.
    left = e.pending()
    assert len(left) == 1 and left[0]["id"].endswith(":toolu_first") and left[0]["record"] is None, (msg, left)


def _fanout_pending(e, label, fanout_pid):
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": label, "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    command = "python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/")
    e.run(bash_payload(command), "--launch")
    entry = e.pending()[0]
    out_dir = entry["fanout_dir"]
    os.makedirs(out_dir, exist_ok=True)
    inventory = os.path.join(out_dir, "fanout-proof.inventory.json")
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key = "winpid=%d pid=%d" if os.name == "nt" else "pid=%d"
    line = stamp + " " + (key % (fanout_pid, fanout_pid) if os.name == "nt" else key % fanout_pid)
    json.dump({"parentSessionId": SID, "fanoutId": "proof-fanout", "fanoutProcess": line,
               "jobs": [{"label": label, "recordPath": os.path.join(out_dir, label + ".record.json")}]},
              open(inventory, "w"))
    return entry


def test_fanout_without_job_pid_and_dead_fanout_is_never_started_once():
    e = Env()
    entry = _fanout_pending(e, "queued-dead", _exited_pid())
    msg = e.run(stop_payload(), "--complete")
    expected = "queued-dead: never started (fan-out proof-fanout ended before launching it)"
    assert expected in msg, msg
    assert e.pending() == [], e.pending()
    assert expected not in e.run(stop_payload(), "--complete")


def test_fanout_without_job_pid_and_live_fanout_stays_queued():
    e = Env()
    _fanout_pending(e, "queued-live", os.getpid())
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "queued-live: never started" not in msg and "queued-live" in msg, msg
    assert len(e.pending()) == 1, e.pending()


def _refused_fanout(e, label, age, task_id=None):
    """A fan-out call that returned `age` seconds ago and wrote no inventory: refused in preflight."""
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": label, "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/")), "--launch")
    state = json.load(open(e.state_path(), encoding="utf-8"))
    for entry in state["dispatch_table_pending"]:
        entry["launched_at"] = entry["issued_at"] = time.time() - age
        entry["task_id"] = task_id
    json.dump(state, open(e.state_path(), "w"))
    os.makedirs(state["dispatch_table_pending"][0]["fanout_dir"], exist_ok=True)


def test_fanout_that_wrote_no_inventory_after_its_call_ended_is_never_started():
    e = Env()
    _refused_fanout(e, "refused", 700)
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "refused: never started" in msg and "still running" not in msg, msg
    assert e.pending() == [], e.pending()


def test_a_refused_call_whose_label_a_later_call_launched_is_dropped_without_a_never_started_line():
    e = Env()
    _refused_fanout(e, "again", 700)
    refused = e.pending()[0]
    later_jobs = os.path.join(e.root, "later", "jobs.json")
    os.makedirs(os.path.dirname(later_jobs))
    json.dump([{"label": "again", "alias": "muse", "promptFile": "a.md"}], open(later_jobs, "w"))
    e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s" % later_jobs.replace("\\", "/")), "--launch")
    later = [p for p in e.pending() if p["fanout_dir"] != refused["fanout_dir"]][0]
    os.makedirs(later["fanout_dir"], exist_ok=True)
    record = os.path.join(later["fanout_dir"], "again.record.json")
    json.dump({"parentSessionId": SID, "fanoutId": "later-fanout", "fanoutProcess": _pid_line(os.getpid()),
               "jobs": [{"label": "again", "recordPath": record}]},
              open(os.path.join(later["fanout_dir"], "fanout-later.inventory.json"), "w"))
    open(record + ".pid", "w").write(_pid_line(os.getpid()) + "\n")
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "never started" not in (msg or ""), msg
    assert all(p["fanout_dir"] != refused["fanout_dir"] for p in e.pending()), e.pending()


def test_fanout_with_no_inventory_yet_stays_pending_inside_its_preflight_window():
    e = Env()
    _refused_fanout(e, "starting", 30)
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "never started" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_background_fanout_call_still_running_stays_pending_without_an_inventory():
    e = Env()
    _refused_fanout(e, "bg", 700, task_id="bgtask")
    e.watch()
    msg = e.run(stop_payload(background=[{"id": "bgtask", "type": "shell"}]), "--complete")
    assert "never started" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_writer_fanout_stays_pending_after_review_seat_exit():
    e = Env()
    entry = _fanout_pending(e, "review-done", os.getpid())
    entry["writes"] = True
    json.dump({"dispatch_table_pending": [entry]}, open(e.state_path(), "w"))
    rec = os.path.join(entry["fanout_dir"], "review-done.record.json")
    open(rec + ".exit", "w").write("0")
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert len(e.pending()) == 1 and "still running" in msg, (msg, e.pending())


def test_finished_author_fanout_is_not_in_stop_running_block():
    for folds in (True, None, False):
        e = Env()
        entry = _fanout_pending(e, "author-done", os.getpid())
        entry["writes"] = True
        json.dump({"dispatch_table_pending": [entry]}, open(e.state_path(), "w"))
        inventory = os.path.join(entry["fanout_dir"], "fanout-proof.inventory.json")
        with open(inventory) as stream:
            data = json.load(stream)
        if folds is not None:
            data["afterSeats"] = folds
        json.dump(data, open(inventory, "w"))
        rec = os.path.join(entry["fanout_dir"], "author-done.record.json")
        open(rec + ".exit", "w").write("0")
        e.watch()
        msg = e.run(stop_payload(), "--complete")
        assert ("still running" in (msg or "")) is (folds is not False), (folds, msg)
        assert bool(e.pending()) is (folds is not False), (folds, e.pending())


def test_fanout_job_pid_keeps_existing_record_resolution():
    e = Env()
    entry = _fanout_pending(e, "started", os.getpid())
    rec = os.path.join(entry["fanout_dir"], "started.record.json")
    open(rec + ".pid", "w").write("pid=%d\\n" % os.getpid())
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "never started" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_relative_inventory_record_path_resolves_beside_the_inventory():
    """An inventory written before recordPath was absolute holds the path relative to the fan-out's
    cwd; the Stop hook runs elsewhere, so it resolves that path beside the inventory, where the
    fan-out writes every record, and a started job is never reported as never started."""
    e = Env()
    entry = _fanout_pending(e, "relstart", _exited_pid())
    out_dir = entry["fanout_dir"]
    inventory = os.path.join(out_dir, "fanout-proof.inventory.json")
    data = json.load(open(inventory, encoding="utf-8"))
    data["jobs"][0]["recordPath"] = "elsewhere/relstart.record.json"
    json.dump(data, open(inventory, "w", encoding="utf-8"))
    rec = os.path.join(out_dir, "relstart.record.json")
    open(rec + ".pid", "w").write(_pid_line(os.getpid()) + "\n")
    e.watch()
    msg = e.run(stop_payload(), "--complete")
    assert "never started" not in msg, msg


def test_two_hook_runs_for_one_fanout_call_register_it_once():
    """Two registered hooks can both fire for one call; they share its tool_use_id, so the second
    run replaces the first rather than leaving a twin the record resolver cannot tell apart."""
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "seat", "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/"), "toolu_once")
    first = e.run(payload, "--launch")
    second = e.run(payload, "--launch")
    assert len(e.pending()) == 1, e.pending()
    assert "seat" in first and second == "", (first, second)
    out_dir = os.path.join(os.path.dirname(jobs), "fanout")
    os.makedirs(out_dir, exist_ok=True)
    record = os.path.join(out_dir, "seat.record.json")
    json.dump({"servedModel": "muse-spark"}, open(record, "w"))
    open(record + ".exit", "w").write("0")
    json.dump({"parentSessionId": SID, "jobs": [{"label": "seat", "recordPath": record}]},
              open(os.path.join(out_dir, "fanout-one.inventory.json"), "w"))
    msg = e.run(stop_payload(), "--complete")
    assert e.pending() == [] and "still running" not in (msg or ""), (msg, e.pending())


def test_a_finished_fanout_launched_without_a_call_id_is_resolved_by_its_only_inventory():
    """A launch whose hook payload carried no tool_use_id (2026-10-10, design-rigid-brief) has no issue
    time, so its record never resolved and Stop demanded a watcher for a job that had finished. With no
    other entry sharing its out dir and label, the one inventory this session wrote for the label is its."""
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "seat", "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    out_dir = os.path.join(e.root, "fanout")
    cmd = "python3 .claude/tools/sidecar_fanout.py %s --out-dir %s" % (
        jobs.replace("\\", "/"), out_dir.replace("\\", "/"))
    e.run(bash_payload(cmd), "--launch", issue=False)
    assert len(e.pending()) == 1, e.pending()
    os.makedirs(out_dir, exist_ok=True)
    record = os.path.join(out_dir, "seat.record.json")
    json.dump({"servedModel": "muse-spark"}, open(record, "w"))
    open(record + ".exit", "w").write("0")
    json.dump({"parentSessionId": SID, "jobs": [{"label": "seat", "recordPath": record}]},
              open(os.path.join(out_dir, "fanout-nocall.inventory.json"), "w"))
    msg = e.run(stop_payload(), "--complete")
    assert e.pending() == [] and e.body.get("decision") != "block", (msg, e.body, e.pending())


def test_sidecar_fanout_rows():
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "fa", "alias": "muse", "effort": "max", "promptFile": "a.md"},
               {"label": "fb", "alias": "flash", "promptFile": "b.md"}], open(jobs, "w"))
    msg = e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s" % jobs.replace("\\", "/")), "--launch")
    assert cells(msg, "fa") == ["fa", "muse-spark-1.3-contributor-free", "max", "sidecar"], msg
    assert cells(msg, "fb") == ["fb", FLASH_VERSION, "default", "sidecar"], msg


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


def test_sidecar_for_loop_tracks_one_job_per_loop_value():
    """`for n in a b; do bash <launcher> -R $D/$n.json -l s-$n & done` runs one seat per value; each
    must be its own row and pending entry, or Stop waits forever on a literal `$n.json`."""
    e = Env()
    root = e.root.replace("\\", "/")
    cmd = ("cd %s && D=out && for n in a b; do (bash .claude/scripts/codex_proxy_sidecar.sh -m sol -e high "
           "-f p_$n.md -R $D/$n.record.json -l s-$n > $D/$n.out) & done; wait" % root)
    msg = e.run(bash_payload(cmd), "--launch")
    assert cells(msg, "s-a") and cells(msg, "s-b") and "$n" not in msg, msg
    records = sorted(os.path.basename(p["record"]) for p in e.pending())
    assert records == ["a.record.json", "b.record.json"], e.pending()
    os.makedirs(os.path.join(e.root, "out"))
    for n in ("a", "b"):
        path = os.path.join(e.root, "out", "%s.record.json" % n)
        json.dump({"servedModel": "gpt-6-sol"}, open(path, "w"))
        open(path + ".exit", "w").write("0")
    e.run(stop_payload(), "--complete")
    assert e.pending() == [], e.pending()


def test_relaunch_with_the_same_record_replaces_its_pending_entry():
    e = Env()
    cmd = "bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l rr -R rr.json -f p.md"
    e.run(bash_payload(cmd), "--launch")
    e.run(bash_payload(cmd), "--launch")
    assert len(e.pending()) == 1, e.pending()


def test_a_second_hook_run_for_one_launcher_call_prints_no_second_table():
    """Both Bash handlers can fire for one call (2026-10-10: the owner read two lines as two agents).
    The registry already keeps one row; the table prints once. A new call for the same record is a
    relaunch and prints again."""
    e = Env()
    cmd = "bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l once -R once.json -f p.md"
    first = e.run(bash_payload(cmd, "toolu_same"), "--launch")
    again = e.run(bash_payload(cmd, "toolu_same"), "--launch")
    relaunch = e.run(bash_payload(cmd, "toolu_other"), "--launch")
    assert "once" in first and again == "" and "once" in relaunch, (first, again, relaunch)
    assert len(e.pending()) == 1, e.pending()


def test_the_launchers_own_registration_does_not_silence_the_first_table():
    """The launcher registers the run before any PostToolUse hook runs (actual=True); the first hook
    run must still print, and only the second is silent."""
    e = Env()
    cmd = "bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -l pre -R pre.json -f p.md"
    payload = bash_payload(cmd, "toolu_pre")
    e.issue(payload)
    payload.update(session_id=SID, transcript_path=e.transcript, cwd=PROJECT)
    code = ("import json,sys; sys.path.insert(0, %r); import dispatch_table as t; p = json.loads(sys.stdin.read());"
            " t.register_sidecars(p, t.sidecar_entries(p)[0], actual=True)" % os.path.join(CLAUDE_DIR, "hooks"))
    done = subprocess.run([sys.executable, "-c", code], input=json.dumps(payload), capture_output=True, text=True,
                          env=hook_env(HARNESS_HOOK_STATE_DIR=e.state, SIDECAR_LEDGER_PATH=e.ledger))
    assert done.returncode == 0, done.stderr
    assert len(e.pending()) == 1, e.pending()
    first = e.run(bash_payload(cmd, "toolu_pre"), "--launch")
    again = e.run(bash_payload(cmd, "toolu_pre"), "--launch")
    assert "pre" in first and again == "", (first, again)
    assert len(e.pending()) == 1, e.pending()


def test_sidecar_record_behind_an_unexpandable_variable_is_not_tracked():
    e = Env()
    cmd = ("while read n; do bash .claude/scripts/deepseek_sidecar.sh -m flash -e low -f p.md "
           "-R $n.json -l w-$n; done < seats.txt")
    msg = e.run(bash_payload(cmd), "--launch")
    assert "not tracked" in msg and e.pending() == [], (msg, e.pending())


def test_sidecar_fanout_with_an_unreadable_jobs_file_is_not_tracked():
    """No record path and no out-dir inventory can ever finish such an entry, so registering it blocked
    Stop for 24 h (2026-09-29); sidecar_fanout refuses the same file, so nothing runs to track."""
    e = Env()
    jobs = os.path.join(e.root, "jobs.json")
    json.dump({"jobs": "not a list"}, open(jobs, "w"))
    cmd = "python3 .claude/tools/sidecar_fanout.py %s --out-dir out" % jobs.replace("\\", "/")
    msg = e.run(bash_payload(cmd), "--launch")
    assert "not tracked" in msg and e.pending() == [], (msg, e.pending())


def test_sidecar_fanout_whose_jobs_file_the_background_call_writes_later_is_tracked():
    """A backgrounded call returns before its own command writes the jobs file (2026-10-01: the launch line
    said "launches nothing" while both jobs ran, and no watcher could find them). The launch registers a
    deferred entry; once the file exists, the next table read expands it into one entry per job."""
    e = Env()
    jobs = os.path.join(e.root, "late.json")
    out_dir = os.path.join(e.root, "out")
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s"
                           % (jobs.replace("\\", "/"), out_dir.replace("\\", "/")))
    payload["tool_input"]["run_in_background"] = True
    msg = e.run(payload, "--launch")
    assert "launches nothing" not in msg and len(e.pending()) == 1 and e.pending()[0].get("deferred"), (msg, e.pending())
    json.dump([{"label": "late", "alias": "muse", "effort": "max", "promptFile": "a.md"}], open(jobs, "w"))
    e.watch()
    e.run(stop_payload(), "--complete")
    got = e.pending()
    assert [(p.get("jobs") or [{}])[0].get("label") for p in got] == ["late"] and not got[0].get("deferred") \
        and os.path.normcase(got[0].get("fanout_dir") or "") == os.path.normcase(out_dir), got


def test_a_fanout_loop_after_an_earlier_launch_in_the_same_call_tracks_each_loop_value():
    """Each launch reads the loops open at its own segment, not at the call's first launch (2026-10-07: a
    launch before `for k in a b c` left `$k` in the loop's jobs path, three slices ran unwatched and the
    watcher woke on `UNRESOLVED jobs_j12$k.json`)."""
    e = Env()
    root = e.root.replace("\\", "/")
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s/jobs_first.json --out-dir %s/out_first --detach\n"
                           "for k in a b; do python3 make_jobs.py $k; python3 .claude/tools/sidecar_fanout.py "
                           "%s/jobs_$k.json --out-dir %s/out_$k --detach; done" % (root, root, root, root))
    payload["tool_input"]["run_in_background"] = True
    e.run(payload, "--launch")
    for k in ("first", "a", "b"):
        json.dump([{"label": "slice-" + k, "alias": "muse", "effort": "max", "promptFile": "a.md"}],
                  open(os.path.join(e.root, "jobs_%s.json" % k), "w"))
    e.watch()
    e.run(stop_payload(), "--complete")
    got = sorted(((p.get("jobs") or [{}])[0].get("label"), os.path.basename(p.get("fanout_dir") or ""))
                 for p in e.pending())
    assert got == [("slice-a", "out_a"), ("slice-b", "out_b"), ("slice-first", "out_first")], e.pending()


def _expired_deferred(e, content=None):
    jobs = os.path.join(e.root, "never.json")
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s"
                           % (jobs.replace("\\", "/"), os.path.join(e.root, "out").replace("\\", "/")))
    payload["tool_input"]["run_in_background"] = True
    e.run(payload, "--launch")
    state = json.load(open(e.state_path(), encoding="utf-8"))
    for entry in state["dispatch_table_pending"]:
        entry["launched_at"] = time.time() - 3600
    json.dump(state, open(e.state_path(), "w", encoding="utf-8"))
    if content is not None:
        open(jobs, "w", encoding="utf-8", newline="\n").write(content)


def test_a_fanout_whose_jobs_file_never_appeared_is_reported_when_it_was_the_only_entry():
    e = Env()
    _expired_deferred(e)
    msg = e.run(stop_payload(), "--complete")
    assert "never.json: its jobs file never appeared" in msg and e.pending() == [], (msg, e.pending())


def test_a_fanout_whose_jobs_file_is_refused_is_reported_as_refused_not_absent():
    e = Env()
    _expired_deferred(e, "{not json")
    msg = e.run(stop_payload(), "--complete")
    assert "never.json: its jobs file was refused" in msg and "never appeared" not in msg, msg


def test_a_fanout_whose_jobs_file_lists_no_labeled_job_is_reported():
    e = Env()
    _expired_deferred(e, '[{"alias": "muse", "effort": "max", "promptFile": "a.md"}]')
    msg = e.run(stop_payload(), "--complete")
    assert "never.json: its jobs file lists no labeled job" in msg and e.pending() == [], (msg, e.pending())


def test_a_dropped_fanout_is_still_reported_after_another_reader_expanded_the_table_first():
    e = Env()
    _expired_deferred(e)
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import dispatch_table; "
                        "dispatch_table.expand_deferred(%r)" % (os.path.dirname(HOOK), SID)],
                       capture_output=True, text=True, timeout=30, env=hook_env(HARNESS_HOOK_STATE_DIR=e.state))
    assert r.returncode == 0, r.stderr
    msg = e.run(stop_payload(), "--complete")
    assert "never.json: its jobs file never appeared" in msg, msg
    assert "never appeared" not in e.run(stop_payload(), "--complete"), "reported twice"


def _expired_producer(e, task_id):
    """A backgrounded fan-out call, run as background task `task_id`, whose jobs file is still absent an hour on."""
    jobs = os.path.join(e.root, "slow.json")
    payload = bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s"
                           % (jobs.replace("\\", "/"), os.path.join(e.root, "out").replace("\\", "/")))
    payload["tool_input"]["run_in_background"] = True
    if task_id:
        payload["tool_response"]["backgroundTaskId"] = task_id
    e.run(payload, "--launch")
    state = json.load(open(e.state_path(), encoding="utf-8"))
    for entry in state["dispatch_table_pending"]:
        entry["launched_at"] = time.time() - 3600
    json.dump(state, open(e.state_path(), "w", encoding="utf-8"))
    return jobs


def test_a_deferred_fanout_whose_producer_still_runs_past_the_give_up_window_stays_tracked():
    e = Env()
    _expired_producer(e, "bslow1")
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import dispatch_table; "
                        "dispatch_table.read_table(%r)" % (os.path.dirname(HOOK), SID)],
                       capture_output=True, text=True, timeout=30, env=hook_env(HARNESS_HOOK_STATE_DIR=e.state))
    assert r.returncode == 0 and len(e.pending()) == 1, (r.stderr, e.pending())
    msg = e.run(stop_payload(background=[{"id": "bslow1", "type": "shell"}]), "--complete") or ""
    assert "launched nothing" not in msg and "never appeared" not in msg, msg
    assert len(e.pending()) == 1 and e.pending()[0].get("deferred"), e.pending()


def test_a_deferred_fanout_whose_producer_ended_without_the_file_launched_nothing():
    e = Env()
    _expired_producer(e, "bslow2")
    msg = e.run(stop_payload(), "--complete") or ""
    assert "slow.json: its jobs file never appeared, so the fan-out launched nothing" in msg and e.pending() == [], \
        (msg, e.pending())


def test_a_deferred_fanout_with_no_checkable_producer_expires_as_unknown_and_revives_when_its_file_appears():
    e = Env()
    jobs = _expired_producer(e, None)
    msg = e.run(stop_payload(), "--complete") or ""
    assert "unknown" in msg and "launched nothing" not in msg and e.pending() == [], (msg, e.pending())
    json.dump([{"label": "tardy", "alias": "muse", "effort": "max", "promptFile": "a.md"}], open(jobs, "w"))
    msg = e.run(stop_payload(), "--complete") or ""
    got = e.pending()
    assert [(p.get("jobs") or [{}])[0].get("label") for p in got] == ["tardy"] and "appeared late" in msg, (msg, got)


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
                            "args": {"jobs": [{"label": "ok1", "model": "haiku45", "effort": "low"},
                                              {"label": "bad1", "model": "opus", "effort": "high"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("ok1", "aa1", "haiku", "claude-haiku-4-5", "low"),
                                           ("bad1", "aa2", "opus", "claude-sonnet-5", "medium")])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ bad1 ran sonnet-5 · medium, asked opus · high", msg
    assert e.pending() == []


def test_stop_reads_a_run_journal_holding_an_undecodable_byte():
    e = Env()
    e.plant_run("wf_proof-1", "completed", [("bad1", "aa2", "opus", "claude-sonnet-5", "medium")])
    host = os.path.join(e.session, "workflows", "wf_proof-1.json")
    raw = open(host, "rb").read()
    assert b'"logs": []' in raw, raw[:200]
    open(host, "wb").write(raw.replace(b'"logs": []', b'"logs": ["caf\xc3"]'))
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))
    import dispatch_table
    got = dispatch_table._workflow_contradictions({"id": "wf_proof-1", "jobs": []}, e.session, {})
    assert got is not None, "an undecodable byte made the run journal read as still being written"


def test_stop_missing_recorded_effort_says_not_recorded():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "ne", "model": "haiku45", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("ne", "ab1", "haiku", "claude-haiku-4-5", None)])
    msg = e.run(stop_payload(), "--complete")
    assert msg == "", "a model that takes no effort is not a mismatch: " + msg


def test_stop_running_workflow_stays_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "j1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "running", [("j1", "ac1", "haiku", "claude-haiku-4-5", "low")])
    msg = e.run(stop_payload([{"id": "t-wf_proof-1", "type": "workflow", "status": "running"}]), "--complete")
    assert msg.startswith("⏳ still running · 1") and len(e.pending()) == 1, (msg, e.pending())


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


def test_secondary_plan_child_transcript_is_read_under_its_config_dir():
    """A child on another Anthropic plan writes its transcript under that plan's `configDir`, and
    its record names the plan as its transport; the default projects root holds nothing."""
    e = Env()
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "tools"))
    import model_registry
    data = model_registry.load()
    config_dir = os.path.join(e.root, "second-login")
    data["transports"]["anthropic-secondary"]["configDir"] = config_dir
    registry = os.path.join(e.root, "external_models.json")
    with open(registry, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    child = os.path.join(config_dir, "projects", "some-project", "child-sid-3.jsonl")
    os.makedirs(os.path.dirname(child))
    with open(child, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"id": "m", "model": "claude-sonnet-5", "content": []}}) + "\n")
    rec = os.path.join(e.root, "as.json").replace("\\", "/")
    saved = os.environ.get("HARNESS_MODEL_REGISTRY")
    os.environ["HARNESS_MODEL_REGISTRY"] = registry
    try:
        e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -l as -R %s -f p.md" % rec), "--launch")
        json.dump({"servedModel": "claude-sonnet-5", "requestedModel": "sonnet", "effort": "low", "label": "as",
                   "transport": "anthropic-secondary", "sessionId": "child-sid-3"}, open(rec, "w"))
        msg = e.run(stop_payload(), "--complete")
    finally:
        if saved is None:
            os.environ.pop("HARNESS_MODEL_REGISTRY", None)
        else:
            os.environ["HARNESS_MODEL_REGISTRY"] = saved
    assert msg == "⚠ as ran sonnet-5 · medium, asked sonnet · low", msg


def test_stale_entry_reported_and_dropped():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "old", "model": "haiku45", "effort": "low"}]}}, run_id="wf_old"), "--launch")
    path = os.path.join(e.state, SID[:8] + ".json")
    state = json.load(open(path))
    state["dispatch_table_pending"][0]["launched_at"] = time.time() - 25 * 3600
    json.dump(state, open(path, "w"))
    msg = e.run(stop_payload(), "--complete")
    assert msg == "⚠ workflow wf_old: no completion after 24 h" and e.pending() == [], (msg, e.pending())


# ---------------------------------------------------------------- session-audit regressions (2026-09-23)

def test_chain_and_lens_omitted_effort_is_unknown_not_the_session_level():
    """No engine fills an omitted effort (it refuses the job), so the row shows `?`, never the session's level."""
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch_chains.js",
                                  "args": {"chains": [{"name": "c", "jobs": [{"label": "cj", "model": "sonnet"}]}]}}), "--launch")
    assert "high" not in msg, msg
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/any_lens_engine.js",
                                  "args": {"lenses": [{"key": "lz", "model": "sonnet", "agentType": "Explore"}]}},
                                 run_id="wf_proof-2"), "--launch")
    assert "high" not in msg, msg


def test_reused_sidecar_label_is_not_judged_by_the_previous_runs_ledger_row():
    e = Env()
    with open(e.ledger, "w") as fh:
        fh.write(json.dumps({"parentSessionId": SID, "label": "re", "timestamp": "2026-01-01T00:00:00Z",
                             "requestedModel": "sonnet", "servedModel": "claude-haiku-4-5", "effort": "low"}) + "\n")
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -l re -f p.md"), "--launch")
    assert "⚠" not in msg and len(e.pending()) == 1, (msg, e.pending())


def _finish_row(e, label, outcome, code, reason=""):
    with open(e.ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 1)) + ".000Z",
                             "parentSessionId": SID, "label": label, "transport": "codex", "exitCode": code,
                             "outcome": outcome, "reason": reason}) + "\n")


def test_sidecar_that_ended_before_a_result_is_named_once_and_leaves():
    """A launch that ends before a result writes a finish row, and the Stop table resolves it once, naming
    the refusal line, instead of listing it as still running."""
    e = Env()
    e.watch()
    msg = e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -l new -f p.md"), "--launch")
    assert len(e.pending()) == 1, (msg, e.pending())
    _finish_row(e, "new", "ended-before-result", 12, "REFUSED memory headroom for new: commit 1.0 GiB free")
    msg = e.run(stop_payload(), "--complete")
    assert "⚠ sidecar new ended before a result (exit 12): REFUSED memory headroom for new: commit 1.0 GiB free" \
        in msg, msg
    assert "still running" not in msg and e.pending() == [], (msg, e.pending())
    msg = e.run(stop_payload(), "--complete")
    assert "new" not in msg, msg


def test_sidecar_that_finished_without_a_record_resolves_silently():
    e = Env()
    e.watch()
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -l plain -f p.md"), "--launch")
    _finish_row(e, "plain", "finished-without-record", 0)
    msg = e.run(stop_payload(), "--complete")
    assert "plain" not in msg and "still running" not in msg and e.pending() == [], (msg, e.pending())


def test_unlabeled_sidecar_without_record_prints_its_row_but_is_not_tracked():
    e = Env()
    msg = e.run(bash_payload("bash .claude/scripts/anthropic_sidecar.sh -m sonnet -e low -f p.md"), "--launch")
    assert cells(msg, "(unlabeled)") == ["(unlabeled)", "sonnet-5-5", "low", "sidecar"], msg
    assert e.pending() == [], e.pending()


def test_run_file_still_being_written_stays_pending():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "pw", "model": "haiku45", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "completed", [("pw", "pw1", "haiku", "claude-haiku-4-5", None)])
    with open(os.path.join(e.session, "workflows", "wf_proof-1.json"), "w") as fh:
        fh.write('{"runId": "wf_proof-1", "sta')
    msg = e.run(stop_payload(), "--complete")
    assert "unreadable" not in msg and len(e.pending()) == 1, (msg, e.pending())


def test_background_agent_that_died_without_end_turn_finishes_once_gone_and_quiet():
    e = Env()
    _agent_transcript(e, "a0001", "tool_use")
    e.run(agent_payload({"description": "dead", "prompt": "x", "model": "haiku45"}), "--launch")
    path = os.path.join(e.session, "subagents", "agent-a0001.jsonl")
    msg = e.run(stop_payload([{"id": "a0001", "type": "subagent"}]), "--complete")
    assert msg.startswith("⏳ still running") and len(e.pending()) == 1, msg
    old = time.time() - 600
    os.utime(path, (old, old))
    msg = e.run(stop_payload([{"id": "a0001", "type": "subagent"}]), "--complete")
    assert msg.startswith("⏳ still running") and len(e.pending()) == 1, "still listed as running: " + msg
    msg = e.run(stop_payload(), "--complete")
    assert msg == "" and e.pending() == [], (msg, e.pending())



def _handback_transcript(e, aid, tool="SubagentHandback"):
    """A subagent transcript the way this client records a finished one: the last assistant row is the handback
    tool call with no stop_reason, never an end_turn row."""
    os.makedirs(os.path.join(e.session, "subagents"), exist_ok=True)
    with open(os.path.join(e.session, "subagents", "agent-%s.jsonl" % aid), "w") as fh:
        fh.write(json.dumps({"type": "assistant", "effort": "medium",
                             "message": {"id": "m1", "model": "claude-haiku-4-5", "stop_reason": None,
                                         "content": [{"type": "tool_use", "name": tool, "input": {}}]}}) + "\n")


def test_agent_that_handed_back_its_report_finishes_at_once():
    """Live replay 2026-10-10: six agents ended with SubagentHandback (no end_turn row) and listed as running for
    five minutes after their reports arrived."""
    e = Env()
    _handback_transcript(e, "a0001")
    e.run(agent_payload({"description": "hb", "prompt": "x", "model": "haiku45"}), "--launch")
    msg = e.run(stop_payload(), "--complete")
    assert msg == "" and e.pending() == [], "handed-back agent still listed: " + msg


def test_agent_that_has_not_handed_back_stays_running():
    e = Env()
    _handback_transcript(e, "a0001", tool="Read")
    e.run(agent_payload({"description": "hb", "prompt": "x", "model": "haiku45"}), "--launch")
    msg = e.run(stop_payload(), "--complete")
    assert msg.startswith("⏳ still running · 1") and len(e.pending()) == 1, msg


def test_handed_back_agent_resumed_in_background_stays_running():
    e = Env()
    _handback_transcript(e, "a0001")
    e.run(agent_payload({"description": "hb", "prompt": "x", "model": "haiku45"}), "--launch")
    msg = e.run(stop_payload([{"id": "a0001", "type": "subagent"}]), "--complete")
    assert msg.startswith("⏳ still running · 1") and len(e.pending()) == 1, msg


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


# ---------------------------------------------------------------- still-running block (2026-09-23)

def _age(e, seconds):
    path = os.path.join(e.state, SID[:8] + ".json")
    state = json.load(open(path, encoding="utf-8"))
    for entry in state["dispatch_table_pending"]:
        entry["launched_at"] = time.time() - seconds
    json.dump(state, open(path, "w", encoding="utf-8"))


def _block(msg):
    """The still-running block's lines ([] when absent); it is always the message's last block."""
    lines = msg.split("\n\n")[-1].splitlines() if msg else []
    return lines if lines and lines[0].startswith("⏳ still running") else []


def test_running_workflow_prints_one_line_with_pins_and_elapsed():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "r1", "model": "haiku45", "effort": "low"},
                                              {"label": "r2", "model": "haiku45", "effort": "low"},
                                              {"label": "r3", "model": "sonnet", "effort": "medium"}]}}), "--launch")
    _age(e, 7 * 60 + 5)
    block = _block(e.run(stop_payload(), "--complete"))
    assert block[:1] == ["⏳ still running · 1"] and len(block) == 2, block
    assert "3 agents" in block[1] and "haiku-4-5 · low ×2" in block[1] and "sonnet-5-5 · medium ×1" in block[1] \
        and block[1].endswith(" · 7m"), block


def test_running_sidecar_line_names_launcher_label_pins():
    e = Env()
    rec = os.path.join(e.root, "run.json").replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -m luna -e max -l lens:a -R %s -f p.md" % rec),
          "--launch")
    _age(e, 2 * 3600 + 5 * 60)
    e.watch()
    block = _block(e.run(stop_payload(), "--complete"))
    assert len(block) == 2 and "codex_proxy" in block[1] and "lens:a" in block[1] and " · max" in block[1] \
        and block[1].endswith(" · 2h05m"), block


def _sidecar_with_pid_file(e, label, pid, stamp):
    """A launched sidecar whose record is still the launcher's empty placeholder, no .exit, and a
    `<record>.pid` line in the launcher's format naming `pid` at `stamp` (epoch seconds)."""
    rec = os.path.join(e.root, "%s.record.json" % label).replace("\\", "/")
    e.run(bash_payload("bash .claude/scripts/codex_proxy_sidecar.sh -m luna -e high -l %s -R %s -f p.md"
                       % (label, rec)), "--launch")
    open(rec, "w").close()
    key = "winpid=%d pid=1" if os.name == "nt" else "pid=%d"
    with open(rec + ".pid", "w", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)) + " " + key % pid + "\n")
    return rec


def _exited_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_sidecar_whose_launcher_died_without_an_exit_file_is_reported_not_running():
    e = Env()
    _sidecar_with_pid_file(e, "gone1", _exited_pid(), time.time())
    msg = e.run(stop_payload(), "--complete")
    assert "⚠" in msg and "gone1" in msg and "ended without" in msg, msg
    assert _block(msg) == [] and e.pending() == [], (msg, e.pending())


def test_sidecar_whose_launcher_is_alive_stays_running():
    e = Env()
    _sidecar_with_pid_file(e, "live1", os.getpid(), time.time() + 1)
    e.watch()
    block = _block(e.run(stop_payload(), "--complete"))
    assert len(block) == 2 and "live1" in block[1] and len(e.pending()) == 1, block


def test_a_reused_launcher_pid_is_not_proof_of_life():
    e = Env()
    # This process is alive, but it was created after the stamp: it cannot be the launcher that wrote it.
    _sidecar_with_pid_file(e, "reused1", os.getpid(), time.time() - 3600)
    msg = e.run(stop_payload(), "--complete")
    assert "reused1" in msg and "ended without" in msg and _block(msg) == [], msg


def test_finished_and_stale_dispatches_are_not_listed_as_running():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "bad1", "model": "opus", "effort": "high"}]}},
                           run_id="wf_done"), "--launch")
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "live1", "model": "haiku45", "effort": "low"}]}},
                           run_id="wf_live"), "--launch")
    e.plant_run("wf_done", "completed", [("bad1", "bd1", "opus", "claude-sonnet-5", "medium")])
    msg = e.run(stop_payload(), "--complete")
    block = _block(msg)
    assert msg.startswith("⚠ bad1 ran"), msg
    assert block[:1] == ["⏳ still running · 1"] and len(block) == 2 and "bad1" not in block[1], msg
    _age(e, 25 * 3600)
    msg = e.run(stop_payload(), "--complete")
    assert "no completion after 24 h" in msg and _block(msg) == [], msg


def test_launch_output_never_carries_the_running_block():
    e = Env()
    msg = e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                                  "args": {"jobs": [{"label": "l1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    assert "still running" not in msg, msg


def test_a_malformed_running_entry_does_not_hide_the_others():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "ok1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    path = os.path.join(e.state, SID[:8] + ".json")
    state = json.load(open(path, encoding="utf-8"))
    state["dispatch_table_pending"].append({"kind": "workflow", "id": "wf_bad", "launched_at": time.time(),
                                           "name": "bad", "run_dir": os.path.join(e.root, "none"),
                                           "jobs": ["not-a-dict"]})
    json.dump(state, open(path, "w", encoding="utf-8"))
    block = _block(e.run(stop_payload(), "--complete"))
    assert block[:1] == ["⏳ still running · 2"] and len(block) == 3, block
    assert "haiku-4-5 · low ×1" in block[1] and "unreadable" in block[2], block

def _plant_cachestat(e, body):
    with open(os.path.join(e.root, "cc-cachestat-%s.json" % SID), "w", encoding="utf-8") as fh:
        fh.write(body)


def _running_workflow(e):
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "j1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "running", [("j1", "ac1", "haiku", "claude-haiku-4-5", "low")])
    return e.run(stop_payload([{"id": "t-wf_proof-1", "type": "workflow", "status": "running"}]), "--complete")


def test_still_running_header_shows_session_context_size():
    e = Env()
    _plant_cachestat(e, json.dumps({"last": "2023:248065:2", "context_pct": 25}))
    msg = _running_workflow(e)
    assert msg.startswith("⏳ still running · 1 · ctx 250k (25%)\n"), msg


def test_still_running_header_omits_context_when_the_gauge_is_absent_or_unreadable():
    e = Env()
    assert _running_workflow(e).startswith("⏳ still running · 1\n")
    _plant_cachestat(e, "{not json")
    assert _running_workflow(e).startswith("⏳ still running · 1\n")


def test_nothing_running_prints_no_context_line():
    e = Env()
    _plant_cachestat(e, json.dumps({"last": "2023:248065:2", "context_pct": 25}))
    assert e.run(stop_payload(), "--complete") == ""


def test_fanout_inventory_is_bounded_by_its_calls_issue_time_alone():
    """An inventory written at or after the call's issue time is the call's, however long before
    the hook's own `launched_at` stamp; one written before the call is an earlier launch's."""
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))
    import dispatch_table
    for name, offset, found in (("just-after-issue", 0.001, True), ("hours-before-stamp", 0.5, True),
                                ("before-issue", -0.5, False)):
        e = Env()
        out_dir = os.path.join(e.root, "out")
        os.makedirs(out_dir)
        record = os.path.join(out_dir, name + ".record.json")
        issued = time.time() - 7200
        _plant_inventory(out_dir, name, [(name, record)], issued + offset)
        entry = {"kind": "sidecar", "id": out_dir + ":" + name + ":toolu_x", "record": None,
                 "fanout_dir": out_dir, "launched_at": time.time(), "issued_at": issued,
                 "jobs": [{"label": name}]}
        assert (dispatch_table.resolve_sidecar_record(entry, SID, [entry]) == record) is found, name
        assert (entry.get("inventory") is not None) is found, name
# ---------------------------------------------------------------- the no-watcher block (2026-09-28)
# A sidecar running with no live watcher means nothing wakes the session when that sidecar needs a
# decision. `--complete` blocks on it; the running block still reaches the user through
# `systemMessage`, and the reason reaches the model.

def test_stop_blocks_a_running_sidecar_with_no_live_watcher():
    e = Env()
    rec = _sidecar_with_pid_file(e, "nowatch", os.getpid(), time.time() + 1)
    msg = e.run(stop_payload(), "--complete")
    assert e.body.get("decision") == "block", e.body
    assert WATCH_COMMAND in e.body["reason"] and "1 sidecar(s)" in e.body["reason"], e.body
    assert "nowatch" in msg and "still running" in msg, msg


def test_stop_does_not_block_a_sidecar_a_live_watcher_covers_by_name():
    e = Env()
    rec = _sidecar_with_pid_file(e, "named1", os.getpid(), time.time() + 1)
    e.watch(records=[rec], session=False)
    e.run(stop_payload(), "--complete")
    assert "decision" not in e.body, e.body


def test_stop_does_not_block_a_sidecar_a_session_watcher_covers():
    e = Env()
    _sidecar_with_pid_file(e, "sess1", os.getpid(), time.time() + 1)
    e.watch()
    e.run(stop_payload(), "--complete")
    assert "decision" not in e.body, e.body


def test_stop_blocks_when_the_registered_watcher_is_dead():
    e = Env()
    _sidecar_with_pid_file(e, "deadwatch", os.getpid(), time.time() + 1)
    e.watch(pid=99999999)
    e.run(stop_payload(), "--complete")
    assert e.body.get("decision") == "block", e.body


def test_stop_hook_active_never_blocks():
    e = Env()
    _sidecar_with_pid_file(e, "active1", os.getpid(), time.time() + 1)
    e.run(stop_payload(stop_hook_active=True), "--complete")
    assert "decision" not in e.body, e.body


def test_workflow_only_pending_never_blocks():
    e = Env()
    e.run(workflow_payload({"scriptPath": ".claude/workflows/dispatch.js",
                            "args": {"jobs": [{"label": "w1", "model": "haiku45", "effort": "low"}]}}), "--launch")
    e.plant_run("wf_proof-1", "running", [("w1", "w1a", "haiku", "claude-haiku-4-5", "low")])
    msg = e.run(stop_payload([{"id": "t-wf_proof-1", "type": "workflow", "status": "running"}]), "--complete")
    assert "decision" not in e.body and "still running" in msg, (e.body, msg)


def test_stop_blocks_once_per_call_for_every_uncovered_sidecar():
    e = Env()
    _sidecar_with_pid_file(e, "u1", os.getpid(), time.time() + 1)
    _sidecar_with_pid_file(e, "u2", os.getpid(), time.time() + 1)
    e.watch(records=[os.path.join(e.root, "u1.record.json")], session=False)
    e.run(stop_payload(), "--complete")
    assert e.body.get("decision") == "block" and "1 sidecar(s)" in e.body["reason"], e.body


# ---------------------------------------------------------------- fan-out identity by fact (2026-09-28)
# Session 1f652996: a foreground fan-out hit its 300 s tool timeout, so PostToolUse stamped
# `launched_at` 301 s after the fan-out wrote its inventory. Matching the inventory by that stamp
# left all 8 finished jobs `still running` on every Stop. A job's record is found from the launch's
# own facts: its out dir, label, parent session, and the transcript time its call was issued.

def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % int(
        (epoch % 1) * 1000)


def _plant_call(e, call, issued_at, answered_at=None):
    """The transcript rows the harness writes for one Bash call: the assistant tool_use row (issued
    before any hook or the command runs), and later its tool_result row naming the same id."""
    rows = [{"type": "assistant", "timestamp": _iso(issued_at), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call, "name": "Bash",
         "input": {"command": "python3 .claude/tools/sidecar_fanout.py jobs.json --out-dir out"}}]}}]
    if answered_at is not None:
        rows.append({"type": "user", "timestamp": _iso(answered_at), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call, "content": ""}]}})
    with open(e.transcript, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _pid_line(pid):
    """A process line in the launcher's format, stamped now so a live pid is not read as reused."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


def _plant_inventory(out_dir, name, records, written_at, fanout_pid=None):
    path = os.path.join(out_dir, "fanout-%s.inventory.json" % name)
    json.dump({"schemaVersion": 1, "fanoutId": name, "parentSessionId": SID,
               "fanoutProcess": _pid_line(fanout_pid or _exited_pid()),
               "jobs": [{"label": label, "launchId": "l-" + label, "recordPath": record}
                        for label, record in records]}, open(path, "w", encoding="utf-8"))
    os.utime(path, (written_at, written_at))
    return path


def _late_registered_fanout(e, labels, exit_code="1", launcher_pid=None, call="toolu_timedout"):
    """The live defect's shape, registered exactly as the unfixed hook registered it: every entry
    carries only `launched_at`, stamped 301 s after the inventory was written, and no issue time."""
    out_dir = os.path.join(e.root, "inventory", "out")
    os.makedirs(out_dir)
    issued = time.time() - 900
    written = issued + 2.46
    launched = written + 301
    _plant_call(e, call, issued, answered_at=launched)
    records = [(label, os.path.join(out_dir, label + ".record.json")) for label in labels]
    for _, record in records:
        open(record + ".pid", "w").write(_pid_line(launcher_pid or _exited_pid()) + "\n")
        if exit_code is not None:  # a run writes its record, then its exit file, as it ends
            json.dump({"servedModel": "muse-spark"}, open(record, "w"))
            open(record + ".exit", "w").write(exit_code)
    _plant_inventory(out_dir, "d7d88dc8", records, written)
    entries = [{"kind": "sidecar", "id": out_dir + ":" + label + ":" + call, "record": None,
                "fanout_dir": out_dir, "task_id": None, "launched_at": launched,
                "jobs": [{"label": label, "model": "muse", "effort": "default", "launcher": "muse",
                          "kind": "fanout"}]} for label in labels]
    json.dump({"dispatch_table_pending": entries}, open(e.state_path(), "w", encoding="utf-8"))
    return out_dir, records



def test_fanout_registered_after_a_tool_timeout_is_finished_not_running():
    e = Env()
    labels = ["drive-inv-j%d" % n for n in range(1, 4)]
    _late_registered_fanout(e, labels)
    e.watch()
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "still running" not in (msg or ""), msg
    assert e.pending() == [], e.pending()


def test_fanout_whose_launcher_lives_without_exit_stays_running():
    e = Env()
    _late_registered_fanout(e, ["live1"], exit_code=None, launcher_pid=os.getpid())
    e.watch()
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "still running" in msg and "live1" in msg, msg
    assert len(e.pending()) == 1 and e.pending()[0]["record"], e.pending()


def test_fanout_whose_launcher_is_gone_without_exit_is_died():
    e = Env()
    _late_registered_fanout(e, ["gone1"], exit_code=None)
    e.watch()
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "⚠ sidecar gone1 ended without an exit file" in msg and "still running" not in msg, msg
    assert e.pending() == [], e.pending()


def test_an_earlier_launchs_inventory_never_satisfies_a_newer_call():
    """Same out dir, same label, same session: the earlier fan-out's inventory predates the newer
    call's issue time, so it is never the newer entry's, however recent it is."""
    e = Env()
    out_dir = os.path.join(e.root, "out")
    os.makedirs(out_dir)
    first = os.path.join(out_dir, "seat.record.json")
    open(first + ".exit", "w").write("0")
    issued = time.time() - 5
    _plant_inventory(out_dir, "earlier", [("seat", first)], issued - 1)
    _plant_call(e, "toolu_newer", issued)
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": "seat", "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s" % (
        jobs.replace("\\", "/"), out_dir.replace("\\", "/")), "toolu_newer"), "--launch")
    assert abs(e.pending()[0].get("issued_at", 0) - issued) < 0.002, ("launch stamps the call's row time", e.pending())
    e.watch()
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "still running" in msg and len(e.pending()) == 1 and e.pending()[0]["record"] is None, (
        msg, e.pending())
    second = os.path.join(out_dir, "seat.attempt2.record.json")
    _plant_inventory(out_dir, "newer", [("seat", second)], time.time(), fanout_pid=os.getpid())
    e.run(stop_payload(), "--complete", projects_root=e.root)
    assert e.pending()[0]["record"] == second, e.pending()


def _refused_then_relaunched(e, label="gate"):
    """The 2026-10-06 shape: one hooked fan-out call; its first attempt was refused for memory (empty
    record, `.exit` 12, an ENDED ledger row); an unhooked process relaunched the same jobs file into
    the same out dir, writing a second inventory, which no later hooked call owns."""
    out_dir = os.path.join(e.root, "out")
    os.makedirs(out_dir)
    issued = time.time() - 60
    _plant_call(e, "toolu_gate", issued)
    jobs = os.path.join(e.root, "jobs.json")
    json.dump([{"label": label, "alias": "muse", "promptFile": "a.md"}], open(jobs, "w"))
    e.run(bash_payload("python3 .claude/tools/sidecar_fanout.py %s --out-dir %s" % (
        jobs.replace("\\", "/"), out_dir.replace("\\", "/")), "toolu_gate"), "--launch")
    first = os.path.join(out_dir, label + ".record.json")
    _plant_inventory(out_dir, "first", [(label, first)], issued + 1)
    open(first, "w").close()
    open(first + ".exit", "w").write("12")
    with open(e.ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": _iso(issued + 2), "label": label, "exitCode": 12,
                             "outcome": "ended-before-result", "parentSessionId": SID,
                             "reason": "launch refused: not enough free memory"}) + "\n")
    second = os.path.join(out_dir, label + ".attempt2.record.json")
    _plant_inventory(out_dir, "relaunch", [(label, second)], issued + 30, fanout_pid=os.getpid())
    open(second + ".pid", "w").write(_pid_line(os.getpid()) + "\n")
    return first, second


def test_an_ended_attempt_resolves_to_its_calls_later_relaunch():
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))
    import dispatch_table
    e = Env()
    first, second = _refused_then_relaunched(e)
    entry = dict(e.pending()[0], record=first,
                 inventory=os.path.join(os.path.dirname(first), "fanout-first.inventory.json"))
    assert dispatch_table.resolve_sidecar_record(entry, SID, [entry]) == second, entry
    assert entry["inventory"].endswith("fanout-relaunch.inventory.json"), entry


def test_a_running_attempt_keeps_its_record_beside_a_later_inventory():
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))
    import dispatch_table
    e = Env()
    first, second = _refused_then_relaunched(e)
    os.remove(first + ".exit")
    entry = dict(e.pending()[0], record=first,
                 inventory=os.path.join(os.path.dirname(first), "fanout-first.inventory.json"))
    assert dispatch_table.resolve_sidecar_record(entry, SID, [entry]) == first, entry


def test_a_later_hooked_calls_inventory_is_never_an_ended_attempts_relaunch():
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "hooks"))
    import dispatch_table
    e = Env()
    first, second = _refused_then_relaunched(e)
    entry = dict(e.pending()[0], record=first,
                 inventory=os.path.join(os.path.dirname(first), "fanout-first.inventory.json"))
    later = dict(entry, id=entry["id"].replace("toolu_gate", "toolu_later"), record=None, inventory=None,
                 issued_at=entry["issued_at"] + 20)
    assert dispatch_table.resolve_sidecar_record(entry, SID, [entry, later]) == first, entry


def test_an_ended_row_never_answers_for_a_later_launch_on_the_same_record():
    """A relaunch into the same out dir reuses the record path; the earlier attempt's ENDED row, written
    before the record's latest launch line, is that attempt's, so the finished relaunch reads as DONE."""
    sys.path.insert(0, os.path.join(CLAUDE_DIR, "tools"))
    import watch_records
    e = Env()
    record = os.path.join(e.root, "seat.record.json")
    issued = time.time() - 120
    ended = {"timestamp": _iso(issued + 2), "label": "seat", "exitCode": 2, "outcome": "ended-before-result",
             "parentSessionId": SID, "reason": "invalid pin basis"}
    entry = {"issued_at": issued, "record": record, "jobs": [{"label": "seat"}]}
    assert watch_records.finish_row(entry, [ended]) == ended
    with open(record + ".pid", "w", encoding="utf-8") as fh:
        fh.write(_iso(issued + 10) + " launch=relaunch winpid=1 pid=1\n")
    assert watch_records.finish_row(entry, [ended]) is None


def test_stop_follows_a_refused_jobs_relaunch_until_it_finishes():
    e = Env()
    first, second = _refused_then_relaunched(e)
    e.watch()
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "ended before a result" not in msg and "still running" in msg, msg
    assert len(e.pending()) == 1 and e.pending()[0]["record"] == second, e.pending()
    json.dump({"requestedModel": "muse"}, open(second, "w"))
    open(second + ".exit", "w").write("0")
    os.remove(second + ".pid")
    msg = e.run(stop_payload(), "--complete", projects_root=e.root)
    assert "ended before a result" not in msg and e.pending() == [], (msg, e.pending())


def _projects_root_in(env, plan=None):
    """dispatch_table._projects_root(plan) in a child whose environment is `env` plus the proof's."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import dispatch_table as d; "
            "print(d._projects_root(sys.argv[2] or None))")
    base = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECTS_ROOT")}
    out = subprocess.run([sys.executable, "-c", code, os.path.join(CLAUDE_DIR, "hooks"), plan or ""],
                         capture_output=True, text=True, timeout=30, env=dict(base, **env))
    assert out.returncode == 0, out.stderr[-500:]
    return os.path.normcase(os.path.normpath(out.stdout.strip()))


def test_own_transcript_root_follows_the_sessions_config_dir():
    """A host session on a second login writes its own transcript under that login's projects dir."""
    root = tempfile.mkdtemp(prefix="dispatch_table_login_")
    login = os.path.join(root, "second-login")
    want = os.path.normcase(os.path.normpath(os.path.join(login, "projects")))
    assert _projects_root_in({"CLAUDE_CONFIG_DIR": login}) == want, "own root follows CLAUDE_CONFIG_DIR"
    override = os.path.join(root, "override")
    got = _projects_root_in({"CLAUDE_CONFIG_DIR": login, "CLAUDE_PROJECTS_ROOT": override})
    assert got == os.path.normcase(os.path.normpath(override)), ("CLAUDE_PROJECTS_ROOT still overrides", got)
    default = os.path.normcase(os.path.normpath(os.path.expanduser("~/.claude/projects")))
    got = _projects_root_in({"HARNESS_DT_TEST_HOME": os.path.expanduser("~"),
                             "CLAUDE_CONFIG_DIR": "$HARNESS_DT_TEST_HOME/.claude/"})
    assert got == default, ("a $VAR spelling of the default login is the default root", got)


def _probe_env(code, **env):
    full = {k: v for k, v in os.environ.items() if k not in ("SIDECAR_DISPATCH_OWNER", "CLAUDE_CODE_SESSION_ID")}
    full.update(env)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full,
                          stdin=subprocess.DEVNULL, timeout=120).stdout.strip().splitlines()[-1:]


def test_a_launch_inside_a_bench_owned_tree_is_not_the_sessions_dispatch():
    """A bench daemon tracks the judge and arm seats it launches; registering them in the session that
    started the daemon left Stop blocking on seats no watcher of that session can keep covered."""
    code = ("import sys; sys.path.insert(0, %r); import sidecar_dispatch_context as c; print(c.tracking_session())"
            % os.path.join(CLAUDE_DIR, "hooks"))
    assert _probe_env(code, CLAUDE_CODE_SESSION_ID=SID) == [SID]
    assert _probe_env(code, CLAUDE_CODE_SESSION_ID=SID, SIDECAR_DISPATCH_OWNER="bench") == ["None"]


# A bench roster this session started registers no sidecar entry (SIDECAR_DISPATCH_OWNER=bench), so the
# no-watcher block must find it the way the session watcher does: a live `<logs>/<name>.pid` whose process
# carries this session id. A live process stands in for the worker: its environment cannot be planted.
def _live_roster(e, owner, name="cov-proof-t7"):
    logs = os.path.join(e.root, "arms", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, name + ".log"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("START %s\n[2026-10-20T19:00:00Z] WAITING for prior grid\n" % name)
    pid_file = os.path.join(logs, name + ".pid")
    shell = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else "bash"
    worker = subprocess.Popen([shell, "-c", 'echo $$ > "$1"; exec sleep 60', "x", pid_file.replace("\\", "/")],
                              env=dict(os.environ, CLAUDE_CODE_SESSION_ID=owner))
    deadline = time.time() + 20
    while time.time() < deadline and not (os.path.exists(pid_file) and open(pid_file).read().strip()):
        time.sleep(0.1)
    return worker


def test_stop_blocks_a_live_roster_of_this_session_with_no_watcher():
    if not os.path.isdir(os.path.join(CLAUDE_DIR, "scripts", "benchmark_campaign", "scoring")):
        return  # the roster poll lives in the benchmark suite, a project-layer tree a consumer lacks
    e = Env()
    worker = _live_roster(e, SID)
    try:
        e.run(stop_payload(), "--complete")
        assert e.body.get("decision") == "block", e.body
        assert WATCH_COMMAND in e.body["reason"] and "cov-proof-t7" in e.body["reason"], e.body
    finally:
        worker.kill()


def test_stop_does_not_block_a_roster_a_session_watcher_covers():
    e = Env()
    worker = _live_roster(e, SID)
    try:
        e.watch()
        e.run(stop_payload(), "--complete")
        assert "decision" not in e.body, e.body
    finally:
        worker.kill()


def test_stop_does_not_block_a_peer_sessions_roster():
    e = Env()
    worker = _live_roster(e, "peer-session-0000-0000-0000-000000000000")
    try:
        e.run(stop_payload(), "--complete")
        assert "decision" not in e.body, e.body
    finally:
        worker.kill()


def _dispatch_table_module():
    hooks = os.path.join(CLAUDE_DIR, "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import dispatch_table
    return dispatch_table


def test_roster_gate_is_false_where_the_benchmark_suite_is_not_installed():
    dt = _dispatch_table_module()
    real_scoring = os.path.join(CLAUDE_DIR, "scripts", "benchmark_campaign", "scoring")
    saved_here, saved_path, saved_mod = dt.HERE, list(sys.path), sys.modules.pop("bench_lib", None)
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != os.path.normcase(real_scoring)]
    with tempfile.TemporaryDirectory() as tmp:
        dt.HERE = os.path.join(tmp, "hooks")
        try:
            assert dt._any_roster_pid_alive() is False
        finally:
            dt.HERE, sys.path[:] = saved_here, saved_path
            if saved_mod is not None:
                sys.modules["bench_lib"] = saved_mod


def test_a_failed_roster_read_is_reported_to_the_owner_not_only_stderr():
    dt = _dispatch_table_module()
    saved = dt._any_roster_pid_alive

    def broken():
        raise OSError("roster logs unreadable")
    dt._any_roster_pid_alive = broken
    try:
        reason, note = dt._unwatched_rosters_reason({"session_id": SID})
    finally:
        dt._any_roster_pid_alive = saved
    assert reason is None, reason
    assert note and "OSError" in note, note


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
