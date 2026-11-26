#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|PowerShell), run by pre_bash_dispatch -- gives a commit made under a session triage that
triage as its `Triage:` trailer. Unwired from the dispatcher (`UNWIRED`), held with plan decision D14.

The one fact it carries: a commit whose message has no `Triage:` line, made in a session whose task record holds a
triage (`task_record.triage_for_session`). No task record names a commit sha, so the trailer is the only durable
link from a commit to the triage it was made under; `escape_log.py` and `triage.py rederive` read it. Nothing is
derived from the diff and nothing is printed: whether the triage understates the diff is `triage.py rederive`'s
at close (`session_end.md` Phase 1) and `task_record.py triage`'s at record time. This hook never denies and
never adds context; an exception exits non-zero and the dispatcher reports the unavailable check.

Two delivery routes, each for one reason. A commit whose message is one readable `-F <file>` (no `-m`) gets the
trailer appended into that file and its command is not rewritten, because auto mode denies a call whose input a
hook changed after review. Every other commit (`-m`, a `-F` file not written yet, a commit nested in a launcher
such as `bash -c`) takes `--trailer` through `updatedInput`, the only place the trailer can go.

Matches the ACTION: commit invocations come from `_git_commit.commit_invocations`, so `git log --grep commit`,
an `echo` and a heredoc body pass. Only a plain `commit` in this project (`_git_commit.in_project`) is given a
trailer: a merge, cherry-pick or revert authors no triage of its own, and another repo that holds its own
`.claude/` stays silent.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
for _d in (HERE, TOOLS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from _commit_scope import publishes_index  # noqa: E402
from _git_commit import _flag_values, commit_invocations, commit_message, commit_trailer_lines, in_project  # noqa: E402
import triage  # noqa: E402


def _project_root():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.normpath(os.path.join(HERE, "..", ".."))


def _trailer(invocation, session_id):
    """The `Triage:` trailer this commit should carry, or None when it has one or the session recorded none."""
    if invocation.sub != "commit":
        return None
    message = commit_message(invocation.rest, invocation.cwd)
    if message is None:
        message = "\n".join(commit_trailer_lines(invocation.rest)) or None
    fields, error = triage.parse_trailer(message) if message is not None else (None, None)
    if fields is not None or error:
        return None
    import task_record
    fields = task_record.triage_for_session(session_id)
    return None if fields is None else "Triage: " + triage.trailer_text(fields)


def _message_file(invocation):
    """The readable `-F` file that is the commit's whole message, else None."""
    files, from_stdin = _flag_values(invocation.rest, "F")
    messages, _ = _flag_values(invocation.rest, "m")
    if len(files) != 1 or from_stdin or messages:
        return None
    path = os.path.join(invocation.cwd, files[0])
    return path if os.path.isfile(path) else None


def _append_trailer(path, trailer):
    """Append the trailer to the message file, inside its last paragraph when that is a trailer block."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read().rstrip("\r\n")
    last = text.rsplit("\n\n", 1)[-1].splitlines()
    in_block = "\n\n" in text and all(re.match(r"^[A-Za-z][A-Za-z0-9-]*: ", line) for line in last)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + ("\n" if in_block else "\n\n") + trailer + "\n")


def _rewrite(command, trailers, calls):
    """Insert each trailer at its shared source offset, escaped through its enclosing words."""
    inserts = []
    for ordinal, call in enumerate(calls):
        trailer = trailers.get(ordinal)
        if trailer is None or call.subcommand_end is None or call.body_path is None:
            continue
        option = ' --trailer "' + trailer + '"'
        for word in reversed(call.body_path):
            if word.quote == "'":
                option = option.replace("'", "'\"'\"'")
            else:
                option = re.sub(r'([\\"$`])', r'\\\1', option)
        inserts.append((call.subcommand_end, option))
    for at, option in sorted(inserts, reverse=True):
        command = command[:at] + option + command[at:]
    return command


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    session_id = payload.get("session_id") or ""
    trailers = {}
    calls = commit_invocations(command, payload.get("cwd") or os.getcwd())
    for ordinal, invocation in enumerate(calls):
        if not publishes_index(invocation.sub, invocation.rest) or not in_project(invocation.cwd, _project_root()):
            continue
        trailer = _trailer(invocation, session_id)
        if trailer is None:
            continue
        path = _message_file(invocation)
        if path is None:
            trailers[ordinal] = trailer
        else:
            _append_trailer(path, trailer)
    if not trailers:
        print("{}")
        return 0
    updated = dict(payload.get("tool_input") or {}, command=_rewrite(command, trailers, calls))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
