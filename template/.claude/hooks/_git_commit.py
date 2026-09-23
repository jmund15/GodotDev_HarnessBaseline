"""
Library (not a hook): the one parser for every PreToolUse guard that gates `git commit`.

A guard that matches the raw command string denies on text the shell never runs (a heredoc
commit message quoting the command), evaluates the wrong repo (`git -C <path>`, a preceding
`cd <path>`), and misses the shapes that widen a commit's content (`-a`, `--amend`, a
pathspec). Nine guards each parsed privately and each carried a different subset of those
holes; this module is the single home, so a fix lands in every guard at once.

    commit_invocations(command, cwd)    -> [CommitInvocation]  every executing commit-like segment
    staged_paths(rest, cwd, env)        -> (paths, None) | (None, failing-git-subcommand)
    incoming_paths(sub, rest, cwd, env) -> same shape, for merge / cherry-pick / revert
    git_environ(git_env)                -> os.environ with a command's GIT_* variables applied
    bypass_declared(inline_env, VAR)    -> True when VAR=1 in the hook's env OR inline on the command

A commit publishes the index its GIT_* variables name (`GIT_INDEX_FILE=<f> git commit`, or an
earlier `export`). Each invocation carries them as `git_env`, and every git read made to judge that
commit passes it: reading the default index judges content the commit never had.

Fail posture belongs to the caller: this module reports git failure as `None`, and a guard
decides whether that denies (enforcement) or allows (advisory).
"""
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field

try:
    from _command_text import executable_text
except Exception:  # pragma: no cover - a missing sibling degrades to raw matching
    def executable_text(command):
        return command

__all__ = [
    "CommitInvocation", "COMMIT_LIKE", "GLOBAL_FLAGS_WITH_VALUE",
    "segments", "git_invocation", "cd_target", "commit_invocations",
    "parse_commit_args", "run_git", "staged_paths", "incoming_paths", "bypass_declared",
    "export_assignments", "command_git_env", "git_environ",
    "resolve_cd", "literal_assignments", "expand_literal",
]

SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# Global git flags that may precede the subcommand; these take a value.
GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# Subcommands that create a commit whose content is not the index alone.
COMMIT_LIKE = {"commit", "merge", "cherry-pick", "revert"}

_COMMIT_VALUE_FLAGS = {"-m", "--message", "-F", "--file", "-c", "-C", "--author", "--date",
                       "--reuse-message", "--fixup", "--squash", "--trailer", "--pathspec-from-file"}
_SHORT_VALUE_LETTERS = "mFcC"


@dataclass
class CommitInvocation:
    sub: str                 # "commit" | "merge" | "cherry-pick" | "revert"
    rest: list               # args after the subcommand
    cwd: str                 # the repo the command targets (payload cwd + cd + -C)
    inline_env: dict = field(default_factory=dict)
    git_env: dict = field(default_factory=dict)   # GIT_* the git process runs with; None unsets


_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def segments(command):
    """Segments split at `||`, `&&`, `;`, `|`, `&` and newlines outside quotes. A separator inside a
    quoted argument (a search pattern naming a git command) does not split. An unbalanced quote splits
    at every separator, so a malformed command can only over-block."""
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
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def git_invocation(segment):
    """(args, chdir, inline_env) for a git segment, or None if not a git invocation."""
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
    chdir = None
    while j < len(args) and args[j].startswith("-"):
        if args[j] == "-C" and j + 1 < len(args):
            chdir = args[j + 1]
        j += 2 if args[j] in GLOBAL_FLAGS_WITH_VALUE else 1
    return args[j:], chdir, inline_env


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


def literal_assignments(command):
    """{NAME: value} for every variable the command assigns exactly once, by a bare or exported
    `NAME=value` segment whose value is a literal. A name that appears bare anywhere else
    (`read NAME`, `for NAME in`, a second assignment) is absent: its value is not knowable."""
    found = {}
    for segment in segments(command):
        tokens = _tokens(segment)
        if len(tokens) not in (1, 2) or (len(tokens) == 2 and tokens[0] != "export"):
            continue
        m = _ASSIGN_SEGMENT.match(" ".join(tokens))
        if m and not _NON_LITERAL.search(m.group(2)):
            found.setdefault(m.group(1), []).append(m.group(2))
    result = {}
    for name, values in found.items():
        bare = re.findall(r"(?<![A-Za-z0-9_$])(?<!\$\{)" + name + r"(?![A-Za-z0-9_])", command)
        if len(values) == 1 and len(bare) == 1:
            result[name] = values[0]
    return result


def expand_literal(token, assignments):
    """`token` with `$NAME` / `${NAME}` replaced from `assignments`, or None when an unknown name,
    a substitution or any other `$` remains."""
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


def command_git_env(exported, inline_env):
    """The GIT_* variables a git segment runs with: earlier exports, then its own inline prefix."""
    merged = dict(exported or {})
    merged.update(inline_env or {})
    return {key: value for key, value in merged.items() if key.startswith("GIT_")}


def commit_invocations(command, cwd):
    """Every executing commit-like git segment in `command`, heredoc bodies excluded, with
    the repo each one targets. An empty list means the command commits nothing."""
    cwd = cwd or "."
    found = []
    exported = {}
    text = executable_text(command or "")
    assigned_once = literal_assignments(text)
    for segment in segments(text):
        moved = cd_target(segment)
        if moved is not None:
            cwd = resolve_cd(cwd, expand_literal(moved, assigned_once) or moved)
            continue
        assigned = export_assignments(segment)
        if assigned is not None:
            exported.update(assigned)
            continue
        parsed = git_invocation(segment)
        if parsed is None:
            continue
        args, chdir, inline_env = parsed
        if not args or args[0] not in COMMIT_LIKE:
            continue
        target = resolve_cd(cwd, expand_literal(chdir, assigned_once) or chdir) if chdir else cwd
        found.append(CommitInvocation(args[0], args[1:], target, inline_env,
                                      command_git_env(exported, inline_env)))
    return found


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


def run_git(args, cwd, env=None):
    """git stdout, or None on any failure (non-zero exit, missing binary, timeout). `env` is the
    invocation's `git_env`."""
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


def staged_paths(rest, cwd, env=None):
    """Paths a `git commit <rest>` will publish. A pathspec commit (after `--`, bare, or from
    `--pathspec-from-file`) publishes only the pathspec's changed files, as git's default `--only`
    mode does, unless `-i`/`--include` adds the index. Otherwise the index is published. HEAD's
    paths join under `--amend`, dirty tracked files under `-a`. An unreadable pathspec file,
    including stdin (`-`), is a failure. `env` is the invocation's `git_env`. (paths, None) on success;
    (None, failing-step) on failure."""
    include_dirty, amend, pathspec = parse_commit_args(rest)
    paths = set()

    spec_file = _pathspec_file(rest)
    if spec_file is not None:
        if spec_file in ("", "-"):
            return None, "pathspec-from-file " + (spec_file or "(missing value)")
        try:
            with open(os.path.join(cwd, spec_file), encoding="utf-8") as fh:
                pathspec = pathspec + [line.strip() for line in fh.read().replace("\0", "\n").splitlines()
                                       if line.strip()]
        except OSError:
            return None, "pathspec-from-file " + spec_file

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
        changed = run_git(["diff", "--name-only", "HEAD", "--"] + pathspec, cwd, env)
        if changed is None:
            return None, "diff --name-only HEAD -- " + " ".join(pathspec)
        paths |= _lines(changed)

    return paths, None


def _first_ref(rest):
    for a in rest:
        if not a.startswith("-"):
            return a
    return None


def incoming_paths(sub, rest, cwd, env=None):
    """Paths a merge / cherry-pick / revert would commit. (paths, None) or (None, failing cmd).
    A `--no-commit` / `-n` run stages without committing and returns an empty set — the later
    `git commit` is where the staged content gets judged."""
    if any(a in ("--no-commit", "-n") for a in rest):
        return set(), None
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


def bypass_declared(inline_env, var):
    """True when `VAR=1` is set in the hook's own environment or inline on the command.
    Hook processes inherit the harness env, not the command's, so an inline prefix is the only
    per-command hatch — and it is transcript-auditable."""
    return os.environ.get(var) == "1" or (inline_env or {}).get(var) == "1"
