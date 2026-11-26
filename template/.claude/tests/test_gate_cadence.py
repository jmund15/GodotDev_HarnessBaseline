#!/usr/bin/env python3
"""Proof of tools/gate_cadence.py: a valid cadence table validates, and each malformed part is named.

    python3 -m pytest -q .claude/tests/test_gate_cadence.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import gate_cadence  # noqa: E402

GOOD = {"slice": ["run the edited unit's proof file"],
        "merge": ["npm run verify:merge"],
        "close": ["python3 .claude/scripts/harness_tests.py --close"],
        "wide": [{"runner": "(?:python3?\\s+)?\\S*harness_tests\\.py\\b", "exempt": ["^--help$"], "narrow": None},
                 {"runner": "npm run verify\\b", "exempt": [],
                  "narrow": {"requires": ["^--reach$", "\\.spec\\.ts$"]}},
                 {"runner": "runtests\\.ps1", "exempt": [],
                  "narrow": {"declared": {"arg": "Filter", "glob": "**/*.cs", "decl": ["class\\s+(\\w+)"],
                                          "admit": []}}}],
        "reach": [{"name": "harness", "paths": [".claude/**"], "cmd": "run {paths}"}],
        "reach_exempt": ["docs/**"], "reach_fallback": "npm run verify",
        "main": {"command": ["pwsh", "-File", "g.ps1"], "retry_exits": [7, 8], "outputs": ["a.json"]}}


def _wide(row):
    return dict(GOOD, wide=[row])


@pytest.mark.parametrize("value", [
    {"slice": [], "merge": [], "close": [], "wide": []},
    GOOD,
    _wide(dict(GOOD["wide"][0], hint="name one scenario")),
    {"main": {"command": ["x"]}},
], ids=["the empty default", "every key", "a wide-row hint", "a main gate with only a command"])
def test_a_valid_table_has_no_errors(value):
    assert gate_cadence.errors(value) == []


@pytest.mark.parametrize("value,needle", [
    (["slice"], "not an object"),
    (dict(GOOD, slices=[]), "slices"),
    (dict(GOOD, slice="run"), "slice"),
    (dict(GOOD, slice=[""]), "slice[0]"),
    (_wide(dict(GOOD["wide"][0], hint=5)), "wide[0].hint"),
    (dict(GOOD, wide="run"), "wide"),
    (dict(GOOD, wide=["run"]), "wide[0]"),
    (_wide(dict(GOOD["wide"][0], narrowish=None)), "wide[0]"),
    (_wide({"runner": "[", "exempt": [], "narrow": None}), "wide[0].runner"),
    (_wide({"runner": "x", "exempt": ["("], "narrow": None}), "wide[0].exempt[0]"),
    (_wide({"runner": "x", "exempt": [], "narrow": {}}), "wide[0].narrow"),
    (_wide({"runner": "x", "exempt": [], "narrow": {"requires": ["a"], "declared": {}}}), "wide[0].narrow"),
    (_wide({"runner": "x", "exempt": [], "narrow": {"requires": ["["]}}), "wide[0].narrow.requires[0]"),
    (_wide({"runner": "x", "exempt": [], "narrow": {"declared": {
        "arg": "Filter", "glob": "**/*.cs", "decl": ["class \\w+"], "admit": []}}}), "wide[0].narrow.declared.decl[0]"),
    (_wide({"runner": "x", "exempt": [], "narrow": {"declared": {
        "arg": "Filter", "decl": ["class (\\w+)"], "admit": []}}}), "wide[0].narrow.declared.glob"),
    ({"reach": [{"name": "a", "paths": ["x/**"]}]}, "reach[0].cmd"),
    ({"reach": [{"name": "a", "paths": [], "cmd": "c"}]}, "reach[0].paths"),
    ({"reach": [], "reach_exempt": "docs/**"}, "reach_exempt"),
    ({"reach": [], "reach_fallback": 3}, "reach_fallback"),
    ({"main": "x"}, "main"),
    ({"main": {}}, "main.command"),
    ({"main": {"command": "run it"}}, "main.command"),
    ({"main": {"command": ["x", ""]}}, "main.command"),
    ({"main": {"command": ["x"], "retry_exits": ["7"]}}, "main.retry_exits"),
    ({"main": {"command": ["x"], "retry_exits": [True]}}, "main.retry_exits"),
    ({"main": {"command": ["x"], "outputs": [1]}}, "main.outputs"),
    ({"main": {"command": ["x"], "retries": 1}}, "main"),
])
def test_a_malformed_part_is_named(value, needle):
    assert any(needle in error for error in gate_cadence.errors(value)), gate_cadence.errors(value)
