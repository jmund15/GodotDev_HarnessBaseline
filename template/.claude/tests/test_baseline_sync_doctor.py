#!/usr/bin/env python3
"""Proofs for `baseline_sync.py doctor` and the lane agents `init` and `pull` render.

    python3 .claude/tests/test_baseline_sync_doctor.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_baseline_sync as T  # noqa: E402
from _case_pool import run_cases  # noqa: E402

IGNORE_ENTRIES_REL = ".claude/gitignore.harness"
IGNORE_ENTRIES = ".claude/.cache/\n.claude/settings.local.json\n"
GITIGNORE_BLOCK = "# >>> harness >>>\n" + IGNORE_ENTRIES + "# <<< harness <<<\n"
WORKLOG = "DevProjects/Demo/Claude/TODO/Worklog.md"
SEED_REL = ".claude/CLAUDE.md"
ITEMS = ("substitutions", "seeds", "git-repo", "git-hooks-path", "gitignore-block", "worklog",
         "user-scope", "lens-registry", "auto-memory-dir", "lane-agents")


def _table(*agent_types: str) -> bytes:
    shapes = {"any": {"builtins": ["Read", "Glob", "Grep"], "mcpServers": []}}
    for name in agent_types:
        shapes[name] = {"agentType": "lane-" + name, "tier": "fanout",
                        "builtins": ["Read", "Bash"], "mcpServers": []}
    table = {"version": 1, "defaultShape": "any", "requiredReadTools": ["Read", "Glob", "Grep"],
             "canonicalOrder": ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "TodoWrite"],
             "shapes": shapes}
    return (json.dumps(table, indent=2) + "\n").encode()


def _doctor(root: Path, *extra: str) -> tuple[int, dict[str, dict]]:
    result = T._run(root, "doctor", "--json", *extra)
    text = result.stdout.decode("utf-8", errors="replace")
    assert text.strip().startswith("["), T._out(result)
    return result.returncode, {row["item"]: row for row in json.loads(text)}


def _lock(root: Path, subs: dict, files: dict) -> None:
    T._write_json(root / ".claude" / "baseline.lock.json", {
        "schema": 2, "profile": "pure", "substitutions": subs, "baseline_repo": "x",
        "baseline_ref": "main", "synced_commit": "0" * 40, "identity": {"abbreviations": []},
        "files": files})


def _seed_row() -> dict:
    return {"status": "local", "layer": "pure"}


def test_doctor_reports_every_gap_of_a_planted_consumer() -> None:
    with T._fixture() as path:
        root = path / "consumer"
        T._init_repo(root)
        T._write(root / ".claude" / "githooks" / "pre-commit", b"#!/bin/sh\n")
        T._write(root / IGNORE_ENTRIES_REL, IGNORE_ENTRIES.encode())
        T._write(root / SEED_REL, b"# {{PROJECT_NAME}}\n<one-line concept>\nWorklog: `" + WORKLOG.encode() + b"`\n")
        T._write(root / ".claude" / "guards" / "tool_profiles.json", _table("verify"))
        _lock(root, {"{{VAULT_ROOT}}": str(path / "no-such-vault").replace("\\", "/")},
              {SEED_REL: _seed_row()})
        code, rows = _doctor(root)
        assert set(ITEMS) <= set(rows), sorted(rows)
        assert code != 0
        assert rows["substitutions"]["status"] == "fail", rows["substitutions"]
        assert rows["seeds"]["status"] == "pending" and SEED_REL in rows["seeds"]["detail"], rows["seeds"]
        assert rows["git-hooks-path"]["status"] == "fail" and "core.hooksPath" in rows["git-hooks-path"]["fix"]
        assert rows["gitignore-block"]["status"] == "fail" and "# >>> harness >>>" in rows["gitignore-block"]["detail"]
        assert rows["worklog"]["status"] == "fail", rows["worklog"]
        assert rows["auto-memory-dir"]["status"] == "fail" and "autoMemoryDirectory" in rows["auto-memory-dir"]["fix"]
        assert rows["lane-agents"]["status"] == "fail" and "agents --write" in rows["lane-agents"]["fix"]
        for row in rows.values():
            assert row["status"] in ("ok", "pending", "fail"), row
            if row["status"] != "ok":
                assert row["fix"], row


def test_doctor_without_a_repo_fails_git_repo() -> None:
    with T._fixture() as path:
        root = path / "consumer"
        (root / ".claude").mkdir(parents=True)
        _lock(root, {}, {})
        code, rows = _doctor(root)
        assert code != 0 and rows["git-repo"]["status"] == "fail", rows["git-repo"]


def _fixed_consumer(path: Path) -> Path:
    root = path / "consumer"
    vault = path / "vault"
    T._init_repo(root)
    T._git(root, "config", "core.hooksPath", ".claude/githooks")
    T._write(root / ".claude" / "githooks" / "pre-commit", b"#!/bin/sh\n")
    T._write(vault / WORKLOG, b"# Worklog\n")
    T._write(root / ".gitignore", GITIGNORE_BLOCK.encode())
    T._write(root / IGNORE_ENTRIES_REL, IGNORE_ENTRIES.encode())
    T._write(root / SEED_REL, b"# Demo\nWorklog: `" + WORKLOG.encode() + b"`\n")
    T._write(root / ".claude" / "auto-memory" / "MEMORY.md", b"# index\n")
    T._write(root / ".claude" / "guards" / "tool_profiles.json", _table("verify"))
    memory = str(root.resolve() / ".claude" / "auto-memory").replace("\\", "/")
    T._write(root / ".claude" / "settings.local.json",
             json.dumps({"autoMemoryDirectory": memory}).encode())
    _lock(root, {"{{VAULT_ROOT}}": str(vault).replace("\\", "/"), "{{PROJECT_NAME}}": "Demo"},
          {SEED_REL: _seed_row(),
           ".claude/agents/lane-verify.md": {"status": "local", "layer": None}})
    sync = [sys.executable, str(T.ENGINE.parent / "tool_profiles.py"), "agents", "--write",
            "--table", str(root / ".claude" / "guards" / "tool_profiles.json"),
            "--agents-dir", str(root / ".claude" / "agents")]
    subprocess.run(sync, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return root


def test_doctor_on_a_fixed_consumer_is_all_ok_and_exits_zero() -> None:
    with T._fixture() as path:
        root = _fixed_consumer(path)
        code, rows = _doctor(root)
        bad = {k: v for k, v in rows.items() if v["status"] != "ok"}
        assert code == 0 and not bad, bad


def test_doctor_owner_content_alone_exits_zero() -> None:
    with T._fixture() as path:
        root = _fixed_consumer(path)
        T._write(root / SEED_REL, b"# Demo\n<one-line concept>\nWorklog: `" + WORKLOG.encode() + b"`\n")
        code, rows = _doctor(root)
        assert code == 0 and rows["seeds"]["status"] == "pending", rows["seeds"]


def test_doctor_reads_the_worklog_path_the_worklog_command_uses() -> None:
    with T._fixture() as path:
        root = _fixed_consumer(path)
        T._write(root / SEED_REL, b"# Demo\n")
        T._write(root / ".claude" / "commands" / "worklog.md", b"Source of truth: `" + WORKLOG.encode() + b"` (Obsidian).\n")
        code, rows = _doctor(root)
        assert rows["worklog"]["status"] == "ok", rows["worklog"]
        (root / ".claude" / "commands" / "worklog.md").unlink()
        code, rows = _doctor(root)
        assert rows["worklog"]["status"] == "fail" and "commands/worklog.md" in rows["worklog"]["fix"], rows["worklog"]


def test_doctor_names_a_wrong_auto_memory_directory() -> None:
    with T._fixture() as path:
        root = _fixed_consumer(path)
        T._write(root / ".claude" / "settings.local.json",
                 json.dumps({"autoMemoryDirectory": "C:/elsewhere/auto-memory"}).encode())
        code, rows = _doctor(root)
        assert code != 0 and rows["auto-memory-dir"]["status"] == "fail"
        assert "C:/elsewhere/auto-memory" in rows["auto-memory-dir"]["detail"]


def test_doctor_fails_a_block_that_lags_the_shipped_entries() -> None:
    with T._fixture() as path:
        root = _fixed_consumer(path)
        T._write(root / IGNORE_ENTRIES_REL, (IGNORE_ENTRIES + ".claude/tmp/\n").encode())
        code, rows = _doctor(root)
        row = rows["gitignore-block"]
        assert code != 0 and row["status"] == "fail" and "differs" in row["detail"], row
        assert "pull" in row["fix"], row


def test_init_and_pull_write_the_ignore_block_and_keep_project_lines() -> None:
    with T._fixture() as path:
        root, baseline, remote, _c = T._v2_fixture(path, {
            IGNORE_ENTRIES_REL: {"upstream": IGNORE_ENTRIES.encode()},
        })
        T._write(root / ".gitignore", b"build/\n")
        (root / ".claude" / "baseline.lock.json").unlink()
        init = T._run(root, "init", "--baseline-dir", str(baseline), "--repo", str(remote).replace("\\", "/"),
                      "--sub", "{{PROJECT_NAME}}=Demo", "--layers", "pure")
        assert init.returncode == 0, T._out(init)
        text = (root / ".gitignore").read_text(encoding="utf-8")
        assert text == "build/\n\n" + GITIGNORE_BLOCK, repr(text)
        T._write(root / ".gitignore", ("build/\n\n" + GITIGNORE_BLOCK.replace(".claude/.cache/\n", "")).encode())
        pull = T._run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, T._out(pull)
        assert (root / ".gitignore").read_text(encoding="utf-8") == text, "pull did not restore the block"
        again = T._run_baseline(root, baseline, "pull")
        assert "harness block written" not in T._out(again), T._out(again)
        assert (root / ".gitignore").read_text(encoding="utf-8") == text


def test_pull_keeps_this_proofs_planted_tokens() -> None:
    sys.path.insert(0, str(T.ENGINE.parent))
    import baseline_sync
    text = Path(__file__).read_text(encoding="utf-8")
    subs = {"{{VAULT_ROOT}}": "C:/vault", "{{PROJECT_NAME}}": "Real"}
    assert baseline_sync.forward_for(".claude/tests/test_baseline_sync_doctor.py", text, subs) == text


LANE_TABLE_ROW = ".claude/guards/tool_profiles.json"


def _lane_names(root: Path) -> list[str]:
    return sorted(p.name for p in (root / ".claude" / "agents").glob("lane-*.md"))


def test_init_renders_lane_agents_with_local_rows() -> None:
    with T._fixture() as path:
        root, baseline, remote, _c = T._v2_fixture(path, {
            LANE_TABLE_ROW: {"upstream": _table("verify", "survey")},
        })
        (root / ".claude" / "baseline.lock.json").unlink()
        init = T._run(root, "init", "--baseline-dir", str(baseline), "--repo", str(remote).replace("\\", "/"),
                      "--sub", "{{PROJECT_NAME}}=Demo", "--layers", "pure")
        assert init.returncode == 0, T._out(init)
        assert _lane_names(root) == ["lane-survey.md", "lane-verify.md"], _lane_names(root)
        files = T._load_lock(root)["files"]
        for name in _lane_names(root):
            row = files[".claude/agents/" + name]
            assert row["status"] == "local" and row["judged"]["verdict"] == "keep-local", row
            assert row["judged"]["sha"] == T._sha((root / ".claude" / "agents" / name).read_bytes())


def test_pull_that_changes_an_agent_input_renders_and_a_noop_pull_writes_nothing() -> None:
    with T._fixture() as path:
        old = _table("verify")
        root, baseline, _r, _c = T._v2_fixture(path, {
            LANE_TABLE_ROW: {"upstream": _table("verify", "survey"), "local": old, "hash": T._sha(old)},
        })
        pull = T._run_baseline(root, baseline, "pull")
        assert pull.returncode == 0, T._out(pull)
        assert _lane_names(root) == ["lane-survey.md", "lane-verify.md"], _lane_names(root)
        files = T._load_lock(root)["files"]
        assert files[".claude/agents/lane-survey.md"]["status"] == "local"
        assert files[".claude/agents/lane-verify.md"]["status"] == "local"
        lock_before = (root / ".claude" / "baseline.lock.json").read_bytes()
        agents_before = {n: (root / ".claude" / "agents" / n).read_bytes() for n in _lane_names(root)}
        again = T._run_baseline(root, baseline, "pull")
        T._assert_no_change(again)
        assert (root / ".claude" / "baseline.lock.json").read_bytes() == lock_before
        assert {n: (root / ".claude" / "agents" / n).read_bytes() for n in _lane_names(root)} == agents_before
        assert "wrote" not in T._out(again)


def _ignored_consumer(path: Path, rows: dict[str, str], *, add_force: tuple[str, ...] = ()) -> Path:
    root = _fixed_consumer(path)
    T._write(root / ".gitignore", GITIGNORE_BLOCK.encode() + b".claude/tests/" + bytes([10]))
    lock = T._load_lock(root)
    for rel, status in rows.items():
        T._write(root / rel, b"x" + bytes([10]))
        lock["files"][rel] = {"status": status, "layer": "pure"}
    T._write_json(root / ".claude" / "baseline.lock.json", lock)
    for rel in add_force:
        T._git(root, "add", "-f", rel)
    return root


def test_doctor_reports_an_untracked_ignored_delivered_row() -> None:
    tracked = ".claude/tests/test_a.py"
    composed = ".claude/tests/test_b.py"
    with T._fixture() as path:
        root = _ignored_consumer(path, {tracked: "tracked", composed: "composed"})
        code, rows = _doctor(root)
        row = rows["delivered-ignored"]
        assert code != 0 and row["status"] == "fail", row
        assert tracked in row["detail"] and composed in row["detail"], row
        assert "git add -f" in row["fix"], row


def test_doctor_allows_a_tracked_ignored_row_and_a_local_ignored_row() -> None:
    delivered = ".claude/tests/test_a.py"
    local = ".claude/tests/test_mine.py"
    with T._fixture() as path:
        root = _ignored_consumer(path, {delivered: "tracked", local: "local"}, add_force=(delivered,))
        code, rows = _doctor(root)
        assert rows["delivered-ignored"]["status"] == "ok", rows["delivered-ignored"]
        assert code == 0, rows


def main() -> int:
    return run_cases([
        test_doctor_reports_every_gap_of_a_planted_consumer,
        test_doctor_without_a_repo_fails_git_repo,
        test_doctor_on_a_fixed_consumer_is_all_ok_and_exits_zero,
        test_doctor_owner_content_alone_exits_zero,
        test_doctor_reads_the_worklog_path_the_worklog_command_uses,
        test_doctor_names_a_wrong_auto_memory_directory,
        test_doctor_fails_a_block_that_lags_the_shipped_entries,
        test_init_and_pull_write_the_ignore_block_and_keep_project_lines,
        test_pull_keeps_this_proofs_planted_tokens,
        test_doctor_reports_an_untracked_ignored_delivered_row,
        test_doctor_allows_a_tracked_ignored_row_and_a_local_ignored_row,
        test_init_renders_lane_agents_with_local_rows,
        test_pull_that_changes_an_agent_input_renders_and_a_noop_pull_writes_nothing,
    ])


if __name__ == "__main__":
    sys.exit(main())
