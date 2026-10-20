#!/usr/bin/env python3
"""One durable record per task: the drive state that must survive compaction and resume.

The record is the mechanism behind `orchestration` reference `checkpointing.md` ("keep a small task state"): requirements and
exclusions, decisions with hashed evidence, scope and owner, active phase, active jobs,
accepted artifacts and the next action. The parent writes it at phase boundaries and on each
consumed result; `hooks/compact_directive_anchor.py` re-injects a bounded block of it after a
compaction and `session_digest.py --brief` prints the same block.

Store: one JSON file per task at `<project>/.claude/logs/tasks/<task_id>.json` (env override
`HARNESS_TASK_RECORD_DIR` for proofs). Every write goes through `_hook_state.update_json_locked`, so
concurrent writers from several hooks or sessions compose. A record is capped at MAX_BYTES; a write
that would exceed it is refused with the offending field named, never truncated (a capped store that
admits everything evicts the early rows that matter). Closed history leaves the record under the same
lock: an archived decision and a job in a finished state move to the append-only
`<task_id>.archive.jsonl` beside it, so the record holds only live state and stays under its cap. Active jobs carry run ids attested by a
Workflow record, a sidecar `-R` record or the writing session's transcript, never a plan.

`slice` upserts one row of the plan's ordered slices; a resumed drive reads these rows, and the `TaskCreate`
board is only the owner's view of them.

`triage` records the six work-triage fields (values and litmus: `tools/triage.py --help`). Reach and oracle are
derived from `--paths` through the tag registry (required) and may not sit below that derived floor; a second call is a re-triage that
needs `--reason` and logs one decision per changed field. Every call binds the record to the caller's session
(`CLAUDE_CODE_SESSION_ID`), so a drive resumed in a new session re-triages to reclaim it; the commit check reads the
result through `triage_for_session`.

`add ... decision --evidence-item '<json>'` checks each repeatable four-kind item through
`verify_claims.check_claims` and refuses anything not VERIFIED. Items retain their add-time root and
session for later checks. `check` rehashes evidence paths (STALE / MISSING / fresh); `--rerun` also
checks stored items, including command output. Without items a decision is `hash-only`: unchanged
bytes are freshness, not a re-verified premise. The retired reproduction flag remains parseable,
refuses the write and prints its replacement; legacy reproduction commands are never executed.

    task_record.py open <task_id> --session <sid> --title <t>
    task_record.py set <task_id> [--phase P] [--next N] [--owner O] [--files PATH ...] [--rigor light|normal|thorough]
    task_record.py add <task_id> requirement|exclusion|decision|artifact "<text>" [--evidence P ...]
    task_record.py add <task_id> decision "<text>" --evidence-item '<json>' [--evidence-item '<json>' ...]
    task_record.py job <task_id> --run RID --label L --source workflow|sidecar|session --state S
                       [--role architect|drafter|transcriber|executor|fold] [--subject PATH]... [--input PATH]...
    task_record.py slice <task_id> --id S --state pending|in_progress|completed
    task_record.py triage <task_id> --reach R --reversibility V --design D --grounding G --oracle O[+O] --size S
                          --paths P... [--kind decision|mechanical] [--outward TEXT...] [--reason TEXT]
    task_record.py condition <task_id> --kind urgency|request|spend --quote TEXT --effect TEXT [--until ISO]
    task_record.py show <task_id> | active --session <sid> | block <task_id> [--max-bytes N]
    task_record.py check <task_id> [--rerun]

`condition` records an owner deadline or an owner request to add or drop process. The tool finds the last
owner turn of the caller's session (`CLAUDE_CODE_SESSION_ID`) whose text contains `--quote`, using
`_owner_text`'s classifier and prompt/answer projections plus typed command arguments. It stores that
turn's uuid and refuses when none does. A caller with `CLAUDE_CODE_SIDECAR` set is a delegate: its row is stored with `delegate` true so
`check` reports DELEGATE. `check` re-reads each condition's transcript and reports fresh, UNRESOLVED (no owner
turn with the stored uuid still holds the quote), MISSING (transcript unreadable) or DELEGATE.
A `spend` condition is the owner's lean posture (`tools/spend_mode.py`): its effect is `lean` or `normal`, it
needs `--until` as an ISO time with a zone, and no delegate may record one.
"""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "hooks"), HERE):
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
SLICE_STATES = ("pending", "in_progress", "completed")
FINISHED_JOB_STATES = {"done", "accepted", "failed", "discarded", "stopped", "cancelled", "rejected"}


class RecordTooLarge(Exception):
    pass


class TriageRefused(Exception):
    pass


class ConditionRefused(Exception):
    pass


class EvidenceRefused(Exception):
    pass


REPRODUCTION_REPLACEMENT = ('--reproduction is retired; use --evidence-item '
                            '\'{"command":"git ...","output":"expected output"}\' '
                            'and check <task_id> --rerun (reference/evidence_contract.md)')


CONDITION_KINDS = ("urgency", "request", "spend")
SPEND_EFFECTS = ("lean", "normal")
TRIAGE_ORDER = ("reach", "reversibility", "design", "grounding", "oracle", "size", "kind")


def record_dir():
    override = os.environ.get("HARNESS_TASK_RECORD_DIR")
    if override:
        return override
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, ".claude", "logs", "tasks")


def path_for(task_id, *, directory=None):
    return os.path.join(record_dir() if directory is None else directory, task_id + ".json")


def archive_path(task_id, *, directory=None):
    return os.path.join(record_dir() if directory is None else directory, task_id + ".archive.jsonl")


def archived_rows(task_id, *, directory=None):
    try:
        with open(archive_path(task_id, directory=directory), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("task archive rows must be objects")
        return rows
    except OSError:
        return []


def all_jobs(task_id):
    """Effective jobs across live and archived rows; acceptance supersedes its dispatch/done row."""
    rec = load(task_id) or {}
    rows = [r for r in archived_rows(task_id) if r.get("kind") == "job"] + list(rec.get("active_jobs") or [])
    effective = []
    for row in rows:
        match = next((i for i, old in enumerate(effective) if _same_run(old.get("run_id"), row.get("run_id"))
                      and old.get("label") == row.get("label")), None)
        if match is None:
            effective.append(row)
        elif row.get("state") == "accepted" or effective[match].get("state") != "accepted":
            effective[match] = row
    return effective


def all_decisions(task_id, *, directory=None):
    """Live and archived decisions by stable id; directory defaults to the caller's task store."""
    rec = load(task_id, directory=directory) or {}
    rows = ([r for r in archived_rows(task_id, directory=directory) if r.get("kind") == "decision"]
            + list(rec.get("archived") or []) + list(rec.get("decisions") or []))
    return list({r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}.values())


def _spill_closed(task_id, working):
    """Move closed history out of `working` into the archive file. Runs inside the record's lock, before
    the record is written, so a failed record write can leave a duplicate archive row but never a lost one."""
    rows = [dict(r, kind="decision") for r in working.pop("archived", None) or []]
    jobs = working.get("active_jobs") or []
    rows += [dict(j, kind="job") for j in jobs if j.get("state") in FINISHED_JOB_STATES]
    if "active_jobs" in working:
        working["active_jobs"] = [j for j in jobs if j.get("state") not in FINISHED_JOB_STATES]
    if rows:
        with open(archive_path(task_id), "a", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def now():
    # Microseconds: `active` orders records by this stamp, and two writes within one second must not tie.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def until_epoch(text):
    """The epoch an ISO-8601 `until` names (a `Z` or an offset is required), or None when it does not parse."""
    try:
        stamp = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.timestamp() if stamp.tzinfo is not None else None


def sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def evidence_rows(paths):
    return [{"path": p, "sha256": sha256(p)} for p in (paths or [])]


def load(task_id, *, directory=None):
    rec = _hook_state.read_json_salvage(path_for(task_id, directory=directory))
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
        except (KeyError, TriageRefused) as exc:
            caught["exc"] = exc
            raise
        working["updated"] = now()
        _spill_closed(task_id, working)
        raw = json.dumps(working, ensure_ascii=True)
        if len(raw.encode("utf-8")) > MAX_BYTES:
            sizes = {k: len(json.dumps(v, ensure_ascii=True).encode("utf-8")) for k, v in working.items()}
            field = max(sizes, key=sizes.get)
            error["msg"] = ("record %s would be %d B, over the %d B cap; largest field is %s (%d B). Trim it "
                            "(move detail to an artifact path) and retry."
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
           "phase": None, "active_jobs": [], "slices": [], "accepted_artifacts": [], "admissions": [], "next_action": None}

    def mutation(state):
        state.clear()
        state.update(rec)
        return rec

    return _mutate(task_id, mutation)


def _require(state, task_id):
    if not state.get("task_id"):
        raise KeyError("no record for %s; open it first" % task_id)


def set_fields(task_id, phase=None, next_action=None, owner=None, files=None, rigor=None):
    if rigor is not None:
        import triage
        if rigor not in triage.RIGORS:
            raise TriageRefused("rigor must be one of %s" % ", ".join(triage.RIGORS))

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
        if rigor is not None:
            state["rigor"] = rigor
        return state["phase"]

    return _mutate(task_id, mutation)


def _next_decision_id(task_id, state):
    used = ([d.get("id") for d in state.get("decisions", [])] + [d.get("id") for d in state.get("archived", [])]
            + [r.get("id") for r in archived_rows(task_id) if r.get("kind") == "decision"])
    n = 1
    while "D%d" % n in used:
        n += 1
    return "D%d" % n


def _check_evidence_items(decision):
    import verify_claims
    result = verify_claims.check_claims(
        [{"id": decision["id"], "evidence": decision["evidence_items"]}],
        root=decision["evidence_root"], session_id=decision.get("evidence_session"))
    return result["verdicts"][decision["id"]]


def _evidence_detail(verdict):
    return "; ".join("%s: %s%s" % (item["kind"], item["reason"],
                                  " " + item["detail"] if item.get("detail") else "")
                     for item in verdict["items"] if item["verdict"] != "VERIFIED")


def add_item(task_id, kind, text, evidence=None, reproduction=None, evidence_items=None):
    field = ITEM_KINDS[kind]
    if reproduction is not None:
        raise EvidenceRefused(REPRODUCTION_REPLACEMENT)
    context = None
    if evidence_items is not None:
        if kind != "decision":
            raise EvidenceRefused("--evidence-item is only for a decision")
        context = {"evidence_items": copy.deepcopy(evidence_items),
                   "evidence_root": str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()),
                   "evidence_session": os.environ.get("CLAUDE_CODE_SESSION_ID") or None}
        verdict = _check_evidence_items(dict(context, id="decision"))
        if verdict["verdict"] != "VERIFIED":
            raise EvidenceRefused("decision evidence %s: %s (reference/evidence_contract.md)"
                                  % (verdict["verdict"], _evidence_detail(verdict)))

    def mutation(state):
        _require(state, task_id)
        if kind == "decision":
            row = {"id": _next_decision_id(task_id, state), "text": text, "evidence": evidence_rows(evidence),
                   "at": now()}
            if context is not None:
                row.update(context)
            state.setdefault("decisions", []).append(row)
            return row
        state.setdefault(field, []).append(text)
        return text

    return _mutate(task_id, mutation)


def _same_run(a, b):
    """One run named from two directories: equal, or one path ends in the other at a path boundary."""
    a, b = str(a or "").replace("\\", "/"), str(b or "").replace("\\", "/")
    a, b = (p[2:] if p.startswith("./") else p for p in (a, b))
    short, long_ = sorted((a, b), key=len)
    return a == b or (bool(short) and long_.endswith("/" + short))


JOB_ROLES = ("architect", "drafter", "transcriber", "executor", "fold")


def add_job(task_id, run_id, label, source, state, role=None, subject=None, *, base_files=None,
            inputs=None, units=None, origins=None):
    """Upsert acceptance on the dispatch row, retaining bases even across the append-only archive.

    Subjects use review_ledger's blob owner; acceptance reads the index for staged paths, disk otherwise.
    Inline session authorship is bound to the writing session's transcript, never a caller-supplied model.
    inputs (CLI --input) captures consumed blobs through that same owner. units and origins carry only
    the checker's per-path unit names and digest source/ledger references; omitted fields retain earlier
    values. base_files is the checked fold's reviewed snapshot, not HEAD or caller-supplied output hashes.
    Explicit acceptance with role, subjects and runtime evidence also backfills a legacy done row.
    """
    if role is not None and role not in JOB_ROLES:
        raise KeyError("unknown job role %r" % role)
    import review_ledger
    import model_registry
    repo = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    files = bases = None
    if subject is not None:
        ctx = review_ledger.Ctx(repo, "HEAD")
        paths = review_ledger.normalize_files(ctx.toplevel, subject)
        if any(Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
            raise KeyError("job subjects must be inside the repository")
        head = review_ledger.Ctx(repo, "HEAD", snapshot="HEAD").hash_files(paths)
        files = ctx.hash_files(paths)
        for path in paths:
            if files[path] is None and head[path] is None:
                staged = review_ledger.Ctx(repo, "HEAD", snapshot="INDEX").hash(path)
                if staged is None:
                    raise KeyError("subject %s is neither present nor tracked" % path)
        bases = head if state == "accepted" else dict(files)
        if state == "accepted":
            result = review_ledger._git(repo, ["diff", "--cached", "--name-only", "-z", "--", *paths])
            if result.returncode:
                raise KeyError("cannot read staged subject paths")
            staged = [p for p in result.stdout.decode("utf-8").split("\0") if p]
            files.update(review_ledger.Ctx(repo, "HEAD", snapshot="INDEX").hash_files(staged))
    input_files = None
    if inputs is not None:
        ctx = review_ledger.Ctx(repo, "HEAD")
        paths = review_ledger.normalize_files(ctx.toplevel, inputs)
        if any(Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
            raise KeyError("job inputs must be inside the repository")
        input_files = ctx.hash_files(paths)
        if any(blob is None for blob in input_files.values()):
            raise KeyError("job inputs must name readable files")
    if base_files is not None:
        if role != "fold" or state != "accepted" or files is None or set(base_files) != set(files):
            raise KeyError("reviewed bases require an accepted fold and exactly its subject paths")
        if any(blob is not None and (not isinstance(blob, str) or
                review_ledger._git(repo, ["cat-file", "-e", blob + "^{blob}"]).returncode)
                for blob in base_files.values()):
            raise KeyError("reviewed fold bases must be repository blobs or null")
        bases = dict(base_files)
    if units is not None and (files is None or not isinstance(units, dict) or set(units) - set(files)
            or any(not isinstance(v, list) or any(not isinstance(u, str) or not u for u in v)
                   for v in units.values())):
        raise KeyError("units must map subject paths to named units")
    if origins is not None and (not isinstance(origins, list) or any(not isinstance(o, dict)
            or o.get("source") not in ("finding", "adapted", "fold") or not o.get("row")
            or not isinstance(o.get("files"), dict) or files is None
            or any(p not in files or files[p] != b for p, b in o["files"].items()) for o in origins)):
        raise KeyError("origins require source, ledger row and matching accepted subject blobs")
    identity = None
    if source == "session":
        session = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not session or run_id != session:
            raise KeyError("--source session --run must name the writing CLAUDE_CODE_SESSION_ID")
        model = review_ledger.served_model()
        identity = model_registry.record_identity({"modelVersion": model})
        if model:
            identity["basis"] = "sessionTranscript"
    elif source == "workflow" and state == "accepted":
        try:
            _, _, _, served, _ = review_ledger._journal_source(run_id, repo)
            actor = (served.get(label) or {}).get("served_model")
        except (review_ledger.ToolError, OSError, ValueError, TypeError, KeyError):
            actor = None
        identity = model_registry.record_identity({"modelVersion": actor})
        if actor:
            identity["basis"] = "workflowTranscript"
    elif source == "sidecar" and state == "accepted":
        path = Path(run_id)
        try:
            record = json.loads((path if path.is_absolute() else repo / path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        identity = model_registry.record_identity(record, token=record.get("requestedModel"))
        data = model_registry.load()
        if record.get("transport") in data["transports"] and model_registry.attestation_for(record["transport"], data) and not record.get("attestedModel"):
            identity = {"model": None, "basis": None, "modelObserved": False}

    def mutation(rec):
        _require(rec, task_id)
        jobs = rec.setdefault("active_jobs", [])
        matched = next((j for j in jobs if _same_run(j.get("run_id"), run_id) and j.get("label") == label), None)
        if matched is None:
            old = next((j for j in reversed(archived_rows(task_id)) if j.get("kind") == "job"
                        and _same_run(j.get("run_id"), run_id) and j.get("label") == label), None)
            matched = dict(old or {}, run_id=run_id, label=label, source=source)
            matched.pop("kind", None)
            jobs.append(matched)
        matched.update(state=state, at=now())
        if role is not None:
            matched["role"] = role
        if files is not None:
            matched["files"] = files
            matched["baseFiles"] = (dict(bases) if base_files is not None else
                {p: (matched.get("baseFiles") or {}).get(p, bases[p]) for p in files})
        for field, value in (("inputFiles", input_files), ("units", units), ("origins", origins)):
            if value is not None:
                matched[field] = copy.deepcopy(value)
        if identity is not None:
            matched.update(model=identity["model"], identityBasis=identity["basis"], modelObserved=identity["modelObserved"])
        return matched

    return _mutate(task_id, mutation)


def record_admission(task_id, row):
    """Append a triage.admits result under the existing record lock and byte cap."""
    def mutation(state):
        _require(state, task_id)
        state.setdefault("admissions", []).append(dict(row))
        return row

    return _mutate(task_id, mutation)


def add_slice(task_id, slice_id, state):
    if state not in SLICE_STATES:
        raise KeyError("slice state %r is not one of %s" % (state, ", ".join(SLICE_STATES)))

    def mutation(rec):
        _require(rec, task_id)
        rows = rec.setdefault("slices", [])
        for row in rows:
            if row.get("id") == slice_id:
                row["state"] = state
                row["at"] = now()
                return row
        row = {"id": slice_id, "state": state, "at": now()}
        rows.append(row)
        return row

    return _mutate(task_id, mutation)


def _session_key(session_id):
    """Records are opened with either the full session id or the 8-char sid the hooks print; both name one session."""
    return (session_id or "")[:8].lower()


def active(session_id, require=None, *, directory=None):
    """The newest record opened or bound for the session; with `require`, the newest that holds that key."""
    directory = record_dir() if directory is None else directory
    key = _session_key(session_id)
    newest = None
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = _hook_state.read_json_salvage(os.path.join(directory, name))
        if not key or _session_key(rec.get("session_id")) != key or (require and not rec.get(require)):
            continue
        if newest is None or (rec.get("updated") or "") > (newest.get("updated") or ""):
            newest = rec
    return newest


def triage_for_session(session_id):
    """The triage fields of the session's newest record that holds a triage, else None."""
    rec = active(session_id, require="triage")
    return (rec.get("triage") or {}).get("fields") if rec else None


def _claude_dir():
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    return os.path.join(root, ".claude") if root else os.path.normpath(os.path.join(HERE, ".."))


def parse_oracle(text):
    import triage
    try:
        return triage.parse_oracle(text)
    except ValueError as exc:
        raise TriageRefused(str(exc))


def _joined(value):
    import triage
    return triage._format_oracle(value) if isinstance(value, list) else value


def record_triage(task_id, fields, paths, outward=None, reason=None, session_id=None):
    """Store the six triage fields with the registry-derived block. Refuses an empty `paths` (no floor to derive),
    a reach below the derived reach or an oracle set missing a value an unconditional match sets. A second call is a re-triage: it needs `reason`, logs
    one decision per changed field and rebinds the record to `session_id`."""
    import triage
    if not paths:
        raise TriageRefused("triage needs --paths: the reach and oracle floor derive from them")
    fields = dict(fields, oracle=sorted(fields["oracle"]))
    res = triage.derive(triage.planned_changes(paths), triage.load_tags(_claude_dir()))
    low = [triage.describe(r) for r in triage.raises(dict(fields, oracle=frozenset(fields["oracle"])), res)]
    if low:
        raise TriageRefused("triage sits below the derived floor: " + "; ".join(low))
    seen, matches = set(), []
    for m in res.matches:
        if (m.tag, m.path) not in seen:
            seen.add((m.tag, m.path))
            matches.append({"tag": m.tag, "path": m.path, "sets": m.sets, "conditional": m.conditional})
    derived = {"reach": res.reach, "oracle": sorted(res.oracle), "matches": matches}

    def mutation(state):
        _require(state, task_id)
        old = (state.get("triage") or {}).get("fields")
        if old is not None and not reason:
            raise TriageRefused("task %s already has a triage; a re-triage needs --reason" % task_id)
        if old is not None:
            for name in TRIAGE_ORDER:
                before, after = _joined(old.get(name)), _joined(fields.get(name))
                if before != after:
                    state.setdefault("decisions", []).append(
                        {"id": _next_decision_id(task_id, state), "evidence": [], "at": now(),
                         "text": "re-triage: %s %s -> %s; %s" % (name, before, after, reason)})
        state["triage"] = {"fields": fields, "paths": list(paths or []), "outward": list(outward or []),
                           "derived": derived, "at": now()}
        if session_id:
            state["session_id"] = session_id
        return state["triage"]

    return _mutate(task_id, mutation)


def _owner_turns(transcript):
    """Condition projection: spoken prompts, picked answers and typed command arguments."""
    import _owner_text
    try:
        with open(transcript, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    turns = []
    for index, line in enumerate(lines):
        try:
            row = _owner_text.classify(json.loads(line), index + 1, raw=line)
            text = _owner_text.owner_quote_text(row) or _owner_text.owner_answer_text(row)
            if (row is not None and row.kind == "command" and not row.meta and not row.sidechain
                    and not row.envelope and not row.tool_results):
                text = row.command_args
        except Exception:
            continue
        if text:
            turns.append((row.uuid, text))
    return turns


def _session_transcript(session_id):
    import session_digest
    return str(session_digest.session_store(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), session_id)
               / (session_id + ".jsonl"))


def record_condition(task_id, kind, quote, effect, session_id=None, transcript=None, until=None):
    """Store a run condition against the owner turn that holds `quote`. Refuses an unknown kind, an empty
    quote, an unreadable transcript, or a quote no owner turn of the session holds. A delegate
    (`CLAUDE_CODE_SIDECAR` set) is stored with `delegate` true, never refused, except for a `spend`
    condition: that is the owner's lean posture, so a delegate cannot record one. A `spend` condition
    also needs an effect of lean or normal and an `until` ISO time with a zone."""
    if kind not in CONDITION_KINDS:
        raise ConditionRefused("condition kind must be one of %s" % ", ".join(CONDITION_KINDS))
    if kind == "spend":
        if os.environ.get("CLAUDE_CODE_SIDECAR"):
            raise ConditionRefused("a delegate cannot record a spend condition; the owner sets lean or normal")
        if effect not in SPEND_EFFECTS:
            raise ConditionRefused("a spend condition's effect must be one of %s" % ", ".join(SPEND_EFFECTS))
        if not until:
            raise ConditionRefused("a spend condition needs --until: an ISO time with a zone")
        if until_epoch(until) is None:
            raise ConditionRefused("--until must be an ISO time with a zone, e.g. 2031-01-01T00:00:00Z")
    quote = (quote or "").strip()
    if not quote:
        raise ConditionRefused("condition needs the owner's words in --quote")
    session_id = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not transcript:
        if not session_id:
            raise ConditionRefused("no session id: set CLAUDE_CODE_SESSION_ID or pass the transcript")
        transcript = _session_transcript(session_id)
    turns = _owner_turns(transcript)
    if turns is None:
        raise ConditionRefused("cannot read the session transcript %s" % transcript)
    holders = [uuid for uuid, text in turns if quote in text]
    if not holders:
        raise ConditionRefused("no owner turn in this session holds the quote")

    def mutation(state):
        _require(state, task_id)
        rows = state.setdefault("conditions", [])
        row = {"id": "C%d" % (len(rows) + 1), "kind": kind, "quote": quote, "effect": effect,
               "message_id": holders[-1], "session_id": session_id, "transcript": str(transcript),
               "delegate": bool(os.environ.get("CLAUDE_CODE_SIDECAR")), "at": now()}
        if until is not None:
            row["until"] = until
        rows.append(row)
        return row

    return _mutate(task_id, mutation)


def _condition_row(condition):
    row = {"id": condition.get("id"), "kind": "condition", "status": "fresh", "detail": None}
    turns = _owner_turns(condition.get("transcript") or "")
    if turns is None:
        row.update(status="MISSING", detail=condition.get("transcript"))
    elif not any(uuid == condition.get("message_id") and (condition.get("quote") or "\0") in text
                 for uuid, text in turns):
        row.update(status="UNRESOLVED", detail=condition.get("message_id"))
    elif condition.get("delegate"):
        row.update(status="DELEGATE", detail="recorded by a delegate")
    return row


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
        if "evidence_items" in d:
            if rerun:
                verdict = _check_evidence_items(d)
                row["repro"] = verdict["verdict"]
                row["evidence_verdict"] = verdict
                if verdict["verdict"] != "VERIFIED":
                    row["detail"] = "; ".join(filter(None, (row["detail"], _evidence_detail(verdict))))
            else:
                row["repro"] = "stored"
        else:
            row["repro"] = "hash-only"
        if d.get("reproduction") is not None:
            row.update(status="UNCHECKED", detail=REPRODUCTION_REPLACEMENT)
        rows.append(row)
    rows += [_condition_row(c) for c in rec.get("conditions", [])]
    return rows


def _clip(text, limit):
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "…"


def _review_line(task_id):
    """`review: <rows> ledger rows, <n> non-covering outcomes (...)` over the review-ledger rows that name the task, or
    None when it has none or the ledger cannot be read: the pointer is advisory and never fails the block."""
    try:
        import review_ledger
        rows = review_ledger.read_rows(review_ledger.ledger_path(Path.cwd()))[0]
        mine = [row for row in rows if row.get("task") == task_id]
        if not mine:
            return None
        bad = sum(row.get("outcome") not in review_ledger.COVERING_OUTCOMES for row in mine)
        return "review: %d ledger rows, %d non-covering outcomes (review_ledger.py rows --task %s)" % (len(mine), bad, task_id)
    except Exception:
        return None


def render_block(task_id, max_bytes=BLOCK_BYTES):
    """The bounded injection text. Requirements and exclusions stay whole; decisions, slices and jobs
    are water-filled in that order; anything left out is named with the show command.
    One `review:` line follows the conditions when the review ledger holds rows naming the task."""
    rec = load(task_id)
    if not rec:
        return ""
    size = lambda lines: len("\n".join(lines).encode("utf-8"))  # noqa: E731
    head = ["[task-record] %s phase=%s next=%s" % (task_id, rec.get("phase"), rec.get("next_action"))]
    if rec.get("rigor"):
        head.append("rigor: " + rec["rigor"])
    triaged = (rec.get("triage") or {}).get("fields")
    if triaged:
        head.append("triage: " + " ".join("%s=%s" % (k, _joined(triaged[k])) for k in TRIAGE_ORDER if k in triaged))
    head += [_clip('- condition %s %s: %s (owner: "%s")' % (c.get("id"), c.get("kind"), c.get("effect"), c.get("quote")),
                   200) for c in rec.get("conditions", [])]
    review = _review_line(task_id)
    if review:
        head.append(review)
    files = (rec.get("scope") or {}).get("files") or []
    if files:
        head.append(_clip("- files: " + ", ".join(files), 400))
    head += ["- requirement: %s" % r for r in rec.get("requirements", [])]
    head += ["- exclusion: %s" % r for r in rec.get("exclusions", [])]
    footer_worst = "omitted: 9999 decisions, 9999 slices, 9999 jobs. Full record: " + SHOW % task_id
    budget = max_bytes - size(head) - len(footer_worst.encode("utf-8")) - 2
    decisions = ["- %s: %s" % (d.get("id"), d.get("text", "")) for d in rec.get("decisions", [])]
    jobs = ["- job %s %s %s" % (j.get("run_id"), j.get("label"), j.get("state")) for j in rec.get("active_jobs", [])]
    slices = ["- slice %s %s" % (r.get("id"), r.get("state")) for r in rec.get("slices", [])]
    kept, omitted = [], {"decisions": 0, "slices": 0, "jobs": 0}
    for name, rows in (("decisions", decisions), ("slices", slices), ("jobs", jobs)):
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
        lines.append("omitted: %d decisions, %d slices, %d jobs. Full record: %s"
                     % (omitted["decisions"], omitted["slices"], omitted["jobs"], SHOW % task_id))
    return "\n".join(lines)


def main(argv=None):
    import triage
    ap = argparse.ArgumentParser(description="One durable record per task.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open"); p.add_argument("task_id"); p.add_argument("--session", required=True)
    p.add_argument("--title", required=True); p.add_argument("--owner")
    p = sub.add_parser("set"); p.add_argument("task_id"); p.add_argument("--phase"); p.add_argument("--next")
    p.add_argument("--owner"); p.add_argument("--files", nargs="*"); p.add_argument("--rigor", choices=triage.RIGORS)
    p = sub.add_parser("add"); p.add_argument("task_id"); p.add_argument("kind", choices=sorted(ITEM_KINDS))
    p.add_argument("text"); p.add_argument("--evidence", nargs="*")
    p.add_argument("--evidence-item", action="append", help="decision evidence JSON; repeat for each item")
    p.add_argument("--reproduction", help=argparse.SUPPRESS)
    p = sub.add_parser("job"); p.add_argument("task_id"); p.add_argument("--run", required=True)
    p.add_argument("--label", required=True); p.add_argument("--source", choices=("workflow", "sidecar", "session"), required=True)
    p.add_argument("--role", choices=JOB_ROLES)
    p.add_argument("--subject", action="append")
    p.add_argument("--input", action="append", dest="inputs")
    p.add_argument("--state", required=True)
    p = sub.add_parser("slice"); p.add_argument("task_id"); p.add_argument("--id", required=True)
    p.add_argument("--state", required=True)
    p = sub.add_parser("show"); p.add_argument("task_id")
    p = sub.add_parser("triage"); p.add_argument("task_id")
    for field in ("reach", "reversibility", "design", "grounding"):
        p.add_argument("--" + field, required=True, choices=triage.FIELDS[field])
    p.add_argument("--oracle", required=True)
    p.add_argument("--size", required=True, choices=triage.FIELDS["size"],
                   type=lambda v: triage.RETIRED["size"].get(v, v))
    p.add_argument("--paths", nargs="*", default=[]); p.add_argument("--outward", nargs="*", default=[])
    p.add_argument("--reason")
    p.add_argument("--kind", choices=triage.OPTIONAL_FIELDS["kind"])
    p = sub.add_parser("condition"); p.add_argument("task_id")
    p.add_argument("--kind", required=True, choices=CONDITION_KINDS)
    p.add_argument("--quote", required=True); p.add_argument("--effect", required=True)
    p.add_argument("--until", help="spend conditions: the ISO time the posture ends, with a zone")
    p = sub.add_parser("active"); p.add_argument("--session", required=True)
    p = sub.add_parser("block"); p.add_argument("task_id"); p.add_argument("--max-bytes", type=int, default=BLOCK_BYTES)
    p = sub.add_parser("check"); p.add_argument("task_id"); p.add_argument("--rerun", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    try:
        if a.cmd == "open":
            print(json.dumps(open_record(a.task_id, a.session, a.title, a.owner), indent=1))
        elif a.cmd == "set":
            set_fields(a.task_id, phase=a.phase, next_action=a.next, owner=a.owner, files=a.files, rigor=a.rigor)
            print(json.dumps(load(a.task_id), indent=1))
        elif a.cmd == "add":
            if a.reproduction is not None:
                raise EvidenceRefused(REPRODUCTION_REPLACEMENT)
            try:
                items = [json.loads(item) for item in a.evidence_item] if a.evidence_item is not None else None
            except json.JSONDecodeError as exc:
                raise EvidenceRefused("--evidence-item needs JSON: %s" % exc.msg)
            print(json.dumps(add_item(a.task_id, a.kind, a.text, a.evidence,
                                      evidence_items=items), indent=1))
        elif a.cmd == "job":
            print(json.dumps(add_job(a.task_id, a.run, a.label, a.source, a.state, role=a.role,
                                     subject=a.subject, inputs=a.inputs), indent=1))
        elif a.cmd == "slice":
            print(json.dumps(add_slice(a.task_id, a.id, a.state), indent=1))
        elif a.cmd == "show":
            rec = load(a.task_id)
            if not rec:
                print("no record for %s" % a.task_id, file=sys.stderr)
                return 1
            print(json.dumps(rec, indent=1))
        elif a.cmd == "triage":
            fields = {"reach": a.reach, "reversibility": a.reversibility, "design": a.design,
                      "grounding": a.grounding, "oracle": parse_oracle(a.oracle), "size": a.size}
            if a.kind is not None:
                fields["kind"] = a.kind
            print(json.dumps(record_triage(a.task_id, fields, a.paths, a.outward, a.reason,
                                           os.environ.get("CLAUDE_CODE_SESSION_ID") or None), indent=1))
        elif a.cmd == "condition":
            print(json.dumps(record_condition(a.task_id, a.kind, a.quote, a.effect, until=a.until), indent=1))
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
            bad = [r for r in rows if r["status"] != "fresh" or r.get("repro") in ("UNVERIFIED", "UNCHECKED")]
            return 1 if bad else 0
    except RecordTooLarge as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1
    except KeyError as exc:
        print("REFUSED: %s" % exc.args[0], file=sys.stderr)
        return 1
    except (TriageRefused, ConditionRefused, EvidenceRefused) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
