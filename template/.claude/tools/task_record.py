#!/usr/bin/env python3
"""One durable record per task: the drive state that must survive compaction and resume.

The record is the mechanism behind orchestration §10 ("keep a small task state"): requirements and
exclusions, decisions with hashed evidence, scope and owner, active phase, active jobs, verification
rows, accepted artifacts and the next action. The parent writes it at phase boundaries and on each
consumed result; `hooks/compact_directive_anchor.py` re-injects a bounded block of it after a
compaction and `session_digest.py --brief` prints the same block.

Store: one JSON file per task at `<project>/.claude/logs/tasks/<task_id>.json` (env override
`HARNESS_TASK_RECORD_DIR` for proofs). Every write goes through `_hook_state.update_json_locked`, so
concurrent writers from several hooks or sessions compose. A record is capped at MAX_BYTES; a write
that would exceed it is refused with the offending field named, never truncated (a capped store that
admits everything evicts the early rows that matter). Active jobs carry run ids attested by a
Workflow record or a sidecar `-R` record, never a plan.

`check` rehashes every evidence path (STALE / MISSING / fresh) and, with `--rerun`, executes each
decision's stored reproduction command (REPRO-FAIL on a non-zero exit). A decision without a
reproduction is reported `hash-only`: byte-unchanged evidence is freshness, not a re-verified premise.

    task_record.py open <task_id> --session <sid> --title <t>
    task_record.py set <task_id> [--phase P] [--next N] [--owner O]
    task_record.py add <task_id> requirement|exclusion|decision|artifact "<text>" [--evidence P ...] [--reproduction CMD]
    task_record.py job <task_id> --run RID --label L --source workflow|sidecar --state S
    task_record.py verify <task_id> --check C --result pass|fail|unverified [--evidence P ...]
    task_record.py archive <task_id> <decision-id>
    task_record.py show <task_id> | active --session <sid> | block <task_id> [--max-bytes N]
    task_record.py check <task_id> [--rerun]
"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "hooks"),):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import _hook_state  # noqa: E402

# The per-turn cost is bounded by BLOCK_BYTES (the injected block), never by the file: the file is read
# whole only by `show` and the digest. MAX_BYTES therefore guards against a dump, not context. Measured
# 2026-09-15: a decision row costs ~95 B beyond its text and a job row ~110 B; a one-day drive reached
# 8,201 B at 9 decisions and 6 jobs, so 8,192 refused a closeout write. 32 KiB holds a multi-day drive.
MAX_BYTES = 32768
BLOCK_BYTES = int(os.environ.get("HARNESS_TASK_RECORD_BLOCK_BYTES") or 2048)
ITEM_KINDS = {"requirement": "requirements", "exclusion": "exclusions", "decision": "decisions",
              "artifact": "accepted_artifacts"}
SHOW = "python3 .claude/tools/task_record.py show %s"


class RecordTooLarge(Exception):
    pass


def record_dir():
    override = os.environ.get("HARNESS_TASK_RECORD_DIR")
    if override:
        return override
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, ".claude", "logs", "tasks")


def path_for(task_id):
    return os.path.join(record_dir(), task_id + ".json")


def now():
    # Microseconds: `active` orders records by this stamp, and two writes within one second must not tie.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def evidence_rows(paths):
    return [{"path": p, "sha256": sha256(p)} for p in (paths or [])]


def load(task_id):
    rec = _hook_state.read_json_salvage(path_for(task_id))
    return rec or None


def _mutate(task_id, mutation):
    """Apply `mutation(record)` under the lock; refuse (unchanged) when the result exceeds MAX_BYTES.

    `update_json_locked` swallows every exception the updater raises and reports `written=False`, so a
    usage error (`_require`, an unknown decision id) is captured here and re-raised as itself; only a
    genuine write failure surfaces as OSError."""
    error = {}
    caught = {}

    def updater(state):
        working = copy.deepcopy(state)
        try:
            result = mutation(working)
        except KeyError as exc:
            caught["exc"] = exc
            raise
        working["updated"] = now()
        raw = json.dumps(working, ensure_ascii=True)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            sizes = {k: len(json.dumps(v, ensure_ascii=True).encode("utf-8")) for k, v in working.items()}
            field = max(sizes, key=sizes.get)
            error["msg"] = ("record %s would be %d B, over the %d B cap; largest field is %s (%d B). Trim it "
                            "(archive a closed decision, move detail to an artifact path) and retry."
                            % (task_id, len(raw.encode("utf-8")), MAX_BYTES, field, sizes[field]))
            return None
        state.clear()
        state.update(working)
        return result

    os.makedirs(record_dir(), exist_ok=True)
    written, result = _hook_state.update_json_locked(path_for(task_id), updater)
    if error:
        raise RecordTooLarge(error["msg"])
    if not written:
        if "exc" in caught:
            raise caught["exc"]
        raise OSError("could not write %s" % path_for(task_id))
    return result


def open_record(task_id, session_id, title, owner=None):
    stamp = now()
    rec = {"task_id": task_id, "session_id": session_id, "title": title, "opened": stamp, "updated": stamp,
           "requirements": [], "exclusions": [], "decisions": [], "scope": {"owner": owner, "files": []},
           "phase": None, "active_jobs": [], "verification": [], "accepted_artifacts": [], "next_action": None,
           "archived": []}

    def mutation(state):
        state.clear()
        state.update(rec)
        return rec

    return _mutate(task_id, mutation)


def _require(state, task_id):
    if not state.get("task_id"):
        raise KeyError("no record for %s; open it first" % task_id)


def set_fields(task_id, phase=None, next_action=None, owner=None, files=None):
    def mutation(state):
        _require(state, task_id)
        if phase is not None:
            state["phase"] = phase
        if next_action is not None:
            state["next_action"] = next_action
        if owner is not None:
            state.setdefault("scope", {})["owner"] = owner
        if files is not None:
            state.setdefault("scope", {})["files"] = list(files)
        return state["phase"]

    return _mutate(task_id, mutation)


def add_item(task_id, kind, text, evidence=None, reproduction=None):
    field = ITEM_KINDS[kind]

    def mutation(state):
        _require(state, task_id)
        if kind == "decision":
            used = [d.get("id") for d in state.get("decisions", [])] + [d.get("id") for d in state.get("archived", [])]
            n = 1
            while "D%d" % n in used:
                n += 1
            row = {"id": "D%d" % n, "text": text, "evidence": evidence_rows(evidence), "at": now()}
            if reproduction:
                row["reproduction"] = reproduction
            state.setdefault("decisions", []).append(row)
            return row
        state.setdefault(field, []).append(text)
        return text

    return _mutate(task_id, mutation)


def add_job(task_id, run_id, label, source, state):
    def mutation(rec):
        _require(rec, task_id)
        jobs = rec.setdefault("active_jobs", [])
        for job in jobs:
            if job.get("run_id") == run_id and job.get("label") == label:
                job["state"] = state
                job["at"] = now()
                return job
        row = {"run_id": run_id, "label": label, "source": source, "state": state, "at": now()}
        jobs.append(row)
        return row

    return _mutate(task_id, mutation)


def add_verification(task_id, check, result, evidence=None):
    def mutation(state):
        _require(state, task_id)
        row = {"check": check, "result": result, "evidence": evidence_rows(evidence), "at": now()}
        state.setdefault("verification", []).append(row)
        return row

    return _mutate(task_id, mutation)


def archive_item(task_id, decision_id):
    def mutation(state):
        _require(state, task_id)
        keep, moved = [], None
        for d in state.get("decisions", []):
            if d.get("id") == decision_id:
                moved = d
            else:
                keep.append(d)
        if moved is None:
            raise KeyError("no decision %s" % decision_id)
        state["decisions"] = keep
        state.setdefault("archived", []).append({"id": moved["id"], "text": moved.get("text"), "at": now()})
        return moved

    return _mutate(task_id, mutation)


def _session_key(session_id):
    """Records are opened with either the full session id or the 8-char sid the hooks print; both name one session."""
    return (session_id or "")[:8].lower()


def active(session_id):
    key = _session_key(session_id)
    newest = None
    try:
        names = os.listdir(record_dir())
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = _hook_state.read_json_salvage(os.path.join(record_dir(), name))
        if not key or _session_key(rec.get("session_id")) != key:
            continue
        if newest is None or (rec.get("updated") or "") > (newest.get("updated") or ""):
            newest = rec
    return newest


def _apply_evidence_status(row, evidence):
    """MISSING when a path cannot be read, STALE when its hash moved; the first MISSING wins."""
    for ev in evidence:
        current = sha256(ev.get("path", ""))
        if current is None:
            row.update(status="MISSING", detail=ev.get("path"))
            return
        if current != ev.get("sha256"):
            row.update(status="STALE", detail=ev.get("path"))


def check_record(task_id, rerun=False):
    rec = load(task_id)
    if not rec:
        raise KeyError("no record for %s" % task_id)
    rows = []
    for d in rec.get("decisions", []):
        row = {"id": d.get("id"), "kind": "decision", "status": "fresh", "detail": None}
        _apply_evidence_status(row, d.get("evidence", []))
        if d.get("reproduction"):
            if rerun:
                r = subprocess.run(d["reproduction"], shell=True, capture_output=True, text=True, timeout=600)
                row["repro"] = "repro-ok" if r.returncode == 0 else "REPRO-FAIL"
            else:
                row["repro"] = "stored"
        else:
            row["repro"] = "hash-only"
        rows.append(row)
    for i, v in enumerate(rec.get("verification", [])):
        row = {"id": "V%d" % (i + 1), "kind": "verification", "status": "fresh", "detail": None}
        _apply_evidence_status(row, v.get("evidence", []))
        rows.append(row)
    return rows


def _clip(text, limit):
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def render_block(task_id, max_bytes=BLOCK_BYTES):
    """The bounded injection text. Requirements and exclusions stay whole; decisions, jobs and the last
    verification row are water-filled in that order; anything left out is named with the show command."""
    rec = load(task_id)
    if not rec:
        return ""
    size = lambda lines: len("\n".join(lines).encode("utf-8"))  # noqa: E731
    head = ["[task-record] %s phase=%s next=%s" % (task_id, rec.get("phase"), rec.get("next_action"))]
    head += ["- requirement: %s" % r for r in rec.get("requirements", [])]
    head += ["- exclusion: %s" % r for r in rec.get("exclusions", [])]
    footer_worst = "omitted: 9999 decisions, 9999 jobs, verification. Full record: " + SHOW % task_id
    budget = max_bytes - size(head) - len(footer_worst.encode("utf-8")) - 2
    decisions = ["- %s: %s" % (d.get("id"), d.get("text", "")) for d in rec.get("decisions", [])]
    jobs = ["- job %s %s %s" % (j.get("run_id"), j.get("label"), j.get("state")) for j in rec.get("active_jobs", [])]
    verification = rec.get("verification", [])
    last_v = ["- verified: %s → %s" % (verification[-1].get("check"), verification[-1].get("result"))] if verification else []
    kept, omitted = [], {"decisions": 0, "jobs": 0, "verification": 0}
    for name, rows in (("decisions", decisions), ("jobs", jobs), ("verification", last_v)):
        for row in rows:
            candidate = _clip(row, 200)
            cost = len(candidate.encode("utf-8")) + 1
            if cost <= budget:
                kept.append(candidate)
                budget -= cost
            else:
                omitted[name] += 1
    lines = head + kept
    if any(omitted.values()):
        lines.append("omitted: %d decisions, %d jobs%s. Full record: %s"
                     % (omitted["decisions"], omitted["jobs"], ", verification" if omitted["verification"] else "",
                        SHOW % task_id))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="One durable record per task.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open"); p.add_argument("task_id"); p.add_argument("--session", required=True)
    p.add_argument("--title", required=True); p.add_argument("--owner")
    p = sub.add_parser("set"); p.add_argument("task_id"); p.add_argument("--phase"); p.add_argument("--next")
    p.add_argument("--owner"); p.add_argument("--files", nargs="*")
    p = sub.add_parser("add"); p.add_argument("task_id"); p.add_argument("kind", choices=sorted(ITEM_KINDS))
    p.add_argument("text"); p.add_argument("--evidence", nargs="*"); p.add_argument("--reproduction")
    p = sub.add_parser("job"); p.add_argument("task_id"); p.add_argument("--run", required=True)
    p.add_argument("--label", required=True); p.add_argument("--source", choices=("workflow", "sidecar"), required=True)
    p.add_argument("--state", required=True)
    p = sub.add_parser("verify"); p.add_argument("task_id"); p.add_argument("--check", required=True)
    p.add_argument("--result", choices=("pass", "fail", "unverified"), required=True); p.add_argument("--evidence", nargs="*")
    p = sub.add_parser("archive"); p.add_argument("task_id"); p.add_argument("decision_id")
    p = sub.add_parser("show"); p.add_argument("task_id")
    p = sub.add_parser("active"); p.add_argument("--session", required=True)
    p = sub.add_parser("block"); p.add_argument("task_id"); p.add_argument("--max-bytes", type=int, default=BLOCK_BYTES)
    p = sub.add_parser("check"); p.add_argument("task_id"); p.add_argument("--rerun", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    try:
        if a.cmd == "open":
            print(json.dumps(open_record(a.task_id, a.session, a.title, a.owner), indent=1))
        elif a.cmd == "set":
            set_fields(a.task_id, phase=a.phase, next_action=a.next, owner=a.owner, files=a.files)
            print(json.dumps(load(a.task_id), indent=1))
        elif a.cmd == "add":
            print(json.dumps(add_item(a.task_id, a.kind, a.text, a.evidence, a.reproduction), indent=1))
        elif a.cmd == "job":
            print(json.dumps(add_job(a.task_id, a.run, a.label, a.source, a.state), indent=1))
        elif a.cmd == "verify":
            print(json.dumps(add_verification(a.task_id, a.check, a.result, a.evidence), indent=1))
        elif a.cmd == "archive":
            print(json.dumps(archive_item(a.task_id, a.decision_id), indent=1))
        elif a.cmd == "show":
            rec = load(a.task_id)
            if not rec:
                print("no record for %s" % a.task_id, file=sys.stderr)
                return 1
            print(json.dumps(rec, indent=1))
        elif a.cmd == "active":
            rec = active(a.session)
            print(json.dumps(rec, indent=1) if rec else "")
        elif a.cmd == "block":
            print(render_block(a.task_id, a.max_bytes))
        elif a.cmd == "check":
            rows = check_record(a.task_id, rerun=a.rerun)
            for r in rows:
                print("%-8s %-4s %-12s %s%s" % (r["status"], r["id"], r["kind"], r.get("repro") or "",
                                               (" " + r["detail"]) if r.get("detail") else ""))
            bad = [r for r in rows if r["status"] != "fresh" or r.get("repro") == "REPRO-FAIL"]
            return 1 if bad else 0
    except RecordTooLarge as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1
    except KeyError as exc:
        print("REFUSED: %s" % exc.args[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
