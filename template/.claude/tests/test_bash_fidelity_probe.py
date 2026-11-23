#!/usr/bin/env python3
r"""Proof for tools/bash_fidelity_probe.py: each argv shape and its exit code.

The probe takes two literals as issued through the Bash tool: `x\\y` and `x\\"y`. It records
`<client-version>|<sys.platform>` into the fidelity record with one bool per backslash-run class:
`collapses` (argv 1 arrived as `x\y`) and `collapses_before_quote` (argv 2 arrived as `x\"y`).
`--client-version` is mandatory (only the hook supplies it); every malformed shape exits 2.
Exit codes outside {0, 2} or a traceback are CRASH.

    python -m pytest .claude/tests/test_bash_fidelity_probe.py -q
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
PROBE = os.path.join(REPO, ".claude", "tools", "bash_fidelity_probe.py")
BS = "\\"
DQ = '"'
ONE = "x" + BS + "y"
TWO = "x" + BS + BS + "y"
QONE = "x" + BS + DQ + "y"
QTWO = "x" + BS + BS + DQ + "y"
VERSION = "9.9.9-probe"
KEY = "%s|%s" % (VERSION, sys.platform)
SEED = {"keep|me": {"collapses": False, "observed": "2026-01-01"}}


def run(args, env):
    """Spawned: the probe's argv is the subject. A missing script also exits 2 ("can't open file"); that is a CRASH."""
    p = subprocess.run([sys.executable, PROBE] + list(args), capture_output=True, text=True,
                       encoding="utf-8", timeout=60, env=env, cwd=REPO)
    crashed = p.returncode not in (0, 2) or "Traceback" in p.stderr or "can't open file" in p.stderr
    return p, crashed


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


@pytest.fixture
def record(tmp_path):
    """A seeded fidelity record: the probe must keep its other keys, and a rejected call must leave it unchanged."""
    path = tmp_path / "cache" / "record.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(SEED), encoding="utf-8")
    return str(path)


@pytest.fixture
def env(record):
    return dict(os.environ, PYTHONIOENCODING="utf-8", HARNESS_BASH_FIDELITY_RECORD=record)


def test_the_probe_file_exists():
    assert os.path.isfile(PROBE), PROBE


@pytest.mark.parametrize("first, second, collapses, before_quote", [
    (ONE, QTWO, True, False),
    (TWO, QTWO, False, False),
    (ONE, QONE, True, True),
    (TWO, QONE, False, True),
], ids=["single-collapses", "double-stays", "single-collapses-before-quote", "double-stays-before-quote"])
def test_an_argv_pair_exits_zero_and_records_its_backslash_classes(env, record, first, second, collapses, before_quote):
    p, crashed = run([first, second, "--client-version", VERSION], env)
    row = (load(record) or {}).get(KEY) or {}
    assert not crashed and p.returncode == 0, p.stdout + p.stderr
    assert (row.get("collapses") is collapses and row.get("collapses_before_quote") is before_quote
            and len(row.get("observed", "")) == 10), row


def test_the_probe_prints_the_record_it_wrote(env):
    p, _ = run([ONE, QTWO, "--client-version", VERSION], env)
    assert KEY in p.stdout and "collapses_before_quote" in p.stdout, p.stdout


def test_an_existing_key_in_the_record_is_preserved(env, record):
    p, crashed = run([ONE, QTWO, "--client-version", VERSION], env)
    assert not crashed and p.returncode == 0, p.stdout + p.stderr
    assert (load(record) or {}).get("keep|me", {}).get("observed") == "2026-01-01"


def test_the_write_leaves_no_temp_file_behind(env, record):
    run([ONE, QTWO, "--client-version", VERSION], env)
    leftovers = [n for n in os.listdir(os.path.dirname(record)) if n != os.path.basename(record) and not n.endswith(".lock")]
    assert not leftovers, leftovers


REJECTED = [
    ("leading --client-version", ["--client-version", VERSION, ONE, QTWO]),
    ("equals --client-version", [ONE, QTWO, "--client-version=" + VERSION]),
    ("no --client-version", [ONE, QTWO]),
    ("--client-version without a value", [ONE, QTWO, "--client-version"]),
    ("the retired one-argument form", [ONE, "--client-version", VERSION]),
    ("unexpected first argument xy", ["xy", QTWO, "--client-version", VERSION]),
    ("three backslashes", ["x" + BS * 3 + "y", QTWO, "--client-version", VERSION]),
    ("second argument without a quote", [ONE, ONE, "--client-version", VERSION]),
    ("three backslashes before the quote", [ONE, "x" + BS * 3 + DQ + "y", "--client-version", VERSION]),
    ("no argument", ["--client-version", VERSION]),
    ("three arguments", [ONE, QTWO, QTWO, "--client-version", VERSION]),
    ("unknown flag", [ONE, QTWO, "--client-version", VERSION, "--bogus"]),
    ("empty client version", [ONE, QTWO, "--client-version", ""]),
]


@pytest.mark.parametrize("args", [a for _, a in REJECTED], ids=[label for label, _ in REJECTED])
def test_a_rejected_invocation_exits_two_with_usage_and_writes_nothing(env, record, args):
    p, crashed = run(args, env)
    assert not crashed and p.returncode == 2 and "usage:" in p.stderr, "rc=%s %s" % (p.returncode, p.stderr)
    assert load(record) == SEED, load(record)


def test_help_exits_zero_and_prints_usage(env):
    p, crashed = run(["--help"], env)
    assert not crashed and p.returncode == 0 and "usage" in (p.stdout + p.stderr).lower(), p.stdout + p.stderr
