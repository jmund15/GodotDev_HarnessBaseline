#!/usr/bin/env python3
"""PreToolUse guard (class: floor) keeping a sidecar child that runs in a linked worktree out of the main checkout.

Usage: sidecar_workdir_guard.py <workdir> <main-checkout> [<spill-dir>]
Denies Write, Edit and NotebookEdit whose resolved path is inside the main checkout and outside the child's
workdir, and a Bash command with a clear write there: any form `_shell_writes` extracts (a redirect, tee, a
cp/mv/install destination, dd of=, a sed -i or perl -i file, an interpreter's inline write), a mutating
`git -C <main>`, or a python command holding a main-checkout path literal beside a write call.
The spill dir (where the launcher's -R/-P files and the job's `<label>.md` report live) and any path
outside the main checkout stay allowed. A job whose workdir is the main checkout is unaffected.
A payload it cannot parse is denied. A Bash target it cannot resolve statically (variable, substitution, glob) is allowed.
Armed by lib/sidecar_common.sh from adaptation.json sidecar_hook_rows; rule home: reference/sidecar_dispatch.md.
Channel: exit 2 + stderr.
"""
import json
import os
import posixpath
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _git_commit import resolve_cd
from _shell_writes import segment_states, written_targets
from _win_path import drive_path

WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
PATH_FIELDS = ("file_path", "notebook_path")
GIT_WRITES = {"add", "rm", "mv", "commit", "reset", "checkout", "restore", "stash", "apply", "merge", "rebase",
              "cherry-pick", "revert", "am", "clean", "switch", "pull", "tag", "branch", "update-index"}
_UNRESOLVED = re.compile(r"[$`*?\[\]{}()~]")
_PY_WRITE = re.compile(r"""write_text|write_bytes|\.write\(|shutil\.|os\.(?:rename|replace|remove)|open\([^)]*,\s*[rb]*['"][wax+]""")
_PY_PATH = re.compile(r"""['"]((?:[A-Za-z]:)?[\\/][^'"\s]+)['"]""")


def parts(p, base=""):
    p = drive_path(p.replace("\\", "/"))
    if base and not (p.startswith("/") or (len(p) > 1 and p[1] == ":")):
        p = drive_path(base.replace("\\", "/")) + "/" + p
    return tuple(x for x in posixpath.normpath(p.lower()).split("/") if x)


def inside(path, root):
    return bool(root) and path[:len(root)] == root


def _git_target(words):
    if not words or posixpath.basename(words[0]) != "git":
        return None
    i, where = 1, None
    while i < len(words):
        if words[i] == "-C" and i + 1 < len(words):
            where, i = words[i + 1], i + 2
        elif words[i].startswith("-"):
            i += 1
        else:
            break
    return where if where is not None and i < len(words) and words[i] in GIT_WRITES else None


def bash_violation(command, cwd, work, main_root, spill):
    def bad(path, base):
        if not path or _UNRESOLVED.search(path) or path.startswith("/dev/"):
            return None
        p = parts(resolve_cd(base, path))
        return path if inside(p, main_root) and not inside(p, work) and not inside(p, spill) else None

    for write in written_targets(command, "Bash", cwd):
        hit = bad(write.path, write.base)
        if hit:
            return hit
    for base, _segment, words in segment_states(command, cwd):
        targets = []
        where = _git_target(words)
        if where:
            targets.append(where)
        if words and re.match(r"python[\d.]*(\.exe)?$", posixpath.basename(words[0])) and _PY_WRITE.search(command):
            targets += _PY_PATH.findall(command)
        for target in targets:
            hit = bad(target, base)
            if hit:
                return hit
    return None


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("sidecar_workdir_guard: needs <workdir> <main-checkout>; denied.\n")
        return 2
    work, main_root = parts(sys.argv[1]), parts(sys.argv[2])
    spill = parts(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else ()
    if work == main_root:
        return 0
    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input") or {}
    except (ValueError, AttributeError):
        sys.stderr.write("sidecar_workdir_guard: unreadable payload; denied.\n")
        return 2
    if data.get("tool_name") == "Bash":
        cmd = tool_input.get("command")
        hit = bash_violation(cmd, data.get("cwd") or sys.argv[1], work, main_root, spill) if isinstance(cmd, str) else None
        if hit:
            sys.stderr.write(
                "Denied: this command writes %s in the main checkout, outside this job's workdir %s "
                "(reference/sidecar_dispatch.md, worktree jobs). Write under %s instead.\n"
                % (hit, sys.argv[1], sys.argv[1]))
            return 2
        return 0
    if data.get("tool_name") not in WRITE_TOOLS:
        return 0
    for field in PATH_FIELDS:
        raw = tool_input.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        path = parts(raw, data.get("cwd") or sys.argv[1])
        if inside(path, main_root) and not inside(path, work) and not inside(path, spill):
            sys.stderr.write(
                "Denied: %s is in the main checkout, outside this job's workdir %s "
                "(reference/sidecar_dispatch.md, worktree jobs). Write the file under %s instead.\n"
                % (raw, sys.argv[1], sys.argv[1]))
            return 2
    return 0


sys.exit(main())
