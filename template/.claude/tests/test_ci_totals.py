"""tools/ci_totals.py counts the tree under its prefix (default `.claude/`) and nothing else."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_totals  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo,
                   check=True, capture_output=True)


def plant(tmp_path, monkeypatch, prefix):
    files = {prefix + "a.py": "x = 1\n", prefix + "tests/test_a.py": "def test_a(): pass\n", prefix + "b.md": "prose\n",
             "v2/note.md": "migration\n" * 50, "v2/mig.py": "y = 2\n", "tests/test_root.py": "def test_r(): pass\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "plant")
    monkeypatch.chdir(tmp_path)


def counts(got):
    return {k: v[0] for k, v in got.items()}


def test_a_consumer_repo_counts_its_dot_claude_by_default(tmp_path, monkeypatch):
    plant(tmp_path, monkeypatch, ".claude/")
    assert counts(ci_totals.totals("HEAD")) == {"code": 1, "test": 1, "prose": 1}


def test_the_shared_repo_passes_its_template_prefix_and_nothing_else_counts(tmp_path, monkeypatch):
    plant(tmp_path, monkeypatch, "template/.claude/")
    assert counts(ci_totals.totals("HEAD", "template/.claude/")) == {"code": 1, "test": 1, "prose": 1}
    assert counts(ci_totals.totals("HEAD")) == {"code": 0, "test": 0, "prose": 0}


def test_the_cli_takes_a_base_and_a_prefix(tmp_path, monkeypatch, capsys):
    plant(tmp_path, monkeypatch, "template/.claude/")
    ci_totals.main(["HEAD", "template/.claude/"])
    out = capsys.readouterr().out
    assert "change since" in out and "+0" in out
