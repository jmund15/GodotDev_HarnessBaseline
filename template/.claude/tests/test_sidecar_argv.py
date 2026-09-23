"""Re-runnable proof for tools/sidecar_argv.py, the one reader of sidecar launch argv.

It splits a shell command into executed segments, finds the `*_sidecar.sh` and `sidecar_fanout.py`
calls those segments run (never a quoted mention), keeps the invoked path as written, and reads a
launcher's flags the way getopts reads SC_OPTSTRING. A `<launcher> --check` probe is not a dispatch.

    python3 .claude/tests/test_sidecar_argv.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(CLAUDE, "tools"))

import orchestration_metrics as om  # noqa: E402

try:
    import sidecar_argv  # noqa: E402
except ImportError:
    sidecar_argv = None


def _call(name, *args):
    if sidecar_argv is None or not hasattr(sidecar_argv, name):
        return "<missing sidecar_argv.%s>" % name
    try:
        return getattr(sidecar_argv, name)(*args)
    except Exception as exc:
        return "<raised %s: %s>" % (type(exc).__name__, exc)


def _plain(invocations):
    if not isinstance(invocations, list):
        return invocations
    return [(i.kind, i.script, i.path, list(i.args)) for i in invocations]


def _shipped_launcher():
    scripts = os.path.join(CLAUDE, "scripts")
    return next((n for n in sorted(os.listdir(scripts)) if n.endswith("_sidecar.sh")), None)


def main():
    cases = []

    cases.append(("segments split on ; && | and newlines",
                  _call("shell_segments", "a 1; b && c | d\ne")
                  == [["a", "1"], ["b"], ["c"], ["d"], ["e"]]))
    cases.append(("redirections are stripped from a segment",
                  _call("strip_redirections", ["x", ">", "log", "2>/dev/null", "y"]) == ["x", "y"]))

    cases.append(("a launch line quoted inside echo is not an invocation",
                  _plain(_call("launcher_invocations", "echo 'bash a/x_sidecar.sh -m f'")) == []))
    cases.append(("a launch line inside python -c is not an invocation",
                  _plain(_call("launcher_invocations",
                               "python3 -c \"print('bash a/x_sidecar.sh -m f')\"")) == []))
    cases.append(("a launcher named as another program's argument is not an invocation",
                  _plain(_call("launcher_invocations",
                               "python3 bench.py --launcher a/x_sidecar.sh")) == []))
    cases.append(("an env-prefixed launch after && is an invocation",
                  _plain(_call("launcher_invocations",
                               "cd /tmp && FOO=1 bash a/x_sidecar.sh -m flash"))
                  == [("sidecar", "x_sidecar.sh", "a/x_sidecar.sh", ["-m", "flash"])]))
    cases.append(("bash -x before the launcher is skipped",
                  _plain(_call("launcher_invocations", "bash -x a/x_sidecar.sh -e high"))
                  == [("sidecar", "x_sidecar.sh", "a/x_sidecar.sh", ["-e", "high"])]))
    cases.append(("Invocation.path is the invoked token as written; script is its lowercase basename",
                  _plain(_call("launcher_invocations", "./.claude/scripts/Ok_Sidecar.sh -A"))
                  == [("sidecar", "ok_sidecar.sh", "./.claude/scripts/Ok_Sidecar.sh", ["-A"])]))
    cases.append(("a fan-out call is a fanout invocation",
                  _plain(_call("launcher_invocations",
                               "python3 .claude/tools/sidecar_fanout.py j.json --out-dir r > log"))
                  == [("fanout", "sidecar_fanout.py", ".claude/tools/sidecar_fanout.py",
                       ["j.json", "--out-dir", "r"])]))
    cases.append(("launcher_calls projects (kind, script, args)",
                  _call("launcher_calls", "bash a/x_sidecar.sh -m f")
                  == [("sidecar", "x_sidecar.sh", ["-m", "f"])]))

    cases.append(("clustered flags, glued and separate values",
                  _call("sidecar_flags", ["-AW", "-mflash", "-e", "high", "-R", "r.json"])
                  == {"A": True, "W": True, "m": "flash", "e": "high", "R": "r.json"}))
    cases.append(("-X is a bare flag",
                  _call("sidecar_flags", ["-X", "-m", "f"]) == {"X": True, "m": "f"}))
    cases.append(("a --long option ends flag parsing (no spurious effort from --check)",
                  _call("sidecar_flags", ["--check", "-m", "f"]) == {}))
    cases.append(("-- and the first operand end flag parsing",
                  _call("sidecar_flags", ["-m", "f", "--", "-e", "x"]) == {"m": "f"}
                  and _call("sidecar_flags", ["-m", "f", "op", "-e", "x"]) == {"m": "f"}))

    cases.append(("fan-out args return the unresolved jobs and out-dir values",
                  _call("parse_fanout_args", ["--max-parallel", "2", "j.json", "--out-dir", "r"])
                  == ("j.json", "r")))
    cases.append(("fan-out args without --out-dir return None for it",
                  _call("parse_fanout_args", ["--out-dir=o", "j.json"]) == ("j.json", "o")
                  and _call("parse_fanout_args", ["j.json"]) == ("j.json", None)))
    cases.append(("an unknown fan-out option or a second positional is refused",
                  _call("parse_fanout_args", ["-x", "j.json"]) is None
                  and _call("parse_fanout_args", ["a.json", "b.json"]) is None
                  and _call("parse_fanout_args", ["--out-dir"]) is None))

    launcher = _shipped_launcher()
    check_rows = om.sidecar_launches("bash .claude/scripts/%s --check -m flash" % launcher) if launcher else None
    cases.append(("sidecar_launches returns [] for a <launcher> --check probe", check_rows == []))
    launch_rows = om.sidecar_launches("bash .claude/scripts/%s -m flash -e high" % launcher) if launcher else None
    cases.append(("sidecar_launches still reads a real launch",
                  isinstance(launch_rows, list) and len(launch_rows) == 1
                  and launch_rows[0]["model"] == "flash" and launch_rows[0]["effort"] == "high"))

    with open(os.path.join(CLAUDE, "scripts", "lib", "sidecar_common.sh"), encoding="utf-8") as fh:
        optstring = re.search(r'^SC_OPTSTRING="([^"]*)"', fh.read(), re.M).group(1)
    value_letters = set(re.findall(r"([A-Za-z]):", optstring))
    cases.append(("SIDECAR_VALUE_FLAGS equals SC_OPTSTRING's value-taking letters",
                  getattr(sidecar_argv, "SIDECAR_VALUE_FLAGS", None) == value_letters))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
