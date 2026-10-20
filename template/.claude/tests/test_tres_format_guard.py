#!/usr/bin/env python3
"""PreToolUse canonical .tres fixes, ordered references, and standalone channel."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOOK = Path(__file__).resolve().parents[1] / 'hooks' / 'tres_format_guard.py'
spec = importlib.util.spec_from_file_location('tres_format_proof', HOOK)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
BAD = '''[gd_resource uid="uid://own" load_steps=9 type="Resource" format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
'''
CLEAN = '''[gd_resource type="Resource" script_class="Probe" format=3 uid="uid://own"]

[ext_resource type="Script" uid="uid://probe" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
'''


def payload(path, content, tool='Write', **extra):
    return {'tool_name': tool, 'hook_event_name': 'PreToolUse', 'tool_input':
            dict(file_path=str(path), content=content, **extra)}


def main():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'Scripts').mkdir()
        (root / 'Scripts/Probe.cs').write_text('public partial class Probe : Resource {}')
        (root / 'Scripts/Probe.cs.uid').write_text('uid://probe\n')
        (root / 'project.godot').write_text('[application]\n')
        path = root / 'probe.tres'
        result = g.process(payload(path, BAD)) or {}
        cases.append(('Write canonicalizes header and target uid',
                      (result.get('updatedInput') or {}).get('content') == CLEAN))
        cases.append(('computed fixes are silent', not result.get('context')))
        cases.append(('canonical input emits nothing', g.process(payload(path, CLEAN)) is None))
        wrong_uid = CLEAN.replace('uid://probe', 'uid://stale')
        result = g.process(payload(path, wrong_uid)) or {}
        cases.append(('stale ext uid is replaced from companion',
                      (result.get('updatedInput') or {}).get('content') == CLEAN))
        path.write_text(BAD)
        ti = {'file_path': str(path), 'old_string': 'value = 1', 'new_string': 'value = 2'}
        result = g.process({'tool_name': 'Edit', 'tool_input': ti}) or {}
        updated = result.get('updatedInput') or {}
        cases.append(('Edit candidate canonicalized outside fragment',
                      updated.get('old_string') == BAD and updated.get('new_string') == CLEAN.replace('value = 1', 'value = 2')))
        cases.append(('input is not mutated', ti['old_string'] == 'value = 1'))
        real_read, reads = Path.read_text, []
        def racing_read(target, *args, **kwargs):
            if target == path:
                reads.append(1)
                return BAD if len(reads) == 1 else BAD.replace('value = 1', 'value = 99')
            return real_read(target, *args, **kwargs)
        Path.read_text = racing_read
        try:
            result = g.process({'tool_name': 'Edit', 'tool_input': ti}) or {}
        finally:
            Path.read_text = real_read
        cases.append(('Edit binds the candidate to the same disk snapshot',
                      result.get('updatedInput', {}).get('old_string') == BAD and len(reads) == 1))
        forward = '[gd_resource type="Resource" format=3]\n\n[sub_resource type="Resource" id="A"]\nx = SubResource("B")\n[sub_resource type="Resource" id="B"]\n[resource]\nx = ExtResource("gone")\n'
        result = g.process(payload(path, forward)) or {}
        ctx = result.get('context', '')
        cases.append(('forward SubResource and dangling ExtResource reported',
                      'forward SubResource("B")' in ctx and 'dangling ExtResource("gone")' in ctx))
        backward = forward.replace('x = SubResource("B")\n', '').replace('x = ExtResource("gone")', 'x = SubResource("B")')
        cases.append(('declared references pass', g.process(payload(path, backward)) is None))
        foreign = CLEAN.replace('Scripts/Probe.cs', 'Jmodot/Probe.cs').replace(' uid="uid://probe"', '')
        cases.append(('foreign uids stay path-only', g.process(payload(path, foreign)) is None))
        # Captured from craft_idle_to_menu_transition.tres: cache-authored foreign uid.
        carried_foreign = '[gd_resource type="Resource" format=3]\n\n[ext_resource type="Script" uid="uid://bkqo1i81ek0je" path="res://Jmodot/Core/AI/HSM/TransitionCondition.cs" id="1_cond_base"]\n\n[resource]\n'
        cases.append(('existing foreign uid is not stripped without cache evidence',
                      g.process(payload(path, carried_foreign)) is None))
        result = g.process(payload(path, CLEAN.replace('Scripts/Probe.cs', 'Scripts/Missing.cs'))) or {}
        cases.append(('unreadable target gets context', 'Missing.cs' in result.get('context', '')))
        cases.append(('adjacent source path ignored', g.process(payload(root / 'probe.cs', BAD)) is None))
        cases.append(('PostToolUse is not a second writer', g.process(dict(payload(path, BAD), hook_event_name='PostToolUse')) is None))
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
        proc = subprocess.run([sys.executable, str(HOOK), '--hook'], input=json.dumps(payload(path, BAD)),
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=60)
        out = (json.loads(proc.stdout).get('hookSpecificOutput') or {}) if proc.stdout else {}
        cases.append(('standalone PreToolUse emits updatedInput only', proc.returncode == 0 and
                      out.get('hookEventName') == 'PreToolUse' and out.get('updatedInput', {}).get('content') == CLEAN
                      and 'additionalContext' not in out))
        proc = subprocess.run([sys.executable, str(HOOK), '--hook'], input='bad json', capture_output=True, text=True, timeout=60)
        cases.append(('malformed input does not crash', proc.returncode == 0 and proc.stdout.strip() == '{}'))
    for label, ok in cases:
        print(('PASS: ' if ok else 'FAIL: ') + label)
    failures = sum(not ok for _, ok in cases)
    print('%d/%d passed' % (len(cases) - failures, len(cases)))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
