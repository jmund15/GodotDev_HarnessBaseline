#!/usr/bin/env python3
"""Re-runnable proof for bootstrap.sh (baseline-repo root), S8: v2 init + compose.

    python3 tests/test_bootstrap.py

Requires a POSIX `bash` on PATH (Git Bash on Windows) and a real `git` remote reachable for the
`init` origin check -- this repo's own `origin` is used, since `--baseline-dir` must match it.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap.sh"

if not BOOTSTRAP.exists():
    print(f"CANNOT-RUN: {BOOTSTRAP} is missing", file=sys.stderr)
    sys.exit(2)

BASH = shutil.which("bash")
if BASH is None:
    print("CANNOT-RUN: no bash on PATH", file=sys.stderr)
    sys.exit(2)


def _origin() -> str:
    result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        print("CANNOT-RUN: this checkout has no origin remote to pin --baseline-dir against",
              file=sys.stderr)
        sys.exit(2)
    return result.stdout.strip()


def _snapshot_checkout(temp_root: Path) -> tuple[Path, str]:
    """A detached worktree at a commit of the files on disk. init reads --baseline-dir's HEAD, and
    publish validates its materialized tree before committing it, so bootstrapping ROOT there would
    test the old baseline instead of this tree. No ref or index of ROOT moves."""
    identity = {"GIT_AUTHOR_NAME": "bootstrap proof", "GIT_AUTHOR_EMAIL": "proof@example.invalid",
                "GIT_COMMITTER_NAME": "bootstrap proof", "GIT_COMMITTER_EMAIL": "proof@example.invalid"}
    snapshot_env = dict(os.environ, GIT_INDEX_FILE=str(temp_root / "snapshot.index"), **identity)

    def git(*args: str, env=None) -> str:
        result = subprocess.run(["git", *args], cwd=ROOT, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
        return result.stdout.strip()

    git("read-tree", "HEAD", env=snapshot_env)
    git("add", "-A", env=snapshot_env)
    tree = git("write-tree", env=snapshot_env)
    snapshot = git("commit-tree", tree, "-p", "HEAD", "-m", "bootstrap proof snapshot", env=snapshot_env)
    baseline = temp_root / "baseline"
    git("worktree", "add", "--detach", str(baseline), snapshot)
    return baseline, snapshot


def _rmtree_writable(path: Path) -> None:
    def onerror(func, p, exc_info):
        try:
            Path(p).chmod(stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


def _to_posix(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def test_bootstrap_into_temp_yields_a_v2_lock_that_checks_clean() -> None:
    origin = _origin()
    temp_root = Path(tempfile.mkdtemp(prefix="bootstrap_tmp_"))
    target = temp_root / "proj"
    baseline = None
    try:
        baseline, snapshot = _snapshot_checkout(temp_root)
        result = subprocess.run(
            [BASH, str(baseline / "bootstrap.sh"), "--target", _to_posix(target), "--project-name", "TestGame",
             "--repo", origin, "--ref", snapshot],
            cwd=baseline, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

        lock_path = target / ".claude" / "baseline.lock.json"
        assert lock_path.is_file(), "no baseline.lock.json written"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        assert lock.get("schema") == 2, lock.get("schema")

        settings_row = lock["files"].get(".claude/settings.json")
        assert settings_row is not None, "no composed .claude/settings.json row"
        assert settings_row["status"] == "composed", settings_row
        assert settings_row["inputs"] == [".claude/settings.base.json", ".claude/settings.project.json"]

        assert (target / ".claude" / "settings.json").is_file(), "compose did not write settings.json"

        check = subprocess.run(
            [sys.executable, str(target / ".claude" / "tools" / "baseline_sync.py"), "check", "--strict",
             "--baseline-dir", str(baseline)],
            cwd=target, capture_output=True, text=True, timeout=60,
        )
        assert check.returncode == 0, check.stdout + check.stderr
    finally:
        removed = baseline is None or subprocess.run(
            ["git", "worktree", "remove", "--force", str(baseline)], cwd=ROOT, capture_output=True
        ).returncode == 0
        _rmtree_writable(temp_root)
        if not removed:
            subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)


PREFIXES = ("pure", "pure,coding", "pure,coding,godot")


def _bootstrap(baseline: Path, snapshot: str, origin: str, target: Path, layers: str) -> None:
    result = subprocess.run(
        [BASH, str(baseline / "bootstrap.sh"), "--target", _to_posix(target), "--project-name", "TestGame",
         "--repo", origin, "--ref", snapshot, "--layers", layers],
        cwd=baseline, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, layers + ": " + result.stdout + result.stderr


def test_each_layer_prefix_installs_only_its_layers_and_imports_their_doctrine() -> None:
    """A consumer adopting a prefix gets exactly that prefix's files, and its CLAUDE.md imports the
    core plus each adopted layer's doctrine file, in layer order."""
    origin = _origin()
    temp_root = Path(tempfile.mkdtemp(prefix="bootstrap_layers_"))
    baseline = None
    try:
        baseline, snapshot = _snapshot_checkout(temp_root)
        manifest = json.loads((baseline / "baseline.manifest.json").read_text(encoding="utf-8"))["files"]
        for layers in PREFIXES:
            adopted = set(layers.split(","))
            target = temp_root / layers.replace(",", "_")
            _bootstrap(baseline, snapshot, origin, target, layers)
            present = {e["path"] for e in manifest if (target / e["path"]).exists()}
            foreign = sorted(e["path"] for e in manifest if e["layer"] not in adopted and e["path"] in present)
            assert not foreign, f"{layers}: files of unadopted layers installed: {foreign[:5]}"
            missing = sorted(e["path"] for e in manifest if e["layer"] in adopted and e["path"] not in present)
            assert not missing, f"{layers}: adopted files missing: {missing[:5]}"
            claude = (target / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            imports = [line.strip() for line in claude.splitlines() if line.startswith("@")]
            expected = ["@CLAUDE.core.md"] + [f"@CLAUDE.{layer}.md" for layer in ("coding", "godot")
                                              if layer in adopted and (baseline / "template" / ".claude" / f"CLAUDE.{layer}.md").exists()]
            assert imports == expected, f"{layers}: imports {imports} != {expected}"
            registry = baseline / "template" / ".claude" / "skills" / "project_subsystems"
            domains = [d["name"] for d in json.loads((registry / "adaptation.json").read_text(encoding="utf-8"))["memory_domains"]]
            for layer in ("coding", "godot"):
                starter = registry / f"adaptation.{layer}.json"
                if layer in adopted and starter.exists():
                    domains += [d["name"] for d in json.loads(starter.read_text(encoding="utf-8"))["memory_domains"]]
            installed = json.loads((target / ".claude" / "skills" / "project_subsystems" / "adaptation.json")
                                   .read_text(encoding="utf-8"))["memory_domains"]
            assert [d["name"] for d in installed] == domains, f"{layers}: domains {[d['name'] for d in installed]}"
    finally:
        removed = baseline is None or subprocess.run(
            ["git", "worktree", "remove", "--force", str(baseline)], cwd=ROOT, capture_output=True
        ).returncode == 0
        _rmtree_writable(temp_root)
        if not removed:
            subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)


def main() -> int:
    cases = [test_bootstrap_into_temp_yields_a_v2_lock_that_checks_clean,
             test_each_layer_prefix_installs_only_its_layers_and_imports_their_doctrine]
    failures = []
    for case in cases:
        try:
            case()
            print("ok " + case.__name__)
        except Exception as exc:  # a TimeoutExpired is a named failure, not a traceback
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
