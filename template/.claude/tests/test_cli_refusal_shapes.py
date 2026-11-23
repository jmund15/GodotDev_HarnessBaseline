"""Every measured CLI refusal states the shape it accepts and one valid call.

One wire format, owned by `tools/cli_usage.py`: a refusal exits non-zero and stderr carries an
`accepts:` line and an `example:` line. The printed example must parse under the tool's own parser,
checked parser-only — some tools' examples write a ledger.

    python -m pytest .claude/tests/test_cli_refusal_shapes.py -q
"""
import importlib.util
import os
import re
import shlex
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")

# (tool, a malformed argv). index_commit: a missing --name refuses, and its example is a real stage call.
# reap: a malformed call refuses with its shape; its observed-fact refusals are rows in floor/test_floor_reap.py.
REFUSALS = [("index_commit", ["stage"]), ("reap", ["--no-such-flag"])]


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


def parser_for(name):
    """The tool's real parser, or the exception that kept it out of reach."""
    try:
        return load(name).build_parser().parse_args
    except BaseException as exc:  # an unreachable parser is the case's fact
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


@pytest.mark.parametrize("tool, args", REFUSALS, ids=[t for t, _ in REFUSALS])
def test_a_bad_call_exits_nonzero_and_states_accepts_and_example(tool, args):
    proc = call(tool + ".py", *args)
    accepts, example = shape_of(proc.stderr)
    assert proc.returncode != 0 and accepts and example, (proc.stdout + proc.stderr)[-500:]


@pytest.mark.parametrize("tool, args", REFUSALS, ids=[t for t, _ in REFUSALS])
def test_the_printed_example_parses_under_the_real_parser(tool, args):
    _, example = shape_of(call(tool + ".py", *args).stderr)
    assert example, "no example line"
    parse = parser_for(tool)
    assert not isinstance(parse, BaseException), "parser unreachable: %r" % (parse,)
    parse(example_argv(example))
