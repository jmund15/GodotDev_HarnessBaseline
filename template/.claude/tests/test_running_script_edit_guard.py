#!/usr/bin/env python3
"""Re-runnable proof for hooks/running_script_edit_guard.py.

Two failure classes it must not repeat (both measured 2026-09-08): warning because a command
line merely NAMED the script (a peer's grep, a `bash -c` wrapper string), and warning on a row
whose process had exited between the table snapshot and the warning. Planted table + planted
liveness probe; the last two cases run the real hook end to end.

    python -m pytest .claude/tests/test_running_script_edit_guard.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "running_script_edit_guard.py")
spec = importlib.util.spec_from_file_location("rseg", HOOK)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

S = ".claude/scripts/opencode_sidecar.sh"
ME = str(os.getpid())
TABLE = "\n".join([
    f'100|1|"C:\\Program Files\\Git\\usr\\bin\\bash.exe" {S} -m muse -e max -D pointer',
    f'101|1|"C:\\Program Files\\Git\\bin\\bash.exe" -c "source /c/u/snap.sh; bash {S} --check"',
    f'102|1|grep -n oc_live_probe {S}',
    f'103|1|python3 .claude/tools/x.py --launcher {S}',
    f'104|1|bash {S} -m luna -e low',
    f'105|1|timeout 600 bash {S} -m sonnet',
    f'106|1|C:/repo/{S} -m opus',
    f'107|1|git log --oneline -- {S}',
    f'{ME}|1|bash {S} -m self',
])


def is_invocation(cmd):
    return g._is_invocation(cmd, "opencode_sidecar.sh")


@pytest.mark.parametrize("cmd", [
    f"bash {S} -m muse",
    f'"C:\\Program Files\\Git\\usr\\bin\\bash.exe" {S} -m muse',
    f"timeout 600 bash {S}",
    f"C:/repo/{S} -m opus",
], ids=["bash-script", "quoted-bash-exe", "timeout-passthrough", "script-as-program"])
def test_a_shell_running_the_script_is_an_invocation(cmd):
    assert is_invocation(cmd)


@pytest.mark.parametrize("cmd", [
    f'bash -c "source snap; bash {S} --check"',
    f'pwsh -Command "cat {S}"',
    f"grep -n foo {S}",
    f"git log -- {S}",
    f"python3 tool.py --launcher {S}",
    "bash .claude/scripts/deepseek_sidecar.sh -m flash",
], ids=["bash-c-wrapper", "pwsh-mention", "grep-noun", "git-noun", "non-shell-argument", "other-script-name"])
def test_a_mention_of_the_script_is_not_an_invocation(cmd):
    assert not is_invocation(cmd)


def test_only_the_true_still_alive_invocations_survive():
    """Planted table + planted probe: 104 exited after the snapshot; this process's own row is an ancestor."""
    hits = g.find_live_instances("opencode_sidecar.sh", scan=lambda: TABLE,
                                 alive=lambda pids: set(pids) - {"104"})
    pids = sorted(h.split(" ", 1)[0] for h in hits)
    assert pids == ["100", "105", "106"], pids


def test_the_wrapper_the_grep_the_tool_argument_and_the_git_mention_are_dropped():
    hits = g.find_live_instances("opencode_sidecar.sh", scan=lambda: TABLE, alive=lambda pids: set(pids))
    pids = [h.split(" ", 1)[0] for h in hits]
    assert not any(p in pids for p in ("101", "102", "103", "107"))


def test_a_row_that_died_between_scan_and_warning_is_dropped():
    hits = g.find_live_instances("opencode_sidecar.sh", scan=lambda: TABLE,
                                 alive=lambda pids: set(pids) - {"104"})
    assert "104" not in [h.split(" ", 1)[0] for h in hits]


def test_the_hooks_own_ancestor_chain_is_excluded():
    hits = g.find_live_instances("opencode_sidecar.sh", scan=lambda: TABLE, alive=lambda pids: set(pids))
    assert ME not in [h.split(" ", 1)[0] for h in hits]


def test_no_candidates_means_the_liveness_probe_is_never_spawned():
    probe_calls = []
    g.find_live_instances("opencode_sidecar.sh", scan=lambda: "102|1|grep x " + S,
                          alive=lambda pids: probe_calls.append(pids) or set(pids))
    assert probe_calls == []


def test_a_scan_failure_stays_silent():
    def boom():
        raise OSError("boom")
    assert g.find_live_instances("x.sh", scan=boom) == []


def test_a_script_that_does_not_exist_yet_is_never_scanned_for(tmp_path):
    """No running instance can exist for a file not yet written, so the process-table query (about a second
    on Windows) is never paid for it."""
    scans = []
    absent = os.path.join(str(tmp_path), ".claude", "scripts", "opencode_sidecar.sh")
    os.makedirs(os.path.dirname(absent))
    g.process({"tool_name": "Write", "tool_input": {"file_path": absent}},
              scan=lambda: scans.append(1) or TABLE, alive=lambda p: set(p))
    assert scans == [], scans


def test_an_existing_script_is_scanned_and_its_live_instances_reported(tmp_path):
    scans = []
    existing = os.path.join(str(tmp_path), ".claude", "scripts", "opencode_sidecar.sh")
    os.makedirs(os.path.dirname(existing))
    with open(existing, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/bash\n")
    out = g.process({"tool_name": "Edit", "tool_input": {"file_path": existing}},
                    scan=lambda: scans.append(1) or TABLE, alive=lambda p: set(p))
    assert scans == [1] and bool(out), scans


def run_hook(payload):
    """Spawned: the hook's stdin entry point is the subject (exit code and output channel)."""
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=60, encoding="utf-8")
    return r.returncode, (r.stdout or "").strip()


def test_e2e_a_non_script_edit_exits_zero_silently():
    rc, out = run_hook({"tool_name": "Edit", "tool_input": {"file_path": "C:/repo/Source/Foo.cs"}})
    assert rc == 0 and out == "", (rc, out[:80])


def test_e2e_a_guarded_script_nothing_runs_exits_zero_silently():
    rc, out = run_hook({"tool_name": "Write", "tool_input": {"file_path": "C:/repo/.claude/scripts/zz_nobody_runs_this_9f3.sh"}})
    assert rc == 0 and out == "", (rc, out[:80])
