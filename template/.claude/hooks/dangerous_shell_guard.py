#!/usr/bin/env python3
"""The destructive-command check of the loss floor (class floor, T1). `_floor.py` runs `check(payload)` for every
Bash and PowerShell call; a fault denies the call.

Blocks, each judged on the resolved target and never on the command's wording:
- Recursive/forced deletes and directory removal (rm -r/-rf, del /s, rd /s, rmdir /s, PowerShell Remove-Item
  -Recurse and its aliases) and drive format commands. Allowed: a target that is a regenerable cache inside the
  project, a path below the system temp directory or `~/.claude/plugins/cache/` holding no git repository, this session's own
  `.claude/scratch/<session id>` output, a pushed regenerable clone, a scratch folder holding no git repository and
  no registered worktree, a folder a proof planted under `.claude/tmp`, a retired worktree (nothing git lacks,
  every commit on a remote; its ignored files go only when this session created it), a `NAME=$(mktemp -d)`
  variable the same command owns, or a folder a flagless `mkdir` of this session created, holding no git
  repository. A chained second delete, a glob in a directory part, `~`, `%`, an unresolvable target, a target a
  live peer session works in, or another session's id folder disqualifies it. A forced `rm` whose every target is
  a literal path proven absent inside `.claude/scratch/` removes nothing and is rewritten to `true`.
- Git discards (`verdict`): `reset --hard` outside a clean worktree this session created, `clean -f`, a checkout or
  restore that overwrites an edit git holds nowhere else (outside this session's own worktrees), force-push,
  `branch -D` of a branch no other ref holds, `stash drop` of an entry no ref holds, `stash clear`,
  `reflog expire`, `gc --prune` and `filter-*`. A call inside a shell launcher body borrows no worktree ownership.
- Peer losses at commit: a private index over a moved stage base (`stage_record_loss`), an index whose blobs
  revert a peer's newer commit (`stale_loss`), an amend of a HEAD this session did not make (`amend_loss`), an
  index the floor cannot read (#16), a publish whose repository cannot be proved (`UNPROVED_TARGET`), and a
  commit an index write precedes in the same command where git's commit-msg hook cannot re-check it
  (`SAME_COMMAND_WRITE`, `_unhooked_publish`).

Quoted spans and heredoc bodies are data, never commands. `git` parsing goes through `_git_commit.py`.

Second caller: `.claude/githooks/commit-msg` runs `dangerous_shell_guard.py --commit-msg <file>`, which re-runs
the stage-record and peer-revert checks against the index git is about to write; exit 1 refuses with one line.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(os.path.dirname(_HOOKS_DIR), "tools"), _HOOKS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)
from _command_text import executable_text, executable_spans, command_words, _REDIRECTION  # noqa: E402
from _regenerable_clone import (ANY_SESSION_ID, empty_leftover, is_regenerable_clone,  # noqa: E402
                                is_plant_exhaust, is_proof_plant, is_retired_worktree, lossless_orphan_worktree,
                                regenerable_segments, session_created_worktree, worktree_unstored)
import _git_commit  # noqa: E402
if _git_commit.executable_index is None or _git_commit.command_substitutions is None:
    raise ImportError("_shell_lex failed to import: %s" % _git_commit.LEXER_ERROR)
from _git_commit import (literal_assignments, expand_literal, resolve_cd, segments,  # noqa: E402
                         git_invocations, run_git, resolve_index, in_project, parse_commit_args, _pathspec_file,
                         _SHORT_VALUE_LETTERS)
from _commit_scope import publishes_index, writes_index  # noqa: E402
from _checkout_root import worktree_chain  # noqa: E402
import _git_run  # noqa: E402
from _transcript_calls import Call, Result, session_events  # noqa: E402
from index_commit import stage_record_base, stage_record_path  # noqa: E402


# Dangerous command patterns — applied to BOTH the Bash and PowerShell tools
# (PowerShell text also reaches Bash via `powershell -Command "..."`).
DANGEROUS_BASH_PATTERNS = [
    # Windows dangerous commands
    (r'del\s+/[sq]', 'BLOCKED: Dangerous recursive delete (del /s or /q)'),
    (r'rd\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rd /s)'),
    (r'rmdir\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rmdir /s)'),
    (r'\bformat\s+[a-zA-Z]:', 'BLOCKED: Format drive command detected'),
    # PowerShell recursive delete — Remove-Item and its delete aliases with
    # -Recurse (PS requires at least -recurse-unambiguous spelling; -r alone
    # errors as ambiguous, so matching the full word keeps false positives low).
    (r'(?i)\b(remove-item|ri|del|erase)\b[^|;\n]*\s-recurse\b', 'BLOCKED: Dangerous recursive delete (Remove-Item -Recurse)'),
    # Block rm with any recursive flag cluster. Plain "rm -f <file>" is a safe
    # single-file delete (force = suppress prompt) and is commonly used for temp
    # cleanup, so it is NOT blocked. Recursion requires -r / -R / --recursive —
    # that is what we actually need to block. `git rm` stays allowed via the
    # lookbehind (version-controlled, recoverable).
    # The intermediate (?:[^\s]+\s+)* span catches separated-flag cases like
    # `rm -f -r dir/` or `rm foo -r` that a stricter prefix match would miss.
    # The token scan stops at a command separator. Without that bound it runs to end-of-line and
    # reads a LATER command's flag as this delete's own -- `rm -f a.txt; tool -R out.json` denied
    # five times in one session, every one a safe delete followed by an unrelated -R.
    # Regression: .claude/tests/test_dangerous_shell_guard_rm_scope.py
    (r'(?<!git\s)\brm\s+(?:[^\s;|&\n]+\s+)*-[a-zA-Z]*[rR]', 'BLOCKED: Dangerous recursive delete (rm -r / -R / -rf)'),
    (r'(?<!git\s)\brm\s+--recursive\b', 'BLOCKED: Dangerous recursive delete (rm --recursive)'),
]


# Regenerable caches a recursive delete may target without asking are `_regenerable_clone.regenerable_segments`:
# harness caches and logs, the semantic-search index, engine and test output, Python bytecode caches. Matched by PATH SEGMENT, never substring:
# the old `\.claude[\\/](?:\.cache|logs)\b` regex passed `.claude/logs-old` and
# `.claude/.cache/../..` (the repo root). Evidence folders (`.claude/scratch`) and checkouts
# (`.claude/worktrees`) are gitignored but are NOT caches, so they stay blocked unless
# `_regenerable_clone` proves the target loses nothing.
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


def _cleanup_tokens(raw: str) -> list[str] | None:
    """Shell tokens of the unblanked command: quotes removed, backslashes kept (Windows paths),
    redirections and their targets dropped (`_command_text`'s grammar), since they are plumbing,
    never arguments. None when the quoting does not parse, which never allows anything."""
    import shlex
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    kept, target_next = [], False
    for token in tokens:
        if target_next:
            target_next = False
            continue
        redirect = _REDIRECTION.match(token)
        if redirect and redirect.end() > 0:
            # A bare operator (`>`, `2>`) takes the next token as its target; `2>&1` and
            # `>/dev/null` carry theirs.
            target_next = redirect.end() == len(token) and '&' not in token.lstrip('0123456789')
            continue
        kept.append(token)
    return kept


def _is_cache_path(token: str, cwd: str | None = None) -> bool:
    """True when `token` is a literal path at or under an allowlisted cache segment and resolves
    inside the project root."""
    if _UNSAFE_PATH_CHARS.search(token):
        return False
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return False
    return regenerable_segments(parts) and _inside_root(token, cwd)


# A quoted FLAG is still a flag: `rm "-r" build/` reaches the shell as `rm -r build/`, but
# blanking quoted spans erases it and the guard reports clean. Unwrapping only quote-wrapped flag
# tokens keeps the blanking that stops `grep "rm -rf"` from being read as a delete.
_QUOTED_FLAG = re.compile(r"""(['"])(-{1,2}[A-Za-z][A-Za-z-]*)\1""")


def _unwrap_quoted_flags(command: str) -> str:
    return _QUOTED_FLAG.sub(r"\2", command)


def _strip_quoted(command: str) -> str:
    """Blank single/double-quoted spans so a dangerous-looking pattern that is merely a
    QUOTED argument (e.g. `grep "rm -rf"`, an echo, a commit body) isn't mistaken for a
    real command. A genuine `rm -rf "<target>"` still trips the guard — only the quoted
    target blanks; the unquoted `rm -rf` remains to match. A `$(...)` or backtick span
    inside double quotes still runs, so it stays, and quoting restarts inside it."""
    return _strip_span(command, 0, "")[0]


def _strip_span(s: str, i: int, close: str) -> tuple[str, int]:
    """Blank quoted text from `i` until the unquoted `close` (")" or "`"; "" = end of input).
    Returns the kept text and the index just past `close`."""
    out, depth, cases = [], 0, 0
    while i < len(s):
        ch = s[i]
        word = re.match(r"(case|esac)(?![\w-])", s[i:]) if ch in "ce" else None
        if word and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in "_-")):
            cases += 1 if word.group(1) == "case" else -1 if cases else 0
            out.append(word.group(1))
            i += 4
        elif ch == "\\":
            out.append(s[i:i + 2])
            i += 2
        elif ch == ")" and cases:
            out.append(ch)  # a case arm's pattern closer, not the substitution's
            i += 1
        elif close == ")" and ch == ")" and depth == 0:
            return "".join(out), i + 1
        elif close == "`" and ch == "`":
            return "".join(out), i + 1
        elif s.startswith("$'", i):
            j = i + 2  # ANSI-C quoting: a backslash escapes the next char, including a quote
            while j < len(s) and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            i = j + 1
            out.append(" ")
        elif ch == "'":
            end = s.find("'", i + 1)
            i = len(s) if end < 0 else end + 1
            out.append(" ")
        elif ch == '"':
            kept, i = _strip_double(s, i + 1)
            out.append(" " + kept + " ")
        elif s.startswith("$(", i):
            inner, i = _strip_span(s, i + 2, ")")
            out.append("$(" + inner + ")")
        elif ch == "`":
            inner, i = _strip_span(s, i + 1, "`")
            out.append("`" + inner + "`")
        else:
            depth += (ch == "(") - (ch == ")")
            out.append(ch)
            i += 1
    return "".join(out), i


def _strip_double(s: str, i: int) -> tuple[str, int]:
    """Inside a double-quoted string starting at `i`: drop literal text, keep each command
    substitution. Returns the kept substitutions and the index just past the closing quote."""
    kept = []
    while i < len(s) and s[i] != '"':
        if s[i] == "\\":
            i += 2
        elif s.startswith("$(", i):
            inner, i = _strip_span(s, i + 2, ")")
            kept.append("$(" + inner + ")")
        elif s[i] == "`":
            inner, i = _strip_span(s, i + 1, "`")
            kept.append("`" + inner + "`")
        else:
            i += 1
    return " ".join(kept), i + 1


_git_memo: dict | None = None  # one `check_bash_command` call's git answers, so each of its targets reuses them


def _memoized(key, compute):
    if _git_memo is None:
        return compute()
    if key not in _git_memo:
        _git_memo[key] = compute()
    return _git_memo[key]


def _registered_worktrees() -> list[str]:
    """Normalized paths of the project's registered worktrees; an unreadable list holds the project root,
    so every scratch target counts as holding one and the delete stays denied."""
    root = _project_root()
    return _memoized(('worktrees', root), lambda: _list_registered_worktrees(root))


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


def _scratch_bases(root: str, cwd: str | None) -> list[str]:
    """The checkouts whose `.claude/scratch` this delete may judge: the project root, plus the git toplevel
    of `cwd` when that toplevel is a registered linked worktree (its `.git` is a file) of this repository
    (a delegate removing its own scratch). An unreadable toplevel or an unregistered checkout adds nothing. A
    toplevel that is the project root is already the first base, so the registered-worktree list (a git process that
    stats every registered worktree) is read only for a different one."""
    bases = [root]
    if cwd and os.path.isdir(cwd):
        top = _memoized(('toplevel', cwd), lambda: _git_toplevel(cwd))
        if top and _norm(top) != _norm(root) and os.path.isfile(os.path.join(top, '.git')) \
                and _norm(top) in _registered_worktrees():
            bases.append(top)
    return bases


def _git_toplevel(cwd: str) -> str:
    try:
        out = _git_run.run(['-C', cwd, 'rev-parse', '--show-toplevel'], timeout=20)
    except OSError:
        return ''
    return out.text.strip() if out.returncode == 0 else ''


def _under_a_scratch_folder(path: str) -> bool:
    """`path` sits below some `.claude/scratch`: the only place a base `_scratch_bases` names can match, so a
    target elsewhere skips the git call that finds the cwd's toplevel."""
    parts = path.replace('\\', '/').split('/')
    return any(parts[i:i + 2] == ['.claude', 'scratch'] and i + 2 < len(parts) for i in range(len(parts) - 2))


def _is_regenerable_target(token: str, cwd: str | None, transcript_path: str | None = None,
                           reasons: list[str] | None = None) -> bool:
    """A pushed clone, a lossless orphaned worktree directory of this repository wherever it sits (needs
    no approval: nothing in it is unheld by git or a non-regenerable ignored file), an empty leftover directory under scratch (no file,
    no `.git`), a scratch entry holding no git repository or worktree, or a retired worktree (ignored files
    go only when this session created it); optionally explain failure."""
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
            for base in _scratch_bases(root, cwd)):
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
    return is_retired_worktree(token, cwd or root, root, allow_ignored=own, reasons=reasons)


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
            or _is_regenerable_target(target, here, transcript_path)
            or (here is not None and _is_regenerable_resolved(target, here))
            or _session_made_dir(target, here, transcript_path))


def _is_safe_ephemeral_cleanup(scan: str, raw: str, cwd: str | None = None,
                               transcript_path: str | None = None) -> bool:
    """True only for a single (non-chained) recursive delete whose every path argument satisfies
    `_deletable_by_fact`: a regenerable cache inside the project, this session's own scratch output, a
    target git cannot lose, or a folder this session's own mkdir made.

    Control operators are judged on the quote-blanked `scan`, so a chained second delete cannot
    ride along. Path arguments come from shell-tokenizing the unblanked `raw`, so a quoted literal
    target is judged as the path it names. Any other path token disqualifies the command; the check
    can only over-block, never green-light a broad delete."""
    if re.search(r'&&|\|\||;|\||`|\$\(', scan):
        return False
    tokens = _cleanup_tokens(raw)
    if tokens is None:
        return False
    paths = [t for t in tokens
             if not t.startswith('-')
             and t.lower() not in ('rm', 'del', 'erase', 'ri', 'remove-item')]
    return bool(paths) and all(_deletable_by_fact(p, cwd, transcript_path) for p in paths)


def _forces_removal(args: list[str]) -> bool:
    """True when a flag stops the delete reporting an absent operand, so deleting nothing exits 0
    (`rm -f`, `-rf`, `-fr`, `--force`). Only then does `true` stand in for the segment."""
    return any(a.lower() == '--force' or (re.fullmatch(r'-[A-Za-z]+', a) and 'f' in a.lower())
               for a in args)


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
    """`command` with every forced recursive delete whose targets are all proven-absent paths under
    this project's `.claude/scratch/` replaced by `true`, or None when any delete is not provable.

    `rm -rf <absent>` exits 0 and removes nothing, so the setup idiom `rm -rf target; mkdir target`
    needs no owner approval for an absent target. The segment is rewritten rather than approved: a
    bare allow would also cover whatever appears at that path between this check and the run. A
    command with any other recursive delete -- an existing path, a glob, an unresolved variable, a
    `..`, a non-`rm` delete, or a segment the parser cannot isolate -- returns None and keeps its
    normal denial.
    """
    spans = list(executable_spans(command))
    if not spans:
        return None
    assignments = literal_assignments(command)
    here = cwd or _project_root()
    elisions, found = [], False
    for start, end, segment in spans:
        delete_shape = any(re.search(p, _strip_quoted(segment), re.IGNORECASE)
                           for p in _RECURSIVE_DELETE_PATTERNS)
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
        if here is None or words[0].lower() != "rm" or not _forces_removal(words[1:]):
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
# literal path never carries. Everything else surrounding it — cd, a subshell pipeline that reads
# the transcript, a printf | hook | grep | cut probe — is unrelated chaining and stays unrestricted;
# only each delete-shaped segment's own targets and the assigning variable are judged.
_DELETE_CMD_NAMES = frozenset({'rm', 'del', 'erase', 'ri', 'remove-item'})
_CHAIN_SEP_RE = re.compile(r'&&|\|\||;|\|')
_VAR_TOKEN_RE = re.compile(r'^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$')
# The value must END at the substitution: a suffix (`$(mktemp -d)/..`, `/*`) points outside the directory.
_MKTEMP_ASSIGN_VALUE_RE = re.compile(r'\$\(\s*mktemp\s+-d(?:\s+-t\s+[^\s)/]+)?\s*\)(?=[\s;&|]|$)')
_RECURSIVE_DELETE_PATTERNS = [p for p, m in DANGEROUS_BASH_PATTERNS if 'recursive' in m.lower()]


def _blank_quotes_samelen(command: str) -> str:
    """Like `_strip_quoted`, but each quoted span is replaced by spaces of the SAME length, so
    positions found in the blanked text still index correctly into the original string."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", lambda m: ' ' * len(m.group(0)), command)


def _split_top_level(raw: str) -> list[str]:
    """`raw` cut at `&&`, `||`, `;` and `|` that sit outside any quoted span — a `|` inside a
    `$(...)` subshell argument (e.g. `T=$(ls ... | head -1)`) is quoted-blanked away first only
    for locating the cut points, then sliced from the real `raw`, so quotes stay intact."""
    blanked = _blank_quotes_samelen(raw)
    segments = []
    pos = 0
    for m in _CHAIN_SEP_RE.finditer(blanked):
        segments.append(raw[pos:m.start()])
        pos = m.end()
    segments.append(raw[pos:])
    return segments


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


def _is_owned_temp_cleanup(raw: str) -> bool:
    """True when EVERY delete-shaped top-level segment in `raw` targets exactly one variable,
    all of whose targets in that segment are `"$NAME"`, `$NAME` or `${NAME}`, where `_is_mktemp_owned`
    holds for NAME. One unowned or mixed-target delete segment anywhere disqualifies the whole
    command — chaining a second, unrelated delete alongside a legitimate one must not ride along."""
    # A segment is judged when any recursive-delete pattern matches it, wherever the delete sits:
    # a delete inside `$(...)`, backticks, or behind `sudo` does not head its segment and so
    # disqualifies the whole command.
    delete_segments = []
    for segment in _split_top_level(raw):
        if not any(re.search(p, _strip_quoted(segment), re.IGNORECASE) for p in _RECURSIVE_DELETE_PATTERNS):
            continue
        tokens = _cleanup_tokens(segment)
        if not tokens or tokens[0].lower() not in _DELETE_CMD_NAMES:
            return False
        delete_segments.append(tokens)
    if not delete_segments:
        return False
    for tokens in delete_segments:
        paths = [t for t in tokens[1:] if not t.startswith('-')]
        if not paths:
            return False
        names = set()
        for p in paths:
            m = _VAR_TOKEN_RE.match(p)
            if not m:
                return False
            names.add(m.group(1))
        if len(names) != 1 or not _is_mktemp_owned(names.pop(), raw):
            return False
    return True


# Resolved-target allow: each delete segment's targets resolve through literal paths, variables the
# same command assigns once, and the tracked `cd`; every one must land strictly below the system temp
# directory, in a named subdirectory of `.claude/scratch`, or in a regenerable cache of this checkout
# or one of its `.claude/worktrees/*` checkouts. The classifier still judges what passes here.
_SEPARATORS = re.compile(r'&&|\|\||[;|&\n]')
_GLOB_CHARS = re.compile(r'[*?\[\]]')


def _segments_with_quotes(raw: str) -> list[str]:
    blanked = _blank_quotes_samelen(raw)
    out, pos = [], 0
    for m in _SEPARATORS.finditer(blanked):
        out.append(raw[pos:m.start()].strip())
        pos = m.end()
    out.append(raw[pos:].strip())
    return [s for s in out if s]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _strictly_under(path: str, base: str) -> bool:
    try:
        return path != base and os.path.commonpath([base, path]) == base
    except ValueError:  # different drives
        return False


def _checkout_rel_parts(path: str) -> list[str] | None:
    """Path parts relative to the checkout holding `path`: the primary root, or the
    `.claude/worktrees/<name>` checkout nested inside it (KFM #42). None outside the repo."""
    root = _norm(_project_root())
    if not _strictly_under(path, root):
        return None
    parts = os.path.relpath(path, root).replace("\\", "/").split("/")
    if parts[:2] == [".claude", "worktrees"]:
        return parts[3:] if len(parts) > 3 else None
    return parts


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
    if _strictly_under(path, _norm(tempfile.gettempdir())):
        # D-TT (owner, 2026-11-23): a temp tree goes with any repository in it, never the protected checkout or a
        # worktree registered to it.
        return not any(wt == path or _strictly_under(wt, path)
                       for wt in [_norm(_project_root())] + _registered_worktrees())
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
    text = executable_text(command)
    assignments = literal_assignments(text, cwd)
    here, out = cwd, []
    for segment in segments(text):
        tokens = _cleanup_tokens(segment)
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


def _is_resolved_safe_cleanup(raw: str, cwd: str | None, accept) -> bool:
    """True when every recursive-delete segment in `raw` is a plain delete command whose targets
    all satisfy `accept(target, base directory)`.
    A delete that does not head its segment (inside `$(...)`, backticks, behind `sudo`/`xargs`), a
    glob in a directory part or a `cd`, `~`, `%` or any unresolvable target disqualifies the whole
    command (KFM #50). A glob in the last part is judged by its directory."""
    assignments = literal_assignments(raw)
    here = cwd or _project_root()
    found = False
    for segment in _segments_with_quotes(raw):
        tokens = _cleanup_tokens(segment)
        if tokens and tokens[0] == "cd" and len(tokens) == 2:
            moved = expand_literal(tokens[1], assignments)
            here = (resolve_cd(here, moved)
                    if here is not None and moved and not _GLOB_CHARS.search(moved) else None)
            continue
        if not any(re.search(p, _strip_quoted(segment), re.IGNORECASE) for p in _RECURSIVE_DELETE_PATTERNS):
            continue
        if not tokens or tokens[0].lower() not in _DELETE_CMD_NAMES or '`' in segment or '$(' in segment:
            return False
        targets = [t for t in tokens[1:] if not t.startswith('-')]
        if not targets:
            return False
        for token in targets:
            value = expand_literal(token, assignments)
            if value is None or here is None or value.startswith('~') or '%' in value:
                return False
            head, tail = os.path.split(value.replace("\\", "/").rstrip("/"))
            if _GLOB_CHARS.search(head):
                return False
            if not accept(head if _GLOB_CHARS.search(tail) else value, here):
                return False
        found = True
    return found


# `git`'s global options sit BETWEEN `git` and the subcommand (`git -C <path> rm ...`),
# which defeats the fixed-width `(?<!git\s)` lookbehind that exempts `git rm` below —
# the four chars before `rm` are the tail of <path>, not "git ". Collapsing the globals
# first restores the exemption as written, with no change to any pattern.
# Only `git rm` is affected: destructive git subcommands are parsed properly by the
# sibling `git_guardrails.py`, which tokenizes and skips these same globals.
_GIT_GLOBALS_RE = re.compile(
    r'\bgit\s+(?:-C\s+\S+|-c\s+\S+|--git-dir=\S+|--work-tree=\S+|--namespace=\S+)\s+'
)


def _normalize_git_globals(scan: str) -> str:
    """Rewrite `git -C <path> <sub>` to `git <sub>` so subcommand-anchored lookbehinds
    match. Purely a matching aid — never consulted for what the command actually does."""
    prev = None
    while prev != scan:                       # loop: globals may repeat (`git -C x -c k=v rm`)
        prev, scan = scan, _GIT_GLOBALS_RE.sub('git ', scan)
    return scan


def _delete_reason(raw: str, cwd: str | None, transcript_path: str | None) -> str:
    """Why the first worktree, scratch or worktree-shaped (a `.git` file) target this delete names is not
    removable, or ''."""
    assignments = literal_assignments(raw)
    here = cwd or _project_root()
    home = _norm(os.path.join(_project_root(), '.claude', 'worktrees'))
    scratch = _norm(os.path.join(_project_root(), '.claude', 'scratch'))
    for segment in _segments_with_quotes(raw):
        tokens = _cleanup_tokens(segment)
        if tokens and tokens[0] == 'cd' and len(tokens) == 2:
            moved = expand_literal(tokens[1], assignments)
            here = resolve_cd(here, moved) if here and moved else None
            continue
        if not tokens or tokens[0].lower() not in _DELETE_CMD_NAMES or here is None:
            continue
        for token in tokens[1:]:
            if token.startswith('-'):
                continue
            value = expand_literal(token, assignments)
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
    return ''


def check_bash_command(command: str, cwd: str | None = None,
                       transcript_path: str | None = None) -> tuple[bool, str]:
    """Check bash command for dangerous patterns. Returns (blocked, message)."""
    global _git_memo
    _git_memo = {}
    try:
        return _check_bash_command(command, cwd, transcript_path)
    finally:
        _git_memo = None


def _check_bash_command(command: str, cwd: str | None, transcript_path: str | None) -> tuple[bool, str]:
    # A heredoc body is data handed to another program, not something this command runs --
    # the same reason quoted spans blank above. Writing a script or a prompt that MENTIONS a
    # recursive delete was denied repeatedly while no delete was ever going to execute.
    raw = _unwrap_quoted_flags(executable_text(command))
    scan = _normalize_git_globals(_strip_quoted(raw))
    for pattern, message in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, scan, re.IGNORECASE):
            # Allow recursive deletes confined to regenerable harness scratch, or every target a
            # `NAME=$(mktemp -d)` variable this same command owns; never relax a drive-format
            # block regardless of target.
            if ('format' not in message.lower()
                    and (_is_safe_ephemeral_cleanup(scan, raw, cwd, transcript_path) or _is_owned_temp_cleanup(raw)
                         or _is_resolved_safe_cleanup(
                             raw, cwd, lambda value, here: _deletable_by_fact(value, here, transcript_path)))):
                continue
            if 'recursive' in message.lower():
                return True, (message + " (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first)."
                              + _CACHE_HINT + _delete_reason(raw, cwd, transcript_path))
            return True, message + " (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first)."
    return False, ""



# --- Git losses ---------------------------------------------------------------------------------------------------
# Moved from git_guardrails.py (its loss checks) and _peer_loss.py. `_git_commit` owns the command parse: heredoc
# bodies stripped, segments split, `git -C <path>` and a preceding `cd <path>` retarget the repo, inline `VAR=1`
# prefixes read.

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
_PUSH_FORCE = {"--force", "--force-with-lease", "--force-if-includes"}


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


def parse_discard(sub, rest, cwd=None):
    """The destructive action `git <sub> <rest>` names, or None when it destroys nothing recoverable.
    Parsed once, so the evidence step and the deny message read the same value."""
    rest = tuple(rest)
    if sub in ("checkout", "restore"):
        if sub == "checkout" and not ("--" in rest or "." in rest):
            return None
        staged_only = ({"--staged", "-S"} & set(rest)
                       and not ("--worktree" in rest or "W" in short_flag_chars(rest)))
        if sub == "restore" and staged_only:
            return None
        split = rest.index("--") if "--" in rest else len(rest)
        head = rest[:split]
        paths = tuple(rest[split + 1:]) if "--" in rest else tuple(a for a in rest if not a.startswith("-"))
        flags = tuple(a for a in head if a.startswith("-"))
        return Discard(sub, paths=paths,
                       refs=tuple(a for a in head if not a.startswith("-")) if sub == "checkout" else (),
                       flags=flags,
                       source=next((a.split("=", 1)[1] for a in flags if a.startswith("--source=")), None),
                       side="theirs" if "--theirs" in flags else "ours" if "--ours" in flags else None,
                       separated="--" in rest, bulk=_bulk(paths, cwd))
    if sub == "reset" and "--hard" in rest:
        return Discard("reset --hard", flags=tuple(a for a in rest if a.startswith("-")))
    if sub == "clean":
        chars = short_flag_chars(rest)
        if "n" not in chars and "--dry-run" not in rest and ("f" in chars or "--force" in rest):
            return Discard("clean")
        return None
    if sub == "push" and (_PUSH_FORCE & set(rest)
                          or any(a.startswith(("--force-with-lease=", "--force-if-includes=")) for a in rest)
                          or "f" in short_flag_chars(rest)):
        return Discard("force-push")
    if sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
        return Discard("branch-delete", flags=tuple(a for a in rest if a.startswith("-")),
                       targets=tuple(a for a in rest if not a.startswith("-")))
    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        args = rest[1:]
        return Discard("stash " + rest[0], flags=tuple(a for a in args if a.startswith("-")),
                       targets=tuple(a for a in args if not a.startswith("-")))
    if sub == "reflog" and "expire" in rest:
        return Discard("reflog-expire")
    if sub == "gc" and any(a == "--prune" or a.startswith("--prune=") for a in rest):
        return Discard("gc-prune")
    if sub in ("filter-branch", "filter-repo"):
        return Discard(sub)
    return None


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
        if d.action == "restore" and not path.startswith(":") and not os.path.lexists(os.path.join(cwd, path)):
            continue
        diff = ["diff", "--quiet", "--ignore-cr-at-eol"] + ([base] if base else []) + ["--", path]
        if (run_git(diff, cwd, git_env) is None and not (base and _clean_in_head(path, cwd, git_env))
                and not (_content_stored(path, cwd) and (d.action == "restore" or not base))):
            return True, path
    return True, None


def _content_stored(path, cwd):
    """True when git's object store holds the working-tree bytes of `path`, so discarding them loses nothing
    git cannot reproduce (`_regenerable_clone.worktree_unstored`). A restore or a checkout from the index
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


_BRANCH_DELETE_FLAGS = ("-D", "--delete", "--force", "-f", "-q", "--quiet")


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


def _checkout_retry(d):
    if d.bulk:
        return "then name each file: `git checkout -- <file>` for one saved edit at a time."
    if d.refs:
        return "then run `git restore --source=%s -- <path>`." % d.refs[0]
    return "then the same command passes."


def _restore_retry(d):
    if d.bulk:
        return "then name each file: `git restore <file>` for one saved edit at a time."
    if "--worktree" in d.flags or "W" in short_flag_chars(d.flags):
        return "then run it without `--worktree`/`-W` (restore's default), one saved file at a time."
    if "-s" in d.flags:
        return "then spell the source `--source=<ref>` and name one saved file."
    return "then the same command passes."


def discard_evidence(d, cwd, git_env=None, transcript_path=None):
    """The facts one destructive decision reads, gathered once. A fact that cannot be read stays at its unproven
    value, so the decision denies."""
    if d.action in ("checkout", "restore"):
        if any((git_env or {}).get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE')):
            return Evidence(redirected=True)
        judged, lost = _lost_path(d, cwd, git_env)
        if judged and lost is None:
            return Evidence(lossless=True)
        eligible = d.separated if d.action == "checkout" else bool(d.paths or d.flags or d.refs or d.separated)
        return Evidence(unstored=(lost,) if lost else (),
                        owned=eligible and _in_own_worktree(cwd, git_env, transcript_path))
    if d.action == "reset --hard":
        owned = _in_own_worktree(cwd, git_env, transcript_path)
        return Evidence(owned=owned, clean=owned and _worktree_clean(cwd, git_env))
    if d.action == "branch-delete":
        return Evidence(problems=tuple(_branch_deletion_problems(d.targets, d.flags, cwd, git_env)))
    if d.action == "stash drop":
        return Evidence(preserved=_stash_preserved(d.flags, d.targets, cwd, git_env))
    return Evidence()


def _denial(d, ev):
    """The block message for a parsed discard, rendered from the same request and evidence the decision read."""
    if d.action in ("checkout", "restore"):
        if not ev.redirected and (ev.lossless or ev.owned):
            return None
        if d.action == "checkout":
            return ("BLOCKED `git checkout` worktree discard (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
                    "overwrites uncommitted edits to those paths, which may be another session's; another session's "
                    "edit needs the owner's words first. To discard your own edit, save it first: `git update-ref refs/preserve/<name> $(git hash-object -w <path>)`, "
                    + _checkout_retry(d))
        return ("BLOCKED `git restore` without `--staged` (CLAUDE.core.md §Shell Discipline: an unsaved edit would be lost) — "
                "discards uncommitted worktree edits, which may be another session's; another session's edit "
                "needs the owner's words first. To discard your own edit, "
                "save it first: `git update-ref refs/preserve/<name> $(git hash-object -w <path>)`, "
                + _restore_retry(d))
    if d.action == "reset --hard":
        if ev.owned and ev.clean:
            return None
        return ("BLOCKED `git reset --hard` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "discards every uncommitted change in the shared worktree with no undo. Use "
                "`git reset --keep` to move HEAD and keep local edits.")
    if d.action == "clean":
        return ("BLOCKED `git clean -f` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "deletes untracked files permanently (git has no copy). Run the same command with "
                "`-n` first, then ask the user to confirm.")
    if d.action == "force-push":
        return ("BLOCKED force-push (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites remote "
                "history and can erase commits other checkouts still depend on. Rebase onto the remote and "
                "push normally.")
    if d.action == "branch-delete":
        if not ev.problems:
            return None
        return ("BLOCKED `git branch -D` (CLAUDE.core.md §Shell Discipline: unreachable commits would be lost) — "
                "force-deleting would lose commits: " + "; ".join(ev.problems) + ". "
                + ("Name only local branches, with no flag beyond `-D`. "
                   if any(not p.endswith("holds commits no other ref has") for p in ev.problems) else "")
                + ("Keep each branch's commits reachable with `git update-ref refs/preserve/<name> <branch>`, "
                   "then the same delete passes."
                   if any(p.endswith("holds commits no other ref has") for p in ev.problems) else ""))
    if d.action == "stash drop":
        if ev.preserved:
            return None
        target = d.targets[0] if d.targets else "stash@{0}"
        return ("BLOCKED `git stash drop` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — stashed work "
                "is unreferenced once dropped. With the owner's go-ahead, keep it reachable and drop: "
                f"`git update-ref refs/preserve/<name> {target}`, then the same drop passes.")
    if d.action == "stash clear":
        return ("BLOCKED `git stash clear` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — "
                "stashed work is unreferenced once dropped. Leave it; ask the user before discarding.")
    if d.action == "reflog-expire":
        return ("BLOCKED `git reflog expire` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — the reflog "
                "is the last recovery path for lost commits. Leave expiry to git's own schedule.")
    if d.action == "gc-prune":
        return ("BLOCKED `git gc --prune` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — permanently "
                "removes unreachable objects that reflog recovery depends on. Run `git gc` without `--prune`.")
    if d.action in ("filter-branch", "filter-repo"):
        return (f"BLOCKED `git {d.action}` (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first) — rewrites every "
                "commit in place, invalidating all existing clones and hashes. Ask the user for explicit "
                "go-ahead before rewriting history; never do it in-session.")
    return None


def verdict(args, cwd=None, git_env=None, transcript_path=None):
    """A block message for one git invocation, or None. `cwd` is the repo the call targets; without it a
    checkout/restore discard is judged unsafe."""
    if not args:
        return None
    d = parse_discard(args[0], args[1:], cwd)
    return None if d is None else _denial(d, discard_evidence(d, cwd, git_env, transcript_path))


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
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — %s, so publishing this "
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


def _revert_message(stale):
    if not stale:
        return None
    shown = "\n  ".join(stale[:10]) + ("\n  ... %d more" % (len(stale) - 10) if len(stale) > 10 else "")
    return ("BLOCKED commit (CLAUDE.md §Core Principles: preserve peer work) — it would revert a peer's commit "
            "to %d file(s):\n  %s\n"
            "The index was built from an older HEAD. Rebuild it from the current HEAD, or commit "
            "your files with a pathspec (`git commit -F <msg> -- <paths>`)." % (len(stale), shown))


def commit_msg_check(message_path, environ=None, cwd=None):
    """Deny text for the commit git is about to write, or None: `base_verdict` and `revert_verdict` re-run inside
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
    git_dir = run_git(["rev-parse", "--git-dir"], cwd, git_env)
    if git_dir and os.path.exists(os.path.join(cwd, git_dir.strip(), "MERGE_HEAD")):
        return None
    return base_verdict(cwd, git_env) or revert_verdict([], cwd, git_env, own_trailer=message_trailer(message_path))


def _skips_hooks(rest):
    """True when a commit-like call's own flags skip git's commit-msg hook (`-n`, `--no-verify`, a bundled `n`)."""
    for a in rest:
        if a == "--":
            break
        if a == "--no-verify":
            return True
        if a.startswith("-") and not a.startswith("--"):
            for letter in a[1:]:
                if letter == "n":
                    return True
                if letter in _SHORT_VALUE_LETTERS:
                    break
    return False


def _unhooked_publish(args, cwd, git_env, command):
    """True when the commit-time entry cannot re-check what this publication commits (D-IW, owner 2026-11-23): the
    call skips the hooks, the command overrides git config, the repository's `core.hooksPath` is not
    `.claude/githooks` with its `commit-msg` present, or the publication amends (the hook re-runs the stage-record
    and peer-revert checks, not the amend check, which read HEAD before this command moved it)."""
    if _skips_hooks(args[1:]) or (args[0] == "commit" and parse_commit_args(args[1:])[1]):
        return True
    if re.search(r"hookspath|GIT_CONFIG_(?:PARAMETERS|COUNT)", command, re.I):
        return True
    configured = run_git(["config", "--get", "core.hooksPath"], cwd, git_env)
    top = run_git(["rev-parse", "--show-toplevel"], cwd, git_env)
    if not configured or not top or configured.strip().replace("\\", "/").rstrip("/") != ".claude/githooks":
        return True
    return not os.path.isfile(os.path.join(top.strip(), ".claude", "githooks", "commit-msg"))


def _unproved_publish(args):
    """True when an opaque call (cwd=None) would write the index, publish a commit or push."""
    return bool(args) and (args[0] == "push" or publishes_index(args[0], args[1:]) or writes_index(args))


def git_verdict(command, cwd, transcript_path=None, session_id=None):
    """The first git loss in `command`, or None. A call inside a shell launcher body (`bash -c`, `eval`, ...)
    borrows no worktree ownership; its resolved repository facts still count."""
    command = executable_text(command)
    if "git" not in command:
        return None
    cwd = cwd or "."
    born = set()  # repos this same command creates with `git init`
    initialized = set()  # existing repos outside this project the command runs `git init` on
    project_root = _project_root()
    index_write = None  # the latest git index write seen so far in this command
    for call in git_invocations(command, cwd):
        args = [call.sub] + call.rest
        target, git_env = call.cwd, call.git_env
        launched = call.body_path is None or len(call.body_path) > 0
        owner_transcript = None if launched else transcript_path
        if target is None:
            message = verdict(args, None, git_env, None)
            if not message and _unproved_publish(args):
                message = UNPROVED_TARGET % call.sub
            if message:
                return message
            continue

        def expand(token):
            return expand_literal(token, call.assignments) or token
        key = os.path.normcase(os.path.abspath(target))
        if args[0] == "init":
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
                born.add(made_key)
            elif not in_project(made, project_root, git_env):
                initialized.add(made_key)
            continue
        judged_args = [expand(arg) for arg in args] if args[0] in ('checkout', 'restore') else args
        message = verdict(judged_args, target, git_env, owner_transcript)
        if (index_write and not message and key not in born and key not in initialized
                and publishes_index(args[0], args[1:]) and _unhooked_publish(args, target, git_env, command)):
            message = SAME_COMMAND_WRITE % (index_write, index_write)
        if writes_index(args):
            index_write = "git " + args[0]
        if (not message and (args[0] in ("commit", "merge", "cherry-pick", "revert")
                             or publishes_index(args[0], args[1:])) and key not in born):
            git_env, problem = resolve_index(git_env, target)
            if problem:
                message = "BLOCKED commit (memory gotcha_concurrent_session_hazards #16) — %s." % problem
            elif args[0] == "commit":
                message = (base_verdict(target, git_env)
                           or amend_verdict(args[1:], target, git_env, session_id)
                           or revert_verdict(args[1:], target, git_env, session_id))
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
    blocked, message = check_bash_command(command, cwd, transcript)
    if blocked:
        rewritten = None
        if payload.get("tool_name") == "Bash" and "recursive" in message.lower():
            rewritten = rewrite_absent_forced_deletes(command, cwd)
        if rewritten is None or check_bash_command(rewritten, cwd, transcript)[0]:
            return _output("deny", message)
        command = rewritten
    why = git_verdict(command, cwd, transcript, payload.get("session_id"))
    if why:
        return _output("deny", why)
    return _output(updated=dict(tool_input, command=command)) if blocked else None


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
