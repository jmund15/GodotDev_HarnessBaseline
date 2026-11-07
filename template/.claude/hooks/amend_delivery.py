"""PreToolUse, Stop and SubagentStop: deliver a parent's amendment to the running job it amends.

A parent corrects a running author job by writing `<brief>.amend.md` (`orchestration` reference `integration.md`).
Each engine ends an author job's prompt with the line `amend_path_in` parses, naming that file; the line is the
job's key on every engine. A sidecar child is a `claude` process, so payload `transcript_path` is its own
transcript. A Workflow or Agent-tool child (payload `agent_id`) shares its parent's session and transcript path; its
own transcript is `<session>/subagents/**/agent-<agent_id>.jsonl`. The key is read from the job's first prompt
record, last match, so a brief quoting another job's line keeps its own.

PreToolUse (every tool): when the amendment file holds text and this job has no receipt for that text's sha, the
full text arrives as `additionalContext` and a receipt is claimed under a file lock, so parallel calls deliver once.
A changed file is a new revision and arrives again. An amendment past the client's hook-text cap (`HOOK_TEXT_CAP`)
arrives in whole lines up to the cap, ending with the line to Read on from: the client would otherwise show only a
2,000-character preview. Stop (sidecar child) and SubagentStop (Workflow or Agent-tool
child): an undelivered revision blocks the stop with the full text as its reason, so a job never ends with an
amendment it was never given; the receipt is claimed, so the next stop passes.

Receipts: `<state dir>/amend_receipts/<sha of the amendment path>.json`, `{"amend": path, "jobs": {job: {"sha",
"at"}}}`, keyed by agent_id or session_id. One file per amended brief: amendments are parent-paced, so the store
grows with them. `tools/dispatch_result.py` reads `delivered_shas` for its `AMEND UNREAD` check.

Fail posture: process class, never a floor. A failure delivers nothing and blocks nothing (exit 0); on PreToolUse
it reports the lost delivery once per session through `additionalContext`.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"Amendments: the harness delivers (.+?\.amend\.md) to you when your parent writes or changes it")
HEAD_LINES = 50
# The client's cap on hook text it injects (additionalContext, stdout); past it the client shows a 2,000-character
# preview and a saved-file path instead (code.claude.com/docs/en/hooks; observed on a 12,030-character amendment).
HOOK_TEXT_CAP = 10_000
# Opens a blocked stop's reason. `tools/dispatch_result.py` `answered_after_report` reads it: the reply after it is the
# job's full report, re-issued as the reason asks, not a trailing answer.
STOP_HEAD = "[amendment] Your parent amended your brief after your last tool call"


def _state():
    """`_hook_state`, imported on use: a job whose prompt names no amendment, the common case, never needs it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _hook_state
    return _hook_state


def amend_path_in(text):
    """The amendment path the last engine line in `text` names, else None."""
    found = LINE_RE.findall(text or "")
    return found[-1].strip() if found else None


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(path):
    p = str(path).replace("\\", "/")
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m and os.name == "nt":
        p = m.group(1) + ":/" + m.group(2)
    return os.path.normcase(os.path.normpath(p))


def receipt_path(amend):
    key = hashlib.sha256(_norm(amend).encode("utf-8")).hexdigest()[:16]
    return os.path.join(_state().state_dir(), "amend_receipts", key + ".json")


def delivered_shas(amend):
    """Every revision sha any job holds a receipt for."""
    state = _state().read_json_salvage(receipt_path(amend))
    return {row.get("sha") for row in (state.get("jobs") or {}).values() if isinstance(row, dict)}


def _prompt_text(record):
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _job_transcript(payload):
    agent = str(payload.get("agent_id") or "").strip()
    if not agent:
        return payload.get("transcript_path") or ""
    given = payload.get("agent_transcript_path")
    if given and os.path.isfile(given):
        return given
    parent = str(payload.get("transcript_path") or "")
    if not parent.endswith(".jsonl"):
        return ""
    base = glob.escape(parent[:-len(".jsonl")])
    name = "agent-%s.jsonl" % glob.escape(agent)
    for pattern in (os.path.join(base, "subagents", name), os.path.join(base, "subagents", "workflows", "*", name)):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return ""


def job_amend_path(payload):
    """The amendment file the job's own first prompt names, absolute, else None."""
    transcript = _job_transcript(payload)
    if not transcript or not os.path.isfile(transcript):
        return None
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for _ in range(HEAD_LINES):
            line = fh.readline()
            if not line:
                return None
            if '"type":"user"' not in line.replace(" ", ""):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") != "user" or record.get("isMeta"):
                continue
            path = amend_path_in(_prompt_text(record))
            if not path:
                return None
            if not os.path.isabs(path.replace("\\", "/")) and not re.match(r"^[a-zA-Z]:", path):
                path = os.path.join(payload.get("cwd") or os.getcwd(), path)
            return path
    return None


def claim(payload, amend):
    """(text, revised) when this job holds no receipt for the amendment's current text, after claiming it; else None."""
    try:
        with open(amend, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    if not text.strip():
        return None
    sha = text_sha(text)
    job = str(payload.get("agent_id") or "").strip() or str(payload.get("session_id") or "")

    def update(state):
        state["amend"] = amend.replace("\\", "/")
        jobs = state.setdefault("jobs", {})
        prior = jobs.get(job) or {}
        if prior.get("sha") == sha:
            return None
        jobs[job] = {"sha": sha, "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return bool(prior.get("sha"))

    path = receipt_path(amend)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    written, revised = _state().update_json_locked(path, update, raising=True)
    if not written or revised is None:
        return None
    return text, revised, sha


def message(amend, text, revised, sha, at_stop):
    rev = sha[:8]
    if at_stop:
        head = (STOP_HEAD + ": %s (revision %s). Apply it now; it overrides the brief where they differ. Then end with "
                "your full report again, naming what you applied." % (amend, rev))
    elif revised:
        head = ("[amendment] Your parent revised the amendment to your brief: %s (revision %s). This text replaces the "
                "earlier revision and overrides the brief where they differ; your report names what you applied."
                % (amend, rev))
    else:
        head = ("[amendment] Your parent amended your brief: %s (revision %s). It overrides the brief where they "
                "differ; your report names what you applied." % (amend, rev))
    whole = head + " Full text:\n\n" + text
    if len(whole) <= HOOK_TEXT_CAP:
        return whole
    lines = text.splitlines(keepends=True)
    tail = ("\n\n[amendment] The text above stops at line %d of %d: the client caps hook text at %d characters. "
            "Read %s from line %d to the end before your next action; it is the rest of this amendment.")
    widest = len(tail % (len(lines), len(lines), HOOK_TEXT_CAP, amend, len(lines)))
    room = HOOK_TEXT_CAP - len(head) - len(" Text:\n\n") - widest
    kept, size = 0, 0
    while kept < len(lines) and size + len(lines[kept]) <= room:
        size += len(lines[kept])
        kept += 1
    return (head + " Text:\n\n" + "".join(lines[:kept]).rstrip("\n")
            + tail % (kept, len(lines), HOOK_TEXT_CAP, amend, kept + 1))


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    try:
        amend = job_amend_path(payload)
        got = claim(payload, amend) if amend else None
    except Exception as exc:  # noqa: BLE001
        if event == "PreToolUse" and _state().fire_once(payload.get("session_id"), "amend_delivery_failed"):
            emit_pre("[amendment] The amendment delivery check failed (%s: %s): an amendment your parent writes may "
                     "not reach you; repair .claude/hooks/amend_delivery.py." % (type(exc).__name__, exc))
        return 0
    if not got:
        return 0
    text, revised, sha = got
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if event in ("Stop", "SubagentStop"):
        print(json.dumps({"decision": "block", "reason": message(amend, text, revised, sha, True)}))
    elif event == "PreToolUse":
        emit_pre(message(amend, text, revised, sha, False))
    return 0


def emit_pre(text):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))


if __name__ == "__main__":
    sys.exit(main())
