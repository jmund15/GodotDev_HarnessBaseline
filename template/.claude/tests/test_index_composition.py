"""Proof for tools/index_composition.py: the report, the baseline drift flag, and `--set-baseline`.

Each case calls `main()` in-process on a planted search db under pytest's tmp_path; one case runs the script as a
subprocess, the CLI's entry point.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
sys.path.insert(0, TOOLS)
import index_composition as tool  # noqa: E402

MARKER = "no baseline set"
BASELINE = {"chunks": 100, "memory_share": 5.7, "date": "2026-08-16"}


def plant_db(root, memory, other):
    os.makedirs(os.path.join(root, ".search-index"), exist_ok=True)
    con = sqlite3.connect(os.path.join(root, ".search-index", "search.db"))
    con.execute("CREATE TABLE chunks (file_path TEXT)")
    rows = [(".claude/auto-memory/m%d.md" % i,) for i in range(memory)]
    for bucket, count in other.items():
        rows += [("%s/f%d.cs" % (bucket, i),) for i in range(count)]
    con.executemany("INSERT INTO chunks VALUES (?)", rows)
    con.commit()
    con.close()


def baseline_path(root):
    return os.path.join(root, ".claude", "index_composition_baseline.json")


def write_baseline(root, text):
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    with open(baseline_path(root), "w", encoding="utf-8") as handle:
        handle.write(text)


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """`run(*args)` runs main() against tmp_path; returns (exit code, stdout)."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    def call(*args):
        monkeypatch.setattr(sys, "argv", ["index_composition.py", *args])
        code = tool.main()
        return code, capsys.readouterr().out
    return call


def test_no_baseline_file_prints_marker_and_no_comparison(tmp_path, run):
    plant_db(tmp_path, 10, {"Scripts": 40, "Scenes": 50})
    code, out = run()
    assert code == 0 and MARKER in out
    assert "vs " not in out and "halved" not in out


def test_no_baseline_still_prints_the_absolute_top_bucket_flag(tmp_path, run):
    plant_db(tmp_path, 5, {"Scripts": 95})
    code, out = run()
    assert code == 0 and MARKER in out and "FLAG: Scripts is 95%" in out


def test_share_under_half_the_baseline_flags_displacement_and_prints_growth(tmp_path, run):
    plant_db(tmp_path, 2, {"Scripts": 49, "Scenes": 49})
    write_baseline(tmp_path, json.dumps(BASELINE))
    code, out = run()
    assert code == 0 and "memory share has more than halved" in out and MARKER not in out
    assert "vs 2026-08-16 baseline" in out and "(+0%)" in out


def test_share_over_half_the_baseline_raises_no_drift_flag(tmp_path, run):
    plant_db(tmp_path, 5, {"Scripts": 47, "Scenes": 48})
    write_baseline(tmp_path, json.dumps(BASELINE))
    code, out = run()
    assert code == 0 and "halved" not in out and MARKER not in out


def test_set_baseline_writes_exactly_three_keys_and_a_rerun_compares(tmp_path, run):
    plant_db(tmp_path, 6, {"Scripts": 44, "Scenes": 50})
    assert run("--set-baseline")[0] == 0
    with open(baseline_path(tmp_path), encoding="utf-8") as handle:
        written = json.load(handle)
    assert set(written) == {"chunks", "memory_share", "date"}
    assert written["chunks"] == 100 and written["memory_share"] == 6.0 and len(written["date"]) == 10
    code, out = run()
    assert code == 0 and "vs %s baseline: 100 chunks (+0%%)" % written["date"] in out and MARKER not in out


def test_set_baseline_with_no_db_exits_1_and_writes_nothing(tmp_path, run):
    assert run("--set-baseline")[0] == 1
    assert not os.path.exists(baseline_path(tmp_path))


def test_set_baseline_over_an_unreadable_db_exits_1_and_writes_nothing(tmp_path, run):
    os.makedirs(tmp_path / ".search-index")
    (tmp_path / ".search-index" / "search.db").write_bytes(b"not a database")
    assert run("--set-baseline")[0] == 1
    assert not os.path.exists(baseline_path(tmp_path))


@pytest.mark.parametrize("text", [
    "{not json", "[1, 2]", json.dumps({"chunks": 100}),
    json.dumps({"chunks": "x", "memory_share": None, "date": 5}),
    json.dumps({"chunks": 0, "memory_share": 5.7, "date": "2026-08-16"}),
], ids=["invalid-json", "non-object", "missing-keys", "wrong-types", "zero-chunks"])
def test_malformed_baseline_prints_the_marker(tmp_path, run, text):
    plant_db(tmp_path, 10, {"Scripts": 90})
    write_baseline(tmp_path, text)
    code, out = run()
    assert code == 0 and MARKER in out


def test_cli_entry_point_runs_as_a_script(tmp_path):
    plant_db(tmp_path, 10, {"Scripts": 40, "Scenes": 50})
    out = subprocess.run([sys.executable, os.path.join(TOOLS, "index_composition.py")], capture_output=True,
                         text=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path)), timeout=60, cwd=tmp_path)
    assert out.returncode == 0 and MARKER in out.stdout and "Traceback" not in out.stderr
