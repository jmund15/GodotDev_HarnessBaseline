"""
Library (not a hook): the one parser for every PreToolUse guard that gates `git commit`.

A guard that matches the raw command string denies on text the shell never runs (a heredoc
commit message quoting the command), evaluates the wrong repo (`git -C <path>`, a preceding
`cd <path>`), and misses the shapes that widen a commit's content (`-a`, `--amend`, a
pathspec). Nine guards each parsed privately and each carried a different subset of those
holes; this module is the single home, so a fix lands in every guard at once.

    commit_invocations(command, cwd) -> [CommitInvocation]  every executing commit-like segment
    staged_paths(rest, cwd)          -> (paths, None) | (None, failing-git-subcommand)
    incoming_paths(sub, rest, cwd)   -> same shape, for merge / cherry-pick / revert
    bypass_declared(inline_env, VAR) -> True when VAR=1 in the hook's env OR inline on the command

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
]

SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")

# Global git flags that may precede the subcommand; these take a value.
GLOBAL_FLAGS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

# Subcommands that create a commit whose content is not the index alone.
COMMIT_LIKE = {"commit", "merge", "cherry-pick", "revert"}

_COMMIT_VALUE_FLAGS = {"-m", "--message", "-F", "--file", "-c", "-C", "--author", "--date",
                       "--reuse-message", "--fixup", "--squash", "--trailer"}
_SHORT_VALUE_LETTERS = "mFcC"


@dataclass
class CommitInvocation:
    sub: str                 # "commit" | "merge" | "cherry-pick" | "revert"
    rest: list               # args after the subcommand
    cwd: str                 # the repo the command targets (payload cwd + cd + -C)
    inline_env: dict = field(default_factory=dict)


def segments(command):
    for raw in SEGMENT_SPLIT.split(command):
        raw = raw.strip()
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


def commit_invocations(command, cwd):
    """Every executing commit-like git segment in `command`, heredoc bodies excluded, with
    the repo each one targets. An empty list means the command commits nothing."""
    cwd = cwd or "."
    found = []
    for segment in segments(executable_text(command or "")):
        moved = cd_target(segment)
        if moved is not None:
            cwd = os.path.join(cwd, os.path.expanduser(moved))
            continue
        parsed = git_invocation(segment)
        if parsed is None:
            continue
        args, chdir, inline_env = parsed
        if not args or args[0] not in COMMIT_LIKE:
            continue
        target = os.path.join(cwd, chdir) if chdir else cwd
        found.append(CommitInvocation(args[0], args[1:], target, inline_env))
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


def run_git(args, cwd):
    """git stdout, or None on any failure (non-zero exit, missing binary, timeout)."""
    try:
        result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _lines(out):
    return {line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}


def staged_paths(rest, cwd):
    """Paths a `git commit <rest>` will publish: the index, plus HEAD's paths under `--amend`,
    plus dirty tracked files under `-a`, plus tracked-and-changed files under a pathspec.
    (paths, None) on success; (None, failing-git-subcommand) on any git failure."""
    include_dirty, amend, pathspec = parse_commit_args(rest)
    paths = set()

    cached = run_git(["diff", "--cached", "--name-only"], cwd)
    if cached is None:
        return None, "diff --cached --name-only"
    paths |= _lines(cached)

    if amend:
        shown = run_git(["show", "--name-only", "--pretty=format:", "HEAD"], cwd)
        if shown is None:
            return None, "show --name-only HEAD"
        paths |= _lines(shown)

    if include_dirty:
        dirty = run_git(["diff", "--name-only"], cwd)
        if dirty is None:
            return None, "diff --name-only"
        paths |= _lines(dirty)

    if pathspec:
        # `git commit -- <pathspec>` commits the tracked files under the pathspec that differ
        # from HEAD — `ls-files` would demand every untouched file under a directory pathspec.
        changed = run_git(["diff", "--name-only", "HEAD", "--"] + pathspec, cwd)
        if changed is None:
            return None, "diff --name-only HEAD -- " + " ".join(pathspec)
        paths |= _lines(changed)

    return paths, None


def _first_ref(rest):
    for a in rest:
        if not a.startswith("-"):
            return a
    return None


def incoming_paths(sub, rest, cwd):
    """Paths a merge / cherry-pick / revert would commit. (paths, None) or (None, failing cmd).
    A `--no-commit` / `-n` run stages without committing and returns an empty set — the later
    `git commit` is where the staged content gets judged."""
    if any(a in ("--no-commit", "-n") for a in rest):
        return set(), None
    ref = _first_ref(rest)
    if ref is None:
        return set(), None
    if sub == "merge":
        out = run_git(["diff", "--name-only", "HEAD..." + ref], cwd)
        cmd = "diff --name-only HEAD...%s" % ref
    else:
        out = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", ref], cwd)
        cmd = "diff-tree --name-only -r %s" % ref
    if out is None:
        return None, cmd
    return _lines(out), None


def bypass_declared(inline_env, var):
    """True when `VAR=1` is set in the hook's own environment or inline on the command.
    Hook processes inherit the harness env, not the command's, so an inline prefix is the only
    per-command hatch — and it is transcript-auditable."""
    return os.environ.get(var) == "1" or (inline_env or {}).get(var) == "1"
