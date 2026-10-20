"""Stage one commit in a private git index, then bring the shared index and the working lock back in line.

Several sessions share one checkout and one `.git/index`. A session that stages there can commit a
peer's staged files, and a peer's next plain `git commit` publishes whatever this one left behind.
This tool performs the safe cycle instead:

  stage   build `.git/index-<name>` from HEAD (read once, recorded as the base), force-add the named paths into it, stage the lock
          blob (HEAD's `baseline.lock.json` plus the rows for this commit's new `.claude/` paths,
          minus the rows of its deleted ones), and print the `git commit` line to run next. A path an
          earlier commit deleted, named alone, stages only the removal of its leftover row.
  resync  after that commit lands, reset the committed paths in the shared index and update the
          working `baseline.lock.json` so the committed rows equal HEAD's while every other row
          stays as it was.

    python3 .claude/tools/index_commit.py stage --name <name> [--blob <path>=<file> ...] [--msg <file>] -- <paths...>
    python3 .claude/tools/index_commit.py resync --name <name>

`stage` never runs `git commit`: the commit guard reads the Bash command text, so running it here
would bypass every guard. Run the printed line in a separate call.

A path given as `--blob <path>=<file>` is staged with `<file>`'s content instead of the working
copy, for a file whose working copy holds a peer's edits beside yours.

  --entry <path>=<mode>,<object-id> copies a complete staged entry; mode 0 removes it.
  --blob keeps HEAD's mode (100644 for a new path), for deliberate bytes-only overlays.

`resync` reads the path list `stage` saved beside the private index (`.git/index-<name>.paths.json`)
and refuses if HEAD does not hold the staged tree for those paths, so a commit that never landed
cannot pass for one that did. A committed path whose shared-index entry differs from both the old
and the new HEAD blob is a peer's staged change: it is reported and left alone. It prints
`RESYNC OK` when every committed path is clean, otherwise it names each mismatch and exits non-zero.
The stage record also names the base HEAD the index was read from, at `<index>.paths.json`;
`hooks/git_guardrails.py` refuses to commit that index over any other HEAD. Should a commit land over
a moved base anyway, the private index still held the old blobs of the paths that intervening commit
changed, so the commit reverts them: `resync` names each and refuses.
The private index and its sidecar are deleted on success only.

`INDEX_COMMIT_CLASSIFY_CMD` overrides the classifier command (default
`python3 .claude/tools/baseline_sync.py classify`); the tool appends `--status local` and the new
paths to it, so each new `.claude/` path lands as a project-local row.

Errors, each a printed `error: ...` and a non-zero exit: not inside a git repository; a `--name`
that is not a plain fragment; a path outside the repo, or unknown to the working tree, HEAD and
HEAD's lock; `baseline.lock.json` itself named as a path; a `--blob` argument without `=` or with an unreadable source; an unreadable
`baseline.lock.json` or one absent from HEAD; a classifier failure; and on `resync`, a missing
sidecar or a commit that did not land. A refused `stage` discards its private index and leaves the
shared index untouched.
"""
import argparse
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _d in (HERE.parent / "hooks", HERE):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
import _hook_state  # noqa: E402

INDEX_LOCK_TIMEOUT = 5.0
LOCK_RELPATH = ".claude/baseline.lock.json"
CLASSIFY_ENV = "INDEX_COMMIT_CLASSIFY_CMD"
DEFAULT_CLASSIFY_CMD = "python3 .claude/tools/baseline_sync.py classify"
COMMIT_TEMPLATE = "GIT_INDEX_FILE=%s git commit -F %s"
BASE_NOTE = "base HEAD %s; the commit guard refuses this index once HEAD moves, so rerun stage then"


class IndexCommitError(Exception):
    pass


def _git(root, args, index=None, check=True, input_bytes=None):
    env = dict(os.environ)
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    proc = subprocess.run(["git"] + list(args), cwd=str(root), env=env, input=input_bytes,
                          capture_output=True)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise IndexCommitError("git %s failed: %s" % (" ".join(args), (err or out).strip()))
    return proc.returncode, out, err


def _repo():
    rc, out, _ = _git(Path.cwd(), ["rev-parse", "--show-toplevel"], check=False)
    if rc != 0:
        raise IndexCommitError("not inside a git repository")
    root = Path(out.strip())
    _, git_dir, _ = _git(root, ["rev-parse", "--absolute-git-dir"])
    return root, Path(git_dir.strip())


def _relpath(root, raw):
    text = raw.replace("\\", "/")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise IndexCommitError("path is outside the repo: %s" % raw)
    norm = posixpath.normpath(text)
    if norm == "." or norm == ".." or norm.startswith("../"):
        raise IndexCommitError("path is outside the repo: %s" % raw)
    resolved = (root / norm).parent.resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise IndexCommitError("path is outside the repo: %s" % raw)
    return norm


def _tree_blobs(root, treeish, paths):
    return {path: entry[1] for path, entry in _tree_entries(root, treeish, paths).items()}


def _tree_entries(root, treeish, paths):
    """{relpath: (mode, object-id)}; modes are part of a Git entry's identity."""
    if not paths:
        return {}
    _, out, _ = _git(root, ['ls-tree', '-z', treeish, '--'] + list(paths))
    found = {}
    for record in out.split('\0'):
        meta, sep, name = record.partition('\t')
        parts = meta.split()
        if sep and len(parts) == 3:
            found[name] = (parts[0], parts[2])
    return found


def _index_entries(root, paths, index=None):
    if not paths:
        return {}
    _, out, _ = _git(root, ['ls-files', '-s', '-z', '--'] + list(paths), index=index)
    found = {}
    for record in out.split('\0'):
        meta, sep, name = record.partition('\t')
        parts = meta.split()
        if sep and len(parts) == 3:
            if parts[2] != '0':
                raise IndexCommitError('unmerged entry: ' + name)
            found[name] = (parts[0], parts[1])
    return found


def _record_matches(entry, record, side):
    if side + '_mode' not in record:
        return (entry[1] if entry else None) == record.get(side)
    return entry == ((record[side + '_mode'], record[side]) if record.get(side) else None)


def _read_lock(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IndexCommitError("could not read %s (%s): %s" % (label, path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise IndexCommitError("could not parse %s (%s): %s" % (label, path, exc)) from exc


def _head_lock(root, ref="HEAD"):
    rc, out, _ = _git(root, ["show", "%s:%s" % (ref, LOCK_RELPATH)], check=False)
    if rc != 0:
        raise IndexCommitError("HEAD holds no %s; commit one before using this tool" % LOCK_RELPATH)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise IndexCommitError("could not parse HEAD's %s: %s" % (LOCK_RELPATH, exc)) from exc


def _blob_specs(root, specs):
    sources = {}
    for spec in specs:
        rel, sep, src = spec.partition("=")
        if not sep or not rel or not src:
            raise IndexCommitError("--blob requires <path>=<file>: %s" % spec)
        rel = _relpath(root, rel)
        source = Path(src)
        if not source.is_file():
            raise IndexCommitError("--blob source is not a readable file: %s" % src)
        sources[rel] = source
    return sources


def _entry_specs(root, specs):
    entries = {}
    for spec in specs:
        rel, sep, value = spec.partition('=')
        mode, comma, sha = value.partition(',')
        if not sep or not comma or mode not in ('0', '100644', '100755', '120000', '160000'):
            raise IndexCommitError('--entry requires PATH=MODE,OBJECT-ID: ' + spec)
        rel = _relpath(root, rel)
        if mode == '0':
            if sha:
                raise IndexCommitError('a removed --entry must have no object id: ' + spec)
            entries[rel] = None
        else:
            if not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', sha):
                raise IndexCommitError('--entry requires a full object id: ' + spec)
            object_type = _git(root, ['cat-file', '-t', sha])[1].strip()
            if object_type != ('commit' if mode == '160000' else 'blob'):
                raise IndexCommitError('--entry object type does not match its mode: ' + spec)
            oid = _git(root, ['rev-parse', '--verify', sha])[1].strip()
            entries[rel] = (mode, oid)
    return entries


def _classify(root, index, paths):
    command = shlex.split(os.environ.get(CLASSIFY_ENV) or DEFAULT_CLASSIFY_CMD)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(index)
    proc = subprocess.run(command + ["--status", "local"] + list(paths), cwd=str(root), env=env,
                          capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or proc.stdout.decode("utf-8", "replace").strip()
        raise IndexCommitError("classify failed for %s: %s" % (", ".join(paths), detail))


def _lock_row(work, path):
    return (work.get("files") or {}).get(path)


def cmd_stage(args):
    root, git_dir = _repo()
    name = args.name
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise IndexCommitError("--name must be a plain fragment (no leading dot, no separator): %r" % name)
    index = git_dir / ("index-%s" % name)
    sidecar = git_dir / ("index-%s.paths.json" % name)

    paths = []
    for raw in args.paths:
        rel = _relpath(root, raw)
        if rel not in paths:
            paths.append(rel)
    sources = _blob_specs(root, args.blob)
    entries = _entry_specs(root, args.entry)
    if set(sources) & set(entries):
        raise IndexCommitError('a path cannot have both --blob and --entry sources')
    for rel in dict.fromkeys([*sources, *entries]):
        if rel not in paths:
            paths.append(rel)
    if not paths:
        raise IndexCommitError("stage needs at least one path after `--`")

    if LOCK_RELPATH in paths:
        raise IndexCommitError("%s is composed here from HEAD's lock plus this commit's rows; name the new or "
                               "deleted path whose row changes instead" % LOCK_RELPATH)
    base_head = _git(root, ["rev-parse", "HEAD"])[1].strip()
    head_lock = _head_lock(root, base_head)
    head_rows = head_lock.get("files") or {}
    for rel in paths:
        if (rel not in sources and rel not in entries and not (root / rel).is_file() and not _tree_blobs(root, base_head, [rel])
                and rel not in head_rows):
            raise IndexCommitError("unknown path: %s (not a file on disk, in HEAD or in HEAD's lock)" % rel)

    lock_path = root / LOCK_RELPATH
    work_lock = _read_lock(lock_path, "the working lock")
    try:
        return _stage_into(root, index, sidecar, name, paths, sources, work_lock, head_lock, base_head,
                           args.msg, entries)
    except Exception:
        for stale in (index, sidecar):
            try:
                stale.unlink()
            except OSError:
                pass
        raise


def _stage_into(root, index, sidecar, name, paths, sources, work_lock, head_lock, base_head, msg, entries=None):
    entries = entries or {}
    _git(root, ["read-tree", base_head], index=index)
    for rel in paths:
        if rel in entries:
            entry = entries[rel]
            if entry is None:
                _git(root, ['update-index', '--force-remove', '--', rel], index=index)
            else:
                mode, sha = entry
                _git(root, ['update-index', '--add', '--cacheinfo', '%s,%s,%s' % (mode, sha, rel)], index=index)
        elif rel in sources:
            sha = _git(root, ["hash-object", "-w", "--", str(sources[rel])])[1].strip()
            mode = (_git(root, ["ls-tree", base_head, "--", rel])[1].split() or ["100644"])[0]
            _git(root, ["update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, sha, rel)], index=index)
        elif (root / rel).is_file():
            _git(root, ["add", "-f", "--", rel], index=index)
        else:
            _git(root, ["rm", "-q", "--cached", "--ignore-unmatch", "--", rel], index=index)

    before = _tree_entries(root, base_head, paths)
    after = _index_entries(root, paths, index=index)
    records = {rel: {'before': before.get(rel, (None, None))[1],
                     'before_mode': before.get(rel, (None, None))[0],
                     'after': after.get(rel, (None, None))[1],
                     'after_mode': after.get(rel, (None, None))[0]} for rel in paths}

    existing_rows = set((work_lock.get("files") or {}))
    new_rows = [rel for rel in paths
                if rel.startswith(".claude/") and rel in after and rel not in existing_rows]
    if new_rows:
        _classify(root, index, new_rows)
        work_lock = _read_lock(root / LOCK_RELPATH, "the working lock")

    blob = json.loads(json.dumps(head_lock))
    files = blob.setdefault("files", {})
    head_rows = set(head_lock.get("files") or {})
    added, removed = [], []
    for rel in paths:
        if rel not in after:
            if files.pop(rel, None) is not None or rel in head_rows:
                removed.append(rel)
        elif rel.startswith(".claude/") and rel not in head_rows:
            row = _lock_row(work_lock, rel)
            if row is None:
                raise IndexCommitError("classify wrote no lock row for %s" % rel)
            files[rel] = row
            added.append(rel)

    payload = (json.dumps(blob, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    lock_sha = _git(root, ["hash-object", "-w", "--stdin"], input_bytes=payload)[1].strip()
    _git(root, ["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (lock_sha, LOCK_RELPATH)], index=index)

    lock_before = _tree_entries(root, base_head, [LOCK_RELPATH]).get(LOCK_RELPATH, (None, None))
    lock_record = {'before': lock_before[1], 'before_mode': lock_before[0],
                   'after': lock_sha, 'after_mode': '100644'}
    sidecar_payload = {"name": name, "base_head": base_head, "index": index.name, "paths": records,
                       "lock": lock_record}
    if not _hook_state.write_json_atomic(str(sidecar), sidecar_payload, raising=True, pretty=True):
        raise IndexCommitError("could not write the stage record %s" % sidecar)

    index_rel = Path(os.path.relpath(index, root)).as_posix()
    print("staged %d path(s) in %s" % (len(paths), index_rel))
    for rel in added:
        print("  + lock row %s" % rel)
    for rel in removed:
        print("  - lock row %s" % rel)
    stat = _git(root, ["diff", "--cached", "--stat"], index=index)[1]
    if stat.strip():
        print(stat.rstrip())
    print(BASE_NOTE % base_head)
    print(COMMIT_TEMPLATE % (index_rel, msg or "<msgfile>"))
    return 0


def _reverted_by_head(root, base_head, staged_paths):
    """Paths a commit landed after `base_head` changed and HEAD sets back to their `base_head` blob.

    The private index is HEAD at stage time; when another commit lands before the private-index commit,
    that commit's tree still carries the old blobs for every path it did not stage."""
    if base_head and _git(root, ['rev-parse', 'HEAD'])[1].strip() == base_head:
        return []
    rc, parent, _ = _git(root, ["rev-parse", "HEAD^"], check=False)
    if not base_head or rc != 0 or parent.strip() == base_head:
        return []
    _, changed, _ = _git(root, ["diff", "--name-only", "-z", base_head, "HEAD^"])
    candidates = [p for p in changed.split("\0") if p and p not in staged_paths]
    if not candidates:
        return []
    at_base = _tree_entries(root, base_head, candidates)
    at_parent = _tree_entries(root, 'HEAD^', candidates)
    at_head = _tree_entries(root, 'HEAD', candidates)
    reverted = sorted(p for p in candidates if at_head.get(p) == at_base.get(p) and at_parent.get(p) != at_base.get(p))
    if LOCK_RELPATH in candidates and LOCK_RELPATH not in reverted:
        rows = [_lock_rows_at(root, ref) for ref in (base_head, "HEAD^", "HEAD")]
        reverted += ["%s row %s" % (LOCK_RELPATH, key) for key in sorted(set(rows[0]) | set(rows[1]))
                     if key not in staged_paths and rows[0].get(key) != rows[1].get(key)
                     and rows[2].get(key) == rows[0].get(key)]
    return reverted


def _lock_rows_at(root, ref):
    rc, out, _ = _git(root, ["show", "%s:%s" % (ref, LOCK_RELPATH)], check=False)
    try:
        return (json.loads(out).get("files") or {}) if rc == 0 else {}
    except (json.JSONDecodeError, AttributeError):
        return {}


def _reset_shared_index(root, git_dir, entries):
    shared_index = git_dir / "index"
    lock = git_dir / "index.lock"
    deadline = time.monotonic() + INDEX_LOCK_TIMEOUT
    fd, private, owned = None, None, False
    try:
        try:
            while fd is None:
                try:
                    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                    owned = True
                except FileExistsError:
                    if time.monotonic() >= deadline:
                        raise IndexCommitError("timed out waiting for %s" % lock)
                    time.sleep(0.01)
            temp_fd, temp_path = tempfile.mkstemp(prefix="index-resync-", dir=git_dir)
            private = Path(temp_path)
            os.close(temp_fd)
            shutil.copyfile(shared_index, private)
            shared = _index_entries(root, list(entries), index=private)
            peer_left, reset = [], []
            for rel in entries:
                entry = shared.get(rel)
                if entry is not None and not any(_record_matches(entry, entries[rel], side) for side in ('before', 'after')):
                    peer_left.append(rel)
                else:
                    reset.append(rel)
            if reset:
                _git(root, ["reset", "-q", "--"] + reset, index=private)
            with os.fdopen(fd, "wb") as fh:
                fd = None
                fh.write(private.read_bytes())
            os.replace(lock, shared_index)
            owned = False
            return peer_left
        finally:
            if fd is not None:
                os.close(fd)
            for stale in ([lock] if owned else []) + ([private, Path(str(private) + ".lock")] if private else []):
                stale.unlink(missing_ok=True)
    except OSError as exc:
        raise IndexCommitError("could not resync shared index %s: %s" % (shared_index, exc)) from exc


def cmd_resync(args):
    root, git_dir = _repo()
    index = git_dir / ("index-%s" % args.name)
    sidecar = git_dir / ("index-%s.paths.json" % args.name)
    if not sidecar.exists():
        raise IndexCommitError("no staged cycle recorded for name %r (%s); run stage first" % (args.name, sidecar))
    record = _read_lock(sidecar, "the stage record")
    records = record.get("paths") or {}
    if not records:
        raise IndexCommitError("the stage record %s lists no paths" % sidecar)
    paths = sorted(records)

    head_now = _tree_entries(root, 'HEAD', paths)
    unlanded = [rel for rel in paths if not _record_matches(head_now.get(rel), records[rel], 'after')]
    if unlanded:
        raise IndexCommitError("the commit did not land for: %s (HEAD does not hold the staged tree; "
                               "commit the private index again, or rebuild it)" % ", ".join(unlanded))

    reverted = _reverted_by_head(root, record.get("base_head"), paths)
    if reverted:
        raise IndexCommitError(
            "HEAD moved between stage and commit, and the private-index commit reverts %s (changed after "
            "base %s); restore them with `git checkout HEAD^ -- <paths>` and commit, then rerun resync"
            % (", ".join(reverted), (record.get("base_head") or "?")[:9]))

    # The lock blob changes with every new or deleted .claude/ path; left in the shared index at its
    # old HEAD blob it reads as a staged revert of those rows.
    entries = dict(records)
    if record.get("lock"):
        entries[LOCK_RELPATH] = record["lock"]
    peer_left = _reset_shared_index(root, git_dir, entries)

    head_lock = _head_lock(root)
    head_rows = head_lock.get("files") or {}

    def updater(work):
        files = work.setdefault("files", {})
        for rel in paths:
            if rel in head_rows:
                files[rel] = head_rows[rel]
            else:
                files.pop(rel, None)
        return True

    written, _ = _hook_state.update_json_locked(str(root / LOCK_RELPATH), updater, raising=True, pretty=True)
    if not written:
        raise IndexCommitError("could not update the working %s" % LOCK_RELPATH)

    work_lock = _read_lock(root / LOCK_RELPATH, "the working lock")
    work_rows = work_lock.get("files") or {}
    _, status, _ = _git(root, ["status", "--short", "--"] + list(entries))
    staged = [line for line in status.splitlines() if line[:1] not in (" ", "?", "")
              and line[3:].strip() not in peer_left]
    lock_bad = [rel for rel in paths if work_rows.get(rel) != head_rows.get(rel)]

    for rel in peer_left:
        print("left (peer-staged in the shared index): %s" % rel)
    mismatches = []
    if staged:
        mismatches.append("still staged in the shared index: %s" % ", ".join(
            line[3:].strip() or line for line in staged))
    if lock_bad:
        mismatches.append("working lock row differs from HEAD: %s" % ", ".join(lock_bad))
    if mismatches:
        for line in mismatches:
            print("MISMATCH %s" % line)
        return 1

    for stale in (index, sidecar):
        try:
            stale.unlink()
        except OSError as exc:
            raise IndexCommitError("could not remove %s: %s" % (stale, exc)) from exc
    print("RESYNC OK (%d path(s), %d left peer-staged)" % (len(paths), len(peer_left)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="index_commit", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="subcommand", required=True)

    stage = sub.add_parser("stage", help="build the private index and print the commit line")
    stage.add_argument("--name", required=True)
    stage.add_argument('--entry', action='append', default=[], metavar='PATH=MODE,OBJECT-ID')
    stage.add_argument("--blob", action="append", default=[], metavar="PATH=FILE")
    stage.add_argument("--msg", default=None, metavar="FILE")
    stage.add_argument("paths", nargs="*")
    stage.set_defaults(run=cmd_stage)

    resync = sub.add_parser("resync", help="reset the shared index and the working lock after the commit")
    resync.add_argument("--name", required=True)
    resync.set_defaults(run=cmd_resync)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except IndexCommitError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
