#!/usr/bin/env python3
"""Rewrite a test, build or gate run before pre_bash_dispatch's guards judge it.

The rewrite reaches the call as pre_bash_dispatch's updatedInput, with an additionalContext line naming it:
- A linked worktree's run_test_suite, verify, regression_gate or run_integration_batched invocation runs
  main's copy of the script with `-Repo <worktree>`: main's code, the target's data.
- run_in_background is set for a delegate's run (those scripts, engine_run, `dotnet build|test`) and for
  any regression_gate run without -Detach. A delegate is a subagent (payload agent_id) or a `claude -p`
  child (CLAUDE_CODE_ENTRYPOINT=sdk-cli). A run with a quick read-only flag, such as
  -SelfTest, is not backgrounded.
Mentions are not actions: quoted text, comments and heredoc bodies that name a script are left alone.
"""
import os
import re
from pathlib import Path

import _git_commit
import foreground_wait_guard
from _hook_state import fire_once_since_compaction

MAIN = Path(__file__).resolve().parents[2]
ENTRYPOINTS = frozenset(('run_test_suite', 'verify', 'regression_gate', 'run_integration_batched'))
RUNNERS = ENTRYPOINTS | {'engine_run'}
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


def _route_wrapped(payload, original, cwd, token, lo, edits, contexts):
    """Route a `pwsh -Command` body as its own PowerShell call; return whether it runs in the background."""
    # -Command's body has its own action shape, not merely a named script.
    raw = token.group()
    inner = _value(raw)
    changed, advice = route(dict(payload, cwd=cwd, tool_name='PowerShell', tool_input=dict(original, command=inner)))
    if not changed:
        return False
    if changed.get('command', inner) != inner:
        edits.append((lo + token.start(), lo + token.end(), _quote(changed['command'], raw)))
    contexts.extend(advice)
    return bool(changed.get('run_in_background'))


def _route_runner(tokens, index, cwd, delegate):
    """For a script invocation at tokens[index]: (runs in the background, (main's script text, worktree) or None)."""
    path = _value(tokens[index].group()).replace('\\', '/')
    if not path.lower().endswith('.ps1'):
        return False, None
    name = Path(path).stem.lower()
    if name not in RUNNERS or '/.claude/scripts/' not in '/' + path.lower():
        return False, None
    flags = {_value(t.group()).lower().split(':')[0] for t in tokens[index + 1:]}
    quick = bool(flags & {'-selftest', '-watch', '-dryrun', '-reapreport', '-bootstrapfromtrx'})
    background = not quick and (delegate or (name == 'regression_gate' and '-detach' not in flags))
    if name not in ENTRYPOINTS:
        return background, None
    target = _linked_target(_git_commit.resolve_cd(cwd, path))
    if not target:
        return background, None
    replacement = _quote((MAIN / '.claude' / 'scripts' / (name + '.ps1')).as_posix(), tokens[index].group())
    if '-repo' not in flags:
        replacement += ' -Repo ' + _quote(target)
    return background, (replacement, target)


def _background_advice(payload):
    if foreground_wait_guard.never_woken(payload):
        return ('[run] Started in the background. Wait per .claude/guards/any.md: '
                '`until <done-test>; do sleep 30; done` in calls of at most 270000 ms.')
    return '[run] Its exit wakes you. See CLAUDE.core.md §Shell Discipline.'


def route(payload):
    """Return (updatedInput or {}, additionalContext lines) for one Bash or PowerShell PreToolUse payload."""
    if payload.get('tool_name') not in ('Bash', 'PowerShell'):
        return {}, []
    tool_input = payload.get('tool_input')
    if not isinstance(tool_input, dict):
        return {}, []
    original = dict(tool_input)
    command = str(original.get('command') or '')
    cwd = str(payload.get('cwd') or MAIN)
    edits, contexts = [], []
    delegate = bool(payload.get('agent_id')) or os.environ.get('CLAUDE_CODE_ENTRYPOINT') == 'sdk-cli'
    run = False
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
                run |= delegate
            continue
        index = None
        if head in ('pwsh', 'powershell'):
            index = _option_value_index(tokens, i + 1, ('-file', '-f'))
            if index is None:
                body = _option_value_index(tokens, i + 1, ('-command', '-c'))
                if body is not None:
                    run |= _route_wrapped(payload, original, cwd, tokens[body], lo, edits, contexts)
                continue
        elif segment.lstrip().startswith(('& ', '. ')):
            # & is shell plumbing excluded by _TOKEN; dot is a token.
            index = i + 1 if head == '.' else i
        if index is None:
            continue
        background, rewrite = _route_runner(tokens, index, cwd, delegate)
        run |= background
        if rewrite:
            replacement, target = rewrite
            edits.append((lo + tokens[index].start(), lo + tokens[index].end(), replacement))
            contexts.append('[run] Use main\'s test tools with -Repo ' + target + '; branch data stays in the target checkout.')
    for start, end, replacement in sorted(edits, reverse=True):
        command = command[:start] + replacement + command[end:]
    updated = dict(original, command=command)
    if run and not original.get('run_in_background'):
        updated['run_in_background'] = True
        if delegate and fire_once_since_compaction(payload.get('session_id') or '', 'run_call_router:background'):
            contexts.append(_background_advice(payload))
    return (updated if updated != original else {}), contexts
