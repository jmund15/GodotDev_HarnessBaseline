#!/usr/bin/env python3
"""Minimal proof for tools/subsys.py: a planted detail catalog, one unknown id refused, one known id returned.

    python -m pytest .claude/tests/test_subsys.py -q

The tool reads its catalog beside itself, so the proof copies the tool into a temp root and plants the catalog there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "subsys.py"

CATALOG = """# Subsystem details

### `alpha`
The first sentence of the planted entry. The rest of the body.
Planted body line.

### `beta` / `gamma`
Second entry, one body line.
"""


def run(root, *args):
    return subprocess.run([sys.executable, str(root / ".claude" / "tools" / "subsys.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


@pytest.fixture
def root(tmp_path):
    """A temp root holding a copy of the tool and a planted catalog."""
    (tmp_path / ".claude" / "tools").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "project_subsystems" / "reference").mkdir(parents=True)
    shutil.copyfile(TOOL, tmp_path / ".claude" / "tools" / "subsys.py")
    shutil.copyfile(TOOL.with_name("cli_usage.py"), tmp_path / ".claude" / "tools" / "cli_usage.py")
    (tmp_path / ".claude" / "skills" / "project_subsystems" / "reference" / "subsystem_details.md").write_text(
        CATALOG, encoding="utf-8")
    return tmp_path


def test_index_lists_the_planted_entries(root):
    done = run(root, "index")
    assert done.returncode == 0, done.stderr
    assert "alpha — The first sentence of the planted entry" in done.stdout, done.stdout
    assert "beta / gamma — Second entry, one body line" in done.stdout, done.stdout


def test_an_unknown_id_exits_one(root):
    done = run(root, "get", "nosuch")
    assert done.returncode == 1, "an unknown id must exit 1"
    assert "no such entry: nosuch" in done.stderr, done.stderr


def test_a_known_id_prints_its_body(root):
    done = run(root, "get", "gamma")
    assert done.returncode == 0, done.stderr
    assert "### `beta` / `gamma`" in done.stdout and "Second entry, one body line." in done.stdout, done.stdout
