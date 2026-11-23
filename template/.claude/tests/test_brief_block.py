"""Proof for tools/brief_block.py: the brief's one `task` fence, read in process.

Decision cases: no fence is None; one object is returned with a one-string check listed; each defect the
guard and MaddManager's round read for (two fences, not an object, empty or non-string checks, a bad writeSet)
raises ValueError.

    python -m pytest .claude/tests/test_brief_block.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from brief_block import task_block  # noqa: E402


def fence(obj):
    return "# brief\n\n```task\n" + (obj if isinstance(obj, str) else json.dumps(obj)) + "\n```\n"


def test_no_fence_is_none():
    assert task_block("# brief with no task fence\n") is None


def test_one_object_is_returned_and_a_string_check_becomes_a_list():
    block = task_block(fence({"check": "pytest -q", "writeSet": ["out/**"]}))
    assert block == {"check": ["pytest -q"], "writeSet": ["out/**"]}


def test_a_missing_write_set_is_absent_not_an_error():
    assert task_block(fence({"check": ["true"]})) == {"check": ["true"]}


@pytest.mark.parametrize("text, reason", [
    (fence({"check": ["a"]}) + fence({"check": ["b"]}), "two blocks"),
    (fence("[1, 2]"), "a list, not an object"),
    (fence("{not json"), "unparseable"),
    (fence({"check": []}), "empty check list"),
    (fence({"check": ["  "]}), "blank check entry"),
    (fence({"check": [3]}), "non-string check entry"),
    (fence({"check": ["a"], "writeSet": "out/"}), "writeSet not a list"),
    (fence({"check": ["a"], "writeSet": [1]}), "writeSet entry not a string"),
])
def test_each_defect_raises(text, reason):
    with pytest.raises(ValueError):
        task_block(text)
