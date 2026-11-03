#!/usr/bin/env python3
"""Proof: the owner's opening goal survives every compaction whole, on a session shaped like f3a34186.

Session f3a34186 (2026-11-01/02, client 2.1.316) compacted 8 times and lost its goal from summary 6 on. The
causes this proof plants, each from that session's evidence:

- Its auto-compact window (500k on a 1M model) put the parent's auto compaction at 46% of the status-line
  window, under the old subagent floor (50%): the ledger and the backup took the parent for a subagent and
  exited silently, and the subagent rail told the parent to "Continue from its Remaining list".
- A subagent compacted while the parent sat idle just after its own compaction; the parent's last recorded
  call predated that compaction, so the ledger took the subagent's compaction for the parent's.
- The client's `api-request-blob` rows (copies of each API request body) were read as owner messages: 51 of
  124, 80% of the bytes, crowding the opening goal down to a 160-character clip.
- The anchor named a summary "Owner directives" section that existed in no summary from 6 on.

Rows are cut from that session's transcript (`fixtures/compaction_intent/rows.jsonl`, texts replaced). Each
case runs the real hooks as processes; an exit outside {0} or a traceback is a CRASH, never a pass.

    python3 .claude/tests/test_compaction_intent.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
FIXTURE = HERE / "fixtures" / "compaction_intent" / "rows.jsonl"
SID = "f3a34186-0000-4000-8000-intentproof0"
HOOK_OUTPUT_LIMIT = 10_000

GOAL_A = "GOAL-A-START " + " ".join(f"set-in-stone-{i}" for i in range(180))[:2850] + " GOAL-A-END"
GOAL_B = "GOAL-B-START " + " ".join(f"idea-brainstorm-{i}" for i in range(250))[:4470] + " GOAL-B-END"
MID_TURN = ("The user sent a new message while you were working:\n{text}\n\nThis is how Claude Code surfaces messages "
            "the user sends mid-turn — within the running turn, often alongside the next tool result, rather than as a "
            "separate conversation turn. Address the message above as you continue this turn.")
REAL_GAUGE = {"last": "465:461537:2", "context_pct": 46}

TEMPLATES = {}
for line in FIXTURE.read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    TEMPLATES[item["kind"]] = item["row"]

_clock = [time.time() - 86400]


def _fill(value, text):
    if isinstance(value, str):
        return value.replace("{TEXT}", text)
    if isinstance(value, list):
        return [_fill(v, text) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, text) for k, v in value.items()}
    return value


def row(kind, text="", **over):
    _clock[0] += 30
    r = _fill(copy.deepcopy(TEMPLATES[kind]), text)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(_clock[0]))
    if "timestamp" in r:
        r["timestamp"] = stamp
    if isinstance(r.get("attachment"), dict) and "timestamp" in r["attachment"]:
        r["attachment"]["timestamp"] = stamp
    r["uuid"] = f"u-{_clock[0]:.0f}-{kind}"
    for key in ("sessionId",):
        if key in r:
            r[key] = SID
    r.update(over)
    return r


def assistant(text, tokens, sidechain=False):
    r = row("assistant", text)
    r["message"]["usage"] = {"input_tokens": 2, "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": tokens - 2, "output_tokens": 300}
    r["isSidechain"] = sidechain
    return r


def summary(text):
    return row("summary", text)


def boundary(trigger, pre, post):
    r = row("boundary")
    r["compactMetadata"] = {"trigger": trigger, "preTokens": pre, "postTokens": post, "durationMs": 40000}
    return r


def session_rows():
    """The parent: two long opening goal messages, then 70 later messages with mid-turn queued messages and the
    client's request-body copies of them, two earlier compactions (one summary with the section, one without),
    and a last recorded call at the real gauge's size."""
    rows = [row("typed", GOAL_A), assistant("On it.", 40_000), row("typed", "Sure, go."),
            assistant("Starting.", 60_000), row("typed", GOAL_B)]
    for i in range(70):
        rows.append(assistant(f"Step {i} done; next I check the frames.", 80_000 + i * 5000))
        later = f"LATER-{i} " + ("the owner steers this step in detail " * (8 + i % 12))
        if i % 5 == 0:
            rows.append(row("queued", later))
            rows.append(row("blob", MID_TURN.format(text=later)))
        else:
            rows.append(row("typed", later))
        if i % 9 == 0:
            rows.append(row("blob", "[Image: original 2388x1744, displayed at 2000x1461. Multiply coordinates by 1.19 "
                                    "to map to original image.]"))
            rows.append(row("blob", "Tool loaded."))
            rows.append(row("image"))
        if i == 20:
            rows += [boundary("manual", 447_287, 23_970),
                     summary("Summary 1.\n## Owner directives\n- U1 — holds — the goal\n## Pending Tasks\n- go")]
        if i == 45:
            rows += [boundary("auto", 466_650, 39_005), summary("Summary 2.\n## Pending Tasks\n- go on")]
    rows.append(assistant("Checking the next frame.", 462_004))
    rows.append(row("typed", "LATEST: keep going on the goal."))
    return rows


def write(path, rows, pad_after=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def anchor_registrations():
    """The argument lists of each `compact_directive_anchor.py` command registered for SessionStart(compact).

    A project reads its composed settings.json; the baseline ships only settings.base.json, which holds the registration."""
    composed = HERE.parent / "settings.json"
    settings = json.loads((composed if composed.exists() else HERE.parent / "settings.base.json").read_text(encoding="utf-8"))
    found = []
    for group in settings.get("hooks", {}).get("SessionStart", []):
        if group.get("matcher") != "compact":
            continue
        for hook in group.get("hooks", []):
            command = hook.get("command", "")
            if "compact_directive_anchor.py" in command:
                found.append(command.split("compact_directive_anchor.py", 1)[1].strip(' "').split())
    return found


ANCHOR_REGISTRATIONS = anchor_registrations()


class World:
    def __init__(self, root):
        self.root = Path(root)
        self.transcript = self.root / "projects" / f"{SID}.jsonl"
        self.project = self.root / "project"
        self.project.mkdir(parents=True)
        (self.root / "gauge").mkdir()
        (self.root / "gauge" / f"cc-cachestat-{SID}.json").write_text(json.dumps(REAL_GAUGE), encoding="utf-8")

    def env(self):
        env = {k: v for k, v in os.environ.items() if k not in (
            "CLAUDE_CODE_SIDECAR_PROMPT_FILE", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")}
        env.update(HARNESS_HOOK_STATE_DIR=str(self.root / "state"), CC_CACHESTAT_DIR=str(self.root / "gauge"),
                   CLAUDE_PROJECT_DIR=str(self.project), HARNESS_PENDING_COUNT="0",
                   HARNESS_TASK_RECORD_DIR=str(self.root / "tasks"), PYTHONIOENCODING="utf-8")
        return env

    def run(self, hook, payload, *args):
        payload = dict(payload, session_id=SID, transcript_path=str(self.transcript), cwd=str(self.project))
        r = subprocess.run([sys.executable, str(HOOKS / hook), *args], input=json.dumps(payload).encode("utf-8"),
                           capture_output=True, env=self.env(), cwd=str(self.project), timeout=180)
        out = r.stdout.decode("utf-8", errors="replace")
        err = r.stderr.decode("utf-8", errors="replace")
        return out, r.returncode != 0 or "Traceback" in err, err

    def anchor(self, payload):
        """(each registered anchor part's output, any crashed): every SessionStart(compact) registration of the
        anchor in settings.json, run as the client runs them, each its own hook output."""
        parts, crashed = [], False
        for args in ANCHOR_REGISTRATIONS:
            out, bad, _ = self.run("compact_directive_anchor.py", payload, *args)
            parts.append(out)
            crashed = crashed or bad
        return parts, crashed

    def subagent(self, tokens):
        """A Workflow agent's transcript, written just now, at `tokens` of context."""
        path = self.transcript.with_suffix("") / "subagents" / "workflows" / "wf_proof-1" / "agent-aproof.jsonl"
        write(path, [assistant("lane work", tokens, sidechain=True),
                     row("typed", "tool result", isSidechain=True)])
        written = os.path.getmtime(path)
        if os.path.getmtime(self.transcript) > written - 2:
            os.utime(self.transcript, (written - 2, written - 2))
        return path


PRE_AUTO = {"hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": None}
START = {"hook_event_name": "SessionStart", "source": "compact"}


def main():
    cases = []

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        write(world.transcript, session_rows())

        ledger, crashed, err = world.run("compact_directive_ledger.py", PRE_AUTO)
        cases.append(("parent auto PreCompact at 46% of a 1M window: no crash", not crashed))
        cases.append(("parent auto PreCompact: the ledger asks the summary for Owner directives",
                      "Owner directives" in ledger))
        rail, crashed, _ = world.run("subagent_compaction_rail.py", PRE_AUTO)
        cases.append(("parent auto PreCompact: the subagent rail stays silent", rail == "" and not crashed))
        world.run("transcript_backup.py", PRE_AUTO)
        backups = list((world.project / "logs" / "transcript_backups").glob("*.jsonl")) \
            if (world.project / "logs" / "transcript_backups").exists() else []
        cases.append(("parent auto PreCompact: the transcript is backed up", len(backups) == 1))

        resume, crashed, _ = world.run("subagent_compaction_rail.py", START)
        cases.append(("parent SessionStart(compact): no subagent resume line", resume == "" and not crashed))

        cases.append(("the anchor is registered twice for SessionStart(compact): the opening part and the rest",
                      sorted(ANCHOR_REGISTRATIONS) == [[], ["--opening"]]))
        parts, crashed = world.anchor(START)
        anchor = "\n".join(parts)
        cases.append(("anchor: no crash", not crashed))
        cases.append(("anchor: the first opening message prints whole", GOAL_A in anchor))
        cases.append(("anchor: the second opening message prints whole", GOAL_B in anchor))
        cases.append(("anchor: the newest message is present", "LATEST: keep going on the goal." in anchor))
        cases.append(("anchor: each part inside the client's hook-output limit",
                      all(len(part) <= HOOK_OUTPUT_LIMIT for part in parts)))
        cases.append(("anchor: no opening message prints twice", anchor.count("GOAL-A-START") == 1))
        cases.append(("anchor: no request-body copy is listed as an owner message",
                      "sent a new message while you were working" not in anchor
                      and "[Image: original" not in anchor and "Tool loaded." not in anchor))
        cases.append(("anchor: never asserts the summary holds an Owner directives section",
                      "its Owner directives section gives each ID a status" not in anchor))
        full = world.project / "logs" / f"compact_directives_{SID[:8]}.md"
        text = full.read_text(encoding="utf-8") if full.exists() else ""
        cases.append(("uncapped file: each queued message appears once",
                      text.count("LATER-5 ") == 1 and "sent a new message while you were working" not in text))

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        tail = [boundary("auto", 434_402, 41_135), summary("Summary 3.\n## Pending Tasks\n- go on")]
        tail += [row("image") for _ in range(1200)]
        write(world.transcript, session_rows() + tail)
        world.subagent(469_371)
        ledger, crashed, _ = world.run("compact_directive_ledger.py", PRE_AUTO)
        cases.append(("subagent auto PreCompact while the parent idles after its own compaction: no crash",
                      not crashed))
        cases.append(("subagent auto PreCompact: no parent ledger in the subagent's summary", ledger.strip() == ""))
        log = world.project / "logs" / "compaction_hooks.jsonl"
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        cases.append(("subagent auto PreCompact: the ledger logs its skip with the payload's keys",
                      any(r.get("hook") == "compact_directive_ledger" and r.get("skipped") == "subagent"
                          and "trigger" in (r.get("payload_keys") or []) for r in rows)))
        parts, crashed = world.anchor(dict(START, agent_id="aproof"))
        cases.append(("a SessionStart(compact) carrying agent_id gets no parent owner messages",
                      parts and all(part == "" for part in parts) and not crashed))
        rail, _, _ = world.run("subagent_compaction_rail.py", PRE_AUTO)
        cases.append(("subagent auto PreCompact: the subagent rail prints", '"Task" section' in rail))

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        write(world.transcript, session_rows())
        quiet = time.time() - 130
        os.utime(world.transcript, (quiet, quiet))
        world.subagent(238_043)
        ledger, crashed, _ = world.run("compact_directive_ledger.py", PRE_AUTO)
        cases.append(("a smaller Workflow agent compacts while the parent has been quiet 130 s: no parent ledger",
                      ledger.strip() == "" and not crashed))

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        write(world.transcript, session_rows())
        world.subagent(120_000)
        ledger, crashed, _ = world.run("compact_directive_ledger.py", PRE_AUTO)
        cases.append(("parent auto PreCompact beside a smaller live subagent: the ledger runs",
                      "Owner directives" in ledger and not crashed))

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        write(world.transcript, session_rows())
        parts, crashed = world.anchor(START)
        cases.append(("compaction that fired no PreCompact: the anchor still prints both opening messages whole",
                      GOAL_A in "\n".join(parts) and GOAL_B in "\n".join(parts) and not crashed))

    with tempfile.TemporaryDirectory() as tmp:
        world = World(tmp)
        huge = "HUGE-START " + "pasted material " * 1200 + " HUGE-END"
        rows = session_rows()
        rows[0] = row("typed", huge)
        write(world.transcript, rows)
        parts, crashed = world.anchor(START)
        anchor = "\n".join(parts)
        cases.append(("an opening goal larger than the limit: each part stays inside it",
                      all(len(part) <= HOOK_OUTPUT_LIMIT for part in parts) and not crashed))
        cases.append(("an opening goal larger than the limit: a required read of the opening names it",
                      "before any other step" in anchor and "--show" in anchor and "U1" in anchor))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
