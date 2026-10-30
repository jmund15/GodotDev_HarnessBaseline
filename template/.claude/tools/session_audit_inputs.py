#!/usr/bin/env python3
"""Freeze /session_audit's harness-quality inputs before checking coverage or dispatching.

Usage: session_audit_inputs.py --root . --base-ref origin/main --session-base <sha>
       --roster <json> --out .claude/scratch/<run>/inputs.json [--files <paths>...]
       [--widen] [--session <sid>] [--task <id>] [--plan <path>]

Admission belongs to triage.admits, not needsAudit. The latter describes fresh input obligations.
A selected task's record, plan and live/archived decisions are frozen even without harness text.

The default outgoing range matches review_ledger owed: upstream..HEAD, or origin/main..HEAD without an upstream.
An explicit --base-ref freezes <base-ref>..HEAD instead, so an owed audit of already-pushed doctrine still runs.
All doctrine commits supply messages and per-commit diffs, including transient paths and
rename sources. --widen unions their paths into the subject. restate-groups.txt carries
`restate_scan.py pairs --changed` over the subject files (peer-dirty paths excluded) since
--session-base, the duplicate-sentence groups the harness-quality seat reads; a nonzero
`restateScanExit` means it did not scan. Coverage uses the ledger's
uncovered_premises and final-state checks, never text coverage alone. A fresh output
folder holds the frozen IDs, blob map, every typed evidence artifact and one view per mandate.
A view lists the exact artifacts that mandate's lens reads, each once: the doctrine commits,
the session's decisions, `restate-groups.txt`, each doctrine commit's diff and each admitted
subject's diff. A subject's whole frozen source is written too and linked from its diff, and
only a mandate whose lens judges source text lists it (`source_required`). Every admitted
(file, mandate) pair is checked to keep an artifact in its own view before the packet is
written; the views, never a `subject-N` filename, are what a caller maps to a seat. An
existing packet is refused. The caller forwards head, blobs and premiseCommits unchanged
through its review route.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import review_ledger as ledger
import task_record
from cli_usage import usage

# Which mandates need a file's whole frozen source as a must-read input. The instruction-quality
# mandate judges the hunk and opens a file only to test consistency, so its diff carries the
# evidence; a mandate this map does not name keeps the source, so a narrowed view never drops code.
SOURCE_REQUIRED = {"harness-quality": False}


def source_required(mandate: str) -> bool:
    """Whether a mandate's view must list a subject file's whole frozen source. Unknown mandates keep it."""
    return SOURCE_REQUIRED.get(mandate, True)


def git(root, *args, optional=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        if optional:
            return None
        raise ledger.ToolError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout.decode("utf-8", errors="replace")


def doctrine_commits(root, base, head):
    commits = [dict(commit, files=[path for path in commit["files"] if ledger.is_doctrine_path(path)])
               for commit in ledger.range_commits(root, base, head)]
    return [commit for commit in commits if commit["files"]]


def restate_groups(root, session_base, files):
    if not files:
        return 2, "restate_scan: no subject files; nothing was scanned\n"
    scan = Path(__file__).resolve().parent / "restate_scan.py"
    result = subprocess.run([sys.executable, str(scan), "--repo", str(root), "pairs", "--changed",
                             "--base", session_base, "--paths", *files], capture_output=True)
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def session_decisions(root, session, task=None):
    if not session and not task:
        return []
    rows = []
    directory = Path(os.environ.get("HARNESS_TASK_RECORD_DIR") or root / ".claude/logs/tasks")
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if path.stem != task and (not session or
                (record.get("session_id") or "")[:8].lower() != session[:8].lower()):
            continue
        for decision in task_record.all_decisions(path.stem, directory=directory):
            rows.append({"path": path.relative_to(root).as_posix(), "task": record.get("task_id"),
                         "decision": decision})
    return rows


def freeze(args):
    root = Path(args.root).resolve()
    out = Path(args.out)
    out = (root / out).resolve() if not out.is_absolute() else out.resolve()
    out.relative_to(root)
    if out.parent.exists():
        raise ledger.ToolError(f"input folder already exists: {out.parent}; use a fresh run path")
    head = git(root, "rev-parse", "HEAD").strip()
    # An explicit --base-ref names the range to audit, pushed or not; without one the outgoing range is upstream..HEAD.
    upstream = None if args.base_ref else git(root, "rev-parse", "--verify", "-q", "@{u}", optional=True)
    args.base_ref = args.base_ref or "origin/main"
    base = upstream.strip() if upstream else git(root, "rev-parse", args.base_ref).strip()
    commits = doctrine_commits(root, base, head)
    doctrine_files = sorted({path for commit in commits for path in commit["files"]})
    files = ledger.normalize_files(root, args.files or [])
    if args.widen:
        files = sorted(set(files) | set(doctrine_files))
    catalog = ledger.load_catalog()
    roster = ledger._load_roster(args.roster, catalog)
    ctx = ledger.Ctx(root, args.base_ref, snapshot=head)
    blobs = {}
    committed_only = set(ledger.normalize_files(root, args.committed_only))
    for path in files:
        source = root / path
        if path in committed_only:
            blob = git(root, "rev-parse", "--verify", f"{head}:{path}", optional=True)
            if blob:
                blobs[path] = blob.strip()
                ctx.overrides[path] = blob.strip()
            else:
                state = ctx.deleted_hash(path)
                if state:
                    ctx.overrides[path] = state
        elif source.is_file():
            # -w stores the frozen bytes: the recorder resolves this id after later edits move the file on.
            blob = git(root, "hash-object", "-w", "--", path).strip()
            blobs[path] = blob
            ctx.overrides[path] = blob
        else:
            state = ctx.deleted_hash(path)
            if state:
                ctx.overrides[path] = state
    rows, _, problem = ledger.read_rows(ledger.ledger_path(root))
    if problem and ledger.ledger_path(root).exists():
        raise ledger.LedgerUnreadable(problem)
    escapes, problem = ledger.read_escapes(root)
    if problem:
        raise ledger.LedgerUnreadable(problem)
    cover = ledger.Cover(ctx, catalog, rows, escapes, "session")
    premises = ledger.uncovered_premises(cover, commits, roster)
    text = ledger.uncovered_pairs(cover, files, ["harness-quality"], roster)
    task = getattr(args, "task", None)
    decisions = session_decisions(root, args.session, task)
    record = None
    plan = getattr(args, "plan", None)
    if task:
        directory = Path(os.environ.get("HARNESS_TASK_RECORD_DIR") or root / ".claude/logs/tasks")
        record = json.loads((directory / (task + ".json")).read_text(encoding="utf-8"))
        if not plan:
            plan = next((p for p in (record.get("scope") or {}).get("files", [])
                         if Path(p).name == "short_plan.md" or
                         ".claude/plans/" in str(p).replace("\\", "/")), None)
        if not plan:
            raise ledger.ToolError(f"task {task} has no plan; pass --plan")
    plan_text = None
    if plan:
        source = Path(plan)
        source = (root / source).resolve() if not source.is_absolute() else source.resolve()
        source.relative_to(root)
        plan_text = source.read_text(encoding="utf-8")
    messages = []
    commit_diffs = []
    for commit in commits:
        messages.append(git(root, "show", "-s", "--format=%H%n%s%n%b", commit["sha"]))
        commit_diffs.append((commit, git(
            root, "show", "--format=fuller", "--cc", "--no-renames", commit["sha"], "--", *commit["files"])))
    subjects = []
    for index, path in enumerate(files):
        committed = git(root, "diff", "--no-renames", f"{args.session_base}..{head}", "--", path)
        dirty = "" if path in committed_only else git(root, "diff", "HEAD", "--", path)
        deleted = git(root, "cat-file", "-e", f"{args.session_base}:{path}", optional=True) is not None and \
            git(root, "cat-file", "-e", f"{head}:{path}", optional=True) is None
        if (path not in committed_only and not deleted and not git(root, "ls-files", "--", path).strip()
                and (root / path).is_file()):
            dirty = (root / path).read_text(encoding="utf-8", errors="replace")
        content = None
        if path in blobs:
            content = (git(root, "show", f"{head}:{path}") if path in committed_only
                       else (root / path).read_text(encoding="utf-8", errors="replace"))
        subjects.append((index, path, f"{path}\n{committed}\n{dirty}", content))
    for path, blob in blobs.items():
        if path not in committed_only and git(root, "hash-object", "--", path).strip() != blob:
            raise ledger.ToolError(f"{path} changed while freezing inputs; retry with a fresh run path")
    if git(root, "rev-parse", "HEAD").strip() != head:
        raise ledger.ToolError("HEAD changed while freezing inputs; retry with a fresh run path")
    scan_exit, scan_text = restate_groups(root, args.session_base, [f for f in files if f not in committed_only])
    out.parent.mkdir(parents=True)

    def write(name, value, structured=False):
        target = out.parent / name
        target.write_text(json.dumps(value, indent=2) + "\n" if structured else value,
                          encoding="utf-8", newline="\n")
        return target.relative_to(root).as_posix()

    artifacts = []

    def emit(kind, name, body, **meta):
        rel = write(name, body)
        entry = {"kind": kind, "artifact": rel, "bytes": len(body.encode("utf-8")), **meta}
        artifacts.append(entry)
        return entry

    ids = write("premise-commits.json", [c["sha"] for c in commits], True)
    blob_path = write("blobs.json", blobs, True)
    decisions_artifact = emit("decisions", "decisions.json", json.dumps(decisions, indent=2) + "\n")
    payload = "\n".join(messages) + "\nSession task_record.py decisions:\n" + json.dumps(decisions, indent=2)
    doctrine_artifact = emit("doctrine", "doctrine-commits.md",
                             "\n".join("> " + line for line in payload.splitlines()) + "\n")
    groups_artifact = emit("restate", "restate-groups.txt", scan_text)
    commit_artifacts = [emit("commit-diff", f"commit-{commit['sha']}.diff", diff,
                             commit=commit["sha"], files=list(commit["files"]))
                        for commit, diff in commit_diffs]
    subject_artifacts, source_artifacts = {}, {}
    for index, path, diff, content in subjects:
        entry = emit("subject-diff", f"subject-{index}.diff", diff, file=path)
        subject_artifacts[path] = entry
        if content is not None:
            source = emit("source", f"subject-{index}.txt", content, file=path)
            source_artifacts[path] = source
            entry["source"] = source["artifact"]
    extras = []
    if record is not None:
        extras.append(emit("task-record", "task.json", json.dumps(record, indent=2) + "\n"))
    if plan_text is not None:
        extras.append(emit("plan", "plan.md", plan_text))

    # The admitted coverage map: each (file, mandate) pair this audit still owes evidence for, from the
    # ledger's uncovered text pairs and its uncovered premises.
    admitted = {}
    for pair in list(text) + [pair for pairs in premises.values() for pair in pairs]:
        admitted.setdefault(pair[1], [])
        if pair[0] not in admitted[pair[1]]:
            admitted[pair[1]].append(pair[0])
    mandates = list(dict.fromkeys(mandate for seat in roster for mandate in (seat.get("mandates") or [])))
    mandates += [mandate for mandate in admitted if mandate not in mandates]
    if not mandates:
        mandates = ["harness-quality"]
    shared = [doctrine_artifact, decisions_artifact, groups_artifact] + commit_artifacts + extras
    views = {}
    for mandate in mandates:
        listed, seen = [], set()
        artifacts_for_view = shared + [subject_artifacts[path] for path in admitted.get(mandate, [])
                                       if path in subject_artifacts]
        if source_required(mandate):
            artifacts_for_view += [source_artifacts[path] for path in admitted.get(mandate, [])
                                   if path in source_artifacts]
        for artifact in artifacts_for_view:
            if artifact["artifact"] in seen:
                continue
            seen.add(artifact["artifact"])
            listed.append(artifact)
        views[mandate] = {"artifacts": listed, "mustRead": [a["artifact"] for a in listed],
                          "bytes": sum(a["bytes"] for a in listed),
                          "admittedFiles": len(admitted.get(mandate, []))}
    for mandate, view in views.items():
        named = set()
        for artifact in view["artifacts"]:
            if artifact.get("file"):
                named.add(artifact["file"])
            named.update(artifact.get("files") or [])
        for path in admitted.get(mandate, []):
            if path not in named:
                raise ledger.ToolError(
                    f"admitted pair {path} / {mandate} has no artifact in its view; refusing a packet "
                    "whose dispatch view cannot cover it")
    seat_mandates = [list(dict.fromkeys(seat.get("mandates") or [])) for seat in roster]
    if seat_mandates:
        must_read = list(dict.fromkeys(
            path for carried in seat_mandates for mandate in carried
            for path in (views.get(mandate) or {}).get("mustRead", [])))
    else:
        must_read = list(dict.fromkeys(path for view in views.values() for path in view["mustRead"]))
    sizes = {"artifacts": {a["artifact"]: a["bytes"] for a in artifacts},
             "views": {mandate: {"bytes": view["bytes"], "artifacts": len(view["mustRead"]),
                                 "admittedFiles": view["admittedFiles"]}
                       for mandate, view in views.items()}}
    print("session_audit inputs: " + "; ".join(
        f"{mandate} view {view['bytes']:,}B / {len(view['mustRead'])} artifacts"
        for mandate, view in views.items()), file=sys.stderr)
    result = {"head": head, "base": base, "files": files, "commits": commits,
              "premiseCommits": ids, "blobs": blob_path, "doctrineCommits": doctrine_artifact["artifact"],
              "restateGroups": groups_artifact["artifact"], "restateScanExit": scan_exit,
              "mustRead": must_read, "artifacts": artifacts, "views": views, "sizes": sizes,
              "uncoveredText": text, "uncoveredPremises": premises,
              "needsAudit": bool(text or decisions or record or plan_text)}
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".")
    parser.add_argument("--base-ref")
    parser.add_argument("--session-base", required=True)
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--roster", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--widen", action="store_true")
    parser.add_argument("--committed-only", nargs="*", default=[],
                        help="peer-dirty widened paths: freeze HEAD, never their uncommitted hunks")
    parser.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    parser.add_argument("--task", help="freeze this task record and its plan, including earlier sessions' decisions")
    parser.add_argument("--plan", help="freeze this plan instead of the task's first recorded plan")
    try:
        result = freeze(parser.parse_args())
        print(json.dumps(result))
        return 0
    except (ledger.ToolError, OSError, ValueError) as exc:
        print(f"session_audit inputs: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
