#!/usr/bin/env python3
"""The escape log: one row per defect found after it landed.

A landed defect is a defect in a commit made before the current task's work began; for a review
stage, in an ancestor of the subject's base. Three writers feed the log. `/self_evaluate` and the
debugging checklist append under their own writer name, and `review_ledger.py record --escaped`
appends under `review:<stage>`, so no review command gains a step.

    escape_log.py add --sha <sha> --writer <self_evaluate|debugging|review:<stage>> [--repo <path>] \
        [--paths <path>...] [--should-have-caught <mandate key|none>] [--base-ref <ref>] \
        [--author-model <id|unknown>] [--tier <tier|inline>] [--evidence <path>] [--found-by <call>]
    escape_log.py list [--repo <path>] [--path <path>] [--model <id>] [--tier <tier>] [--since <ISO>] [--json]

`--repo` is the checkout a sha and the log resolve against (default `CLAUDE_PROJECT_DIR`, else the cwd). `add`
computes the row's evidence first, then appends it to that checkout's `.claude/escape_log.jsonl` under
`_hook_state`'s file lock, which spans only the id and the append, so concurrent writers compose without
waiting on each other's evidence. `--paths` are stored repo-relative (an absolute, `./` or backslash form is
normalized by `review_ledger.normalize_files`), the form coverage clause 5 compares. The log path and the
review ledger path are `review_ledger.py`'s `escape_log_path` and `ledger_path`, the ones its coverage check
reads. The log is tracked, so a row reaches another checkout's log only through git; `add` also appends the
same row to the shared runtime overlay beside the ledger (`review_ledger.escape_overlay_path`), so escape
revocation (coverage clause 5) reaches every worktree that shares the ledger before the row is merged there.
The tracked row is the overlay row's durable projection. Each
row's `id` is the commit's short id plus a counter over the rows already holding that prefix: the prefix width
is cosmetic, and the counter is what keeps two rows over one commit distinct. It fills three fields from
evidence and never from the caller:

  `triage`     the sha's `Triage:` trailer, else the task record that names the sha, else
               `unrecorded` (`triage.parse_trailer` reads the trailer, so a malformed one records
               `unrecorded` rather than a guess).
  `checksRan`  the ids of the ledger rows that covered a `--paths` path at the sha:
               `review_ledger.cover_at(...).covering_ids`, every catalog mandate and subject kind with
               the path's content taken as its blob at the sha, against `--base-ref` (default
               `origin/main`). A path absent at the sha, or an escape log that cannot be parsed, gives
               no row.
  `domain`     `triage.domain_keys` over the paths, the function a yield row keys on too.

`--paths` defaults to the files the sha changed, `--author-model` to `unknown` and `--tier` to
`inline`, so the documented `/self_evaluate` and debugging invocations run as written.

Refusals (exit 2, nothing written): an unknown mandate key, a `--sha` that is not a commit, an
empty path list, a `--writer` outside the three forms, and a mandate key when the project has no
`review_mandates` catalog to declare it. A write that fails, or a lock that cannot be taken, exits 1 naming the path.
The tracked row is written first, so an overlay failure leaves it standing and says so. Both files are
append-only: rows are never dropped or rotated, so each grows one row per escaped defect.

`HARNESS_ESCAPE_LOG` redirects the log and `HARNESS_REVIEW_LEDGER` the ledger and the overlay beside it,
through `review_ledger.py`.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.normpath(os.path.join(HERE, "..", "hooks")), HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import _git_commit  # noqa: E402
import _hook_state  # noqa: E402
import review_ledger  # noqa: E402
import triage  # noqa: E402

WRITERS = ("self_evaluate", "debugging")
NO_MANDATE = "none"
UNRECORDED = "unrecorded"
VERSION = 1
ABSENT = "absent"


_REPO = None


def project_root():
    """The repo a sha and the log resolve against: `--repo`, else `CLAUDE_PROJECT_DIR`, else the cwd."""
    return _REPO or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def claude_dir():
    return os.path.join(project_root(), ".claude")


def log_path():
    """The checkout's escape log: `review_ledger.escape_log_path`."""
    return str(review_ledger.escape_log_path(Path(project_root())))


def mandate_keys(claude=None):
    """The mandate keys `review_mandates` declares, read as `review_ledger.load_catalog` reads them; an absent
    or wrong-typed catalog is an empty frozenset, so a key with no catalog behind it fails rather than passes."""
    return frozenset(review_ledger.load_catalog(Path(claude or claude_dir())))


def rows(path=None):
    """Every readable row of the log, oldest first; an absent file is no rows."""
    out = []
    target = path or log_path()
    try:
        with io.open(target, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        out.append(record)
    except OSError:
        return []
    return out


def _checks_ran(sha, paths, base_ref):
    """The sorted ids of the ledger rows that cover a path at its blob in `sha` (`review_ledger.cover_at`)."""
    root = Path(project_root())
    blobs = {}
    for path in paths:
        out = _git_commit.run_git(["rev-parse", "-q", "--verify", "%s:%s" % (sha, path)], str(root))
        blobs[path] = out.strip() if out else None
    try:
        return review_ledger.cover_at(root, blobs, base_ref).covering_ids(paths)
    except review_ledger.ToolError as exc:
        print("escape_log: checksRan left empty: %s" % exc, file=sys.stderr)
        return []


def domain_keys(paths):
    return triage.domain_keys(paths, claude_dir())


def _trailer_text(fields):
    """One canonical `Triage:` trailer line. `triage.parse_trailer` reads it back unchanged."""
    parts = []
    for key in triage.FIELDS | triage.OPTIONAL_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "oracle":
            value = triage._format_oracle(value)
        parts.append("%s=%s" % (key, value))
    return " ".join(parts)


def _task_naming(sha):
    """The newest task record whose JSON text names `sha`, else None. No record field holds a
    commit sha today, so the match is textual; a record that never names one simply does not match."""
    import task_record
    directory = task_record.record_dir()
    try:
        names = [os.path.join(directory, n) for n in os.listdir(directory) if n.endswith(".json")]
    except OSError:
        return None
    for path in sorted(names, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                       reverse=True):
        try:
            with io.open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if sha not in text:
            continue
        try:
            loaded = json.loads(text)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _triage_text(sha):
    message = _git_commit.run_git(["log", "-1", "--format=%B", sha], project_root()) or ""
    fields, _error = triage.parse_trailer(message)
    if fields is not None:
        return _trailer_text(fields)
    record = _task_naming(sha)
    if record is not None:
        fields = (record.get("triage") or {}).get("fields")
        if isinstance(fields, dict) and fields:
            return _trailer_text(fields)
    return UNRECORDED


def _canonical_sha(sha):
    """The full commit id `sha` names (40 hex characters, git's fixed SHA-1 width), or None when
    it names no commit."""
    out = _git_commit.run_git(["rev-parse", "-q", "--verify", sha + "^{commit}"], project_root())
    return out.strip() if out else None


def _repo_paths(paths):
    """`paths` as the repo-relative POSIX form the review ledger and coverage clause 5 compare."""
    top = _git_commit.run_git(["rev-parse", "--show-toplevel"], project_root())
    return review_ledger.normalize_files(Path(top.strip() if top else project_root()), paths)


def _changed_paths(sha):
    try:
        return [change.path for change in triage.changes_from_commit(project_root(), sha)]
    except OSError:
        return []


def _writer_ok(writer):
    if writer in WRITERS:
        return True
    return writer.startswith("review:") and len(writer) > len("review:")


def _next_id(existing, sha):
    base = sha[:8]
    taken = {row.get("id") for row in existing}
    seq = 1
    while "%s:%d" % (base, seq) in taken:
        seq += 1
    return "%s:%d" % (base, seq)


def cmd_add(args):
    sha = _canonical_sha(args.sha)
    if sha is None:
        print("escape_log: --sha %s names no commit in %s" % (args.sha, project_root()), file=sys.stderr)
        return 2
    if not _writer_ok(args.writer):
        print("escape_log: --writer must be one of %s, or review:<stage>"
              % ", ".join(WRITERS), file=sys.stderr)
        return 2
    key = args.should_have_caught
    if key != NO_MANDATE and key not in mandate_keys():
        print("escape_log: --should-have-caught %s is not a review_mandates key (see "
              "review_ledger.py keys), and is not %s" % (key, NO_MANDATE), file=sys.stderr)
        return 2
    paths = _repo_paths(args.paths) if args.paths else _changed_paths(sha)
    if not paths:
        print("escape_log: --paths is empty and %s changed no file; name at least one path"
              % sha[:8], file=sys.stderr)
        return 2

    evidence = {"triage": _triage_text(sha), "checksRan": _checks_ran(sha, paths, args.base_ref),
                "domain": domain_keys(paths), "evidence": args.evidence}
    target = log_path()
    handle = _hook_state._acquire_json_lock(target)
    if handle is None:
        print("escape_log: cannot lock %s; nothing written" % target, file=sys.stderr)
        return 1
    try:
        record = {"v": VERSION, "id": _next_id(rows(target), sha),
                  "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "sha": sha, "paths": paths, "shouldHaveCaught": key, "writer": args.writer,
                  "authorModel": args.author_model, "tier": args.tier}
        if args.found_by:
            record["foundBy"] = args.found_by
        record.update(evidence)
        if not _append(target, record):
            return 1
    finally:
        _hook_state._release_json_lock(handle)
    print(json.dumps(record, ensure_ascii=False))
    overlay = str(review_ledger.escape_overlay_path(Path(project_root())))
    handle = _hook_state._acquire_json_lock(overlay)
    if handle is None:
        print("escape_log: cannot lock %s; the tracked row stands, and other worktrees see it only once it "
              "is merged there" % overlay, file=sys.stderr)
        return 1
    try:
        return 0 if _append(overlay, record) else 1
    finally:
        _hook_state._release_json_lock(handle)


def _append(target, record):
    """Append `record` to `target` as one JSON line; False, after printing why, when the write fails."""
    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with io.open(target, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("escape_log: cannot append to %s: %s" % (target, exc), file=sys.stderr)
        return False
    return True


def cmd_list(args):
    kept = []
    for row in rows():
        if args.path and args.path not in (row.get("paths") or []):
            continue
        if args.model and row.get("authorModel") != args.model:
            continue
        if args.tier and row.get("tier") != args.tier:
            continue
        if args.since and str(row.get("ts") or "") < args.since:
            continue
        kept.append(row)
    for row in kept:
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
        else:
            print("%s %s %s %s %s" % (row.get("ts"), str(row.get("sha"))[:8], row.get("writer"),
                                      row.get("shouldHaveCaught"), ",".join(row.get("paths") or [])))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="escape_log.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    repo = argparse.ArgumentParser(add_help=False)
    repo.add_argument("--repo", default=None, help="the checkout (default: CLAUDE_PROJECT_DIR, else the cwd)")

    add = sub.add_parser("add", parents=[repo], help="append one row for a defect found in a landed commit")
    add.add_argument("--sha", required=True, help="the landed commit the defect is in")
    add.add_argument("--writer", required=True, help="self_evaluate, debugging, or review:<stage>")
    add.add_argument("--paths", nargs="*", default=[], help="default: the files the sha changed")
    add.add_argument("--should-have-caught", required=True,
                     help="the mandate key whose review would have caught it, or none")
    add.add_argument("--base-ref", default="origin/main",
                     help="the base ref coverage reads the merge-base against (default: origin/main)")
    add.add_argument("--author-model", default="unknown", help="default: unknown")
    add.add_argument("--tier", default="inline", help="default: inline")
    add.add_argument("--evidence", default=None, help="a path holding the defect's evidence")
    add.add_argument("--found-by", default=None,
                     help="the `call` of the review record that found the defect: that call's own ledger rows stay "
                          "covering at the escape's second")
    add.set_defaults(func=cmd_add)

    listing = sub.add_parser("list", parents=[repo], help="print the rows, one per line, filtered")
    listing.add_argument("--path", default=None)
    listing.add_argument("--model", default=None)
    listing.add_argument("--tier", default=None)
    listing.add_argument("--since", default=None, help="ISO-8601 date or timestamp, inclusive")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)
    return parser


def main(argv=None):
    global _REPO
    args = build_parser().parse_args(argv)
    _REPO = args.repo
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
