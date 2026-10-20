#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- warn on a commit whose changed rule text holds a number with no
row in the instruction-rows file (`instruction_quality` §1). It never denies: whether a number has a
basis is a judgment, so the author judges it here and the reviewers who run the same check judge it later.

The decision is `tools/extract_numbers.py check`, run in-process over the paths this commit
publishes. An index commit is judged on the index (`--staged`); a commit with `-a`, `-i`, a pathspec
or `--pathspec-from-file` publishes the working tree (`_git_commit.commit_source`), so it is judged on
the working tree against HEAD. A
merge-arrived file is dropped (`_commit_scope.authored_paths`): it was judged where it was
committed. Check exit 2 allows the commit: missing scope or a failed/unavailable scan is
uncovered, not proof that every number has a row. This guard does not check behavior claims.

A crash, including a failed `extract_numbers` import, exits 1: the dispatcher shows the error to the user
and the commit proceeds.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", via
`pre_bash_dispatch.HOOKS` after `baseline_classification_guard`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "tools")
for _p in (_HOOKS_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _git_commit import commit_invocations, commit_source, run_git, staged_paths  # noqa: E402
from _commit_scope import authored_paths, publishes_index  # noqa: E402
from _hook_state import fire_once  # noqa: E402

FIX_HINT = (
    "instruction_quality §1: does this number bound agent behavior? if yes, give its basis or rewrite it "
    "as a judgment. Record its class with `extract_numbers.py stub --staged` "
    "(`--paths <files>` for a pathspec commit)."
)


def _judge_commit(rest: list, cwd: str, env: dict | None = None, session_id: str = "") -> str | None:
    root = run_git(["rev-parse", "--show-toplevel"], cwd, env)
    if not root:
        return None
    root = root.strip()
    committed, failed_cmd = staged_paths(rest, cwd, env)
    if committed is None:
        return "[numbers] could not list the commit's paths (git %s); its numbers are unchecked." % failed_cmd
    paths = sorted(authored_paths(committed, cwd, env))
    if not paths:
        return None
    source = commit_source(rest, cwd, env)
    if source is None:
        return ("[numbers] cannot read this `-i` commit: it stages paths outside its pathspec that differ from "
                "their working copies, so its numbers are unchecked.")
    from_tree = source == "worktree"
    import extract_numbers  # here, so an import failure reaches main()'s fail-closed deny

    argv = ["--repo", root, "check", "--json"] + ([] if from_tree else ["--staged"]) + ["--paths"] + paths
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = extract_numbers.main(argv)
    if code in (0, 2):
        return None
    report = json.loads(out.getvalue())
    fresh = [f for f in report['failures'] if fire_once(session_id, 'rule_number_guard:' +
             json.dumps([root, f['path'], f['token'], f['rule']]))]
    if not fresh:
        if report['counts']['unparseable_rows'] and fire_once(session_id, 'rule_number_guard:bad_rows:' + root):
            return '[numbers] unparseable instruction rows; run extract_numbers.py check.'
        return None
    lines = ['%s:%d %s: %s' % (f['path'], f['line'], f['token'], f['rule']) for f in fresh]
    return "[numbers] changed rule text has a number with no row:\n%s\n%s" % ('\n'.join(lines), FIX_HINT)


def _run() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    for invocation in commit_invocations(command, payload.get("cwd") or "."):
        if not publishes_index(invocation.sub, invocation.rest):
            continue
        reason = _judge_commit(invocation.rest, invocation.cwd, invocation.git_env, payload.get('session_id') or '')
        if reason:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": reason}}))
            return 0
    return 0


def main() -> int:
    try:
        return _run()
    except (Exception, SystemExit) as exc:
        print("rule_number_guard crashed (%s: %s); this commit's numbers are unchecked."
              % (type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
