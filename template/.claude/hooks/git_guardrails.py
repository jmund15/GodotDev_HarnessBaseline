#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — block git operations that destroy local state: deny `git commit --amend` of a HEAD that is neither this
session's nor a lane's own commit (`amend_verdict`; floor guard), and deny a commit
that a git index write (`add`, `rm`, `reset`, `stash`, ...; `_commit_scope.writes_index`) precedes in the
same command: every commit guard reads the index before the command runs, so it would judge the commit
without that write. A repo the same command creates with `git init` is exempt.

Why:
- Uncommitted and unpushed work has no remote copy and no undo. A single
  `git reset --hard` / `git clean -f` / force-push discards work that no other
  session, branch, or reflog entry can recover. Concurrent sessions share one
  checkout, so the loss is not even necessarily the caller's own work.
- Every blocked operation has a non-destructive alternative that preserves the
  same intent (stash, --keep, dry-run, unstage), so blocking costs one extra
  step and never a re-do.

A merge commit whose result drops lines a parent added to a `.claude/` file since the merge base
names them as an advisory (`merge_drop_advisory`), never a deny.

Scope: destructive local actions and history rewrites. `git
status`, `git restore --staged`, `git clean -n`, `git branch -d` stay allowed —
they are recoverable or non-destructive. So does `git branch -D` of a local branch whose every
commit `git cherry` finds already applied to HEAD or origin/HEAD (a squash-merged branch), of a branch
whose tip a ref outside the deleted set holds (a remote-tracking ref, a tag, `refs/preserve/`), or of a
`scratch/<name>` session fixture branch (`orchestration` reference `worktrees.md`). A path-scoped checkout
or restore that loses no edit passes too (`_discards_nothing`: a `__pycache__` path, an edit git stores, a restore
of a file the worktree lacks).
`git stash drop` of one entry passes when a ref outside `refs/stash` holds the entry commit, or when one ref's
tip already holds every change the entry carries (`_stash_changes_held`).

Matching: `_git_commit.py` owns the command parse (heredoc bodies stripped, segments split,
`git -C <path>` and a preceding `cd <path>` retarget the repo, inline `VAR=1` prefixes read).
Pathological quoting may over-block; the message names the alternative, so the recovery is
one line.

Wired in: settings.json hooks.PreToolUse with matcher "Bash|PowerShell".
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
sys.path.insert(0, os.path.join(_CLAUDE_DIR, "scripts"))
sys.path.insert(0, _HOOKS_DIR)
# Guard imports so a broken helper cannot crash the destructive-state checks. State failure denies
# commits.
try:
    from _hook_state import spent_mark, unspend  # noqa: E402
    _STATE_IMPORT_ERROR = None
except Exception as _exc:
    spent_mark, unspend = (lambda: 0), (lambda _start, _stop=None: None)
    _STATE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)


# Which paths a merge commit authors, shared with `baseline_classification_guard.py`. Guarded like
# the two above: a missing helper must deny commit-like invocations, never crash the other git guards.
try:
    from _commit_scope import publishes_index, writes_index  # noqa: E402
    _SCOPE_IMPORT_ERROR = None
except Exception as _exc:
    publishes_index = writes_index = None
    _SCOPE_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)
# The merge-drop advisory's helpers left the shared helpers with v2 (V1 Lost); without them it stays silent.
try:
    from _commit_scope import merge_dropped_lines  # noqa: E402
    from _git_commit import merge_refs, merge_strategy  # noqa: E402
except ImportError:
    merge_dropped_lines = merge_refs = merge_strategy = None
from _git_commit import (  # noqa: E402
    git_environ,
    git_invocation, git_invocations, executable_text, run_git,
    staged_paths, incoming_paths, bypass_declared, parse_commit_args, resolve_index,
    _pathspec_file,
    unexpanded_hint, resolve_cd, expand_literal, ref_resolves, refs_created, ref_names_created,
)

REF_MAKERS = ("fetch", "update-ref", "branch", "tag")  # subcommands that can create the ref a later merge names

try:
    from _regenerable_clone import session_created_worktree, worktree_unstored
except Exception:
    session_created_worktree = worktree_unstored = None
try:
    sys.path.insert(0, os.path.join(_CLAUDE_DIR, 'tools'))
    from sidecar_argv import command_substitutions, shell_segments, executable_index, literal_launcher_body
    _OPAQUE_IMPORT_ERROR = None
except Exception as _exc:
    command_substitutions = shell_segments = executable_index = None
    _OPAQUE_IMPORT_ERROR = '%s: %s' % (type(_exc).__name__, _exc)

BYPASS_VAR = "HARNESS_ALLOW_UNSTAMPED_HARNESS"
# Every commit guard reads the index before the command runs, so a write earlier in the same command
# lands after they judged the commit.
SAME_COMMAND_WRITE = ("BLOCKED commit (CLAUDE.core.md §Shell Discipline: an index write runs in its own Bash call) — `%s` "
                      "runs earlier in this command, and every commit guard reads the index before the command "
                      "runs, so they would judge this commit without that write. Run the `%s` in its own Bash "
                      "call, then commit.")


def short_flag_chars(args):
    """Characters of single-dash bundled flags, e.g. -fdx -> {'f','d','x'}."""
    chars = set()
    for a in args:
        if a.startswith("-") and not a.startswith("--"):
            chars.update(a[1:])
    return chars


_SIDE_UNSAFE = (".tscn", ".tres")


# Options a path-scoped discard may carry and still be judged by the worktree diff alone. Any other
# (`--staged`/`-S`, `--worktree`/`-W`, a short `-s <tree>`, `-f`, a bundle) reads as a loss.
_NO_LOSS_OPTS = {"--theirs", "--ours", "-q", "--quiet"}


def _discards_nothing(sub, rest, cwd, git_env=None):
    """True when a path-scoped checkout/restore loses no edit: every named path is an unmerged
    path taken with --theirs/--ours (not .tscn/.tres, where one side silently drops Export wiring),
    or sits in a `__pycache__` directory (Python regenerates the bytecode), or has no difference from its source
    beyond CR line endings, or (checked out of another ref) is a
    path HEAD tracks with no staged or unstaged edit, or a path absent from HEAD, index and worktree,
    or (a restore, which writes only the worktree) a path absent from the worktree, which holds no bytes to lose.
    Any doubt reads as a loss."""
    if cwd is None or (sub == "checkout" and "--" not in rest):
        return False
    split = rest.index("--") if "--" in rest else len(rest)
    head = rest[:split]
    paths = rest[split + 1:] if "--" in rest else [a for a in rest if not a.startswith("-")]
    opts = [a for a in head if a.startswith("-")]
    refs = [a for a in head if not a.startswith("-")] if sub == "checkout" else []
    source = [a.split("=", 1)[1] for a in opts if a.startswith("--source=")]
    if (not paths or len(refs) > 1
            or any(o not in _NO_LOSS_OPTS and not o.startswith("--source=") for o in opts)
            or any(p in (".", ":/", "/") or re.search(r"[*?\[\]$`%]", p) for p in paths)):
        return False
    side = "--theirs" in opts or "--ours" in opts
    base = refs[0] if refs else (source[0] if source else None)
    for path in paths:
        if "__pycache__" in Path(path).parts and ".." not in Path(path).parts and not path.startswith(":"):
            continue
        if side:
            if path.lower().endswith(_SIDE_UNSAFE):
                return False
            unmerged = run_git(["ls-files", "-u", "--", path], cwd, git_env)
            if not (unmerged or "").strip():
                return False
            continue
        if sub == "restore" and not path.startswith(":") and not os.path.lexists(os.path.join(cwd, path)):
            continue
        diff = ["diff", "--quiet", "--ignore-cr-at-eol"] + ([base] if base else []) + ["--", path]
        if (run_git(diff, cwd, git_env) is None and not (base and _clean_in_head(path, cwd, git_env))
                and not (_content_stored(path, cwd) and (sub == "restore" or not base))):
            return False
    return True


def _content_stored(path, cwd):
    """True when git's object store holds the working-tree bytes of `path`, so discarding them loses nothing
    git cannot reproduce (the orphan-worktree rule's standard: `_regenerable_clone.worktree_unstored`). A restore
    or a checkout from the index qualifies; a checkout from another ref also overwrites the index, whose staged
    bytes this does not judge."""
    if worktree_unstored is None or not os.path.isfile(os.path.join(cwd, path)):
        return False
    return worktree_unstored(cwd, [path]) == []


def _clean_in_head(path, cwd, git_env=None):
    """True when HEAD tracks `path` and neither the index nor the worktree holds an edit to it: overwriting it
    from another commit loses nothing git does not still hold (landing a merge onto a shared checkout)."""
    if run_git(["cat-file", "-e", "HEAD:" + path], cwd, git_env) is None:
        # Absent from HEAD, the index and the worktree: the checkout creates it and overwrites nothing.
        return (not os.path.lexists(os.path.join(cwd, path))
                and run_git(["ls-files", "--", path], cwd, git_env) == "")
    return (run_git(["diff", "--quiet", "--ignore-cr-at-eol", "HEAD", "--", path], cwd, git_env) is not None
            and run_git(["diff", "--quiet", "--cached", "HEAD", "--", path], cwd, git_env) is not None)


def _stash_preserved(args, cwd, git_env=None):
    """True when the one stash entry `git stash drop` targets loses nothing git cannot reach: a ref outside
    refs/stash holds the entry commit, or `_stash_changes_held` finds every change it carries in one ref's tip."""
    args = [a for a in args if a not in ("-q", "--quiet")]
    targets = [a for a in args if not a.startswith("-")]
    if len(targets) > 1 or len(targets) != len(args) or not cwd:
        return False
    sha = run_git(["rev-parse", "--verify", "--quiet", (targets or ["stash@{0}"])[0] + "^{commit}"], cwd, git_env)
    if not sha or not sha.strip():
        return False
    holders = run_git(["for-each-ref", "--contains", sha.strip(), "--format=%(refname)"], cwd, git_env)
    if any(ref and ref != "refs/stash" for ref in (holders or "").splitlines()):
        return True
    return _stash_changes_held(sha.strip(), cwd, git_env)


_STASH_PREFILTER_PATHS = 16


def _tree_names(args, cwd, git_env):
    """The NUL-separated path list of a `git diff`/`ls-tree` call as a set, or None when the call failed."""
    out = run_git(args, cwd, git_env)
    return None if out is None else {p for p in out.split("\0") if p}


def _stash_changes_held(stash, cwd, git_env=None):
    """True when one ref outside refs/stash has, at its tip, every change the stash entry carries: each tracked
    path the entry's worktree tree or its index commit (`stash^2`) changes against its base (`stash^1`), and each
    file of its untracked commit (`stash^3`), is byte- and mode-equal at that tip (the worktree and index versions
    both, so a staged version no tip holds is not lost silently). Fails closed on a deleted branch, a branch that
    moved past the change, a detached HEAD (no ref), a partial match, an entry with no change to compare, a path a
    batch query cannot name (a newline or an undecodable byte) and any git failure. `cat-file --batch-check`
    prefilters every ref tip in one process on the first `_STASH_PREFILTER_PATHS` paths, a cost bound only: the
    tree diffs on each surviving tip are the verdict."""
    index = run_git(["rev-parse", "--verify", "--quiet", stash + "^2^{tree}"], cwd, git_env)
    base = run_git(["rev-parse", "--verify", "--quiet", stash + "^1^{tree}"], cwd, git_env)
    untracked = run_git(["rev-parse", "--verify", "--quiet", stash + "^3^{tree}"], cwd, git_env)
    if not index or not base:
        return False
    worktree_paths = _tree_names(["diff", "--name-only", "-z", "--no-renames", stash + "^1", stash], cwd, git_env)
    index_paths = _tree_names(["diff", "--name-only", "-z", "--no-renames", stash + "^1", stash + "^2"], cwd, git_env)
    untracked_paths = (_tree_names(["ls-tree", "-r", "--name-only", "-z", stash + "^3"], cwd, git_env)
                       if untracked and untracked.strip() else set())
    if worktree_paths is None or index_paths is None or untracked_paths is None:
        return False
    expected = ([(stash, p) for p in sorted(worktree_paths)]
                + [(stash + "^2", p) for p in sorted(index_paths - worktree_paths)]
                + [(stash + "^3", p) for p in sorted(untracked_paths)])
    if not expected or any("\n" in p or "�" in p for _, p in expected):
        return False
    refs = run_git(["for-each-ref", "--format=%(objectname) %(refname)"], cwd, git_env)
    if refs is None:
        return False
    tips = []
    for line in refs.splitlines():
        tip, _, name = line.partition(" ")
        if tip and name != "refs/stash" and tip not in tips:
            tips.append(tip)
    probe = expected[:_STASH_PREFILTER_PATHS]
    queries = [f"{tree}:{p}" for tree, p in probe]
    for tip in tips:
        queries.extend(f"{tip}:{p}" for _, p in probe)
    try:
        batch = subprocess.run(["git", "cat-file", "--batch-check"], cwd=cwd, input="\n".join(queries).encode("utf-8") + b"\n",
                               capture_output=True, timeout=30, env=git_environ(git_env))
    except (OSError, subprocess.TimeoutExpired):
        return False
    rows = batch.stdout.decode("utf-8", "replace").splitlines()
    if batch.returncode != 0 or len(rows) != len(queries):
        return False
    answers = [None if row.endswith(" missing") else row.split(" ", 1)[0] for row in rows]
    want = answers[:len(probe)]
    for n, tip in enumerate(tips):
        if answers[len(probe) * (n + 1):len(probe) * (n + 2)] != want:
            continue
        if _tip_holds_stash(tip, stash, worktree_paths, index_paths, untracked_paths, cwd, git_env):
            return True
    return False


def _tip_holds_stash(tip, stash, worktree_paths, index_paths, untracked_paths, cwd, git_env):
    """The exact verdict for one ref tip: no path the entry's worktree tree changes differs between the tip and
    that tree, no path its index commit changes differs between the tip and that commit (an unstaged path leaves
    the index at its base, so it is not compared there), and no untracked file differs between the tip and
    `stash^3`."""
    for side, paths in ((stash, worktree_paths), (stash + "^2", index_paths)):
        if paths:
            differing = _tree_names(["diff", "--name-only", "-z", "--no-renames", tip, side], cwd, git_env)
            if differing is None or differing & paths:
                return False
    if untracked_paths:
        differing = _tree_names(["diff", "--name-only", "-z", "--no-renames", "--diff-filter=AMTU", tip, stash + "^3"],
                                cwd, git_env)
        if differing is None or differing & untracked_paths:
            return False
    return True


_BRANCH_DELETE_FLAGS = ("-D", "--delete", "--force", "-f", "-q", "--quiet")


def _branch_deletion_problems(rest, cwd, git_env=None):
    """Why `branch -D` would lose a commit: one line per unmet branch, empty when it loses none. A branch is safe
    when it is a `scratch/<name>` session fixture (`orchestration` reference `worktrees.md`), when every commit
    `git cherry` finds is already applied to HEAD or to origin/HEAD (a squash-merged or cherry-picked branch, which
    `-d` refuses), or when a ref outside the deleted set (a remote-tracking ref, a tag, `refs/preserve/`) holds its
    tip. Any other flag, a missing branch or a failed git call is a problem."""
    names = [a for a in rest if not a.startswith("-")]
    if not cwd or not names:
        return ["no branch is named, or the repository cannot be read"]
    problems = ["flag `%s` is not one `branch -D` is judged with" % a
                for a in rest if a.startswith("-") and a not in _BRANCH_DELETE_FLAGS]
    bases = ["HEAD"] + (["refs/remotes/origin/HEAD"]
                        if run_git(["rev-parse", "--verify", "-q", "refs/remotes/origin/HEAD"], cwd, git_env) else [])
    doomed = {"refs/heads/" + n for n in names}
    for name in names:
        tip = run_git(["rev-parse", "--verify", "-q", "refs/heads/" + name], cwd, git_env)
        if tip is None or not tip.strip():
            problems.append("`%s` is not a local branch" % name)
            continue
        if name.startswith("scratch/"):
            continue
        if any(out is not None and not any(l.startswith("+") for l in out.splitlines())
               for out in (run_git(["cherry", base, "refs/heads/" + name], cwd, git_env) for base in bases)):
            continue
        holders = run_git(["for-each-ref", "--contains", tip.strip(), "--format=%(refname)"], cwd, git_env)
        if not any(ref and ref not in doomed for ref in (holders or "").splitlines()):
            problems.append("`%s` holds commits no other ref has" % name)
    return problems


def _in_own_worktree(cwd, git_env, transcript_path):
    """True when `cwd` (resolved through `-C`) lies in a worktree this session created, a submodule checked out
    inside one included: that clone is the session's own. Redirected git state or an unknown session reads False."""
    if (not session_created_worktree or not cwd or not transcript_path or any((git_env or {}).get(k) for k in
            ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'))):
        return False
    top = run_git(['rev-parse', '--show-toplevel'], cwd, git_env)
    while top and top.strip():
        if session_created_worktree(top.strip(), transcript_path):
            return True
        top = run_git(['rev-parse', '--show-superproject-working-tree'], top.strip(), git_env)
    return False


def _own_worktree_discard(sub, rest, cwd, git_env, transcript_path):
    if sub == 'checkout' and '--' not in rest:
        return False
    if sub not in ('checkout', 'restore') or not rest:
        return False
    return _in_own_worktree(cwd, git_env, transcript_path)


def _own_clean_reset(cwd, git_env, transcript_path):
    """A `reset --hard` in a worktree this session created whose tree holds nothing uncommitted, untracked files
    included, discards nothing git holds nowhere else. An unreadable status reads as dirty."""
    if not _in_own_worktree(cwd, git_env, transcript_path):
        return False
    status = run_git(['status', '--short'], cwd, git_env)
    return status is not None and status.strip() == ''


def _named_paths(rest, cwd):
    """(index of `--` in `rest`, else len(rest); True when a path is `.`, `:/`, `/`, a glob or a directory)."""
    split = rest.index("--") if "--" in rest else len(rest)
    paths = rest[split + 1:] if "--" in rest else [a for a in rest if not a.startswith("-")]
    bulk = any(p in (".", ":/", "/") or re.search(r"[*?\[\]$`%]", p) or (cwd and os.path.isdir(os.path.join(cwd, p)))
               for p in paths)
    return split, bulk


def _checkout_retry(rest, cwd):
    """The step after the save that `_discards_nothing` admits for this checkout's shape: the same command for
    `checkout -- <file>`, `restore --source` for a checkout from a ref (its stored-bytes test covers a restore only),
    and one named file at a time for `.`, a glob or a directory (it judges named files only)."""
    split, bulk = _named_paths(rest, cwd)
    if bulk:
        return "then name each file: `git checkout -- <file>` for one saved edit at a time."
    refs = [a for a in rest[:split] if not a.startswith("-")]
    if refs:
        return "then run `git restore --source=%s -- <path>`." % refs[0]
    return "then the same command passes."


def _restore_retry(rest, cwd):
    """The step after the save that `_discards_nothing` admits for this restore's shape: one named file at a
    time for `.`, a glob or a directory, no `--worktree`/`-W` (restore's default), `--source=<ref>` spelled with
    the equals sign, else the same command."""
    _split, bulk = _named_paths(rest, cwd)
    if bulk:
        return "then name each file: `git restore <file>` for one saved edit at a time."
    if "--worktree" in rest or "W" in short_flag_chars(rest):
        return "then run it without `--worktree`/`-W` (restore's default), one saved file at a time."
    if "-s" in rest:
        return "then spell the source `--source=<ref>` and name one saved file."
    return "then the same command passes."


def verdict(args, cwd=None, git_env=None, transcript_path=None):
    """Return a one-line block message, or None if allowed. `cwd` is the repo the command
    targets; without it a checkout/restore discard is judged unsafe."""
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub in ("checkout", "restore"):
        redirected = any((git_env or {}).get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE'))
        if not redirected and (_discards_nothing(sub, rest, cwd, git_env)
                or _own_worktree_discard(sub, rest, cwd, git_env, transcript_path)):
            return None

    if sub == "reset" and "--hard" in rest and not _own_clean_reset(cwd, git_env, transcript_path):
        return ("BLOCKED `git reset --hard` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "discards every uncommitted change in the shared worktree with no undo. Use "
                "`git reset --keep` to move HEAD and keep local edits.")

    if sub == "clean":
        flags = short_flag_chars(rest)
        dry = "n" in flags or "--dry-run" in rest
        if not dry and ("f" in flags or "--force" in rest):
            return ("BLOCKED `git clean -f` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                    "deletes untracked files permanently (git has no copy). Run the same command with "
                    "`-n` first, then ask the user to confirm.")

    if sub == "checkout" and ("--" in rest or "." in rest):
        return ("BLOCKED `git checkout` worktree discard (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
                "overwrites uncommitted edits to those paths, which may be another session's; another session's "
                "edit needs the owner's words first. To discard your own edit, save it first: `git update-ref refs/preserve/<name> $(git hash-object -w <path>)`, "
                + _checkout_retry(rest, cwd))

    staged_only = ({"--staged", "-S"} & set(rest)) and not ("--worktree" in rest or "W" in short_flag_chars(rest))
    if sub == "restore" and not staged_only:
        return ("BLOCKED `git restore` without `--staged` (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
                "discards uncommitted worktree edits, which may be another session's; another session's edit "
                "needs the owner's words first. To discard your own edit, "
                "save it first: `git update-ref refs/preserve/<name> $(git hash-object -w <path>)`, "
                + _restore_retry(rest, cwd))

    if sub == "push":
        forcing = {"--force", "--force-with-lease", "--force-if-includes"} & set(rest)
        forcing = forcing or any(
            a.startswith(("--force-with-lease=", "--force-if-includes=")) for a in rest
        )
        forcing = forcing or "f" in short_flag_chars(rest)
        if forcing:
            return ("BLOCKED force-push (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites remote "
                    "history and can erase commits other checkouts still depend on. Rebase onto the remote and "
                    "push normally.")

    if sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
        problems = _branch_deletion_problems(rest, cwd, git_env)
        if problems:
            return ("BLOCKED `git branch -D` (CLAUDE.core.md §Shell Discipline: unreachable commits would be lost) — "
                    "force-deleting would lose commits: " + "; ".join(problems) + ". "
                    + ("Name only local branches, with no flag beyond `-D`. "
                       if any(not p.endswith("holds commits no other ref has") for p in problems) else "")
                    + ("Keep each branch's commits reachable with `git update-ref refs/preserve/<name> <branch>`, "
                       "then the same delete passes."
                       if any(p.endswith("holds commits no other ref has") for p in problems) else ""))

    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        if rest[0] == "drop" and _stash_preserved(rest[1:], cwd, git_env):
            return None
        if rest[0] == "drop":
            target = next((a for a in rest[1:] if not a.startswith("-")), "stash@{0}")
            return ("BLOCKED `git stash drop` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — stashed work "
                    "is unreferenced once dropped. With the owner's go-ahead, keep it reachable and drop: "
                    f"`git update-ref refs/preserve/<name> {target}`, then the same drop passes.")
        return ("BLOCKED `git stash clear` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "stashed work is unreferenced once dropped. Leave it; ask the user before discarding.")

    if sub == "reflog" and "expire" in rest:
        return ("BLOCKED `git reflog expire` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — the reflog "
                "is the last recovery path for lost commits. Leave expiry to git's own schedule.")

    if sub == "gc" and any(a == "--prune" or a.startswith("--prune=") for a in rest):
        return ("BLOCKED `git gc --prune` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — permanently "
                "removes unreachable objects that reflog recovery depends on. Run `git gc` without `--prune`.")

    if sub in ("filter-branch", "filter-repo"):
        return (f"BLOCKED `git {sub}` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites every "
                "commit in place, invalidating all existing clones and hashes. Ask the user for explicit "
                "go-ahead before rewriting history; never do it in-session.")

    return None


STAGE_RECORD_SUFFIX = ".paths.json"  # tools/index_commit.py writes `<index>.paths.json` beside its index


def base_verdict(cwd, git_env):
    """Deny publishing a private index over a HEAD other than the base it was read from.

    `index_commit.py stage` records `base_head` beside its index. Any commit after that base is lost
    by publishing the index, deletions of peer-added files included, which `revert_verdict` cannot
    see. Git's own HEAD update then compares against the HEAD the commit read, so a move after this
    check fails the commit instead of losing work. An index without a stage record is left to
    `revert_verdict`; a record that cannot be read denies."""
    index = (git_env or {}).get("GIT_INDEX_FILE")
    if not index or not os.path.isfile(index + STAGE_RECORD_SUFFIX):
        return None
    try:
        with open(index + STAGE_RECORD_SUFFIX, encoding="utf-8") as fh:
            base = (json.load(fh) or {}).get("base_head")
    except (OSError, ValueError, AttributeError):
        base = None
    if not isinstance(base, str):
        base = None
    head = run_git(["rev-parse", "HEAD"], cwd, git_env)
    if base and head is not None and head.strip() == base:
        return None
    reason = ("HEAD is %s but the index was read from base %s" % (head.strip()[:9], base[:9])
              if base and head is not None else "its stage record %s%s cannot be read"
              % (os.path.basename(index), STAGE_RECORD_SUFFIX))
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — %s, so publishing this "
            "private index would undo every commit since its base. Rerun `python3 "
            ".claude/tools/index_commit.py stage` (index_commit) with the same paths, then commit the "
            "new index." % reason)


REVERT_SCAN_CAP = 500  # staged paths checked; one stale file is enough to deny


def revert_verdict(rest, cwd, git_env, session_id=None, own_trailer=None):
    """Deny an index commit that reverts a peer's commit because its index was read from an older HEAD.

    Three signatures, each a path whose content the commit changes (a mode-only change is no revert):
    its working tree still matches HEAD; its staged blob is the version one of HEAD's last REVERT_HISTORY
    commits replaced, which holds even when a peer kept editing the file; or it is a staged
    addition the working tree lacks and HEAD's history deleted. Only index-publishing commits are
    judged; a pathspec or `-a` commit takes the working tree. Staged deletions are skipped, because
    `git rm --cached` has the same shape on purpose. A git failure skips that signature. A blob only
    this session's own commits replaced (its `Claude-Session` trailer, `_own_trailer`) is not a peer's,
    nor is one only a linked worktree's own commits replaced (`_lane_commits`), and neither is a merge
    staging the merged-in parent's own blob: that is the parent's history.
    While REVERT_HEAD names a commit, a path that commit changed against any of its parents (a merge
    revert included) is the revert git was asked for, not a stale index; every other path is judged as
    usual. The exemption is by path: a revert of a non-tip commit yields a blob no parent holds, so a
    blob test cannot stand in for it. `--pathspec-from-file` is a pathspec."""
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    has_spec = bool(pathspec) or _pathspec_file(rest) is not None
    if include_dirty or (has_spec and not ({"-i", "--include"} & set(rest))):
        return None
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        return None
    root = root.strip()
    reverting = run_git(["rev-parse", "-q", "--verify", "REVERT_HEAD"], root, git_env)

    def verdict(found):
        if reverting and found:
            changed = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "-z", "REVERT_HEAD"],
                              root, git_env)
            reverted = set((changed or "").split("\0"))
            found = [p for p in found if p not in reverted]
        return _revert_message(found)

    added =run_git(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"], root, git_env)
    readded = [p for p in (added or "").split("\0") if p and not os.path.lexists(os.path.join(root, p))
               and run_git(["log", "-1", "--format=%h", "--diff-filter=D", "HEAD", "--", p], root)]
    out = run_git(["diff", "--cached", "--name-only", "--diff-filter=M", "-z"], root, git_env)
    staged = _raw_blobs(run_git(["diff", "--cached", "--raw", "-z", "--no-abbrev", "--diff-filter=M"],
                                root, git_env))
    paths = [p for p in (out or "").split("\0") if p and staged.get(p, (0, 1))[0] != staged.get(p, (0, 1))[1]]
    paths = paths[:REVERT_SCAN_CAP]
    if not paths:
        return verdict(readded)
    head = run_git(["ls-tree", "-z", "HEAD", "--"] + paths, root)
    work = run_git(["hash-object", "--"] + paths, root)
    if head is None or work is None:
        return verdict(readded)
    head_blob = {}
    for entry in head.split("\0"):
        meta, _, path = entry.partition("\t")
        if path:
            head_blob[path] = meta.split()[-1]
    stale = [p for p, blob in zip(paths, work.split()) if head_blob.get(p) == blob]
    replaced = _replaced_blobs(root, own_trailer or _own_trailer(session_id))
    merging = run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root, git_env)
    theirs = {}
    if merging:
        for entry in (run_git(["ls-tree", "-z", "MERGE_HEAD", "--"] + paths, root) or "").split("\0"):
            meta, _, path = entry.partition("\t")
            if path:
                theirs[path] = meta.split()[-1]
    stale += [p for p in paths if p not in stale and staged.get(p, (None, None))[1] in replaced.get(p, ())
              and staged[p][1] != theirs.get(p)]
    return verdict(stale + readded)


REVERT_HISTORY = 30  # recent HEAD commits whose replaced blobs count as stale


def _raw_blobs(out):
    """{path: (old blob, new blob)} from `git ... --raw -z --no-abbrev` output; {} on None."""
    tokens = (out or "").split("\0")
    found = {}
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split()
        if meta.startswith(":") and len(fields) >= 4 and path:
            found[path] = (fields[2], fields[3])
    return found


def _own_trailer(session_id):
    """This session's commit trailer, `claude.ai/code/<bridgeSessionId>`, read from the runtime's
    local registry (~/.claude/sessions/<pid>.json) for `session_id`; None when unresolvable."""
    if not session_id:
        return None
    for path in (Path.home() / ".claude" / "sessions").glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("sessionId") == session_id and doc.get("bridgeSessionId"):
            return "claude.ai/code/" + doc["bridgeSessionId"]
    return None


def amend_verdict(rest, cwd, git_env=None, session_id=None):
    """Deny `git commit --amend` unless HEAD is this session's own commit (its trailer, `_own_trailer`) or a linked
    worktree's own commit (`_lane_commits`): in a shared checkout HEAD may be a peer's or the owner's commit,
    and an amend rewrites it. Floor guard: an unreadable HEAD or trailer denies."""
    if not parse_commit_args(rest)[1]:
        return None
    root = (run_git(["rev-parse", "--show-toplevel"], cwd, git_env) or cwd).strip()
    sha = (run_git(["rev-parse", "HEAD"], root, git_env) or "").strip()
    if sha and sha in _lane_commits(root):
        return None
    trailer = _own_trailer(session_id)
    body = run_git(["log", "-1", "--format=%B", "HEAD"], root, git_env) if sha else None
    if trailer and body is not None and trailer in body:
        return None
    if body is None:
        why = "HEAD cannot be read"
    elif not trailer:
        why = "this session's commit trailer cannot be resolved, so HEAD %s has no known author" % sha[:9]
    else:
        why = "HEAD %s carries no `%s` trailer, so another author made it" % (sha[:9], trailer)
    return ("BLOCKED git commit --amend (CLAUDE.core.md §Core Principles: preserve peer work) — %s, and the amend "
            "would rewrite it. Commit the change as a new commit." % why)


def _replaced_by(root, revs):
    """{path: {blob}} each commit in `revs` replaced (modifications only)."""
    out = run_git(["show", "--raw", "-z", "--no-abbrev", "--no-renames", "--format="] + revs, root) if revs else ""
    replaced = {}
    tokens = [t for t in (out or "").replace("\n", "").split("\0")]
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split()
        if meta.startswith(":") and len(fields) >= 5 and fields[4] == "M":
            replaced.setdefault(path, set()).add(fields[2])
    return replaced


def _lane_commits(root):
    """The commits above the merge-base of HEAD and the default branch, in a linked worktree; empty in
    the shared checkout or when the default branch or the merge-base cannot be read. A peer cannot
    commit into a lane's HEAD, so these are the lane's own line of work."""
    git_dir, common = (run_git(["rev-parse", "--git-dir", "--git-common-dir"], root) or "\n").split("\n")[:2]
    if not git_dir or os.path.realpath(os.path.join(root, git_dir)) == os.path.realpath(os.path.join(root, common)):
        return set()
    for ref in ("refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/remotes/origin/master"):
        base = run_git(["merge-base", "HEAD", ref], root)
        if base and base.strip():
            return set((run_git(["rev-list", "%s..HEAD" % base.strip()], root) or "").split())
    return set()


def _replaced_blobs(root, own_trailer=None):
    """{path: {blob}}: every blob one of HEAD's last REVERT_HISTORY commits replaced, except those
    replaced only by commits carrying `own_trailer` or by a lane's own commits (`_lane_commits`)."""
    revs = (run_git(["rev-list", "-n", str(REVERT_HISTORY), "HEAD"], root) or "").split()
    own = _lane_commits(root)
    if own_trailer and revs:
        own |= set((run_git(["log", "--format=%H", "-F", "--grep=" + own_trailer, "-n",
                             str(REVERT_HISTORY), "HEAD"], root) or "").split())
    return _replaced_by(root, [r for r in revs if r not in own])


def _revert_message(stale):
    """The denial for `stale` paths, the signatures `revert_verdict` names."""
    if not stale:
        return None
    shown = "\n  ".join(stale[:10]) + ("\n  ... %d more" % (len(stale) - 10) if len(stale) > 10 else "")
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — it would revert a peer's commit "
            "to %d file(s):\n  %s\n"
            "The index was built from an older HEAD. Rebuild it from the current HEAD, or commit "
            "your files with a pathspec (`git commit -F <msg> -- <paths>`)." % (len(stale), shown))


ADVISORIES = []  # commit advisories `main()` prints as additionalContext when no check denies

MERGE_DROP_SHOWN = (10, 8)  # files, then lines per file, the advisory prints; the rest it counts


def _merge_result(sub, rest, cwd, git_env):
    """(ours, theirs, result) for the merge this invocation commits, or None.

    The commit that closes a merge (`commit`, `merge --continue`) commits the index against
    `MERGE_HEAD`. A bare two-parent `git merge` that commits at once is read only when its strategy
    can drop a side's lines without a conflict (`-s ours`, `-X ours|theirs`); a default merge keeps
    every added line or stops at a conflict, whose closing commit is read instead. A `-a` commit
    takes the worktree, which this reads as the index."""
    if publishes_index(sub, rest):
        if run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd, git_env) is None:
            return None
        if (run_git(["ls-files", "-u"], cwd, git_env) or "").strip():
            return None
        return "HEAD", "MERGE_HEAD", ""
    if sub != "merge" or merge_refs is None or any(a in ("--no-commit", "--squash", "--abort", "--quit", "--continue") for a in rest):
        return None
    refs = merge_refs(rest)
    strategy, options = merge_strategy(rest)
    if len(refs) != 1:
        return None
    if strategy == "ours":
        return "HEAD", refs[0], "HEAD^{tree}"
    picks = [o for o in options if o in ("ours", "theirs")]
    if not picks or strategy not in (None, "ort", "recursive"):
        return None
    tree = run_git(["merge-tree", "--write-tree", "-X", picks[-1], "HEAD", refs[0]], cwd, git_env)
    if not tree or not tree.split():
        return None
    return "HEAD", refs[0], tree.split()[0]


def merge_drop_advisory(sub, rest, cwd, git_env=None):
    """The advisory naming the lines a merge drops that a parent added to a `.claude/` file since the
    merge base, or None. Never a deny: a replacement made on purpose reads the same."""
    merge = _merge_result(sub, rest, cwd, git_env)
    if merge is None or merge_dropped_lines is None:
        return None
    ours, theirs, result = merge
    drops, unchecked = merge_dropped_lines(ours, theirs, result, cwd, git_env)
    if not drops and not unchecked:
        return None
    files_shown, lines_shown = MERGE_DROP_SHOWN
    lines = ["[merge drops] this merge's result lacks lines a parent added since the merge base "
             "(a replacement made on purpose reads the same):"]
    entries = [(side, path, found) for side in ("ours", "theirs") for path, found in sorted(drops.get(side, {}).items())]
    for side, path, found in entries[:files_shown]:
        lines.append("  %s: %d of %d lines %s added are missing"
                     % (path, len(found["dropped"]), found["added"], ours if side == "ours" else theirs))
        lines.extend("    - " + line.strip()[:160] for line in found["dropped"][:lines_shown])
        if len(found["dropped"]) > lines_shown:
            lines.append("    ... %d more" % (len(found["dropped"]) - lines_shown))
    if len(entries) > files_shown:
        lines.append("  ... %d more files" % (len(entries) - files_shown))
    if unchecked:
        lines.append("  not checked (past the read budget or a failed read): " + ", ".join(unchecked[:files_shown])
                     + (" ... %d more" % (len(unchecked) - files_shown) if len(unchecked) > files_shown else ""))
    return "\n".join(lines)


def harness_verdict(sub, rest, cwd, inline_env=None, git_env=None, session_id=None, refs_pending=()):
    """Quick commit checks and an advisory, budgeted run of authored harness paths' bound proofs."""
    if bypass_declared(inline_env, BYPASS_VAR):
        return None

    if _STATE_IMPORT_ERROR:
        return ("BLOCKED commit — harness guard state unavailable (%s). Repair "
                "hooks/_hook_state.py; for that repair commit prefix `%s=1`."
                % (_STATE_IMPORT_ERROR, BYPASS_VAR))

    if _SCOPE_IMPORT_ERROR:
        return ("BLOCKED commit-like git invocation — merge-scope state unavailable (%s). Repair "
                "hooks/_commit_scope.py; for that repair commit prefix `%s=1`."
                % (_SCOPE_IMPORT_ERROR, BYPASS_VAR))

    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        if cwd and "$" not in cwd and not os.path.isdir(cwd):
            # `cd <dir>` into a worktree an earlier step of this command creates: the hook reads the command
            # before that step runs. A bare merge, cherry-pick or revert re-commits content judged where it
            # was committed, so only a commit that publishes the index is denied.
            if not publishes_index(sub, rest):
                return None
            return ("BLOCKED harness commit — the directory `%s` does not exist yet when the hook reads the "
                    "command, so the commit scope cannot be verified; run the step that creates it in its own "
                    "call, then commit." % cwd)
        return ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot "
                "verify commit scope." + unexpanded_hint(cwd))
    repo_root = root.strip()

    if publishes_index(sub, rest):
        paths, failed_cmd = staged_paths(rest, cwd, git_env)
    else:
        paths, failed_cmd = incoming_paths(sub, rest, cwd, git_env)
    if (paths is None and not publishes_index(sub, rest) and ref_names_created(refs_pending, rest)
            and not ref_resolves(rest, cwd, git_env)):
        # An earlier fetch / branch of this command creates the ref in this repo; it does not exist
        # yet, and a bare merge is never denied on content below.
        return None
    if paths is None:
        return ("BLOCKED harness commit — `git %s` failed; cannot verify commit scope." % failed_cmd
                + unexpanded_hint(failed_cmd))

    return None


TRAILER_RE = re.compile(r"claude\.ai/code/session_\w+")


def commit_msg_hook(message_path, environ=None, cwd=None):
    """Deny text for the commit git is about to write, or None: `base_verdict` and `revert_verdict` re-run inside git's commit-msg
    hook (`.claude/githooks/commit-msg`), because the PreToolUse check ran before the commit hooks and a peer's
    commit can land in between. A pathspec (`next-index-*`) or `-a` (`index.lock`) commit takes the working tree,
    and a merge has its own guard, so those pass. The message's own Claude-Session trailer is the session whose
    commits it may revert."""
    environ = os.environ if environ is None else environ
    cwd = cwd or os.getcwd()
    index = environ.get("GIT_INDEX_FILE") or ""
    name = os.path.basename(index)
    if name.startswith("next-index") or name == "index.lock":
        return None
    git_dir = run_git(["rev-parse", "--git-dir"], cwd)
    if git_dir and os.path.exists(os.path.join(cwd, git_dir.strip(), "MERGE_HEAD")):
        return None
    try:
        with open(message_path, encoding="utf-8", errors="replace") as fh:
            trailer = TRAILER_RE.search(fh.read())
    except OSError:
        trailer = None
    git_env = {"GIT_INDEX_FILE": os.path.abspath(os.path.join(cwd, index))} if index else None
    return (base_verdict(cwd, git_env)
            or revert_verdict([], cwd, git_env, own_trailer=trailer.group(0) if trailer else ""))


def _opaque_discard(command, nested=False):
    """Return a floor verdict for opaque git calls; they cannot borrow cwd/ownership exceptions."""
    import shlex
    if command_substitutions is None:
        return ('BLOCKED git call (CLAUDE.core.md §Shell Discipline): the nested-command parser '
                'failed to import (%s). Repair sidecar_argv.py before running git.' % _OPAQUE_IMPORT_ERROR)
    plain, bodies = command_substitutions(command)
    for body in bodies:
        why = _opaque_discard(body, nested=True)
        if why:
            return why
    for raw in shell_segments(plain):
        tokens, index = executable_index(raw)
        if index >= len(tokens):
            continue
        head = os.path.basename(tokens[index].replace('\\', '/')).lower()
        wrapped = index > 0 and any(t in ('env', 'sudo', 'xargs') for t in tokens[:index])
        if head in ('sudo', 'xargs'):
            values = ({'-u', '-g', '-h', '-p', '-C', '--user', '--group', '--host', '--prompt', '--chdir'}
                      if head == 'sudo' else {'-I', '-i', '-L', '-l', '-n', '-P', '-a', '-d', '-E', '-e',
                                             '--replace', '--max-lines', '--max-args', '--max-procs',
                                             '--arg-file', '--delimiter', '--eof'})
            index += 1
            while index < len(tokens) and tokens[index].startswith('-'):
                option = tokens[index]
                index += 2 if option in values else 1
                if option == '--':
                    break
            tokens, index = executable_index(tokens[index:])
            wrapped = True
        if index >= len(tokens):
            continue
        body = literal_launcher_body(tokens, index)
        if body is not None:
            why = _opaque_discard(body.body, nested=True)
            if why:
                return why
            continue
        parsed = git_invocation(shlex.join(tokens[index:]))
        if parsed and (nested or wrapped):
            why = verdict(parsed[0], git_env=parsed[2])
            if why:
                return why
    return None


def main() -> None:
    if sys.argv[1:2] == ["--commit-msg"]:
        try:
            why = commit_msg_hook(sys.argv[2] if len(sys.argv) > 2 else "")
        except Exception as exc:  # a floor guard: a fault refuses the commit
            print("BLOCKED commit: the commit-msg peer-revert guard crashed (%s: %s). Repair "
                  ".claude/hooks/git_guardrails.py commit_msg_hook until .claude/tests/test_git_commit_msg_hook.py "
                  "passes, then commit again." % (type(exc).__name__, exc), file=sys.stderr)
            sys.exit(1)
        if why:
            print(why, file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    ADVISORIES.clear()
    spent_from = spent_mark()
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = executable_text((input_data.get("tool_input") or {}).get("command") or "")
    if 'git' not in command:
        sys.exit(0)
    opaque = _opaque_discard(command)
    if opaque:
        print(opaque, file=sys.stderr)
        sys.exit(2)

    cwd = input_data.get("cwd") or "."
    born = set()  # repos this same command creates with `git init`: no harness history to stamp
    index_write = None  # the latest git index write seen so far in this command
    ref_makers = {}  # repo -> ref names an earlier invocation of this command creates there

    for call in git_invocations(command, cwd):
        args = [call.sub] + call.rest
        target, inline_env, git_env = call.cwd, call.inline_env, call.git_env

        def expand(token):
            return expand_literal(token, call.assignments) or token
        key = os.path.normcase(os.path.abspath(target)) if target else None
        if args and args[0] == "init" and target:
            named, takes_value = [], False
            for a in args[1:]:
                if takes_value:
                    takes_value = False
                elif a in ("-b", "--initial-branch", "--template", "--separate-git-dir", "--object-format", "--ref-format"):
                    takes_value = True
                elif not a.startswith("-"):
                    named.append(a)
            made = resolve_cd(target, expand(named[0])) if named else target
            top = run_git(["rev-parse", "--show-toplevel"], made, git_env)
            made_key = os.path.normcase(os.path.abspath(made))
            if top is None or os.path.normcase(os.path.abspath(top.strip())) != made_key:
                born.add(made_key)  # a new repo; `git init` on an existing root changes nothing
            continue
        judged_args = [expand(arg) for arg in args] if args and args[0] in ('checkout', 'restore') else args
        message = verdict(judged_args, target, git_env, input_data.get('transcript_path'))
        if (not message and target is None and args
                and (args[0] in ("commit", "merge", "cherry-pick", "revert") or publishes_index and publishes_index(args[0], args[1:]))):
            # An unexpandable `-C` or a wrapped call: the repo cannot be proved, so the commit scope cannot be either.
            message = ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot verify commit scope."
                       + unexpanded_hint(command))
        if (index_write and not message and key not in born and publishes_index and args
                and publishes_index(args[0], args[1:])):
            message = SAME_COMMAND_WRITE % (index_write, index_write)
        if writes_index and writes_index(args):
            index_write = "git " + args[0]
        if (not message and args and (args[0] in ("commit", "merge", "cherry-pick", "revert")
                                      or publishes_index and publishes_index(args[0], args[1:]))
                and key not in born):
            git_env, problem = resolve_index(git_env, target)
            if problem:
                message = "BLOCKED commit (memory gotcha_concurrent_session_hazards #16) — %s." % problem
            elif args[0] == "commit":
                message = (base_verdict(target, git_env)
                           or amend_verdict(args[1:], target, git_env, input_data.get("session_id"))
                           or revert_verdict(args[1:], target, git_env, input_data.get("session_id")))
            if not message:
                message = harness_verdict(args[0], args[1:], target, inline_env, git_env,
                                          input_data.get("session_id"), ref_makers.get(key, ()))
            if not message and publishes_index is not None:
                try:
                    dropped = merge_drop_advisory(args[0], args[1:], target, git_env)
                except Exception as exc:
                    dropped = "[merge drops] the check could not run (%s: %s); this merge's dropped lines are unchecked." % (
                        type(exc).__name__, exc)
                if dropped:
                    ADVISORIES.append(dropped)
        if args and args[0] in REF_MAKERS:
            ref_makers.setdefault(key, set()).update(refs_created(args[0], args[1:]))
        if message:
            unspend(spent_from)  # a denied call prints no advisory: the once-keys built for it go back
            print(message, file=sys.stderr)
            sys.exit(2)

    if ADVISORIES:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": "\n\n".join(ADVISORIES)}}))
    sys.exit(0)


# A floor guard: pre_bash_dispatch blocks the call when it crashes, since a crash here must never
# read as allow.
FAIL_CLOSED = True

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        sys.stderr.write("git_guardrails is a floor guard and crashed, so the call is blocked:\n"
                         + traceback.format_exc())
        sys.exit(2)
