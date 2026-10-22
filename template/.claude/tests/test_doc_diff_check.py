"""Proof for doc_diff_check.py: a planted git repository holds one C# file. A comment-only edit passes (exit 0), an edit to
code is refused (exit 1), and an unknown base revision cannot run (exit 2). The script is copied into the planted tree,
because it resolves its repository from its own location. No network."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "doc_diff_check.py"
IDENT = ["-c", "user.name=proof", "-c", "user.email=proof@example.invalid"]


def git(repo, *args):
    r = subprocess.run(["git", *IDENT, *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (args, r.stderr[-300:])
    return r.stdout


def run(repo, *args):
    return subprocess.run([sys.executable, str(repo / ".claude" / "scripts" / "doc_diff_check.py"), *args],
                          cwd=str(repo), capture_output=True, text=True, encoding="utf-8")


def contract(temp_root):
    repo = Path(temp_root) / "repo"
    (repo / ".claude" / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, repo / ".claude" / "scripts" / "doc_diff_check.py")
    git(repo, "init", "-q")
    source = repo / "Planted.cs"
    source.write_text("// first note\nclass Planted { int Value() { return 1; } }\n", encoding="utf-8", newline="\n")
    git(repo, "add", "Planted.cs")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", "planted")

    source.write_text("// reworded note\nclass Planted { int Value() { return 1; } }\n", encoding="utf-8", newline="\n")
    comment_only = run(repo)
    assert comment_only.returncode == 0 and "comments only. OK." in comment_only.stdout, (comment_only.returncode, comment_only.stdout)

    source.write_text("// reworded note\nclass Planted { int Value() { return 2; } }\n", encoding="utf-8", newline="\n")
    code_changed = run(repo)
    assert code_changed.returncode == 1 and "PRODUCTION CODE CHANGED" in code_changed.stdout, (code_changed.returncode, code_changed.stdout)

    bad_rev = run(repo, "no-such-revision")
    assert bad_rev.returncode == 2 and "failed" in bad_rev.stderr, (bad_rev.returncode, bad_rev.stderr[-300:])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="doc_diff_check_proof_") as tmp:
        contract(tmp)
    print("ok doc_diff_check.py")
