"""Proof for tools/launch_check.py, the launch checks the seat launcher and the Workflow entry share.

    python -m pytest .claude/tests/test_launch_check.py -q
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import launch_check as lc  # noqa: E402

LAUNCH_CHECK = TOOLS / "launch_check.py"


def _brief(root, body):
    path = root / "brief.md"
    path.write_text(body, encoding="utf-8")
    return "brief.md"


def test_parse_brief_reads_both_list_headers_and_stops_at_prose():
    text = "inputs:\n- a/x.md\n- b/y.md\nprose line\ntargets:\n- out.md\n"
    assert lc.parse_brief(text) == {"inputs": ["a/x.md", "b/y.md"], "targets": ["out.md"]}


def test_missing_brief_is_refused(tmp_path):
    assert lc.check("nope.md", "out.md", str(tmp_path), "yes") == ["brief does not resolve: nope.md"]


def test_unresolved_input_is_refused_against_the_root(tmp_path):
    brief = _brief(tmp_path, "inputs:\n- missing.md\n")
    causes = lc.check(brief, "out.md", str(tmp_path), "yes")
    assert causes and "missing.md" in causes[0]


def test_resolved_input_is_allowed(tmp_path):
    (tmp_path / "present.md").write_text("x", encoding="utf-8")
    brief = _brief(tmp_path, "inputs:\n- present.md\n")
    assert lc.check(brief, "out.md", str(tmp_path), "no") == []


def test_read_only_seat_naming_another_target_is_refused(tmp_path):
    brief = _brief(tmp_path, "targets:\n- elsewhere.md\n")
    causes = lc.check(brief, "out.md", str(tmp_path), "no")
    assert causes and "elsewhere.md" in causes[0]


def test_read_only_seat_naming_only_the_output_is_allowed(tmp_path):
    brief = _brief(tmp_path, "targets:\n- out.md\n")
    assert lc.check(brief, "out.md", str(tmp_path), "no") == []


def test_writing_seat_may_name_targets(tmp_path):
    brief = _brief(tmp_path, "targets:\n- src/a.py\n")
    assert lc.check(brief, "out.md", str(tmp_path), "yes") == []


def test_seat_row_expands_config_dir_and_unknown_seat_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "seats.json").write_text(
        '{"seats": {"s": {"adapter": "scripts/a.sh", "writes": "no", "max_concurrent": 2,'
        ' "configDir": "~/cfg", "model": "m"}}}', encoding="utf-8")
    text = lc.seat_assignments("s", claude_dir=tmp_path)
    assert "SEAT_WRITES=no" in text and "SEAT_MAX=2" in text
    value = dict(line.split("=", 1) for line in text.splitlines())["SEAT_CONFIG_DIR"]
    assert shlex.split(value) == [os.path.expanduser("~/cfg")]
    assert lc.seat_assignments("nope", claude_dir=tmp_path) is None


def test_shipped_seat_table_names_both_seats():
    rows = lc.seat_rows(TOOLS.parent)
    assert rows["antigravity"]["writes"] == "no" and rows["claude-secondary"]["writes"] == "yes"


def test_unset_hook_runs_nothing(tmp_path):
    assert lc.run_hook("dispatch.pre_launch", ["s"], str(tmp_path), lookup=lambda key: None) == (0, "")


def test_hook_gets_the_job_arguments_and_its_failure_passes_stderr(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text('printf "%s|%s|%s|%s" "$@" > args.txt\n', encoding="utf-8", newline="\n")
    rc, _ = lc.run_hook("k", ["seat", "brief", "out", "label"], str(tmp_path),
                        lookup=lambda key: "bash hook.sh")
    assert rc == 0
    assert (tmp_path / "args.txt").read_text(encoding="utf-8") == "seat|brief|out|label"
    rc, err = lc.run_hook("k", [], str(tmp_path), lookup=lambda key: "echo refused >&2; exit 3")
    assert rc == 3 and "refused" in err


def test_main_pre_launch_refuses_with_the_hook_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = lc.main(["pre_launch", "s", "b", "o", "l"], lookup=lambda key: "echo no-launch >&2; exit 1")
    assert rc == 2 and "no-launch" in capsys.readouterr().err


def test_main_post_return_refuses_with_the_hook_stderr_and_leaves_the_answer(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out.md").write_text("answer", encoding="utf-8")
    rc = lc.main(["post_return", "s", "b", "out.md", "l"], lookup=lambda key: "echo answer-rejected >&2; exit 1")
    assert rc == 2 and "answer-rejected" in capsys.readouterr().err
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == "answer"


def test_spawned_check_refuses_a_read_only_job_naming_a_peer_target(tmp_path):
    brief = _brief(tmp_path, "targets:\n- other.md\n")
    done = subprocess.run([sys.executable, str(LAUNCH_CHECK), "check", "--brief", brief, "--out", "out.md",
                           "--root", str(tmp_path), "--writes", "no"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 2 and "launch refused" in done.stderr


BACKSLASH_DIR = 'C:' + chr(92) + 'Users' + chr(92) + 'jo' + chr(92) + 'wt'


def _stage(tmp_path, brief_abs, writes=True, workdir=None):
    here = tmp_path / "here"
    (here / "guards").mkdir(parents=True, exist_ok=True)
    (here / "guards" / "rails.md").write_text("RAILS TEXT\n", encoding="utf-8")
    slot = tmp_path / "slot"
    slot.mkdir(exist_ok=True)
    lc.stage(slot, brief_abs, str(tmp_path / "spill" / "out.md"), str(here), workdir or str(tmp_path / "wt"),
             str(tmp_path / "main") if writes else "")
    return slot


def test_stage_prompt_is_the_rails_once_then_the_brief(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("inputs:\n- a.md\n", encoding="utf-8")
    prompt = (_stage(tmp_path, str(brief)) / "prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("RAILS TEXT\n") and prompt.count("RAILS TEXT") == 1
    assert prompt.endswith("inputs:\n- a.md\n")


def test_stage_settings_register_the_floor_in_place_of_the_bare_wait_guard_and_fence_the_spill_dir(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("x\n", encoding="utf-8")
    groups = json.loads((_stage(tmp_path, str(brief)) / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [h["command"] for g in groups for h in g["hooks"]]
    assert any("pre_bash.py" in c for c in commands) and not any("foreground_wait_guard" in c for c in commands)
    fence = [c for c in commands if "sidecar_workdir_guard.py" in c]
    assert len(fence) == 1 and "spill" in fence[0]


def test_stage_settings_with_a_backslash_path_parse(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("x" + chr(10), encoding="utf-8")
    slot = _stage(tmp_path, str(brief), workdir=BACKSLASH_DIR)
    commands = [h["command"] for g in json.loads((slot / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
                for h in g["hooks"]]
    assert any(BACKSLASH_DIR in c for c in commands)


def test_stage_read_only_seat_gets_no_fence(tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("x\n", encoding="utf-8")
    slot = _stage(tmp_path, str(brief), writes=False)
    assert "sidecar_workdir_guard" not in (slot / "settings.json").read_text(encoding="utf-8")
