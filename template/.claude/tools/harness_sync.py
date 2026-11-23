#!/usr/bin/env python3
"""Share the harness's .claude/ files between the shared repo and each project (T7).

  harness_sync.py pull <ref> --source DIR [--project DIR]   copy the shared files at <ref> into the project
  harness_sync.py status [--project DIR]                    list edits and missing files; check the floor is registered
  harness_sync.py push --source DIR [--project DIR] [--branch NAME] [--dry-run]
                                                            upstream the edited shared files as a pull request

The shared repo keeps the files under template/.claude/; a project keeps the same bytes under .claude/. The
manifest .claude/harness.manifest.json records the pulled commit, each file's sha256 and the floor hook paths, so
pull and status tell an unedited file from a hand edit. Pull checks every write and delete before it makes one:
a file that differs from the manifest is refused and nothing is written. A repo never forks a shared file, so the
fix for an edit is to push it upstream. Pull names every shared file the project's gitignore hides: `git add` skips
it, so it needs `git add -f` or no commit carries it.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_PREFIX = "template/.claude/"
DEST_PREFIX = ".claude/"
MANIFEST = ".claude/harness.manifest.json"
SETTINGS = ".claude/settings.json"
FLOOR = [".claude/hooks/pre_bash.py"]  # the floor entry the shared repo writes into every manifest (T1)


class SyncError(Exception):
    """A refusal or a failed git call; main prints it as one line and exits 1."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args, cwd=None):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, cwd=cwd)
    if done.returncode:
        raise SyncError(f"git {args[0]} failed in {repo}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode(errors="replace").strip()


def local_bytes(project, rel):
    path = Path(project) / rel
    return path.read_bytes() if path.is_file() else None


def read_manifest(project):
    path = Path(project) / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def source_files(source, ref):
    """The commit <ref> names, {project path: bytes} for every blob under the shared prefix at that commit, and
    the set of those paths whose blob mode is 100755.

    Raw blobs, not `git archive`: archive applies eol conversion, and shared files must be byte-identical (T7)."""
    commit = git(source, "rev-parse", "--verify", f"{ref}^{{commit}}")
    listing = subprocess.run(["git", "-C", str(source), "ls-tree", "-r", "-z", commit, "--", SOURCE_PREFIX.rstrip("/")],
                             capture_output=True)
    if listing.returncode:
        raise SyncError(f"git ls-tree failed for {commit}: {listing.stderr.decode(errors='replace').strip()}")
    entries, executable = [], set()
    for item in filter(None, listing.stdout.split(b"\0")):
        meta, path = item.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind == "blob":
            entries.append((path.decode(), oid))
            if mode == "100755":
                executable.add(DEST_PREFIX + path.decode()[len(SOURCE_PREFIX):])
    if not entries:
        raise SyncError(f"no files under {SOURCE_PREFIX} at {commit}")
    blobs = subprocess.run(["git", "-C", str(source), "cat-file", "--batch"],
                           input="".join(oid + "\n" for _, oid in entries).encode(), capture_output=True)
    if blobs.returncode:
        raise SyncError(f"git cat-file failed: {blobs.stderr.decode(errors='replace').strip()}")
    files, pos, out = {}, 0, blobs.stdout
    for path, _oid in entries:
        end = out.index(b"\n", pos)
        size = int(out[pos:end].split()[2])
        files[DEST_PREFIX + path[len(SOURCE_PREFIX):]] = out[end + 1:end + 1 + size]
        pos = end + 1 + size + 1
    return commit, files, executable


def plan_pull(project, incoming, manifest):
    """(writes, deletes, conflicts). A conflict is a local file that differs from what the manifest last pulled."""
    pulled = manifest["files"] if manifest else {}
    writes, deletes, conflicts = [], [], []
    for rel, data in sorted(incoming.items()):
        local = local_bytes(project, rel)
        if local == data:
            continue
        if local is None or pulled.get(rel) == sha256(local):
            writes.append(rel)
        else:
            conflicts.append(rel)
    for rel in sorted(set(pulled) - set(incoming)):
        local = local_bytes(project, rel)
        if local is None:
            continue
        if sha256(local) == pulled[rel]:
            deletes.append(rel)
        else:
            conflicts.append(rel)
    return writes, deletes, conflicts


def pull(ref, source, project):
    commit, incoming, executable = source_files(source, ref)
    manifest = read_manifest(project)
    writes, deletes, conflicts = plan_pull(project, incoming, manifest)
    if conflicts:
        raise SyncError("refused, nothing written; these shared files differ from the manifest: " + ", ".join(conflicts))
    for rel in writes:
        target = Path(project) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(incoming[rel])
        if rel in executable:
            target.chmod(target.stat().st_mode | 0o111)
    for rel in deletes:
        (Path(project) / rel).unlink()
    new = {"ref": commit, "files": {rel: sha256(data) for rel, data in sorted(incoming.items())}, "floor": FLOOR}
    target = Path(project) / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(new, indent=2) + "\n").encode("utf-8"))
    text = f"pulled {commit[:12]}: {len(writes)} written, {len(deletes)} deleted"
    hidden = ignored(project, sorted(incoming))
    if hidden:
        text += (f"\n{len(hidden)} shared files are gitignored, so `git add` skips them and no commit carries them;"
                 f" stage them with `git add -f`: " + " ".join(hidden))
    return text, 0


def ignored(project, paths):
    """The `paths` the project's gitignore hides and git does not track; none when the project is no repo."""
    done = subprocess.run(["git", "-C", str(project), "check-ignore", "--no-index", "--stdin", "-z"],
                          input="\0".join(paths).encode("utf-8"), capture_output=True)
    if done.returncode not in (0, 1):
        return []
    hits = [p for p in done.stdout.decode("utf-8", errors="replace").split("\0") if p]
    if not hits:
        return []
    tracked = subprocess.run(["git", "-C", str(project), "ls-files", "-z", "--", *hits], capture_output=True)
    known = set(tracked.stdout.decode("utf-8", errors="replace").split("\0"))
    return [p for p in hits if p not in known]


def floor_commands(settings):
    """The command strings of hooks registered under PreToolUse for a matcher that covers Bash, separators as `/`."""
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        matcher = entry.get("matcher") or ".*"
        if re.search(matcher, "Bash"):
            for hook in entry.get("hooks", []):
                yield str(hook.get("command", "")).replace("\\", "/")


def status(project):
    manifest = read_manifest(project)
    if manifest is None:
        raise SyncError(f"no {MANIFEST}; run pull first")
    lines, failed = [], False
    for rel, digest in sorted(manifest["files"].items()):
        local = local_bytes(project, rel)
        if local is None:
            lines.append(f"missing {rel}")
            failed = True
        elif sha256(local) != digest:
            lines.append(f"edited  {rel}")
    settings = Path(project) / SETTINGS
    commands = []
    if settings.is_file():
        try:
            commands = list(floor_commands(json.loads(settings.read_text(encoding="utf-8"))))
        except (ValueError, AttributeError, TypeError, re.error) as err:
            lines.append(f"ERROR {SETTINGS} is not a readable hooks file: {err}")
            return "\n".join(lines), 1
    for rel in manifest["floor"]:
        if not any(rel in command for command in commands):
            lines.append(f"ERROR floor hook not registered in {SETTINGS}: {rel}")
            failed = True
    return "\n".join(lines) or "clean", 1 if failed else 0


def push(source, project, branch, dry_run):
    manifest = read_manifest(project)
    if manifest is None:
        raise SyncError(f"no {MANIFEST}; run pull first")
    changed = [rel for rel, digest in sorted(manifest["files"].items())
               if local_bytes(project, rel) is not None and sha256(local_bytes(project, rel)) != digest]
    if not changed:
        return "no shared file is edited; nothing to push", 0
    shared_paths = [SOURCE_PREFIX + rel[len(DEST_PREFIX):] for rel in changed]
    branch = branch or "harness-sync/" + sha256("\n".join(shared_paths).encode())[:10]
    if dry_run:
        return f"would push branch {branch} to {source} with {len(changed)} file(s):\n" + "\n".join(shared_paths), 0
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "pr"
        git(source, "worktree", "add", "-b", branch, str(worktree), manifest["ref"])
        try:
            for rel, shared in zip(changed, shared_paths):
                (worktree / shared).write_bytes(local_bytes(project, rel))
            git(worktree, "add", "--", SOURCE_PREFIX.rstrip("/"))
            git(worktree, "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                f"harness sync: {len(changed)} shared file(s) edited in a project")
            git(worktree, "push", "-u", "origin", branch)
            done = subprocess.run(["gh", "pr", "create", "--head", branch, "--title",
                                   f"harness sync: {len(changed)} shared file(s)",
                                   "--body", "\n".join(shared_paths)], cwd=worktree, capture_output=True, text=True)
            if done.returncode:
                raise SyncError(f"branch {branch} is pushed; gh pr create failed: {done.stderr.strip()}")
            return f"opened {done.stdout.strip()}", 0
        finally:
            git(source, "worktree", "remove", "--force", str(worktree))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harness_sync.py", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    pull_verb = verbs.add_parser("pull")
    pull_verb.add_argument("ref")
    pull_verb.add_argument("--source", required=True, help="the shared repo checkout")
    status_verb = verbs.add_parser("status")
    push_verb = verbs.add_parser("push")
    push_verb.add_argument("--source", required=True, help="the shared repo checkout")
    push_verb.add_argument("--branch")
    push_verb.add_argument("--dry-run", action="store_true")
    for verb in (pull_verb, status_verb, push_verb):
        verb.add_argument("--project", default=".", help="the project checkout (default: the current directory)")
    args = parser.parse_args(argv)
    if args.verb != "status":
        print("harness_sync.py " + " ".join(sys.argv[1:] if argv is None else argv))
    try:
        if args.verb == "pull":
            text, code = pull(args.ref, args.source, args.project)
        elif args.verb == "status":
            text, code = status(args.project)
        else:
            text, code = push(args.source, args.project, args.branch, args.dry_run)
    except SyncError as err:
        print(f"harness_sync: {err}", file=sys.stderr)
        return 1
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
