#!/usr/bin/env python3
"""Minimal proof for tools/subsys.py: a planted detail catalog, one unknown id refused, one known id returned.

    python3 .claude/tests/test_subsys.py

The tool reads its catalog beside itself, so the proof copies the tool into a temp root and plants the catalog there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


def planted_root():
    root = Path(tempfile.mkdtemp(prefix="subsys-proof-"))
    (root / ".claude" / "tools").mkdir(parents=True)
    (root / ".claude" / "skills" / "project_subsystems" / "reference").mkdir(parents=True)
    shutil.copyfile(TOOL, root / ".claude" / "tools" / "subsys.py")
    (root / ".claude" / "skills" / "project_subsystems" / "reference" / "subsystem_details.md").write_text(
        CATALOG, encoding="utf-8")
    return root


def case_index_lists_planted_entries(root):
    done = run(root, "index")
    assert done.returncode == 0, done.stderr
    assert "alpha — The first sentence of the planted entry" in done.stdout, done.stdout
    assert "beta / gamma — Second entry, one body line" in done.stdout, done.stdout


def case_unknown_id_exits_nonzero(root):
    done = run(root, "get", "nosuch")
    assert done.returncode == 1, "an unknown id must exit 1"
    assert "no such entry: nosuch" in done.stderr, done.stderr


def case_known_id_prints_its_body(root):
    done = run(root, "get", "gamma")
    assert done.returncode == 0, done.stderr
    assert "### `beta` / `gamma`" in done.stdout and "Second entry, one body line." in done.stdout, done.stdout


def main():
    cases = [case_index_lists_planted_entries, case_unknown_id_exits_nonzero, case_known_id_prints_its_body]
    failures = []
    for case in cases:
        root = planted_root()
        try:
            case(root)
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
        finally:
            shutil.rmtree(root, ignore_errors=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
