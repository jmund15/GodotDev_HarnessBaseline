#!/usr/bin/env python3
"""Proof: a hook's read-only git call never leaves a stale .git/index.lock.

`git status` refreshes the index opportunistically, taking .git/index.lock to do it. A hook runs git under a
subprocess timeout; on Windows the kill is a hard terminate, so git never removes the lock and every later git
command in the checkout fails on it (seen 2026-09-25: session_context_loader's stray-index sweep, three copies at
once, 5 s timeout). `git --no-optional-locks` makes the refresh skip the lock.

  1. every `["git", ...]` argv literal in .claude/hooks/*.py that runs `status` carries `--no-optional-locks`
     before its subcommand; a forwarding helper (`*args`) is flagged only when the module calls it with
     `status`, since a helper no caller uses for status never touches the index;
  2. the flag is what stops git writing the index (control: without it, the stale-stat refresh rewrites it).

    python3 .claude/tests/test_git_optional_locks.py
"""
import ast
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
FLAG = "--no-optional-locks"
TRANSPORT = ("run", "git_run")


def _literal_elts(node):
    return [e.value if isinstance(e, ast.Constant) else "*" if isinstance(e, ast.Starred) else "?" for e in node.elts]


def _is_git_argv(node):
    return (isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant)
            and node.elts[0].value == "git")


def _enclosing_functions(tree):
    """{argv-literal line: innermost enclosing function name} for every git argv literal in the module."""
    out = {}

    def walk(node, function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if _is_git_argv(node):
            out[node.lineno] = function
        for child in ast.iter_child_nodes(node):
            walk(child, function)

    walk(tree, None)
    return out


def _status_callers(tree):
    """Names the module calls with a literal `status` (or `submodule status`) first argument: a forwarding
    helper only runs status through a caller like this, so its argv is the one that needs the flag."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
        if args[:1] == ["status"] or args[:2] == ["submodule", "status"]:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name:
                names.add(name)
    return names


def subcommand(argv):
    """(the subcommand, the element after it), skipping git's global options; a non-literal is '*'/'?'."""
    i = 1
    while i < len(argv) and isinstance(argv[i], str) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-C", "-c") else 1
    return (argv[i] if i < len(argv) else None), (argv[i + 1] if i + 1 < len(argv) else None)


def unguarded(src):
    """Lines of argv literals that run `status` (plain or `submodule status`) without the flag, and forwarding
    helpers (a non-literal subcommand) only when this module calls them with `status`: a helper that no caller
    uses for status never touches the index."""
    tree = ast.parse(src)
    enclosing = _enclosing_functions(tree)
    status_callers = _status_callers(tree)
    bad = []
    for node in ast.walk(tree):
        if not _is_git_argv(node):
            continue
        argv = _literal_elts(node)
        sub, nxt = subcommand(argv)
        if FLAG in argv:
            continue
        if sub in ("status",) or (sub == "submodule" and nxt == "status"):
            bad.append(node.lineno)
        elif sub in ("*", "?"):
            owner = enclosing.get(node.lineno)
            if owner is None or owner in status_callers:
                bad.append(node.lineno)
    for node in ast.walk(tree):
        if _is_wrapper_status_call(node):
            bad.append(node.lineno)
    return sorted(bad)


def _is_wrapper_status_call(node):
    """A transport call (`run` / `git_run` / `_git_run.run`) whose argv runs status without `optional_locks=False`
    or the flag in the argv."""
    if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], ast.List):
        return False
    func = node.func
    if (func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None) not in TRANSPORT:
        return False
    argv = _literal_elts(node.args[0])
    if argv[:1] == ["git"]:
        return False
    sub, nxt = subcommand(["git"] + argv)
    if not (sub == "status" or (sub == "submodule" and nxt == "status")):
        return False
    if FLAG in argv:
        return False
    return not any(k.arg == "optional_locks" and isinstance(k.value, ast.Constant) and k.value.value is False
                   for k in node.keywords)


def test_scan_catches_a_planted_violation():
    assert unguarded('git_run(["status", "--porcelain"], cwd=r)') == [1], "a transport status call is flagged"
    assert unguarded('run(["status"], optional_locks=False)') == [], "a transport call with optional_locks=False passes"
    assert unguarded('run(["log", "-1"])') == [], "a transport call without status is out of scope"
    assert unguarded('subprocess.run(["git", "status", "--porcelain"])') == [1], "the scan must flag an unguarded status"
    assert unguarded('def g(*a):\n    subprocess.run(["git", *a])\ng("status")') == [2], \
        "a forwarding helper whose caller runs status is flagged"
    assert unguarded('def g(*a):\n    subprocess.run(["git", *a])\ng("log", "-1")') == [], \
        "a forwarding helper no caller runs status through is out of scope"
    assert unguarded('subprocess.run(["git", "--no-optional-locks", "status"])') == [], "a guarded call passes"
    assert unguarded('subprocess.run(["git", "log", "-1"])') == [], "a call with no status is out of scope"
    assert unguarded('subprocess.run(["git", "submodule", "update", "--", *paths])') == [], "a fixed non-status subcommand is out of scope"
    assert unguarded('subprocess.run(["git", "-C", r, "submodule", "status"])') == [1], "submodule status is status"


def test_every_hook_git_status_skips_optional_locks():
    bad = []
    for f in sorted(HOOKS.glob("*.py")):
        for line in unguarded(f.read_text(encoding="utf-8")):
            bad.append(f"{f.name}:{line}")
    assert not bad, "hook git argv without %s: %s" % (FLAG, ", ".join(bad))


def _git(repo, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo,
                          capture_output=True, text=True, check=True)


def test_the_flag_stops_the_index_write():
    with tempfile.TemporaryDirectory() as td:
        _git(td, "init", "-q")
        f = os.path.join(td, "a.txt")
        open(f, "w").write("a\n")
        _git(td, "add", "a.txt")
        _git(td, "commit", "-qm", "a")
        index = os.path.join(td, ".git", "index")

        def stale_then(argv):
            later = time.time() + 5
            os.utime(f, (later, later))               # same content, new stat: the index entry needs a refresh
            before = os.stat(index).st_mtime_ns
            time.sleep(0.05)
            _git(td, *argv)
            return os.stat(index).st_mtime_ns != before

        assert stale_then(["status", "--porcelain"]), "control: plain status refreshes (writes) the index"
        assert not stale_then([FLAG, "status", "--porcelain"]), "with %s status must not write the index" % FLAG


def run():
    cases = (test_scan_catches_a_planted_violation, test_every_hook_git_status_skips_optional_locks,
             test_the_flag_stops_the_index_write)
    failed = 0
    for case in cases:
        try:
            case()
            print("ok %s" % case.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (case.__name__, e))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
