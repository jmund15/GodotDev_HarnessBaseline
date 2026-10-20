#!/usr/bin/env python3
"""Write each dispatch job's full final deliverable to `<outDir>/<label>.md`, on either route.

A Workflow job with Write spills its deliverable to `<spillDir>/<label>.md` itself. A job that
cannot (a profile without Write, or a run without spillDir) returns it only inline, recorded in
the run's `journal.jsonl`; this tool writes that text to the same path. A sidecar fan-out settles `<label>.md` when
each job ends (`tools/sidecar_fanout.py` calls `capture_delivery`): a report the job wrote there during the run
stays the delivery and a differing final message is kept as `<label>.reply.md`; with no such report the final
message is written, and `answered_after_report` flags a stream whose final message answered a later turn. This
tool fills in a `<label>.md` for an out dir that predates that. Both routes name the file the way
`workflows/dispatch.js` does.

USAGE
    python3 .claude/tools/dispatch_result.py <workflow-run-dir | fanout-out-dir> [--out-dir DIR]
    python3 .claude/tools/dispatch_result.py check <spill.md> --block <name>=<key,key...> [--block ...]
    python3 .claude/tools/dispatch_result.py consume <workflow-run-dir | sidecar-out-dir | sidecar-record.json> --label <label> [--check-timeout S] [--brief P]

A workflow run dir is the Workflow result's transcript dir (it holds `journal.jsonl`). Its out dir
is the run's spillDir, read from the sibling run record `<session>/workflows/<runId>.json`;
`--out-dir` overrides it and is required when the run passed none. A relative spillDir resolves
against the repository root. A fan-out out dir holds `<label>.out.json` per job.

Per job it prints one line: `wrote <path>`, `kept <path>` (the file exists: the job spilled or an
earlier pass wrote it), `spill-missing <label> <path>` (the job's digest names a spill file that
does not exist; the digest is not the deliverable, so nothing is written), or `no-result <label>`.

`check` validates a spill the parent parses by script. Each `--block` names a fenced block (a
fence opened with the name) and the keys every row must hold. A block fenced more than once reads
as one block. Every non-blank line of a block must be a JSON object holding its keys. It prints
one line per bad row (`<name> row <n>: ...`, n counting the block's non-blank rows), `MISSING block
<name>` for a block that was never fenced, and `<name>: <n> rows` per block.

EXIT 0 every job has its `<label>.md` (`check`: every block present and every row holds its keys);
1 some job has none (`check`: a block is missing or a row is bad); 2 the input is unusable.

`consume` runs one label of a Workflow run (a dispatch.js run or a dispatch_chains.js run, whose label is any job of any
lane), a sidecar fan-out out dir or a `-R` record (which needs `--brief`, since neither holds the brief's path): it writes
`<label>.md`, prints a bounded digest, reads the brief's one `task` block, runs each `check` in order from the repo root
under a deadline and stops at the first failure, so later checks print NOT-RUN, lists changed paths (`git status`
over `writeSet` and the deliverable's `changedPaths:` line, flagging paths outside `writeSet`, and a gitignored
file written after the run start), prints the diff, and writes `<label>.seed.json` and `<label>.manifest.json`
beside the deliverable. It prints facts only. EXIT 0 every check passed; 1 a check failed, timed
out, or was HELD (the brief was written after the run started, so no check ran); 2 refused, with nothing run.
A check matching a floor guard (an index write, a commit, a push, a process kill outside tools/reap.py) is
refused; the parent runs it in its own Bash call.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".claude" / "hooks"


def _consume_deps():
    """The hook and launcher helpers only `consume` needs. Imported on use: a bench sandbox copies a checkout
    without `.claude/hooks`, and its ruler imports this module for the deliverable writer alone."""
    for path in (str(HOOKS), os.path.dirname(os.path.abspath(__file__))):
        if path not in sys.path:
            sys.path.insert(0, path)
    from _bounded_run import run_bounded
    from _commit_scope import publishes_index, writes_index
    from _git_commit import git_invocations
    from sidecar_launch import git_bash
    return run_bounded, publishes_index, writes_index, git_invocations, git_bash

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_FULL_LINE = re.compile(r"^FULL:\s*(\S.*?)\s*$")
TERMINAL_STATUSES = frozenset(("completed", "failed", "cancelled", "error", "killed"))
DEFAULT_CHECK_TIMEOUT = 540
DIGEST_LINES = 30
DIGEST_CHARS = 2000
DIFF_CHARS = 20000
TAIL_LINES = 15
_KILL_WORD = re.compile(r"(?<![\w./-])(?:taskkill(?:\.exe)?|Stop-Process|kill)(?![\w-])", re.I)
_SEGMENT = re.compile(r"&&|\|\||[;|&\n`]|\$\(")
_TEXT_COMMANDS = frozenset(("git", "grep", "egrep", "fgrep", "rg", "echo", "printf", "cat", "head", "tail", "wc", "ls"))
_REAPER_LAUNCHERS = frozenset(("python", "python3", "py", "bash", "sh"))
_RETURNED = re.compile(r"^\s*changedPaths:[ \t]*(.*?)\s*$", re.M)


def deliverable_path(out_dir, label):
    """`<out_dir>/<label>.md` with the label sanitized exactly as dispatch.js's spillPath does."""
    return Path(out_dir) / (_UNSAFE.sub("_", str(label)) + ".md")


def _text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    return json.dumps(value, indent=2, ensure_ascii=False)


def write_deliverable(out_dir, label, text):
    """Write `text` to the label's deliverable path (UTF-8, LF) and return the path. The file
    appears whole or not at all: a run killed mid-write must not leave a truncated `<label>.md`
    that a later pass reports as `kept`."""
    path = deliverable_path(out_dir, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def final_message(stdout_text):
    """The final message of one sidecar run's captured stdout, or None.

    Selects the same event `sc_write_record` in `scripts/lib/sidecar_common.sh` builds the `-R`
    record from: a codex run's last `agent_message` item; otherwise the last `result` event, where
    a trailing synthetic usage-limit event yields to the child's own last result.
    A schema's structured_output takes precedence over result prose.
    """
    events = []
    try:
        whole = json.loads(stdout_text)
        events = whole if isinstance(whole, list) else [whole]
    except ValueError:
        for line in stdout_text.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    events = [e for e in events if isinstance(e, dict)]
    results = [e for e in events if e.get("type") == "result" or "result" in e and "type" not in e]
    if not results and any(e.get("type") in ("turn.completed", "item.completed") for e in events):
        texts = [(e.get("item") or {}).get("text") for e in events if e.get("type") == "item.completed"
                 and (e.get("item") or {}).get("type") == "agent_message"]
        texts = [t for t in texts if t]
        return texts[-1] if texts else None
    if not results:
        return None
    last = results[-1]
    if last.get("synthetic"):
        real = [e for e in results if not e.get("synthetic")]
        last = real[-1] if real else last
    return _text(last.get('structured_output') if isinstance(last.get('structured_output'), dict) else last.get('result'))


def final_message_of(stdout_path):
    try:
        with open(stdout_path, encoding="utf-8", errors="replace") as fh:
            return final_message(fh.read())
    except OSError:
        return None


def _stream_events(stdout_text):
    try:
        whole = json.loads(stdout_text)
        events = whole if isinstance(whole, list) else [whole]
    except ValueError:
        events = []
        for line in stdout_text.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return [e for e in events if isinstance(e, dict)]


def _blocks(event):
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def answered_after_report(stdout_text):
    """True when a Claude stream holds a text-only assistant message, then a user turn carrying text (a
    notification or hook feedback, not a tool result), then another assistant text: the final message
    answered that later turn and may not be the job's report. Structure only, never length. A codex
    stream narrates between tool calls in agent messages, so it never reports this."""
    state = None
    for event in _stream_events(stdout_text):
        kind, blocks = event.get("type"), _blocks(event)
        if kind == "assistant":
            has_text = any(b.get("type") == "text" and str(b.get("text", "")).strip() for b in blocks)
            has_tool = any(b.get("type") == "tool_use" for b in blocks)
            if state == "new_turn" and has_text:
                return True
            state = "text_only" if has_text and not has_tool else (None if has_tool else state)
        elif kind == "user":
            if any(b.get("type") == "tool_result" for b in blocks):
                state = None
            elif state == "text_only" and any(b.get("type") == "text" for b in blocks):
                state = "new_turn"
    return False


def capture_delivery(out_dir, label, stdout_path, since):
    """Settle `<out_dir>/<label>.md` when a job ends and return {path, reply, flag}.

    A job told to write its report to `<label>.md` has written it by now (mtime at or after `since`):
    that file is the delivery, and a final message that differs is kept as `<label>.reply.md`, its path
    returned as `reply`. With no such file the final message is the delivery, as before, and `flag`
    names the stream shape in which it may be a trailing reply (`answered_after_report`).
    """
    out_dir = Path(out_dir)
    path = deliverable_path(out_dir, label)
    final = final_message_of(stdout_path)
    try:
        report = path.read_text(encoding="utf-8", errors="replace") if path.stat().st_mtime >= since else None
    except OSError:
        report = None
    if report is not None:
        reply = None
        if final is not None and final.strip() != report.strip():
            reply = write_deliverable(out_dir, label + ".reply", final)
        return {"path": path, "reply": reply, "flag": None}
    if final is None:
        return {"path": None, "reply": None, "flag": None}
    try:
        with open(stdout_path, encoding="utf-8", errors="replace") as fh:
            trailing = answered_after_report(fh.read())
    except OSError:
        trailing = False
    flag = ("the final message answered a later turn after an earlier reply; the job named no report file, "
            "so the delivery may be a trailing reply") if trailing else None
    return {"path": write_deliverable(out_dir, label, final), "reply": None, "flag": flag}


def write_from_stdout(out_dir, label, stdout_path):
    """Write the run's final message to `<out_dir>/<label>.md`; the path, or None with no message."""
    text = final_message_of(stdout_path)
    return write_deliverable(out_dir, label, text) if text is not None else None


def _run_record_path(run_dir):
    return run_dir.parent.parent.parent / "workflows" / (run_dir.name + ".json")


def _spill_dir_of(run_dir):
    record = _run_record_path(run_dir)
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for source in (data.get("result"), data.get("args")):
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except ValueError:
                continue
        value = source.get("spillDir") if isinstance(source, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return None


def _journal_jobs(journal):
    """[(label, result-or-None)] in start order; a label's latest agent with a result wins."""
    labels, results, order = {}, {}, []
    for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or not ev.get("agentId"):
            continue
        if ev.get("type") == "started" and ev.get("label"):
            labels[ev["agentId"]] = ev["label"]
            if ev["label"] not in order:
                order.append(ev["label"])
        elif ev.get("type") == "result":
            results[ev["agentId"]] = ev.get("result")
    by_label = {}
    for agent, label in labels.items():
        if agent in results and results[agent] is not None:
            by_label[label] = results[agent]
    return [(label, by_label.get(label)) for label in order]


def _spill_target(text):
    lines = [l for l in str(text).splitlines() if l.strip()]
    match = _FULL_LINE.match(lines[-1]) if lines else None
    return match.group(1) if match else None


def _materialize_one(out_dir, label, value):
    """(status, path) for one job's deliverable: `kept` (the file exists), `wrote`, `no-result`
    (path None) or `spill-missing` (path is the missing spill target)."""
    path = deliverable_path(out_dir, label)
    if path.is_file():
        return "kept", path
    text = _text(value)
    if text is None:
        return "no-result", None
    target = _spill_target(text) if isinstance(value, str) else None
    if target:
        return "spill-missing", target
    return "wrote", write_deliverable(out_dir, label, text)


def materialize_workflow(run_dir, out_dir):
    missing = 0
    for label, value in _journal_jobs(run_dir / "journal.jsonl"):
        status, path = _materialize_one(out_dir, label, value)
        if status == "kept" or status == "wrote":
            print(f"{status} {path.as_posix()}")
        elif status == "no-result":
            print(f"no-result {label}")
            missing += 1
        else:
            print(f"spill-missing {label} {path}")
            missing += 1
    return 1 if missing else 0


def materialize_fanout(out_dir):
    missing = 0
    for stdout in sorted(out_dir.glob("*.out.json")):
        label = stdout.name[:-len(".out.json")]
        path = deliverable_path(out_dir, label)
        if path.is_file():
            print(f"kept {path.as_posix()}")
            continue
        written = write_from_stdout(out_dir, label, stdout)
        if written is None:
            print(f"no-result {label}")
            missing += 1
        else:
            print(f"wrote {written.as_posix()}")
    return 1 if missing else 0


def _fenced_bodies(text, name):
    """The body of every fence opened with `name` in `text`, in order."""
    return re.findall(r"```" + re.escape(name) + r"[ \t]*\r?\n(.*?)```", text, re.S)


def check_spill(text, blocks):
    """(report lines, bad count) for `blocks`, a {name: [keys]} mapping, checked against `text`."""
    lines, bad = [], 0
    for name, keys in blocks.items():
        found = _fenced_bodies(text, name)
        if not found:
            lines.append(f"MISSING block {name}")
            bad += 1
            continue
        rows = 0
        for raw in "\n".join(found).splitlines():
            if not raw.strip():
                continue
            rows += 1
            try:
                obj = json.loads(raw)
            except ValueError as exc:
                lines.append(f"{name} row {rows}: not JSON ({exc})")
                bad += 1
                continue
            if not isinstance(obj, dict):
                lines.append(f"{name} row {rows}: not a JSON object")
                bad += 1
                continue
            missing = [k for k in keys if k not in obj]
            if missing:
                lines.append(f"{name} row {rows}: missing {missing}")
                bad += 1
        lines.append(f"{name}: {rows} rows")
    return lines, bad


class Refused(Exception):
    """A consume request that runs nothing; the message says why."""


def task_block(text):
    """The brief's one `task` object: None when it holds no `task` fence. Raises ValueError naming the
    defect when the fence does not hold exactly one JSON object, or its `check` list is empty. A `check`
    given as one string is that one-entry list."""
    found = _fenced_bodies(text, "task")
    if not found:
        return None
    rows = [line for line in "\n".join(found).splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError(f"the brief must hold one task block of one JSON object; found {len(rows)} rows")
    try:
        obj = json.loads(rows[0])
    except ValueError as exc:
        raise ValueError(f"the task block is not JSON ({exc})")
    if not isinstance(obj, dict):
        raise ValueError("the task block is not a JSON object")
    checks = obj.get("check")
    if isinstance(checks, str):
        checks = obj["check"] = [checks]
    if not isinstance(checks, list) or not checks:
        raise ValueError("the task block has an empty check list")
    if not all(isinstance(c, str) and c.strip() for c in checks):
        raise ValueError("every check entry must be a non-empty string")
    write_set = obj.get("writeSet", [])
    if not isinstance(write_set, list) or not all(isinstance(p, str) for p in write_set):
        raise ValueError("writeSet must be a list of paths")
    return obj


def _kills_process(command):
    """True when a segment of `command` runs taskkill, kill or Stop-Process, or hands such a word to a program
    that is not a text command; a segment that launches tools/reap.py is the reaper itself."""
    for segment in _SEGMENT.split(command):
        words = segment.split()
        while words and re.match(r"\w+=", words[0]):
            words.pop(0)
        if not words or not _KILL_WORD.search(segment):
            continue
        launcher = os.path.splitext(os.path.basename(words[0].replace("\\", "/")))[0].lower()
        if launcher in _TEXT_COMMANDS:
            continue
        if launcher in _REAPER_LAUNCHERS and any(
                w.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1] == "reap.py" for w in words[1:3]):
            continue
        return True
    return False


def floor_reason(command):
    """Why `command` matches a floor guard's predicate (an index write, a commit, a push, a discard
    `git_guardrails` blocks, a process kill outside tools/reap.py), or None. The guards do not see a check run
    here, so this replays them."""
    _run, publishes_index, writes_index, git_invocations, _bash = _consume_deps()
    import git_guardrails
    for inv in git_invocations(command, str(REPO)):
        if git_guardrails.verdict([inv.sub, *inv.rest], cwd=inv.cwd, git_env=inv.git_env):
            return "discards work git_guardrails blocks"
        if publishes_index(inv.sub, inv.rest):
            return "writes a commit"
        if writes_index([inv.sub, *inv.rest]):
            return "writes the index"
        if inv.sub == "push":
            return "pushes"
    if _kills_process(command):
        return "kills a process outside tools/reap.py"
    return None


def _record_args(record):
    args = record.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {}
    return args if isinstance(args, dict) else {}


def _record_jobs(args):
    """Every job row of a run's args: dispatch.js's `jobs` and dispatch_chains.js's `chains[].jobs`."""
    rows = list(args.get("jobs") or [])
    for chain in args.get("chains") or []:
        if isinstance(chain, dict):
            rows += chain.get("jobs") or []
    return [j for j in rows if isinstance(j, dict)]


def _run_start_ms(record, label):
    starts = [e.get("startedAt") for e in record.get("workflowProgress") or []
              if isinstance(e, dict) and e.get("type") == "workflow_agent" and e.get("label") == label
              and isinstance(e.get("startedAt"), (int, float))]
    return starts[-1] if starts else None


def _shell_command(command, run, out_dir, label):
    """The shell line a check runs. The literal `claims` routes to `post_check.py claims` over the run."""
    if command != "claims":
        return command
    out = out_dir / (_UNSAFE.sub("_", str(label)) + ".claims.json")
    return ("python3 .claude/tools/post_check.py claims --workflow-dir " + shlex.quote(run.resolve().as_posix())
            + " --root " + shlex.quote(REPO.as_posix()) + " --out " + shlex.quote(out.as_posix()))


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def _changed_rows(root, write_set, returned, since):
    """One row per path in writeSet or the deliverable's changedPaths: its git status (`unchanged` when
    git lists none), and whether it lies outside writeSet. A gitignored file counts (`!!`) when its
    mtime is after `since` (epoch seconds): a new proof or fixture under a gitignored writeSet entry is a change."""
    scope = list(write_set) + [p for p in returned if p not in write_set]
    rows, seen = [], set()
    if scope:
        for line in _git(root, "status", "--short", "--ignored", "--untracked-files=all", "--", *scope).splitlines():
            if len(line) < 4:
                continue
            path = line[3:].split(" -> ")[-1]
            if line[:2] == "!!" and not _written_since(root, path, since):
                continue
            seen.add(path)
            rows.append({"status": line[:2], "path": path, "outside": not _inside(path, write_set)})
    for path in scope:
        if path not in seen:
            rows.append({"status": "unchanged", "path": path, "outside": not _inside(path, write_set)})
    return rows


def _inside(path, write_set):
    """True when `path` is a writeSet entry or lies under a writeSet directory entry."""
    return any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in write_set)


def _written_since(root, path, since):
    full = os.path.join(str(root), path)
    return os.path.isfile(full) and os.path.getmtime(full) > since


def _tail(text):
    return "\n".join(line[:300] for line in text.rstrip().splitlines()[-TAIL_LINES:])


def _sidecar_source(source, label):
    """(record, captured stdout, out dir) for a sidecar source, or None. A fan-out out dir holds
    `<label>.record.json` and `<label>.out.json`; a `-R` record is read with its `<record>.out`."""
    src = Path(source)
    if src.is_dir():
        return src / f"{label}.record.json", src / f"{label}.out.json", src
    if src.is_file() and src.name.endswith(".record.json"):
        return src, Path(str(src) + ".out"), src.parent
    return None


def _resolve(root, path):
    value = Path(path or "")
    return value if value.is_absolute() else Path(root) / value


def _workflow_origin(root, run, label, brief):
    record_path = _run_record_path(run)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"run record {record_path} is missing, so the run is not terminal")
    if record.get("status") not in TERMINAL_STATUSES:
        raise Refused(f"run {run.name} is not terminal (status {record.get('status')})")
    args = _record_args(record)
    seed_jobs = [j for j in _record_jobs(args) if j.get("label") == label]
    if not seed_jobs:
        raise Refused(f"label {label} is not a job of run {run.name}")
    start_ms = _run_start_ms(record, label)
    if start_ms is None:
        raise Refused(f"run record has no startedAt for label {label}")
    spill = _spill_dir_of(run)
    if not spill:
        raise Refused(f"run record names no spillDir for label {label}")
    journal = dict(_journal_jobs(run / "journal.jsonl"))
    if label not in journal:
        raise Refused(f"label {label} has no journal entry")
    return {"out_dir": Path(spill) if Path(spill).is_absolute() else REPO / spill,
            "value": journal.get(label), "start_s": start_ms / 1000,
            "brief": _resolve(root, brief or seed_jobs[0].get("promptPath")),
            "seed_jobs": seed_jobs,
            "session": run.parent.parent.parent, "record_dir": None, "run": run}


def _sidecar_origin(root, source, label, brief):
    paths = _sidecar_source(source, label)
    if paths is None:
        raise Refused("consume reads a Workflow run dir, a sidecar fan-out out dir or a -R record")
    record_path, stdout_path, out_dir = paths
    if not brief:
        raise Refused("a sidecar source needs --brief: its record and inventory hold no brief path")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"sidecar record {record_path} is missing or unreadable")
    if not isinstance(record, dict) or record.get("label") != label:
        raise Refused(f"sidecar record {record_path.name} does not hold label {label}")
    if not Path(str(record_path) + ".exit").is_file():
        raise Refused(f"sidecar {label} is not terminal (no {record_path.name}.exit)")
    pid = Path(str(record_path) + ".pid")
    if not pid.is_file():
        raise Refused(f"sidecar {label} has no {pid.name}, so its run start is unknown")
    return {"out_dir": out_dir, "value": final_message_of(stdout_path), "start_s": pid.stat().st_mtime,
            "stdout": stdout_path,
            "brief": _resolve(root, brief),
            "seed_jobs": [{"label": label, "model": record.get("requestedModel"), "effort": record.get("effort")}],
            "session": None, "record_dir": record_path.parent, "run": None,
            "claims_path": Path(str(record_path) + ".claims.json")}


def _read_task(brief_file):
    """The brief's one task block, refused when it is unreadable, absent, or holds a floor-guarded check."""
    try:
        text = brief_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"brief {brief_file} is unreadable ({exc.strerror})")
    try:
        task = task_block(text)
    except ValueError as exc:
        raise Refused(str(exc))
    if task is None:
        raise Refused("brief has no task block")
    for command in task["check"]:
        why = None if command == "claims" else floor_reason(command)
        if why:
            raise Refused(f"check {command!r} is floor-guarded ({why}); run it in the parent's own Bash call")
    return task


def _write_manifest(root, label, origin):
    """Write `<label>.seed.json` and `<label>.manifest.json` beside the deliverable. Returns (manifest path,
    None) or (None, refusal reason). The rating is the parent's; the manifest only joins evidence."""
    import orchestration_metrics as om
    stem = _UNSAFE.sub("_", str(label))
    out_dir = origin["out_dir"]
    seed_path, manifest_path = out_dir / f"{stem}.seed.json", out_dir / f"{stem}.manifest.json"
    seed = om.seed_from_jobs(origin["seed_jobs"])
    with open(seed_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(seed, fh, indent=2)
    verdicts = Path(root) / ".claude" / "orchestration_verdicts.json"
    try:
        om.write_manifest(str(seed_path), str(manifest_path), session=origin["session"],
                          sidecar_record_dir=origin["record_dir"],
                          verdicts_path=str(verdicts) if verdicts.is_file() else None)
    except (OSError, ValueError, om.ManifestError) as exc:
        return None, str(exc)
    return manifest_path, None


def _sidecar_claims_verdict(path):
    """(verdict, tail) for a sidecar's `<record>.claims.json`. The launcher's file nests the report under
    `verification`; a post_check report is flat. `pass` is PASS, any other status FAIL (the exit post_check
    claims gives a Workflow run); no readable file is UNCHECKED, never a pass."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return "UNCHECKED", f"no {path.name}: the launcher wrote no claims verdict for this job"
    except ValueError:
        return "UNCHECKED", f"{path.name} is not valid JSON"
    if not isinstance(report, dict):
        return "UNCHECKED", f"{path.name} is not a JSON object"
    nested = report.get("verification")
    verdict = nested if isinstance(nested, dict) and "status" in nested else report
    status = verdict.get("status")
    if status == "pass":
        return "PASS", ""
    if not isinstance(status, str) or status == "unavailable":
        return "UNCHECKED", f"{path.name} holds no claims verdict (status {status!r})"
    counts = verdict.get("counts") or {}
    detail = ", ".join(f"{k} {v}" for k, v in counts.items())
    errors = "; ".join(str(e) for e in (verdict.get("errors") or [])[:3])
    return "FAIL", _tail(f"claims {status}" + (f" ({detail})" if detail else "") + (f": {errors}" if errors else ""))


def _judge(root, label, origin, check_timeout, result):
    """Write the deliverable, list changed paths and the diff, then run the brief's checks in order (or
    mark them HELD when the brief is newer than the run start). Shared by every source."""
    task = _read_task(origin["brief"])
    out_dir = origin["out_dir"]
    status, path = _materialize_one(out_dir, label, origin["value"])
    if status in ("no-result", "spill-missing"):
        raise Refused(f"label {label} has no deliverable ({status} {path or ''})".rstrip())
    deliverable = deliverable_path(out_dir, label)
    text = deliverable.read_text(encoding="utf-8", errors="replace")
    returned = []
    for raw in _RETURNED.findall(text):
        try:
            values = json.loads(raw)
        except ValueError:
            raise Refused(f"the deliverable's changedPaths line is not a JSON list: {raw[:80]}")
        if not isinstance(values, list) or not all(isinstance(p, str) for p in values):
            raise Refused("the deliverable's changedPaths line must be a JSON list of paths")
        returned.extend(values)
    reply = deliverable_path(out_dir, label + ".reply")
    if reply.is_file():
        result["reply"] = reply.as_posix()
    elif origin.get("stdout"):
        try:
            trailing = answered_after_report(Path(origin["stdout"]).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            trailing = False
        if trailing:
            result["delivery_flag"] = "the final message answered a later turn after an earlier reply and no report file was kept"
    result.update(deliverable=deliverable.as_posix(),
                  digest="\n".join(text.splitlines()[:DIGEST_LINES])[:DIGEST_CHARS],
                  changed=_changed_rows(root, task.get("writeSet", []), returned, origin["start_s"]))
    new = [row["path"] for row in result["changed"] if row["status"] in ("??", "!!")]
    tracked = [row["path"] for row in result["changed"] if row["status"] not in ("??", "!!", "unchanged")]
    diff = ""
    if tracked:
        diff = _git(root, "diff", "--stat", "HEAD", "--", *tracked) + _git(root, "diff", "HEAD", "--", *tracked)
    for path in new:
        if len(diff) >= DIFF_CHARS:
            break
        diff += _git(root, "diff", "--no-index", "--", "/dev/null", path)
    if diff:
        result["diff"] = diff[:DIFF_CHARS]
    manifest, refused = _write_manifest(root, label, origin)
    result.update(manifest=manifest.as_posix() if manifest else None, manifest_refused=refused)
    if origin["brief"].stat().st_mtime > origin["start_s"]:
        result["checks"] = [{"n": n, "verdict": "HELD", "command": c, "tail": ""}
                            for n, c in enumerate(task["check"], 1)]
        return result
    run_bounded, _pub, _writes, _inv, git_bash = _consume_deps()
    try:
        bash = git_bash()
    except RuntimeError as exc:
        raise Refused(str(exc))
    stopped = False
    for n, command in enumerate(task["check"], 1):
        if stopped:
            result["checks"].append({"n": n, "verdict": "NOT-RUN", "command": command, "tail": ""})
            continue
        if command == "claims" and origin["run"] is None:
            verdict, tail = _sidecar_claims_verdict(origin["claims_path"])
            result["checks"].append({"n": n, "verdict": verdict, "command": command, "tail": tail})
            stopped = verdict != "PASS"
            continue
        shell = _shell_command(command, origin["run"], out_dir, label)
        try:
            proc = run_bounded([bash, "-c", shell], check_timeout, cwd=str(root))
        except OSError as exc:
            verdict, tail = "FAIL", str(exc)
        else:
            verdict = "TIMEOUT" if proc.returncode is None else ("PASS" if proc.returncode == 0 else "FAIL")
            tail = "" if verdict == "PASS" else _tail(proc.stdout + proc.stderr)
        result["checks"].append({"n": n, "verdict": verdict, "command": command, "tail": tail})
        stopped = verdict != "PASS"
    return result


def _consume(source, label, root, check_timeout, brief, result):
    if (Path(source) / "journal.jsonl").is_file():
        origin = _workflow_origin(root, Path(source), label, brief)
    else:
        origin = _sidecar_origin(root, source, label, brief)
    return _judge(root, label, origin, check_timeout, result)


def consume(source, label, root, check_timeout, brief=None):
    """Consume one label of a Workflow run. Returns {digest, deliverable, checks, changed, diff, manifest,
    refused}: `refused` names why nothing ran; `checks` rows are {n, verdict, command, tail}."""
    result = {"digest": "", "deliverable": None, "checks": [], "changed": [], "diff": "", "manifest": None,
              "manifest_refused": None, "refused": None}
    try:
        return _consume(source, label, Path(root), check_timeout, brief, result)
    except Refused as exc:
        result["refused"] = str(exc)
        return result


def main_consume(argv):
    ap = argparse.ArgumentParser(prog="dispatch_result.py consume",
                                 description="Consume one label of a Workflow run: deliverable, checks, changed paths, diff.")
    ap.add_argument("source", help="a Workflow run's transcript dir, a sidecar fan-out out dir or a -R record")
    ap.add_argument("--label", required=True, help="the job label to consume")
    ap.add_argument("--check-timeout", type=float, default=DEFAULT_CHECK_TIMEOUT,
                    help="seconds each check may run before it reports TIMEOUT")
    ap.add_argument("--brief", default=None, help="the brief path: required for a sidecar source, optional for a Workflow run whose record names it")
    args = ap.parse_args(argv)
    res = consume(args.source, args.label, REPO, args.check_timeout, brief=args.brief)
    if res["refused"]:
        print("refused: " + res["refused"], file=sys.stderr)
        return 2
    print("DIGEST")
    print(res["digest"])
    print(f"DELIVERABLE {res['deliverable']}")
    if res.get("reply"):
        print(f"REPLY {res['reply']}")
    if res.get("delivery_flag"):
        print(f"WARNING {res['delivery_flag']}")
    for row in res["checks"]:
        print(f"CHECK {row['n']} {row['verdict']} {row['command']}")
        if row["tail"]:
            print(row["tail"])
    for row in res["changed"]:
        print(f"CHANGED {row['status']} {row['path']}" + (" outside-writeSet" if row["outside"] else ""))
    print("DIFF")
    print(res["diff"])
    print(f"MANIFEST {res['manifest']}" if res["manifest"] else f"MANIFEST refused: {res['manifest_refused']}")
    return 1 if any(row["verdict"] in ("FAIL", "TIMEOUT", "HELD", "UNCHECKED") for row in res["checks"]) else 0


def _block_spec(value):
    name, sep, keys = value.partition("=")
    keys = [k.strip() for k in keys.split(",") if k.strip()]
    if not name.strip() or not sep or not keys:
        raise argparse.ArgumentTypeError(f"{value!r} is not <name>=<key,key...>")
    return name.strip(), keys


def main_check(argv):
    ap = argparse.ArgumentParser(prog="dispatch_result.py check",
                                 description="Check the fenced JSON Lines blocks of a spill file.")
    ap.add_argument("spill", help="the spill markdown file")
    ap.add_argument("--block", action="append", required=True, type=_block_spec,
                    metavar="NAME=KEY,KEY", help="a fenced block and the keys each row must hold")
    args = ap.parse_args(argv)
    try:
        text = Path(args.spill).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"{args.spill}: {exc}", file=sys.stderr)
        return 2
    lines, bad = check_spill(text, dict(args.block))
    print("\n".join(lines))
    print("OK" if bad == 0 else f"BAD {bad}")
    return 1 if bad else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "check":
        return main_check(argv[1:])
    if argv and argv[0] == "consume":
        return main_consume(argv[1:])
    ap = argparse.ArgumentParser(description="Write each dispatch job's deliverable to <outDir>/<label>.md.")
    ap.add_argument("dir", help="a Workflow run's transcript dir, or a sidecar fan-out out dir")
    ap.add_argument("--out-dir", default=None, help="where <label>.md lands (Workflow runs only)")
    args = ap.parse_args(argv)
    src = Path(args.dir)
    if not src.is_dir():
        print(f"{src} is not a directory", file=sys.stderr)
        return 2
    if (src / "journal.jsonl").is_file():
        out = args.out_dir or _spill_dir_of(src)
        if not out:
            print(f"{src}: the run record names no spillDir; pass --out-dir", file=sys.stderr)
            return 2
        out = Path(out)
        return materialize_workflow(src, out if out.is_absolute() else REPO / out)
    if any(src.glob("*.out.json")):
        if args.out_dir:
            print("--out-dir applies to a Workflow run; a fan-out writes into its own out dir",
                  file=sys.stderr)
            return 2
        return materialize_fanout(src)
    print(f"{src} holds neither a Workflow journal.jsonl nor sidecar <label>.out.json files",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
