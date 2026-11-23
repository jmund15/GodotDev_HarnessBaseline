"""tools/ci_totals.py counts the shared tree, `template/.claude/`, and nothing else: `v2/` migration files and
repo-root files are not shared core."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_totals  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo,
                   check=True, capture_output=True)


def test_only_the_template_tree_is_counted(tmp_path, monkeypatch):
    files = {"template/.claude/a.py": "x = 1\n", "template/.claude/tests/test_a.py": "def test_a(): pass\n",
             "template/.claude/b.md": "prose\n", "v2/note.md": "migration\n" * 50, "v2/mig.py": "y = 2\n",
             "tests/test_root.py": "def test_r(): pass\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "plant")
    monkeypatch.chdir(tmp_path)
    got = ci_totals.totals("HEAD")
    assert {k: v[0] for k, v in got.items()} == {"code": 1, "test": 1, "prose": 1}
