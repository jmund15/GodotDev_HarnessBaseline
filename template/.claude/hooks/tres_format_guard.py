#!/usr/bin/env python3
"""PreToolUse Write|Edit: apply canonical resource text through updatedInput.

For .tres, write script_class from the root C# script, uid last and no load_steps.
External resource uids come from companions in the edited target's nearest
Godot project; an unknown project leaves authored uids unchanged and advises.
Jmodot stays path-only. Scene headers retain their scene attributes. Unreadable targets and
forward/dangling resource references get additionalContext, never a deny.
Computed fixes are silent. Owner: rules/godot_files.md §UID handling.
"""
import json
import pathlib
import re
import sys

SUBMODULE = 'Jmodot/'
_ATTR = re.compile(r'(\w+)\s*=\s*("(?:\\.|[^"\\])*"|[^\s\]]+)')
_BLOCK = re.compile(r'^\[(ext_resource|sub_resource)\b[^\n]*\]', re.M)
_REF = re.compile(r'\b(SubResource|ExtResource)\("([^"\n]+)"\)')


def project_root_for(path):
    try:
        target = pathlib.Path(path).resolve()
        return next((parent for parent in target.parents if (parent / 'project.godot').is_file()), None)
    except OSError:
        return None


def target_uid(rel_path: str, project_root: pathlib.Path) -> str | None:
    p = project_root / rel_path
    try:
        if rel_path.endswith('.cs'):
            uid = p.with_suffix('.cs.uid').read_text(encoding='utf-8-sig').strip()
            return uid if uid.startswith('uid://') else None
        if p.suffix in ('.tres', '.tscn'):
            with p.open(encoding='utf-8-sig') as handle:
                head = handle.readline()
            match = re.search(r'\buid="(uid://[^"\n]+)"', head)
            return match.group(1) if match else None
    except OSError:
        pass
    return None


def _attrs(line):
    return dict(_ATTR.findall(line))


def reference_issues(text):
    declarations = {}
    for match in _BLOCK.finditer(text):
        ident = _attrs(match.group()).get('id', '').strip('"')
        declarations[(match.group(1), ident)] = match.start()
    issues = []
    for match in _REF.finditer(text):
        if text[text.rfind('\n', 0, match.start()) + 1:match.start()].lstrip().startswith(';'):
            continue
        kind, ident = match.groups()
        key = ('sub_resource' if kind == 'SubResource' else 'ext_resource', ident)
        declared = declarations.get(key)
        if declared is None or declared > match.start():
            label = 'dangling' if declared is None else 'forward'
            issues.append('%s %s("%s") at line %d' %
                          (label, kind, ident, text.count('\n', 0, match.start()) + 1))
    return issues


def canonicalize(text, path, project_root=None):
    """Return corrected text and only the facts that cannot be fixed from authored data."""
    project_root = project_root or project_root_for(path)
    if not str(path).endswith(('.tres', '.tscn')):
        return text, []
    lines = text.splitlines(keepends=True)
    if not lines:
        return text, ['empty resource: no header to normalize']
    header = re.fullmatch(r'\[(gd_resource|gd_scene)\s+([^\n]*)\](\r?\n)?', lines[0])
    if not header:
        return text, ['unreadable resource header']
    issues = []
    ext = {}
    for line in lines:
        if line.startswith('[ext_resource '):
            attrs = _attrs(line)
            ext[attrs.get('id', '').strip('"')] = attrs
    attrs = _attrs(lines[0])
    attrs.pop('load_steps', None)
    if header.group(1) == 'gd_resource':
        root = re.search(r'^\[resource\]\r?\n(.*?)(?=^\[|\Z)', text, re.M | re.S)
        script = re.search(r'^script\s*=\s*ExtResource\("([^"\n]+)"\)', root.group(1), re.M) if root else None
        if script:
            target = ext.get(script.group(1), {})
            rel = target.get('path', '').strip('"')
            if rel.startswith('res://') and rel.endswith('.cs'):
                attrs['script_class'] = json.dumps(pathlib.PurePosixPath(rel).stem)
            else:
                issues.append('cannot compute script_class for root script ' + script.group(1))
    ordered = [k for k in ('type', 'script_class') if k in attrs]
    ordered += [k for k in attrs if k not in ('type', 'script_class', 'uid')]
    if 'uid' in attrs:
        ordered.append('uid')
    lines[0] = '[' + header.group(1) + ' ' + ' '.join(k + '=' + attrs[k] for k in ordered) + ']' + (header.group(3) or '')
    for i, line in enumerate(lines):
        if not line.startswith('[ext_resource '):
            continue
        attrs = _attrs(line)
        rel = attrs.get('path', '').strip('"')
        if not rel.startswith('res://'):
            continue
        rel = rel[6:]
        if rel.startswith(SUBMODULE):
            continue  # The foreign cache, not this checkout's companion, owns a carried uid.
        if project_root is None:
            issues.append('cannot resolve Godot project root for ' + str(path) + '; authored uids unchanged')
            continue
        uid = target_uid(rel, project_root)
        if uid:
            attrs['uid'] = json.dumps(uid)
        elif not (project_root / rel).exists():
            issues.append('cannot read ext_resource target ' + rel)
        order = [k for k in ('type', 'uid', 'path', 'id') if k in attrs]
        order += [k for k in attrs if k not in order]
        ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
        lines[i] = '[ext_resource ' + ' '.join(k + '=' + attrs[k] for k in order) + ']' + ending
    fixed = ''.join(lines)
    return fixed, list(dict.fromkeys(issues + reference_issues(fixed)))


def check_file(path_str):
    path = pathlib.Path(path_str)
    if path.suffix not in ('.tres', '.tscn') or not path.exists():
        return []
    original = path.read_text(encoding='utf-8-sig')
    fixed, issues = canonicalize(original, path)
    return (['non-canonical resource text'] if fixed != original else []) + issues


def process(data):
    if data.get('hook_event_name', 'PreToolUse') != 'PreToolUse':
        return None
    tool = data.get('tool_name')
    ti = data.get('tool_input') or {}
    if tool not in ('Write', 'Edit') or not isinstance(ti, dict):
        return None
    path = ti.get('file_path', '')
    if not path.endswith(('.tres', '.tscn')):
        return None
    target = pathlib.Path(path)
    if not target.is_absolute():
        cwd = data.get('cwd')
        if not cwd:
            return {'context': '[tres-format-guard] Cannot resolve Godot project root for relative target ' + path}
        target = pathlib.Path(cwd) / target
    if tool == 'Write':
        original = ti.get('content', '')
    else:
        try:
            original = target.read_text(encoding='utf-8')
        except OSError:
            return {'context': '[tres-format-guard] Cannot read Edit target ' + path}
        old = ti.get('old_string', '')
        count = original.count(old) if old else 0
        if not count or (count != 1 and not ti.get('replace_all')):
            return None  # The Edit tool owns its match error; do not make an invalid edit valid.
        on_disk = original
        original = original.replace(old, ti.get('new_string', ''), -1 if ti.get('replace_all') else 1)
    fixed, issues = canonicalize(original, target, project_root_for(target))
    result = {}
    if fixed != original:
        if tool == 'Write':
            result['updatedInput'] = dict(ti, content=fixed)
        else:
            result['updatedInput'] = dict(ti, old_string=on_disk, new_string=fixed, replace_all=False)
    if issues:
        result['context'] = '[tres-format-guard] ' + path + ' (rules/godot_files.md):\n' + '\n'.join(issues)
    return result or None


def hook():
    try:
        data = json.load(sys.stdin)
    except (ValueError, TypeError):
        print('{}')
        return 0
    result = process(data) or {}
    out = {'hookEventName': 'PreToolUse'}
    if result.get('updatedInput') is not None:
        out['updatedInput'] = result['updatedInput']
    if result.get('context'):
        out['additionalContext'] = result['context']
    print(json.dumps({'hookSpecificOutput': out}) if len(out) > 1 else '{}')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(hook() if '--hook' in sys.argv[1:] else 0)
