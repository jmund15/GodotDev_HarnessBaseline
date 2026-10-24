"""
Shared parser for commit guards; its PreToolUse entry folds tracked `add && commit` before guards.

A guard that matches the raw command string denies on text the shell never runs (a heredoc
commit message quoting the command), evaluates the wrong repo (`git -C <path>`, a preceding
`cd <path>`), and misses the shapes that widen a commit's content (`-a`, `--amend`, a
pathspec). Nine guards each parsed privately and each carried a different subset of those
holes; this module is the single home, so a fix lands in every guard at once.

    commit_invocations(command, cwd)    -> [CommitInvocation]  every executing commit-like segment
    staged_paths(rest, cwd, env)        -> (paths, None) | (None, failing-git-subcommand)
    commit_source(rest, cwd, env)       -> "index" | "worktree" | None  where the published content is read; None when `-i` mixes sources
    commit_message(rest, cwd)           -> str | None  the message a commit carries, None when unknowable
    common_dir(path, env)               -> str | None  the repo's git common dir
    memoized()                          context: run_git answers a repeated read-only query once
    in_project(cwd, project_root, env)  -> bool  the commit targets this project's repo (worktrees included)
    incoming_paths(sub, rest, cwd, env) -> same shape, for merge / cherry-pick / revert
    resolve_index(git_env, cwd)         -> (git_env, None) | (None, reason the index is unreadable)
    git_environ(git_env)                -> os.environ with a command's GIT_* variables applied
    bypass_declared(inline_env, VAR)    -> True when VAR=1 in the hook's env OR inline on the command

A commit publishes the index its GIT_* variables name (`GIT_INDEX_FILE=<f> git commit`, or an
earlier `export`). Each invocation carries them as `git_env`, and every git read made to judge that
commit passes it: reading the default index judges content the commit never had.

Fail posture belongs to the caller: this module reports git failure as `None`, and a guard
decides whether that denies (enforcement) or allows (advisory).
"""
import json
import os
import re
import sys
import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
try:
    from sidecar_argv import literal_launcher_body, executable_index
except Exception:  # each guard that needs the parser imports it itself and denies on its failure
    literal_launcher_body = executable_index = None

try:
    from _command_text import executable_text, executable_spans, command_words, executable_word, strip_heredocs
except Exception:  # pragma: no cover - a missing sibling degrades to raw matching
    def executable_text(command):
        return command

__all__ = [
    "CommitInvocation", "LauncherWord", "COMMIT_LIKE", "GLOBAL_FLAGS_WITH_VALUE",
    "segments", "join_continuations", "git_invocation", "base_rev", "git_invocations", "cd_target", "commit_invocations",
    "parse_commit_args", "run_git", "staged_paths", "incoming_paths", "bypass_declared",
    "commit_source", "commit_message",
    "export_assignments", "command_git_env", "resolve_index", "git_environ",
    "resolve_cd", "literal_assignments", "expand_literal",
    "common_dir", "in_project", "memoized",
]

# A lone `&` separates commands; the `&` of a redirect (`2>&1`, `&>log`) does not.
SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|\n]|(?<![<>])&(?!>)")
# A redirect and its target (`2>&1`, `> log`, `2>/dev/null`, `<<'EOF'`) is shell plumbing, not an
# argument: read as one, it becomes a pathspec that narrows the commit a guard judges.
_REDIRECT = re.compile(r"(?:(?<=\s)|^)\d*(?:&>>?|>>?|<<<|<<-?|<>|<)(?:&(?:\d+-?|-)|\s*[^\s;&|<>]+)")

# Global git flags that may precede the subcommand; these take a value.
# Global flags that select the repository; git treats each as the GIT_* variable beside it, and the
# invocation's `git_env` carries them so every read made to judge the commit targets the same repo.
_REPO_FLAGS = (("--git-dir", "GIT_DIR"), ("--work-tree", "GIT_WORK_TREE"))
GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# Subcommands that create a commit whose content is not the index alone.
COMMIT_LIKE = {"commit", "merge", "cherry-pick", "revert"}

_COMMIT_VALUE_FLAGS = {"-m", "--message", "-F", "--file", "-c", "-C", "--author", "--date",
                       "--reuse-message", "--fixup", "--squash", "--trailer", "--pathspec-from-file"}
_SHORT_VALUE_LETTERS = "mFcC"


@dataclass(frozen=True)
class LauncherWord:
    """Enclosing body word's outermost source span and its shell quote delimiter."""
    start: int
    end: int
    quote: str


@dataclass
class CommitInvocation:
    sub: str                 # "commit" | "merge" | "cherry-pick" | "revert"
    rest: list               # args after the subcommand
    cwd: str                 # the repo the command targets (payload cwd + cd + -C)
    inline_env: dict = field(default_factory=dict)
    git_env: dict = field(default_factory=dict)   # GIT_* the git process runs with; None unsets
    assignments: dict = field(default_factory=dict)
    subcommand_end: int | None = field(default=None, compare=False)
    body_path: tuple[LauncherWord, ...] | None = field(default=None, compare=False)


_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_CONTINUATION = re.compile(r"\\\r?\n")


def join_continuations(command):
    """`command` with each backslash-newline blanked to spaces of the same length, so every offset
    still reads the source. A wrapped argument list then stays one segment and one token run."""
    return _CONTINUATION.sub(lambda match: " " * len(match.group(0)), command)


def segments(command):
    """Segments split at `||`, `&&`, `;`, `|`, a lone `&` and newlines outside quotes. A separator inside a
    quoted argument (a search pattern naming a git command) does not split. An unbalanced quote splits
    at every separator, so a malformed command can only over-block. A backslash-newline continues the
    line, so it splits nothing."""
    command = join_continuations(command)
    blanked = _QUOTED.sub(lambda match: " " * len(match.group(0)), command)
    if "'" in blanked or '"' in blanked:
        blanked = command
    start = 0
    for match in SEGMENT_SPLIT.finditer(blanked):
        raw = command[start:match.start()].strip()
        if raw:
            yield raw
        start = match.end()
    raw = command[start:].strip()
    if raw:
        yield raw


def _tokens(segment):
    parsed = command_words(segment)
    if parsed is not None:
        return [word.value for word in parsed[0]]
    masked = _QUOTED.sub(lambda match: "x" * len(match.group(0)), segment)
    comment = re.search(r'(?<!\S)#[^\n]*', masked)
    if comment:
        segment = segment[:comment.start()]
        masked = masked[:comment.start()]
    if "'" not in masked and '"' not in masked:
        for match in reversed(list(_REDIRECT.finditer(masked))):
            segment = segment[:match.start()] + " " + segment[match.end():]
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


class _GitChdir(str):
    """Keep -C operands until expansion, while preserving the parser's string contract."""
    def __new__(cls, paths):
        value = ''
        for path in paths:
            if path:
                value = resolve_cd(value, path)
        instance = str.__new__(cls, value)
        instance.paths = tuple(paths)
        return instance


def git_invocation(segment):
    """(args, chdir, inline_env) for a git segment, or None if not a git invocation. `inline_env` holds the
    segment's `VAR=value` prefix plus `--git-dir`/`--work-tree` as GIT_DIR/GIT_WORK_TREE."""
    tokens = _tokens(segment)
    if not tokens:
        return None
    i = 0
    inline_env = {}
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        key, _, value = tokens[i].partition("=")
        inline_env[key] = value
        i += 1
    if i >= len(tokens):
        return None
    head = tokens[i].replace("\\", "/").rsplit("/", 1)[-1]
    if head not in ("git", "git.exe"):
        return None
    args = tokens[i + 1:]
    j = 0
    chdirs = []
    while j < len(args) and args[j].startswith("-"):
        if args[j] == "-C" and j + 1 < len(args):
            chdirs.append(args[j + 1])
        for flag, var in _REPO_FLAGS:
            if args[j] == flag and j + 1 < len(args):
                inline_env[var] = args[j + 1]
            elif args[j].startswith(flag + "="):
                inline_env[var] = args[j].split("=", 1)[1]
        j += 2 if args[j] in GLOBAL_FLAGS_WITH_VALUE else 1
    return args[j:], _GitChdir(chdirs) if chdirs else None, inline_env


def cd_target(segment):
    """The path a bare `cd <path>` segment moves to, or None."""
    tokens = _tokens(segment)
    if len(tokens) == 2 and tokens[0] == "cd":
        return tokens[1]
    return None


_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(/|$)")
_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGN_SEGMENT = re.compile(r"^(?:export\s+)?(" + _NAME + r")=(.*)$")
_EXPANSION = re.compile(r"\$\{(" + _NAME + r")\}|\$(" + _NAME + r")")
_NON_LITERAL = re.compile(r"[$`()*?\[\]{}~]")


def resolve_cd(cwd, moved):
    """`cwd` after a `cd <moved>`, understanding Git Bash's `/c/...` drive form.

    The Bash tool runs Git Bash, so an operand is routinely MSYS-absolute. `os.path.join` on
    Windows treats a leading `/` as root-relative to the CURRENT drive and produces
    `C:\\repo\\c\\Users\\...`, a path that does not exist. `/tmp` maps to the system temp directory,
    as Git Bash mounts it."""
    moved = os.path.expanduser(moved)
    posix = moved.replace("\\", "/")
    m = _MSYS_DRIVE.match(posix)
    if m:
        moved = "%s:\\%s" % (m.group(1).upper(), posix[3:].replace("/", os.sep))
    elif (posix == "/tmp" or posix.startswith("/tmp/")) and os.name == "nt":
        import tempfile
        moved = os.path.join(tempfile.gettempdir(), posix[5:])
    return moved if os.path.isabs(moved) else os.path.join(cwd, moved)


def literal_assignments(command, cwd=None):
    """Once-assigned literal variables, including known path expansions and `$(pwd)`.
    Unknown substitutions and names assigned or mutated more than once stay unresolved."""
    found, known = {}, {}
    if cwd is not None:
        known['PWD'] = cwd
    for segment in segments(command):
        moved = cd_target(segment)
        if moved is not None and cwd is not None:
            expanded = expand_literal(moved, known)
            if expanded is not None:
                cwd = resolve_cd(cwd, expanded)
                known['PWD'] = cwd
            continue
        tokens = _tokens(segment)
        # A non-expansion mention can mutate a name (read, unset, +=, declare, printf -v).
        # Invalidate before resolving later copies; shell assignments copy values, not references.
        for name in list(known):
            if re.search(r'(?<![A-Za-z0-9_$])(?<!\$\{)' + re.escape(name) + r'(?![A-Za-z0-9_])', segment):
                known.pop(name)
        if len(tokens) not in (1, 2) or (len(tokens) == 2 and tokens[0] != "export"):
            continue
        m = _ASSIGN_SEGMENT.match(" ".join(tokens))
        if m:
            raw = m.group(2)
            if cwd is not None:
                raw = raw.replace('$(pwd)', cwd)
            value = expand_literal(raw, known)
            if value is not None and not _NON_LITERAL.search(value):
                value = os.path.expanduser(value)
                found.setdefault(m.group(1), []).append(value)
                known[m.group(1)] = value
    result = {}
    for name, values in found.items():
        bare = re.findall(r"(?<![A-Za-z0-9_$])(?<!\$\{)" + name + r"(?![A-Za-z0-9_])", command)
        if len(values) == 1 and len(bare) == 1:
            result[name] = values[0]
    return result


def expand_literal(token, assignments):
    """`token` with `$NAME` / `${NAME}` replaced from `assignments`, or None when an unknown name,
    a substitution or any other `$` remains."""
    if isinstance(token, _GitChdir):
        paths = [expand_literal(path, assignments) for path in token.paths]
        return None if any(path is None for path in paths) else str(_GitChdir(paths))
    expanded = _EXPANSION.sub(lambda m: assignments.get(m.group(1) or m.group(2), "\0"), token)
    if "\0" in expanded or "$" in expanded or "`" in expanded:
        return None
    return expanded


def export_assignments(segment):
    """{KEY: value} an `export K=V ...` segment sets, {KEY: None} an `unset K ...` clears, else None."""
    tokens = _tokens(segment)
    if len(tokens) < 2:
        return None
    if tokens[0] == "export":
        pairs = (t.partition("=") for t in tokens[1:] if "=" in t and not t.startswith("-"))
        return {key: value for key, _, value in pairs}
    if tokens[0] == "unset":
        return {t: None for t in tokens[1:] if not t.startswith("-")}
    return None


def command_git_env(exported, inline_env, assignments=None):
    """The GIT_* variables a git segment runs with: earlier exports, then its own inline prefix.
    A value naming a once-assigned literal variable is expanded; any other `$` stays, and
    `resolve_index` refuses it."""
    merged = dict(exported or {})
    merged.update(inline_env or {})
    return {key: (expand_literal(value, assignments or {}) or value) if value is not None else None
            for key, value in merged.items() if key.startswith("GIT_")}


def resolve_index(git_env, cwd):
    """(git_env with GIT_INDEX_FILE as a native path, None), or (None, reason) when the guard cannot
    read the index the commit will publish. Git reads a missing index as EMPTY, so an unreadable one
    would make every guard judge a phantom commit that deletes the whole tree."""
    value = (git_env or {}).get("GIT_INDEX_FILE")
    if value is None:
        return git_env, None
    if "$" in value or "`" in value:
        return None, ("GIT_INDEX_FILE=%s is not a literal path, so the guard cannot read the index "
                      "this commit publishes; name the index by its literal path" % value)
    path = resolve_cd(cwd, value)
    if not os.path.isfile(path):
        return None, ("GIT_INDEX_FILE=%s does not exist when the guard runs; build the index in an "
                      "earlier call, then commit it by its literal path" % value)
    return dict(git_env, GIT_INDEX_FILE=path), None


def _launcher_context(segment, cwd, exported, assignments):
    if executable_index is None:
        return None
    parsed = command_words(segment)
    if parsed is None:
        tokens = _tokens(segment)
    else:
        tokens = [word.value if re.fullmatch(r"'[^']*'", segment[word.start:word.end])
                  else (expand_literal(word.value, assignments) or word.value) for word in parsed[0]]
    tokens, index = executable_index(tokens)
    body = literal_launcher_body(tokens, index)
    if body is None:
        return None
    inherited = dict(exported)
    prefix = 0
    while prefix < index:
        token = tokens[prefix]
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', token):
            key, value = token.split('=', 1)
            inherited[key] = expand_literal(value, assignments) or value
        elif token in ('-u', '--unset') and prefix + 1 < index:
            inherited[tokens[prefix + 1]] = None
            prefix += 1
        elif token.startswith('--unset='):
            inherited[token.split('=', 1)[1]] = None
        elif token in ('-C', '--chdir') and prefix + 1 < index:
            moved = tokens[prefix + 1]
            cwd = resolve_cd(cwd, expand_literal(moved, assignments) or moved)
            prefix += 1
        elif token.startswith('--chdir='):
            moved = token.split('=', 1)[1]
            cwd = resolve_cd(cwd, expand_literal(moved, assignments) or moved)
        elif token in ('-i', '--ignore-environment'):
            inherited = {key: None for key in set(inherited) | set(os.environ) if key.startswith('GIT_')}
        prefix += 1
    return body, cwd, inherited


def _body_source(segment, body):
    """Safe literal body word and decoded-character boundaries in its segment."""
    parsed = command_words(segment)
    if parsed is None or executable_index is None:
        return None
    words = parsed[0]
    tokens, index = executable_index([word.value for word in words])
    literal = literal_launcher_body(tokens, index)
    if (literal is None or literal.end != literal.start + 1 or len(tokens) != len(words)):
        return None
    word = words[literal.start]
    raw = segment[word.start:word.end]
    if word.value != body.body:
        return None
    if re.fullmatch(r"'[^']*'", raw):
        offsets = tuple(range(word.start + 1, word.end))
    elif re.fullmatch(r'"(?:[^"\\$`]|\\.)*"', raw):
        offsets, decoded, i = [word.start + 1], [], 1
        while i < len(raw) - 1:
            if raw[i] == '\\' and raw[i + 1] in '\\"':
                i += 1
            decoded.append(raw[i])
            i += 1
            offsets.append(word.start + i)
        if ''.join(decoded) != body.body:
            return None
        offsets = tuple(offsets)
    else:
        return None
    return word, raw[0], offsets


def _subcommand_end(segment, args):
    index = len(_tokens(segment)) - len(args)
    parsed = command_words(segment)
    if parsed is not None:
        words = parsed[0]
        return words[index].end if 0 <= index < len(words) else None
    token_text = segment
    quoted = _QUOTED.sub(lambda m: 'x' * len(m.group(0)), segment)
    for redirect in reversed(list(_REDIRECT.finditer(quoted))):
        token_text = token_text[:redirect.start()] + ' ' * len(redirect.group(0)) + token_text[redirect.end():]
    tokens = list(re.finditer(r"(?:\\.|[^\s'\"]|'[^']*'|\"(?:[^\"\\]|\\.)*\")+", token_text))
    return tokens[index].end() if 0 <= index < len(tokens) else None


def _git_invocations(command, cwd, exported, inherited, source_offsets=None, body_path=()):
    found = []
    text = join_continuations(strip_heredocs(command or '', preserve_offsets=True))
    if source_offsets is None and body_path is not None:
        source_offsets = tuple(range(len(command or '') + 1))
    assigned_once = dict(inherited)
    assigned_once.update(literal_assignments(text, cwd))
    cursor = 0
    for segment in segments(text):
        start = text.index(segment, cursor)
        cursor = start + len(segment)
        assigned_once['PWD'] = cwd
        moved = cd_target(segment)
        if moved is not None:
            cwd = resolve_cd(cwd, expand_literal(moved, assigned_once) or moved)
            continue
        assigned = export_assignments(segment)
        if assigned is not None:
            exported.update({key: expand_literal(value, assigned_once) or value if value is not None else None
                             for key, value in assigned.items()})
            continue
        launched = _launcher_context(segment, cwd, exported, assigned_once)
        if launched is not None:
            body, target, child_env = launched
            child_known = ({key: value for key, value in child_env.items() if value is not None}
                           if body.scoped else assigned_once)
            source = _body_source(segment, body) if source_offsets is not None else None
            child_offsets, child_path = None, None
            if source is not None:
                word, quote, offsets = source
                child_offsets = tuple(source_offsets[start + offset] for offset in offsets)
                child_path = body_path + (LauncherWord(source_offsets[start + word.start],
                                                       source_offsets[start + word.end], quote),)
            calls, after_cwd, after_env = _git_invocations(body.body, target, child_env, child_known,
                                                          child_offsets, child_path)
            found.extend(calls)
            if not body.scoped:
                cwd, exported = after_cwd, after_env
                assigned_once.update(literal_assignments(body.body, target))
            continue
        parsed = git_invocation(segment)
        if parsed is None:
            continue
        args, chdir, inline_env = parsed
        if not args:
            continue
        target = resolve_cd(cwd, expand_literal(chdir, assigned_once) or chdir) if chdir else cwd
        git_env = command_git_env(exported, inline_env, assigned_once)
        for var in ('GIT_DIR', 'GIT_WORK_TREE'):
            if git_env.get(var) and '$' not in git_env[var]:
                git_env[var] = resolve_cd(target, git_env[var])
        end = _subcommand_end(segment, args) if source_offsets is not None else None
        offset = source_offsets[start + end] if end is not None else None
        found.append(CommitInvocation(args[0], args[1:], target, inline_env, git_env, dict(assigned_once),
                                      offset, body_path if offset is not None else None))
    return found, cwd, exported


def git_invocations(command, cwd):
    """Every literal git call in execution order with cwd/env and outermost rewrite coordinates.
    subcommand_end is just after its token; body_path lists enclosing word spans/quotes, outer first.
    Both are None for unsafe source forms; rewrite metadata does not participate in equality."""
    return _git_invocations(command, cwd or '.', {}, {})[0]


def commit_invocations(command, cwd):
    """Commit-like calls from `git_invocations`; heredoc data and parse-only shells execute none."""
    return [call for call in git_invocations(command, cwd) if call.sub in COMMIT_LIKE]


def parse_commit_args(rest):
    """(include_dirty, amend, pathspec) — pathspec is everything after `--`, or trailing bare
    args. Combined short flags (`-am`, `-aF`) are split letter by letter: `a` anywhere sets
    include_dirty, and a value-taking letter in last position consumes the next token."""
    include_dirty = False
    amend = False
    pathspec = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            pathspec.extend(rest[i + 1:])
            break
        if a in ("-a", "--all"):
            include_dirty = True
            i += 1
            continue
        if a == "--amend":
            amend = True
            i += 1
            continue
        if a in _COMMIT_VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            letters = a[1:]
            if "a" in letters:
                include_dirty = True
            i += 2 if letters[-1] in _SHORT_VALUE_LETTERS else 1
            continue
        pathspec.append(a)
        i += 1
    return include_dirty, amend, pathspec


def git_environ(git_env):
    """os.environ with `git_env` applied (a None value unsets), or None when `git_env` is empty, so the
    subprocess inherits the hook's environment unchanged."""
    if not git_env:
        return None
    env = dict(os.environ)
    for key, value in git_env.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


# Subcommands that only read the repository, so one answer serves a whole dispatch.
_READ_ONLY = frozenset(("rev-parse", "diff", "ls-files", "ls-tree", "show", "cat-file", "merge-base",
                        "log", "for-each-ref", "symbolic-ref"))
_memo = None


@contextmanager
def memoized():
    """Inside the block, `run_git` answers a repeated `_READ_ONLY` query (same argv, cwd and git_env)
    from its first answer. `pre_bash_dispatch` holds it for one tool call: its sub-hooks read one repo
    state and would otherwise spawn the same git query once each. Outside it every call runs git."""
    global _memo
    outer = _memo
    _memo = {} if outer is None else outer
    try:
        yield
    finally:
        _memo = outer


def _subcommand(args):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c") else 1
    return args[i] if i < len(args) else None


def run_git(args, cwd, env=None):
    """git stdout, or None on any failure (non-zero exit, missing binary, timeout). `env` is the
    invocation's `git_env`."""
    key = None
    if _memo is not None and _subcommand(args) in _READ_ONLY:
        key = (tuple(args), os.path.normcase(os.path.abspath(cwd or ".")), tuple(sorted((env or {}).items())))
        if key in _memo:
            return _memo[key]
    out = _run_git(args, cwd, env)
    if key is not None:
        _memo[key] = out
    return out


def _run_git(args, cwd, env):
    try:
        result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30, env=git_environ(env))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _lines(out):
    return {line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}


def common_dir(path, env=None):
    """The normalized git common dir of the repo `path` sits in, or None when it is no repo. A
    linked worktree and its main checkout share it; two unrelated repos do not. `env` is an
    invocation's `git_env`: `GIT_DIR` there names the repo a commit targets whatever `path` is."""
    out = run_git(["rev-parse", "--git-common-dir"], path, env)
    if not out or not out.strip():
        return None
    common = out.strip()
    if not os.path.isabs(common):
        common = os.path.join(path, common)
    return os.path.normcase(os.path.realpath(common))


def in_project(cwd, project_root, env=None):
    """True when the commit at `cwd` (under the invocation's `git_env`) lands in the same repository as
    `project_root` -- the same git common dir, so a linked worktree of this project counts and another
    repo that holds its own `.claude/` does not. A `cwd` that is no repo is never in the project."""
    theirs = common_dir(cwd, env)
    return theirs is not None and theirs == common_dir(project_root)


def _pathspec_file(rest):
    """The `--pathspec-from-file` value, or None when the flag is absent."""
    for i, a in enumerate(rest):
        if a == "--":
            break
        if a.startswith("--pathspec-from-file="):
            return a.split("=", 1)[1]
        if a == "--pathspec-from-file":
            return rest[i + 1] if i + 1 < len(rest) else ""
    return None


def _includes_index(rest):
    """True for `-i`/`--include`: the index is published alongside the pathspec."""
    for a in rest:
        if a == "--":
            break
        if a in ("-i", "--include"):
            return True
        if a.startswith("-") and not a.startswith("--") and len(a) > 1:
            letters = a[1:]
            cut = min((letters.index(ch) for ch in _SHORT_VALUE_LETTERS if ch in letters), default=len(letters))
            if "i" in letters[:cut]:
                return True
    return False


def _full_pathspec(rest, cwd, pathspec):
    """(pathspec plus the lines of `--pathspec-from-file`, None) | (None, the failing step)."""
    spec_file = _pathspec_file(rest)
    if spec_file is None:
        return pathspec, None
    if spec_file in ("", "-"):
        return None, "pathspec-from-file " + (spec_file or "(missing value)")
    try:
        with open(os.path.join(cwd, spec_file), encoding="utf-8") as fh:
            return pathspec + [line.strip() for line in fh.read().replace("\0", "\n").splitlines()
                               if line.strip()], None
    except OSError:
        return None, "pathspec-from-file " + spec_file


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def base_rev(cwd, env=None):
    """`HEAD`, or git's empty tree (SHA-1 repos) while HEAD is unborn: a repo's first commit has no HEAD to diff."""
    return "HEAD" if run_git(["rev-parse", "--verify", "-q", "HEAD"], cwd, env) is not None else EMPTY_TREE


def staged_paths(rest, cwd, env=None):
    """Paths a `git commit <rest>` will publish. A pathspec commit (after `--`, bare, or from
    `--pathspec-from-file`) publishes only the pathspec's changed files, as git's default `--only`
    mode does, unless `-i`/`--include` adds the index. Otherwise the index is published. HEAD's
    paths join under `--amend`, dirty tracked files under `-a`. An unreadable pathspec file,
    including stdin (`-`), is a failure. `env` is the invocation's `git_env`. (paths, None) on success;
    (None, failing-step) on failure."""
    env, problem = resolve_index(env, cwd)
    if problem:
        return None, problem
    include_dirty, amend, pathspec = parse_commit_args(rest)
    paths = set()

    pathspec, problem = _full_pathspec(rest, cwd, pathspec)
    if problem:
        return None, problem

    if not pathspec or include_dirty or _includes_index(rest):
        cached = run_git(["diff", "--cached", "--name-only"], cwd, env)
        if cached is None:
            return None, "diff --cached --name-only"
        paths |= _lines(cached)

    if amend:
        shown = run_git(["show", "--name-only", "--pretty=format:", "HEAD"], cwd, env)
        if shown is None:
            return None, "show --name-only HEAD"
        paths |= _lines(shown)

    if include_dirty:
        dirty = run_git(["diff", "--name-only"], cwd, env)
        if dirty is None:
            return None, "diff --name-only"
        paths |= _lines(dirty)

    if pathspec:
        # `git commit -- <pathspec>` commits the tracked files under the pathspec that differ
        # from HEAD — `ls-files` would demand every untouched file under a directory pathspec.
        base = base_rev(cwd, env)
        changed = run_git(["diff", "--name-only", base, "--"] + pathspec, cwd, env)
        if changed is None:
            return None, "diff --name-only %s -- %s" % (base, " ".join(pathspec))
        paths |= _lines(changed)

    return paths, None


def commit_source(rest, cwd=None, env=None):
    """"worktree" when `git commit <rest>` publishes working-tree content (a pathspec, `--pathspec-from-file`,
    `-a` or `-i`), else "index". The same rule `staged_paths` applies to choose its paths.

    With `cwd`, a `-i` commit that also stages paths outside its pathspec is checked: git publishes those paths
    from the index and the pathspec paths from the working tree, which one source cannot name, so a staged path
    that differs from its working copy (or a failed read) returns None. Each caller fails per its own posture."""
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    if not (pathspec or include_dirty or _pathspec_file(rest) is not None or _includes_index(rest)):
        return "index"
    if cwd is not None and _includes_index(rest) and not include_dirty and _mixed_publication(rest, cwd, env):
        return None
    return "worktree"


def index_sourced_paths(rest, cwd, env, paths):
    """The subset of `paths` that `git commit <rest>` publishes from the index: all of them without a pathspec,
    none for an `-a` commit or a pathspec commit without `-i`, and under `-i` those its pathspec (command line
    plus `--pathspec-from-file`) does not name. None when git cannot say."""
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    paths = {p.replace("\\", "/") for p in paths}
    has_spec = bool(pathspec) or _pathspec_file(rest) is not None
    if include_dirty or not paths or (has_spec and not _includes_index(rest)):
        return set()
    pathspec, problem = _full_pathspec(rest, cwd, pathspec)
    if problem:
        return None
    if not pathspec:
        return paths
    staged = run_git(["diff", "--cached", "--name-only", "--"] + pathspec, cwd, env)
    worktree = run_git(["diff", "--name-only", "--"] + pathspec, cwd, env)
    if staged is None or worktree is None:
        return None
    return paths - _lines(staged) - _lines(worktree)


def _mixed_publication(rest, cwd, env):
    """True when a `-i` commit stages a path outside its pathspec whose working copy differs from the index, or
    when git cannot say."""
    pathspec, problem = _full_pathspec(rest, cwd, parse_commit_args(rest)[2])
    if problem or not pathspec:
        return bool(problem)
    cached = run_git(["diff", "--cached", "--name-only"], cwd, env)
    if cached is None:
        return True
    others = index_sourced_paths(rest, cwd, env, _lines(cached))
    if others is None:
        return True
    if not others:
        return False
    others = sorted(others)
    differing = run_git(["diff", "--name-only", "--"] + others, cwd, env)
    return differing is None or bool(differing.strip())


def _flag_values(rest, long_name, short_letter):
    """(values, stdin) for a repeatable value flag: `--long v`, `--long=v`, `-x v`, `-xv`, and the letter
    inside a combined run such as `-am v`. `stdin` is True when a value is `-`."""
    values, i = [], 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            break
        if a == long_name and i + 1 < len(rest):
            values.append(rest[i + 1])
            i += 2
            continue
        if a.startswith(long_name + "="):
            values.append(a.split("=", 1)[1])
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            letters = a[1:]
            cut = min((letters.index(ch) for ch in _SHORT_VALUE_LETTERS if ch in letters), default=None)
            if cut is not None and short_letter and letters[cut] == short_letter:
                attached = letters[cut + 1:]
                if attached:
                    values.append(attached)
                elif i + 1 < len(rest):
                    values.append(rest[i + 1])
                    i += 2
                    continue
            elif cut is not None and cut == len(letters) - 1 and i + 1 < len(rest):
                i += 2
                continue
        elif a in _COMMIT_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    return values, "-" in values


def commit_message(rest, cwd):
    """The message `git commit <rest>` carries: every `-m`/`--message` as a paragraph, `-F`/`--file` read
    relative to `cwd`, and each `--trailer` as a trailer line, in that order. None when the message cannot be
    known here: `-F -` (stdin), an editor commit (no `-m`, `-F` or `--trailer`), or an unreadable `-F` file."""
    messages, _ = _flag_values(rest, "--message", "m")
    files, from_stdin = _flag_values(rest, "--file", "F")
    trailers, _ = _flag_values(rest, "--trailer", None)
    if from_stdin:
        return None
    parts = list(messages)
    for name in files:
        try:
            with open(os.path.join(cwd, name), encoding="utf-8", errors="replace") as fh:
                parts.append(fh.read().rstrip("\n"))
        except OSError:
            return None
    lines = []
    for trailer in trailers:
        cut = min((trailer.index(sep) for sep in ":=" if sep in trailer), default=None)
        lines.append("%s: %s" % (trailer[:cut].strip(), trailer[cut + 1:].strip()) if cut is not None else trailer)
    if not parts and not lines:
        return None
    return "\n\n".join(parts + (["\n".join(lines)] if lines else []))


def fold_tracked_adds(command, cwd, *, _exported=None, _inherited=None):
    """Fold an adjacent tracked-only `add && commit` into a pathspec commit, including literal launchers.
    A new path, option-driven add, different repo/index or shell plumbing stays for the guard."""
    exported, inherited = dict(_exported or {}), dict(_inherited or {})
    original = command
    body_replacements = []
    for start, end, segment in executable_spans(command):
        parsed_words = command_words(segment)
        if parsed_words is None:
            continue
        words = parsed_words[0]
        prefix = command[:start]
        _, target, before_env = _git_invocations(prefix, cwd, dict(exported), inherited)
        known = dict(inherited, **literal_assignments(prefix, cwd))
        launched = _launcher_context(segment, target, before_env, known)
        if launched is None:
            continue
        body, target, child_env = launched
        if any(re.search(r'[$`]', re.sub(r"'[^']*'", '', segment[word.start:word.end]))
               for word in words[body.start:body.end]):
            continue
        child_known = ({key: value for key, value in child_env.items() if value is not None}
                       if body.scoped else known)
        folded = fold_tracked_adds(body.body, target, _exported=child_env, _inherited=child_known)
        if folded is not None and body.start < len(words):
            body_replacements.append((start + words[body.start].start, start + words[body.end - 1].end,
                                      shlex.quote(folded)))
    for start, end, replacement in reversed(body_replacements):
        command = command[:start] + replacement + command[end:]
    spans = list(executable_spans(command))
    replacements = []
    for first, second in zip(spans, spans[1:]):
        start, end, add = first
        if start is None or second[0] is None or not re.fullmatch(r'\s*&&\s*', command[end:second[0]]):
            continue
        add_words = command_words(add)
        commit_words = command_words(second[2])
        if add_words is None or commit_words is None or add_words[1] != len(add.rstrip()):
            continue
        words = add_words[0]
        head = executable_word(words, add)
        # Removing an add-only assignment or configuration changes the call's meaning.
        if head != 0 or any(w.value != '-C' and w.value.startswith('-')
                            for w in words[1:next((i for i, w in enumerate(words) if w.value == 'add'), len(words))]):
            continue
        parsed = git_invocation(add)
        if parsed is None or not parsed[0] or parsed[0][0] != 'add':
            continue
        args, chdir, inline = parsed
        paths = args[1:]
        if paths[:1] == ['-f']:
            paths = paths[1:]
        if paths[:1] == ['--']:
            paths = paths[1:]
        if not paths or any(p.startswith('-') or '$' in p or '`' in p for p in paths):
            continue
        prefix = command[:start]
        invocations = [call for call in _git_invocations(prefix + second[2], cwd, dict(exported), inherited)[0]
                       if call.sub in COMMIT_LIKE]
        if len(invocations) != 1 or invocations[0].sub != 'commit':
            continue
        commit = invocations[0]
        cwords = commit_words[0]
        chead = executable_word(cwords, second[2])
        globals_ = cwords[chead + 1:len(cwords) - len(commit.rest) - 1]
        if any(w.value.startswith('-') and w.value != '-C' for w in globals_):
            continue
        if any(any(ch in w.value for ch in '*?[') and second[2][w.start:w.end][:1] not in ('\"', "'")
               for w in cwords) or any(any(ch in w.value for ch in '*?[') and add[w.start:w.end][:1] not in ('\"', "'")
                                      for w in words):
            continue
        subword = words[len(words) - len(args)]
        probe = add[:subword.start] + 'commit' + add[subword.end:]
        add_probe = [call for call in _git_invocations(prefix + probe, cwd, dict(exported), inherited)[0]
                     if call.sub in COMMIT_LIKE]
        if len(add_probe) != 1:
            continue
        target = add_probe[0]
        if target.cwd != commit.cwd or target.git_env != commit.git_env:
            continue
        dirty, amend, existing = parse_commit_args(commit.rest)
        if dirty or amend or _includes_index(commit.rest) or _pathspec_file(commit.rest) is not None:
            continue
        tracked = run_git(['ls-files', '-z', '--'] + paths, target.cwd, target.git_env)
        new = run_git(['ls-files', '--others', '-z', '--'] + paths, target.cwd, target.git_env)
        if not tracked or new is None or new:
            continue
        # Every named operand must select tracked content, not merely one of the operands.
        if any(not run_git(['ls-files', '-z', '--', p], target.cwd, target.git_env) for p in paths):
            continue
        if existing:
            selected = run_git(['ls-files', '-z', '--'] + existing, target.cwd, target.git_env)
            if selected is None or set(selected.split('\0')) != set(tracked.split('\0')):
                continue
        insertion = commit_words[1]
        replacement = second[2] if existing else (second[2][:insertion].rstrip() + ' -- ' +
                      shlex.join(paths) + ' ' + second[2][insertion:])
        replacements.append((start, second[1], replacement))
    for start, end, replacement in reversed(replacements):
        command = command[:start] + replacement + command[end:]
    return command if command != original else None


def main():
    """PreToolUse normalizer; guards later in the dispatcher judge its updated command."""
    payload = json.load(sys.stdin)
    if payload.get('tool_name') != 'Bash':
        return 0
    tool_input = payload.get('tool_input') or {}
    command = fold_tracked_adds(tool_input.get('command') or '', payload.get('cwd') or '.')
    if command:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
              'updatedInput': dict(tool_input, command=command),
              'additionalContext': 'Tracked add+commit folded to a pathspec commit; guards judge that publication.'}}))
    return 0


# merge / cherry-pick / revert options whose value is the next argument, never a ref.
_VALUE_OPTIONS = {"-m", "-F", "--file", "-s", "--strategy", "-X", "--strategy-option", "--into-name"}


def _first_ref(rest):
    skip = False
    for a in rest:
        if skip:
            skip = False
        elif a in _VALUE_OPTIONS:
            skip = True
        elif not a.startswith("-"):
            return a
    return None


def incoming_paths(sub, rest, cwd, env=None):
    """Paths a merge / cherry-pick / revert would commit. (paths, None) or (None, failing cmd).
    A `--no-commit` / `-n` run stages without committing and returns an empty set — the later
    `git commit` is where the staged content gets judged. A bare rebase or am names no incoming ref
    here, so it returns an empty set too."""
    if sub not in ("merge", "cherry-pick", "revert") or any(a in ("--no-commit", "-n") for a in rest):
        return set(), None
    env, problem = resolve_index(env, cwd)
    if problem:
        return None, problem
    ref = _first_ref(rest)
    if ref is None:
        return set(), None
    if sub == "merge":
        out = run_git(["diff", "--name-only", "HEAD..." + ref], cwd, env)
        cmd = "diff --name-only HEAD...%s" % ref
    else:
        out = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", ref], cwd, env)
        cmd = "diff-tree --name-only -r %s" % ref
    if out is None:
        return None, cmd
    return _lines(out), None


def ref_resolves(rest, cwd, env=None):
    """True when the first ref of a merge / cherry-pick / revert names a commit in `cwd`'s repo."""
    ref = _first_ref(rest)
    return ref is not None and run_git(["rev-parse", "--verify", "-q", ref + "^{commit}"], cwd, env) is not None


_REF_PREFIXES = ("refs/heads/", "refs/remotes/", "refs/tags/")


def refs_created(sub, rest):
    """Ref names a `fetch` / `branch` / `tag` / `update-ref` would create: each refspec destination
    (also without its `refs/...` prefix), and `<remote>/` for a fetch that names a remote only."""
    args = [a for a in rest if not a.startswith("-")]
    made = set()
    if sub == "fetch":
        if len(args) == 1:
            made.add(args[0] + "/")
        for spec in args[1:]:
            made.add(spec.lstrip("+").rsplit(":", 1)[-1] if ":" in spec else "")
    elif args:
        made.add(args[0])
    made.discard("")
    return made | {m[len(p):] for m in made for p in _REF_PREFIXES if m.startswith(p)}


def ref_names_created(created, rest):
    """True when the first ref of a merge / cherry-pick / revert is one of `created` (`refs_created`)."""
    ref = _first_ref(rest)
    return ref is not None and any(ref == c or (c.endswith("/") and ref.startswith(c)) for c in created)


def bypass_declared(inline_env, var):
    """True when `VAR=1` is set in the hook's own environment or inline on the command.
    Hook processes inherit the harness env, not the command's, so an inline prefix is the only
    per-command hatch — and it is transcript-auditable."""
    return os.environ.get(var) == "1" or (inline_env or {}).get(var) == "1"


if __name__ == '__main__':
    sys.exit(main())
