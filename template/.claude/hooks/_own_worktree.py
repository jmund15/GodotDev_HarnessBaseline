#!/usr/bin/env python3
"""Proof that this session created a live linked worktree, read from its transcript (`session_created_worktree`).

`dangerous_shell_guard` reads it for the git discards a session may make in its own worktree (`reset --hard` of a
clean one, a checkout or restore of an edit); a call inside a shell launcher body borrows none. The proof is the
worktree's full top-level path in an add whose execution and success follow from the call's literal control flow and
its result, with the current `.git` file's timestamp inside that call. Missing evidence fails closed.

Moved unchanged from `_regenerable_clone.py`, which T1's deletion rule (`_delete_rule.py`) replaces; a recursive
delete no longer reads ownership.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import _git_commit
import _git_run
from _shell_lex import lex, split_at
from _git_commit import (resolve_cd, segments, git_invocation, cd_target,
                         executable_text, literal_assignments, expand_literal)
from _transcript_calls import Call, Result, session_events

_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")


def _git(args: list[str], cwd: Path) -> str | None:
    """stdout of a read-only `git <args>` in `cwd`, or None (fail closed); one answer per floor decision."""
    env = {"GIT_CEILING_DIRECTORIES": str(cwd.parent)}
    return _git_commit.cached(("own", tuple(args), os.path.normcase(str(cwd))),
                              lambda: _git_run.soft_text(args, cwd=str(cwd), env=env, optional_locks=False,
                                                         timeout=10))


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
        words = [w.value for seg in lex(part) for w in seg.words]
        # An unterminated quote lexes as a literal character; the shell would refuse the command.
        if (not words or any(c in w for w in words for c in "'\"") or '#' in part or '\\' in part
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
    pipeline = split_at(segment, '|')
    if not pipeline or len(pipeline) > 2 or (len(pipeline) == 2 and not _TAIL_FILTER.fullmatch(pipeline[1])):
        return []  # a blank or comment-only segment lexes to no pipeline stage
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
            parsed = git_invocation(next(iter(split_at(first, '|')), '').strip().removesuffix('2>&1').strip())
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
        words = [w.value for seg in lex(match['words']) for w in seg.words]
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
    for part in (part.strip() for part in split_at(command, '&&')):
        if cd_target(part) is not None:
            continue
        pipeline = split_at(part, '|')
        if not pipeline or len(pipeline) > 2 or (len(pipeline) == 2 and not _TAIL_FILTER.fullmatch(pipeline[1])):
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
        paths = (_git(['rev-parse', '--path-format=absolute', '--show-toplevel', '--absolute-git-dir',
                       '--git-common-dir'], target) or '').splitlines()
        top, gitdir, common = paths if len(paths) == 3 and all(p.strip() for p in paths) else ('', '', '')
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
