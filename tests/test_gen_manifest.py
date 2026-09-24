#!/usr/bin/env python3
"""Re-runnable proof for tools/gen_manifest.py (baseline-repo root).

    python3 tests/test_gen_manifest.py
"""
from __future__ import annotations

import json
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


LAYER_ENTRIES = ROOT / "tools" / "layer_entries.json"


def _with_layer_entries(entries: dict, action) -> None:
    """Run `action` with `entries` MERGED into `tools/layer_entries.json`, then restore the file.

    Merged rather than replaced: a publication records every new row's layer in this file before it
    runs the battery, so replacing the file strands those rows unclassified and fails the case for a
    reason that has nothing to do with what it tests."""
    before = LAYER_ENTRIES.read_bytes() if LAYER_ENTRIES.exists() else None
    merged = dict(json.loads(before.decode("utf-8")).get("entries") or {}) if before else {}
    merged.update(entries)
    try:
        LAYER_ENTRIES.write_text(json.dumps({"version": 1, "entries": merged}, indent=2) + "\n",
                                 encoding="utf-8", newline="\n")
        action()
    finally:
        if before is None:
            LAYER_ENTRIES.unlink(missing_ok=True)
        else:
            LAYER_ENTRIES.write_bytes(before)


def test_exact_layer_entry_classifies_a_path_no_pattern_matches() -> None:
    # A publication adds template files no pattern list names; their layer arrives as data.
    rel = ".claude/" + "s9_proof_" + "layer_entry" + "_marker.unclassified"
    planted = ROOT / "template" / rel
    assert not planted.exists(), "planted-file fixture path already exists"
    before = MANIFEST.read_bytes()

    def action() -> None:
        planted.write_text("layer entry fixture content\n", encoding="utf-8", newline="\n")
        result = _run_check()
        err = result.stderr.decode("utf-8", errors="replace")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "stale" in err and rel not in err, err
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            sys.modules.pop("gen_manifest", None)
            import gen_manifest as gm
            assert gm.classify(rel) == "coding"
        finally:
            sys.path.remove(str(ROOT / "tools"))
            sys.modules.pop("gen_manifest", None)
        assert MANIFEST.read_bytes() == before, "gen_manifest.py --check must write nothing"

    try:
        _with_layer_entries({rel: "coding"}, action)
    finally:
        if planted.exists():
            planted.unlink()


def test_invalid_layer_entry_exits_1_naming_the_path() -> None:
    rel = ".claude/" + "s9_proof_" + "bad_layer" + ".md"

    def action() -> None:
        result = _run_check()
        err = result.stderr.decode("utf-8", errors="replace")
        assert result.returncode == 1, result.stdout + result.stderr
        assert rel in err and "universal" in err, err

    _with_layer_entries({rel: "universal"}, action)


def test_sync_kind_offers_memory_and_seeds_its_index() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_manifest as gm
        assert gm.sync_kind(".claude/auto-memory/x.md") == "offer"
        assert gm.sync_kind(".claude/auto-memory/archive/y.md") == "offer"
        assert gm.sync_kind(".claude/auto-memory/MEMORY.md") == "seed"
        assert gm.sync_kind(".claude/CLAUDE.md") == "seed"
        assert gm.sync_kind(".claude/tools/baseline_identity.py") == "auto"
        original = list(gm.SEED_PATTERNS)
        gm.SEED_PATTERNS.append(".claude/auto-memory/seeded_*.md")
        try:
            assert gm.sync_kind(".claude/auto-memory/seeded_one.md") == "seed"
        finally:
            gm.SEED_PATTERNS[:] = original
    finally:
        sys.path.remove(str(ROOT / "tools"))
        sys.modules.pop("gen_manifest", None)


def main() -> int:
    cases = [
        test_check_passes_on_the_committed_tree,
        test_check_exits_1_on_unclassified_file_and_writes_nothing,
        test_classify_returns_none_for_an_unknown_path,
        test_exact_layer_entry_classifies_a_path_no_pattern_matches,
        test_invalid_layer_entry_exits_1_naming_the_path,
        test_sync_kind_offers_memory_and_seeds_its_index,
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
