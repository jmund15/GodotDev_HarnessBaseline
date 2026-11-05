"""Every measured CLI refusal states the shape it accepts and one valid call.

One wire format, owned by `tools/cli_usage.py`: a refusal exits non-zero and stderr carries an
`accepts:` line and an `example:` line. The printed example must parse under the tool's own parser,
checked parser-only — some tools' examples write a ledger.

    python3 .claude/tests/test_cli_refusal_shapes.py
"""
import importlib.util
import os
import re
import shlex
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")


def call(name, *args):
    return subprocess.run([sys.executable, os.path.join(TOOLS, name), *args],
                          capture_output=True, text=True, cwd=HERE)


def load(name):
    path = os.path.join(TOOLS, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def parser_for(name, interleaved=False):
    """The tool's real parser, or the exception that kept it out of reach."""
    try:
        parser = load(name).build_parser()
        return lambda argv: (parser.parse_intermixed_args(argv) if interleaved
                             else parser.parse_args(argv))
    except BaseException as exc:  # an unreachable parser is this case's fact
        return exc


def shape_of(stderr):
    accepts = re.search(r"^accepts: (.*)$", stderr, re.M)
    example = re.search(r"^example: (.*)$", stderr, re.M)
    return (accepts.group(1) if accepts else None, example.group(1) if example else None)


def example_argv(example):
    """The argv an `example:` line names, after its `python3 .../<tool>.py` prefix."""
    tokens = shlex.split(example)
    for i, token in enumerate(tokens):
        if token.endswith(".py"):
            return tokens[i + 1:]
    return tokens


def refusal_cases(cases, label, proc, parse):
    accepts, example = shape_of(proc.stderr)
    cases.append((f"{label}: a bad call exits non-zero and states accepts: and example:",
                  proc.returncode != 0 and bool(accepts) and bool(example),
                  (proc.stdout + proc.stderr)[-500:]))
    parsed, failure = None, ""
    if isinstance(parse, BaseException):
        failure = "parser unreachable: %r" % (parse,)
    elif not example:
        failure = "no example line"
    else:
        try:
            parsed = parse(example_argv(example))
        except BaseException as exc:  # a parse failure IS the case's fact
            failure = "example did not parse: %r" % (exc,)
    cases.append((f"{label}: the printed example parses under the real parser",
                  parsed is not None and not failure, failure))


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="cli_refusal_")

    # index_commit: a missing --name refuses, and its example is a real stage call.
    refusal_cases(cases, "index_commit", call("index_commit.py", "stage"),
                  parser_for("index_commit"))

    # autolearn_outcomes: a bad enum refuses without touching the ledger; the example parses.
    ledger = os.path.join(tmp, "autolearn_outcomes.jsonl")
    refusal_cases(cases, "autolearn_outcomes",
                  call("autolearn_outcomes.py", "record", "--outcome", "bogus", "--file", ledger),
                  parser_for("autolearn_outcomes"))

    # baseline_sync: a bad verdict refuses; the example parses in either flag order.
    refusal_cases(cases, "baseline_sync",
                  call("baseline_sync.py", "judge", ".claude/tools/reap.py", "--verdict", "nope"),
                  parser_for("baseline_sync", interleaved=True))
    interleaved = parser_for("baseline_sync", interleaved=True)
    try:
        legacy = (None if isinstance(interleaved, BaseException) else
                  interleaved(["--verdict", "push", "judge", ".claude/tools/reap.py"]))
    except BaseException as exc:
        legacy = exc
    cases.append(("baseline_sync: the legacy judge flag order (flags before the op) still parses",
                  getattr(legacy, "op", None) == "judge" and getattr(legacy, "verdict", None) == "push",
                  repr(legacy)))
    helped = call("baseline_sync.py", "judge", "--help")
    scoped = helped.stdout + helped.stderr
    cases.append(("baseline_sync: judge --help is operation-scoped (omits other ops' flags)",
                  helped.returncode == 0 and "--verdict" in scoped and "accepts:" in scoped
                  and "example:" in scoped
                  and "--merge" not in scoped and "--template" not in scoped
                  and "--status" not in scoped, scoped[:600]))
    # The scoped help is a hand-kept list: every flag it names must be one the real parser registers.
    with open(os.path.join(TOOLS, "baseline_sync.py"), encoding="utf-8") as fh:
        source = fh.read()
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", scoped)) - {"--help"}
    unregistered = sorted(flag for flag in named if 'add_argument("%s"' % flag not in source)
    cases.append(("baseline_sync: every flag judge --help names has an add_argument call in baseline_sync.py",
                  bool(named) and not unregistered, repr(unregistered)))

    # reap: a malformed call refuses with its shape; a refusal on an observed fact (a pid already gone) states only
    # its verdict. The help states each reason's evidence example.
    refusal_cases(cases, "reap", call("reap.py", "--no-such-flag"), parser_for("reap"))
    posted = "".join([call("reap.py", "--help").stdout, call("reap.py", "--help").stderr])
    cases.append(("reap: --help pairs each reason with its evidence flag",
                  all(re.search(pattern, posted) for pattern in
                      (r"duplicate\s+--twin", r"stalled\s+--log",
                       r"superseded\s+--artifact")),
                  posted[-400:]))

    # orchestration_metrics: a short --rate refuses; a flat mandates names the object shape.
    refusal_cases(cases, "orchestration_metrics",
                  call("orchestration_metrics.py", "--rate", "only-one-argument"),
                  parser_for("orchestration_metrics"))
    try:
        module = load("orchestration_metrics")
        spec, refusal = module._rating_spec({"label": "review:x", "outcome": "clean",
                                             "mandates": '{"x": "findings"}'})
        names_shape = (spec is None
                       and "{mandate: {outcome: findings|clean|not-reached, unique, duplicate}}" in refusal
                       and "x" in refusal)
    except BaseException as exc:
        refusal, names_shape = repr(exc), False
    cases.append(("orchestration_metrics: --mandates refusal names the object shape it accepts",
                  names_shape, repr(refusal)))
    # The root parser serves every mode, so a usage error outside --rate states the mode list, never
    # --rate's own flags.
    modes = call("orchestration_metrics.py", "--pricier-pins", "not-a-number")
    modes_accepts, modes_example = shape_of(modes.stderr)
    parsed_modes = None
    if modes_example:
        try:
            parsed_modes = parser_for("orchestration_metrics")(example_argv(modes_example))
        except BaseException as exc:  # a parse failure IS the case's fact
            parsed_modes = exc
    cases.append(("orchestration_metrics: a non-rate usage error names the modes, not another mode's flags",
                  modes.returncode != 0 and bool(modes_accepts) and "--rollup" in modes_accepts
                  and "--mandates" not in modes_accepts and parsed_modes is not None
                  and not isinstance(parsed_modes, BaseException), modes.stderr[-500:]))

    failures = [label for label, ok, _ in cases if not ok]
    for label, ok, detail in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok and detail:
            print("      " + str(detail).replace("\n", "\n      "))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
