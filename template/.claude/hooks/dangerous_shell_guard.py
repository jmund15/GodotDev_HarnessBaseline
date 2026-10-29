#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell - the destructive-command floor.

The one destructive-command check of every profile. `pre_bash_dispatch` loads it as a floor member
(not optional), so an import fault denies per `_floor_stub`. The engine-layer edit-time half that
used to share a file with it stays in `pattern_enforcer`, which no longer judges shell commands.

Blocks:
- Recursive/forced deletes and directory removal (rm -r/-rf, del /s, rd /s, rmdir /s, PowerShell
  Remove-Item -Recurse and its aliases) and drive format commands.

Exemptions, each judged on the resolved target and never on the command's wording:
- Quoted spans and heredoc bodies are blanked before matching, so a quoted mention (`grep "rm -rf"`,
  an echo, a commit body) is no command. `git rm` stays allowed.
- A recursive delete whose every target is a regenerable cache inside the project, a path below the
  system temp directory holding no git repository, this session's own `.claude/scratch/<session id>`
  output, a pushed regenerable clone, a scratch folder holding no git repository and no registered
  worktree, a retired worktree (nothing git lacks, every commit on a remote; its ignored files go
  only when this session created it), or a `NAME=$(mktemp -d)` variable the same command owns.
  Any chained second delete, glob in a directory part, `~`, `%` or unresolvable target disqualifies it, as
  does a target a live peer session works in, or a scratch target that is or holds another session's id folder.

Git history rewrites (reset --hard, clean -f, force-push, branch -D, stash drop) belong to
`git_guardrails`, which parses git invocations; this file holds no second copy of them.

Wired in: hooks/pre_bash_dispatch.py (floor member). Standalone: stdin PreToolUse JSON, exit 2 on a block.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _command_text import executable_text, _REDIRECTION  # noqa: E402
from _regenerable_clone import (empty_leftover, is_regenerable_clone, is_retired_worktree,  # noqa: E402
                                lossless_orphan_worktree, session_created_worktree)
from _git_commit import literal_assignments, expand_literal, resolve_cd  # noqa: E402


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


# Regenerable caches a recursive delete may target without asking: harness scratch the tooling
# itself creates and destroys (baseline clone cache, log spool), the semantic-search index (rebuilt
# by /reindex_search) and Python bytecode/test caches. Matched by PATH SEGMENT, never substring:
# the old `\.claude[\\/](?:\.cache|logs)\b` regex passed `.claude/logs-old` and
# `.claude/.cache/../..` (the repo root). Evidence folders (`.claude/scratch`) and checkouts
# (`.claude/worktrees`) are gitignored but are NOT caches, so they stay blocked unless
# `_regenerable_clone` proves the target loses nothing.
_CACHE_SEGMENTS = frozenset({".search-index", "__pycache__", ".pytest_cache"})
_CACHE_PAIRS = frozenset({(".claude", ".cache"), (".claude", "logs")})
_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
_CACHE_HINT = " Allowed: caches, temp dirs, clean pushed scratch clones, retired worktrees (`orchestration` reference `worktrees.md`), scratch folders holding no git repository."


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
    segment = (any(p in _CACHE_SEGMENTS for p in parts)
               or any(pair in _CACHE_PAIRS for pair in zip(parts, parts[1:])))
    return segment and _inside_root(token, cwd)


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


def _registered_worktrees() -> list[str]:
    """Normalized paths of the project's registered worktrees; an unreadable list holds the project root,
    so every scratch target counts as holding one and the delete stays denied."""
    import subprocess
    root = _project_root()
    try:
        out = subprocess.run(['git', '-C', root, 'worktree', 'list', '--porcelain'], capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return [_norm(root)]
    if out.returncode:
        return [_norm(root)]
    return [_norm(line[len('worktree '):]) for line in out.stdout.splitlines() if line.startswith('worktree ')]


_ANY_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


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
    held =[wt for wt in _registered_worktrees() if wt == _norm(target) or _strictly_under(wt, _norm(target))]
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


def _is_regenerable_target(token: str, cwd: str | None, transcript_path: str | None = None,
                           reasons: list[str] | None = None) -> bool:
    """A pushed clone, a lossless orphaned worktree directory (needs no approval: nothing in it is
    unheld by git or a non-regenerable ignored file), an empty leftover directory under scratch (no file,
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
    if _strictly_under(_norm(target), _norm(os.path.join(root, '.claude', 'scratch'))):
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


def _is_safe_ephemeral_cleanup(scan: str, raw: str, cwd: str | None = None,
                               transcript_path: str | None = None) -> bool:
    """True only for a single (non-chained) recursive delete whose every path argument is a
    literal path at or under an allowlisted cache segment inside the repo (`_is_cache_path`) or a
    regenerable scratch clone (`_is_regenerable_target`).

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
    return bool(paths) and all(_is_cache_path(p, cwd) or _is_own_session_scratch(p, cwd, transcript_path)
                               or _is_regenerable_target(p, cwd, transcript_path) for p in paths)


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
    bare_re = re.compile(r'(?<![A-Za-z0-9_$])(?<!\$\{)' + re.escape(name) + r'(?![A-Za-z0-9_])')
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
        return not _holds_git(path)
    parts = _checkout_rel_parts(path)
    if not parts:
        return False
    lowered = [p.lower() for p in parts]
    if lowered[:2] == [".claude", "scratch"]:
        if len(parts) < 3:
            return False
        root = _project_root()
        return is_regenerable_clone(path, root, root)
    if lowered[:2] in ([".claude", ".cache"], [".claude", "logs"]) or lowered[0] == ".godot":
        return True
    return any(p in _CACHE_SEGMENTS for p in lowered)


def _is_regenerable_resolved(target: str, here: str) -> bool:
    return _is_regenerable_or_scratch(_norm(resolve_cd(here, target)))


def _is_resolved_safe_cleanup(raw: str, cwd: str | None, accept=_is_regenerable_resolved) -> bool:
    """True when every recursive-delete segment in `raw` is a plain delete command whose targets
    all satisfy `accept(target, base directory)`; the default admits `_is_regenerable_or_scratch` paths.
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
            here = resolve_cd(here, moved) if moved and not _GLOB_CHARS.search(moved) else None
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
    """Why the first worktree or scratch target this delete names is not removable, or ''."""
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
    return ''


def check_bash_command(command: str, cwd: str | None = None,
                       transcript_path: str | None = None) -> tuple[bool, str]:
    """Check bash command for dangerous patterns. Returns (blocked, message)."""
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
                         or _is_resolved_safe_cleanup(raw, cwd)
                         or _is_resolved_safe_cleanup(raw, cwd, lambda value, here: (
                             _is_cache_path(value, here) or _is_own_session_scratch(value, here, transcript_path)
                             or _is_regenerable_target(value, here, transcript_path))))):
                continue
            if 'recursive' in message.lower():
                return True, (message + " (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first)."
                              + _CACHE_HINT + _delete_reason(raw, cwd, transcript_path))
            return True, message + " (CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first)."
    return False, ""


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)

    tool_input = input_data.get("tool_input") if isinstance(input_data, dict) else None
    if isinstance(input_data, dict) and input_data.get("tool_name") in ("Bash", "PowerShell") \
            and isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        blocked, message = check_bash_command(tool_input["command"], input_data.get("cwd"),
                                              input_data.get("transcript_path"))
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
