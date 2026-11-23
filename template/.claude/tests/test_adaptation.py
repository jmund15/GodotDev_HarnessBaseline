"""Proof for tools/adaptation.py, the reader of `skills/project_subsystems/adaptation.json`.

Contract: an absent file gives every default silently; a valid key replaces its default; a wrong-typed
key keeps its default and prints one stderr line naming the file; a dotted key reads into a table; the
`tag` lookup returns a `sensitivity_tags` entry's folders; `get`, `run` and `tag` exit as the module says.
Per-layer defaults files are not read (the one negative case names one). Fixtures live under pytest's tmp_path.

    python -m pytest .claude/tests/test_adaptation.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "tools" / "adaptation.py"
sys.path.insert(0, str(TOOL.parent))
import adaptation  # noqa: E402

GOOD_CADENCE = {
    "slice": ["run the edited unit's proof file"],
    "merge": ["npm run verify:merge"],
    "close": ["python3 .claude/scripts/harness_tests.py --close"],
    "wide": [{"runner": "(?:python3?\\s+)?\\S*harness_tests\\.py\\b", "exempt": ["^--help$"], "narrow": None},
             {"runner": "npm run verify\\b", "exempt": [], "narrow": {"requires": ["^--reach$", "\\.spec\\.ts$"]}}],
}


def _claude_dir(tmp_path, content=None, name="proj"):
    """A `.claude` dir with `adaptation.json` holding `content` (a str, or a dict to dump); None writes no file."""
    adaptation._CACHE.clear()  # a same-size rewrite inside one clock tick keeps its (mtime, size) key
    claude = tmp_path / name / ".claude"
    skill = claude / "skills" / "project_subsystems"
    skill.mkdir(parents=True)
    if content is not None:
        text = content if isinstance(content, str) else json.dumps(content)
        (skill / "adaptation.json").write_text(text, encoding="utf-8", newline="\n")
    return str(claude)


def _load(claude):
    adaptation._CACHE.clear()
    return adaptation.load(claude)


def test_absent_file_gives_every_default_silently(tmp_path, capsys):
    claude = _claude_dir(tmp_path, None)
    assert _load(claude) == adaptation.DEFAULTS
    assert capsys.readouterr().err == ""


def test_valid_key_replaces_its_default_and_others_keep_theirs(tmp_path, capsys):
    claude = _claude_dir(tmp_path, {"read_only_commands": ["make"], "tests_root": "MyTests"})
    result = _load(claude)
    assert result["read_only_commands"] == ["make"]
    assert result["tests_root"] == "MyTests"
    assert result["git_submodules"] == []
    assert capsys.readouterr().err == ""


def test_wrong_typed_key_keeps_default_and_names_itself_in_one_line(tmp_path, capsys):
    claude = _claude_dir(tmp_path, {"tests_root": ["not", "a", "string"]})
    result = _load(claude)
    err = capsys.readouterr().err
    assert result["tests_root"] == "Tests"
    assert "tests_root" in err and len(err.strip().splitlines()) == 1


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"], ids=["unparseable", "not-an-object"])
def test_unreadable_file_gives_defaults_and_one_line(tmp_path, capsys, content):
    claude = _claude_dir(tmp_path, content)
    assert _load(claude) == adaptation.DEFAULTS
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_unknown_key_is_ignored(tmp_path, capsys):
    claude = _claude_dir(tmp_path, {"totally_unknown_key": 123, "memory_domains": []})
    assert _load(claude) == adaptation.DEFAULTS
    assert capsys.readouterr().err == ""


def test_dropped_layer_files_are_not_read(tmp_path):
    claude = _claude_dir(tmp_path, {"tests_root": "Project"})
    skill = Path(claude) / "skills" / "project_subsystems"
    (skill / "adaptation.godot.defaults.json").write_text('{"tests_root": "Layer"}', encoding="utf-8")
    assert _load(claude)["tests_root"] == "Project"
    (skill / "adaptation.json").unlink()
    assert _load(claude)["tests_root"] == "Tests"


def test_dotted_key_reads_a_table_sub_key(tmp_path, capsys):
    claude = _claude_dir(tmp_path, {"review": {"checklists": ["commands/checklists/a.md"]},
                                    "dispatch": {"pre_launch": "check.sh", "post_return": 5}})
    assert adaptation.get(claude, "review.checklists") == ["commands/checklists/a.md"]
    assert adaptation.get(claude, "dispatch.pre_launch") == "check.sh"
    assert adaptation.get(claude, "dispatch.post_return") == ""  # wrong-typed sub-key keeps its default
    err = capsys.readouterr().err
    assert "dispatch.post_return" in err and len(err.strip().splitlines()) == 1


def test_get_defaults_a_missing_key_and_returns_none_for_unknown(tmp_path):
    claude = _claude_dir(tmp_path, None)
    assert adaptation.get(claude, "review.checklists") == []
    assert adaptation.get(claude, "no_such_key") is None


def test_overnight_dir_dispatch_hooks_and_pr_checks_read_their_values(tmp_path):
    claude = _claude_dir(tmp_path, {"overnight_dir": "D:/Vault/Overnight", "dispatch": {"pre_launch": "pre.sh"},
                                    "pr_ready": {"checks": ["checks/boot.md"]}})
    assert adaptation.get(claude, "overnight_dir") == "D:/Vault/Overnight"
    assert adaptation.get(claude, "dispatch.pre_launch") == "pre.sh"
    assert adaptation.get(claude, "dispatch.post_return") == ""
    assert adaptation.get(claude, "pr_ready.checks") == ["checks/boot.md"]


def test_every_top_level_key_reads_back_from_a_fixture(tmp_path):
    values = {}
    for key, default in adaptation.DEFAULTS.items():
        if key in adaptation._SUB_TYPES:  # table keys read back through their sub-keys (above)
            continue
        values[key] = {"x": "y"} if isinstance(default, dict) else ["x"] if isinstance(default, list) else "x"
    result = _load(_claude_dir(tmp_path, values))
    assert {key: result[key] for key in values} == values


def test_project_name_and_vault_root_are_strings(tmp_path):
    claude = _claude_dir(tmp_path, {"project_name": "PushinPotions", "vault_root": "C:/Vault"})
    assert adaptation.get(claude, "project_name") == "PushinPotions"
    assert adaptation.get(claude, "vault_root") == "C:/Vault"


def test_tag_paths_returns_the_fixture_folders(tmp_path):
    entries = [{"id": "scene-wiring", "paths": ["**/*.tscn"], "sets": {}, "source": "s"},
               {"id": "playtest-subject-folders", "paths": ["NPCs/**", "Spells/**"], "sets": {}, "source": "s"}]
    claude = _claude_dir(tmp_path, {"sensitivity_tags": entries})
    assert adaptation.tag_paths(claude, "playtest-subject-folders") == ["NPCs/**", "Spells/**"]
    assert adaptation.tag_paths(claude, "no-such-tag") is None


def test_tag_paths_is_none_when_no_registry_is_set(tmp_path):
    assert adaptation.tag_paths(_claude_dir(tmp_path, None), "playtest-subject-folders") is None


def test_gate_cadence_good_table_validates_and_bad_rows_are_named():
    assert adaptation.gate_cadence_errors(GOOD_CADENCE) == []
    bad = [
        ("wide[0].hint", dict(GOOD_CADENCE, wide=[dict(GOOD_CADENCE["wide"][0], hint=5)])),
        ("slice", dict(GOOD_CADENCE, slice="run")),
        ("wide[0].runner", dict(GOOD_CADENCE, wide=[{"runner": "[", "exempt": [], "narrow": None}])),
        ("wide[0].narrow", dict(GOOD_CADENCE, wide=[{"runner": "x", "exempt": [], "narrow": {}}])),
        ("reach[0].cmd", {"reach": [{"name": "a", "paths": ["x/**"]}]}),
        ("main.command", {"main": {"command": "run it"}}),
        ("main.retry_exits", {"main": {"command": ["x"], "retry_exits": [True]}}),
    ]
    for needle, value in bad:
        errors = adaptation.gate_cadence_errors(value)
        assert any(needle in e for e in errors), (needle, errors)


def test_gate_cadence_wrong_type_falls_back_to_empty_table(tmp_path, capsys):
    claude = _claude_dir(tmp_path, {"gate_cadence": GOOD_CADENCE["slice"]})
    assert _load(claude)["gate_cadence"] == adaptation.DEFAULTS["gate_cadence"]
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_rewriting_the_file_reloads_on_a_same_size_edit(tmp_path):
    claude = _claude_dir(tmp_path, {"land_prefix": "coding/"})
    assert adaptation.get(claude, "land_prefix") == "coding/"
    skill = Path(claude) / "skills" / "project_subsystems"
    (skill / "adaptation.json").write_text('{"land_prefix": "coding2/"}', encoding="utf-8", newline="\n")
    assert adaptation.get(claude, "land_prefix") == "coding2/"


def _spawn_get(claude_root, key):
    tools = Path(claude_root) / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "adaptation.py").write_bytes(TOOL.read_bytes())
    return subprocess.run([sys.executable, str(tools / "adaptation.py"), "get", key], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8")


def test_cli_get_prints_a_scalar_through_a_spawned_process(tmp_path):
    claude = _claude_dir(tmp_path, {"tests_root": "T"})
    done = _spawn_get(Path(claude), "tests_root")
    assert done.returncode == 0 and done.stdout.strip() == "T"


def test_cli_verbs_in_process(tmp_path, monkeypatch, capsys):
    claude = _claude_dir(tmp_path, {
        "index_prebuild": [], "usage_reset": {"weekday": "monday", "hour": 2},
        "sensitivity_tags": [{"id": "playtest-subject-folders", "paths": ["NPCs/**"], "sets": {}, "source": "s"}],
        "gate_cadence": GOOD_CADENCE}, name="cli")
    spec = importlib.util.spec_from_file_location("adaptation_cli", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(Path(claude) / "tools" / "adaptation.py")  # claude_dir is the file's grandparent
    assert module.main(["get", "usage_reset"]) == 0
    assert json.loads(capsys.readouterr().out) == {"weekday": "monday", "hour": 2}
    assert module.main(["tag", "playtest-subject-folders"]) == 0
    assert capsys.readouterr().out.splitlines() == ["NPCs/**"]
    assert module.main(["tag", "missing"]) == 2
    assert module.main(["get", "no_such_key"]) == 2
    assert module.main(["run", "tests_root"]) == 2


def test_cli_run_executes_each_entry_and_reports_a_failure(tmp_path, capsys):
    claude = Path(_claude_dir(tmp_path, None, name="run"))
    marker = (tmp_path / "ran.txt").as_posix()
    py = sys.executable.replace("\\", "/")
    entries = ["%s -c \"import sys; sys.exit(3)\"" % py,
               "%s -c \"open(r'%s','a').write('x')\"" % (py, marker)]
    (claude / "skills" / "project_subsystems" / "adaptation.json").write_text(
        json.dumps({"index_prebuild": entries}), encoding="utf-8", newline="\n")
    adaptation._CACHE.clear()
    spec = importlib.util.spec_from_file_location("adaptation_run", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(claude / "tools" / "adaptation.py")
    assert module.main(["run", "index_prebuild"]) == 0
    assert open(marker, encoding="utf-8").read() == "x"
    assert "exit 3" in capsys.readouterr().err
