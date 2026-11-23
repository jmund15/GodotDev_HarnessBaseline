#!/usr/bin/env python3
"""Minimal proof for tools/verify_doc_citations.py: a planted file:line past EOF exits 1, a real one exits 0.

    python -m pytest .claude/tests/test_verify_doc_citations_contract.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_doc_citations.py"

HELPER = "def planted_function():\n    return 1\n"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def planted_repo(directory, doc_text):
    repo = Path(directory) / "repo"
    (repo / ".claude" / "tools").mkdir(parents=True)
    (repo / ".claude" / "tools" / "planted_helper.py").write_text(HELPER, encoding="utf-8")
    doc = repo / "doc.md"
    doc.write_text(doc_text, encoding="utf-8")
    return repo, doc


def test_a_real_file_line_is_found(tmp_path):
    repo, doc = planted_repo(tmp_path, "The helper lives at `.claude/tools/planted_helper.py:1`.\n")
    done = run(str(doc), "--repo", str(repo), "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["counts"] == {"found": 1, "missing": 0, "unparseable": 0}, done.stdout


def test_a_file_line_past_eof_is_missing_and_exits_one(tmp_path):
    repo, doc = planted_repo(tmp_path, "The helper lives at `.claude/tools/planted_helper.py:999`.\n")
    done = run(str(doc), "--repo", str(repo), "--json")
    assert done.returncode == 1, "a citation past EOF must exit 1"
    assert json.loads(done.stdout)["counts"]["missing"] == 1, done.stdout


def test_an_unknown_kind_exits_two(tmp_path):
    repo, doc = planted_repo(tmp_path, "Nothing cited.\n")
    done = run(str(doc), "--repo", str(repo), "--kinds", "no-such-kind")
    assert done.returncode == 2, done.stdout + done.stderr
