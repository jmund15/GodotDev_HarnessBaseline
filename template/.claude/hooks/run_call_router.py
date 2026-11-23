#!/usr/bin/env python3
"""Floor helper (`_floor.HELPER_NAMES`): rewrite a test, build or gate run, through `check(payload)`.

The rewrite reaches the call as the floor entry's updatedInput, with an additionalContext line naming it:
- The project lists its scripts in `adaptation.json` `pre_bash.router_entrypoints`, one row per script:
  `{"name": <stem>, "redirect": <bool, default true>, "detach_flag": <flag, optional>}`. A linked worktree's run of a
  `redirect` script runs main's copy with `-Repo <worktree>`: main's code, the target's data.
- run_in_background is set for a delegate's run of a listed script or `dotnet build|test`, and for any caller's run of
  a script whose row names a `detach_flag` (it outlives a turn) unless that flag is passed. A delegate is a subagent
  (payload agent_id) or a `claude -p` child (CLAUDE_CODE_ENTRYPOINT=sdk-cli). A run with a quick read-only flag, such
  as -SelfTest, is not backgrounded.
Mentions are not actions: quoted text, comments and heredoc bodies that name a script are left alone.
The floor's checks judge the call as sent: this changes only which copy of a script runs and whether it runs in the
background, which no floor check reads. A fault here skips the rewrite (`_floor`: a failing helper is skipped).
`foreground_wait_guard` and `_hook_state` load on first use, so `import _floor` carries no per-session state module.
"""
import os
import re
from pathlib import Path

import _delete_rule
import _git_commit
import _shell_lex

MAIN = Path(__file__).resolve().parents[2]


def _entrypoints():
    """The project's `adaptation.json` `pre_bash.router_entrypoints` as {stem: (redirect, detach flag or '')}. The
    shared code names no script (T7); a row with no name is skipped (`tools/adaptation.py` names a malformed row)."""
    listed = _delete_rule.project_data('pre_bash', 'router_entrypoints')
    rows = {}
    for row in listed if isinstance(listed, list) else ():
        name = row.get('name') if isinstance(row, dict) else None
        if isinstance(name, str) and name.strip():
            detach = row.get('detach_flag')
            rows[name.strip().lower()] = (row.get('redirect') is not False,
                                          detach.lower() if isinstance(detach, str) else '')
    return rows


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


def _command_name(word):
    return word.text.replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')


def _option_value_index(words, start, names):
    for j in range(start, len(words) - 1):
        if words[j].value.lower() in names:
            return j + 1
    return None


def _route_wrapped(payload, original, cwd, word, edits, contexts):
    """Route a `pwsh -Command` body as its own PowerShell call; return whether it runs in the background."""
    # -Command's body has its own action shape, not merely a named script.
    changed, advice = route(dict(payload, cwd=cwd, tool_name='PowerShell',
                                 tool_input=dict(original, command=word.value)))
    if not changed:
        return False
    if changed.get('command', word.value) != word.value:
        edits.append((word.start, word.end, _quote(changed['command'], word.raw)))
    contexts.extend(advice)
    return bool(changed.get('run_in_background'))


def _route_runner(words, index, cwd, delegate):
    """For a script invocation at words[index]: (runs in the background, (main's script text, worktree) or None)."""
    path = words[index].text.replace('\\', '/')
    if not path.lower().endswith('.ps1'):
        return False, None
    name = Path(path).stem.lower()
    row = _entrypoints().get(name)
    if row is None or '/.claude/scripts/' not in '/' + path.lower():
        return False, None
    redirect, detach = row
    flags = {w.value.lower().split(':')[0] for w in words[index + 1:]}
    quick = bool(flags & {'-selftest', '-watch', '-dryrun', '-reapreport', '-bootstrapfromtrx'})
    background = not quick and (delegate or bool(detach and detach not in flags))
    if not redirect:
        return background, None
    target = _linked_target(_git_commit.resolve_cd(cwd, path))
    if not target:
        return background, None
    replacement = _quote((MAIN / '.claude' / 'scripts' / (name + '.ps1')).as_posix(), words[index].raw)
    if '-repo' not in flags:
        replacement += ' -Repo ' + _quote(target)
    return background, (replacement, target)


def _background_advice(payload):
    import foreground_wait_guard
    if foreground_wait_guard.never_woken(payload):
        return ('[run] Started in the background. Wait per .claude/guards/rails.md: '
                '`until <done-test>; do sleep 30; done` in calls of at most 270000 ms.')
    return '[run] Its exit wakes you. See CLAUDE.core.md §Shell Discipline.'


def _fire_once(payload, key):
    from _hook_state import fire_once_since_compaction
    return fire_once_since_compaction(payload.get('session_id') or '', key)


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
    # A subagent or `claude -p` child is a delegate whether or not a completion wake reaches it: its foreground run
    # blocks its own turn, so it is backgrounded either way. `never_woken` only picks the advice text.
    delegate = bool(payload.get('agent_id')) or os.environ.get('CLAUDE_CODE_ENTRYPOINT') == 'sdk-cli'
    run = False
    for segment in _shell_lex.lex(command):
        words = segment.words
        i = _shell_lex.command_index([w.value for w in words])
        # PowerShell's call operator `& 'script.ps1'` leaves the script as the segment's command word.
        before = command[:words[0].start].rstrip() if words else ''
        was_called = before.endswith('&') and not before.endswith('&&')
        if i >= len(words):
            continue
        head = _command_name(words[i])
        if head in ('cd', 'set-location') and i + 1 < len(words):
            cwd = _git_commit.resolve_cd(cwd, words[i + 1].text)
            continue
        if head == 'dotnet':
            if i + 1 < len(words) and words[i + 1].value.lower() in ('build', 'test'):
                run |= delegate
            continue
        index = None
        if head in ('pwsh', 'powershell'):
            index = _option_value_index(words, i + 1, ('-file', '-f'))
            if index is None:
                body = _option_value_index(words, i + 1, ('-command', '-c'))
                if body is not None:
                    run |= _route_wrapped(payload, original, cwd, words[body], edits, contexts)
                continue
        elif was_called:
            index = i
        elif head == '.':
            index = i + 1
        if index is None or index >= len(words):
            continue
        background, rewrite = _route_runner(words, index, cwd, delegate)
        run |= background
        if rewrite:
            replacement, target = rewrite
            edits.append((words[index].start, words[index].end, replacement))
            if _fire_once(payload, 'run_call_router:redirect'):
                contexts.append("[run] Ran main's copy with -Repo " + target + "; branch data stays in the target checkout.")
    for start, end, replacement in sorted(edits, reverse=True):
        command = command[:start] + replacement + command[end:]
    updated = dict(original, command=command)
    if run and not original.get('run_in_background'):
        updated['run_in_background'] = True
        if delegate and _fire_once(payload, 'run_call_router:background'):
            contexts.append(_background_advice(payload))
    return (updated if updated != original else {}), contexts


def check(payload):
    """The floor helper's output for one payload: an updatedInput rewrite with its context lines, or None."""
    updated, contexts = route(payload)
    if not updated:
        return None
    out = {"hookEventName": "PreToolUse", "updatedInput": updated}
    if contexts:
        out["additionalContext"] = "\n".join(contexts)
    return {"hookSpecificOutput": out}
