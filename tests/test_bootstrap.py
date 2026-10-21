#!/usr/bin/env python3
"""Re-runnable proof for bootstrap.sh (baseline-repo root), S8: v2 init + compose.

    python3 tests/test_bootstrap.py

Requires a POSIX `bash` on PATH (Git Bash on Windows) and a real `git` remote reachable for the
`init` origin check -- this repo's own `origin` is used, since `--baseline-dir` must match it.
"""
from __future__ import annotations

import json
import os
import re
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
        assert settings_row["inputs"] == [".claude/settings.base.json", ".claude/settings.coding.json",
                                          ".claude/settings.godot.json", ".claude/settings.project.json"]

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


HOOK_CRASH_MARKERS = ("Traceback (most recent call last)", "ModuleNotFoundError", "can't open file",
                      "No such file or directory")


def _hook_payload(event: str, target: Path) -> str:
    return json.dumps({
        "session_id": "bootstrap-smoke", "hook_event_name": event, "cwd": str(target),
        "transcript_path": str(target / "transcript.jsonl"), "prompt": "smoke",
        "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": ""},
        "source": "startup",
    })


def _hook_crashes(target: Path) -> list[str]:
    """Run every hook the composed settings.json registers once, with a minimal payload for its
    event; name each one that crashes (traceback, missing module or missing script)."""
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target), CLAUDE_CODE_SESSION_ID="bootstrap-smoke")
    crashes = []
    for event, groups in sorted(settings.get("hooks", {}).items()):
        for group in groups:
            for hook in group.get("hooks", []):
                command = hook.get("command")
                if hook.get("type") != "command" or not command:
                    continue
                try:
                    run = subprocess.run([BASH, "-c", command], cwd=target, env=env, input=_hook_payload(event, target),
                                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
                except subprocess.TimeoutExpired:
                    crashes.append(f"{event}: {command} (timeout)")
                    continue
                marker = next((m for m in HOOK_CRASH_MARKERS if m in run.stderr), None)
                if marker:
                    crashes.append(f"{event}: {command} ({marker}: {run.stderr.strip().splitlines()[-1][:160]})")
    return crashes


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
            for pure_file in (".claude/commands/workstation_setup.md",
                              ".claude/commands/reference/workstation_setup_land_runner.md",
                              ".claude/tools/workstation_profile.py"):
                assert (target / pure_file).is_file(), f"{layers}: {pure_file} not installed"
            toolchain = target / ".claude" / "commands" / "reference" / "workstation_setup_toolchain.md"
            assert toolchain.is_file() == ("godot" in adopted), f"{layers}: toolchain reference installed={toolchain.is_file()}"
            godot_profile = target / ".claude" / "tools" / "workstation_profile_godot.py"
            assert godot_profile.is_file() == ("godot" in adopted), f"{layers}: godot profile module installed={godot_profile.is_file()}"
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
            crashes = _hook_crashes(target)
            assert not crashes, f"{layers}: hooks crash in a fresh project: " + "; ".join(crashes[:6])
            composed = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
            assert composed.get("outputStyle") == "terse", f"{layers}: outputStyle {composed.get('outputStyle')}"
            assert (target / ".claude" / "output-styles" / "terse.md").is_file(), f"{layers}: terse.md not installed"
            stack_rows = [row for row in json.dumps(composed).split('"') if "csharp" in row or "mcp__godot__" in row]
            assert bool(stack_rows) == ("godot" in adopted), f"{layers}: stack rows {stack_rows[:3]}"
            registered = {h["command"] for groups in composed["hooks"].values() for g in groups for h in g["hooks"]}
            assert any("dispatch_table.py" in c for c in registered), f"{layers}: dispatch_table not registered"
    finally:
        removed = baseline is None or subprocess.run(
            ["git", "worktree", "remove", "--force", str(baseline)], cwd=ROOT, capture_output=True
        ).returncode == 0
        _rmtree_writable(temp_root)
        if not removed:
            subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)


class _Baseline:
    """A snapshot checkout plus a scratch dir, removed on exit."""

    def __enter__(self):
        self.origin = _origin()
        self.temp_root = Path(tempfile.mkdtemp(prefix="bootstrap_onb_"))
        self.baseline = None
        self.baseline, self.snapshot = _snapshot_checkout(self.temp_root)
        return self

    def __exit__(self, *exc):
        removed = self.baseline is None or subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.baseline)], cwd=ROOT, capture_output=True
        ).returncode == 0
        _rmtree_writable(self.temp_root)
        if not removed:
            subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(self.baseline / "bootstrap.sh"), "--repo", self.origin, "--ref", self.snapshot, *args],
            cwd=self.baseline, capture_output=True, text=True, timeout=180,
        )


def _native(path: Path) -> str:
    return str(path).replace("\\", "/")


def _text_files(target: Path):
    for p in sorted(target.rglob("*")):
        if ".git" in p.relative_to(target).parts or not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        yield p, data


def _tree(target: Path) -> dict[str, bytes]:
    """Every file but the git dir and bytecode, with the lock's per-row `at` timestamps masked: a rebuilt lock
    stamps the run time, which is the only byte a second run may change."""
    tree = {p.relative_to(target).as_posix(): p.read_bytes()
            for p in sorted(target.rglob("*"))
            if p.is_file() and not {".git", "__pycache__"} & set(p.relative_to(target).parts)}
    lock = ".claude/baseline.lock.json"
    tree[lock] = re.sub(rb'"at": "[^"]*"', b'"at": ""', tree[lock])
    return tree


def test_backslash_paths_normalize_and_a_bad_vault_root_fails_before_any_write() -> None:
    with _Baseline() as b:
        vault = b.temp_root / "vault"
        vault.mkdir()
        target = b.temp_root / "proj"
        result = b.run("--target", str(target), "--project-name", "TestGame", "--vault-root", str(vault))
        assert result.returncode == 0, result.stdout + result.stderr
        assert (target / ".claude" / "baseline.lock.json").is_file(), "backslash --target did not reach the real folder"
        assert not [p for p in b.baseline.iterdir() if "\\" in p.name or ":" in p.name], "a backslash path became a relative folder"
        subs = json.loads((target / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))["substitutions"]
        assert subs["{{VAULT_ROOT}}"] == _native(vault), subs
        absent = b.temp_root / "no_such_vault"
        refused = b.temp_root / "refused"
        result = b.run("--target", str(refused), "--project-name", "TestGame", "--vault-root", str(absent))
        assert result.returncode != 0, "a vault root that is not a directory was accepted"
        assert not refused.exists(), "files were written before the vault root was rejected"
        dropped = b.run("--target", str(refused), "--project-root", str(refused))
        assert dropped.returncode != 0 and not refused.exists(), "--project-root is still accepted"


def test_every_written_file_is_lf() -> None:
    with _Baseline() as b:
        vault = b.temp_root / "vault"
        vault.mkdir()
        target = b.temp_root / "proj"
        result = b.run("--target", _to_posix(target), "--project-name", "TestGame", "--vault-root", _to_posix(vault))
        assert result.returncode == 0, result.stdout + result.stderr
        crlf = [p.relative_to(target).as_posix() for p, data in _text_files(target) if b"\r" in data]
        crlf += [p.relative_to(vault).as_posix() for p, data in _text_files(vault) if b"\r" in data]
        assert not crlf, f"{len(crlf)} written files carry CR bytes: {crlf[:5]}"


def test_forced_rerun_is_idempotent_and_keeps_seeds() -> None:
    with _Baseline() as b:
        vault = b.temp_root / "vault"
        vault.mkdir()
        target = b.temp_root / "proj"
        args = ["--target", _to_posix(target), "--project-name", "TestGame", "--vault-root", _to_posix(vault)]
        first = b.run(*args)
        assert first.returncode == 0, first.stdout + first.stderr
        rerun_without_force = b.run(*args)
        assert rerun_without_force.returncode != 0, "a second run without --force clobbered .claude"
        before = _tree(target)
        second = b.run(*args, "--force")
        assert second.returncode == 0, second.stdout + second.stderr
        after = _tree(target)
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        assert not changed, f"a forced re-run changed {changed[:6]}"
        seed = target / ".claude" / "CLAUDE.md"
        seed.write_bytes(seed.read_bytes() + b"\nPROJECT-OWNED LINE\n")
        third = b.run(*args, "--force")
        assert third.returncode == 0, third.stdout + third.stderr
        assert b"PROJECT-OWNED LINE" in seed.read_bytes(), "--force overwrote an existing seed"
        text = (target / ".gitignore").read_text(encoding="utf-8")
        assert text.count("# >>> harness >>>") == 1 and text.count("# <<< harness <<<") == 1, text


def test_bootstrap_sets_up_git_ignore_worklog_and_memory_directory() -> None:
    with _Baseline() as b:
        vault = b.temp_root / "vault"
        vault.mkdir()
        target = b.temp_root / "OnboardGame"
        target.mkdir()
        (target / ".gitignore").write_text("build/\n", encoding="utf-8", newline="\n")
        local = target / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text('{"permissions": {"allow": ["Read"]}}\n', encoding="utf-8", newline="\n")
        args = ["--target", _to_posix(target), "--vault-root", _to_posix(vault), "--layers", "pure,coding"]
        result = b.run(*args, "--force")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OnboardGame" in result.stdout, "the derived project name was not echoed"
        assert (target / ".git").exists(), "no git repo created"
        hooks = subprocess.run(["git", "-C", str(target), "config", "--get", "core.hooksPath"],
                               capture_output=True, text=True).stdout.strip()
        assert hooks == ".claude/githooks", hooks
        assert (target / ".claude" / "githooks").is_dir(), "hooksPath points at a missing folder"
        ignore = (target / ".gitignore").read_text(encoding="utf-8")
        assert ignore.startswith("build/\n"), "the existing .gitignore lines were not kept"
        assert ".claude/settings.local.json" in ignore and ".claude/scratch/" in ignore, ignore
        assert ignore.index("# >>> harness >>>") < ignore.index(".claude/scratch/") < ignore.index("# <<< harness <<<")
        worklog = vault / "DevProjects" / "OnboardGame" / "Claude" / "TODO" / "Worklog.md"
        assert worklog.is_file(), "no vault Worklog.md skeleton"
        body = worklog.read_text(encoding="utf-8")
        assert "## Active" in body and "last_updated:" in body, body
        worklog.write_text("MINE\n", encoding="utf-8", newline="\n")
        data = json.loads(local.read_text(encoding="utf-8"))
        assert data["permissions"] == {"allow": ["Read"]}, "settings.local.json lost its other keys"
        assert data["autoMemoryDirectory"] == _native(target) + "/.claude/auto-memory", data
        again = b.run(*args, "--force")
        assert again.returncode == 0, again.stdout + again.stderr
        assert worklog.read_text(encoding="utf-8") == "MINE\n", "an existing Worklog.md was overwritten"
        assert ignore == (target / ".gitignore").read_text(encoding="utf-8"), "the .gitignore block is not idempotent"
        subs = json.loads((target / ".claude" / "baseline.lock.json").read_text(encoding="utf-8"))["substitutions"]
        assert subs["{{PROJECT_NAME}}"] == "OnboardGame", subs


# Template files deleted in commit range 9483449..bdb68ae (`git diff --name-only --diff-filter=D
# 9483449 bdb68ae -- template/`), relative to the target. Embedded as data because CI checkouts are
# shallow and cannot read that range.
RETIRED_IN_RANGE = (
    ".claude/auto-memory/arch_rule_absent_input_needs_its_own_outcome_value.md",
    ".claude/auto-memory/arch_rule_autonomous_loop_positive_liveness.md",
    ".claude/auto-memory/feedback_dont_defer_immediately_addressable.md",
    ".claude/auto-memory/feedback_found_defect_is_fixed_now_peer_owned_is_a_git_fact.md",
    ".claude/auto-memory/feedback_invoke_named_skill_not_manual_equivalent.md",
    ".claude/auto-memory/feedback_plan_mode_is_claude_code_built_in.md",
    ".claude/auto-memory/feedback_single_gate_no_secondary_approval.md",
    ".claude/auto-memory/gotcha_guard_can_be_an_algebraic_identity.md",
    ".claude/auto-memory/gotcha_workflow_args_permission_control_chars.md",
    ".claude/cloud-install.sh",
    ".claude/commands/agents/jmodot_submodule_procedure.md",
    ".claude/commands/agents/worklog_drive_triage.md",
    ".claude/commands/agents/worklog_plan_triage.md",
    ".claude/commands/create_obsidian_design_doc.md",
    ".claude/commands/plan_drive.md",
    ".claude/commands/system_check.md",
    ".claude/hooks/bash_shape_guard.py",
    ".claude/hooks/heredoc_escape_guard.py",
    ".claude/hooks/json_merge.py",
    ".claude/hooks/load_steps_validator.py",
    ".claude/hooks/tool_routing_cumulative.py",
    ".claude/hooks/tool_routing_cumulative_block.py",
    ".claude/hooks/tool_routing_post_grep.py",
    ".claude/reference/model_ladder_candidates.md",
    ".claude/scripts/anthropic_quota_probe.py",
    ".claude/scripts/codex_sidecar.sh",
    ".claude/skills/parallel_agents/SKILL.md",
    ".claude/tests/test_anthropic_quota_probe.py",
    ".claude/tests/test_duplicate_test_double_guard_commit_detection.py",
    ".claude/tests/test_heredoc_escape_guard.py",
    ".claude/tests/test_load_steps_validator.py",
    ".claude/tests/test_test_suite_gate_coverage_guard_commit_detection.py",
    ".claude/tools/sidecar_compact_end.py",
)


def test_fresh_bootstrap_contains_none_of_the_range_deleted_files() -> None:
    with _Baseline() as b:
        target = b.temp_root / "FreshGame"
        result = b.run("--target", _to_posix(target), "--project-name", "FreshGame")
        assert result.returncode == 0, result.stdout + result.stderr
        present = [rel for rel in RETIRED_IN_RANGE if (target / rel).exists()]
        assert not present, present


def main() -> int:
    cases = [test_bootstrap_into_temp_yields_a_v2_lock_that_checks_clean,
             test_each_layer_prefix_installs_only_its_layers_and_imports_their_doctrine,
             test_backslash_paths_normalize_and_a_bad_vault_root_fails_before_any_write,
             test_every_written_file_is_lf,
             test_forced_rerun_is_idempotent_and_keeps_seeds,
             test_bootstrap_sets_up_git_ignore_worklog_and_memory_directory,
             test_fresh_bootstrap_contains_none_of_the_range_deleted_files]
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
