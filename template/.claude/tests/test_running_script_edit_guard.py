#!/usr/bin/env python3
"""Re-runnable proof for hooks/running_script_edit_guard.py.

Two failure classes it must not repeat (both measured 2026-09-08): warning because a command
line merely NAMED the script (a peer's grep, a `bash -c` wrapper string), and warning on a row
whose process had exited between the table snapshot and the warning. Planted table + planted
liveness probe; the last two cases run the real hook end to end.

    python3 .claude/tests/test_running_script_edit_guard.py
"""
import importlib.util
import json
import os
import subprocess
import sys

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


def main():
    cases = []
    inv = lambda cmd: g._is_invocation(cmd, "opencode_sidecar.sh")  # noqa: E731
    cases.append(("bash <script> args IS an invocation", inv(f"bash {S} -m muse")))
    cases.append(("a quoted absolute bash.exe path still counts", inv(f'"C:\\Program Files\\Git\\usr\\bin\\bash.exe" {S} -m muse')))
    cases.append(("timeout N bash <script> passes through to the shell", inv(f"timeout 600 bash {S}")))
    cases.append(("the script run directly as the program counts", inv(f"C:/repo/{S} -m opus")))
    cases.append(("a bash -c wrapper STRING is not an invocation", not inv(f'bash -c "source snap; bash {S} --check"')))
    cases.append(("pwsh -Command mentioning the path is not an invocation", not inv(f'pwsh -Command "cat {S}"')))
    cases.append(("grep on the script is a noun, not an invocation", not inv(f"grep -n foo {S}")))
    cases.append(("git log -- <script> is a noun", not inv(f"git log -- {S}")))
    cases.append(("an argument to a non-shell program is a mention", not inv(f"python3 tool.py --launcher {S}")))
    cases.append(("a different script name never matches", not inv("bash .claude/scripts/deepseek_sidecar.sh -m flash")))

    # Planted table + planted probe: 104 exited after the snapshot; this process's own row is an ancestor.
    hits = g.find_live_instances("opencode_sidecar.sh", scan=lambda: TABLE,
                                 alive=lambda pids: set(pids) - {"104"})
    pids = sorted(h.split(" ", 1)[0] for h in hits)
    cases.append(("only the true, still-alive invocations survive (100, 105, 106)", pids == ["100", "105", "106"], pids))
    cases.append(("the wrapper, the grep, the tool argument and the git mention are dropped",
                  not any(p in pids for p in ("101", "102", "103", "107"))))
    cases.append(("a row that died between scan and warning is dropped", "104" not in pids))
    cases.append(("the hook's own ancestor chain is excluded", ME not in pids))
    probe_calls = []
    g.find_live_instances("opencode_sidecar.sh", scan=lambda: "102|1|grep x " + S,
                          alive=lambda pids: probe_calls.append(pids) or set(pids))
    cases.append(("no candidates -> the liveness probe is never spawned", probe_calls == []))
    cases.append(("a scan failure stays silent", g.find_live_instances("x.sh", scan=lambda: (_ for _ in ()).throw(OSError("boom"))) == []))

    # End to end through stdin: a non-guarded path exits 0 with no output; a guarded path whose
    # script nothing runs also stays silent. Any other exit code is a CRASH, never a pass.
    def run(payload):
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, timeout=60, encoding="utf-8")
        return r.returncode, (r.stdout or "").strip()

    rc, out = run({"tool_name": "Edit", "tool_input": {"file_path": "C:/repo/Source/Foo.cs"}})
    cases.append(("e2e: a non-script edit exits 0 silently", rc == 0 and out == "", (rc, out[:80])))
    rc, out = run({"tool_name": "Write", "tool_input": {"file_path": "C:/repo/.claude/scripts/zz_nobody_runs_this_9f3.sh"}})
    cases.append(("e2e: a guarded script nothing runs exits 0 silently", rc == 0 and out == "", (rc, out[:80])))

    failures = [c for c in cases if not c[1]]
    for c in cases:
        print("%-4s %s%s" % ("ok" if c[1] else "FAIL", c[0], "" if c[1] or len(c) < 3 else "  got=%r" % (c[2],)))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
