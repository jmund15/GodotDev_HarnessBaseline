#!/usr/bin/env python3
"""Rewrite a test, build or gate run before pre_bash_dispatch's guards judge it.

The rewrite reaches the call as pre_bash_dispatch's updatedInput, with an additionalContext line naming it:
- A linked worktree's run_test_suite, verify, regression_gate or run_integration_batched invocation runs
  main's copy of the script with `-Repo <worktree>`: main's code, the target's data.
- Where a delegate's run goes. A delegate is what `_prompt_provenance.in_delegate` says: a subagent (payload agent_id),
  a launcher child (CLAUDE_CODE_SIDECAR) or a `claude -p` child (CLAUDE_CODE_ENTRYPOINT=sdk-cli). A run is one of
  those scripts, engine_run or `dotnet build|test`; one with a quick read-only flag, such as -SelfTest, is left alone.
  - A suite or build run in the foreground stays there with the caller's wait clamp
    (`foreground_wait_guard.wait_clamp_ms`) as its timeout and, in a Bash call, the guard's EXIT line: waiting is the
    delegate's next step, so its verdict returns in that call instead of a launch turn plus wait turns. A run still
    going at the clamp is moved to the background by the client, and run_wait.py waits on its log or output file.
  - A LONG_RUNS run (scope, batched, gate) and any run the caller backgrounded run in the background. The client of a
    `claude -p` child stops a command it moved to the background 10 minutes after the move, and a LONG_RUNS run can
    outlast that: a scope run's hold reached 977 s over one drive, past the 870 s a moved command lives.
  - Any caller's regression_gate run without -Detach runs in the background.
- A run it routes to the background carries the Bash tool's ceiling as its `timeout`, whatever the caller sent. A
  `claude -p` child's client kills a background call at that `timeout`; the runner bounds its own life
  (`Get-SuiteRunBounds` in scripts/GodotProcess.ps1: lane wait plus attempts, at least 50 minutes), so the call's
  limit is only a backstop above it and the bound has one owner. An interactive session's client enforces no such
  limit; the raise costs it nothing.
Mentions are not actions: quoted text, comments and heredoc bodies that name a script are left alone.
"""
import os
import re
from pathlib import Path

import _git_commit
import foreground_wait_guard
from _hook_state import fire_once_since_compaction
from _prompt_provenance import in_delegate

MAIN = Path(__file__).resolve().parents[2]
BACKGROUND_TIMEOUT_MS = 7200000  # the Bash tool's ceiling for a background call (the number watch_records.ARM_CALL tells callers)
ENTRYPOINTS = frozenset(('run_test_suite', 'verify', 'regression_gate', 'run_integration_batched'))
RUNNERS = ENTRYPOINTS | {'engine_run'}
LONG_RUNS = frozenset(('verify', 'run_integration_batched', 'regression_gate'))
_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s;|&]+")
_SEPARATORS = re.compile(r'&&|\|\||[;|\n]')


def _value(token):
    return token.strip("'\"")


def _quote(value, original=''):
    q = original[0] if original[:1] in ("'", '"') else ('"' if re.search(r'\s', value) else '')
    return q + value + q


def _linked_target(script):
    root = Path(script).parent.parent.parent
    dotgit = root / '.git'
    if not dotgit.is_file():
        return None
    try:
        text = dotgit.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not text.startswith('gitdir: '):
        return None
    gitdir = Path(os.path.abspath(os.path.join(root, text[8:])))
    expected = MAIN / '.git' / 'worktrees'
    if os.path.normcase(str(gitdir.parent)) != os.path.normcase(str(expected)):
        return None
    return root.as_posix()


def _spans(command):
    # Quoted separators and heredoc data cannot turn a mention into an invocation.
    masked = list(command)
    for m in re.finditer(r"'[^']*'|\"[^\"]*\"", command):
        masked[m.start():m.end()] = ' ' * (m.end() - m.start())
    for m in re.finditer(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", command, re.M | re.S):
        body = command.find('\n', m.start())
        masked[body:m.end()] = ' ' * (m.end() - body)
    text = ''.join(masked)
    start = 0
    for m in _SEPARATORS.finditer(text):
        yield start, m.start()
        start = m.end()
    yield start, len(command)


def _skip_assignments(tokens, i):
    while i < len(tokens) and re.match(r'^\w+=', tokens[i].group()):
        i += 1
    return i


def _command_index(tokens):
    """Index of a segment's command word, past `VAR=value` assignments and an env/command prefix; None if absent."""
    i = _skip_assignments(tokens, 0)
    if i < len(tokens) and _value(tokens[i].group()) in ('env', 'command'):
        i = _skip_assignments(tokens, i + 1)
    return i if i < len(tokens) else None


def _command_name(token):
    return _value(token.group()).replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')


def _option_value_index(tokens, start, names):
    for j in range(start, len(tokens) - 1):
        if tokens[j].group().lower() in names:
            return j + 1
    return None


_PLACES = (None, 'foreground', 'background')


def _place(a, b):
    """The stronger of two placements: a background run in a call takes the whole call to the background."""
    return max(a, b, key=_PLACES.index)


def _route_wrapped(payload, original, cwd, token, lo, edits, contexts):
    """Route a `pwsh -Command` body as its own PowerShell call; return its placement."""
    # -Command's body has its own action shape, not merely a named script.
    raw = token.group()
    inner = _value(raw)
    command, advice, place = _scan(dict(payload, cwd=cwd, tool_name='PowerShell', tool_input=dict(original, command=inner)))
    if command != inner:
        edits.append((lo + token.start(), lo + token.end(), _quote(command, raw)))
    contexts.extend(advice)
    return place


def _route_runner(tokens, index, cwd, delegate):
    """For a script invocation at tokens[index]: (its placement, (main's script text, worktree) or None)."""
    path = _value(tokens[index].group()).replace('\\', '/')
    if not path.lower().endswith('.ps1'):
        return None, None
    name = Path(path).stem.lower()
    if name not in RUNNERS or '/.claude/scripts/' not in '/' + path.lower():
        return None, None
    flags = {_value(t.group()).lower().split(':')[0] for t in tokens[index + 1:]}
    quick = bool(flags & {'-selftest', '-watch', '-dryrun', '-reapreport', '-bootstrapfromtrx'})
    if quick:
        place = None
    elif (name == 'regression_gate' and '-detach' not in flags) or (delegate and name in LONG_RUNS):
        place = 'background'
    else:
        place = 'foreground' if delegate else None
    if name not in ENTRYPOINTS:
        return place, None
    target = _linked_target(_git_commit.resolve_cd(cwd, path))
    if not target:
        return place, None
    replacement = _quote((MAIN / '.claude' / 'scripts' / (name + '.ps1')).as_posix(), tokens[index].group())
    if '-repo' not in flags:
        replacement += ' -Repo ' + _quote(target)
    return place, (replacement, target)


WAIT_CALL = '`python3 .claude/tools/run_wait.py <the log it writes, or the output file the client names>`'


def _background_advice(payload):
    if foreground_wait_guard.never_woken(payload):
        return '[run] Started in the background; wait on it with %s calls.' % WAIT_CALL
    return '[run] Its exit wakes you. See `CLAUDE.interactive.md` §Waits and wakes.'


def _foreground_advice(clamp_ms):
    return ('[run] Runs in the foreground for up to %d ms, so its verdict returns in this call. If it runs longer, the '
            'client moves it to the background; then wait on it with %s.' % (clamp_ms, WAIT_CALL))


def route(payload):
    """Return (updatedInput or {}, additionalContext lines) for one Bash or PowerShell PreToolUse payload."""
    if payload.get('tool_name') not in ('Bash', 'PowerShell'):
        return {}, []
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return {}, []
    original = dict(tool_input)
    command, contexts, place = _scan(payload)
    updated = dict(original, command=command)
    delegate = in_delegate(payload, os.environ)
    session = payload.get('session_id') or ''
    if place == 'background' or (place == 'foreground' and original.get('run_in_background')):
        updated.update(run_in_background=True, timeout=BACKGROUND_TIMEOUT_MS)
        if (delegate and not original.get('run_in_background')
                and fire_once_since_compaction(session, 'run_call_router:background')):
            contexts.append(_background_advice(payload))
    elif place == 'foreground':
        clamp = foreground_wait_guard.wait_clamp_ms()
        updated['timeout'] = clamp
        if payload.get('tool_name') == 'Bash':
            updated['command'] = command.rstrip() + '\n' + foreground_wait_guard.EXIT_LINE
        if fire_once_since_compaction(session, 'run_call_router:foreground'):
            contexts.append(_foreground_advice(clamp))
    return (updated if updated != original else {}), contexts


def _scan(payload):
    """(the command with each worktree run pointed at main's script, context lines, the call's placement)."""
    original = payload.get('tool_input') or {}
    command = str(original.get('command') or '')
    cwd = str(payload.get('cwd') or MAIN)
    edits, contexts = [], []
    delegate = in_delegate(payload, os.environ)
    place = None
    for lo, hi in _spans(command):
        segment = command[lo:hi]
        tokens = list(_TOKEN.finditer(segment))
        if not tokens or segment.lstrip().startswith('#'):
            continue
        i = _command_index(tokens)
        if i is None:
            continue
        head = _command_name(tokens[i])
        if head in ('cd', 'set-location') and i + 1 < len(tokens):
            cwd = _git_commit.resolve_cd(cwd, _value(tokens[i + 1].group()))
            continue
        if head == 'dotnet':
            if i + 1 < len(tokens) and _value(tokens[i + 1].group()).lower() in ('build', 'test'):
                place = _place(place, 'foreground' if delegate else None)
            continue
        index = None
        if head in ('pwsh', 'powershell'):
            index = _option_value_index(tokens, i + 1, ('-file', '-f'))
            if index is None:
                body = _option_value_index(tokens, i + 1, ('-command', '-c'))
                if body is not None:
                    place = _place(place, _route_wrapped(payload, original, cwd, tokens[body], lo, edits, contexts))
                continue
        elif segment.lstrip().startswith(('& ', '. ')):
            # & is shell plumbing excluded by _TOKEN; dot is a token.
            index = i + 1 if head == '.' else i
        if index is None:
            continue
        runner, rewrite = _route_runner(tokens, index, cwd, delegate)
        place = _place(place, runner)
        if rewrite:
            replacement, target = rewrite
            edits.append((lo + tokens[index].start(), lo + tokens[index].end(), replacement))
            if fire_once_since_compaction(payload.get('session_id') or '', 'run_call_router:redirect'):
                contexts.append('[run] Ran main\'s copy with -Repo ' + target + '; branch data stays in the target checkout.')
    for start, end, replacement in sorted(edits, reverse=True):
        command = command[:start] + replacement + command[end:]
    return command, contexts, place
