#!/usr/bin/env python3
"""Minimal proof for tools/verify_doc_citations.py: a planted file:line past EOF exits 1, a real one exits 0.

    python3 .claude/tests/test_verify_doc_citations_contract.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
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


def case_real_fileline_is_found(directory):
    repo, doc = planted_repo(directory, "The helper lives at `.claude/tools/planted_helper.py:1`.\n")
    done = run(str(doc), "--repo", str(repo), "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["counts"] == {"found": 1, "missing": 0, "unparseable": 0}, done.stdout


def case_fileline_past_eof_is_missing_and_exits_nonzero(directory):
    repo, doc = planted_repo(directory, "The helper lives at `.claude/tools/planted_helper.py:999`.\n")
    done = run(str(doc), "--repo", str(repo), "--json")
    assert done.returncode == 1, "a citation past EOF must exit 1"
    assert json.loads(done.stdout)["counts"]["missing"] == 1, done.stdout


def case_unknown_kind_exits_two(directory):
    repo, doc = planted_repo(directory, "Nothing cited.\n")
    done = run(str(doc), "--repo", str(repo), "--kinds", "no-such-kind")
    assert done.returncode == 2, done.stdout + done.stderr


def main():
    cases = [case_real_fileline_is_found, case_fileline_past_eof_is_missing_and_exits_nonzero,
             case_unknown_kind_exits_two]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="verify-doc-citations-proof-")
        try:
            case(directory)
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
