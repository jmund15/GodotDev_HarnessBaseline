"""Proof that a `test_*.py` pytest collects and that holds no test fails its run, naming the file (conftest.py), while a
pytest file and a script-style proof beside it both pass. The empty file here is a script with no `__main__` block whose
text holds a `def test_` line, so the runner's classifier reads it as a pytest file: without the check pytest passes it
silently and its body never runs."""
from test_script_proofs import plant, run_pytest

EMPTY = 'EXAMPLE = """\ndef test_example():\n    pass\n"""\nprint("script body ran")\n'
EMPTY_NATIVE = 'EXAMPLE = """\ndef test_example():\n    pass\n"""\n'
REAL = "def test_real():\n    pass\n"
SCRIPT = "if __name__ == '__main__':\n    print('script proof ran')\n"


def test_a_test_file_with_no_test_fails_the_run_and_names_the_file(tmp_path):
    tests = plant(tmp_path, {"test_empty.py": EMPTY, "test_real.py": REAL})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code != 0, out
    assert "test_empty.py collects no test" in out, out  # a script body: one proof case, whose own pytest run fails it


def test_a_definitions_only_file_with_no_test_fails_collection_and_names_the_file(tmp_path):
    tests = plant(tmp_path / "native", {"test_empty_native.py": EMPTY_NATIVE, "test_real.py": REAL})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code != 0, out
    assert "test_empty_native.py collects no test" in out, out  # only definitions: collected in process


def test_a_pytest_file_and_a_script_proof_pass(tmp_path):
    tests = plant(tmp_path, {"test_real.py": REAL, "test_script.py": SCRIPT})
    code, out = run_pytest(tests, "-p", "no:xdist")
    assert code == 0, out
    assert "test_real.py::test_real PASSED" in out and "test_script.py::proof PASSED" in out, out
