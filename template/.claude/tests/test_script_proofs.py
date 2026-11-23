"""The one runner for the proofs that are not pytest: each `*_test.js`, `test_*.mjs`, `test_*.ps1` and `proof_*.py` in
this folder runs under its interpreter (a script-style Python proof is named `proof_*.py`, never `test_*.py`: conftest.py
fails a `test_*.py` that collects no test), one case per file, so CI's single `pytest` run covers them (T6). A new proof file needs no
registration. A file that exits non-zero fails its case with its output; a machine without the interpreter skips.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RUNNERS = {".js": ["node"], ".mjs": ["node"], ".ps1": ["pwsh", "-NoProfile", "-File"], ".py": [sys.executable]}
PROOFS = sorted([*HERE.glob("*_test.js"), *HERE.glob("test_*.mjs"), *HERE.glob("test_*.ps1"), *HERE.glob("proof_*.py")])


def run_proof(path, timeout=300):
    command = RUNNERS[path.suffix]
    if shutil.which(command[0]) is None:
        pytest.skip(f"{command[0]} is not installed")
    done = subprocess.run([*command, str(path)], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    assert done.returncode == 0, f"{path.name} exited {done.returncode}\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"


@pytest.mark.parametrize("path", PROOFS, ids=[p.name for p in PROOFS])
def test_proof_passes(path):
    run_proof(path)


def test_the_glob_finds_every_proof_kind():
    assert {p.suffix for p in PROOFS} >= set(RUNNERS) - {".py"}


def test_a_failing_proof_fails_its_case(tmp_path):
    bad = tmp_path / "planted_test.js"
    bad.write_text("throw new Error('planted')\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="planted"):
        run_proof(bad)
