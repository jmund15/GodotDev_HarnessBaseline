#!/usr/bin/env python3
"""Re-runnable proof for tools/gen_manifest.py (baseline-repo root).

    python3 tests/test_gen_manifest.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN_MANIFEST = ROOT / "tools" / "gen_manifest.py"
MANIFEST = ROOT / "baseline.manifest.json"

if not GEN_MANIFEST.exists():
    print("CANNOT-RUN: %s is missing" % GEN_MANIFEST, file=sys.stderr)
    sys.exit(2)


def _run_check() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GEN_MANIFEST), "--check"], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_check_passes_on_the_committed_tree() -> None:
    result = _run_check()
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_exits_1_on_unclassified_file_and_writes_nothing() -> None:
    planted = ROOT / "template" / ".claude" / (
        "s3_proof_" + "planted" + "_marker.unclassified"
    )
    assert not planted.exists(), "planted-file fixture path already exists"
    before = MANIFEST.read_bytes()
    try:
        planted.write_text("unclassified fixture content\n", encoding="utf-8", newline="\n")
        result = _run_check()
        assert result.returncode == 1, result.stdout + result.stderr
        assert planted.name in result.stderr.decode("utf-8", errors="replace"), result.stderr
        after = MANIFEST.read_bytes()
        assert after == before, "gen_manifest.py --check must write nothing on failure"
    finally:
        if planted.exists():
            planted.unlink()


def test_classify_returns_none_for_an_unknown_path() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_manifest as gm
        assert gm.classify(".claude/does/not/match/any_pattern.xyz") is None
        assert gm.classify(".claude/tools/baseline_identity.py") == "pure"
    finally:
        sys.path.remove(str(ROOT / "tools"))
        sys.modules.pop("gen_manifest", None)


def main() -> int:
    cases = [
        test_check_passes_on_the_committed_tree,
        test_check_exits_1_on_unclassified_file_and_writes_nothing,
        test_classify_returns_none_for_an_unknown_path,
    ]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
