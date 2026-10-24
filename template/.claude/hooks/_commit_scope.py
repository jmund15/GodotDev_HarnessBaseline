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

`hooks/git_guardrails.py` and `hooks/baseline_classification_guard.py` both read this, so the two
guards cannot disagree about what a merge authored.
"""
import os
import subprocess
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _git_commit import git_environ, run_git, staged_paths  # noqa: E402

__all__ = ["authored_paths", "publishes_index", "writes_index", "merge_authored", "incoming_heads"]

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
    return {pair: None if line.endswith((" missing", " ambiguous")) else line.split(" ", 1)[0]
            for pair, line in zip(pairs, lines)}


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
