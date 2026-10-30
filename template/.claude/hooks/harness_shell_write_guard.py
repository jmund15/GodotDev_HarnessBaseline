#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — deny a shell command that writes a governed harness file.

Rule home: `CLAUDE.core.md` §9 Writing (`.claude/` harness files use direct Edit/Write). The Write|Edit
route carries the `instruction_quality` load gate and each file's edit notes
(`harness_edit_skill_reminder.py`); a shell write skips both, so it is denied before it runs and the deny
names the Edit/Write route.

Write forms matched (extracted by `_shell_writes`, which `sidecar_workdir_guard` shares), each on a path `harness_edit_skill_reminder.is_harness_file` governs:
- shell: `>`/`>>` redirects, `tee`, `sed -i`/`perl -i`, `dd of=`, and `cp`/`mv`/`install` whose
  destination is governed and whose source is not (a move or copy between governed files authors nothing);
- an interpreter's inline code (heredoc body, `-c`/`-e`/`-Command`): `open(<path>, 'w'|'a'|'x')`,
  `.write_text`/`.write_bytes` on a path literal or a name bound to one, `shutil` copy/move and
  `os.replace`/`os.rename` destinations, Node `writeFile*`, PowerShell `Set-Content`/`Add-Content`/
  `Out-File` and `[IO.File]::Write*`;
- the PowerShell tool: the same cmdlets, `Copy-Item`/`Move-Item -Destination`, and redirects.
Reads, mentions in quoted data, git operations and project tools that write their own state pass.
Coverage limit: a path computed at run time (joined from parts, read from a file) is not decidable from the
command text; the commit advisory in `git_guardrails.py` stays the backstop for it.

Class: process guard, blocking from its first commit at the owner's request (the commit quotes it).
Fail posture: a fault here reaches `pre_bash_dispatch`, which reports the lost check and lets the call run.

Wired in: pre_bash_dispatch on Bash|PowerShell|Monitor; `main()` also supports standalone use.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _shell_writes import written_targets  # noqa: E402
from harness_edit_skill_reminder import is_harness_file  # noqa: E402


def _governed(path, cwd):
    path = path.strip().strip("'\"")
    if not path or ("$" in path and ".claude" not in path):
        return None
    if ".claude" not in path.replace("\\", "/").split("/") and not os.path.isabs(path) and cwd:
        path = cwd.rstrip("/\\") + "/" + path
    return path if is_harness_file(path) else None


def written_harness_paths(command, tool, cwd=""):
    """The governed harness paths this command writes, by the forms `_shell_writes` lists."""
    governed = set()
    for write in written_targets(command, tool, cwd):
        dest = _governed(write.path, write.base)
        if not dest or write.verb == "git" or (write.source and _governed(write.source, write.base)):
            continue
        governed.add(dest)
    return sorted(governed)


def deny_reason(paths):
    shown = ", ".join("`%s`" % p for p in paths)
    return ("Shell write to harness file %s DENIED: harness files are authored with the Edit or Write tool "
            "(CLAUDE.core.md §9 Writing), where the instruction_quality gate and the file's edit notes run. "
            "Make this change with Edit or Write." % shown)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool = payload.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command") or ""
    paths = written_harness_paths(command, tool, payload.get("cwd") or "")
    if paths:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": deny_reason(paths),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
