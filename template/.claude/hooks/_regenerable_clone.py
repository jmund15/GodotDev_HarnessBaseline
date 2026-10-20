#!/usr/bin/env python3
"""Recoverability and live worktree ownership for the destructive-action floor guards.

`session_created_worktree` owns the full-path, creation-interval proof; callers cannot infer
ownership from a basename. `is_retired_worktree` checks whether retirement loses local work.

A standalone scratch clone is regenerable only when all these hold:
- the token is a literal path: no glob, variable, `~` or `..`;
- it resolves strictly inside `<project>/.claude/scratch/`, never scratch itself;
- it has a `.git` directory and is its own top level;
- it has one worktree, a remote, no changed, untracked or ignored files, no stash, and
  no branch or tag commit unreachable from a remote-tracking ref.

Missing or ambiguous evidence fails closed.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

from _git_commit import (resolve_cd, segments, git_invocation, cd_target,
                         executable_text, literal_assignments, expand_literal)

_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
# A proof running the whole hook on a loaded machine raises the bound through the environment.
try:
    _GIT_TIMEOUT_S = int(os.environ.get("REGENERABLE_CLONE_GIT_TIMEOUT_S", "10"))
except ValueError:
    _GIT_TIMEOUT_S = 10


def _git(args: list[str], cwd: Path) -> str | None:
    """stdout of `git <args>` run in `cwd`, or None on any failure (fail closed).

    Read-only by construction:
    - `--no-optional-locks` stops `status` from taking the index lock to refresh it. A timeout kill
      would otherwise leave a stale `index.lock`.
    - `GIT_CEILING_DIRECTORIES` stops git from climbing into an enclosing repository when the
      target's `.git` disappears mid-check; git would otherwise lock and judge the project repo.
    """
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(cwd.parent)}
    try:
        proc = subprocess.run(["git", "--no-optional-locks", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=_GIT_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _timestamp(row: dict) -> float | None:
    try:
        return datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _added_worktrees(command: str, cwd: str) -> list[Path]:
    text = executable_text(command)
    if text != command or '$' in text or '`' in text:
        return []
    parts = list(segments(text))
    separators, cursor = [], 0
    for part in parts:
        start = text.find(part, cursor)
        separators.append(text[cursor:start].strip())
        cursor = start + len(part)
        try:
            words = shlex.split(part, comments=True)
        except ValueError:
            return []
        if (not words or '#' in part or '\\' in part
                or not (git_invocation(part) or cd_target(part) is not None
                        or words[0] in ('true', 'false', 'echo', 'printf', 'sleep')
                        or all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=[^;|&(){}<>]*', w)
                               for w in words))):
            return []
    if any(s not in ('', ';', '&&') for s in separators) or text[cursor:].strip():
        return []
    assignments = literal_assignments(command)
    found = []
    for index, segment in enumerate(parts):
        required = all(s == '&&' for s in separators[index + 1:])
        moved = cd_target(segment)
        if moved is not None:
            value = expand_literal(moved, assignments)
            target = resolve_cd(cwd, value) if value and cwd else None
            # An earlier list may have failed its cd while the final list still succeeded.
            same = target and cwd and _same_path(Path(target).resolve(), Path(cwd).resolve())
            cwd = target if required or same else None
            continue
        parsed = git_invocation(segment)
        if not parsed or not cwd or not required:
            continue
        args, chdir, env = parsed
        if args[:2] != ['worktree', 'add'] or env or '$(' in segment or '`' in segment:
            continue
        found += _add_target(args, chdir, cwd, assignments)
    return found


def _add_target(args: list[str], chdir: str | None, cwd: str, assignments: dict) -> list[Path]:
    """The resolved path a parsed `worktree add` creates, or nothing when it is not a literal."""
    base = expand_literal(chdir, assignments) if chdir else cwd
    if base is None:
        return []
    base = resolve_cd(cwd, base) if chdir else base
    pos = 2
    while pos < len(args) and args[pos].startswith('-'):
        if args[pos] in ('-b', '-B', '--orphan'):
            pos += 2
        elif args[pos] in ('-q', '--quiet', '-d', '--detach', '--lock', '--no-checkout'):
            pos += 1
        elif args[pos] == '--':
            pos += 1
            break
        else:
            break
    if pos < len(args) and not args[pos].startswith('-'):
        value = expand_literal(args[pos], assignments)
        if value and not _UNSAFE_PATH_CHARS.search(value):
            return [Path(resolve_cd(base, value)).resolve()]
    return []


_ADD_SKIPPERS = frozenset({'exit', 'return', 'exec', 'set', 'trap', 'eval', 'source', '.', 'if', 'then', 'else',
                           'elif', 'while', 'until', 'for', 'case', 'function', '{', '(', 'cd', 'pushd', 'popd'})
_TAIL_FILTER = re.compile(r'\s*tail(?:\s+-n\s*\d+|\s+-\d+)?\s*')
_FAILURE_LINE = re.compile(r'(?m)^\s*(?:fatal|error):')


def _visible_git_invocation(segment: str, cwd: str):
    """A literal git call with visible errors and at most the supported tail filter."""
    pipeline = segment.split('|')
    if len(pipeline) > 2 or (len(pipeline) == 2 and not _TAIL_FILTER.fullmatch(pipeline[1])):
        return []
    head = pipeline[0].strip()
    if head.endswith('2>&1'):
        head = head[:-4].strip()
    if any(c in head for c in '<>$`\\#'):
        return []
    parsed = git_invocation(head)
    if not parsed or not cwd:
        return []
    args, chdir, env = parsed
    if env:
        return []
    return parsed


def _visible_add_target(segment: str, cwd: str) -> list[Path]:
    """The target of a literal, visible `git worktree add`, else nothing."""
    parsed = _visible_git_invocation(segment, cwd)
    if not parsed:
        return []
    args, chdir, _ = parsed
    if args[:2] != ['worktree', 'add']:
        return []
    return _add_target(args, chdir, cwd, {})


def _first_visible_add(command: str, cwd: str) -> list[Path]:
    """The paths of the adds that run first in their call with their errors in the call's output.

    Accepts `git [-C <dir>] worktree add <args> [2>&1] [| tail -N]` as the first list (after any literal
    `cd`, which `&&` makes binding and `;` admits only to the directory it already runs in), and each
    list of the same form joined to it by `&&`, `;` or a newline, followed by anything: what follows
    cannot undo or skip them. A failed add always prints `fatal:` or `error:`, so the caller proves
    success from the result's text (`_FAILURE_LINE`), not its exit status. `||` after an add, a
    discarded stderr, another filter or any expansion in its list ends the run of adds; on the first
    add it yields nothing."""
    while True:
        match = re.search(r'\|\||&&|;|\n', command)
        first, sep = (command[:match.start()], match.group(0)) if match else (command, '')
        if sep == '||':
            return []
        moved = cd_target(first) if '$' not in first and '`' not in first else None
        if moved is None:
            parsed = git_invocation(first.split('|')[0].strip().removesuffix('2>&1').strip())
            if parsed and parsed[0][:2] == ['worktree', 'add']:
                break
            words = first.split()
            # A `;`-joined simple command before the add cannot skip it; one that can end the shell,
            # move it or branch around the add makes the add's run unknown.
            if (sep in (';', '\n') and words and words[0] not in _ADD_SKIPPERS
                    and not re.search(r'\b(?:cd|pushd|popd|exit|exec)\b', first)):
                command = command[match.end():]
                continue
            break
        target = resolve_cd(cwd, moved) if cwd else None
        # Past `;` a failed cd leaves the old cwd, so only a cd to that same directory is known.
        if not target or (sep != '&&' and not _same_path(Path(target).resolve(), Path(cwd).resolve())):
            return []
        cwd, command = target, command[match.end():]
    found = _visible_add_target(first, cwd)
    while found and match:
        command = command[match.end():]
        match = re.search(r'\|\||&&|;|\n', command)
        segment, sep = (command[:match.start()], match.group(0)) if match else (command, '')
        target = _visible_add_target(segment, cwd) if sep != '||' else []
        if not target:
            # A submodule init between literal loop iterations cannot skip the next add.
            parsed = _visible_git_invocation(segment, cwd)
            if sep in (';', '\n') and parsed and parsed[0][:3] == ['submodule', 'update', '--init']:
                continue
            break
        found += target
    return found


_LOOP = re.compile(r'(?P<lead>^|;\s*|\n\s*|&&\s*)for\s+(?P<var>[A-Za-z_]\w*)\s+in\s+'
                   r'(?P<words>[^;&|<>()$`\n\\*?\[\]{}~]*?)\s*[;\n]\s*do\s+(?P<body>.*?)\s*[;\n]\s*done'
                   r'(?=\s*(?:[;\n]|&&|$))', re.S)
_LOOP_UNSAFE_BODY = re.compile(r'\b(?:for|while|until|if|case|select|break|continue|exit|return|exec|eval|trap'
                               r'|function|shift|set)\b|[(){}`]|(?<![&>])&(?!&)')


def _unrolled_loops(command: str) -> str | None:
    """`command` with each `for VAR in <literal words>; do <body>; done` replaced by its body once per
    word, or None when there is no loop or a loop it matches is not fully literal. A loop it cannot match
    stays in the text, where `_first_visible_add` stops at its `for`.

    The word list holds no expansion, glob or brace. The body is straight-line: no nested or branching
    construct, no `break`/`continue`/`exit`, no background `&`, and `$VAR`/`${VAR}` is its only
    expansion. A leading `set -- $VAR` may split a quoted literal tuple into positional fields;
    changed IFS, missing fields and any other expansion fail closed. Nothing may pipe, redirect or `||`
    the finished loop, since that could hide an earlier iteration's error. The caller then treats
    each unrolled add as a literal one."""
    text, out, cursor = command.strip(), [], 0
    if re.search(r'\bIFS\s*=', text):
        return None
    for match in _LOOP.finditer(text):
        try:
            words = shlex.split(match['words'])
        except ValueError:
            return None
        var, body = match['var'], match['body']
        split = re.match(r'\s*set\s+--\s+\$' + re.escape(var) + r'\s*;\s*', body)
        if not words or any(not re.fullmatch(r'[\w.@%+=,:/ \t-]+', w) or w.startswith('-') for w in words):
            return None
        if split:
            body = body[split.end():]
            positions = list(re.finditer(r'\$(?:\{([1-9][0-9]*)\}|([1-9])(?![0-9]))', body))
            fields = [word.split() for word in words]
            if not positions or any(int(m[1] or m[2]) > len(row) for row in fields for m in positions):
                return None
            unrolled = [re.sub(r'\$(?:\{([1-9][0-9]*)\}|([1-9])(?![0-9]))',
                               lambda m, row=row: row[int(m[1] or m[2]) - 1], body) for row in fields]
        else:
            if any(len(word.split()) != 1 for word in words):
                return None
            names = r'\$\{%s\}|\$%s(?![A-Za-z0-9_])' % (var, var)
            unrolled = [re.sub(names, lambda _m, w=w: w, body) for w in words]
        if any('$' in b or _LOOP_UNSAFE_BODY.search(b) for b in unrolled):
            return None
        out.append(text[cursor:match.start()] + match['lead'] + '; '.join(unrolled))
        cursor = match.end()
    return ''.join(out) + text[cursor:] if out else None


_LANE_CREATE = re.compile(r'(?:python3?|py)\s+(?:\S*[/\\])?lane_worktree\.py\s+create\s+([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|$)')


def _lane_created_name(command: str) -> str | None:
    """The name a literal `lane_worktree.py create <name> ...` call adds, else None.

    The tool prints an `error:` line on every failure, so its result text decides success like a visible
    `git worktree add`'s. Only `&&` may join the call to earlier `cd`s or to anything after it; `;`, `||`, a
    newline, an expansion or a background `&` make its run unknown. A trailing `2>&1` and `| tail -N` are kept."""
    if re.search(r'\|\||;|\n|[$`\\<]|(?<![&>])&(?!&)', command):
        return None
    for part in (part.strip() for part in command.split('&&')):
        if cd_target(part) is not None:
            continue
        pipeline = part.split('|')
        if len(pipeline) > 2 or (len(pipeline) == 2 and not _TAIL_FILTER.fullmatch(pipeline[1])):
            return None
        match = _LANE_CREATE.match(pipeline[0].strip().removesuffix('2>&1').strip())
        return match.group(1) if match else None
    return None


def _result_text(block: dict) -> str:
    content = block.get('content')
    if isinstance(content, list):
        return '\n'.join(str(part.get('text', '')) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ''


def session_created_worktree(path: str, transcript_path: str | None) -> bool:
    """Prove ownership of this live linked worktree, not just its name (`orchestration` reference `worktrees.md`).

    Its full top-level path must occur in an add whose execution and success follow from
    the call's supported literal control flow and successful result. The current .git file's
    timestamp must lie inside that call; removal/recreation cannot inherit old ownership.
    A fully literal `for` loop counts as its unrolled adds, each proven as a visible add (`_unrolled_loops`).
    A literal `lane_worktree.py create <name>` call counts as a visible add of `<main>/.claude/worktrees/<name>`.
    Missing timestamps, results or provable control flow fail closed.
    """
    if not path or not transcript_path or _UNSAFE_PATH_CHARS.search(path):
        return False
    try:
        target = Path(path).resolve(strict=True)
        if not (target / '.git').is_file():
            return False
        top = _git(['rev-parse', '--show-toplevel'], target)
        gitdir = _git(['rev-parse', '--absolute-git-dir'], target)
        common = _git(['rev-parse', '--path-format=absolute', '--git-common-dir'], target)
        if (not top or not gitdir or not common
                or not _same_path(Path(top.strip()).resolve(), target)
                or _same_path(Path(gitdir.strip()).resolve(), Path(common.strip()).resolve())):
            return False
        born = (target / '.git').stat().st_mtime
    except (OSError, ValueError):
        return False
    sid = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    paths = [transcript_path]
    if sid:
        paths.append(os.path.join(os.path.dirname(transcript_path), sid + '.jsonl'))
    for transcript in dict.fromkeys(paths):
        pending = {}
        try:
            with open(transcript, encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or row.get('isSidechain'):
                        continue
                    stamp = _timestamp(row)
                    content = (row.get('message') or {}).get('content') or []
                    for block in content if isinstance(content, list) else []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'tool_use' and block.get('name') == 'Bash':
                            command = (block.get('input') or {}).get('command', '')
                            cwd = row.get('cwd')
                            if stamp is not None and cwd and 'worktree' in command:
                                unrolled = _unrolled_loops(command)
                                lane = _lane_created_name(command)
                                if lane is not None:
                                    made = Path(common.strip()).resolve().parent / '.claude' / 'worktrees' / lane
                                    if _same_path(made, target):
                                        pending[block.get('id')] = (stamp, True)
                                elif unrolled is not None:
                                    if any(_same_path(p, target) for p in _first_visible_add(unrolled, cwd)):
                                        pending[block.get('id')] = (stamp, True)
                                elif any(_same_path(p, target) for p in _added_worktrees(command, cwd)):
                                    pending[block.get('id')] = (stamp, False)
                                elif any(_same_path(p, target) for p in _first_visible_add(command, cwd)):
                                    pending[block.get('id')] = (stamp, True)
                        elif block.get('type') == 'tool_result':
                            start, visible = pending.pop(block.get('tool_use_id'), (None, False))
                            text = _result_text(block)
                            # A visible add's success is read from the text, so a later command's exit
                            # status cannot void it; a denial never ran and carries no `Exit code` line.
                            ran = not block.get('is_error') or (visible and text.startswith('Exit code '))
                            outcome = row.get('toolUseResult')  # a dict, or the error text on a failed call
                            if (start is not None and stamp is not None and ran
                                    and not (isinstance(outcome, dict) and outcome.get('interrupted'))
                                    and not (visible and _FAILURE_LINE.search(text))
                                    and start <= born <= stamp):
                                return True
        except (OSError, TypeError, ValueError):
            continue
    return False


def is_regenerable_clone(token: str, cwd: str, project_root: str, reasons: list[str] | None = None) -> bool:
    """True only when `token` names a clean, fully pushed standalone clone under scratch.

    Its status may hold only what `_first_loss` treats as regenerable, the same rule a retired
    worktree meets. If supplied, `reasons` receives the first failed condition."""
    def reject(reason):
        if reasons is not None:
            reasons.append(reason)
        return False

    if not token or _UNSAFE_PATH_CHARS.search(token):
        return reject('target is not a literal path')
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return reject('target contains path traversal or has no path')
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        scratch = (Path(project_root) / ".claude" / "scratch").resolve(strict=True)
    except OSError:
        return reject('target does not exist')
    if _same_path(target, scratch) or not any(_same_path(p, scratch) for p in target.parents):
        return reject('target is not inside .claude/scratch')
    if not (target / ".git").is_dir():
        return reject('target is not a standalone clone (no .git directory)')

    top = _git(["rev-parse", "--show-toplevel"], target)
    try:
        if top is None or not _same_path(Path(top.strip()).resolve(strict=True), target):
            return reject('target is not its own repository root')
    except OSError:
        return reject('target is not its own repository root')

    worktrees = _git(["worktree", "list", "--porcelain"], target)
    if worktrees is None or sum(1 for line in worktrees.splitlines() if line.startswith("worktree ")) != 1:
        return reject('clone has linked worktrees')
    remotes = _git(["remote"], target)
    if not remotes or not remotes.strip():
        return reject('clone has no remote')
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching"], target)
    if status is None:
        return reject('cannot read clone status')
    loss = _first_loss(status, target)
    if loss:
        return reject('clone has local files or changes: ' + loss)
    if _git(["stash", "list"], target) != "":
        return reject('clone has a stash')
    if _git(["rev-list", "--branches", "--tags", "--not", "--remotes"], target) != "":
        return reject('clone has commits on no remote branch')
    return True


_GENERATED_UNTRACKED_SUFFIXES = (".import",)
_REGENERABLE_IGNORED_SEGMENTS = frozenset({".godot", "__pycache__", ".pytest_cache", ".search-index", ".cache",
                                           "TestResults"})
_REGENERABLE_IGNORED_PAIRS = frozenset({(".claude", "cache"), (".claude", "logs")})


def _regenerable_ignored(entry: str) -> bool:
    parts = entry.split("/")
    return (bool(_REGENERABLE_IGNORED_SEGMENTS & set(parts))
            or any(pair in _REGENERABLE_IGNORED_PAIRS for pair in zip(parts, parts[1:])))


def _holds_no_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        return not any(p.is_file() or p.is_symlink() for p in path.rglob("*"))
    except OSError:
        return False


def _first_loss(status: str, root: Path, allow_ignored: bool = False) -> str | None:
    """The first `git status --porcelain --ignored=matching` line whose deletion loses work, or None.
    Untracked Godot `.import` files, ignored regenerable caches and ignored directories holding no file
    lose nothing; `allow_ignored` (the owner approved the removal) lets every ignored entry go."""
    for line in status.splitlines():
        entry = line[3:].rstrip("/")
        if line.startswith("?? ") and entry.endswith(_GENERATED_UNTRACKED_SUFFIXES):
            continue
        if line.startswith("!! ") and (allow_ignored or _regenerable_ignored(entry) or _holds_no_file(root / entry)):
            continue
        return line
    return None


def _pushed(repo: Path, reasons: list[str] | None = None, prefix: str = '') -> bool:
    """Every HEAD commit is on a remote, by hash or, for a lane landed by rebase, by patch: each
    local-only non-merge commit's patch is on the remote's default branch."""
    def reject(reason):
        if reasons is not None:
            reasons.append(prefix + reason)
        return False

    out = _git(["rev-list", "HEAD", "--not", "--remotes"], repo)
    if out is None:
        return reject('cannot verify HEAD reachability')
    if not out.strip():
        return True
    unpushed = 'HEAD ' + (_git(['rev-parse', '--short', 'HEAD'], repo) or '(unreadable)').strip() \
        + ' is on no remote branch'
    if _git(["rev-list", "--merges", "HEAD", "--not", "--remotes"], repo) != "":
        return reject(unpushed)
    default = (_git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], repo) or "").strip()
    if not default:
        return reject(unpushed)
    missing = _git(["rev-list", "--cherry-pick", "--right-only", "--no-merges", default + "...HEAD"], repo)
    if missing is None or missing.strip():
        return reject(unpushed)
    return True


def _registered_unlocked(listing: str, target: Path, reasons: list[str] | None = None) -> bool:
    for block in listing.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        try:
            path = Path(lines[0][len("worktree "):]).resolve(strict=True)
        except OSError:
            continue
        if _same_path(path, target):
            if any(line == "locked" or line.startswith("locked ") for line in lines):
                if reasons is not None:
                    reasons.append('worktree is locked')
                return False
            return True
    if reasons is not None:
        reasons.append('worktree is not registered')
    return False


# Under pre_bash_dispatch's 75 s hook budget: hashing a full orphaned checkout is the slow call.
_ORPHAN_HASH_TIMEOUT_S = 45


def _orphan_admin(target: Path) -> Path | None:
    """The admin directory a worktree's `.git` file names, when that directory is gone, else None."""
    try:
        text = (target / ".git").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("gitdir:"):
        return None
    admin = Path(text[len("gitdir:"):].strip())
    admin = admin if admin.is_absolute() else target / admin
    return None if admin.exists() else admin


def _unheld_file(git_dir: Path, work_tree: Path, root: Path, absent_index: Path) -> str | None:
    """The first non-ignored file under `work_tree` whose content `git_dir`'s object store lacks, or
    None. `absent_index` names no file, so git reads an empty index and writes nothing. A nested
    checkout (a submodule) is judged against `root`'s `.git/modules/<path>` store."""
    env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(work_tree),
           "GIT_INDEX_FILE": str(absent_index), "GIT_CEILING_DIRECTORIES": str(work_tree.parent)}

    def run(args, stdin=None):
        try:
            proc = subprocess.run(["git", "--no-optional-locks", *args], cwd=str(work_tree), input=stdin,
                                  capture_output=True, text=True, timeout=_ORPHAN_HASH_TIMEOUT_S, env=env)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    subs = []
    if (work_tree / ".gitmodules").is_file():
        listed = run(["config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"])
        subs = [line.split(" ", 1)[1].strip("/") for line in (listed or "").splitlines() if " " in line]
    for sub in subs:
        if not (work_tree / sub).is_dir() or not any((work_tree / sub).iterdir()):
            continue
        store = root / ".git" / "modules" / sub
        if not store.is_dir():
            return sub + "/ (a populated submodule with no store at .git/modules/" + sub + ")"
        loss = _unheld_file(store, work_tree / sub, root, absent_index)
        if loss:
            return sub + "/" + loss
    listed = run(["ls-files", "--others", "--exclude-standard", "-z"])
    if listed is None:
        return "(cannot list files)"
    files = [entry for entry in filter(None, listed.split("\0"))
             if not entry.endswith(_GENERATED_UNTRACKED_SUFFIXES)
             and not any(entry == sub or entry.startswith(sub + "/") for sub in subs)]
    if any(entry.endswith("/") for entry in files):
        return next(entry for entry in files if entry.endswith("/")) + " (a nested repository)"
    if not files:
        return None
    ids = run(["hash-object", "--stdin-paths"], "\n".join(files) + "\n")
    if ids is None or len(ids.split()) != len(files):
        return "(cannot hash files)"
    held = run(["cat-file", "--batch-check"], ids)
    if held is None:
        return "(cannot read the object store)"
    for path, line in zip(files, held.splitlines()):
        if line.endswith(" missing"):
            return path
    return None


def is_retired_worktree(token: str, cwd: str, project_root: str, allow_ignored: bool = False,
                        reasons: list[str] | None = None) -> bool:
    """True only when `token` names a linked worktree of `project_root`, directly under
    `<project>/.claude/worktrees/`, whose deletion loses nothing.

    `git worktree remove` refuses every worktree carrying a submodule, so a retired one can only go
    by a recursive delete followed by `git worktree prune`. The worktree must be registered and
    unlocked; its status may hold only untracked Godot `.import` files and ignored regenerable caches
    (`_REGENERABLE_IGNORED_SEGMENTS`, `_REGENERABLE_IGNORED_PAIRS`) or ignored directories holding no
    file, so ignored scratch evidence or local config blocks the delete unless `allow_ignored` (the
    owner approved removing this worktree, so its ignored files may go with it); its HEAD and every populated
    submodule's HEAD must be on a remote (`_pushed`: by hash, or by patch for a rebased lane), and each
    submodule must be clean. An orphan (git pruned its admin directory, so status cannot be read)
    passes only under `allow_ignored`, and only when every non-ignored file's content, submodules
    included, is already a blob in the repository's object store: commits and branches live in the
    shared store, so unsaved file content is the only loss. A leftover directory with no `.git` file
    (an interrupted removal) is judged the same way. If supplied, `reasons` receives the first
    failed condition; the bool contract is unchanged.
    """
    def reject(reason):
        if reasons is not None:
            reasons.append(reason)
        return False

    if not token or _UNSAFE_PATH_CHARS.search(token):
        return reject('target is not a literal path')
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return reject('target contains path traversal or has no path')
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
        home = (root / ".claude" / "worktrees").resolve(strict=True)
    except OSError:
        return reject('target or worktree home cannot be resolved')
    if not _same_path(target.parent, home):
        return reject('target is not a linked worktree directly under .claude/worktrees')
    if not (target / ".git").exists() and target.is_dir():
        # A leftover whose `.git` file is gone too (an interrupted removal): judged as an orphan.
        if not allow_ignored:
            return reject('worktree is orphaned (no .git link) and its removal is not owner-approved')
        loss = _unheld_file(root / ".git", target, root, target / ".git-absent" / "index")
        return True if loss is None else reject('orphaned worktree holds content git does not have: ' + loss
                                                     + ' (save it elsewhere or delete that file, then retry)')
    if not (target / ".git").is_file():
        return reject('target is not a linked worktree directly under .claude/worktrees')

    admin = _orphan_admin(target)
    if admin is not None:
        if not allow_ignored:
            return reject('worktree is orphaned (git pruned its admin directory) and its removal is not owner-approved')
        loss = _unheld_file(root / ".git", target, root, admin / "index")
        return True if loss is None else reject('orphaned worktree holds content git does not have: ' + loss
                                                     + ' (save it elsewhere or delete that file, then retry)')
    listing = _git(["worktree", "list", "--porcelain"], root)
    if listing is None:
        return reject('cannot read worktree registration')
    if not _registered_unlocked(listing, target, reasons):
        return False
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                   "--ignore-submodules=none"], target)
    if status is None:
        return reject('cannot read worktree status')
    loss = _first_loss(status, target, allow_ignored)
    if loss:
        return reject('worktree has local files or changes: ' + loss)
    if not _pushed(target, reasons):
        return False

    submodules = _git(["submodule", "status", "--recursive"], target)
    if submodules is None:
        return reject('cannot read submodule status')
    for line in submodules.splitlines():
        fields = line[1:].split()
        if len(fields) < 2:
            return reject('cannot parse submodule status')
        sub = target / fields[1]
        if not (sub / ".git").exists():
            continue
        sub_status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                           "--ignore-submodules=none"], sub)
        if sub_status is None:
            return reject('submodule ' + fields[1] + ': cannot read status')
        if not _pushed(sub, reasons, 'submodule ' + fields[1] + ': '):
            return False
        loss = _first_loss(sub_status, sub, allow_ignored)
        if loss:
            return reject('submodule ' + fields[1] + ' has local files or changes: ' + loss)
    return True
