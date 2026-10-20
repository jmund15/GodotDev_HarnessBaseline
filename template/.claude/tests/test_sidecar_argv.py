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
    heredoc_then_launch = ("cat >> brief.md <<'EOF'\nStep 1 built it's `x` \"quoted\".\nEOF\n"
                           "bash a/x_sidecar.sh -m f -l run")
    cases.append(("a launch after a heredoc whose body does not tokenize is still an invocation",
                  [c[:2] for c in _call("launcher_calls", heredoc_then_launch) or []] == [("sidecar", "x_sidecar.sh")]))
    launch_in_heredoc = "cat > run.sh <<-EOF\n\tbash a/x_sidecar.sh -m f\n\tEOF\necho done"
    cases.append(("a launch line inside a heredoc body is file content, not an invocation",
                  _call("launcher_calls", launch_in_heredoc) == []))
    quoted_body_backticks = ("cat > x.py <<'PYEOF'\nwhere = \"`.claude/tools/sidecar_fanout.py`\"\nPYEOF\n"
                             "python3 x.py")
    cases.append(("backticks inside a quoted-delimiter heredoc body are text, not a substitution",
                  _plain(_call("launcher_invocations", quoted_body_backticks)) == []))
    unquoted_body_substitution = "cat > x.txt <<EOF\n$(bash a/x_sidecar.sh -m f)\nEOF"
    cases.append(("a substitution inside an unquoted heredoc body still runs",
                  [c[:2] for c in _plain(_call("launcher_invocations", unquoted_body_substitution)) or []]
                  == [("sidecar", "x_sidecar.sh")]))
    cases.append(("a here-string is not a heredoc: the next line still runs",
                  [c[:2] for c in _call("launcher_calls", "grep x <<< 'a'\nbash a/x_sidecar.sh -m f") or []]
                  == [("sidecar", "x_sidecar.sh")]))
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
    # 2026-09-24: `bash -n <launcher>` (syntax check) was denied as a dispatch needing the ladder read.
    cases.append(("bash -n (parse only) executes nothing: not an invocation",
                  _plain(_call("launcher_invocations", "bash -n a/x_sidecar.sh")) == []))
    cases.append(("... also inside a combined flag cluster (-xn)",
                  _plain(_call("launcher_invocations", "bash -xn a/x_sidecar.sh")) == []))
    cases.append(("Invocation.path is the invoked token as written; script is its lowercase basename",
                  _plain(_call("launcher_invocations", "./.claude/scripts/Ok_Sidecar.sh -A"))
                  == [("sidecar", "ok_sidecar.sh", "./.claude/scripts/Ok_Sidecar.sh", ["-A"])]))
    cases.append(("a fan-out call is a fanout invocation",
                  _plain(_call("launcher_invocations",
                               "python3 .claude/tools/sidecar_fanout.py j.json --out-dir r > log"))
                  == [("fanout", "sidecar_fanout.py", ".claude/tools/sidecar_fanout.py",
                       ["j.json", "--out-dir", "r"])]))
    # Wrapper commands and `bash -c` carriers still EXECUTE the launcher (observed 2026-09-23: a
    # gate keyed on this reader failed open for every shape it did not recognize).
    x_launch = [("sidecar", "x_sidecar.sh", "a/x_sidecar.sh", ["-m", "f"])]
    for label, command in (
        ("nohup", "nohup bash a/x_sidecar.sh -m f"),
        ("timeout with a duration", "timeout 600 bash a/x_sidecar.sh -m f"),
        ("timeout with options", "timeout -k 5 --signal=TERM 600 bash a/x_sidecar.sh -m f"),
        ("env with options and assignments", "env -u HOME FOO=1 bash a/x_sidecar.sh -m f"),
        ("time", "time bash a/x_sidecar.sh -m f"),
        ("nice -n", "nice -n 5 bash a/x_sidecar.sh -m f"),
        ("command and exec", "command exec bash a/x_sidecar.sh -m f"),
        ("bash -c", "bash -c 'bash a/x_sidecar.sh -m f'"),
        ("sh -lc after cd", "cd x && sh -lc 'cd y && bash a/x_sidecar.sh -m f'"),
    ):
        cases.append(("a %s-wrapped launch is an invocation" % label,
                      _plain(_call("launcher_invocations", command)) == x_launch))
    # Compound shapes and carriers the shell still executes (S40, 2026-09-24: each returned [] and
    # the ladder gate exited before judging it).
    for label, command in (
        ("subshell", "(bash a/x_sidecar.sh -m f)"),
        ("cd-prefixed subshell", "(cd wt && bash a/x_sidecar.sh -m f)"),
        ("brace group", "{ bash a/x_sidecar.sh -m f; }"),
        ("if/then body", "if true; then bash a/x_sidecar.sh -m f; fi"),
        ("if condition", "if bash a/x_sidecar.sh -m f; then :; fi"),
        ("negated", "! bash a/x_sidecar.sh -m f"),
        ("eval string", "eval 'bash a/x_sidecar.sh -m f'"),
        ("unquoted $() substitution", "echo $(bash a/x_sidecar.sh -m f)"),
        ("double-quoted $() substitution", "echo \"$(bash a/x_sidecar.sh -m f)\""),
        ("backtick substitution", "x=`bash a/x_sidecar.sh -m f`"),
        ("bash -o pipefail -c", "bash -o pipefail -c 'bash a/x_sidecar.sh -m f'"),
    ):
        cases.append(("%s: the launch is an invocation" % label,
                      _plain(_call("launcher_invocations", command)) == x_launch))
    cases.append(("a for/do-body fan-out is a fanout invocation",
                  _plain(_call("launcher_invocations",
                               "for i in 1; do python3 t/sidecar_fanout.py j.json; done"))
                  == [("fanout", "sidecar_fanout.py", "t/sidecar_fanout.py", ["j.json"])]))
    for label, command in (
        ("a single-quoted $() mention", "echo '$(bash a/x_sidecar.sh -m f)'"),
        ("a quoted mention inside a subshell", "(echo 'bash a/x_sidecar.sh -m f')"),
        ("an eval of an echo", "eval 'echo bash a/x_sidecar.sh -m f'"),
        ("bash -o pipefail -n (parse only)", "bash -o pipefail -n a/x_sidecar.sh"),
        ("bash -n inside a brace group", "{ bash -n a/x_sidecar.sh; }"),
    ):
        cases.append(("%s is not an invocation" % label,
                      _plain(_call("launcher_invocations", command)) == []))
    cases.append(("a bash -c carried fan-out is a fanout invocation",
                  _plain(_call("launcher_invocations",
                               "bash -c 'python3 t/sidecar_fanout.py j.json'"))
                  == [("fanout", "sidecar_fanout.py", "t/sidecar_fanout.py", ["j.json"])]))
    for label, command in (
        ("a wrapped grep naming the launcher", "timeout 5 grep -n m a/x_sidecar.sh"),
        ("a bash -c echo of a launch line", "bash -c 'echo bash a/x_sidecar.sh -m f'"),
        ("a bare env listing piped to grep", "env | grep x_sidecar.sh"),
    ):
        cases.append(("%s is not an invocation" % label,
                      _plain(_call("launcher_invocations", command)) == []))
    cases.append(("launcher_calls projects (kind, script, args)",
                  _call("launcher_calls", "bash a/x_sidecar.sh -m f")
                  == [("sidecar", "x_sidecar.sh", ["-m", "f"])]))

    cases.append(("clustered flags, glued and separate values",
                  _call("sidecar_flags", ["-AW", "-mflash", "-e", "high", "-R", "r.json"])
                  == {"A": True, "W": True, "m": "flash", "e": "high", "R": "r.json"}))
    cases.append(("-X is a bare flag",
                  _call("sidecar_flags", ["-X", "-m", "f"]) == {"X": True, "m": "f"}))
    # S9: -Y carries the launch's pin basis, `<pick>,<spec>`; it consumes the next token like -m.
    cases.append(("-Y is a value flag",
                  _call("sidecar_flags", ["-Y", "cell,tight", "-m", "f"]) == {"Y": "cell,tight", "m": "f"}
                  and _call("sidecar_flags", ["-Ycell,tight"]) == {"Y": "cell,tight"}
                  and "Y" in (getattr(sidecar_argv, "SIDECAR_VALUE_FLAGS", None) or ())))
    cases.append(("sidecar_flag_pairs keeps repeated -C and -a values in order",
                  _call("sidecar_flag_pairs", ["-C", "one", "-a", "two", "-C", "three", "-a", "four"])
                  == ([ ("C", "one"), ("a", "two"), ("C", "three"), ("a", "four") ], 8)))
    cases.append(("sidecar_flag_pairs retains bare -X and reports the first operand",
                  _call("sidecar_flag_pairs", ["-X", "-m", "f", "operand", "-a", "later"])
                  == ([ ("X", True), ("m", "f") ], 3)))
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
    cases.append(("a --review/--fold launch still names its jobs file",
                  _call("parse_fanout_args", ["j.json", "--review", "--fold", "t.md", "--fold-alias", "arm-a",
                                              "--fold-effort", "medium", "--plan", "a.md", "--plan=b.md",
                                              "--max-parallel", "7"]) == ("j.json", None)))
    cases.append(("a --fold-plan launch still names its jobs file",
                  _call("parse_fanout_args", ["j.json", "--review", "--fold", "t.md", "--fold-plan", "max"])
                  == ("j.json", None)
                  and _call("parse_fanout_args", ["j.json", "--fold-plan=max"]) == ("j.json", None)))
    cases.append(("an empty token from an unquoted variable that expanded to nothing is no argument",
                  _call("parse_fanout_args", ["j.json", "--review", "--out-dir", "o", "", "--detach"])
                  == ("j.json", "o")))
    cases.append(("a fan-out mode that launches nothing is not a launch",
                  _call("parse_fanout_args", ["--status", "o"]) is None
                  and _call("parse_fanout_args", ["--review-artifact", "r.json", "--select", "F1"]) is None))
    cases.append(("an unknown fan-out option or a second positional is refused",
                  _call("parse_fanout_args", ["-x", "j.json"]) is None
                  and _call("parse_fanout_args", ["a.json", "b.json"]) is None
                  and _call("parse_fanout_args", ["--out-dir"]) is None))

    # 2026-10-18: `sidecar_fanout.py j.json --out dir --detach` is argparse's abbreviation of --out-dir, so a
    # launch written that way ran while this reader called it no launch and its records stayed unattributed.
    cases.append(("an unambiguous option prefix reads as the option argparse resolves it to",
                  _call("parse_fanout_args", ["j.json", "--out", "r", "--detach"]) == ("j.json", "r")
                  and _call("parse_fanout_args", ["j.json", "--out=r"]) == ("j.json", "r")
                  and _call("parse_fanout_args", ["j.json", "--out-d", "r"]) == ("j.json", "r")
                  and _call("parse_fanout_args", ["j.json", "--det", "--dry"]) == ("j.json", None)
                  and _call("parse_fanout_args", ["j.json", "--fold-a", "arm-a"]) == ("j.json", None)))
    cases.append(("an ambiguous prefix, a prefix of a non-launch mode and an unknown prefix are no launch",
                  _call("parse_fanout_args", ["j.json", "--re"]) is None
                  and _call("parse_fanout_args", ["--stat", "o"]) is None
                  and _call("parse_fanout_args", ["--select", "F1", "j.json"]) is None
                  and _call("parse_fanout_args", ["j.json", "--outx", "r"]) is None))
    cases.append(("an exact option wins over longer options it prefixes",
                  _call("parse_fanout_args", ["j.json", "--fold", "t.md", "--out-dir", "r"]) == ("j.json", "r")))

    for flag in ('--root', '--ledger-head', '--ledger-blobs', '--ledger-premise-commits'):
        for option in ([flag, 'fixture'], [flag + '=fixture']):
            cases.append((flag + ' keeps the jobs path with ' + repr(option),
                          _call('parse_fanout_args', option + ['jobs.json']) == ('jobs.json', None)))

    try:
        import sidecar_fanout  # noqa: E402
        parser_options = {opt: action.nargs != 0 for action in sidecar_fanout.build_parser()._actions
                          for opt in action.option_strings}
    except Exception as exc:
        parser_options = "<raised %s: %s>" % (type(exc).__name__, exc)
    values = set(getattr(sidecar_argv, "FANOUT_VALUE_OPTIONS", ()))
    flags = set(getattr(sidecar_argv, "FANOUT_FLAG_OPTIONS", ()))
    non_launch = set(getattr(sidecar_argv, "FANOUT_NON_LAUNCH_OPTIONS", ()))
    classified = {opt: True for opt in values} | {opt: False for opt in flags}
    cases.append(("every sidecar_fanout option is classified once, by its value arity",
                  isinstance(parser_options, dict)
                  and not (values & flags) and not ((values | flags) & non_launch)
                  and {o: v for o, v in parser_options.items() if o not in non_launch} == classified
                  and non_launch <= set(parser_options)))

    launcher = _shipped_launcher()
    check_rows = om.sidecar_launches("bash .claude/scripts/%s --check -m flash" % launcher) if launcher else None
    cases.append(("sidecar_launches returns [] for a <launcher> --check probe", check_rows == []))
    launch_rows = om.sidecar_launches("bash .claude/scripts/%s -m flash -e high" % launcher) if launcher else None
    cases.append(("sidecar_launches still reads a real launch",
                  isinstance(launch_rows, list) and len(launch_rows) == 1
                  and launch_rows[0]["model"] == "flash" and launch_rows[0]["effort"] == "high"))

    # 2026-10-09, session ffd4976e: `T=$(mktemp -d) && <launcher> -R "$T/x.record.json"` registered
    # `<cwd>/$/x.record.json`, a record no run writes, so the Stop hook waited on it every turn. A value known
    # only at run time leaves `$T` as written, where the tracker's shell-variable check refuses it.
    runtime = [om.sidecar_launches('%s && bash .claude/scripts/%s -l x -R "$T/x.record.json" -- hi' % (assign, launcher))
               if launcher else None for assign in ("T=$(mktemp -d)", "T=`mktemp -d`", "T=$OTHER/sub")]
    cases.append(("a run-time assignment leaves $T in the record path",
                  all(rows and len(rows) == 1 and "$T" in (rows[0]["record"] or "") for rows in runtime)))
    literal = om.sidecar_launches('T=/tmp/x && bash .claude/scripts/%s -l x -R "$T/x.record.json" -- hi'
                                  % launcher) if launcher else None
    cases.append(("a literal assignment still resolves the record path",
                  bool(literal) and literal[0]["record"].replace("\\", "/").endswith("/tmp/x/x.record.json")))

    # 2026-10-19, session 30b7e19d: `--ledger-head $(git rev-parse HEAD)` lost its value to the substitution
    # strip, so `--ledger-blobs` became that option's value, the fan-out read as no launch, and the session
    # watcher printed NONE until the launcher registered its own seat.
    substituted = ("S=r5; python3 .claude/tools/sidecar_fanout.py $S/jobs.json --out $S/out --review "
                   "--ledger-head $(git rev-parse HEAD) --ledger-blobs $S/blobs.json --detach 2>&1 | tail -1")
    calls = _call("launcher_calls", substituted) or []
    parsed = _call("parse_fanout_args", calls[0][2]) if calls else None
    cases.append(("a command substitution in a fan-out option value keeps the argv parseable",
                  bool(parsed) and parsed[0] == "$S/jobs.json" and parsed[1] == "$S/out"))
    inner = _call("launcher_calls", "echo $(bash a/x_sidecar.sh -m f)") or []
    cases.append(("a launcher inside a substitution body is still read",
                  [c[:2] for c in inner] == [("sidecar", "x_sidecar.sh")]))

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
