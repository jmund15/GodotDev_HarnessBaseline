#!/usr/bin/env python3
"""
baseline_sync.py — three-way sync engine between a project's .claude/ harness and the
shared harness-baseline repo.

The lock file (.claude/baseline.lock.json) records:
  - baseline_repo / baseline_ref : where the baseline lives
  - substitutions                : placeholder -> project value map written at bootstrap
  - files                       : per-file entries
      status "tracked" : hash-synced (hash = sha256 of the SUBSTITUTED baseline content
                         at last sync; comparing local vs that detects local edits)
      status "watch"   : seed/adapted files — never hash-compared; drift checks only
                         flag that they changed so Claude can judge manually
      status "forked"  : intentionally diverged — excluded from all checks
      status "local"   : project-owned artifact acknowledged as not-for-baseline —
                         excluded from checks AND from the new-file candidates scan

Baseline-side files live at <baseline>/template/<relpath>. Comparison is
substitution-aware: baseline content is materialized through the substitution map
before hashing, so placeholder files compare clean against bootstrapped copies.

Mechanical only — classification of WHICH hunks are universal is judgment and lives
in the /sync_baseline command (Claude), not here.

Usage:
  baseline_sync.py check   [--json] [--baseline-dir DIR]
  baseline_sync.py diff    RELPATH [--baseline-dir DIR]
  baseline_sync.py pull    [RELPATH ...] [--baseline-dir DIR]   # upstream -> local
  baseline_sync.py materialize RELPATH ... [--baseline-dir DIR] # local -> baseline worktree
  baseline_sync.py update-lock [RELPATH ...] [--baseline-dir DIR]
  baseline_sync.py fork    RELPATH ...
  baseline_sync.py track   RELPATH ...
  baseline_sync.py ignore  RELPATH ...                          # mark project-owned
  baseline_sync.py candidates                                   # new .claude/ files unknown to the lock
  baseline_sync.py paths   [--status tracked|watch|forked|local]
  baseline_sync.py init    --baseline-dir DIR --repo URL [--ref REF]
                           [--sub PLACEHOLDER=VALUE ...]
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

LOCK_RELPATH = ".claude/baseline.lock.json"
CACHE_CLONE = ".claude/.cache/baseline-repo"
MANIFEST_NAME = "baseline.manifest.json"


def project_root() -> Path:
    p = Path.cwd()
    while p != p.parent:
        if (p / ".claude").is_dir():
            return p
        p = p.parent
    sys.exit("error: no .claude/ directory found upward from cwd")


def load_lock(root: Path) -> dict:
    lock_path = root / LOCK_RELPATH
    if not lock_path.exists():
        sys.exit(f"error: {LOCK_RELPATH} not found — run 'init' first")
    return json.loads(lock_path.read_text(encoding="utf-8"))


def save_lock(root: Path, lock: dict) -> None:
    (root / LOCK_RELPATH).write_text(
        json.dumps(lock, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def normalize(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(normalize(data)).hexdigest()


def forward_sub(text: str, subs: dict) -> str:
    for placeholder, value in subs.items():
        text = text.replace(placeholder, value)
    return text


def reverse_sub(text: str, subs: dict) -> str:
    # Longest value first so nested values (PROJECT_ROOT contains PROJECT_NAME)
    # don't partially mask each other.
    for placeholder, value in sorted(subs.items(), key=lambda kv: -len(kv[1])):
        if not value:
            continue
        if re.search(r"[\\/:]", value):
            # Path-like value: unambiguous, plain replace. Word boundaries would
            # mis-fire around path separators.
            text = text.replace(value, placeholder)
        else:
            # Word-like value (e.g. a short PROJECT_NAME): bound the match so a
            # common word doesn't corrupt unrelated identifiers — "Test" must not
            # rewrite "TestSuite" → "{{PROJECT_NAME}}Suite" when upstreaming.
            text = re.sub(rf"\b{re.escape(value)}\b", placeholder, text)
    return text


def ensure_baseline(lock: dict, root: Path, baseline_dir: str | None) -> Path:
    if baseline_dir:
        d = Path(baseline_dir).resolve()
        if not (d / "template").is_dir():
            sys.exit(f"error: {d} has no template/ directory — not a baseline checkout")
        return d
    clone = root / CACHE_CLONE
    repo, ref = lock["baseline_repo"], lock.get("baseline_ref", "main")
    if not clone.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", ref, repo,
                        str(clone)], check=True)
    else:
        subprocess.run(["git", "-C", str(clone), "fetch", "origin", ref], check=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", f"origin/{ref}"],
                       check=True)
    return clone


def baseline_commit(baseline: Path) -> str | None:
    r = subprocess.run(["git", "-C", str(baseline), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def upstream_text(baseline: Path, relpath: str, subs: dict) -> str | None:
    p = baseline / "template" / relpath
    if not p.exists():
        return None
    return forward_sub(p.read_text(encoding="utf-8", errors="replace"), subs)


def local_text(root: Path, relpath: str) -> str | None:
    p = root / relpath
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


# Paths that can never be baseline candidates: session state, caches, generated
# artifacts, and the lock itself. Prefixes match directory subtrees; names match
# basename globs anywhere under .claude/.
CANDIDATE_EXCLUDE_PREFIXES = (
    ".claude/.cache/", ".claude/logs/", ".claude/worktrees/",
    ".claude/sessions/", ".claude/plans/", ".claude/scratch/",
)
CANDIDATE_EXCLUDE_NAMES = (
    "baseline.lock.json", "settings.local.json", "tool_resource_classes.txt",
    "self_evaluate_archive*", "worklog-pending*", "worklog-tackle-history*",
    "*.pyc", "*.bak", ".gitkeep",
)


def classify(root: Path, baseline: Path, lock: dict, relpath: str, entry: dict) -> str:
    if entry.get("status") in ("forked", "watch", "local"):
        return entry["status"]
    up = upstream_text(baseline, relpath, lock["substitutions"])
    lo = local_text(root, relpath)
    if up is None:
        return "removed-upstream"
    if lo is None:
        return "missing-local"
    lock_hash = entry.get("hash")
    up_h, lo_h = sha(up.encode()), sha(lo.encode())
    if lo_h == up_h:
        return "in-sync" if lock_hash == up_h else "in-sync-lock-stale"
    if lo_h == lock_hash:
        return "upstream-updated"
    if up_h == lock_hash:
        return "local-modified"
    return "diverged"


def cmd_check(root, lock, baseline, as_json):
    results = {rp: classify(root, baseline, lock, rp, e)
               for rp, e in sorted(lock["files"].items())}
    if as_json:
        print(json.dumps({"baseline_commit": baseline_commit(baseline),
                          "results": results}, indent=2))
        return
    buckets: dict[str, list[str]] = {}
    for rp, state in results.items():
        buckets.setdefault(state, []).append(rp)
    quiet = {"in-sync", "watch", "forked", "local"}
    for state in sorted(buckets, key=lambda s: (s in quiet, s)):
        files = buckets[state]
        if state in quiet:
            print(f"{state}: {len(files)} file(s)")
        else:
            print(f"{state}:")
            for rp in files:
                print(f"  {rp}")
    actionable = [s for s in buckets if s not in quiet]
    print("\nclean" if not actionable
          else f"\nactionable states: {', '.join(sorted(actionable))}")


def cmd_diff(root, lock, baseline, relpath):
    up = upstream_text(baseline, relpath, lock["substitutions"]) or ""
    lo = local_text(root, relpath) or ""
    sys.stdout.writelines(difflib.unified_diff(
        up.splitlines(keepends=True), lo.splitlines(keepends=True),
        fromfile=f"baseline/{relpath}", tofile=f"local/{relpath}"))


def cmd_pull(root, lock, baseline, relpaths):
    targets = relpaths or [
        rp for rp, e in lock["files"].items()
        if classify(root, baseline, lock, rp, e) == "upstream-updated"]
    for rp in targets:
        up = upstream_text(baseline, rp, lock["substitutions"])
        if up is None:
            print(f"skip (no upstream): {rp}")
            continue
        dest = root / rp
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(up, encoding="utf-8")
        lock["files"][rp]["hash"] = sha(up.encode())
        print(f"pulled: {rp}")
    lock["synced_commit"] = baseline_commit(baseline)
    save_lock(root, lock)


def cmd_materialize(root, lock, baseline, relpaths):
    for rp in relpaths:
        lo = local_text(root, rp)
        if lo is None:
            print(f"skip (no local): {rp}")
            continue
        dest = baseline / "template" / rp
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(reverse_sub(lo, lock["substitutions"]), encoding="utf-8")
        print(f"materialized -> {dest}")
    print("\nReview the baseline worktree diff, split out anything project-specific, "
          "then commit/push there and run 'update-lock'.")


def cmd_update_lock(root, lock, baseline, relpaths):
    targets = relpaths or [
        rp for rp, e in lock["files"].items()
        if classify(root, baseline, lock, rp, e) == "in-sync-lock-stale"]
    for rp in targets:
        up = upstream_text(baseline, rp, lock["substitutions"])
        if up is None:
            continue
        lock["files"][rp]["hash"] = sha(up.encode())
        print(f"lock updated: {rp}")
    lock["synced_commit"] = baseline_commit(baseline)
    save_lock(root, lock)


def cmd_set_status(root, lock, relpaths, status):
    for rp in relpaths:
        entry = lock["files"].setdefault(rp, {})
        entry["status"] = status
        if status in ("forked", "local"):
            entry.pop("hash", None)
        print(f"{rp}: status={status}")
    save_lock(root, lock)


def cmd_paths(lock, status_filter):
    # Default output is the drift-gate set (tracked + watch); local/forked files are
    # project-owned and must not trigger the gate — request them via --status.
    for rp, e in sorted(lock["files"].items()):
        st = e.get("status", "tracked")
        if status_filter:
            if st != status_filter:
                continue
        elif st not in ("tracked", "watch"):
            continue
        print(rp)


def cmd_candidates(root: Path, lock: dict):
    import fnmatch
    r = subprocess.run(["git", "-C", str(root), "ls-files", "--", ".claude"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("error: candidates requires the project to be a git repository "
                 "(it scans committed .claude/ files) — run git init + commit first")
    known = set(lock["files"])
    found = False
    for rp in sorted(r.stdout.splitlines()):
        if rp in known:
            continue
        if any(rp.startswith(p) for p in CANDIDATE_EXCLUDE_PREFIXES):
            continue
        name = rp.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(name, g) for g in CANDIDATE_EXCLUDE_NAMES):
            continue
        print(rp)
        found = True
    if not found:
        print("(no candidates — every committed .claude/ artifact is known to the lock)")


def cmd_init(root, baseline_dir, repo, ref, subs):
    baseline = Path(baseline_dir).resolve()
    manifest_path = baseline / MANIFEST_NAME
    if not manifest_path.exists():
        sys.exit(f"error: {manifest_path} not found — run tools/gen_manifest.py "
                 "in the baseline first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {}
    for entry in manifest["files"]:
        rp = entry["path"]
        lo = local_text(root, rp)
        if lo is None:
            continue  # layer not adopted or file removed — leave untracked
        if entry.get("sync") == "seed":
            files[rp] = {"status": "watch", "layer": entry["layer"]}
            continue
        up = upstream_text(baseline, rp, subs)
        if up is not None and sha(up.encode()) == sha(lo.encode()):
            files[rp] = {"status": "tracked", "hash": sha(up.encode()),
                         "layer": entry["layer"]}
        else:
            files[rp] = {"status": "watch", "layer": entry["layer"]}
    lock = {"baseline_repo": repo, "baseline_ref": ref,
            "synced_commit": baseline_commit(baseline),
            "substitutions": subs, "files": files}
    save_lock(root, lock)
    tracked = sum(1 for e in files.values() if e["status"] == "tracked")
    watch = sum(1 for e in files.values() if e["status"] == "watch")
    print(f"lock written: {tracked} tracked, {watch} watch, "
          f"{len(manifest['files']) - len(files)} not adopted")


def main():
    # Windows consoles default to cp1252, which raises UnicodeEncodeError on the
    # non-ASCII glyphs (arrows, em-dashes) in diff/check output. Force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["check", "diff", "pull", "materialize",
                                   "update-lock", "fork", "track", "ignore",
                                   "candidates", "paths", "init"])
    ap.add_argument("relpaths", nargs="*")
    ap.add_argument("--baseline-dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--repo")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--sub", action="append", default=[],
                    metavar="PLACEHOLDER=VALUE")
    args = ap.parse_args()
    root = project_root()

    if args.op == "init":
        if not (args.baseline_dir and args.repo):
            sys.exit("init requires --baseline-dir and --repo")
        subs = dict(s.split("=", 1) for s in args.sub)
        cmd_init(root, args.baseline_dir, args.repo, args.ref, subs)
        return

    lock = load_lock(root)
    if args.op == "paths":
        cmd_paths(lock, args.status)
        return
    if args.op == "candidates":
        cmd_candidates(root, lock)
        return
    if args.op in ("fork", "track", "ignore"):
        status = {"fork": "forked", "track": "tracked", "ignore": "local"}[args.op]
        cmd_set_status(root, lock, args.relpaths, status)
        return

    baseline = ensure_baseline(lock, root, args.baseline_dir)
    if args.op == "check":
        cmd_check(root, lock, baseline, args.json)
    elif args.op == "diff":
        cmd_diff(root, lock, baseline, args.relpaths[0])
    elif args.op == "pull":
        cmd_pull(root, lock, baseline, args.relpaths)
    elif args.op == "materialize":
        cmd_materialize(root, lock, baseline, args.relpaths)
    elif args.op == "update-lock":
        cmd_update_lock(root, lock, baseline, args.relpaths)


if __name__ == "__main__":
    main()
