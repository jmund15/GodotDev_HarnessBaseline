#!/usr/bin/env python3
"""Proof that the `stamp-inputs:` list in the workstation setup command parses.

`tools/workstation_profile.py` reads `stamp-inputs:` from the command's frontmatter to decide whether a
machine's setup stamp is current, and the shared user-scope tool, manifest and templates must be listed there.
A copy that drops a template, or misspells the key, must read as short or undeclared.

    python -m pytest .claude/tests/test_setup_docs.py -q
"""
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SETUP_DOC = ".claude/commands/workstation_setup.md"


def profile_tool():
    """`tools/workstation_profile.py` from this checkout, loaded by path so either cwd works."""
    spec = importlib.util.spec_from_file_location(
        "docs_workstation_profile", REPO / ".claude" / "tools" / "workstation_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def doc_copy(tmp_path, old=None, new=""):
    """A root holding the setup doc, with the first `old` replaced by `new`."""
    text = (REPO / SETUP_DOC).read_text(encoding="utf-8")
    if old is not None:
        assert old in text, "the planted edit did not apply: %r" % old
        text = text.replace(old, new, 1)
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / SETUP_DOC).write_text(text, encoding="utf-8")
    return tmp_path


def shared_user_scope_paths():
    """The tool and every path the shared manifest names, as repo-relative stamp inputs."""
    manifest = json.loads((REPO / ".claude/user_scope/manifest.json").read_text(encoding="utf-8"))
    return ([".claude/tools/user_scope.py", ".claude/user_scope/manifest.json"]
            + [".claude/user_scope/" + item["src"] for item in manifest["items"] if "src" in item])


def undeclared(root):
    declared = profile_tool().stamp_inputs(root) or []
    return [path for path in shared_user_scope_paths() if path not in declared]


def test_the_shared_user_scope_tool_manifest_and_templates_are_declared_stamp_inputs():
    assert undeclared(REPO) == []


def test_a_dropped_template_leaves_the_stamp_inputs_short(tmp_path):
    root = doc_copy(tmp_path, '  - ".claude/user_scope/statusline.py"\n')
    assert undeclared(root) == [".claude/user_scope/statusline.py"]


def test_the_real_command_docs_stamp_inputs_parse():
    """A malformed `stamp-inputs:` reads as UNAUDITED on every machine forever, which no other case would show."""
    declared = profile_tool().stamp_inputs(REPO)
    assert declared and ".mcp.json" in declared


def test_a_misspelled_stamp_inputs_reads_as_no_declaration(tmp_path):
    assert profile_tool().stamp_inputs(doc_copy(tmp_path, "stamp-inputs:", "stamp-input:")) is None
