"""Proof for hooks/compact_directive_anchor.py: a state-file write points the session at it, and a compaction
re-prints the pointed-at file.

Decision cases call the hook's functions in process. One spawned case proves the registered command runs
from stdin JSON and writes the file's text to stdout.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "compact_directive_anchor.py"
sys.path.insert(0, str(HOOK.parent))
import compact_directive_anchor as hook  # noqa: E402

STATE_TEXT = "# Initiative\n\n## Goal\nShip it.\n\n## Standing facts\n- Push: owner said \"push when green\".\n\n## Slices\n- s1 in progress\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / ".claude" / "work").mkdir(parents=True)
    return tmp_path


def write_payload(root, file_path, session="sess_01"):
    return {"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": session,
            "tool_input": {"file_path": file_path}, "cwd": str(root)}


def test_a_state_file_write_points_the_session_at_it(root):
    hook.on_write(write_payload(root, ".claude/work/slug.md"), root)
    assert (root / ".claude/scratch/active_state_sess_01").read_text(encoding="utf-8").strip() == ".claude/work/slug.md"


def test_an_absolute_edit_path_points_the_session_too(root):
    target = str(root / ".claude" / "work" / "slug.md")
    hook.on_write(write_payload(root, target), root)
    assert (root / ".claude/scratch/active_state_sess_01").read_text(encoding="utf-8").strip() == ".claude/work/slug.md"


@pytest.mark.parametrize("file_path", [".claude/scratch/x.md", ".claude/work/sub/x.md", ".claude/work/x.txt", "README.md"])
def test_a_write_outside_the_state_files_writes_no_pointer(root, file_path):
    hook.on_write(write_payload(root, file_path), root)
    assert not (root / ".claude/scratch").exists()


def test_compaction_prints_the_state_file_with_its_standing_facts(root):
    (root / ".claude/work/slug.md").write_text(STATE_TEXT, encoding="utf-8")
    hook.on_write(write_payload(root, ".claude/work/slug.md"), root)
    out = hook.on_compact({"hook_event_name": "SessionStart", "session_id": "sess_01"}, root)
    assert out == STATE_TEXT
    assert "owner said" in out


def test_no_pointer_no_output(root):
    assert hook.on_compact({"session_id": "sess_02"}, root) == ""


def test_a_pointer_to_a_deleted_file_prints_nothing(root):
    hook.on_write(write_payload(root, ".claude/work/gone.md"), root)
    assert hook.on_compact({"session_id": "sess_01"}, root) == ""


def test_a_long_file_keeps_its_standing_facts_and_names_the_rest(root):
    body = "## Slices\n" + "- slice line\n" * 2000
    text = STATE_TEXT.split("## Slices")[0] + body
    clipped = hook.clip(text, ".claude/work/slug.md")
    assert len(clipped) <= hook.CAP + 100
    assert "owner said" in clipped
    assert "read .claude/work/slug.md for the rest" in clipped


def test_an_unsafe_session_id_writes_and_reads_nothing(root):
    hook.on_write(write_payload(root, ".claude/work/slug.md", session="../evil"), root)
    assert not (root / ".claude/scratch").exists()


def test_the_registered_command_prints_the_file_from_stdin(root):
    (root / ".claude/work/slug.md").write_text(STATE_TEXT, encoding="utf-8")
    hook.on_write(write_payload(root, ".claude/work/slug.md"), root)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "sess_01", "source": "compact"})
    run = subprocess.run([sys.executable, str(HOOK)], input=payload.encode("utf-8"), env=env,
                         capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stdout.decode("utf-8") == STATE_TEXT


def test_a_non_json_payload_is_a_silent_no_op():
    run = subprocess.run([sys.executable, str(HOOK)], input=b"not json", capture_output=True, timeout=30)
    assert run.returncode == 0 and run.stdout == b""
