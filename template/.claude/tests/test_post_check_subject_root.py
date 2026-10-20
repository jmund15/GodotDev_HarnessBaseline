"""`post_check.py claims --root` resolves a claim's file label in the subject checkout, not this repo.

A label names a file by a path tail (`lib.rs:1: quote`); the legacy adapter finds it in a tracked listing. That
listing must come from the `--root` checkout the verifier then reads, or a claim about any other checkout
is UNCHECKED `unresolved-label` however correct it is.
"""
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "post_check.py"


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def test_root_resolves_a_path_tail_label_in_the_subject_checkout(tmp_path):
    subject = tmp_path / "subject"
    (subject / "src").mkdir(parents=True)
    (subject / "src" / "lib.rs").write_text("pub fn generate_normal_map() {}\n", encoding="utf-8", newline="\n")
    git(subject, "init")
    git(subject, "add", "src/lib.rs")
    result = tmp_path / "lens.json"
    result.write_text(json.dumps({"claims": [
        {"claim": "the entry point exists", "evidence": "lib.rs:1: pub fn generate_normal_map()"}]}), encoding="utf-8")
    out = tmp_path / "verdicts.json"
    run = subprocess.run([sys.executable, str(TOOL), "claims", "--result", str(result), "--root", str(subject),
                          "--out", str(out)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert run.returncode in (0, 1), run.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["counts"] == {"VERIFIED": 1, "UNVERIFIED": 0, "UNCHECKED": 0}, report["verdicts"]
