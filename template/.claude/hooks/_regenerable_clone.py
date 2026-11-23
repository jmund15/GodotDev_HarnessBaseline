#!/usr/bin/env python3
"""Recoverability and live worktree ownership for the destructive-action floor guards.

`session_created_worktree` owns the full-path, creation-interval proof; callers cannot infer
ownership from a basename. `is_retired_worktree` checks whether retirement loses local work.

A standalone scratch clone is regenerable only when all these hold:
- the token is a literal path: no glob, variable, `~` or `..`;
- it resolves strictly inside a `.claude/scratch/` of the project or one of its linked worktrees,
  never scratch itself;
- it has a `.git` directory and is its own top level;
- it has one worktree, a remote, no changed, untracked or ignored files, no stash, and
  no branch or tag commit unreachable from a remote-tracking ref.

A saved copy the guard can verify under the project's `.claude/scratch` makes the work regenerable
instead: a bundle holding the commits, or a tar or patch holding the files (`_saved_copy_holds`).

Missing or ambiguous evidence fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path

import _git_commit
import _git_run
from _git_commit import (resolve_cd, segments, git_invocation, cd_target,
                         executable_text, literal_assignments, expand_literal)
from _transcript_calls import Call, Result, session_events

_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
ANY_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# A proof running the whole hook on a loaded machine raises the bound through the environment.
try:
    _GIT_TIMEOUT_S = int(os.environ.get("REGENERABLE_CLONE_GIT_TIMEOUT_S", "10"))
except ValueError:
    _GIT_TIMEOUT_S = 10


def _git(args: list[str], cwd: Path, stdin: str | None = None) -> str | None:
    """stdout of `git <args>` run in `cwd`, or None on any failure (fail closed).

    Read-only by construction:
    - `--no-optional-locks` stops `status` from taking the index lock to refresh it. A timeout kill
      would otherwise leave a stale `index.lock`.
    - `GIT_CEILING_DIRECTORIES` stops git from climbing into an enclosing repository when the
      target's `.git` disappears mid-check; git would otherwise lock and judge the project repo.
    Inside `_git_commit.memoized()` (one floor decision) a repeated call answers from its first run.
    """
    memo = _git_commit._memo
    key = ("_regenerable_clone", tuple(args), os.path.normcase(str(cwd)), stdin)
    if memo is not None and key in memo:
        return memo[key]
    env = {"GIT_CEILING_DIRECTORIES": str(cwd.parent)}
    out = _git_run.soft_text(args, cwd=str(cwd), env=env, optional_locks=False,
                             input=stdin.encode() if isinstance(stdin, str) else stdin,
                             timeout=_GIT_TIMEOUT_S)
    if memo is not None:
        memo[key] = out
    return out


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


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
_BACKGROUND_NOTICE = re.compile(r'Command running in background with ID: (\w+)\. Output is being written to: '
                                r'(.+?)\. You will be notified when it completes\.')
_EXIT_CODE = re.compile(r'\(exit code (\d+)\)')


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
            # A submodule init or an expansion-free simple command (an `echo ok`) between literal loop
            # iterations cannot skip or move the next add.
            parsed = _visible_git_invocation(segment, cwd)
            words = segment.split()
            if sep in (';', '\n') and parsed and parsed[0][:3] == ['submodule', 'update', '--init']:
                continue
            if (sep in (';', '\n') and words and words[0] == 'echo'
                    and not re.search(r'[$`<>|]', segment)):
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


def _background_add_ran(visible: bool, output: str, summary: str) -> bool:
    """Whether a backgrounded add succeeded: a visible add reads its output file, any other add the exit code."""
    if visible:
        try:
            with open(output, encoding='utf-8', errors='replace') as handle:
                return not _FAILURE_LINE.search(handle.read())
        except OSError:
            return False
    code = _EXIT_CODE.search(summary)
    return code is not None and code[1] == '0'


def session_created_worktree(path: str, transcript_path: str | None) -> bool:
    """Prove ownership of this live linked worktree, not just its name (`orchestration` reference `worktrees.md`).

    Its full top-level path must occur in an add whose execution and success follow from
    the call's supported literal control flow and successful result. The current .git file's
    timestamp must lie inside that call; removal/recreation cannot inherit old ownership.
    A fully literal `for` loop counts as its unrolled adds, each proven as a visible add (`_unrolled_loops`).
    A literal `lane_worktree.py create <name>` call counts as a visible add of `<main>/.claude/worktrees/<name>`.
    A backgrounded add's result is the background notice; its call ends at the completed `<task-notification>`
    for that task id, and success reads the output file (visible add) or the notice's exit code (other add).
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
    for events in session_events(transcript_path):
        pending = {}
        backgrounded = {}  # task id -> (start, visible, output file) of an add still awaiting its notification
        for event in events:
            if isinstance(event, Call):
                command, cwd, stamp = event.command, event.cwd, event.stamp
                if stamp is not None and cwd and 'worktree' in command:
                    unrolled = _unrolled_loops(command)
                    lane = _lane_created_name(command)
                    if lane is not None:
                        made = Path(common.strip()).resolve().parent / '.claude' / 'worktrees' / lane
                        if _same_path(made, target):
                            pending[event.id] = (stamp, True)
                    elif unrolled is not None:
                        if any(_same_path(p, target) for p in _first_visible_add(unrolled, cwd)):
                            pending[event.id] = (stamp, True)
                    elif any(_same_path(p, target) for p in _added_worktrees(command, cwd)):
                        pending[event.id] = (stamp, False)
                    elif any(_same_path(p, target) for p in _first_visible_add(command, cwd)):
                        pending[event.id] = (stamp, True)
            elif isinstance(event, Result):
                start, visible = pending.pop(event.id, (None, False))
                notice = _BACKGROUND_NOTICE.search(event.text)
                if notice and start is not None and not event.is_error:
                    # The notice is the result, not git's output: the call ends at its notification.
                    backgrounded[notice[1]] = (start, visible, notice[2])
                    continue
                # A visible add's success is read from the text, so a later command's exit
                # status cannot void it; a denial never ran and carries no `Exit code` line.
                ran = not event.is_error or (visible and event.text.startswith('Exit code '))
                if (start is not None and event.stamp is not None and ran and not event.interrupted
                        and not (visible and _FAILURE_LINE.search(event.text))
                        and start <= born <= event.stamp):
                    return True
            elif event.task in backgrounded and event.stamp is not None and event.status == 'completed':
                start, visible, notice_output = backgrounded[event.task]
                if (start <= born <= event.stamp and _same_path(Path(event.output), Path(notice_output))
                        and _background_add_ran(visible, event.output, event.summary)):
                    return True
    return False


def _scratch_clone_path(target: Path, project_root: Path) -> bool:
    """True when `target` lies strictly under a `.claude/scratch` of the project or of one of its linked
    worktrees: the directories this repository's own jobs publish into."""
    for parent in target.parents:
        if parent.name == "scratch" and parent.parent.name == ".claude":
            holder = parent.parent.parent
            if _same_path(holder, project_root):
                return True
            return _same_path(holder.parent, project_root / ".claude" / "worktrees")
    return False


# Saved copies a deleting agent may leave for the guard to verify. The name carries the target's
# directory name only to narrow candidates; every check below reads the artifact's content.
_SAVED_COPY_SUFFIXES = (".bundle", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
                        ".patch", ".diff")
# Files at most this many path components below `.claude/scratch`: the deepest copy observed is a job's
# `bundles/`, and a scandir walk to that depth measured 1.3 s over a 4,616-directory scratch.
_SAVED_COPY_LEVELS = 3


def _save_step(repo: Path, project_root: Path | None) -> str:
    """The deny's next step for a repository `_saved_copy_holds` judges: a tar alone never saves a commit no remote
    holds, so the step names the bundle for its commits and the tar for its files."""
    scratch = (project_root / ".claude" / "scratch").as_posix() if project_root else ".claude/scratch"
    listing = _git(["worktree", "list", "--porcelain"], repo) or ""
    more = (" Every linked worktree must lie inside the delete: bundle after its last commit (`--all` "
            "carries each worktree's HEAD), and tar the whole folder so it holds every worktree's files."
            if sum(1 for line in listing.splitlines() if line.startswith("worktree ")) > 1 else "")
    return (' (save its commits: `git -C "%s" bundle create "%s/%s.bundle" --all`; and its uncommitted files as '
            '"%s/%s.tar"; then retry.%s)' % (repo.as_posix(), scratch, repo.name, scratch, repo.name, more))


def _saved_copies(project_root: Path, stem: str, target: Path) -> list[Path]:
    """Files under the project's `.claude/scratch` whose name carries `stem` and whose suffix is a
    saved-copy format, never inside `target` (it dies with the delete). The name narrows candidates;
    `_saved_copy_holds` decides on content."""
    base = project_root / ".claude" / "scratch"
    if not base.is_dir():
        return []
    needle = stem.lower()
    found: list[Path] = []
    level: list[tuple[Path, int]] = [(base, 0)]
    while level:
        nxt: list[tuple[Path, int]] = []
        for here, depth in level:
            try:
                entries = list(os.scandir(here))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != ".git" and depth + 1 < _SAVED_COPY_LEVELS \
                                and not _same_path(path, target):
                            nxt.append((path, depth + 1))
                    elif (needle in entry.name.lower() and entry.name.lower().endswith(_SAVED_COPY_SUFFIXES)
                          and not any(_same_path(parent, target) for parent in path.parents)):
                        found.append(path)
                except OSError:
                    continue
        level = nxt
    return found


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _archive_contents(path: Path) -> dict[str, str] | None:
    """`{relative path: sha256}` for every regular file in a tar archive, with one shared top directory
    (`pubcopy/...`) removed. None when the file is not a readable tar or holds a link or unreadable entry."""
    try:
        with tarfile.open(path) as archive:
            members = [member for member in archive if member.isfile()]
            raw = [_tar_name(member.name) for member in members]
            tops = {name.split("/", 1)[0] for name in raw}
            cut = next(iter(tops)) + "/" if len(tops) == 1 and all("/" in name for name in raw) else ""
            contents: dict[str, str] = {}
            for member, name in zip(members, raw):
                handle = archive.extractfile(member)
                if handle is None or not name:
                    return None
                contents[name[len(cut):]] = hashlib.sha256(handle.read()).hexdigest()
        return contents
    except (OSError, tarfile.TarError):
        return None


def _tar_name(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def _patch_paths(path: Path, repo: Path) -> set[str] | None:
    """The paths a patch touches, or None when git cannot read it."""
    out = _git(["apply", "--numstat", "-z", str(path)], repo)
    if out is None:
        return None
    return {record.split("\t")[-1] for record in out.split("\0") if record.strip()}


def _patch_holds(path: Path, target: Path, wanted: set[str]) -> bool:
    """True when the patch covers exactly the target's changed paths and reverses cleanly from its
    working tree: applying it to the base commit reconstructs the target's content. The caller has
    already required every commit to be on a remote, so the base survives the delete."""
    return _patch_paths(path, target) == wanted and _git(["apply", "--reverse", "--check", str(path)],
                                                         target) is not None


def _bundle_covers(target: Path, bundle: Path, commits: list[str]) -> bool:
    """True when the bundle verifies against `target` and every commit is at or behind one of its heads."""
    if _git(["bundle", "verify", str(bundle)], target) is None:
        return False
    listing = _git(["bundle", "list-heads", str(bundle)], target)
    heads = [line.split()[0] for line in (listing or "").splitlines() if line.split()]
    if not heads:
        return not commits
    left = _git(["rev-list", "--stdin"], target, stdin="\n".join([*commits, "--not", *heads]) + "\n")
    return left is not None and not left.strip()  # one call: a repository with no remote lists every commit


def _saved_copy_holds(target: Path, project_root: Path, deleting: Path | None = None) -> bool:
    """True when deleting `target` loses nothing: its git state survives the delete (every ref on a remote
    or in a bundle named for it, and for a `.git` file a repository lying outside `deleting`; a repository's
    linked worktrees must all lie inside `deleting`, each HEAD held like a ref and each worktree's losses
    counted), and every file `_losses` reports is held by a saved copy under the project's `.claude/scratch` — a tar with the
    same files, or a patch covering exactly the target's changes that reverses cleanly from its tree. A
    clean tree has no file loss, so its refs decide it and no file copy is needed."""
    deleting = deleting or target
    copies = _saved_copies(project_root, target.name, target)
    bundles = [copy for copy in copies if copy.name.lower().endswith(".bundle")]
    copy_is_worktree = not (target / ".git").is_dir()
    linked: list[Path] = []
    if copy_is_worktree:
        for flag in ("--git-dir", "--git-common-dir"):
            found = (_git(["rev-parse", "--path-format=absolute", flag], target) or "").strip()
            if not found or any(_same_path(Path(found), spot)
                                or any(_same_path(parent, spot) for parent in Path(found).parents)
                                for spot in (target, deleting)):
                return False  # the repository holding this copy goes with the delete
        off_remote = (_git(["rev-list", "HEAD", "--not", "--remotes"], target) or "").strip()
        required = off_remote.split()
    else:
        listing = _git(["worktree", "list", "--porcelain"], target)
        if listing is None:
            return False
        linked = [Path(line[9:]) for line in listing.splitlines() if line.startswith("worktree ")][1:]
        if any(not any(_same_path(parent, deleting) for parent in (spot, *spot.parents)) for spot in linked):
            return False  # a linked worktree outside the delete keeps its own state
        required = (_git(["rev-list", "--branches", "--tags", "--not", "--remotes"], target) or "").split()
        for spot in linked:
            required += (_git(["rev-list", "HEAD", "--not", "--remotes"], spot) or "").split()
        stash = (_git(["rev-parse", "--verify", "-q", "refs/stash"], target) or "").strip()
        if stash:
            required.append(stash)
    if required and not any(_bundle_covers(target, bundle, required) for bundle in bundles):
        return False  # a commit no remote holds dies with the target; only a bundle saves it
    wanted: dict[str, str] = {}
    patterns = _project_patterns(project_root)
    inside = tuple(spot.as_posix().lower() + "/" for spot in linked)
    for tree in [target, *linked]:
        status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching"], tree)
        if status is None:
            return False
        for line in _losses(status, tree, patterns=patterns):
            entry = line[3:].split(" (")[0]
            if len(entry) > 1 and entry[0] == entry[-1] == '"' and "\\" not in entry:
                entry = entry[1:-1]
            path = tree / entry.rstrip("/")
            files = [path] if path.is_file() else (sorted(p for p in path.rglob("*") if p.is_file())
                                                   if path.is_dir() else [])
            if not files:
                return False
            for file in files:
                if tree is target and file.as_posix().lower().startswith(inside):
                    continue  # a linked worktree's files are judged by its own status
                digest = None if file.is_symlink() else _sha256(file)
                if digest is None:
                    return False
                wanted[Path(os.path.relpath(file, target)).as_posix()] = digest
    if not wanted:
        return not copy_is_worktree  # a clean checkout of another repository's worktree has no loss here
    for copy in copies:
        name = copy.name.lower()
        if name.endswith(".bundle"):
            continue
        if name.endswith((".patch", ".diff")):
            if _patch_holds(copy, target, set(wanted)):
                return True
        elif _archive_holds(copy, wanted):
            return True
    return False


def _archive_holds(path: Path, wanted: dict[str, str]) -> bool:
    contents = _archive_contents(path)
    return contents is not None and all(contents.get(rel) == digest for rel, digest in wanted.items())


def _bundle_holds(path: Path, head: str, repo: Path) -> bool:
    """True when the bundle verifies against `repo` (every prerequisite is there) and lists `head`."""
    if _git(["bundle", "verify", str(path)], repo) is None:
        return False
    listing = _git(["bundle", "list-heads", str(path)], repo)
    return any(line.split()[:1] == [head] for line in (listing or "").splitlines())


def _bundle_preserves(project_root: Path, target: Path, head: str) -> bool:
    """True when a saved bundle named for `target` holds `head`: its commits can be restored from it."""
    return any(_bundle_holds(copy, head, project_root)
               for copy in _saved_copies(project_root, target.name, target)
               if copy.name.lower().endswith(".bundle"))


def _clean_pushed_clone(target: Path, reasons: list[str], project_root: Path | None = None,
                        deleting: Path | None = None) -> bool:
    """True only when `target` is a standalone clone whose deletion loses nothing: a saved copy under the
    project's `.claude/scratch` holds everything it would lose (`_saved_copy_holds`), or it is its own top
    level, has one worktree and a remote, and holds no changed, untracked or ignored file (beyond a
    regenerable cache), no stash, and no branch or tag commit unreachable from a remote-tracking ref."""
    def reject(reason):
        reasons.append(reason)
        return False

    if project_root is not None and _saved_copy_holds(target, project_root, deleting):
        return True
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
        if _git(["rev-list", "--all", "-n", "1"], target) != "":  # a repository with no commit has nothing to push
            return reject('clone has no remote')
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching"], target)
    if status is None:
        return reject('cannot read clone status')
    loss = _first_loss(status, target, project_root=project_root)
    if loss:
        return reject('clone has local files or changes: ' + loss)
    if _git(["stash", "list"], target) != "":
        return reject('clone has a stash')
    if _git(["rev-list", "--branches", "--tags", "--not", "--remotes"], target) != "":
        return reject('clone has commits on no remote branch')
    return True


def _scratch_clone_target(token: str, cwd: str, project_root: str) -> Path | None:
    """`token` resolved to a clone directory under a project or worktree scratch, else None."""
    if not token or _UNSAFE_PATH_CHARS.search(token):
        return None
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
    except OSError:
        return None
    return target if _scratch_clone_path(target, root) else None


def is_regenerable_clone(token: str, cwd: str, project_root: str, reasons: list[str] | None = None) -> bool:
    """True only when `token` names a clean, fully pushed standalone clone under the project's
    `.claude/scratch` or a linked worktree's `.claude/scratch` (a publish job's copy).

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
    found = _scratch_clone_target(token, cwd, project_root)
    if found is None:
        return reject('target is not inside a .claude/scratch of the project or its worktrees')
    return _clean_pushed_clone(found, reasons if reasons is not None else [], Path(project_root), found)


_GENERATED_UNTRACKED_SUFFIXES = (".import",)
_GODOT_IMPORT_TEMP = re.compile(r"\.import(?:\d+|~RF[0-9a-f]+)\.tmp$", re.I)
_REGENERABLE_IGNORED_SEGMENTS = frozenset({".godot", "__pycache__", ".pytest_cache", ".search-index", ".cache",
                                           "TestResults"})
_REGENERABLE_IGNORED_PAIRS = frozenset({(".claude", "cache"), (".claude", "logs")})


def regenerable_segments(parts) -> bool:
    """True when a path's components hold a regenerable-cache segment or adjacent pair, compared without case.
    The one list: the clone judge and the delete guard both read it."""
    lowered = [p.lower() for p in parts]
    return (any(seg.lower() in lowered for seg in _REGENERABLE_IGNORED_SEGMENTS)
            or any(pair in _REGENERABLE_IGNORED_PAIRS for pair in zip(lowered, lowered[1:])))


def _project_patterns(project_root: Path) -> tuple[str, ...]:
    """The project's `adaptation.json` `regenerable_ignored` patterns for `project_root`. A non-list, an
    unreadable file and an entry that is empty, absolute, climbs with `..` or opens with a wildcard
    contribute nothing, so a broken list only narrows what may go."""
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    sys.path.insert(0, tools)
    try:
        import adaptation
        listed = adaptation.load(project_root / ".claude").get("regenerable_ignored")
    except Exception:
        return ()
    finally:
        sys.path.remove(tools)
    return tuple(p for p in (listed if isinstance(listed, list) else [])
                 if isinstance(p, str) and p and not p.startswith(("/", "*", "\\")) and ".." not in p.split("/")
                 and ":" not in p)


def _listed(entry: str, patterns: tuple[str, ...]) -> bool:
    parts = entry.split("/")
    return any(fnmatchcase("/".join(parts[:n]), pattern) for n in range(1, len(parts) + 1) for pattern in patterns)


def _only_listed_files(path: Path, entry: str, patterns: tuple[str, ...]) -> bool:
    """True when every file under the ignored directory `path` (`entry` from the worktree root) is listed. A link
    inside it is a loss: it is not followed."""
    if not patterns or path.is_symlink() or not path.is_dir():
        return False
    try:
        with os.scandir(path) as children:
            for child in children:
                name = entry + "/" + child.name
                if _listed(name, patterns):
                    continue
                if child.is_symlink() or not child.is_dir(follow_symlinks=False) \
                        or not _only_listed_files(Path(child.path), name, patterns):
                    return False
    except OSError:
        return False
    return True


# `.claude/tmp` holds the owner's loose files (transcripts, drafts, probes) beside the folders a proof plants
# there. Only a folder whose name a proof builds from `PROOF_PLANT_PREFIX`, a kind and `tempfile.mkdtemp`'s
# fixed eight-character suffix is exhaust: whatever sits inside one is the proof's own fixture.
PROOF_PLANT_PREFIX = "pe_absent_"
_PROOF_PLANT = re.compile("^" + re.escape(PROOF_PLANT_PREFIX) + r"[a-z]+_[a-z0-9_]{8}$")


def is_proof_plant(name: str) -> bool:
    """True when `name`, a folder directly under a checkout's `.claude/tmp`, is one a proof planted."""
    return bool(_PROOF_PLANT.match(name))


# The id git gives a tree with no entry, per object format (SHA-1, SHA-256).
_EMPTY_TREES = frozenset({"4b825dc642cb6eb9a060e54bf8d69288fbee4904",
                          "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"})


def _holds_only_empty_commits(repo: Path) -> bool:
    """True when deleting `repo` loses no more than commit messages: a standalone repository with no remote, no
    changed or untracked file, and whose every commit on any ref (a stash included) has the empty tree, which is
    what a proof's `git commit --allow-empty` fixture is. A commit that carries a file is work."""
    if not (repo / ".git").is_dir() or _git(["remote"], repo) != "":
        return False
    if _git(["status", "--porcelain", "--untracked-files=all"], repo) != "":
        return False
    trees = _git(["log", "--all", "--format=%T"], repo)
    return trees is not None and all(line in _EMPTY_TREES for line in trees.split())


def is_plant_exhaust(path: Path) -> bool:
    """True when `path` is a proof's planted folder (`is_proof_plant` on its name) that is no link and holds no
    git repository at any depth except one `_holds_only_empty_commits`: a repository with a file in a commit, a
    remote or a changed file may hold work no remote has, so it takes the ordinary judgment. A folder that
    cannot be read is not exhaust."""
    if not is_proof_plant(path.name) or path.is_symlink():
        return False
    try:
        for here, dirs, files in os.walk(path, followlinks=False):
            if ".git" in files:
                return False
            if ".git" in dirs:
                if not _holds_only_empty_commits(Path(here)):
                    return False
                dirs.remove(".git")
    except OSError:
        return False
    return True


def _scratch_start(parts: list[str]) -> int | None:
    """The index just past the first `.claude/scratch` or `.claude/tmp` pair in `parts`, or None. `.claude/tmp`
    is where a proof plants a fixture under a name unique to its run, so it is judged as a job's scratch."""
    for index, pair in enumerate(zip(parts, parts[1:])):
        if pair in ((".claude", "scratch"), (".claude", "tmp")):
            return index + 2
    return None


def _names_no_commit(git_dir: Path) -> bool:
    """True for a `.git` directory git cannot open (no `HEAD`) that holds no ref: no `packed-refs` and no file
    under `refs/`, so no branch, tag or stash names a commit in it and only loose objects remain. A directory
    that cannot be read is not judged empty."""
    if (git_dir / "HEAD").exists() or (git_dir / "packed-refs").exists():
        return False
    try:
        return not any(p.is_file() or p.is_symlink() for p in (git_dir / "refs").rglob("*"))
    except OSError:
        return False


def _job_scratch(entry: str, root: Path | None = None, project_root: Path | None = None) -> str | None:
    """Why an ignored `.claude/scratch` or `.claude/tmp` entry keeps the delete denied, or None when it is a
    job's own output, or a folder a proof planted under `.claude/tmp` that holds no `.git` entry at any depth
    (`is_plant_exhaust`; without `root` its name alone, and the rest of it is not judged): no link, no folder named by a session id, and no git repository except a clean, pushed
    standalone clone (`_clean_pushed_clone`), one a saved copy under the project's `.claude/scratch`
    holds (`_saved_copy_holds`), or a `.git` directory that names no commit (`_names_no_commit`). A session id
    names that session's own output folder; a file only named by one (a guard's per-session state, a copied
    fixture) is no folder. A loose file directly in a `.claude/scratch` folder is evidence, not a job's
    output, so it keeps the delete denied; a job's output lives in a folder under the scratch root. Any other
    repository may hold commits on no remote. Either keeps the delete
    denied. Given `root`, the tree under the entry is read, since git reports an ignored directory as one line; a nested copy's own
    `template/.claude/scratch` counts at any depth."""
    parts = entry.split("/")
    start = _scratch_start(parts)
    if start is None:
        return None

    def named(name: str, folder: bool = True) -> str | None:
        if name == ".git":
            return 'holds a git repository that is not a clean pushed clone'
        if folder and ANY_SESSION_ID.search(name):
            return 'holds another session\'s output folder'
        return None

    if parts[start - 1] == "tmp" and start < len(parts) and is_proof_plant(parts[start]) \
            and (root is None or is_plant_exhaust(root.joinpath(*parts[:start + 1]))):
        return None
    leaf_is_file = root is not None and (root / entry).is_file()
    for index, name in enumerate(parts[start:], start):
        reason = named(name, folder=not (leaf_is_file and index == len(parts) - 1))
        if reason:
            return reason
        if index == start == len(parts) - 1 and parts[start - 1] == "scratch"                 and (root is None or leaf_is_file):
            return 'is a loose file in the scratch folder, not a job output folder'
    if root is None:
        return None
    top = root / entry
    if top.is_symlink():
        return 'is a link, so its contents cannot be judged'
    if not top.is_dir():
        return None
    try:
        for here, dirs, files in os.walk(top, followlinks=False):
            rel = os.path.relpath(here, root).replace("\\", "/")
            if rel.endswith(".claude/tmp"):  # a proof's planted folder is its own fixture, never walked
                dirs[:] = [d for d in dirs if not is_plant_exhaust(Path(here) / d)]
            if ".git" in files:
                if project_root is not None and _saved_copy_holds(Path(here), project_root, root):
                    dirs[:] = []
                    continue
                return rel + ' holds a linked worktree, not a clean pushed clone' + _save_step(Path(here), project_root)
            if ".git" in dirs and _names_no_commit(Path(here) / ".git"):
                dirs.remove(".git")
            if ".git" in dirs:
                why: list[str] = []
                if _clean_pushed_clone(Path(here), why, project_root, root):
                    dirs[:] = []  # a clean clone's own ignored files were judged; do not descend
                    continue
                return rel + ' holds a git repository that loses work: ' + (why[0] if why else 'unreadable') \
                    + _save_step(Path(here), project_root)
            direct_scratch = rel.split("/")[-2:] == [".claude", "scratch"]
            for name, folder in [(d, True) for d in dirs] + [(f, False) for f in files]:
                reason = named(name, folder)
                if reason:
                    return rel + '/' + name + ' ' + reason
                if direct_scratch and not folder:
                    return rel + '/' + name + ' is a loose file in the scratch folder, not a job output folder'
            for name in dirs:
                if (Path(here) / name).is_symlink():
                    return rel + '/' + name + ' is a link, so its contents cannot be judged'
    except OSError:
        return 'cannot read the job scratch tree'
    return None


def _job_scratch_regenerable(entry: str, root: Path | None = None, project_root: Path | None = None) -> bool:
    """True only for a `.claude/scratch` or `.claude/tmp` entry whose tree loses nothing (`_job_scratch`)."""
    return _scratch_start(entry.split("/")) is not None and _job_scratch(entry, root, project_root) is None


def _regenerable_ignored(entry: str, patterns: tuple[str, ...] = (), root: Path | None = None,
                         project_root: Path | None = None) -> bool:
    """A shared cache, a Godot import temp file, a path the project lists, a job's scratch output
    (`_job_scratch`), or (given `root`) an ignored directory whose every file is listed."""
    parts = entry.split("/")
    return (regenerable_segments(parts)
            or bool(_GODOT_IMPORT_TEMP.search(entry))
            or _listed(entry, patterns)
            or _job_scratch_regenerable(entry, root, project_root)
            or (root is not None and _only_listed_files(root / entry, entry, patterns)))


def _holds_no_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        return not any(p.is_file() or p.is_symlink() for p in path.rglob("*"))
    except OSError:
        return False


def empty_leftover(path: str) -> bool:
    """True when `path` is a directory holding no file and no `.git` at any depth: an interrupted removal's
    empty shell, which loses nothing."""
    root = Path(path)
    return _holds_no_file(root) and not any(p.name == ".git" for p in root.rglob("*"))


_LOSS_LIST_CAP = 8


def _format_losses(losses: list[str]) -> str:
    """The losses as one list for a deny: the first `_LOSS_LIST_CAP`, then how many more."""
    more = len(losses) - _LOSS_LIST_CAP
    return "; ".join(losses[:_LOSS_LIST_CAP]) + (" (and %d more)" % more if more > 0 else "")


def _losses(status: str, root: Path, allow_ignored: bool = False, patterns: tuple[str, ...] = (),
            project_root: Path | None = None) -> list[str]:
    """Every `git status --porcelain --ignored=matching` line whose deletion loses work.
    Untracked Godot `.import` files, a tracked file missing from the checkout (` D`: its index entry equals HEAD,
    which `_pushed` holds), ignored regenerable caches, a job's scratch output, the project's
    `regenerable_ignored` paths and ignored directories holding no file lose nothing; `allow_ignored` (this
    session created the worktree) lets every ignored entry go. Given `project_root`, a nested repository a
    saved copy under its `.claude/scratch` holds is no loss either."""
    out = []
    for line in status.splitlines():
        entry = line[3:]
        if len(entry) > 1 and entry[0] == entry[-1] == '"' and "\\" not in entry:
            entry = entry[1:-1]  # git quotes a path holding a space; an escaped path stays quoted and blocks
        entry = entry.rstrip("/")
        if line.startswith("?? ") and entry.endswith(_GENERATED_UNTRACKED_SUFFIXES):
            continue
        if line.startswith(" D "):
            continue
        if line.startswith("!! ") and (allow_ignored or _regenerable_ignored(entry, patterns, root, project_root)
                                       or _holds_no_file(root / entry)):
            continue
        problem = _job_scratch(entry, root, project_root) if line.startswith("!! ") else None
        out.append(line + (' (' + problem + ')' if problem else ''))
    return out


def _first_loss(status: str, root: Path, allow_ignored: bool = False, patterns: tuple[str, ...] = (),
                project_root: Path | None = None) -> str | None:
    """The first line `_losses` reports, or None."""
    return next(iter(_losses(status, root, allow_ignored, patterns, project_root)), None)


def _unstored_losses(status: str, root: Path) -> list[str]:
    """`_losses` for a worktree this session created: a dirty path loses nothing when its working file is gone
    (an index blob is always in the store) or its content is already a blob in the object store, and a
    submodule's gitlink is judged by that submodule's own checks. A missing working file that git does not
    report deleted (a path git quoted, say) is a loss: the check fails closed."""
    out = []
    for line in status.splitlines():
        if _first_loss(line, root, allow_ignored=True) is None:
            continue
        entry = line[3:].split(" -> ")[-1].strip().strip('"')
        path = root / entry
        if (path / ".git").exists():
            continue
        if not (path.is_file() or path.is_symlink()):
            if "D" not in line[:2]:
                out.append(line)
            continue
        unstored = worktree_unstored(str(root), [entry])
        if unstored is None or unstored:
            out.append(line)
    return out


def _first_unstored_loss(status: str, root: Path) -> str | None:
    return next(iter(_unstored_losses(status, root)), None)


# Each candidate costs one git call inside a hook's 75 s budget; every real landing observed sat within
# 14 commits of the tip on its paths, and an older holder fails closed.
_HISTORY_SCAN = 20
_REAPPLY_SCAN = 8


def _patch_lines(repo: Path, rev: str) -> tuple[Counter, Counter] | None:
    """The added and removed lines of `rev`'s own change, or None for a merge or an unreadable diff."""
    parents = _git(["rev-list", "--parents", "-n", "1", rev], repo)
    if parents is None or len(parents.split()) != 2:
        return None
    text = _git(["diff", "--no-renames", "--no-color", "--no-ext-diff", "-U0", rev + "^", rev], repo)
    if text is None:
        return None
    added, removed, in_hunk = Counter(), Counter(), False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk:
            in_hunk = line.startswith("@@")
        elif line.startswith("+"):
            added[line[1:]] += 1
        elif line.startswith("-"):
            removed[line[1:]] += 1
    return added, removed


def _diff_paths(repo: Path, rev: str) -> set[str] | None:
    """The paths `rev` changed, or None when git cannot say."""
    out = _git(["diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", rev], repo)
    return (set(out.split("\n")) - {""}) if out is not None else None


def _merge_adds_nothing(repo: Path, ref: str) -> bool:
    """True when a clean three-way merge of HEAD into `ref` yields `ref`'s own tree: the default branch
    already carries the branch's net change."""
    merged = _git(["merge-tree", "--write-tree", ref, "HEAD"], repo)
    want = _git(["rev-parse", ref + "^{tree}"], repo)
    return bool(merged and want and merged.split("\n", 1)[0].strip() == want.strip())


def _content_held_by_history(repo: Path, ref: str, paths: list[str]) -> bool:
    """True when a commit reachable from `ref` carries HEAD's content for every path the branch changed:
    a squash or rebase whose state the default branch later built on."""
    listing = _git(["rev-list", "--max-count=%d" % _HISTORY_SCAN, ref, "--", *paths], repo)
    if listing is None:
        return False
    for sha in listing.split():
        if _git(["diff", "--quiet", sha, "HEAD", "--", *paths], repo) is not None:
            return True
    return False


def _reapplied(repo: Path, ref: str, commit: str) -> bool:
    """One commit's re-application: a commit on `ref` changed the same paths with the same added and
    removed lines, on a different base. Candidates come from the commit's new-file blobs
    (`--find-object`), else from the changed path `ref` touched least; an unanchored commit fails
    closed. A merge commit never qualifies."""
    mine = _patch_lines(repo, commit)
    paths = _diff_paths(repo, commit)
    if mine is None or not paths:
        return False
    candidates: set[str] = set()
    added = _git(["diff-tree", "--no-commit-id", "--diff-filter=A", "--name-only", "--no-renames", "-r", commit], repo)
    for path in (added or "").splitlines():
        blob = _git(["rev-parse", commit + ":" + path], repo)
        if blob and blob.strip():
            found = _git(["log", "--format=%H", "--no-color", "--find-object=" + blob.strip(), ref, "--", path], repo)
            candidates.update((found or "").split())
    if not candidates:
        counts = []
        for path in paths:
            count = _git(["rev-list", "--count", ref, "--", path], repo)
            if count is None or not count.strip().isdigit():
                return False
            counts.append((int(count), path))
        listing = _git(["rev-list", "--max-count=%d" % _REAPPLY_SCAN, ref, "--", min(counts)[1]], repo)
        candidates.update((listing or "").split())
    for other in candidates:
        if other != commit and _diff_paths(repo, other) == paths and _patch_lines(repo, other) == mine:
            return True
    return False


def _commits_reapplied(repo: Path, ref: str) -> bool:
    """True when every commit HEAD holds but `ref` does not is a re-application of a commit on `ref`."""
    unique = _git(["rev-list", "HEAD", "--not", ref], repo)
    if unique is None:
        return False
    return all(_reapplied(repo, ref, commit) for commit in unique.split())


def _landed_on(repo: Path, ref: str) -> bool:
    """Every HEAD commit's work is on `ref`: HEAD is reachable from it, each commit's patch is on it and none
    is a merge (a cherry-pick or a rebased lane), or no file the branch changed since the merge base differs
    from `ref` (a squash), or `ref` holds the work in another form: a merge of HEAD into it changes nothing
    (`_merge_adds_nothing`), a commit on it carries HEAD's content for every changed path
    (`_content_held_by_history`), or each of HEAD's commits is a re-application of one on `ref`
    (`_commits_reapplied`)."""
    if _git(["rev-list", "HEAD", "--not", ref], repo) == "":
        return True
    if (_git(["rev-list", "--merges", "HEAD", "--not", ref], repo) == ""
            and _git(["rev-list", "--cherry-pick", "--right-only", "--no-merges", ref + "...HEAD"], repo) == ""):
        return True
    base = (_git(["merge-base", ref, "HEAD"], repo) or "").strip()
    changed = _git(["diff", "--name-only", "--no-renames", "-z", base, "HEAD"], repo) if base else None
    differs = _git(["diff", "--name-only", "--no-renames", "-z", ref, "HEAD"], repo)
    if (changed is not None and differs is not None
            and not (set(changed.split("\0")) & set(differs.split("\0"))) - {""}):
        return True
    paths = [path for path in (changed or "").split("\0") if path]
    if not paths:
        return False
    return (_merge_adds_nothing(repo, ref)
            or _content_held_by_history(repo, ref, paths)
            or _commits_reapplied(repo, ref))


def _pushed(repo: Path, reasons: list[str] | None = None, prefix: str = '', also: Path | None = None,
            local_default: bool = False, preserved=None) -> bool:
    """Every HEAD commit is on a remote, or its work is on the remote's default branch (`_landed_on`). `also`
    is the main checkout's copy of the same submodule: a worktree's submodule clone keeps its own remote refs,
    which go stale, so a HEAD that copy sees on a remote branch is pushed. `local_default` (the worktree of
    the project, whose branches live in the shared repository and outlast it) also accepts work on the local
    default branch; a submodule's clone is deleted with its worktree, so it never takes this. `preserved(head)`
    accepts a HEAD that a saved copy holds instead."""
    def reject(reason):
        if reasons is not None:
            reasons.append(prefix + reason)
        return False

    out = _git(["rev-list", "HEAD", "--not", "--remotes"], repo)
    if out is None:
        return reject('cannot verify HEAD reachability')
    if not out.strip():
        return True
    head = (_git(['rev-parse', 'HEAD'], repo) or '').strip()
    if also is not None and head and (_git(["branch", "-r", "--contains", head], also) or '').strip():
        return True
    if preserved is not None and head and preserved(head):
        return True
    next_step = (' (save a bundle of its refs under .claude/scratch named for the worktree, then retry)'
                 if preserved is not None else ' (push it to a remote, then retry)')
    unpushed = 'HEAD ' + (_git(['rev-parse', '--short', 'HEAD'], repo) or '(unreadable)').strip() \
        + ' is on no remote branch' + next_step
    default = (_git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], repo) or "").strip()
    if not default:
        return reject(unpushed)
    refs = [default]
    local = default.split("/", 1)[-1]
    if local_default and "/" in default and _git(["rev-parse", "--verify", "-q", "refs/heads/" + local], repo):
        refs.insert(0, "refs/heads/" + local)  # a job's work lands here first; skip the remote's full scan
    return True if any(_landed_on(repo, ref) for ref in refs) else reject(unpushed)


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


def _open_prs(root: Path) -> list[tuple[int, str]] | None:
    """`(number, head branch)` per open PR of the project's GitHub remote, [] when origin is not on
    GitHub, None when `gh` cannot answer (fail closed: the caller blocks)."""
    origin = _git(["remote", "get-url", "origin"], root)
    if origin is None or "github.com" not in origin:
        return []
    try:
        proc = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "200", "--json",
                               "number,headRefName"], cwd=str(root), capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT_S)
        return [(int(row["number"]), str(row["headRefName"])) for row in json.loads(proc.stdout)] \
            if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def _open_pr_reason(target: Path, root: Path) -> str | None:
    """Why `target` holds an open PR's branch (its head branch, or `sync/pr<N>` of open PR N): a PR under
    test or review needs its checkout until the PR closes or merges."""
    branch = (_git(["symbolic-ref", "-q", "--short", "HEAD"], target) or "").strip()
    if not branch:
        return None
    prs = _open_prs(root)
    if prs is None:
        return 'cannot read open PRs (gh failed), so cannot tell whether branch ' + branch + \
            ' heads one; fix gh and retry'
    match = re.fullmatch(r"sync/pr(\d+)", branch)
    for number, head in prs:
        if branch == head or (match and int(match.group(1)) == number):
            return 'branch ' + branch + ' belongs to open PR #' + str(number) + \
                ', which still needs this checkout; remove it after the PR closes or merges'
    return None


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


def unstored_files(files: list[str], run) -> list[str] | None:
    """The files in `files` whose working-tree content the object store lacks, in order; None when git
    cannot say. `run(args, stdin)` returns git's stdout, or None on failure."""
    ids = run(["hash-object", "--stdin-paths"], "\n".join(files) + "\n")
    if ids is None or len(ids.split()) != len(files):
        return None
    held = run(["cat-file", "--batch-check"], ids)
    if held is None:
        return None
    return [path for path, line in zip(files, held.splitlines()) if line.endswith(" missing")]


def worktree_unstored(cwd: str, files: list[str]) -> list[str] | None:
    """`unstored_files` for the repository checked out at `cwd`."""
    return unstored_files(files, lambda args, stdin: _git(args, Path(cwd), stdin))


def _unheld_file(*args, **kwargs) -> str | None:
    """`_unheld_files` as one printable list, or None when nothing is lost."""
    found = _unheld_files(*args, **kwargs)
    return _format_losses(found) if found else None


def _unheld_files(git_dir: Path, work_tree: Path, root: Path, absent_index: Path,
                  ignored_ok: bool = True, patterns: tuple[str, ...] = ()) -> list[str]:
    """Every non-ignored file under `work_tree` whose content `git_dir`'s object store lacks (an empty
    list when none). `absent_index` names no file, so git reads an empty index and writes nothing. A nested
    checkout (a submodule) is judged against `root`'s `.git/modules/<path>` store. Unless `ignored_ok`
    (this session created the worktree), an ignored file that is no regenerable cache is a loss too
    (`_regenerable_ignored`, Godot `.import`; an ignored directory holding no file lists nothing)."""
    env = {"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(work_tree),
           "GIT_INDEX_FILE": str(absent_index), "GIT_CEILING_DIRECTORIES": str(work_tree.parent)}

    def run(args, stdin=None):
        return _git_run.soft_text(args, cwd=str(work_tree), env=env, optional_locks=False,
                                  input=stdin.encode() if isinstance(stdin, str) else stdin,
                                  timeout=_ORPHAN_HASH_TIMEOUT_S)

    found: list[str] = []
    subs = []
    if (work_tree / ".gitmodules").is_file():
        listed = run(["config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"])
        subs = [line.split(" ", 1)[1].strip("/") for line in (listed or "").splitlines() if " " in line]
    for sub in subs:
        if not (work_tree / sub).is_dir() or not any((work_tree / sub).iterdir()):
            continue
        store = root / ".git" / "modules" / sub
        if not store.is_dir():
            found.append(sub + "/ (a populated submodule with no store at .git/modules/" + sub + ")")
            continue
        found.extend(sub + "/" + loss for loss in _unheld_files(store, work_tree / sub, root, absent_index, ignored_ok, patterns))
    if not ignored_ok:
        ignored = run(["ls-files", "--others", "--ignored", "--exclude-standard", "-z"])
        if ignored is None:
            return found + ["(cannot list ignored files)"]
        for entry in filter(None, ignored.split("\0")):
            if any(entry == sub or entry.startswith(sub + "/") for sub in subs):
                continue
            if not (entry.endswith(_GENERATED_UNTRACKED_SUFFIXES) or _regenerable_ignored(entry, patterns)):
                found.append(entry + " (ignored, and not a regenerable cache)")
    listed = run(["ls-files", "--others", "--exclude-standard", "-z"])
    if listed is None:
        return found + ["(cannot list files)"]
    files = [entry for entry in filter(None, listed.split("\0"))
             if not entry.endswith(_GENERATED_UNTRACKED_SUFFIXES)
             and not any(entry == sub or entry.startswith(sub + "/") for sub in subs)]
    found.extend(entry + " (a nested repository)" for entry in files if entry.endswith("/"))
    files = [entry for entry in files if not entry.endswith("/")]
    if not files:
        return found
    missing = unstored_files(files, run)
    if missing is None:
        return found + ["(cannot read the object store)"]
    return found + missing


_NOT_A_WORKTREE = ('target is not a linked worktree directly under .claude/worktrees, nor an orphaned worktree '
                   'of this repository')


def _common_git_dir(root: Path) -> Path | None:
    """The repository's shared git directory: `root/.git`, unless `root` is itself a linked worktree."""
    found = (_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], root) or "").strip()
    return Path(found) if found else None


def _orphan_of_repo(target: Path, root: Path) -> bool:
    """True when `target`'s `.git` file names `<this repository's git dir>/worktrees/<name>` and git pruned
    that admin directory: an orphaned worktree of this repository, wherever it sits. A directory that holds
    the project is never one."""
    admin = _orphan_admin(target)
    common = _common_git_dir(root)
    if admin is None or common is None or any(_same_path(parent, target) for parent in (root, *root.parents)):
        return False
    return _same_path(admin.parent.resolve(), (common / "worktrees").resolve())


def _worktree_target(token: str, cwd: str, project_root: str, reject) -> tuple[Path, Path] | None:
    """`(target, project root)` when `token` is a literal path to a directory directly under
    `<project>/.claude/worktrees/`, or to an orphaned worktree of the project's repository
    (`_orphan_of_repo`), else None after `reject(reason)`."""
    def refuse(reason):
        reject(reason)
        return None

    if not token or _UNSAFE_PATH_CHARS.search(token):
        return refuse('target is not a literal path')
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return refuse('target contains path traversal or has no path')
    try:
        target = Path(resolve_cd(cwd, token)).resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
        home = (root / ".claude" / "worktrees").resolve()
    except OSError:
        return refuse('target or worktree home cannot be resolved')
    if not _same_path(target.parent, home) and not _orphan_of_repo(target, root):
        return refuse(_NOT_A_WORKTREE)
    return target, root


def _orphan_verdict(target: Path, root: Path, allow_ignored: bool, reject) -> bool | None:
    """None when `target` has a live `.git` link (judge it by status). For an orphan (git pruned the
    admin directory, or no `.git` link remains) True when nothing is lost, else `reject(reason)`: content
    git lacks is always a loss, and so is an ignored file that is no regenerable cache unless
    `allow_ignored`."""
    if not (target / ".git").exists() and target.is_dir():
        admin = target / ".git-absent"
    elif (target / ".git").is_file():
        admin = _orphan_admin(target)
        if admin is None:
            return None
    else:
        return reject(_NOT_A_WORKTREE)
    loss = _unheld_file(_common_git_dir(root) or root / ".git", target, root, admin / "index", allow_ignored,
                        _project_patterns(root))
    if loss is None:
        return True
    return reject('orphaned worktree holds content git does not have: ' + loss
                  + ' (save those files elsewhere or delete them, then retry)')


def lossless_orphan_worktree(token: str, cwd: str, project_root: str, reasons: list[str] | None = None) -> bool:
    """True only for an orphaned worktree directory (git pruned its admin directory, or no `.git` link
    remains) directly under `<project>/.claude/worktrees/`, or a directory anywhere whose `.git` file names
    a pruned admin directory of this repository (`_orphan_of_repo`), that holds nothing git lacks: every
    non-ignored file, submodules included, is a blob in the object store, and every ignored file is a
    regenerable cache. An orphan needs no creation proof. A worktree with a live `.git` link never qualifies. `reasons` receives the loss for an orphan and
    the 'not a linked worktree' reason for a target that is neither an orphan nor a live worktree;
    for a live worktree, or a target `_worktree_target` rejects (not a literal path, path traversal,
    unresolvable, or neither under `.claude/worktrees/` nor an orphan of this repository), it receives
    nothing."""
    def reject(reason):
        if reasons is not None:
            reasons.append(reason)
        return False

    located = _worktree_target(token, cwd, project_root, lambda _reason: False)
    return located is not None and _orphan_verdict(*located, False, reject) is True


def is_retired_worktree(token: str, cwd: str, project_root: str, allow_ignored: bool = False,
                        reasons: list[str] | None = None) -> bool:
    """True only when `token` names a linked worktree of `project_root`, directly under
    `<project>/.claude/worktrees/`, whose deletion loses nothing.

    `git worktree remove` refuses every worktree carrying a submodule, so a retired one can only go
    by a recursive delete followed by `git worktree prune`. The worktree must be registered and
    unlocked; its status may hold only untracked Godot `.import` files and ignored regenerable caches
    (`_REGENERABLE_IGNORED_SEGMENTS`, `_REGENERABLE_IGNORED_PAIRS`) or ignored directories holding no
    file, so ignored scratch evidence or local config blocks the delete unless `allow_ignored` (this
    session created the worktree, so its ignored files may go with it, and so may a tracked or
    untracked change whose content is already a blob in the object store or whose file is gone,
    `_first_unstored_loss`); its HEAD and every populated
    submodule's HEAD must be on a remote (`_pushed`: by hash, or by patch for a rebased lane), and each
    submodule must be clean. An orphan (git pruned its admin directory, so status cannot be read)
    passes when every non-ignored file's content, submodules included, is already a blob in the
    repository's object store (commits and branches live in the shared store, so unsaved file content
    is the only loss) and every ignored file is a regenerable cache; `allow_ignored` lets any ignored
    file go (`lossless_orphan_worktree` is that check alone). A leftover directory with no `.git` file
    (an interrupted removal) is judged the same way, and so is a submodule whose `.git` names a pruned
    admin directory, against the main checkout's `.git/modules/<path>` store. A submodule HEAD counts
    as pushed when the main checkout's copy of that submodule sees it on a remote branch. A standalone
    clone under a project or worktree `.claude/scratch` is judged by `_clean_pushed_clone`. A saved copy
    under the project's `.claude/scratch` makes an unpushed HEAD or a nested repository's files
    regenerable instead (`_saved_copy_holds`). If supplied,
    `reasons` receives every failed condition, so one deny names them all; a target or registration
    that cannot be judged stops at that reason. The bool contract is unchanged.
    """
    def reject(reason):
        if reasons is not None:
            reasons.append(reason)
        return False

    clone = _scratch_clone_target(token, cwd, project_root)
    if clone is not None:
        return _clean_pushed_clone(clone, reasons if reasons is not None else [], Path(project_root), clone)
    located = _worktree_target(token, cwd, project_root, reject)
    if located is None:
        return False
    target, root = located
    verdict = _orphan_verdict(target, root, allow_ignored, reject)
    if verdict is not None:
        return verdict
    listing = _git(["worktree", "list", "--porcelain"], root)
    if listing is None:
        return reject('cannot read worktree registration')
    if not _registered_unlocked(listing, target, reasons):
        return False
    pr_reason = _open_pr_reason(target, root)
    if pr_reason:
        return reject(pr_reason)
    listed = _git(["config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"], target) \
        if (target / ".gitmodules").is_file() else ""
    declared = [line.split(" ", 1)[1].strip().strip("/") for line in (listed or "").splitlines() if " " in line]
    broken = [name for name in declared if (target / name / ".git").is_file() and _orphan_admin(target / name)]
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                   "--ignore-submodules=" + ("all" if broken else "none")], target)
    if status is None:
        return reject('cannot read worktree status')
    failed: list[str] = []
    patterns = _project_patterns(root)
    loss = _unstored_losses(status, target) if allow_ignored else _losses(
        status, target, patterns=patterns, project_root=root)
    if loss:
        failed.append('worktree has local files or changes: ' + _format_losses(loss))
    _pushed(target, failed, local_default=True,
            preserved=lambda head: _bundle_preserves(root, target, head))

    for name in broken:
        if not allow_ignored:
            failed.append('submodule ' + name + ': its .git names a missing admin directory, so its state is '
                          'unreadable, and this session did not create the worktree')
            continue
        store = root / ".git" / "modules" / name
        lost = _unheld_file(store, target / name, root, target / name / ".git-absent" / "index") \
            if store.is_dir() else '(no store at .git/modules/' + name + ')'
        if lost:
            failed.append('submodule ' + name + ': its .git names a missing admin directory, and it holds '
                          'content git does not have: ' + lost + ' (save it elsewhere, then retry)')

    if broken:
        rows = [name for name in declared if name not in broken]
    else:
        submodules = _git(["submodule", "status", "--recursive"], target)
        if submodules is None:
            return reject('cannot read submodule status')
        rows = []
        for line in submodules.splitlines():
            fields = line[1:].split()
            if len(fields) < 2:
                return reject('cannot parse submodule status')
            rows.append(fields[1])
    for name in rows:
        sub = target / name
        if not (sub / ".git").exists():
            continue
        sub_status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                           "--ignore-submodules=none"], sub)
        if sub_status is None:
            failed.append('submodule ' + name + ': cannot read status')
            continue
        _pushed(sub, failed, 'submodule ' + name + ': ', also=root / name)
        loss = _unstored_losses(sub_status, sub) if allow_ignored else _losses(sub_status, sub, patterns=patterns)
        if loss:
            failed.append('submodule ' + name + ' has local files or changes: ' + _format_losses(loss))
    if reasons is not None:
        reasons.extend(failed)
    return not failed


def main(argv: list[str]) -> int:
    """`--retire-check [--created] <worktree>...`: the delete guard's verdict per worktree, with
    every reason it would deny; `--created` judges as for a worktree this session created. Exit 1 when any
    worktree is not retired."""
    if not argv or argv[0] != "--retire-check":
        print(main.__doc__)
        return 2
    created = "--created" in argv
    root = os.environ.get("CLAUDE_PROJECT_DIR") or _git(["rev-parse", "--show-toplevel"], Path.cwd()) or "."
    root = root.strip()
    blocked = 0
    for token in (a for a in argv[1:] if not a.startswith("--")):
        reasons: list[str] = []
        if is_retired_worktree(token, os.getcwd(), root, allow_ignored=created, reasons=reasons):
            print(f"{token}: retired")
        else:
            blocked += 1
            print(f"{token}: blocked: " + "; ".join(reasons))
    return 1 if blocked else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
