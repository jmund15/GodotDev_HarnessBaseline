#!/usr/bin/env python3
"""`dispatch_result.py take`: the facts around a returned author job, in one call.

USAGE
    python3 .claude/tools/dispatch_result.py take <fan-out out dir | <label>.record.json> --label <label>
        [--workdir DIR] [--base REF] [--root DIR] [--run-proofs] [--proof-timeout S] [--created]

`consume` judges a job against its brief's checks. `take` serves the session's return loop for a job that
worked in its own worktree on its own branch, and needs no brief. It prints facts only; which commits land,
the rating and whether to retire stay the session's judgment (`instruction_quality` §3).

    JOB          label, exit code, workdir and branch
    REPORT/REPLY each turn's answer (its result event, else its last text-only assistant message): the report,
                 then each answer to a later turn
    BASE/COMMITS the merge-base with --base (default `main`), then each commit oldest first, `new` or `landed`
                 (`git cherry`), and the `git cherry-pick` line over the new ones, full shas
    DIRTY        uncommitted paths the job left in its worktree
    ASSERTS      removed `.claude/tests` assert lines; each is `kept` when an added line of the diff holds the same
                 text at least as many times, else `MISSING` (an edited or dropped assert reads MISSING)
    NEW          each added file, with its row in the worktree's `baseline.lock.json` (`lock=none` when absent)
    PROOF        each changed proof file; `not-run`, or PASS/FAIL/TIMEOUT with `--run-proofs` (run in the worktree)
    RUNNING      jobs of the same out dir that have not ended, and WATCHER: whether this session's watcher is armed
                 (with the re-arm call when it is not and a job runs)
    RETIRE       the delete guard's verdict for the worktree (`hooks/_regenerable_clone.py --retire-check`), asked once
                 every commit is on --base; before that, how many are not
    RATE         the rating call with the outcome and note left to the session

EXIT 0 facts printed; 1 a removed assert is MISSING, a proof failed or timed out, or the worktree holds
uncommitted work; 2 refused (the job has not ended, or its record is unreadable).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for _path in (str(HERE), str(REPO / ".claude" / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

REPORT_LINES = 40
REPORT_CHARS = 3000
SHOWN = 12
RETIRE_CHARS = 400
DEFAULT_PROOF_TIMEOUT = 300
_ASSERT = re.compile(r"^(self\.assert\w*\(|assert\b|check\(|case\()")
_PROOF = re.compile(r"^\.claude/tests/(test_[^/]+\.py|[^/]+_test\.py)$")


class Refused(Exception):
    pass


def _git(work, *args):
    done = subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.returncode, done.stdout


def _stream_events(text):
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return [e for e in events if isinstance(e, dict)]


def turn_reports(stream_text):
    """Each turn's answer, in order: its result event's answer (a schema job answers through a tool call, so
    its last text is a mid-run note), else its last text-only assistant message. A turn ends at a user message
    that carries text (hook feedback or a follow-up), never at a tool result; a codex stream or one with no
    such message falls back to the stream's final message."""
    import dispatch_result
    reports, current = [], None
    for event in _stream_events(stream_text):
        blocks = dispatch_result._blocks(event)
        if event.get("type") == "assistant":
            text = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text").strip()
            if text and not any(b.get("type") == "tool_use" for b in blocks):
                current = text
        elif event.get("type") == "result" and not event.get("synthetic"):
            current = dispatch_result.final_message(json.dumps(event)) or current
        elif event.get("type") == "user" and not any(b.get("type") == "tool_result" for b in blocks):
            if any(b.get("type") == "text" for b in blocks) and current is not None:
                reports.append(current)
                current = None
    if current is not None:
        reports.append(current)
    if not reports:
        final = dispatch_result.final_message(stream_text)
        reports = [final] if final else []
    return reports


def _head(text):
    lines = text.splitlines()
    shown = "\n".join(lines[:REPORT_LINES])[:REPORT_CHARS]
    more = len(lines) - REPORT_LINES
    return shown + ("\n  ... %d more line(s)" % more if more > 0 else "")


def _source(source, label):
    src = Path(source)
    if src.is_dir():
        import sidecar_fanout
        record, out_dir = sidecar_fanout.latest_attempt_record_path(src, label) or src / f"{label}.record.json", src
    elif src.is_file() and src.name.endswith(".record.json"):
        record, out_dir = src, src.parent
    else:
        raise Refused("take reads a fan-out out dir or a <label>.record.json")
    stream = Path(str(record) + ".out")
    if not stream.is_file():
        stream = out_dir / f"{label}.out.json"
    return record, stream, out_dir


def _removed_asserts(diff):
    removed, added = [], {}
    for line in diff.splitlines():
        if line.startswith(("---", "+++")) or not line[:1] in ("-", "+"):
            continue
        text = line[1:].strip()
        if line[0] == "-" and _ASSERT.match(text):
            removed.append(text)
        elif line[0] == "+":
            added[text] = added.get(text, 0) + 1
    rows = []
    for text in removed:
        stands = added.get(text, 0) > 0
        if stands:
            added[text] -= 1
        rows.append((text, stands))
    return rows


def _lock_rows(work):
    code, text = _git(work, "show", "HEAD:.claude/baseline.lock.json")
    try:
        files = json.loads(text).get("files") if code == 0 else None
    except ValueError:
        files = None
    return files if isinstance(files, dict) else {}


def _run_proof(work, path, timeout):
    try:
        done = subprocess.run([sys.executable, path], cwd=str(work), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    tail = "\n".join((done.stdout + done.stderr).rstrip().splitlines()[-8:])
    return ("PASS", "") if done.returncode == 0 else ("FAIL", tail)


def _git_facts(work, base_ref, out, run_proofs, proof_timeout):
    """Append the worktree's git facts to `out`; return (a finding was printed, commits still to land)."""
    finding = False
    code, branch = _git(work, "rev-parse", "--abbrev-ref", "HEAD")
    code, base = _git(work, "merge-base", "HEAD", base_ref)
    base = base.strip()
    if code != 0 or not base:
        raise Refused(f"no merge-base of HEAD and {base_ref} in {work}")
    out.append(f"BASE {base} (merge-base with {base_ref}) branch {branch.strip()}")
    _, listing = _git(work, "log", "--reverse", "--format=%H%x09%s", f"{base}..HEAD")
    commits = [row.split("\t", 1) for row in listing.splitlines() if row]
    _, cherry = _git(work, "cherry", base_ref, "HEAD")
    landed = {row[2:].strip() for row in cherry.splitlines() if row.startswith("-")}
    out.append(f"COMMITS {len(commits)}")
    for sha, subject in commits:
        out.append(f"  {'landed' if sha in landed else 'new'} {sha} {subject}")
    new = [sha for sha, _ in commits if sha not in landed]
    if new:
        out.append("CHERRY-PICK git cherry-pick " + " ".join(new))
    _, dirty = _git(work, "status", "--short", "--ignore-submodules=all")
    rows = [row for row in dirty.splitlines() if row]
    if rows:
        finding = True
        out.append(f"DIRTY {len(rows)} uncommitted path(s):")
        out += ["  " + row for row in rows[:SHOWN]]
    _, tests = _git(work, "diff", "-U0", base, "HEAD", "--", ".claude/tests")
    asserts = _removed_asserts(tests)
    kept = sum(1 for _, stands in asserts if stands)
    out.append(f"ASSERTS removed {len(asserts)}, kept {kept}, MISSING {len(asserts) - kept}")
    for text, stands in asserts:
        if not stands:
            finding = True
            out.append("  MISSING " + text)
    _, status = _git(work, "diff", "--name-status", base, "HEAD")
    changed = [row.split("\t") for row in status.splitlines() if row]
    lock = _lock_rows(work)
    for row in changed:
        if row[0].startswith("A"):
            entry = lock.get(row[-1])
            state = entry.get("status", "row") if isinstance(entry, dict) else "none"
            out.append(f"NEW {row[-1]} lock={state}")
        elif row[0].startswith(("D", "R")):
            out.append(f"{row[0][0]} {' -> '.join(row[1:])}")
    for row in changed:
        path = row[-1]
        if row[0][0] in "AM" and _PROOF.match(path):
            if not run_proofs:
                out.append(f"PROOF {path} not-run")
                continue
            verdict, tail = _run_proof(work, path, proof_timeout)
            out.append(f"PROOF {path} {verdict}")
            if tail:
                out.append("\n".join("  " + line for line in tail.splitlines()))
            finding = finding or verdict != "PASS"
    return finding, len(new)


def _watcher_lines(out_dir, label, session):
    import sidecar_fanout
    import watch_records
    labels = {re.sub(r"(\.attempt\d+)?\.record\.json$", "", p.name) for p in Path(out_dir).glob("*.record.json")}
    running = sorted(other for other in labels - {label} if watch_records.ended_code(str(
        sidecar_fanout.latest_attempt_record_path(out_dir, other) or Path(out_dir) / f"{other}.record.json")) is None)
    if not running:
        return ["WATCHER not needed: no other job of this out dir is running"]
    lines = [f"RUNNING {len(running)}: {', '.join(running)}"]
    live = False
    if session:
        import _hook_state
        live = watch_records.session_watcher_live(_hook_state.read_json_salvage(_hook_state.state_path(session)))
    if live:
        return lines + ["WATCHER armed"]
    return lines + [f"WATCHER NOT ARMED: re-arm now {watch_records.ARM_CALL}: {watch_records.ARM_COMMAND} --await-launch"]


def take(source, label, root, base_ref="main", workdir=None, run_proofs=False,
         proof_timeout=DEFAULT_PROOF_TIMEOUT, created=False, session=None):
    """The printed facts as (lines, finding). Raises Refused when the job has not ended."""
    import dispatch_result
    import watch_records
    record, stream, out_dir = _source(source, label)
    try:
        json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"record {record} is missing or unreadable")
    code = watch_records.ended_code(str(record))
    if code is None:
        raise Refused(f"job {label} has not ended (no ended row or .exit beside {record.name})")
    work = workdir or dispatch_result._inventory_job(out_dir, label).get("workdir")
    out = [f"JOB {label} exit={code or '?'} workdir={work or 'none'}"]
    try:
        text = stream.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
        out.append(f"REPORT unreadable: {stream}")
    for n, report in enumerate(turn_reports(text)):
        out.append("REPORT" if n == 0 else "REPLY")
        out.append(_head(report))
    if not work:
        out.append("NO WORKDIR: a job with no worktree has no commits to take")
    elif not Path(work).is_dir():
        out.append(f"NO WORKDIR: {work} is gone; its commits are on its branch if it still exists")
    finding, unlanded = False, None
    if work and Path(work).is_dir():
        finding, unlanded = _git_facts(work, base_ref, out, run_proofs, proof_timeout)
    out += _watcher_lines(out_dir, label, session)
    if unlanded:
        out.append(f"RETIRE not yet: {unlanded} commit(s) are not on {base_ref}; take again after they land")
    elif unlanded == 0:
        # The delete guard's own walk of the worktree, about ten seconds: asked only when it can answer yes.
        from _regenerable_clone import is_retired_worktree
        reasons = []
        retired = is_retired_worktree(str(work), os.getcwd(), str(root), allow_ignored=created, reasons=reasons)
        why = "; ".join(reasons)
        out.append(f"RETIRE {work}: " + ("retired: safe to delete" if retired else
                                         "blocked: " + (why if len(why) <= RETIRE_CHARS else why[:RETIRE_CHARS] + " ...")))
    out.append(f"RATE python3 .claude/tools/orchestration_metrics.py --rate {label} <clean|defects|rework|discarded> "
               f"--shape <right|over|under> --note \"<what the work needed>\"")
    return out, finding


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="dispatch_result.py take", description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="a sidecar fan-out out dir or a <label>.record.json")
    ap.add_argument("--label", required=True)
    ap.add_argument("--workdir", default=None, help="the job's worktree (default: its fan-out inventory's)")
    ap.add_argument("--base", default="main", help="the ref the commits are measured against")
    ap.add_argument("--root", default=None, help="the main checkout the retire verdict is judged from")
    ap.add_argument("--run-proofs", action="store_true", help="run each changed proof file in the worktree")
    ap.add_argument("--proof-timeout", type=float, default=DEFAULT_PROOF_TIMEOUT)
    ap.add_argument("--created", action="store_true", help="this session created the worktree (retire verdict)")
    args = ap.parse_args(argv)
    try:
        lines, finding = take(args.source, args.label, args.root or REPO, args.base, args.workdir, args.run_proofs,
                              args.proof_timeout, args.created, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    except Refused as exc:
        print("refused: " + str(exc), file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 1 if finding else 0
