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
final-state check. A fresh output
folder holds the frozen IDs, blob map, every typed evidence artifact and one view per mandate.
A view lists the exact artifacts that mandate's lens reads, each once: the doctrine commits,
the session's decisions, `restate-groups.txt`, each doctrine commit's diff and each admitted
subject's diff. A subject's whole frozen source is written too and linked from its diff, and
only a mandate whose lens judges source text lists it (`source_required`). Every admitted
(file, mandate) pair is checked to keep an artifact in its own view before the packet is
written; the views, never a `subject-N` filename, are what a caller maps to a seat. An
existing run folder is never overwritten: the freeze writes the first free `_rN` sibling
folder, and the returned paths name it. The caller forwards head, blobs and premiseCommits
unchanged through its review route.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import review_ledger as ledger
import task_record
from _checkout_root import linked_main_root
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


def commit_diff_text(root, commit, limit=24000):
    """One commit's header and diff over its paths, in `git show` calls that each fit a Windows command line."""
    chunks, chunk, size = [], [], 0
    for path in commit["files"]:
        if chunk and size + len(path) + 3 > limit:
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append(path)
        size += len(path) + 3
    chunks.append(chunk)
    return "".join(git(root, "show", "--format=fuller" if index == 0 else "--format=", "--cc", "--no-renames",
                       commit["sha"], "--", *paths) for index, paths in enumerate(chunks))


def restate_groups(root, session_base, files):
    if not files:
        return 2, "restate_scan: no subject files; nothing was scanned\n"
    scan = Path(__file__).resolve().parent / "restate_scan.py"
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "paths.txt"
        listing.write_text("\n".join(files) + "\n", encoding="utf-8", newline="\n")
        result = subprocess.run([sys.executable, str(scan), "--repo", str(root), "pairs", "--changed",
                                 "--base", session_base, "--paths", f"@{listing}"], capture_output=True)
    return result.returncode, result.stdout.decode("utf-8", errors="replace")


def session_decisions(root, session, task=None):
    if not session and not task:
        return []
    rows = []
    directory = Path(task_record.record_dir(root))
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if path.stem != task and (not session or
                (record.get("session_id") or "")[:8].lower() != session[:8].lower()):
            continue
        for decision in task_record.all_decisions(path.stem, directory=directory):
            shown = path.relative_to(root) if path.is_relative_to(root) else path
            rows.append({"path": shown.as_posix(), "task": record.get("task_id"),
                         "decision": decision})
    return rows


def next_free_out(out: Path) -> Path:
    """`out` when its folder is free, else the first free `_rN` sibling: a finished run's inputs stay put."""
    if not out.parent.exists():
        return out
    parent, stem = out.parent.parent, out.parent.name
    n = 2
    while (parent / f"{stem}_r{n}").exists():
        n += 1
    return parent / f"{stem}_r{n}" / out.name


def freeze(*, root, out, roster, session_base, base_ref=None, files=(), widen=False, committed_only=(),
           session=None, task=None, plan=None):
    """Freeze the audit's inputs under `out`'s folder and return the packet dict `main` prints."""
    root = Path(root).resolve()
    out = Path(out)
    out = (root / out).resolve() if not out.is_absolute() else out.resolve()
    out = next_free_out(out)
    out.relative_to(root)
    head = git(root, "rev-parse", "HEAD").strip()
    # An explicit --base-ref names the range to audit, pushed or not; without one the outgoing range is upstream..HEAD.
    upstream = None if base_ref else git(root, "rev-parse", "--verify", "-q", "@{u}", optional=True)
    base_ref = base_ref or "origin/main"
    base = upstream.strip() if upstream else git(root, "rev-parse", base_ref).strip()
    commits = doctrine_commits(root, base, head)
    doctrine_files = sorted({path for commit in commits for path in commit["files"]})
    files_in = ledger.normalize_files(root, list(files or []))
    files = sorted(set(files_in) | set(doctrine_files)) if widen else files_in
    catalog = ledger.load_catalog()
    roster = ledger._load_roster(roster, catalog)
    ctx = ledger.Ctx(root, base_ref, snapshot=head)
    blobs = {}
    committed_only = set(ledger.normalize_files(root, list(committed_only or [])))
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
    text = ledger.uncovered_pairs(cover, files, ["harness-quality"], roster)
    decisions = session_decisions(root, session, task)
    record = None
    if task:
        directory = Path(task_record.record_dir(root))
        record = json.loads((directory / (task + ".json")).read_text(encoding="utf-8"))
        if not plan:
            plan = next((p for p in (record.get("scope") or {}).get("files", [])
                         if Path(p).name == "short_plan.md" or
                         ".claude/plans/" in str(p).replace("\\", "/")), None)
        if not plan:
            raise ledger.ToolError(f"task {task} has no plan; pass --plan")
    plan_text = None
    if plan:
        homes = [root] + [Path(main) for main in [linked_main_root(str(root))] if main]
        source = Path(plan)
        if not source.is_absolute():
            source = next((home / source for home in homes if (home / source).is_file()), root / source)
        source = source.resolve()
        if not any(source.is_relative_to(home) for home in homes):
            raise ledger.ToolError(f"plan {plan} is outside {root} and its main checkout")
        plan_text = source.read_text(encoding="utf-8")
    messages = []
    commit_diffs = []
    for commit in commits:
        messages.append(git(root, "show", "-s", "--format=%H%n%s%n%b", commit["sha"]))
        commit_diffs.append((commit, commit_diff_text(root, commit)))
    subjects = []
    changed = []
    for index, path in enumerate(files):
        committed = git(root, "diff", "--no-renames", f"{session_base}..{head}", "--", path)
        dirty = "" if path in committed_only else git(root, "diff", "HEAD", "--", path)
        deleted = git(root, "cat-file", "-e", f"{session_base}:{path}", optional=True) is not None and \
            git(root, "cat-file", "-e", f"{head}:{path}", optional=True) is None
        if (path not in committed_only and not deleted and not git(root, "ls-files", "--", path).strip()
                and (root / path).is_file()):
            dirty = (root / path).read_text(encoding="utf-8", errors="replace")
        content = None
        if path in blobs:
            content = (git(root, "show", f"{head}:{path}") if path in committed_only
                       else (root / path).read_text(encoding="utf-8", errors="replace"))
        subjects.append((index, path, f"{path}\n{committed}\n{dirty}", content))
        if committed.strip() or dirty.strip() or deleted or path in doctrine_files:
            changed.append(path)
    if files and not changed:
        raise ledger.ToolError(
            f"no change to review: every subject file is identical at {session_base}, {head} and in the working "
            f"tree ({', '.join(files[:5])}{', ...' if len(files) > 5 else ''}); a seat over empty diffs cannot "
            "cover anything. Freeze after the range has landed, or name the range's head as --base-ref's partner")
    for path, blob in blobs.items():
        if path not in committed_only and git(root, "hash-object", "--", path).strip() != blob:
            raise ledger.ToolError(f"{path} changed while freezing inputs; retry with a fresh run path")
    if git(root, "rev-parse", "HEAD").strip() != head:
        raise ledger.ToolError("HEAD changed while freezing inputs; retry with a fresh run path")
    scan_exit, scan_text = restate_groups(root, session_base, [f for f in files if f not in committed_only])
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
    # ledger's uncovered text pairs.
    admitted = {}
    for pair in text:
        admitted.setdefault(pair[1], [])
        if pair[0] not in admitted[pair[1]]:
            admitted[pair[1]].append(pair[0])
    # A view exists only for the harness mandate and mandates the packet admitted files for: a code seat
    # with no admitted pair keeps its own file list (lens_briefs._must_read_for reads any view first).
    mandates = ["harness-quality"] + [mandate for mandate in admitted if mandate != "harness-quality"]
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
    result = {"head": head, "base": base, "sessionBase": session_base, "files": files, "commits": commits,
              "premiseCommits": ids, "blobs": blob_path, "doctrineCommits": doctrine_artifact["artifact"],
              "restateGroups": groups_artifact["artifact"], "restateScanExit": scan_exit,
              "mustRead": must_read, "artifacts": artifacts, "views": views, "sizes": sizes,
              "uncoveredText": text,
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
        a = parser.parse_args()
        result = freeze(root=a.root, out=a.out, roster=a.roster, session_base=a.session_base, base_ref=a.base_ref,
                        files=a.files, widen=a.widen, committed_only=a.committed_only, session=a.session,
                        task=a.task, plan=a.plan)
        print(json.dumps(result))
        return 0
    except (ledger.ToolError, OSError, ValueError) as exc:
        print(f"session_audit inputs: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
