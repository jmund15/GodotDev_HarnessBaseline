"""
Library (not a hook): the one rule for which invocations publish the index and which paths a commit
AUTHORS.

A merge / cherry-pick / revert re-commits content that was judged where it was committed on its own
ref; only a conflict resolution or an edit differs from BOTH sides, so only those paths are this
commit's own. A path absent from both the index and the incoming head is a deletion that head made.
Every invocation that commits the index (`--continue` included) is judged, not only `git commit`.

    authored_paths(paths, cwd, git_env=None) -> list   the subset of `paths` this commit authors
    publishes_index(sub, rest)              -> bool    whether the invocation commits the index
    writes_index(args)                      -> bool    whether a git invocation changes the index or HEAD
    incoming_heads(cwd, git_env=None)       -> list    the ref holding the incoming side, or []
    merge_authored(rest, cwd, git_env=None) -> set|None  same, for a guard that scans the whole tree
    merge_dropped_lines(ours, theirs, result, cwd, git_env=None) -> (drops, unchecked)
                                            lines a parent added under `.claude/` since the merge
                                            base that the merge result lacks

`hooks/git_guardrails.py` and `hooks/baseline_classification_guard.py` both read this, so the two
guards cannot disagree about what a merge authored.
"""
import os
import subprocess
import sys
from collections import Counter

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _git_commit import git_environ, run_git, staged_paths  # noqa: E402

__all__ = ["authored_paths", "publishes_index", "writes_index", "merge_authored", "incoming_heads",
           "merge_dropped_lines"]

# The `--continue` that finishes a stopped merge / cherry-pick / revert / rebase runs `git commit` itself;
# am's `--continue` and `--resolved` (or `-r`) commit the resolved patch the same way.
_CONTINUE_FLAGS = {"merge": ("--continue",), "cherry-pick": ("--continue",), "revert": ("--continue",),
                   "rebase": ("--continue",), "am": ("--continue", "--resolved", "-r")}

# The ref holding the incoming side of each operation: `git rev-parse <HEAD>:<path>` reads the
# version that ref already committed. One exists only while its operation is mid-flight.
_INCOMING_HEADS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


def publishes_index(sub, rest):
    """True when this invocation publishes the index: `git commit`, or the continue form that closes a
    merge / cherry-pick / revert / rebase / am. A bare `merge`/`cherry-pick`/`revert`/`rebase`
    re-commits content judged on its own ref, or stops at a conflict for one of the forms above to finish."""
    return sub == "commit" or any(a in _CONTINUE_FLAGS.get(sub, ()) for a in rest)


# Subcommands that change the index or HEAD a later commit publishes against. `stash list|show` and a
# plain `apply` (worktree only) read or touch nothing a commit publishes.
_INDEX_WRITERS = {"add", "stage", "rm", "mv", "reset", "restore", "update-index", "read-tree", "checkout",
                  "switch", "stash", "merge", "cherry-pick", "revert", "pull", "rebase", "am", "apply"}


def writes_index(args):
    """True when `git <args>` changes the index or HEAD, so a commit later in the same command would be
    judged by every commit guard against an index the command has not yet written."""
    if not args or args[0] not in _INDEX_WRITERS:
        return False
    if args[0] == "stash":
        return not (len(args) > 1 and args[1] in ("list", "show"))
    if args[0] == "apply":
        return any(a in ("--cached", "--index", "-3", "--3way") for a in args[1:])
    return True


def _blobs(refs, paths, cwd, git_env):
    """One `git cat-file --batch-check` read of every (ref, path) pair: {(ref, path): blob id, or
    None when the ref or the path is absent}. None instead of a dict when the read fails."""
    info = _blob_info(refs, paths, cwd, git_env)
    return None if info is None else {pair: entry and entry[0] for pair, entry in info.items()}


def _blob_info(refs, paths, cwd, git_env):
    """`_blobs` with each blob's size: {(ref, path): (blob id, size) or None}, or None."""
    pairs = [(ref, path) for path in paths for ref in refs]
    stdin = "".join("%s:%s\n" % pair for pair in pairs).encode("utf-8")
    try:
        result = subprocess.run(["git", "cat-file", "--batch-check"], cwd=cwd, input=stdin,
                                capture_output=True, timeout=60, env=git_environ(git_env))
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = result.stdout.decode("utf-8", errors="replace").split("\n")
    if result.returncode != 0 or len(lines) < len(pairs):
        return None
    info = {}
    for pair, line in zip(pairs, lines):
        fields = line.split(" ")
        missing = line.endswith((" missing", " ambiguous")) or len(fields) < 3
        info[pair] = None if missing else (fields[0], int(fields[2]) if fields[2].isdigit() else 0)
    return info


def incoming_heads(cwd, git_env=None):
    """The refs holding the incoming side of the operation in progress -- `MERGE_HEAD`,
    `CHERRY_PICK_HEAD`, `REVERT_HEAD` -- or [] when none is. At most one exists at a time."""
    return [head for head in _INCOMING_HEADS
            if run_git(["rev-parse", "-q", "--verify", head], cwd, git_env) is not None]


def authored_paths(paths, cwd, git_env=None):
    """The members of `paths` this commit authors: staged content differing from BOTH sides, i.e. a
    conflict resolution or an edit made during the merge / cherry-pick / revert. A path equal to
    HEAD's or the incoming ref's version -- and a path absent from both the index and that ref (a
    deletion the incoming side made) -- arrived from a ref where it was already judged. Resolving a
    conflict to the incoming side therefore counts as authored nothing. Without an operation in
    progress every path is this commit's own, so an ordinary commit is judged exactly as before."""
    paths = list(paths)
    if not paths:
        return paths
    heads = incoming_heads(cwd, git_env)
    if not heads:
        return paths
    blobs = _blobs(["", "HEAD"] + heads, paths, cwd, git_env)
    if blobs is None:
        return paths
    return [norm for norm in paths
            if blobs[("", norm)] != blobs[("HEAD", norm)]
            and all(blobs[("", norm)] != blobs[(head, norm)] for head in heads)]


def merge_authored(rest, cwd, git_env=None):
    """The paths a commit closing a merge, cherry-pick or revert authors, or None when none of the
    three is in progress.

    A guard whose scan is the whole tree, not a caller-supplied path list, asks this instead of
    `authored_paths`: None means "judge everything", so an ordinary commit is judged exactly as
    before, and a set means "only these paths". The set is bounded by the index -- only staged
    content can be authored -- so the per-path blob reads stay small on a large tree. An unreadable
    index returns None too: narrowing on a failed read would drop every finding, which reads as an
    allow."""
    if not incoming_heads(cwd, git_env):
        return None
    paths, _failed = staged_paths(rest, cwd, git_env)
    if paths is None:
        return None
    return set(authored_paths(paths, cwd, git_env))


MERGE_DROP_BYTE_CAP = 8 << 20  # blob bytes one check reads; a file past it is named unchecked


def _dropped(base, side, result):
    """The lines `side` added since `base` that `result` lacks, in `side`'s order. Counted per line,
    so a moved line counts none and a line added twice and kept once counts one. A blank line never
    counts."""
    have_base, have_side, have_result = Counter(base), Counter(side), Counter(result)
    owed = {}
    for line, n in have_side.items():
        added = n - have_base[line]
        if line.strip() and added > 0 and have_result[line] < n:
            owed[line] = min(added, n - have_result[line])
    out = []
    for line in side:
        if owed.get(line, 0) > 0:
            out.append(line)
            owed[line] -= 1
    return out


def _read_blobs(oids, cwd, git_env):
    """{blob id: text} from one `git cat-file --batch` read; a binary blob (a NUL byte) reads None.
    None instead of a dict when the read fails."""
    if not oids:
        return {}
    stdin = "".join(oid + "\n" for oid in oids).encode("ascii")
    try:
        result = subprocess.run(["git", "cat-file", "--batch"], cwd=cwd, input=stdin,
                                capture_output=True, timeout=60, env=git_environ(git_env))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out, pos, texts = result.stdout, 0, {}
    for oid in oids:
        end = out.find(b"\n", pos)
        header = out[pos:end].decode("ascii", "replace").split(" ") if end >= 0 else []
        if len(header) < 3 or not header[2].isdigit():
            return None
        size = int(header[2])
        body = out[end + 1:end + 1 + size]
        pos = end + 2 + size
        texts[oid] = None if b"\0" in body else body.decode("utf-8", errors="replace")
    return texts


def merge_dropped_lines(ours, theirs, result, cwd, git_env=None, prefix=".claude/"):
    """Lines each parent added under `prefix` since the merge base that the merge `result` lacks.

    `ours` and `theirs` are the parents' revisions; `result` is a tree-ish, or "" for the index.
    Returns ({"ours"|"theirs": {path: {"dropped": [line, ...], "added": n}}}, unchecked paths), where
    `added` counts the non-blank lines that parent added to the file. A path whose blobs would pass
    MERGE_DROP_BYTE_CAP is listed unchecked. A parent's own deletion of a file drops nothing. A
    failed git read returns what it could not check as unchecked, or ({}, []) when the merge base
    or the changed paths cannot be read. Five git reads, whatever the merge's size."""
    base = run_git(["merge-base", ours, theirs], cwd, git_env)
    if not base or not base.strip():
        return {}, []
    base = base.strip()
    sides = {"ours": ours, "theirs": theirs}
    changed = {}
    for label, rev in sides.items():
        out = run_git(["diff", "--name-only", "-z", "--no-renames", base, rev, "--", ":(top)" + prefix],
                      cwd, git_env)
        if out is None:
            return {}, []
        changed[label] = [p for p in out.split("\0") if p]
    paths = sorted(set(changed["ours"]) | set(changed["theirs"]))
    if not paths:
        return {}, []
    info = _blob_info([base, ours, theirs, result], paths, cwd, git_env)
    if info is None:
        return {}, paths
    jobs, unchecked, budget = [], [], MERGE_DROP_BYTE_CAP
    for label, rev in sides.items():
        for path in changed[label]:
            side, kept = info[(rev, path)], info[(result, path)]
            if side is None or (kept and kept[0] == side[0]):
                continue
            was = info[(base, path)]
            cost = sum(entry[1] for entry in (was, side, kept) if entry)
            if cost > budget:
                unchecked.append(path)
                continue
            budget -= cost
            jobs.append((label, path, was, side, kept))
    oids = sorted({entry[0] for job in jobs for entry in job[2:] if entry})
    texts = _read_blobs(oids, cwd, git_env)
    if texts is None:
        return {}, sorted(set(unchecked) | {job[1] for job in jobs})
    drops = {}
    for label, path, was, side, kept in jobs:
        side_text = texts.get(side[0])
        if side_text is None:
            continue
        base_lines = (texts.get(was[0]) or "").splitlines() if was else []
        kept_lines = (texts.get(kept[0]) or "").splitlines() if kept else []
        side_lines = side_text.splitlines()
        dropped = _dropped(base_lines, side_lines, kept_lines)
        if dropped:
            added = sum(n for line, n in (Counter(side_lines) - Counter(base_lines)).items() if line.strip())
            drops.setdefault(label, {})[path] = {"dropped": dropped, "added": added}
    return drops, sorted(set(unchecked))
