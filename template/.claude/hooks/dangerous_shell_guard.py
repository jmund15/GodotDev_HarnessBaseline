#!/usr/bin/env python3
"""The destructive-command check of the loss floor (class floor, T1). `pre_bash_dispatch.py`, behind the entry
`pre_bash.py`, runs it for every Bash and PowerShell call; a fault denies the call.

Blocks, each judged on the resolved target and never on the command's wording:
- Recursive/forced deletes and directory removal (rm -r/-rf, del /s, rd /s, rmdir /s, PowerShell Remove-Item
  -Recurse and its aliases) and drive format commands. Allowed: a target that is a regenerable cache inside the
  project, a `.agents`, `bin` or `obj` folder of the project or a worktree where git sees no tracked or unignored
  file under the target (`_is_ignored_output`), a path below the system temp directory (any repository in it
  included, never the protected checkout or a worktree registered to it) or below `~/.claude/plugins/cache/` holding no git repository, this session's own
  `.claude/scratch/<session id>` output, a pushed regenerable clone, a scratch folder (the project's or a registered
  worktree's) holding no git repository and no registered worktree, a folder a proof planted under `.claude/tmp`, a retired worktree (`_delete_rule`: nothing
  outside the regenerable entries, every commit pushed or landed, no open PR), a `NAME=$(mktemp -d)`
  variable the same command owns, or a folder a flagless `mkdir` of this session created, holding no git
  repository. A chained second delete, a glob in a directory part, `~`, `%`, an unresolvable target, a target a
  live peer session works in, or another session's id folder disqualifies it. A forced `rm` whose every target is
  a literal path proven absent inside `.claude/scratch/` removes nothing and is rewritten to `true`.
  `find <roots> -delete` is judged as `rm -rf <roots>`.
- Git discards (`verdict`): `reset --hard` outside a clean worktree this session created, `clean -f`, a checkout or
  restore that overwrites an edit git holds nowhere else (outside this session's own worktrees), force-push,
  `branch -D` of a branch no other ref holds (and `branch -M/-C/-f` onto such a branch), `switch -f`,
  `push --delete`/`:ref`/`--mirror`, `clean` without `-f` where `clean.requireForce=false`, `update-ref -d` of a
  ref no other ref holds, `stash drop` of an entry no ref holds, `stash clear`, `reflog expire`/`delete`,
  `gc --prune` and `filter-*`. A call inside a shell launcher body borrows no worktree ownership.
- The harness push close (`harness_push_verdict`, v1's floor guard): where the project declares no
  `adaptation.json` `main_check`, an outgoing harness commit must be the one a green close proved, with its base and
  paths; a push to a `land_prefix` branch passes, and a push source the guard cannot resolve is denied.
- Peer losses at commit: a private index over a moved stage base (`stage_record_loss`), an index whose blobs
  revert a peer's newer commit (`stale_loss`), an amend of a HEAD this session did not make (`amend_loss`), an
  index the floor cannot read (#16), a publish whose repository cannot be proved (`UNPROVED_TARGET`), and a
  commit an index write precedes in the same command where git's commit-msg hook cannot re-check it with every
  check the chain ran (`SAME_COMMAND_WRITE`, `_unhooked_publish`).

Every command is read through `_shell_lex` (a quoted span is one word, a heredoc body is data unless a shell reads it,
each body a launcher or substitution runs is judged too); `git` parsing goes through `_git_commit.py`.

Second caller: `.claude/githooks/commit-msg` runs `pre_bash_dispatch.py --commit-msg <file>` (`commit_time`), which
calls `commit_msg_check` to re-run the stage-record and peer-revert checks against the index git is about to write,
then every other commit judge; exit 1 refuses with one line. `dangerous_shell_guard.py --commit-msg <file>` runs
`commit_msg_check` alone.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
from _shell_lex import (OPAQUE_WRAPPERS, POWERSHELLS, SHELLS, PowerShellText, base, cmd_switches, command_index,  # noqa: E402
                        command_words, executable_spans, executable_text, lex, options, ps_param, walk)
from _delete_rule import (ANY_SESSION_ID, empty_leftover, holds_only_ignored_output,  # noqa: E402
                          is_regenerable_clone, is_plant_exhaust, is_proof_plant, is_retired_worktree,
                          lossless_orphan_worktree, names_ignored_output, project_data, regenerable_segments,
                          worktree_unstored)
from _own_worktree import session_created_worktree  # noqa: E402
import _git_commit  # noqa: E402
if _git_commit.lex is None:
    raise ImportError("_shell_lex failed to import: %s" % _git_commit.LEXER_ERROR)
from _git_commit import (literal_assignments, expand_literal, resolve_cd,  # noqa: E402
                         git_invocations, run_git, resolve_index, in_project, parse_commit_args, _pathspec_file,
                         commit_options, GLOBAL_FLAGS_WITH_VALUE)
from _commit_scope import (publication, publishes_index, stage_record_base, stage_record_path,  # noqa: E402
                           writes_index)
from _git_commit import bypass_declared, ref_names_created, ref_resolves, refs_created, unexpanded_hint  # noqa: E402
from _checkout_root import worktree_chain  # noqa: E402
import _git_run  # noqa: E402
from _transcript_calls import Call, Result, session_events  # noqa: E402


def _forces_removal(args: list[str]) -> bool:
    """True when a flag stops the delete reporting an absent operand, so deleting nothing exits 0
    (`rm -f`, `-rf`, `-fr`, `--force`). Only then does `true` stand in for the segment."""
    return any(a.lower() == '--force' or (re.fullmatch(r'-[A-Za-z]+', a) and 'f' in a.lower())
               for a in args)


# GNU rm's long options, so a typed prefix (`--rec`) resolves as rm resolves it.
_RM_LONG = ("force", "interactive", "one-file-system", "no-preserve-root", "preserve-root", "recursive", "dir",
            "verbose", "help", "version")


def _rm_flags(args):
    return options(args, long=_RM_LONG).flags


def _cmd_tree(args):
    """A cmd.exe delete of a tree or without confirmation: a `/s` or `/q` switch."""
    return any(sw[:1] in ("s", "q") for sw in cmd_switches(args))


@dataclass(frozen=True)
class Delete:
    """One delete the floor blocks, judged from the parsed words of its command (Bash and PowerShell alike; PowerShell
    text also reaches Bash through `powershell -Command "..."`). `names` are the program names it runs under
    (`_shell_lex.base`); `runs(args)` is True when the words after the name make it this delete; `message` is its deny
    text. A `tree` delete passes when every target it names is proved to lose nothing (`_deletable`), and its deny
    names what the floor allows; `judged` lists the names, as the shell runs them (`base`), whose operands are those
    targets (none: the floor cannot read its operands, so it always denies); `absent_ok(args)` is True when a run
    that removes nothing exits 0, so a delete whose every target is absent from scratch is rewritten to `true`. A new
    delete is one row and its table test."""
    key: str
    names: tuple
    runs: object
    message: str
    judged: tuple = ()
    absent_ok: object = None
    tree: bool = True
    operands: object = None   # `operands(args)` -> the targets among the words after the name; None: every non-dash word


def _find_roots(args):
    """The start paths of a `find` call: the words after any leading `-H`/`-L`/`-P` up to the first expression word
    (`-name`, `(`, `!`). None named means find starts at `.`, which no row judges, so the delete is denied."""
    i = 0
    while i < len(args) and args[i] in ("-H", "-L", "-P"):
        i += 1
    roots = []
    for word in args[i:]:
        if word.startswith(("-", "(", "!", "\\(")):
            break
        roots.append(word)
    return roots


# Each delete, in the order its deny is chosen; a word two rows' names share is the first matching row's. Plain
# `rm -f <file>` is a single-file delete and is not blocked; `git rm` is version-controlled and recoverable.
DELETES = (
    Delete("del", ("del", "erase"), _cmd_tree, "BLOCKED: Dangerous recursive delete (del /s or /q)",
           judged=("del", "erase")),
    Delete("rd", ("rd",), _cmd_tree, "BLOCKED: Dangerous recursive directory removal (rd /s)"),
    Delete("rmdir", ("rmdir",), _cmd_tree, "BLOCKED: Dangerous recursive directory removal (rmdir /s)"),
    Delete("format", ("format",), lambda args: any(re.match(r"[A-Za-z]:", a) for a in args),
           "BLOCKED: Format drive command detected", tree=False),
    Delete("rm", ("rm",), lambda args: bool(_rm_flags(args) & {"r", "R"}),
           "BLOCKED: Dangerous recursive delete (rm -r / -R / -rf)", judged=("rm",), absent_ok=_forces_removal),
    Delete("find", ("find",), lambda args: "-delete" in args,
           "BLOCKED: Dangerous recursive delete (find -delete)", judged=("find",), operands=_find_roots),
    Delete("rm-long", ("rm",), lambda args: "recursive" in _rm_flags(args),
           "BLOCKED: Dangerous recursive delete (rm --recursive)", judged=("rm",), absent_ok=_forces_removal),
    # PowerShell's names for Remove-Item; `-Recurse` may be spelled by any prefix (`-r`), as PowerShell binds it.
    Delete("remove-item", ("remove-item", "ri", "del", "erase", "rd", "rmdir", "rm"),
           lambda args: any(ps_param(a, "recurse") for a in args),
           "BLOCKED: Dangerous recursive delete (Remove-Item -Recurse)",
           judged=("remove-item", "ri", "del", "erase", "rm")),
)


def _git_subcommand(words, k):
    """True when `words[k]` is the subcommand of a git call in the same segment (`git -C x rm`)."""
    for j in range(k - 1, -1, -1):
        if base(words[j]) == "git":
            i = j + 1
            while i < k and words[i].startswith("-"):
                i += 2 if words[i] in GLOBAL_FLAGS_WITH_VALUE else 1
            return i == k
    return False


# Programs that read their arguments as data and run none of them: a delete word among their arguments deletes
# nothing. git reads that way only in the subcommands named (`git submodule foreach` and `git bisect run` run theirs).
_DATA_READERS = frozenset(("rg", "grep", "egrep", "fgrep", "ag", "ack", "echo", "printf", "cat", "head", "tail",
                           "less", "more", "wc", "ls", "stat", "file", "sort", "uniq", "cut", "tr", "jq", "yq",
                           "diff", "cmp", "basename", "dirname", "realpath", "readlink", "mkdir", "touch", "cd",
                           "pwd", "which", "type", "findstr", "select-string", "sls", "write-host", "write-output"))
_GIT_READERS = frozenset(("log", "show", "diff", "grep", "status", "blame", "ls-files", "ls-tree", "rev-parse",
                          "rev-list", "cat-file", "describe", "shortlog"))


# Programs that run the text piped into them: what a data reader prints into one is code, not data.
_PIPE_RUNNERS = SHELLS | POWERSHELLS | frozenset(("eval", "xargs", "cmd", "iex", "invoke-expression"))


def _pipes_into_runner(seg, following):
    """True when `seg`'s output is piped (`|`, `|&`) into the `_PIPE_RUNNERS` program that `following` runs."""
    if seg.op not in ("|", "|&") or following is None:
        return False
    nxt = [w.value for w in following.words]
    at = command_index(nxt, through=tuple(OPAQUE_WRAPPERS))
    return at < len(nxt) and base(nxt[at]) in _PIPE_RUNNERS


def _read_as_data(words, k, piped=False):
    """True when `words[k]` is an argument of a program that reads its arguments as data (`_DATA_READERS`, a git read
    subcommand), past any wrapper (`sudo`, `nice`, `env`, ...): it runs nothing there, unless its output is piped
    into a runner (`piped`), which runs it."""
    i = command_index(words, through=tuple(OPAQUE_WRAPPERS))
    if piped or i >= k:
        return False
    name = base(words[i])
    if name == "git":
        j = i + 1
        while j < k and words[j].startswith("-"):
            j += 2 if words[j] in GLOBAL_FLAGS_WITH_VALUE else 1
        return j < k and words[j] in _GIT_READERS
    return name in _DATA_READERS


def _delete_row(words, k, piped=False):
    """The `DELETES` row of the delete `words[k]` runs with the words after it in its segment, or None. Every word
    is a candidate, not only the command position: a delete behind `sudo`, `xargs`, `find -exec` or any other
    runner is still judged, and a quoted argument (`grep "rm -rf"`) is one word, never a delete. A git subcommand
    (`git rm`) is git's own, and version-controlled; an argument of a program that reads its arguments as data
    (`rg del /src`, `git log ... format C:/x`, `echo rm -rf`) runs nothing."""
    name, args = base(words[k]), words[k + 1:]
    row = next((row for row in DELETES if name in row.names and row.runs(args)), None)
    return None if row is None or _git_subcommand(words, k) or _read_as_data(words, k, piped) else row


def _deletes(command):
    """[(row, depth, segment, index)] for each delete in `command` and every body it runs (`_shell_lex.walk`)."""
    found = []
    for _kind, _text, segments, depth in walk(command):
        for n, seg in enumerate(segments):
            values = [w.value for w in seg.words]
            piped = _pipes_into_runner(seg, segments[n + 1] if n + 1 < len(segments) else None)
            found += [(row, depth, seg, k) for k in range(len(values)) for row in [_delete_row(values, k, piped)] if row]
    return found


# Regenerable caches a recursive delete may target without asking are `_delete_rule.regenerable_segments`:
# harness caches and logs, the semantic-search index, engine and test output, Python bytecode caches. Matched by PATH SEGMENT, never substring:
# the old `\.claude[\\/](?:\.cache|logs)\b` regex passed `.claude/logs-old` and
# `.claude/.cache/../..` (the repo root). Evidence folders (`.claude/scratch`) and checkouts
# (`.claude/worktrees`) are gitignored but are NOT caches, so they stay blocked unless
# `_delete_rule` proves the target loses nothing.
_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
_CACHE_HINT = " Allowed: caches, temp dirs, clean pushed scratch clones, retired worktrees (`orchestration` reference `worktrees.md`), scratch folders holding no git repository, folders this session made with a flagless mkdir."


def _project_root() -> str:
    """The checkout this guard protects: CLAUDE_PROJECT_DIR, else the repository holding this hook."""
    here = os.path.abspath(__file__)
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _inside_root(token: str, cwd: str | None) -> bool:
    """True when `token`, resolved against `cwd` (else the project root) with links followed, lies
    inside the project root. A cache segment in another checkout or the user profile is not ours."""
    root = os.path.normcase(os.path.realpath(_project_root()))
    base = cwd or _project_root()
    joined = token if (os.path.isabs(token) or re.match(r"^[A-Za-z]:", token)) else os.path.join(base, token)
    target = os.path.normcase(os.path.realpath(joined))
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:  # different drives
        return False


def _is_cache_path(token: str, cwd: str | None = None) -> bool:
    """True when `token` is a literal path at or under an allowlisted cache segment and resolves
    inside the project root."""
    if _UNSAFE_PATH_CHARS.search(token):
        return False
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return False
    return regenerable_segments(parts) and _inside_root(token, cwd)


def _registered_worktrees() -> list[str]:
    """Normalized paths of the project's registered worktrees; an unreadable list holds the project root,
    so every scratch target counts as holding one and the delete stays denied."""
    root = _project_root()
    return _git_commit.cached(('worktrees', root), lambda: _list_registered_worktrees(root))


def _list_registered_worktrees(root: str) -> list[str]:
    try:
        out = _git_run.run(['-C', root, 'worktree', 'list', '--porcelain'], timeout=20)
    except OSError:
        return [_norm(root)]
    if out.returncode:
        return [_norm(root)]
    return [_norm(line[len('worktree '):]) for line in out.text.splitlines() if line.startswith('worktree ')]


_ANY_SESSION_ID = ANY_SESSION_ID


def _peer_session_folder(target: str, transcript_path: str | None) -> str | None:
    """The first name, in `target`'s path below scratch or among its direct children, that carries a session id
    other than this session's: a folder another session's output lives in. This session's own id counts only as
    a whole name, so a prefixed or extended name is a peer's."""
    sid = os.path.splitext(os.path.basename(transcript_path or ""))[0].lower()
    below = os.path.relpath(_norm(target), _norm(os.path.join(_project_root(), '.claude', 'scratch')))
    names = [p for p in below.replace("\\", "/").split("/") if p]
    try:
        names += os.listdir(target)
    except OSError:
        pass
    return next((n for n in names if _ANY_SESSION_ID.search(n) and n.lower() != sid), None)


def _peer_working_in(target: str, transcript_path: str | None) -> str | None:
    """The name of a session in the runtime's local registry, other than this one, whose recorded working
    directory is `target` or lies inside it: deleting `target` breaks that session. None when the registry
    cannot be read, so an unreadable registry observes nothing."""
    try:
        from peer_message_guard import live_sessions
        sessions = live_sessions()
    except Exception:
        return None
    sid = os.path.splitext(os.path.basename(transcript_path or ""))[0].lower()
    base = _norm(target)
    for row in sessions:
        if not row["cwd"] or row["sessionId"].lower() == sid:
            continue
        here = _norm(row["cwd"])
        if here == base or _strictly_under(here, base):
            return row["name"]
    return None


def _removable_scratch_entry(token: str, target: str, cwd: str | None, transcript_path: str | None = None,
                             reasons: list[str] | None = None) -> bool:
    """A scratch entry that is no regenerable clone (a copy, a study's output) loses nothing git holds when it
    contains no git repository and no registered worktree: a repository there may hold commits on no remote.
    A folder another session's id names is that session's output, not this one's to delete."""
    peer = _peer_session_folder(target, transcript_path)
    if peer:
        if reasons is not None:
            reasons.append('it is or holds another session\'s folder (%s)' % peer)
        return False
    held = [wt for wt in _registered_worktrees() if wt == _norm(target) or _strictly_under(wt, _norm(target))]
    if held:
        if reasons is not None:
            reasons.append('a registered worktree lies inside it (%s): retire that worktree first' % held[0])
        return False
    if _holds_git(target):
        if reasons is not None:
            clone: list[str] = []
            is_regenerable_clone(token, cwd or _project_root(), _project_root(), clone)
            reasons.append('it holds a git repository whose work is not shown to be saved (%s): push its commits to a remote, then '
                           'delete it' % (clone[0] if clone else 'not a clean pushed clone'))
        return False
    return True


def _scratch_bases() -> list[str]:
    """The checkouts whose `.claude/scratch` this delete may judge: the project root and every worktree registered
    to it, whichever checkout the call runs in (a session retiring a lane's scratch from the root, or a delegate
    removing its own). An unregistered checkout adds nothing. A live session working in the target, another
    session's id folder and a repository inside it are judged per target, so a base widens no loss."""
    return [_project_root(), *_registered_worktrees()]


def _under_a_scratch_folder(path: str) -> bool:
    """`path` sits below some `.claude/scratch`: the only place a base `_scratch_bases` names can match, so a
    target elsewhere skips the git call that finds the cwd's toplevel."""
    parts = path.replace('\\', '/').split('/')
    return any(parts[i:i + 2] == ['.claude', 'scratch'] and i + 2 < len(parts) for i in range(len(parts) - 2))


def _is_regenerable_target(token: str, cwd: str | None, transcript_path: str | None = None,
                           reasons: list[str] | None = None) -> bool:
    """A pushed clone, a lossless orphaned worktree directory of this repository wherever it sits (needs
    no approval: nothing in it is unheld by git or a non-regenerable ignored file), an empty leftover directory under
    scratch (no file, no `.git`), a scratch entry holding no git repository or worktree, or a retired worktree
    (`_delete_rule`: nothing outside the regenerable entries, every commit pushed or landed; a worktree this session
    created also lets its ignored files and its stored edits go); optionally explain failure."""
    root = _project_root()
    target = resolve_cd(cwd or root, token)
    peer = _peer_working_in(target, transcript_path)
    if peer:
        if reasons is not None:
            reasons.append('live session %s works in it, so deleting it breaks that session: remove it after '
                           'that session ends' % peer)
        return False
    if is_regenerable_clone(token, cwd or root, root):
        return True
    if _under_a_scratch_folder(_norm(target)) and any(
            _strictly_under(_norm(target), _norm(os.path.join(base, '.claude', 'scratch')))
            for base in _scratch_bases()):
        if empty_leftover(_norm(target)):
            return True
        if _UNSAFE_PATH_CHARS.search(token):
            if reasons is not None:
                reasons.append('it is not a literal path, so it can match another session\'s folder')
            return False
        return _removable_scratch_entry(token, target, cwd, transcript_path, reasons)
    orphan_loss: list[str] = []
    if lossless_orphan_worktree(token, cwd or root, root, orphan_loss):
        return True
    own = session_created_worktree(target, transcript_path)
    if reasons is not None and not own:
        reasons.extend(orphan_loss)
    return is_retired_worktree(token, cwd or root, root, reasons=reasons, own=own)


_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_own_session_scratch(token: str, cwd: str | None, transcript_path: str | None) -> bool:
    """A literal path under `.claude/scratch/` with a segment equal to this session's full id.

    The id comes from the transcript file's name, so only this session's own scratch output
    qualifies; a peer's id, a substring of the id, or a path outside scratch does not."""
    sid = os.path.splitext(os.path.basename(transcript_path or ""))[0]
    if not _SESSION_ID.match(sid) or _UNSAFE_PATH_CHARS.search(token):
        return False
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts or sid not in parts:
        return False
    rel = os.path.relpath(resolve_cd(cwd or _project_root(), token), _project_root()).replace("\\", "/")
    return rel.startswith(".claude/scratch/") and _inside_root(token, cwd)


def _deletable_by_fact(target: str, here: str | None = None, transcript_path: str | None = None) -> bool:
    """Every per-target fact that makes a recursive delete need no owner answer: a regenerable cache inside the
    project, this session's own scratch output, a target holding nothing git cannot yield, or a folder a flagless
    mkdir of this session created. One owner for the fact list, so a new class is one edit and both the token-level
    and resolved evaluators admit it. The cheapest fact decides first; the transcript read comes last."""
    return (_is_cache_path(target, here) or _is_own_session_scratch(target, here, transcript_path)
            or (here is not None and not _UNSAFE_PATH_CHARS.search(target)
                and _is_ignored_output(_norm(resolve_cd(here, target)), transcript_path))
            or _is_regenerable_target(target, here, transcript_path)
            or (here is not None and _is_regenerable_resolved(target, here))
            or _session_made_dir(target, here, transcript_path))


def _absent_scratch_target(value: str | None, here: str | None) -> bool:
    """True when `value` names a literal path strictly inside this project's `.claude/scratch/` that
    does not exist, with no link escaping scratch: `realpath` resolves every existing ancestor, so a
    symlinked or junctioned parent that leaves scratch fails the containment test."""
    if not value or here is None or _UNSAFE_PATH_CHARS.search(value):
        return False
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return False
    literal = resolve_cd(here, value)
    if os.path.lexists(literal):        # a file, a directory or a dangling link: real work
        return False
    return _strictly_under(_norm(literal), _norm(os.path.join(_project_root(), ".claude", "scratch")))


def rewrite_absent_forced_deletes(command: str, cwd: str | None = None) -> str | None:
    """`command` with every forced tree delete whose targets are all proven-absent paths under
    this project's `.claude/scratch/` replaced by `true`, or None when any delete is not provable.

    `rm -rf <absent>` exits 0 and removes nothing, so the setup idiom `rm -rf target; mkdir target`
    needs no owner approval for an absent target. The segment is rewritten rather than approved: a
    bare allow would also cover whatever appears at that path between this check and the run. A
    command with any other recursive delete -- an existing path, a glob, an unresolved variable, a
    `..`, a delete whose row has no `absent_ok`, or a segment the parser cannot isolate -- returns None and keeps its
    normal denial.
    """
    spans = list(executable_spans(command))
    if not spans:
        return None
    assignments = literal_assignments(command)
    here = cwd or _project_root()
    elisions, found = [], False
    for start, end, segment in spans:
        delete_shape = any(row.tree for row, *_ in _deletes(segment))
        parsed = command_words(segment)
        if parsed is None:
            if delete_shape:
                return None
            continue
        words = [w.value for w in parsed[0]]
        if not delete_shape:
            if words and words[0] == "cd" and len(words) == 2:
                moved = expand_literal(words[1], assignments)
                here = (resolve_cd(here, moved)
                        if here is not None and moved and not _GLOB_CHARS.search(moved) else None)
            continue
        row = _delete_row(words, 0)
        if (here is None or row is None or row.absent_ok is None or base(words[0]) not in row.judged
                or not row.absent_ok(words[1:])):
            return None
        targets = [t for t in words[1:] if not t.startswith("-")]
        if not targets or not all(_absent_scratch_target(expand_literal(t, assignments), here)
                                  for t in targets):
            return None
        elisions.append((start, end))
        found = True
    if not found:
        return None
    out, cursor = [], 0
    for start, end in elisions:
        out.append(command[cursor:start])
        out.append("true")
        cursor = end
    out.append(command[cursor:])
    return "".join(out)


# A4: `NAME=$(mktemp -d)` (or `-t <template>` with no `/` in the template) is ownership evidence a
# literal path never carries; a delete target that is exactly `$NAME` of such a variable is the command's own.
_VAR_TOKEN_RE = re.compile(r'^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$')
# The value must END at the substitution: a suffix (`$(mktemp -d)/..`, `/*`) points outside the directory.
_MKTEMP_ASSIGN_VALUE_RE = re.compile(r'\$\(\s*mktemp\s+-d(?:\s+-t\s+[^\s)/]+)?\s*\)(?=[\s;&|]|$)')


def _is_mktemp_owned(name: str, raw: str) -> bool:
    """True when `name` is assigned exactly once anywhere in `raw`, and that one assignment's
    value starts `$(mktemp -d)` or `$(mktemp -d -t <template>)` with no `/` in the template. A
    literal directory name, however mktemp-shaped, is never ownership evidence."""
    # Every bare occurrence of the name (not a `$NAME` / `${NAME}` expansion) must be that one
    # assignment: `read NAME`, `for NAME in`, `export NAME` and `printf -v NAME` reassign it too.
    # A name right after `-` is a flag (`mktemp -d`), never an assignment.
    bare_re = re.compile(r'(?<![A-Za-z0-9_$-])(?<!\$\{)' + re.escape(name) + r'(?![A-Za-z0-9_])')
    matches = list(bare_re.finditer(raw))
    if len(matches) != 1 or raw[matches[0].end():matches[0].end() + 1] != '=':
        return False
    tail = raw[matches[0].end() + 1:]
    return _MKTEMP_ASSIGN_VALUE_RE.match(tail) is not None


# Resolved-target allow: each delete segment's targets resolve through literal paths, variables the
# same command assigns once, and the tracked `cd`; every one must land strictly below the system temp
# directory, in a named subdirectory of `.claude/scratch`, or in a regenerable cache of this checkout
# or one of its `.claude/worktrees/*` checkouts. The classifier still judges what passes here.
_GLOB_CHARS = re.compile(r'[*?\[\]]')


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _strictly_under(path: str, base: str) -> bool:
    try:
        return path != base and os.path.commonpath([base, path]) == base
    except ValueError:  # different drives
        return False


def _checkout_split(path: str) -> tuple[str, list[str]] | None:
    """The checkout holding `path` (the primary root, or the `.claude/worktrees/<name>` checkout nested inside it,
    KFM #42) and the path's parts below it. None outside the repo."""
    root = _norm(_project_root())
    if not _strictly_under(path, root):
        return None
    parts = os.path.relpath(path, root).replace("\\", "/").split("/")
    if parts[:2] == [".claude", "worktrees"]:
        return (os.path.join(root, *parts[:3]), parts[3:]) if len(parts) > 3 else None
    return root, parts


def _checkout_rel_parts(path: str) -> list[str] | None:
    """Path parts relative to the checkout holding `path`; None outside the repo."""
    split = _checkout_split(path)
    return split[1] if split else None


def _is_ignored_output(path: str, transcript_path: str | None) -> bool:
    """`path` (absolute, normalized) is, or lies in, a `bin`, `obj` or `.agents` folder of this project or one of its
    worktrees that git observes as ignored output (`holds_only_ignored_output`), holding no repository and no live
    peer's working directory, reached through no link."""
    split = _checkout_split(path)
    if split is None:
        return False
    root, parts = split
    if not names_ignored_output(parts):
        return False
    real_root = os.path.realpath(root)
    if os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.join(real_root, *parts)):
        return False
    if _peer_working_in(path, transcript_path) or _holds_git(path):
        return False
    repo, inside = root, parts
    for i in range(len(parts) - 1):  # a submodule or clone holds its own ignore rules and index: git in `root` is blind there
        if os.path.exists(os.path.join(root, *parts[:i + 1], ".git")):
            repo, inside = os.path.join(root, *parts[:i + 1]), parts[i + 1:]
    return holds_only_ignored_output(Path(repo), inside)


_GIT_WALK_CAP = 20000


def _holds_git(path: str) -> bool:
    """True when `path` is, or contains, a git repository or worktree. A walk that hits the cap
    reads as True: an unknown tree is never judged plain."""
    seen = 0
    for _dirpath, dirnames, filenames in os.walk(path):
        if ".git" in dirnames or ".git" in filenames:
            return True
        seen += len(dirnames) + len(filenames)
        if seen > _GIT_WALK_CAP:
            return True
    return False


def _is_regenerable_or_scratch(path: str) -> bool:
    """`path` (absolute, normalized) is safe to delete recursively without asking."""
    import tempfile
    project = _norm(_project_root())
    if _strictly_under(path, _norm(tempfile.gettempdir())) and not _strictly_under(path, project):
        # D-TT (owner, 2026-11-23): a temp tree goes with any repository in it, never the protected checkout or a
        # worktree registered to it, whole or in part; a path inside either keeps its rules wherever it sits.
        return not any(wt == path or _strictly_under(wt, path) or _strictly_under(path, wt)
                       for wt in [project] + _registered_worktrees())
    if _strictly_under(path, _norm(str(Path.home() / ".claude" / "plugins" / "cache"))):
        return not _holds_git(path)  # a plugin's download: the client fetches it again
    parts = _checkout_rel_parts(path)
    if not parts:
        return False
    lowered = [p.lower() for p in parts]
    if lowered[:2] == [".claude", "scratch"]:
        if len(parts) < 3:
            return False
        root = _project_root()
        return is_regenerable_clone(path, root, root)
    if lowered[:2] == [".claude", "tmp"]:
        if len(parts) < 3 or not is_proof_plant(parts[2]):
            return False
        plant = path
        for _ in range(len(parts) - 3):
            plant = os.path.dirname(plant)
        return is_plant_exhaust(Path(plant))
    return regenerable_segments(parts)


def _mkdir_targets(command: str, cwd: str | None) -> list[str]:
    """Normalized paths a flagless `mkdir` in `command` names, resolved through literal assignments and the
    tracked `cd`. A heredoc body is data, so a `mkdir` written there names nothing."""
    assignments = literal_assignments(executable_text(command), cwd)
    here, out = cwd, []
    for seg in lex(command):
        tokens = [w.text for w in seg.words]
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) == 2:
            moved = expand_literal(tokens[1], assignments)
            here = (resolve_cd(here, moved)
                    if here is not None and moved and not _GLOB_CHARS.search(moved) else None)
            continue
        if tokens[0] != "mkdir" or here is None or len(tokens) < 2 or any(t.startswith("-") for t in tokens[1:]):
            continue
        for token in tokens[1:]:
            value = expand_literal(token, assignments)
            if value and not _UNSAFE_PATH_CHARS.search(value):
                out.append(_norm(resolve_cd(here, value)))
    return out


def _session_made_dir(value: str, here: str, transcript_path: str | None) -> bool:
    """`value`, resolved against `here`, is a folder a flagless `mkdir` of this session created: a main-thread Bash
    call in the transcript names it, finished without error, and the folder's creation time lies inside that call.
    A flagless `mkdir` fails on an existing path, so a folder born inside the window is the call's own. The folder
    holds no git repository and no live peer session works in it. No creation time on this platform fails closed."""
    if not transcript_path or here is None:
        return False
    target = _norm(resolve_cd(here, value))
    try:
        born = os.stat(target).st_birthtime
    except (OSError, AttributeError):
        return False
    if not os.path.isdir(target) or _holds_git(target) or _peer_working_in(target, transcript_path):
        return False
    for events in session_events(transcript_path):
        pending = {}
        for event in events:
            if isinstance(event, Call):
                if (event.stamp is not None and "mkdir" in event.command
                        and target in _mkdir_targets(event.command, event.cwd)):
                    pending[event.id] = event.stamp
            elif isinstance(event, Result):
                start = pending.pop(event.id, None)
                if start is not None and event.stamp is not None and not event.is_error and start <= born <= event.stamp:
                    return True
    return False


def _is_regenerable_resolved(target: str, here: str) -> bool:
    if _UNSAFE_PATH_CHARS.search(target):
        return False
    return _is_regenerable_or_scratch(_norm(resolve_cd(here, target)))


def _delete_targets(raw: str, cwd: str | None):
    """Every target of every recursive-delete segment in `raw`, as (token, path, here): `path` is the literal the
    delete removes, expanded through assignments the command makes and resolved later against the tracked `cd`
    `here`, or its directory for a glob in the last part; None when it cannot be proved (an unexpanded value, `~`,
    `%`, a glob in a directory part, an unknown cwd). None for the whole command when a delete does not head its
    segment (inside `$(...)`, backticks, a launcher body, behind `sudo`/`xargs`), is typed as a name whose operands
    its row does not judge, or names no target, or when no segment deletes."""
    deletes = [d for d in _deletes(raw) if d[0].tree]
    if any(depth or k for _row, depth, _seg, k in deletes):
        return None
    heads = {seg.start: row for row, _depth, seg, _k in deletes}
    assignments = literal_assignments(executable_text(raw))
    here = cwd or _project_root()
    found = []
    for seg in lex(raw):
        tokens = [w.text for w in seg.words]
        if tokens and tokens[0] == "cd" and len(tokens) == 2:
            moved = expand_literal(tokens[1], assignments)
            here = (resolve_cd(here, moved)
                    if here is not None and moved and not _GLOB_CHARS.search(moved) else None)
            continue
        if seg.start not in heads:
            continue
        if base(tokens[0]) not in heads[seg.start].judged or seg.substitutions:
            return None
        row = heads[seg.start]
        targets = row.operands(tokens[1:]) if row.operands else [t for t in tokens[1:] if not t.startswith('-')]
        if not targets:
            return None
        for token in targets:
            value = expand_literal(token, assignments)
            path = None
            if value is not None and here is not None and not value.startswith('~') and '%' not in value:
                head, tail = os.path.split(value.replace("\\", "/").rstrip("/"))
                if not _GLOB_CHARS.search(head):
                    path = head if _GLOB_CHARS.search(tail) else value
            found.append((token, path, here))
    return found or None


def _deletable(raw: str, cwd: str | None, transcript_path: str | None) -> bool:
    """Every recursive-delete target in `raw` needs no owner answer: a `$NAME` of the command's own
    `NAME=$(mktemp -d)`, or a proved path `_deletable_by_fact` admits. One unproved target denies the command."""
    targets = _delete_targets(raw, cwd)
    if not targets:
        return False
    for token, path, here in targets:
        var = _VAR_TOKEN_RE.match(token)
        if var and _is_mktemp_owned(var.group(1), raw):
            continue
        if path is None or not _deletable_by_fact(path, here, transcript_path):
            return False
    return True


def _delete_reason(raw: str, cwd: str | None, transcript_path: str | None) -> str:
    """Why the first worktree, scratch or worktree-shaped (a `.git` file) target this delete names is not
    removable, or ''."""
    home = _norm(os.path.join(_project_root(), '.claude', 'worktrees'))
    scratch = _norm(os.path.join(_project_root(), '.claude', 'scratch'))
    for _token, value, here in _delete_targets(raw, cwd) or ():
        if not value or _UNSAFE_PATH_CHARS.search(value):
            continue
        target = _norm(resolve_cd(here, value))
        reasons = []
        if target == home or _strictly_under(target, home):
            if not _is_regenerable_target(value, here, transcript_path, reasons):
                return ' %s is not retired: %s.' % (value, '; '.join(reasons))
        elif _strictly_under(target, scratch):
            clone_reasons = []
            if not (is_regenerable_clone(value, here, _project_root(), clone_reasons)
                    or _is_regenerable_target(value, here, transcript_path, reasons)):
                return ' %s is not removable: %s; %s.' % (value, clone_reasons[0], reasons[0])
        elif os.path.isfile(os.path.join(target, '.git')) \
                and not _is_regenerable_target(value, here, transcript_path, reasons):
            return ' %s is not removable: %s.' % (value, '; '.join(dict.fromkeys(reasons)))
    return _unproved_reason(raw, cwd, transcript_path)


def _unproved_reason(raw: str, cwd: str | None, transcript_path: str | None) -> str:
    """The first delete target no exemption admits and no per-class reason above named, or '': an unresolvable
    path, else one in no exemption. A deny always names which exemption failed."""
    targets = _delete_targets(raw, cwd)
    if targets is None:
        return (' The guard cannot read this delete down to literal targets (it sits inside a loop, `$(...)`, a '
                'launcher body or behind sudo/xargs): run each delete as its own plain command.')
    for token, value, here in targets:
        var = _VAR_TOKEN_RE.match(token)
        if var and _is_mktemp_owned(var.group(1), raw):
            continue
        if value is None or _UNSAFE_PATH_CHARS.search(token):
            return (' %s is not a literal path the guard can resolve (a variable, `~`, `%%` or a glob): '
                    'spell the path out.' % token)
        if not _deletable_by_fact(value, here, transcript_path):
            return (' %s is outside every exemption: only this project\'s caches and scratch, retired worktrees '
                    'and the system temp directory go without the owner.' % token)
    return ''


def check_bash_command(command: str, cwd: str | None = None,
                       transcript_path: str | None = None) -> tuple[bool, str]:
    """Check bash command for dangerous patterns. Returns (blocked, message)."""
    with _git_commit.memoized():
        return _check_bash_command(command, cwd, transcript_path)


def _check_bash_command(command: str, cwd: str | None, transcript_path: str | None) -> tuple[bool, str]:
    found = {row.key for row, *_ in _deletes(command)}
    for row in DELETES:
        if row.key not in found:
            continue
        # A tree delete confined to regenerable harness scratch, or to a `NAME=$(mktemp -d)` directory this same
        # command owns, passes; a drive format never does.
        if row.tree and _deletable(command, cwd, transcript_path):
            continue
        message = row.message + " (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first)."
        if row.tree:
            message += _CACHE_HINT + _delete_reason(command, cwd, transcript_path)
        return True, message
    return False, ""


# --- Git losses ---------------------------------------------------------------------------------------------------
# Moved from git_guardrails.py (its loss checks) and _peer_loss.py. `_git_commit` owns the command parse: heredoc
# bodies stripped, segments split, `git -C <path>` and a preceding `cd <path>` retarget the repo, inline `VAR=1`
# prefixes read.

# Subcommands that can create the ref a later merge in the same command names.
REF_MAKERS = ("fetch", "update-ref", "branch", "tag")
# An opaque call is one whose repository cannot be proved; the parser reports it with cwd=None.
UNPROVED_TARGET = ("BLOCKED `git %s` (CLAUDE.core.md §Shell Discipline): the guard cannot prove which "
                   "repository this call targets (an unexpanded `-C` value, or a call behind env/sudo/xargs), so "
                   "it cannot read the index or the peer work that call would publish. Name the repository with "
                   "a literal `-C` path, and run the call directly rather than through a wrapper.")
# Every commit check reads the index before the command runs, so a write earlier in the same command
# lands after they judged the commit.
SAME_COMMAND_WRITE = ("BLOCKED commit (CLAUDE.core.md §Shell Discipline: an index write runs in its own Bash call) — `%s` "
                      "runs earlier in this command, and every commit guard reads the index before the command "
                      "runs, so they would judge this commit without that write; git's commit-msg hook cannot "
                      "re-check it here (%s). Run the `%s` in its own Bash call, then commit.")


# The options of each git subcommand a discard case reads, as git's parse-options reads them (`_shell_lex.options`:
# bundles, a value in the bundle or the next word, unique long prefixes, options after operands until `--`): short
# letters taking a value, long names, long names taking a value. A long list is complete enough to
# resolve a prefix the way git does; a prefix that could mean a dangerous option counts as that option.
_GIT_OPTIONS = {
    "branch": ("u",
               ("delete", "force", "quiet", "remotes", "all", "list", "move", "copy", "verbose", "track", "no-track",
                "set-upstream-to", "unset-upstream", "edit-description", "contains", "no-contains", "merged",
                "no-merged", "points-at", "sort", "format", "color", "column", "abbrev", "ignore-case", "omit-empty",
                "create-reflog", "recurse-submodules", "show-current"),
               ("set-upstream-to", "points-at", "sort", "format")),
    "reset": ("",
              ("hard", "soft", "mixed", "merge", "keep", "quiet", "patch", "intent-to-add", "recurse-submodules",
               "pathspec-from-file", "pathspec-file-nul", "refresh", "no-refresh"), ("pathspec-from-file",)),
    "clean": ("e", ("dry-run", "force", "interactive", "quiet", "exclude"), ("exclude",)),
    "push": ("o",
             ("all", "branches", "mirror", "delete", "tags", "follow-tags", "dry-run", "porcelain", "force",
              "force-with-lease", "force-if-includes", "repo", "set-upstream", "thin", "no-thin", "quiet", "verbose",
              "progress", "recurse-submodules", "verify", "no-verify", "signed", "atomic", "push-option", "ipv4",
              "ipv6", "receive-pack", "exec", "prune"), ("repo", "receive-pack", "exec", "push-option")),
    "checkout": ("bB",
                 ("quiet", "progress", "force", "ours", "theirs", "track", "no-track", "guess", "detach", "orphan",
                  "ignore-skip-worktree-bits", "merge", "conflict", "patch", "ignore-other-worktrees",
                  "overwrite-ignore", "recurse-submodules", "overlay", "no-overlay", "pathspec-from-file",
                  "pathspec-file-nul"), ("orphan", "conflict", "pathspec-from-file")),
    "restore": ("s",
                ("source", "staged", "worktree", "ours", "theirs", "merge", "conflict", "ignore-unmerged", "overlay",
                 "no-overlay", "quiet", "progress", "patch", "pathspec-from-file", "pathspec-file-nul",
                 "recurse-submodules", "ignore-skip-worktree-bits"), ("source", "pathspec-from-file")),
    "switch": ("cC",
               ("create", "force-create", "detach", "guess", "no-guess", "force", "discard-changes", "merge",
                "conflict", "orphan", "track", "no-track", "quiet", "progress", "recurse-submodules",
                "ignore-other-worktrees"), ("create", "force-create", "conflict", "orphan")),
    "update-ref": ("m", ("no-deref", "stdin", "create-reflog"), ()),
    "gc": ("", ("prune", "no-prune", "aggressive", "auto", "quiet", "force", "keep-largest-pack", "cruft",
                     "max-cruft-size", "expire-to", "detach"), ()),
}


def _git_opts(sub, args):
    return options(list(args), *_GIT_OPTIONS[sub])


_SIDE_UNSAFE = (".tscn", ".tres")


# Options a path-scoped discard may carry and still be judged by the worktree diff alone. Any other
# (`--staged`/`-S`, `--worktree`/`-W`, a short `-s <tree>`, `-f`, a bundle) reads as a loss.
_NO_LOSS_OPTS = {"--theirs", "--ours", "-q", "--quiet"}


@dataclass(frozen=True)
class Discard:
    """One destructive Git invocation, parsed once. `paths` are the operands a checkout/restore would
    overwrite, `targets` the named branches or stash entries, `flags` every dash option in order."""
    action: str
    paths: tuple = ()
    refs: tuple = ()
    flags: tuple = ()
    targets: tuple = ()
    source: str | None = None
    side: str | None = None
    separated: bool = False
    bulk: bool = False


@dataclass(frozen=True)
class Evidence:
    """What the repository shows about a parsed discard. Every fact defaults to the unproven value, so a
    failed read denies instead of allowing."""
    redirected: bool = False
    lossless: bool = False
    unstored: tuple = ()
    owned: bool = False
    clean: bool = False
    problems: tuple = ()
    preserved: bool = False


def _bulk(paths, cwd):
    """True when a named path is `.`, `:/`, `/`, a glob or a directory, so the retry names files instead."""
    return any(p in (".", ":/", "/") or re.search(r"[*?\[\]$`%]", p)
               or (cwd and os.path.isdir(os.path.join(cwd, p))) for p in paths)


def _is_commit(name, cwd):
    """True when `name` names a commit in `cwd`, so `git checkout <name> [<path>...]` reads it as a ref; without a
    readable repository the first operand is taken as the ref, as git's own parse leans."""
    if cwd is None or name == "-" or name.startswith("@{"):
        return True
    return run_git(["rev-parse", "--verify", "-q", name + "^{commit}"], cwd) is not None


def _parse_tree(sub, rest, cwd):
    """A checkout or restore that overwrites worktree paths, a forced checkout (every uncommitted edit), or None (a
    branch switch, a branch creation, a staged-only restore). Without `--`, `git checkout <a> [<b>...]` reads `<a>` as
    a ref when it names a commit and as a path otherwise, as git does."""
    split = rest.index("--") if "--" in rest else len(rest)
    head, separated = rest[:split], "--" in rest
    parsed = _git_opts(sub, head)
    flags = tuple(a for a in head if a.startswith("-"))
    source = next((a.split("=", 1)[1] for a in flags if a.startswith("--source=")), None)
    if sub == "restore":
        if (("S" in parsed.flags or "staged" in parsed.exact)
                and not ("W" in parsed.flags or "worktree" in parsed.flags)):
            return None
        refs, paths = (), tuple(parsed.operands) + tuple(rest[split + 1:])
    else:
        if parsed.flags & {"b", "B", "orphan"}:
            return None
        forced = bool(parsed.flags & {"f", "force"})
        named = parsed.operands
        if _keeps_local_changes(sub, rest):
            return None
        if separated:
            refs, paths = tuple(named), tuple(rest[split + 1:])
        elif named and _is_commit(named[0], cwd):
            refs, paths = (named[0],), tuple(named[1:])
        else:
            refs, paths = (), tuple(named)
        if not paths and not forced:
            return None
        # v1 read `git checkout .` as an unseparated discard; it keeps that reading.
        separated = separated or (bool(paths) and "." not in rest)
    return Discard(sub, paths=paths, refs=refs, flags=flags, source=source,
                   side="theirs" if "--theirs" in flags else "ours" if "--ours" in flags else None,
                   separated=separated, bulk=_bulk(paths, cwd))


# The options a branch switch may carry and still discard nothing: git aborts a switch that would lose a local
# change unless told `--force`/`--discard-changes` or `--merge` (git-switch(1); git-checkout(1) `--merge`: "staged
# changes may be lost"). `--orphan` removes every tracked file and `-C`/`-B` reset a branch, so neither is here.
_SWITCH_KEEPS = {"checkout": frozenset(("q", "quiet", "progress", "detach", "ignore-other-worktrees", "guess")),
                 "switch": frozenset(("q", "quiet", "progress", "detach", "d", "c", "create", "guess", "track", "t",
                                      "ignore-other-worktrees"))}


def _keeps_local_changes(sub, rest):
    """True when `git <sub> <rest>` is a branch switch that discards nothing, in any repository: a `switch`, or a
    `checkout --detach` (it takes one commit and no paths), with no option outside `_SWITCH_KEEPS`."""
    keeps = _SWITCH_KEEPS.get(sub)
    if keeps is None or "--" in rest:
        return False
    parsed = _git_opts(sub, rest)
    if sub == "checkout" and ("detach" not in parsed.flags or len(parsed.operands) > 1):
        return False
    return parsed.flags <= keeps and len(parsed.operands) <= 2


def _flags(rest):
    return tuple(a for a in rest if a.startswith("-"))


def _operands(rest):
    return tuple(a for a in rest if not a.startswith("-"))


def _lost_path(d, cwd, git_env):
    """(judged, the first path whose content the discard would lose). `judged` False means the request's
    shape cannot be proven lossless: no repository, a checkout with no `--`, a flag the diff cannot judge,
    or a bulk path. A `judged` True with no path means every named path loses nothing. A restore of a path
    absent from the worktree writes bytes and overwrites none."""
    if cwd is None or (d.action == "checkout" and not d.separated):
        return False, None
    if (not d.paths or len(d.refs) > 1
            or any(o not in _NO_LOSS_OPTS and not o.startswith("--source=") for o in d.flags)
            or any(p in (".", ":/", "/") or re.search(r"[*?\[\]$`%]", p) for p in d.paths)):
        return False, None
    base = d.refs[0] if d.refs else d.source
    for path in d.paths:
        if "__pycache__" in Path(path).parts and ".." not in Path(path).parts and not path.startswith(":"):
            continue
        if d.side:
            if path.lower().endswith(_SIDE_UNSAFE):
                return True, path
            if not (run_git(["ls-files", "-u", "--", path], cwd, git_env) or "").strip():
                return True, path
            continue
        # A cwd that is no directory (a `cd` that will fail, an unresolved `-C "$VAR"`) holds no path, so its absent
        # paths prove nothing (MaddManager aaa816fc).
        if (d.action == "restore" and not path.startswith(":") and os.path.isdir(cwd)
                and not os.path.lexists(os.path.join(cwd, path))):
            continue
        diff = ["diff", "--quiet", "--ignore-cr-at-eol"] + ([base] if base else []) + ["--", path]
        if (run_git(diff, cwd, git_env) is None and not (base and _clean_in_head(path, cwd, git_env))
                and not (_content_stored(path, cwd) and (d.action == "restore" or not base))):
            return True, path
    return True, None


def _content_stored(path, cwd):
    """True when git's object store holds the working-tree bytes of `path`, so discarding them loses nothing
    git cannot reproduce (`_delete_rule.worktree_unstored`). A restore or a checkout from the index
    qualifies; a checkout from another ref also overwrites the index, whose staged bytes this does not judge."""
    if not os.path.isfile(os.path.join(cwd, path)):
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


def _stash_preserved(flags, targets, cwd, git_env=None):
    """True when the one stash entry `git stash drop` targets loses nothing git cannot reach: a ref outside
    refs/stash holds the entry commit, or `_stash_changes_held` finds every change it carries in one ref's tip."""
    if len(targets) > 1 or [a for a in flags if a not in ("-q", "--quiet")] or not cwd:
        return False
    sha = run_git(["rev-parse", "--verify", "--quiet", (targets or ("stash@{0}",))[0] + "^{commit}"], cwd, git_env)
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
    batch = _git_run.soft_text(["cat-file", "--batch-check"], cwd=cwd, env=git_env, timeout=30,
                               input=("\n".join(queries) + "\n").encode("utf-8"))
    rows = (batch or "").splitlines()
    if batch is None or len(rows) != len(queries):
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
    that tree, no path its index commit changes differs between the tip and that commit, and no untracked file
    differs between the tip and `stash^3`."""
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


_BRANCH_DELETE_FLAGS = ("-D", "-d", "--delete", "--force", "-f", "-q", "--quiet")


def _branch_delete(rest):
    """`git branch <rest>` as a force delete (`-D`, `-d -f`, `-df`, `--delete --force`, any prefix, either order,
    before or after the names), or None: Discard with each option spelled `-x`/`--name` and the named branches."""
    parsed = _git_opts("branch", rest)
    if not ("D" in parsed.flags or (parsed.flags & {"d", "delete"} and parsed.flags & {"f", "force"})):
        return None
    flags = tuple(sorted(("-" if len(f) == 1 else "--") + f for f in parsed.flags))
    return Discard("branch-delete", flags=flags, targets=tuple(parsed.operands))


def _branch_deletion_problems(names, flags, cwd, git_env=None):
    """Why `branch -D` would lose a commit: one line per unmet branch, empty when it loses none. A branch is safe
    when it is a `scratch/<name>` session fixture, when every commit `git cherry` finds is already applied to HEAD
    or to origin/HEAD (a squash-merged or cherry-picked branch, which `-d` refuses), or when a ref outside the
    deleted set (a remote-tracking ref, a tag, `refs/preserve/`) holds its tip. Any other flag, a missing branch or
    a failed git call is a problem."""
    if not cwd or not names:
        return ["no branch is named, or the repository cannot be read"]
    problems = ["flag `%s` is not one `branch -D` is judged with" % a
                for a in flags if a not in _BRANCH_DELETE_FLAGS]
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
        if any(out is not None and not any(line.startswith("+") for line in out.splitlines())
               for out in (run_git(["cherry", base, "refs/heads/" + name], cwd, git_env) for base in bases)):
            continue
        holders = run_git(["for-each-ref", "--contains", tip.strip(), "--format=%(refname)"], cwd, git_env)
        if not any(ref and ref not in doomed for ref in (holders or "").splitlines()):
            problems.append("`%s` holds commits no other ref has" % name)
    return problems


_BRANCH_CREATE_FLAGS = {"f", "force", "q", "quiet", "track", "no-track", "create-reflog"}


def _branch_overwrite(rest):
    """`git branch -M/-C/-f` (or `-m`/`-c` with `-f`, any prefix, either order) as a Discard naming the branch it
    writes over: the last operand of a move or copy, the first of a forced create. None for any other call
    (a delete is `_branch_delete`'s, a list or a rename without force never overwrites)."""
    parsed = _git_opts("branch", rest)
    flags, names = parsed.flags, parsed.operands
    if flags & {"d", "D", "delete"} or not names:
        return None
    forced = bool(flags & {"f", "force"})
    if flags & {"M", "C"} or (forced and flags & {"m", "move", "c", "copy"}):
        target = names[-1]
    elif forced and flags <= _BRANCH_CREATE_FLAGS:
        target = names[0]
    else:
        return None
    return Discard("branch-overwrite", flags=tuple(sorted(("-" if len(f) == 1 else "--") + f for f in flags)),
                   targets=(target,))


def _overwrite_facts(d, cwd, git_env, transcript_path):
    """A branch that does not exist yet loses nothing; an existing one is judged as `branch -D` judges it."""
    if cwd:
        tip = run_git(["rev-parse", "--verify", "-q", "refs/heads/" + d.targets[0]], cwd, git_env)
        if tip is None or not tip.strip():
            return Evidence()
    return Evidence(problems=tuple(_branch_deletion_problems(d.targets, (), cwd, git_env)))


def _update_ref_delete(rest):
    parsed = _git_opts("update-ref", rest)
    if "d" not in parsed.flags:
        return None
    return Discard("update-ref-delete", flags=_flags(rest), targets=tuple(parsed.operands[:1]))


def _ref_deletion_problems(d, cwd, git_env):
    """Why `update-ref -d` would lose a commit, as `_branch_deletion_problems` words it: a branch is judged as
    `branch -D` judges it; any other ref (tag, remote-tracking, preserve, stash) is safe when another ref holds its
    tip. An absent ref removes nothing; a name outside `refs/` or a symbolic ref cannot be proved."""
    if not cwd or not d.targets:
        return ["no ref is named, or the repository cannot be read"]
    ref = d.targets[0]
    if not ref.startswith("refs/"):
        return ["`%s` is not a full ref name (`refs/...`), so the ref it deletes is not proved" % ref]
    if run_git(["symbolic-ref", "-q", ref], cwd, git_env):
        return ["`%s` is a symbolic ref, so the delete lands on the ref it names" % ref]
    tip = run_git(["rev-parse", "--verify", "-q", ref], cwd, git_env)
    if tip is None or not tip.strip():
        return []
    if ref.startswith("refs/heads/"):
        return _branch_deletion_problems([ref[len("refs/heads/"):]], (), cwd, git_env)
    holders = run_git(["for-each-ref", "--contains", tip.strip(), "--format=%(refname)"], cwd, git_env)
    if any(r and r != ref for r in (holders or "").splitlines()):
        return []
    return ["`%s` holds commits no other ref has" % ref]


def _in_own_worktree(cwd, git_env, transcript_path):
    """True when `cwd` (resolved through `-C`) lies in a worktree this session created, a submodule checked out
    inside one included: that clone is the session's own. Redirected git state or an unknown session reads False."""
    if (not cwd or not transcript_path
            or any((git_env or {}).get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'))):
        return False
    return any(session_created_worktree(top, transcript_path)
               for top in worktree_chain(cwd, lambda flag, start: run_git(["rev-parse", flag], start, git_env)))


def _worktree_clean(cwd, git_env):
    """True when the worktree holds nothing uncommitted, untracked files included. An unreadable status reads as
    dirty."""
    status = run_git(['status', '--short'], cwd, git_env)
    return status is not None and status.strip() == ''


def _tree_facts(d, cwd, git_env, transcript_path):
    if any((git_env or {}).get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE')):
        return Evidence(redirected=True)
    if d.action in ("checkout", "switch") and not d.paths:  # a forced switch discards every uncommitted change
        if cwd is not None and _worktree_clean(cwd, git_env):
            return Evidence(lossless=True)
        return Evidence(owned=_in_own_worktree(cwd, git_env, transcript_path))
    judged, lost = _lost_path(d, cwd, git_env)
    if judged and lost is None:
        return Evidence(lossless=True)
    eligible = d.separated if d.action == "checkout" else bool(d.paths or d.flags or d.refs or d.separated)
    return Evidence(unstored=(lost,) if lost else (), owned=eligible and _in_own_worktree(cwd, git_env, transcript_path))


def _reset_facts(d, cwd, git_env, transcript_path):
    owned = _in_own_worktree(cwd, git_env, transcript_path)
    return Evidence(owned=owned, clean=owned and _worktree_clean(cwd, git_env))


def _tree_passes(ev):
    return not ev.redirected and (ev.lossless or ev.owned)


_SAVE_FIRST = "save it first: `git update-ref refs/preserve/<name> $(git hash-object -w <path>)`, "


def _checkout_denial(d, ev):
    if not d.paths:
        retry = "then commit or save each edit and switch without `-f`."
    elif d.bulk:
        retry = "then name each file: `git checkout -- <file>` for one saved edit at a time."
    elif d.refs:
        retry = "then run `git restore --source=%s -- <path>`." % d.refs[0]
    else:
        retry = "then the same command passes."
    return ("BLOCKED `git checkout` worktree discard (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
            "overwrites uncommitted edits to those paths, which may be another session's; another session's "
            "edit needs the owner's words first. To discard your own edit, " + _SAVE_FIRST + retry)


def _restore_denial(d, ev):
    if d.bulk:
        retry = "then name each file: `git restore <file>` for one saved edit at a time."
    elif {"W", "worktree"} & _git_opts("restore", d.flags).flags:
        retry = "then run it without `--worktree`/`-W` (restore's default), one saved file at a time."
    elif "-s" in d.flags:
        retry = "then spell the source `--source=<ref>` and name one saved file."
    else:
        retry = "then the same command passes."
    return ("BLOCKED `git restore` without `--staged` (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
            "discards uncommitted worktree edits, which may be another session's; another session's edit "
            "needs the owner's words first. To discard your own edit, " + _SAVE_FIRST + retry)


def _switch_denial(d, ev):
    return ("BLOCKED `git switch -f` (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
            "`-f`/`--discard-changes` throws away every uncommitted edit in this worktree, which may be another "
            "session's; another session's edit needs the owner's words first. To discard your own, "
            + _SAVE_FIRST + "then commit or save each edit and switch without `-f`.")


# What each ref-losing call is called in its deny: (the call, what it does to the ref, the retry noun).
_REF_VERBS = {"branch-delete": ("`git branch -D`", "force-deleting", "delete"),
              "branch-overwrite": ("`git branch -M/-C/-f`", "overwriting", "call"),
              "update-ref-delete": ("`git update-ref -d`", "deleting", "delete")}


def _branch_denial(d, ev):
    call, doing, noun = _REF_VERBS[d.action]
    unreachable = [p for p in ev.problems if p.endswith("holds commits no other ref has")]
    return ("BLOCKED %s (CLAUDE.core.md §Shell Discipline: unreachable commits would be lost) — " % call
            + ("%s %s" % (doing, ", ".join("`%s`" % t for t in d.targets)) if d.action != "branch-delete" else doing)
            + " would lose commits: " + "; ".join(ev.problems) + ". "
            + ("Name only local branches, with no flag beyond `-D`. "
               if d.action == "branch-delete" and len(unreachable) < len(ev.problems) else "")
            + ("Keep each branch's commits reachable with `git update-ref refs/preserve/<name> <branch>`, "
               "then the same %s passes." % noun if unreachable else ""))


def _stash_drop_denial(d, ev):
    target = d.targets[0] if d.targets else "stash@{0}"
    return ("BLOCKED `git stash drop` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — stashed work "
            "is unreferenced once dropped. With the owner's go-ahead, keep it reachable and drop: "
            f"`git update-ref refs/preserve/<name> {target}`, then the same drop passes.")


_GO_AHEAD = "(CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "


def _clean_forced(parsed):
    """`git clean` that deletes: forced, and no dry run (`-n`, or `--dry-run` spelled in full as v1 read it)."""
    return bool(parsed.flags & {"f", "force"}) and "n" not in parsed.flags and "dry-run" not in parsed.exact


def _clean_unforced(parsed):
    """`git clean` with no `-f`, no dry run and no `-i`: it deletes only where `clean.requireForce` is false."""
    return (not parsed.flags & {"f", "force", "n", "i", "interactive"} and "dry-run" not in parsed.exact)


def _clean_config_facts(d, cwd, git_env, transcript_path):
    """`clean.requireForce` as git reads it for this call (repo config, then `-c`): only an explicit false makes
    the unforced clean delete. An unset or unreadable value is git's default, which refuses. No repository to
    read leaves the call unproved."""
    if cwd is None:
        return Evidence()
    value = run_git(["config", "--type=bool", "--get", "clean.requireForce"], cwd, git_env)
    return Evidence(lossless=not (value is not None and value.strip() == "false"))


def _push_specs(parsed):
    """The refspec operands of a push: all but the remote, unless `--repo` names the remote."""
    return parsed.operands if parsed.values.get("repo") else parsed.operands[1:]


def _push_deletes(parsed):
    """A push that deletes remote refs: `--delete`/`-d`, `--mirror`, or a `:<ref>` refspec."""
    return bool(parsed.flags & {"d", "delete", "mirror"}) or any(op.startswith(":") for op in _push_specs(parsed))


def _push_delete_denial(d, ev):
    refs = ", ".join("`%s`" % t.lstrip(":") for t in d.targets)
    if any(f.startswith("--m") for f in d.flags):
        what = "`--mirror` makes the remote's refs match this clone's, deleting every ref this clone lacks"
    else:
        what = "deletes %s on the remote" % (refs or "a ref")
    return ("BLOCKED `git push` that deletes remote refs " + _GO_AHEAD + what + ", and the commits only that ref "
            "held with it. Leave the remote's refs; ask the user before deleting one.")


def _reflog_delete_denial(d, ev):
    names = ", ".join("`%s`" % t for t in d.targets) or "reflog entries"
    return ("BLOCKED `git reflog delete` " + _GO_AHEAD + "deleting %s drops a recovery point, and the reflog is the "
            "last recovery path for lost commits. Leave it." % names)


def _push_forced(parsed):
    """A push that may overwrite remote history: `-f`, a `--force*` option, or a `+<refspec>` (`--repo` naming the
    remote leaves every operand a refspec, `_push_specs`)."""
    return (bool(parsed.flags & {"f", "force", "force-with-lease", "force-if-includes"})
            or any(op.startswith("+") for op in _push_specs(parsed)))


@dataclass(frozen=True)
class Case:
    """One git discard the floor judges. `parse(sub, rest, cwd)` returns the parsed call or None; `facts(d, cwd,
    git_env, transcript_path)` reads the evidence once (None reads none); `passes(ev)` is True when that evidence
    shows the discard loses nothing (None never passes); `denial` is the block text, or `denial(d, ev)` renders it
    from the same request and evidence. A new loss case is one row and its table test."""
    parse: object
    denial: object
    facts: object = None
    passes: object = None


CASES = (
    Case(lambda sub, rest, cwd: _parse_tree(sub, rest, cwd) if sub == "checkout" else None, _checkout_denial,
         _tree_facts, _tree_passes),
    Case(lambda sub, rest, cwd: _parse_tree(sub, rest, cwd) if sub == "restore" else None, _restore_denial,
         _tree_facts, _tree_passes),
    Case(lambda sub, rest, cwd: Discard("reset --hard", flags=_flags(rest))
         if sub == "reset" and "hard" in _git_opts("reset", rest).flags else None,
         "BLOCKED `git reset --hard` " + _GO_AHEAD + "discards every uncommitted change in the shared worktree with "
         "no undo. Use `git reset --keep` to move HEAD and keep local edits.",
         _reset_facts, lambda ev: ev.owned and ev.clean),
    Case(lambda sub, rest, cwd: Discard("clean") if sub == "clean" and _clean_forced(_git_opts("clean", rest))
         else None,
         "BLOCKED `git clean -f` " + _GO_AHEAD + "deletes untracked files permanently (git has no copy). Run the "
         "same command with `-n` first, then ask the user to confirm."),
    Case(lambda sub, rest, cwd: Discard("force-push") if sub == "push" and _push_forced(_git_opts("push", rest))
         else None,
         "BLOCKED force-push " + _GO_AHEAD + "rewrites remote history and can erase commits other checkouts still "
         "depend on. Rebase onto the remote and push normally."),
    Case(lambda sub, rest, cwd: Discard("switch", flags=_flags(rest))
         if sub == "switch" and _git_opts("switch", rest).flags & {"f", "force", "discard-changes"} else None,
         _switch_denial, _tree_facts, _tree_passes),
    Case(lambda sub, rest, cwd: Discard("push-delete", flags=_flags(rest),
                                        targets=tuple(_push_specs(_git_opts("push", rest))))
         if sub == "push" and _push_deletes(_git_opts("push", rest)) else None,
         _push_delete_denial),
    Case(lambda sub, rest, cwd: Discard("clean-config") if sub == "clean" and _clean_unforced(_git_opts("clean", rest))
         else None,
         "BLOCKED `git clean` without `-f` " + _GO_AHEAD + "`clean.requireForce` is false here, so clean deletes "
         "untracked files permanently (git has no copy) without `-f`. Run it with `-n` first, then ask the user to "
         "confirm.",
         _clean_config_facts, lambda ev: ev.lossless),
    Case(lambda sub, rest, cwd: _branch_overwrite(rest) if sub == "branch" else None,
         _branch_denial, _overwrite_facts, lambda ev: not ev.problems),
    Case(lambda sub, rest, cwd: _update_ref_delete(rest) if sub == "update-ref" else None,
         _branch_denial,
         lambda d, cwd, git_env, tp: Evidence(problems=tuple(_ref_deletion_problems(d, cwd, git_env))),
         lambda ev: not ev.problems),
    Case(lambda sub, rest, cwd: Discard("reflog-delete", targets=_operands(rest[1:]))
         if sub == "reflog" and rest[:1] == ("delete",) else None,
         _reflog_delete_denial),
    Case(lambda sub, rest, cwd: _branch_delete(rest) if sub == "branch" else None,
         _branch_denial,
         lambda d, cwd, git_env, tp: Evidence(problems=tuple(_branch_deletion_problems(d.targets, d.flags, cwd,
                                                                                         git_env))),
         lambda ev: not ev.problems),
    Case(lambda sub, rest, cwd: Discard("stash drop", flags=_flags(rest[1:]), targets=_operands(rest[1:]))
         if sub == "stash" and rest[:1] == ("drop",) else None,
         _stash_drop_denial,
         lambda d, cwd, git_env, tp: Evidence(preserved=_stash_preserved(d.flags, d.targets, cwd, git_env)),
         lambda ev: ev.preserved),
    Case(lambda sub, rest, cwd: Discard("stash clear") if sub == "stash" and rest[:1] == ("clear",) else None,
         "BLOCKED `git stash clear` " + _GO_AHEAD + "stashed work is unreferenced once dropped. Leave it; ask the "
         "user before discarding."),
    Case(lambda sub, rest, cwd: Discard("reflog-expire") if sub == "reflog" and "expire" in rest else None,
         "BLOCKED `git reflog expire` " + _GO_AHEAD + "the reflog is the last recovery path for lost commits. Leave "
         "expiry to git's own schedule."),
    Case(lambda sub, rest, cwd: Discard("gc-prune") if sub == "gc" and "prune" in _git_opts("gc", rest).flags
         else None,
         "BLOCKED `git gc --prune` " + _GO_AHEAD + "permanently removes unreachable objects that reflog recovery "
         "depends on. Run `git gc` without `--prune`."),
    Case(lambda sub, rest, cwd: Discard(sub) if sub in ("filter-branch", "filter-repo") else None,
         lambda d, ev: "BLOCKED `git %s` " % d.action + _GO_AHEAD + "rewrites every commit in place, invalidating "
         "all existing clones and hashes. Ask the user for explicit go-ahead before rewriting history; never do it "
         "in-session."),
)


def verdict(args, cwd=None, git_env=None, transcript_path=None):
    """A block message for one git invocation, or None. `cwd` is the repo the call targets; without it a
    checkout/restore discard is judged unsafe."""
    if not args:
        return None
    sub, rest = args[0], tuple(args[1:])
    for case in CASES:
        d = case.parse(sub, rest, cwd)
        if d is None:
            continue
        ev = case.facts(d, cwd, git_env, transcript_path) if case.facts else Evidence()
        if case.passes and case.passes(ev):
            return None
        return case.denial if isinstance(case.denial, str) else case.denial(d, ev)
    return None


# --- Peer losses at commit: stage records, stale blobs, amends (moved from _peer_loss.py) ---------------------
REVERT_SCAN_CAP = 500
REVERT_HISTORY = 30
TRAILER_RE = re.compile(r"claude\.ai/code/session_\w+")


@dataclass(frozen=True)
class Loss:
    kind: str
    paths: tuple = ()
    head: str | None = None
    base: str | None = None
    record: str | None = None
    trailer: str | None = None


def stage_record_loss(cwd, git_env):
    """A private index `tools/index_commit.py stage` built over a HEAD that has since moved, or whose record
    cannot be read: committing it would undo every commit since its base."""
    index = (git_env or {}).get("GIT_INDEX_FILE")
    if not index:
        return None
    record = stage_record_path(index)
    if not record.is_file():
        return None
    base = stage_record_base(record)
    head = run_git(["rev-parse", "HEAD"], cwd, git_env)
    head = head.strip() if head is not None else None
    if base and head == base:
        return None
    return Loss("stage_record", head=head, base=base, record=record.name)


def message_trailer(message_path):
    try:
        match = TRAILER_RE.search(Path(message_path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return match.group(0) if match else None


def session_trailer(session_id):
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


def lane_commits(root, git_env):
    dirs = (run_git(["rev-parse", "--git-dir", "--git-common-dir"], root, git_env) or "").splitlines()
    if len(dirs) < 2 or os.path.realpath(os.path.join(root, dirs[0])) == os.path.realpath(os.path.join(root, dirs[1])):
        return set()
    for ref in ("refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/remotes/origin/master"):
        base = run_git(["merge-base", "HEAD", ref], root, git_env)
        if base and base.strip():
            return set((run_git(["rev-list", base.strip() + "..HEAD"], root, git_env) or "").split())
    return set()


def amend_loss(rest, cwd, git_env=None, session_id=None):
    if not parse_commit_args(rest)[1]:
        return None
    root = (run_git(["rev-parse", "--show-toplevel"], cwd, git_env) or cwd).strip()
    head = (run_git(["rev-parse", "HEAD"], root, git_env) or "").strip()
    if head and head in lane_commits(root, git_env):
        return None
    trailer = session_trailer(session_id)
    body = run_git(["log", "-1", "--format=%B", "HEAD"], root, git_env) if head else None
    if trailer and body is not None and trailer in body:
        return None
    return Loss("amend", head=head if body is not None else None, trailer=trailer)


def _replaced_blobs(root, git_env, own_trailer):
    revs = (run_git(["rev-list", "-n", str(REVERT_HISTORY), "HEAD"], root, git_env) or "").split()
    own = lane_commits(root, git_env)
    if own_trailer and revs:
        own |= set((run_git(["log", "--format=%H", "-F", "--grep=" + own_trailer, "-n",
                             str(REVERT_HISTORY), "HEAD"], root, git_env) or "").split())
    peers = [r for r in revs if r not in own]
    out = run_git(["show", "--raw", "-z", "--no-abbrev", "--no-renames", "--format="] + peers,
                  root, git_env) if peers else ""
    found = {}
    for path, fields in _raw_entries((out or "").replace("\n", "")):
        if len(fields) >= 5 and fields[4] == "M":
            found.setdefault(path, set()).add(fields[2])
    return found


def _raw_entries(out):
    tokens = (out or "").split("\0")
    for meta, path in zip(tokens[::2], tokens[1::2]):
        if meta.startswith(":") and path:
            yield path, meta.split()


def _tree_blobs(ref, paths, root, git_env):
    out = run_git(["ls-tree", "-z", ref, "--"] + paths, root, git_env)
    if out is None:
        return None
    entries = (entry.partition("\t") for entry in out.split("\0"))
    return {path: meta.split()[-1] for meta, _, path in entries if path}


def stale_loss(rest, cwd, git_env=None, session_id=None, own_trailer=None):
    """Staged paths whose blob would set a peer's newer commit back: a staged blob equal to HEAD's worktree copy
    from an older index, a blob a peer commit replaced, or a file a peer deleted re-added."""
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    has_spec = bool(pathspec) or _pathspec_file(rest) is not None
    if include_dirty or (has_spec and not ({"-i", "--include"} & set(rest))):
        return None
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if root is None:
        return None
    root = root.strip()
    reverting = run_git(["rev-parse", "-q", "--verify", "REVERT_HEAD"], root, git_env)
    added = run_git(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"], root, git_env)
    readded = [p for p in (added or "").split("\0") if p and not os.path.lexists(os.path.join(root, p))
               and run_git(["log", "-1", "--format=%h", "--diff-filter=D", "HEAD", "--", p], root, git_env)]
    out = run_git(["diff", "--cached", "--name-only", "--diff-filter=M", "-z"], root, git_env)
    raw = run_git(["diff", "--cached", "--raw", "-z", "--no-abbrev", "--diff-filter=M"], root, git_env)
    staged = {p: (fields[2], fields[3]) for p, fields in _raw_entries(raw) if len(fields) >= 4}
    mode_only = {p for p, (old, new) in staged.items() if old == new}
    paths = [p for p in (out or "").split("\0") if p and p not in mode_only][:REVERT_SCAN_CAP]
    stale = []
    if paths:
        head = _tree_blobs("HEAD", paths, root, git_env)
        work = run_git(["hash-object", "--"] + paths, root, git_env)
        if head is not None and work is not None:
            stale = [p for p, blob in zip(paths, work.split()) if head.get(p) == blob]
            replaced = _replaced_blobs(root, git_env, own_trailer or session_trailer(session_id))
            merging = run_git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], root, git_env)
            theirs = (_tree_blobs("MERGE_HEAD", paths, root, git_env) or {}) if merging else {}
            stale += [p for p in paths if p not in stale and p in staged and staged[p][1] in replaced.get(p, ())
                      and staged[p][1] != theirs.get(p)]
    found = stale + readded
    if reverting and found:
        changed = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "-z", "REVERT_HEAD"],
                          root, git_env)
        reverted = set((changed or "").split("\0"))
        found = [p for p in found if p not in reverted]
    return Loss("stale", paths=tuple(found)) if found else None


def base_verdict(cwd, git_env):
    loss = stage_record_loss(cwd, git_env)
    if loss is None:
        return None
    reason = ("HEAD is %s but the index was read from base %s" % (loss.head[:9], loss.base[:9])
              if loss.base and loss.head is not None else "its stage record %s cannot be read" % loss.record)
    return ("BLOCKED commit (CLAUDE.core.md §Core Principles: preserve peer work) — %s, so publishing this "
            "private index would undo every commit since its base. Rerun `python3 "
            ".claude/tools/index_commit.py stage` (index_commit) with the same paths, then commit the "
            "new index." % reason)


def revert_verdict(rest, cwd, git_env, session_id=None, own_trailer=None):
    loss = stale_loss(rest, cwd, git_env, session_id, own_trailer)
    return _revert_message(loss.paths) if loss else None


def amend_verdict(rest, cwd, git_env=None, session_id=None):
    loss = amend_loss(rest, cwd, git_env, session_id)
    if loss is None:
        return None
    if loss.head is None:
        why = "HEAD cannot be read"
    elif not loss.trailer:
        why = "this session's commit trailer cannot be resolved, so HEAD %s has no known author" % loss.head[:9]
    else:
        why = "HEAD %s carries no `%s` trailer, so another author made it" % (loss.head[:9], loss.trailer)
    return ("BLOCKED git commit --amend (CLAUDE.core.md §Core Principles: preserve peer work) — %s, and the amend "
            "would rewrite it. Commit the change as a new commit." % why)


# Each commit-time check: (check(rest, cwd, git_env, session_id, own_trailer) -> denial or None, re-run by the
# commit-msg hook). The amend check reads HEAD and its trailer, which nothing between the tool call and the hook
# moves, so the hook skips it; the other two read the index git is writing.
COMMIT_CHECKS = (
    (lambda rest, cwd, git_env, session_id, own_trailer: base_verdict(cwd, git_env), True),
    (lambda rest, cwd, git_env, session_id, own_trailer: amend_verdict(rest, cwd, git_env, session_id), False),
    (revert_verdict, True),
)


def commit_verdict(rest, cwd, git_env, session_id=None, own_trailer=None, hooked=False):
    """The first commit-time loss of `git commit <rest>`, or None; `hooked` runs only the checks the hook re-runs."""
    for check, rechecked in COMMIT_CHECKS:
        message = None if hooked and not rechecked else check(rest, cwd, git_env, session_id, own_trailer)
        if message:
            return message
    return None


def _revert_message(stale):
    if not stale:
        return None
    shown = "\n  ".join(stale[:10]) + ("\n  ... %d more" % (len(stale) - 10) if len(stale) > 10 else "")
    return ("BLOCKED commit (CLAUDE.core.md §Core Principles: preserve peer work) — it would revert a peer's commit "
            "to %d file(s):\n  %s\n"
            "The index was built from an older HEAD. Rebuild it from the current HEAD, or commit "
            "your files with a pathspec (`git commit -F <msg> -- <paths>`)." % (len(stale), shown))


def commit_msg_check(message_path, environ=None, cwd=None):
    """Deny text for the commit git is about to write, or None: the `COMMIT_CHECKS` marked for it re-run inside
    git's commit-msg hook (`.claude/githooks/commit-msg`), because the PreToolUse check ran before the commit
    hooks and a peer's commit can land in between. A pathspec (`next-index-*`) or `-a` (`index.lock`) commit takes
    the working tree, and a merge has its own guard, so those pass. The message's own Claude-Session trailer is
    the session whose commits it may revert."""
    environ = os.environ if environ is None else environ
    cwd = cwd or os.getcwd()
    index = environ.get("GIT_INDEX_FILE") or ""
    name = os.path.basename(index)
    if name.startswith("next-index") or name == "index.lock":
        return None
    git_env = {k: v for k, v in environ.items() if k.startswith("GIT_")}
    if index:
        git_env["GIT_INDEX_FILE"] = os.path.abspath(os.path.join(cwd, index))
    if merge_in_progress(cwd, git_env):
        return None
    return commit_verdict([], cwd, git_env, own_trailer=message_trailer(message_path), hooked=True)


def merge_in_progress(cwd, git_env):
    """True when the repository has a `MERGE_HEAD`: the commit that closes the merge, which the commit-msg entry
    does not re-check."""
    git_dir = run_git(["rev-parse", "--git-dir"], cwd, git_env)
    return bool(git_dir) and os.path.exists(os.path.join(cwd, git_dir.strip(), "MERGE_HEAD"))


def _skips_hooks(rest):
    """True when a commit-like call's own flags skip git's commit-msg hook (`-n` in any bundle, `--no-verify` by any
    prefix)."""
    return "n" in commit_options(rest).flags


def _unhooked_publish(args, cwd, git_env, command):
    """Why git's commit-msg hook cannot re-check what this publication commits with every check the chain ran, or
    None when it can (D-IW, owner 2026-11-23: an index write may precede a commit only where the hook re-checks).
    The hook re-runs them (`pre_bash_dispatch.commit_time`) only for a plain `git commit` it runs in a repository
    whose commit-time entry is this harness's own."""
    if args[0] != "commit":
        return "git runs the commit-msg hook for `git commit`, not for `git %s`" % args[0]
    if _skips_hooks(args[1:]):
        return "the commit skips the hooks"
    if parse_commit_args(args[1:])[1]:
        return "an amend: the amend check read HEAD before this command moved it"
    if merge_in_progress(cwd, git_env):
        return "the commit closes a merge, which the hook passes"
    if re.search(r"hookspath|GIT_CONFIG_(?:PARAMETERS|COUNT)", command, re.I):
        return "the command overrides git config"
    configured = run_git(["config", "--get", "core.hooksPath"], cwd, git_env)
    top = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if not configured or not top or configured.strip().replace("\\", "/").rstrip("/") != ".claude/githooks":
        return "the repository's core.hooksPath is not .claude/githooks"
    if not _same_commit_time_entry(Path(top.strip()) / ".claude"):
        return ("the repository's commit-time entry is not this checkout's: its githooks/commit-msg, "
                "hooks/pre_bash_dispatch.py or commit judges differ, as in an older lane")
    return None


# The files that decide what git's commit-msg hook re-runs: the hook and the dispatcher holding `COMMIT_JUDGES`.
_COMMIT_TIME_FILES = ("githooks/commit-msg", "hooks/pre_bash_dispatch.py")


def _same_commit_time_entry(theirs):
    """True when the `.claude` folder `theirs` runs this checkout's commit-time entry: it is ours, or its commit-msg
    hook and dispatcher match ours byte for byte and it holds the same commit judges. A hook that differs may re-run
    fewer checks than the chain ran."""
    ours = Path(_HOOKS_DIR).parent
    try:
        if os.path.samefile(ours, theirs):
            return True
    except OSError:
        return False
    import pre_bash_dispatch
    try:
        for rel in _COMMIT_TIME_FILES:
            if (theirs / rel).read_bytes() != (ours / rel).read_bytes():
                return False
    except OSError:
        return False
    return all((theirs / "hooks" / (name + ".py")).is_file() == (ours / "hooks" / (name + ".py")).is_file()
               for name, _argv in pre_bash_dispatch.COMMIT_JUDGES)


def _unproved_publish(args):
    """True when an opaque call (cwd=None) would write the index, publish a commit or push."""
    return bool(args) and (args[0] == "push" or publishes_index(args[0], args[1:]) or writes_index(args))


_INIT_VALUE_FLAGS = ("-b", "--initial-branch", "--template", "--separate-git-dir", "--object-format", "--ref-format")


def _init_target(rest, target, git_env, expand):
    """(the path `git init <rest>` makes a repository at, its normalized key, True when no repository has its top
    there yet)."""
    named, takes_value = [], False
    for a in rest:
        if takes_value:
            takes_value = False
        elif a in _INIT_VALUE_FLAGS:
            takes_value = True
        elif not a.startswith("-"):
            named.append(a)
    made = resolve_cd(target, expand(named[0])) if named else target
    top = run_git(["rev-parse", "--show-toplevel"], made, git_env)
    made_key = os.path.normcase(os.path.abspath(made))
    return made, made_key, top is None or os.path.normcase(os.path.abspath(top.strip())) != made_key


# v1's repair prefix for the scope check (rules/harness_tooling.md): only for repairing the guard or its runner.
SCOPE_BYPASS = "HARNESS_ALLOW_UNSTAMPED_HARNESS"


def _names_outside(word, cwd, root):
    """True when a piece of `word`'s text, split as v1 split it, resolves outside the repository `root`."""
    top = os.path.normcase(os.path.abspath(root))
    for piece in word.split():
        piece = piece.strip("$()`'\"")
        if piece and not piece.startswith("-"):
            path = os.path.normcase(os.path.abspath(resolve_cd(cwd, piece)))
            if path != top and not path.startswith(top.rstrip(os.sep) + os.sep):
                return True
    return False


def scope_verdict(sub, rest, cwd, git_env=None, inline_env=None, refs_pending=()):
    """The deny for a commit-like call whose published paths git cannot list, or None: no loss check can judge
    content it cannot name, so an unverifiable scope fails closed (v1 `git_guardrails.harness_verdict`'s denies). A
    bare merge, cherry-pick or revert of a ref an earlier step of the same command creates passes: that ref does not
    exist yet when the hook reads the command, and its content was judged where it was committed. A pathspec the
    shell still has to expand passes, as in v1, unless its text names a path outside the repository (v1 26a: v1 listed
    the word-split text and git refused that path)."""
    if bypass_declared(inline_env, SCOPE_BYPASS):
        return None
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if sub == "commit" and root is not None:
        outside = next((word for word in parse_commit_args(rest)[2]
                        if ("$" in word or "`" in word) and _names_outside(word, cwd, root.strip())), None)
        if outside:
            return ("BLOCKED harness commit — the pathspec `%s` is expanded by the shell after the hook reads it, "
                    "and its text names a path outside the repository; cannot verify commit scope." % outside
                    + unexpanded_hint(outside))
    if root is None:
        if cwd and "$" not in cwd and not os.path.isdir(cwd):
            # `cd <dir>` into a worktree an earlier step of this command creates: only a commit that publishes the
            # index is denied, since a bare merge, cherry-pick or revert re-commits content judged where it was made.
            if not publishes_index(sub, rest):
                return None
            return ("BLOCKED harness commit — the directory `%s` does not exist yet when the hook reads the "
                    "command, so the commit scope cannot be verified; run the step that creates it in its own "
                    "call, then commit." % cwd)
        return ("BLOCKED harness commit — `git rev-parse --show-toplevel` failed; cannot "
                "verify commit scope." + unexpanded_hint(cwd))
    paths, failed_cmd = publication(sub, rest, cwd, git_env).selection
    if (paths is None and not publishes_index(sub, rest) and ref_names_created(refs_pending, rest)
            and not ref_resolves(rest, cwd, git_env)):
        return None
    if paths is None:
        return ("BLOCKED harness commit — `git %s` failed; cannot verify commit scope." % failed_cmd
                + unexpanded_hint(failed_cmd))
    return None


# --- The harness push close (v1 `git_guardrails.harness_push_verdict`, a floor guard) -----------------------------
def _push_ranges(rest, root, git_env, push_range, targets=None):
    """Local evidence for each published source; no network calls in the hook. A `targets` list receives each
    range's (remote, destination branch), in range order."""
    if "--dry-run" in rest or "-n" in rest or "--delete" in rest or "-d" in rest:
        return []
    operands, remote, option = [], None, None
    for arg in rest:
        if option:
            if option == "--repo":
                remote = arg
            option = None
        elif arg in ("--repo", "--receive-pack", "--exec", "--push-option", "-o"):
            option = arg
        elif arg.startswith("--repo="):
            remote = arg.partition("=")[2]
        elif not arg.startswith("-"):
            operands.append(arg)
    branch = (run_git(["branch", "--show-current"], root, git_env) or "").strip()

    def config(key):
        return (run_git(["config", "--get", key], root, git_env) or "").strip()
    if remote is None:
        remote = operands.pop(0) if operands else push_range.push_destination(root, git_env)[0]
    specs, args = [], iter(operands)
    for arg in args:
        if arg == "tag":
            name = next(args, None)
            if name is None:
                raise RuntimeError("missing push tag")
            specs.append("refs/tags/" + name)
        else:
            specs.append(arg)
    if "--all" in rest or "--mirror" in rest:
        namespace = "refs" if "--mirror" in rest else "refs/heads"
        specs += (run_git(["for-each-ref", "--format=%(refname)", namespace], root, git_env) or "").splitlines()
    if "--tags" in rest:
        specs += (run_git(["for-each-ref", "--format=%(refname)", "refs/tags"], root, git_env) or "").splitlines()
    if not specs:
        specs = (run_git(["config", "--get-all", "remote." + remote + ".push"], root, git_env) or "").splitlines()
    if not specs:
        mode = config("push.default") or "simple"
        if mode == "matching":
            specs = [":"]
        elif mode == "nothing":
            return []
        else:
            target = config("branch." + branch + ".merge") if mode in ("simple", "upstream") else ""
            specs = ["HEAD:" + (target or "refs/heads/" + branch)]
    expanded = []
    for spec in specs:
        if spec.lstrip("+") == ":":
            heads = (run_git(["for-each-ref", "--format=%(refname)", "refs/heads"], root, git_env) or "").splitlines()
            expanded += [ref for ref in heads if run_git(["rev-parse", "--verify", "refs/remotes/" + remote + "/"
                         + ref.removeprefix("refs/heads/")], root, git_env)]
        else:
            expanded.append(spec)
    ranges = []
    for spec in expanded:
        source, _, target = spec.lstrip("+").partition(":")
        if not source:
            continue
        rev = run_git(["rev-parse", "--verify", source + "^{commit}"], root, git_env)
        if not rev:
            raise RuntimeError("cannot resolve push source " + source)
        target = (target or source).removeprefix("refs/heads/")
        if target == "HEAD":
            target = (run_git(["branch", "--show-current"], root, git_env) or "").strip()
        ref = "refs/remotes/" + remote + "/" + target
        base = push_range.close_base(root, push_ref=ref, head=rev.strip(), git_env=git_env, remote=remote)
        ranges.append((base, rev.strip()))
        if targets is not None:
            targets.append((remote, target))
    return ranges


def _adaptation_str(key):
    """The project's `adaptation.json` string `key`; empty when it is absent or not a string (no declaration)."""
    value = project_data(key)
    return value if isinstance(value, str) else ""


def harness_push_verdict(rest, cwd, inline_env=None, git_env=None):
    """The deny for `git push <rest>`, or None. Where the project declares no `adaptation.json` `main_check`, each
    outgoing harness ref must be the commit a green close proved, with its base and paths; a commit landed after the
    close stays local, and the push names the proven commit. A push to a branch under `land_prefix` passes: the land
    runner integrates it and the close is not its job. Where `main_check` is declared the default branch is proved
    after the push, so no close is required; a push source the guard cannot resolve is still denied.
    `approving_gates.py` still approves only a push of a stored green close's commit."""
    if bypass_declared(inline_env, SCOPE_BYPASS):
        return None
    land, declared = _adaptation_str("land_prefix"), bool(_adaptation_str("main_check"))
    if land:
        next_step = ("BLOCKED harness push (rules/harness_tooling.md) — %%s; this project has a land runner: "
                     "`git push --recurse-submodules=check origin HEAD:%s<session id's first 8 chars>`; the runner "
                     "lands the branch." % land)
    else:
        next_step = ("BLOCKED harness push (rules/harness_tooling.md) — %s; run `python3 "
                     ".claude/scripts/harness_tests.py --close` in the background, then push the commit it proves.")
    try:
        import _push_range as push_range
    except Exception:  # noqa: BLE001 - without the range reader no push is proved
        return next_step % "push-range machinery unavailable"
    root = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if not root:
        return next_step % "cannot resolve the push checkout"
    root = root.strip()
    targets = []
    try:
        ranges = _push_ranges(rest, root, git_env, push_range, targets)
        outgoing = [(base, rev, push_range.close_paths(root, base, rev, remote, git_env))
                    for (base, rev), (remote, _target) in zip(ranges, targets)]
    except RuntimeError as exc:
        source = str(exc).removeprefix("cannot resolve push source ")
        if source != str(exc):
            return ("BLOCKED harness push (rules/harness_tooling.md) — cannot resolve push source `%s`; the guard "
                    "does not expand shell variables, so push the commit by its literal hash or ref." % source)
        return next_step % "cannot resolve the outgoing harness range"
    except (OSError, ValueError, subprocess.CalledProcessError):
        return next_step % "cannot resolve the outgoing harness range"
    if declared:
        return None
    harness = [(base, rev, paths, dest) for (base, rev, paths), dest in zip(outgoing, targets)
               if paths and not (land and dest[1].removeprefix("refs/heads/").startswith(land))]
    if not harness:
        return None
    close = push_range.green_close(root)
    proven = close.get("head") if close else None
    for base, rev, paths, (remote, target) in harness:
        if proven and rev != proven:
            return ("BLOCKED harness push (rules/harness_tooling.md) — the green close proved %s, not the pushed "
                    "%s; push the proven commit: `git push %s %s:%s`, or check that ref out and run `python3 "
                    ".claude/scripts/harness_tests.py --close` in the background there."
                    % (proven[:12], rev[:12], remote, proven, target))
        if not push_range.close_covers(close, base, rev, paths, root, remote, git_env):
            return next_step % ("no green close result covers %s, its outgoing base and paths" % rev[:12])
    return None


def _names_git(command):
    """True when a word of `command`, or of a body it runs, is git as the shell runs it (`_shell_lex.base`: any case,
    `.exe`, a path, quotes and escapes removed). The cheap gate before the git parse: `GIT`, `g\\it` and `g"i"t`
    run git too."""
    return any(base(word.value) == "git" for _kind, _text, segments, _depth in walk(command)
               for segment in segments for word in segment.words)


def git_verdict(command, cwd, transcript_path=None, session_id=None):
    """The first git loss in `command`, or None. A call inside a shell launcher body (`bash -c`, `eval`, ...)
    borrows no worktree ownership; its resolved repository facts still count."""
    if not _names_git(command):
        return None
    cwd = cwd or "."
    born = set()  # repos this same command creates with `git init`
    initialized = set()  # existing repos outside this project the command runs `git init` on
    project_root = _project_root()
    index_write = None  # the latest git index write seen so far in this command
    ref_makers = {}  # repo -> ref names an earlier invocation of this command creates there
    for call in git_invocations(command, cwd):
        args = [call.sub] + call.rest
        target, git_env = call.cwd, call.git_env
        launched = call.body_path is None or len(call.body_path) > 0
        owner_transcript = None if launched else transcript_path
        if target is None:
            message = verdict(args, None, git_env, None)
            if not message and _unproved_publish(args) and not _keeps_local_changes(call.sub, call.rest):
                message = UNPROVED_TARGET % call.sub
            if message:
                return message
            if writes_index(args):
                index_write = "git " + args[0]
            continue

        def expand(token):
            return expand_literal(token, call.assignments) or token
        key = os.path.normcase(os.path.abspath(target))
        if args[0] == "init":
            made, made_key, fresh = _init_target(args[1:], target, git_env, expand)
            if fresh:
                born.add(made_key)
            elif not in_project(made, project_root, git_env):
                initialized.add(made_key)
            continue
        judged_args = [expand(arg) for arg in args] if args[0] in ('checkout', 'restore') else args
        message = verdict(judged_args, target, git_env, owner_transcript)
        if not message and args[0] == "push":
            message = harness_push_verdict(args[1:], target, call.inline_env, git_env)
        if index_write and not message and key not in born and key not in initialized \
                and publishes_index(args[0], args[1:]):
            unhooked = _unhooked_publish(args, target, git_env, command)
            if unhooked:
                message = SAME_COMMAND_WRITE % (index_write, unhooked, index_write)
        if writes_index(args):
            index_write = "git " + args[0]
        if (not message and (args[0] in ("commit", "merge", "cherry-pick", "revert")
                             or publishes_index(args[0], args[1:])) and key not in born):
            git_env, problem = resolve_index(git_env, target)
            if problem:
                message = "BLOCKED commit (memory gotcha_concurrent_session_hazards #16) — %s." % problem
            elif args[0] == "commit":
                message = commit_verdict(args[1:], target, git_env, session_id)
            if not message:
                scoped = [expand(arg) for arg in args] if args[0] == "commit" else args
                message = scope_verdict(scoped[0], scoped[1:], target, git_env, call.inline_env,
                                        ref_makers.get(key, ()))
        if args[0] in REF_MAKERS:
            ref_makers.setdefault(key, set()).update(refs_created(args[0], args[1:]))
        if message:
            return message
    return None


# --- Entry points ---------------------------------------------------------------------------------------------------
def _output(decision=None, reason=None, updated=None):
    hso = {"hookEventName": "PreToolUse"}
    if decision:
        hso.update(permissionDecision=decision, permissionDecisionReason=reason)
    if updated is not None:
        hso["updatedInput"] = updated
    return {"hookSpecificOutput": hso}


def check(payload):
    """The floor's verdict on one PreToolUse payload: None (allow), a deny, or a rewrite of an absent forced delete."""
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") not in ("Bash", "PowerShell") or not isinstance(tool_input, dict) \
            or not isinstance(tool_input.get("command"), str):
        return None
    command, cwd, transcript = tool_input["command"], payload.get("cwd"), payload.get("transcript_path")
    if payload.get("tool_name") == "PowerShell":
        command = PowerShellText(command)  # its here-strings are data unless a shell reads them
    blocked, message = check_bash_command(command, cwd, transcript)
    if blocked:
        rewritten = None
        if payload.get("tool_name") == "Bash":
            rewritten = rewrite_absent_forced_deletes(command, cwd)
        if rewritten is None or check_bash_command(rewritten, cwd, transcript)[0]:
            return _output("deny", message)
        command = rewritten
    why = git_verdict(command, cwd, transcript, payload.get("session_id"))
    if why:
        return _output("deny", why)
    return _output(updated=dict(tool_input, command=str(command))) if blocked else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--commit-msg"]:
        try:
            why = commit_msg_check(argv[1] if len(argv) > 1 else "")
        except Exception as exc:  # a floor check: a fault refuses the commit
            print("BLOCKED commit: the commit-msg peer-revert check crashed (%s: %s). Repair "
                  ".claude/hooks/dangerous_shell_guard.py commit_msg_check until .claude/tests/floor/"
                  "test_floor_commit_msg.py passes, then commit again." % (type(exc).__name__, exc), file=sys.stderr)
            return 1
        if why:
            print(why, file=sys.stderr)
            return 1
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = None
    out = check(payload) if isinstance(payload, dict) else None
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
