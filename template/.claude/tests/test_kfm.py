#!/usr/bin/env python3
"""Minimal proof for tools/kfm.py: a planted catalog, one stale snapshot refused, one current snapshot accepted.

    python3 .claude/tests/test_kfm.py

The tool reads its catalog beside itself, so the proof copies the tool and its cli_usage helper into a temp root and plants the catalog there.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "kfm.py"

CATALOG = """# Known failure modes

## Planted section

### 1. Planted entry
**Catches you when**: a planted trigger. The rest of the body.
Planted body line.
"""


def run(root, *args):
    return subprocess.run([sys.executable, str(root / ".claude" / "tools" / "kfm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def planted_root():
    root = Path(tempfile.mkdtemp(prefix="kfm-proof-"))
    (root / ".claude" / "tools").mkdir(parents=True)
    (root / ".claude" / "reference").mkdir(parents=True)
    shutil.copyfile(TOOL, root / ".claude" / "tools" / "kfm.py")
    shutil.copyfile(TOOL.parent / "cli_usage.py", root / ".claude" / "tools" / "cli_usage.py")
    (root / ".claude" / "reference" / "known_failure_modes_entries.md").write_text(CATALOG, encoding="utf-8")
    return root


def case_index_lists_planted_entry(root):
    done = run(root, "index")
    assert done.returncode == 0, done.stderr
    assert "#1 Planted entry — a planted trigger" in done.stdout, done.stdout


def case_get_without_ids_exits_nonzero(root):
    done = run(root, "get")
    assert done.returncode != 0, "get with no ids must fail"
    assert "get needs ids or --section" in done.stderr, done.stderr


def case_current_snapshot_passes_and_edited_catalog_is_refused(root):
    snap = root / ".claude" / "scratch" / "run" / "kfm"
    made = run(root, "materialize", "--out", str(snap))
    assert made.returncode == 0, made.stderr
    current = run(root, "check", "--dir", str(snap))
    assert current.returncode == 0 and "CURRENT" in current.stdout, current.stdout + current.stderr
    catalog = root / ".claude" / "reference" / "known_failure_modes_entries.md"
    catalog.write_text(CATALOG + "Edited after materialize.\n", encoding="utf-8")
    stale = run(root, "check", "--dir", str(snap))
    assert stale.returncode != 0, "a snapshot taken before a catalog edit must be refused"
    assert "stale" in stale.stderr, stale.stderr


def main():
    cases = [case_index_lists_planted_entry, case_get_without_ids_exits_nonzero,
             case_current_snapshot_passes_and_edited_catalog_is_refused]
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
