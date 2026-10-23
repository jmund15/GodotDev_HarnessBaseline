#!/usr/bin/env python3
"""Re-runnable proof for tools/gen_manifest.py (baseline-repo root).

    python3 tests/test_gen_manifest.py
"""
from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN_MANIFEST = ROOT / "tools" / "gen_manifest.py"

if not GEN_MANIFEST.exists():
    print("CANNOT-RUN: %s is missing" % GEN_MANIFEST, file=sys.stderr)
    sys.exit(2)

# The root .gitignore ignores these, so skipping them leaves the mirror's template file list unchanged.
_MIRROR_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "logs", ".cache", "sessions", "worktrees",
                                      "plans", "scratch", ".search-index")
_mirror_root: Path | None = None


def _mirror() -> Path:
    """A private git copy of tools/, template/ and the manifest, for every case that plants a file or
    rewrites layer_entries.json. The publish runner runs all baseline checks at once on one checkout,
    so a plant in the shared tree reds whichever scan overlaps it."""
    global _mirror_root
    if _mirror_root is None:
        root = Path(tempfile.mkdtemp(prefix="gen_manifest_proof_"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        for name in ("tools", "template"):
            shutil.copytree(ROOT / name, root / name, ignore=_MIRROR_SKIP)
        for name in (".gitignore", "baseline.manifest.json"):
            shutil.copy2(ROOT / name, root / name)
        subprocess.run(["git", "init", "-q", str(root)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _mirror_root = root
    return _mirror_root


def _run_check(root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "gen_manifest.py"), "--check"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_check_passes_on_the_committed_tree() -> None:
    result = _run_check()
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_exits_1_on_unclassified_file_and_writes_nothing() -> None:
    root = _mirror()
    manifest = root / "baseline.manifest.json"
    planted = root / "template" / ".claude" / (
        "s3_proof_" + "planted" + "_marker.unclassified"
    )
    assert not planted.exists(), "planted-file fixture path already exists"
    before = manifest.read_bytes()
    try:
        planted.write_text("unclassified fixture content\n", encoding="utf-8", newline="\n")
        result = _run_check(root)
        assert result.returncode == 1, result.stdout + result.stderr
        assert planted.name in result.stderr.decode("utf-8", errors="replace"), result.stderr
        after = manifest.read_bytes()
        assert after == before, "gen_manifest.py --check must write nothing on failure"
    finally:
        if planted.exists():
            planted.unlink()


def test_planted_ignored_file_under_template_changes_no_check_result() -> None:
    # `.search-index/` is gitignored at the repo root, so git never lists a file in it.
    root = _mirror()
    manifest = root / "baseline.manifest.json"
    ignored_dir = root / "template" / ".claude" / ".search-index"
    assert not ignored_dir.exists(), "planted-file fixture directory already exists"
    before = manifest.read_bytes()
    try:
        ignored_dir.mkdir()
        (ignored_dir / ("s10_proof_" + "ignored_marker.unclassified")).write_text(
            "ignored fixture content\n", encoding="utf-8", newline="\n")
        result = _run_check(root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert manifest.read_bytes() == before, "gen_manifest.py --check must write nothing"
    finally:
        shutil.rmtree(ignored_dir, ignore_errors=True)


def test_classify_returns_none_for_an_unknown_path() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_manifest as gm
        assert gm.classify(".claude/does/not/match/any_pattern.xyz") is None
        assert gm.classify(".claude/tools/baseline_identity.py") == "pure"
    finally:
        sys.path.remove(str(ROOT / "tools"))
        sys.modules.pop("gen_manifest", None)


def _with_layer_entries(root: Path, entries: dict, action) -> None:
    """Run `action` with `entries` MERGED into `root`'s `tools/layer_entries.json`, then restore the file.

    Merged rather than replaced: a publication records every new row's layer in this file before it
    runs the battery, so replacing the file strands those rows unclassified and fails the case for a
    reason that has nothing to do with what it tests."""
    layer_entries = root / "tools" / "layer_entries.json"
    before = layer_entries.read_bytes() if layer_entries.exists() else None
    merged = dict(json.loads(before.decode("utf-8")).get("entries") or {}) if before else {}
    merged.update(entries)
    try:
        layer_entries.write_text(json.dumps({"version": 1, "entries": merged}, indent=2) + "\n",
                                 encoding="utf-8", newline="\n")
        action()
    finally:
        if before is None:
            layer_entries.unlink(missing_ok=True)
        else:
            layer_entries.write_bytes(before)


def test_exact_layer_entry_classifies_a_path_no_pattern_matches() -> None:
    # A publication adds template files no pattern list names; their layer arrives as data.
    root = _mirror()
    manifest = root / "baseline.manifest.json"
    rel = ".claude/" + "s9_proof_" + "layer_entry" + "_marker.unclassified"
    planted = root / "template" / rel
    assert not planted.exists(), "planted-file fixture path already exists"
    before = manifest.read_bytes()

    def action() -> None:
        planted.write_text("layer entry fixture content\n", encoding="utf-8", newline="\n")
        result = _run_check(root)
        err = result.stderr.decode("utf-8", errors="replace")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "stale" in err and rel not in err, err
        sys.path.insert(0, str(root / "tools"))
        try:
            sys.modules.pop("gen_manifest", None)
            import gen_manifest as gm
            assert gm.classify(rel) == "coding"
        finally:
            sys.path.remove(str(root / "tools"))
            sys.modules.pop("gen_manifest", None)
        assert manifest.read_bytes() == before, "gen_manifest.py --check must write nothing"

    try:
        _with_layer_entries(root, {rel: "coding"}, action)
    finally:
        if planted.exists():
            planted.unlink()


def test_invalid_layer_entry_exits_1_naming_the_path() -> None:
    root = _mirror()
    rel = ".claude/" + "s9_proof_" + "bad_layer" + ".md"

    def action() -> None:
        result = _run_check(root)
        err = result.stderr.decode("utf-8", errors="replace")
        assert result.returncode == 1, result.stdout + result.stderr
        assert rel in err and "universal" in err, err

    _with_layer_entries(root, {rel: "universal"}, action)


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


def test_adaptation_defaults_are_auto_at_their_layer_and_seeds_stay_seed() -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_manifest as gm
        base = ".claude/skills/project_subsystems/"
        for layer in ("pure", "coding", "godot"):
            rel = "%sadaptation.%s.defaults.json" % (base, layer)
            assert gm.sync_kind(rel) == "auto", (rel, gm.sync_kind(rel))
            assert gm.classify(rel) == layer, (rel, gm.classify(rel))
        for name in ("SKILL.md", "adaptation.json", "adaptation.coding.json", "adaptation.godot.json"):
            assert gm.sync_kind(base + name) == "seed", (name, gm.sync_kind(base + name))
    finally:
        sys.path.remove(str(ROOT / "tools"))
        sys.modules.pop("gen_manifest", None)


def main() -> int:
    cases = [
        test_check_passes_on_the_committed_tree,
        test_check_exits_1_on_unclassified_file_and_writes_nothing,
        test_planted_ignored_file_under_template_changes_no_check_result,
        test_classify_returns_none_for_an_unknown_path,
        test_exact_layer_entry_classifies_a_path_no_pattern_matches,
        test_invalid_layer_entry_exits_1_naming_the_path,
        test_sync_kind_offers_memory_and_seeds_its_index,
        test_adaptation_defaults_are_auto_at_their_layer_and_seeds_stay_seed,
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
