"""Proof that a `test_*.py` collecting no test fails its run (conftest.py), and that script-style proofs have one
home: a `proof_*.py` here runs under `test_script_proofs.py`, so a consumer's script needs no runner of its own."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import test_script_proofs

HERE = Path(__file__).resolve().parent


def run_pytest(tmp_path, **files):
    shutil.copy(HERE / "conftest.py", tmp_path / "conftest.py")
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                           "--rootdir", str(tmp_path), str(tmp_path)],
                          capture_output=True, text=True, cwd=str(tmp_path), timeout=120)
    return done.returncode, done.stdout + done.stderr


def test_a_test_file_with_no_test_fails_the_run_and_names_the_file(tmp_path):
    code, out = run_pytest(tmp_path, **{"test_script_style.py": "print('proof body')\n"})
    assert code != 0
    assert "test_script_style.py" in out


def test_a_file_with_a_test_and_a_proof_file_pass(tmp_path):
    code, out = run_pytest(tmp_path, **{"test_real.py": "def test_x():\n    assert True\n",
                                        "proof_script.py": "raise SystemExit('never collected')\n"})
    assert code == 0, out


def test_a_proof_py_runs_as_a_script_case(tmp_path):
    ok = tmp_path / "proof_ok.py"
    ok.write_text("print('ok')\n", encoding="utf-8")
    test_script_proofs.run_proof(ok)
    bad = tmp_path / "proof_bad.py"
    bad.write_text("import sys\nsys.exit('planted')\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="planted"):
        test_script_proofs.run_proof(bad)
