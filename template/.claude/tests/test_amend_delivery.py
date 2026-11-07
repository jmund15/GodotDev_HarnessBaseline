"""Proof for hooks/amend_delivery.py: a running job receives its brief's amendment from the harness.

Real PreToolUse, Stop and SubagentStop payloads on stdin against planted transcripts (a sidecar child's own, and a
Workflow agent's under the orchestrator's `subagents/workflows/<run>/`) in a temp root, with the hook state dir
redirected there. Writes nothing outside it.

    python3 .claude/tests/test_amend_delivery.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading

CLAUDE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(CLAUDE, "hooks", "amend_delivery.py")
FAILURES = []
ROOT = tempfile.mkdtemp(prefix="amend_delivery_proof_")
STATE = os.path.join(ROOT, "state")
BRIEF = os.path.join(ROOT, "brief.md").replace("\\", "/")
AMEND = BRIEF + ".amend.md"
LINE = ("Amendments: the harness delivers %s to you when your parent writes or changes it; a delivered amendment "
        "overrides the brief, and your report names what you applied.")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def transcript(path, prompt, preamble=True):
    """Record shapes cut from captured transcripts, values trimmed: a `claude -p` sidecar child's (session-owner and
    hook_success rows before its prompt record) and a Workflow agent's (`agentId`, prompt record first, every
    computed line indented)."""
    rows = []
    if preamble:
        rows.append({"type": "session-owner"})
        rows.append({"parentUuid": None, "isSidechain": False, "type": "attachment",
                     "attachment": {"type": "hook_success", "hookName": "SessionStart:startup", "content": "x"}})
        rows.append({"parentUuid": None, "isSidechain": False, "promptId": "p1", "type": "user",
                     "message": {"role": "user", "content": prompt}, "promptSource": "sdk", "userType": "external"})
    else:
        rows.append({"parentUuid": None, "isSidechain": True, "promptId": "p1", "agentId": "a", "type": "user",
                     "message": {"role": "user", "content": prompt}, "userType": "external"})
    rows.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}})
    write(path, "".join(json.dumps(r) + "\n" for r in rows))
    return path


SIDECAR_TX = transcript(os.path.join(ROOT, "child", "sc-1.jsonl"), "# Task\nDo the work.\n\n" + LINE % AMEND)
ORCH_TX = transcript(os.path.join(ROOT, "orch", "orch-1.jsonl"), "Orchestrate.")
AGENT_TX = transcript(os.path.join(ROOT, "orch", "orch-1", "subagents", "workflows", "wf_1", "agent-a1.jsonl"),
                      "[Workflow harness — computed task] follows:\n  Your full task brief is at: %s\n  %s"
                      % (BRIEF, LINE % AMEND), preamble=False)
AGENT2_TX = transcript(os.path.join(ROOT, "orch", "orch-1", "subagents", "workflows", "wf_1", "agent-a2.jsonl"),
                       "  " + LINE % AMEND, preamble=False)
PLAIN_TX = transcript(os.path.join(ROOT, "plain", "p-1.jsonl"), "Fix the bug.")
QUOTED_TX = transcript(os.path.join(ROOT, "quoted", "q-1.jsonl"),
                       "The old job used: " + LINE % (ROOT + "/other.md.amend.md") + "\n\n" + LINE % AMEND)
REL_TX = transcript(os.path.join(ROOT, "rel", "r-1.jsonl"), LINE % "rel/brief.md.amend.md")


def run(payload):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", timeout=30, env=env)
    return r.returncode, r.stdout, r.stderr


def pre(tx, session="sc-1", agent=None, cwd=ROOT, tool="Read"):
    payload = {"session_id": session, "transcript_path": tx, "cwd": cwd, "hook_event_name": "PreToolUse",
               "tool_name": tool, "tool_input": {}}
    if agent:
        payload.update(agent_id=agent, agent_type="workflow-subagent")
    return run(payload)


def context(out):
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        return ""


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:400]))


# ---- PreToolUse delivery on a sidecar child ---------------------------------------------------------------------
rc, out, err = pre(SIDECAR_TX)
check("a job whose amendment does not exist yet sees nothing", rc == 0 and out.strip() == "", out + err)

write(AMEND, "Amendment one: also build row R7.\nSecond line keeps its place.\n")
rc, out, err = pre(SIDECAR_TX, tool="mcp__ai-worker__read_files")
ctx = context(out)
check("an amendment written mid-run reaches the job at its next tool call, any tool, with its full text",
      rc == 0 and "also build row R7." in ctx and "Second line keeps its place." in ctx and AMEND in ctx, out + err)

rc, out, err = pre(SIDECAR_TX)
check("a delivered amendment is not repeated at the next call", rc == 0 and out.strip() == "", out + err)

write(AMEND, "Amendment two: drop R7, build R8.\n")
rc, out, err = pre(SIDECAR_TX, tool="Bash")
ctx = context(out)
check("a changed amendment reaches the job again, marked as a revision, with its new text",
      rc == 0 and "drop R7, build R8." in ctx and "also build row R7." not in ctx and "revis" in ctx.lower(), out + err)

# ---- Workflow agent: keyed by agent_id, its own transcript under the orchestrator's session ----------------------
rc, out, err = pre(ORCH_TX, session="orch-1", agent="a1")
check("a Workflow agent receives its amendment through its own transcript (agent_id)",
      rc == 0 and "drop R7, build R8." in context(out), out + err)

rc, out, err = pre(ORCH_TX, session="orch-1", agent="a2")
check("a second job on the same brief holds its own receipt and receives it too",
      rc == 0 and "drop R7, build R8." in context(out), out + err)

rc, out, err = pre(ORCH_TX, session="orch-1")
check("the orchestrator itself, whose prompt names no amendment, sees nothing", rc == 0 and out.strip() == "", out + err)

rc, out, err = pre(PLAIN_TX, session="p-1")
check("a job with no amendment line sees nothing", rc == 0 and out.strip() == "", out + err)

# ---- key parsing --------------------------------------------------------------------------------------------------
write(ROOT + "/other.md.amend.md", "WRONG FILE\n")
rc, out, err = pre(QUOTED_TX, session="q-1")
check("a brief that quotes another job's line is keyed by its own (last) line",
      rc == 0 and "drop R7, build R8." in context(out) and "WRONG FILE" not in context(out), out + err)

write(os.path.join(ROOT, "rel", "brief.md.amend.md"), "Relative amendment text.\n")
rc, out, err = pre(REL_TX, session="r-1", cwd=os.path.join(ROOT, "rel", ".."))
check("a relative amendment path resolves against the job's cwd",
      rc == 0 and "Relative amendment text." in context(out), out + err)

# ---- parallel tool calls deliver once ------------------------------------------------------------------------------
write(AMEND, "Amendment three: parallel.\n")
outs = []
threads = [threading.Thread(target=lambda: outs.append(pre(SIDECAR_TX))) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
check("parallel tool calls of one job deliver one revision once",
      sum(1 for _, o, _ in outs if "parallel." in context(o)) == 1, outs)

# ---- an amendment past the client's 10,000-character hook-text cap ---------------------------------------------------
long_lines = ["Long line %04d pads this amendment past the hook text cap." % i for i in range(300)]
write(AMEND, "Amendment long: first line.\n" + "\n".join(long_lines) + "\nLast line LONG-END.\n")
rc, out, err = pre(SIDECAR_TX)
ctx = context(out)
tail = re.search(r"from line (\d+)", ctx)
check("an amendment past the cap arrives inline up to 10,000 characters, naming its file and the line to continue from",
      rc == 0 and 0 < len(ctx) <= 10000 and "Amendment long: first line." in ctx and AMEND in ctx and bool(tail)
      and "LONG-END" not in ctx, (len(ctx), ctx[-400:]))
check("the inline part ends on a whole line before the continuation line",
      bool(tail) and ("Long line %04d" % (int(tail.group(1)) - 3)) in ctx and ("Long line %04d" % (int(tail.group(1)) - 2)) not in ctx,
      ctx[-400:])

# ---- Stop side ------------------------------------------------------------------------------------------------------
write(AMEND, "Amendment four: written after the last tool call.\n")
rc, out, err = run({"session_id": "sc-1", "transcript_path": SIDECAR_TX, "cwd": ROOT, "hook_event_name": "Stop",
                    "stop_hook_active": False})
try:
    stop = json.loads(out)
except ValueError:
    stop = {}
check("a job cannot end with an amendment it was never given: Stop blocks with the full text",
      rc == 0 and stop.get("decision") == "block" and "written after the last tool call." in stop.get("reason", ""),
      out + err)

rc, out, err = run({"session_id": "sc-1", "transcript_path": SIDECAR_TX, "cwd": ROOT, "hook_event_name": "Stop",
                    "stop_hook_active": True})
check("once the stop delivered it, the next stop passes", rc == 0 and out.strip() == "", out + err)

rc, out, err = run({"session_id": "orch-1", "transcript_path": ORCH_TX, "cwd": ROOT, "hook_event_name": "SubagentStop",
                    "agent_id": "a1", "agent_transcript_path": AGENT_TX, "stop_hook_active": False})
try:
    stop = json.loads(out)
except ValueError:
    stop = {}
check("a Workflow agent's SubagentStop blocks on an undelivered revision with its full text",
      rc == 0 and stop.get("decision") == "block" and "written after the last tool call." in stop.get("reason", ""),
      out + err)

rc, out, err = run({"session_id": "p-1", "transcript_path": PLAIN_TX, "cwd": ROOT, "hook_event_name": "Stop"})
check("a job with no amendment stops freely", rc == 0 and out.strip() == "", out + err)

os.remove(AMEND)
rc, out, err = run({"session_id": "orch-1", "transcript_path": ORCH_TX, "cwd": ROOT, "hook_event_name": "SubagentStop",
                    "agent_id": "a2", "agent_transcript_path": AGENT2_TX})
check("a removed amendment blocks nothing", rc == 0 and out.strip() == "", out + err)

# ---- receipts the parent reads -------------------------------------------------------------------------------------
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
os.environ["HARNESS_HOOK_STATE_DIR"] = STATE
import amend_delivery  # noqa: E402

shas = amend_delivery.delivered_shas(AMEND.replace("/", "\\"))
check("the parent reads each job's latest delivered revision by the amendment path, in either slash spelling",
      amend_delivery.text_sha("Amendment four: written after the last tool call.\n") in shas
      and amend_delivery.text_sha("Amendment one: also build row R7.\nSecond line keeps its place.\n") not in shas, shas)

# ---- failure posture ------------------------------------------------------------------------------------------------
r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True, encoding="utf-8",
                   timeout=30, env=dict(os.environ, HARNESS_HOOK_STATE_DIR=STATE))
check("an unreadable payload blocks nothing", r.returncode == 0 and '"block"' not in r.stdout, r.stdout + r.stderr)

# ---- every engine's prompt line carries the key the hook parses -----------------------------------------------------
HEAD = "Amendments: the harness delivers "
for rel in ("workflows/dispatch.js", "workflows/dispatch_chains.js", "workflows/review_fanout.js",
            "scripts/lib/sidecar_common.sh"):
    with open(os.path.join(CLAUDE, rel), encoding="utf-8") as fh:
        src = fh.read()
    check(rel + " ends an author job's prompt with the key line and no instruction to poll",
          HEAD in src and " to you when your parent writes or changes it" in src
          and not re.search(r"at each item and before returning, Read", src), rel)
check("the hook parses the exact line the engines write",
      amend_delivery.amend_path_in(LINE % "C:\\Users\\x y\\brief.md.amend.md") == "C:\\Users\\x y\\brief.md.amend.md")

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
