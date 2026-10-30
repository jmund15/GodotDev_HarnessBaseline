"""UID ownership follows the edited Godot project, not the hook launcher."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import tres_format_guard as guard

TEXT = ('[gd_resource type="Resource" format=3]\n\n'
        '[ext_resource type="Script" uid="uid://authored" path="res://Scripts/Probe.cs" id="1"]\n\n'
        '[resource]\nscript = ExtResource("1")\nvalue = 1\n')


def project(root, uid):
    root.mkdir()
    (root / 'project.godot').write_text('[application]\nconfig/name="Probe"\n')
    (root / 'Scripts').mkdir()
    (root / 'Scripts/Probe.cs').write_text('public partial class Probe : Resource {}')
    (root / 'Scripts/Probe.cs.uid').write_text(uid)
    (root / 'Data').mkdir()
    return root / 'Data/probe.tres'


def invoke(payload, launcher):
    proc = subprocess.run([sys.executable, str(Path(guard.__file__)), '--hook'],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env=dict(os.environ, CLAUDE_PROJECT_DIR=str(launcher)), timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout).get('hookSpecificOutput', {})


def test_uid_follows_absolute_edit_target_not_launcher(tmp_path):
    main = tmp_path / 'main'
    edited = tmp_path / 'worktree'
    project(main, 'uid://main')
    target = project(edited, 'uid://worktree')
    target.write_text(TEXT)
    result = invoke({'tool_name': 'Edit', 'cwd': str(main), 'tool_input': {
        'file_path': str(target), 'old_string': 'value = 1', 'new_string': 'value = 2'}}, main)
    fixed = result['updatedInput']['new_string']
    assert 'uid="uid://worktree"' in fixed
    assert 'uid://main' not in fixed
    assert 'value = 2' in fixed
    assert 'additionalContext' not in result


def test_relative_write_target_uses_payload_cwd_and_nearest_project(tmp_path):
    main = tmp_path / 'main'
    project(main, 'uid://main')
    nested = main / 'nested'
    project(nested, 'uid://nested')
    result = invoke({'tool_name': 'Write', 'cwd': str(nested / 'Data'), 'tool_input': {
        'file_path': 'probe.tres', 'content': TEXT}}, main)
    assert 'uid="uid://nested"' in result['updatedInput']['content']
    assert 'additionalContext' not in result


def test_relative_edit_target_uses_payload_cwd(tmp_path):
    main = tmp_path / 'main'
    project(main, 'uid://main')
    edited = tmp_path / 'worktree'
    target = project(edited, 'uid://worktree')
    target.write_text(TEXT)
    result = invoke({'tool_name': 'Edit', 'cwd': str(edited / 'Data'), 'tool_input': {
        'file_path': 'probe.tres', 'old_string': 'value = 1', 'new_string': 'value = 2'}}, main)
    assert 'uid="uid://worktree"' in result['updatedInput']['new_string']


def test_unknown_project_preserves_authored_uid_and_advises(tmp_path):
    main = tmp_path / 'main'
    project(main, 'uid://main')
    result = invoke({'tool_name': 'Write', 'cwd': str(tmp_path), 'tool_input': {
        'file_path': str(tmp_path / 'orphan.tres'), 'content': TEXT}}, main)
    fixed = result.get('updatedInput', {}).get('content', TEXT)
    assert 'uid="uid://authored"' in fixed
    assert 'uid://main' not in fixed
    assert 'project root' in result.get('additionalContext', '')
    assert 'permissionDecision' not in result


def test_crlf_edit_matches_live_client_normalization(tmp_path):
    # Captured live Edit probe, session 10749e62-d94f-4520-a85f-12bcd127afe9.
    original = '[gd_resource load_steps=1 type="Resource" format=3]\n\n[resource]\nvalue = 1\n'
    target = project(tmp_path / 'project', 'uid://probe')
    target.write_bytes(original.replace('\n', '\r\n').encode())
    result = guard.process({'tool_name': 'Edit', 'tool_input': {
        'file_path': str(target), 'old_string': 'value = 1', 'new_string': 'value = 2', 'replace_all': False}})
    updated = result['updatedInput']
    assert updated['old_string'] == original
    assert updated['new_string'] == '[gd_resource type="Resource" format=3]\n\n[resource]\nvalue = 2\n'
    assert updated['replace_all'] is False
    assert target.read_bytes().count(b'\r\n') == 4
