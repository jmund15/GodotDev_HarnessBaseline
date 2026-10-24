#!/usr/bin/env python3
"""
Hook: PreToolUse - Enforce coding patterns before code is written

Applies computed attributes through PreToolUse updatedInput:
- [RequiredExport] for non-null Export initializers
- [Tool] for GlobalClass Resources

Advises on GetNode in _Process: judge whether it runs each frame on a stable path.
Blocks remaining violations:
- No GD.Print / GD.PrintErr (use JmoLogger; #if TOOLS editor code exempt)

Shell commands are not judged here: `dangerous_shell_guard` is the one destructive-command check.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_scope import harness_tail  # noqa: E402


# Patterns to BLOCK (exit code 2) in code content
CODE_BLOCKED_PATTERNS = [
    (r'GD\.Print(?:Err)?\s*\(', 'BLOCKED (rules/csharp_patterns.md): Use JmoLogger (Debug/Info/Warning/Error) instead of GD.Print / GD.PrintErr'),
]

# Pattern for GetNode in _Process (multi-line detection)
PROCESS_GETNODE_PATTERN = r'(override\s+void\s+_Process|public\s+override\s+void\s+_Process)[^}]*GetNode\s*[<(]'

_TOOLS_OPEN = re.compile(r'^[ \t]*#if[ \t]+TOOLS\b', re.MULTILINE)
_DIRECTIVE = re.compile(r'^[ \t]*#(if|elif|else|endif)\b', re.MULTILINE)


def editor_only_spans(content: str) -> list[tuple[int, int]]:
    """Character spans of `#if TOOLS` bodies: each ends at its `#else`/`#elif`/`#endif`, or at the end of
    the content when unterminated (an Edit snippet that opens the block for the code it replaces)."""
    spans = []
    for opener in _TOOLS_OPEN.finditer(content):
        depth = 1
        end = len(content)
        for directive in _DIRECTIVE.finditer(content, opener.end()):
            kind = directive.group(1)
            if kind == 'if':
                depth += 1
            elif kind == 'endif':
                depth -= 1
            elif depth == 1:
                depth = 0
            if depth == 0:
                end = directive.start()
                break
        spans.append((opener.end(), end))
    return spans


def is_test_fixture(file_path: str) -> bool:
    """Check if the file is an agent test fixture (intentionally contains violations)."""
    normalized = file_path.replace('\\', '/')
    return '.claude/tests/agent_fixtures/' in normalized


def check_code_patterns(content: str, file_path: str = "") -> tuple[bool, str]:
    """Return (blocked, message); a non-blocking message is advice for the edit."""

    # The rules guard C#; a Markdown or Python file may quote a violation.
    if file_path and not file_path.lower().endswith('.cs'):
        return False, ""

    # Skip test fixtures — they intentionally contain violations
    if is_test_fixture(file_path):
        return False, ""

    editor_spans = editor_only_spans(content)

    # Check standard blocked patterns
    for pattern, message in CODE_BLOCKED_PATTERNS:
        for match in re.finditer(pattern, content):
            # Allow GD.Print only inside a `#if TOOLS` body
            if 'GD.Print' in message and any(lo <= match.start() < hi for lo, hi in editor_spans):
                continue
            return True, message

    # Check for GetNode inside _Process (needs multiline)
    if re.search(PROCESS_GETNODE_PATTERN, content, re.DOTALL | re.MULTILINE):
        return False, ('Advisory (rules/csharp_patterns.md): GetNode in _Process — does this lookup run '
                       'each frame on a stable path? If so, cache references in _Ready().')

    return False, ""


# --- [Tool] cascade enforcement (Tool Attribute Audit policy: blanket-on-Resources) ---
# A [GlobalClass] Resource MUST be [Tool] — otherwise the editor loads instances as bare
# Godot.Resource and the auto-generated setter throws InvalidCastException. This is the
# edit-time half of the gate; the project-wide static detector + headless-import gate
# (in /regression_gate via tool_cascade_audit.py) are the authoritative backstops.
_TOOL_TOK = re.compile(r"(?<![A-Za-z_])Tool(?![A-Za-z_])")
_GC_TOK = re.compile(r"(?<![A-Za-z_])GlobalClass(?![A-Za-z_])")
_ATTR_LINE = re.compile(r"^\s*\[")
_CLASS_DECL = re.compile(
    r"^\s*(?:public|internal|private|protected|abstract|sealed|static|partial|new|file|\s)*"
    r"\b(?:class|record)\s+([A-Za-z_]\w*)\s*(<[^>{]*>)?\s*:\s*([^{]+)")


def _load_resource_classes() -> set:
    """Resource-rooted class names emitted by tool_cascade_audit.py. Lets the hook flag a
    class declared `: AbilityEffect` (an indirect Resource base), not just `: Resource`.
    Missing file → empty set → graceful fallback to direct `: Resource` detection only."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "tool_resource_classes.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return {ln.strip() for ln in f
                    if ln.strip() and not ln.lstrip().startswith("#")}
    except OSError:
        return set()


def _first_base(base_str: str) -> str:
    """First top-level base token, stripped of generics + namespace qualifier."""
    depth, cur = 0, []
    for ch in base_str:
        if ch == "<":
            depth += 1
            cur.append(ch)
        elif ch == ">":
            depth -= 1
            cur.append(ch)
        elif (ch == "," and depth == 0) or ch == "{":
            break
        else:
            cur.append(ch)
    return "".join(cur).split("<")[0].strip().split(".")[-1].strip()


def check_tool_cascade(content: str, file_path: str) -> tuple[bool, str]:
    """Flag a [GlobalClass] Resource declaration that is missing [Tool]."""
    norm = file_path.replace("\\", "/")
    if not norm.endswith(".cs"):
        return False, ""
    # Jmodot is a black-box framework (paired-PR only); Tests are throwaway fixtures.
    if ("/Jmodot/" in norm or "/Tests/" in norm or "/addons/" in norm
            or harness_tail(norm) is not None):
        return False, ""
    resource_classes = _load_resource_classes()
    lines = content.split("\n")
    for i, ln in enumerate(lines):
        m = _CLASS_DECL.match(ln)
        if not m:
            continue
        if m.group(2):  # generic class — Godot can't register/serialize it as [Tool]/.tres
            continue
        name = m.group(1)
        base = _first_base(m.group(3))
        if not (base == "Resource" or base in resource_classes):
            continue
        # Scan the attribute block directly above for [GlobalClass] / [Tool].
        has_gc = has_tool = False
        j = i - 1
        while j >= 0:
            up, s = lines[j], lines[j].strip()
            if _ATTR_LINE.match(up):
                has_gc = has_gc or bool(_GC_TOK.search(up))
                has_tool = has_tool or bool(_TOOL_TOK.search(up))
                j -= 1
            elif s == "" or s.startswith(("//", "/*", "*")):
                j -= 1
            else:
                break
        if has_gc and not has_tool:
            return True, (
                f"BLOCKED (rules/csharp_patterns.md): [GlobalClass] Resource '{name}' (: {base}) is missing [Tool]. "
                "Godot's [Tool] cascade requires every [GlobalClass] Resource to be [Tool] — "
                "otherwise the editor loads instances as bare Godot.Resource and the "
                "auto-generated setter throws InvalidCastException at load. "
                "Use: [GlobalClass, Tool]. (Policy: Tool Attribute Audit; full detector: "
                ".claude/hooks/tool_cascade_audit.py)")
    return False, ""


_NON_CODE = re.compile(r'//[^\n]*|/\*.*?\*/|@"(?:""|[^"])*"|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_ATTRS_DECL = re.compile(r'(?P<attrs>(?:\[[^\]]+\]\s*)+)', re.M)
_RESOURCE_CLASSES = frozenset(_load_resource_classes())


def fixes(content: str, path: str) -> tuple[str, list[str]]:
    """Compute attributes without changing optional exports, Node types or quoted examples."""
    if not path.lower().endswith('.cs') or is_test_fixture(path):
        return content, []
    code = _NON_CODE.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group()), content)
    norm = path.replace('\\', '/')
    resource_scope = not (harness_tail(norm) is not None
                          or any(part in norm for part in ('/Jmodot/', '/Tests/', '/addons/')))
    edits, notes = [], []
    for match in _ATTRS_DECL.finditer(code):
        attrs = match.group('attrs')
        tail = code[match.end():]
        member = re.match(r'(?:[^;{}]|\{[^{}]*\})+', tail)
        decl = member.group() if member else ''
        # An accessor block with no initializer after it ends the member; the next member's `= null!` is not its own.
        accessors = re.search(r'\{[^{}]*\}(?!\s*=)', decl)
        if accessors:
            decl = decl[:accessors.end()]
        export = re.search(r'\bExport\b', attrs)
        if (export and not re.search(r'\bRequiredExport\b', attrs)
                and re.search(r'=\s*null\s*!', decl)
                and not re.search(r'\?\s+\w+\s*(?:\{|=)', decl)):
            at = match.start('attrs') + export.end()
            # An Export argument belongs to Export, not RequiredExport.
            if code[at:at + 1] == '(':
                depth, pos = 1, at + 1
                while pos < match.end('attrs') and depth:
                    depth += (code[pos] == '(') - (code[pos] == ')')
                    pos += 1
                if depth:
                    continue
                at = pos
            edits.append((at, ', RequiredExport'))
            notes.append('Added RequiredExport: ValidateRequiredExports() checks this non-null export. '
                         'Write an optional export as T? (rules/csharp_patterns.md).')
        gc = _GC_TOK.search(attrs)
        class_decl = _CLASS_DECL.match(decl)
        if resource_scope and gc and not _TOOL_TOK.search(attrs) and class_decl and not class_decl.group(2):
            base = _first_base(class_decl.group(3))
            if base == 'Resource' or base in _RESOURCE_CLASSES:
                edits.append((match.start('attrs') + gc.end(), ', Tool'))
                notes.append('Added Tool to the GlobalClass Resource (rules/csharp_patterns.md).')
    for at, text in sorted(edits, reverse=True):
        content = content[:at] + text + content[at:]
    return content, list(dict.fromkeys(notes))


def _content_fix(input_data):
    tool = input_data.get('tool_name')
    ti = input_data.get('tool_input') or {}
    key = 'content' if tool == 'Write' else 'new_string'
    content = ti.get(key, '')
    path = ti.get('file_path', '')
    fixed, notes = fixes(content, path)
    updated = dict(ti, **{key: fixed}) if fixed != content else None
    if tool == 'Edit' and path.lower().endswith('.cs'):
        try:
            with open(path, encoding='utf-8') as handle:
                original = handle.read()
            old = ti.get('old_string', '')
            count = original.count(old) if old else 0
            if count and (count == 1 or ti.get('replace_all')):
                candidate = original.replace(old, content, -1 if ti.get('replace_all') else 1)
                corrected, full_notes = fixes(candidate, path)
                if corrected != candidate:
                    updated = dict(ti, old_string=original, new_string=corrected, replace_all=False)
                    notes = full_notes
        except OSError:
            pass
    return updated, notes


def _edit_is_editor_only(file_path: str, old_string: str, replace_all) -> bool:
    """True when the Edit replaces text only inside `#if TOOLS` bodies of the file on disk."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
    except (OSError, IOError):
        return False
    if not old_string:
        return False
    spans = editor_only_spans(file_content)
    starts, at = [], file_content.find(old_string)
    while at != -1:
        starts.append(at)
        if not replace_all:
            break
        at = file_content.find(old_string, at + 1)
    return bool(starts) and all(any(lo <= at < hi for lo, hi in spans) for at in starts)


def process(input_data):
    """Dispatcher entry: `{"block": <message>}` when the call must be denied, else None.

    Self-gates on tool_name, so a union matcher is safe. `pre_edit_dispatch.py` calls this;
    `main()` keeps the standalone channel.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    if tool_name == "Write":
        content = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "")

        blocked, message = check_code_patterns(content, file_path)
        if blocked:
            return {"block": message}

        updated, notes = _content_fix(input_data)
        if message:
            notes.insert(0, message)
        if updated or notes:
            result = {'context': '\n'.join(notes)}
            if updated:
                result['updatedInput'] = updated
            return result

    elif tool_name == "Edit":
        new_string = tool_input.get("new_string", "")
        file_path = tool_input.get("file_path", "")

        # The new_string snippet carries no `#if TOOLS` of its own: it is editor-only when every
        # place it replaces sits inside a `#if TOOLS` body of the file.
        edit_context = new_string
        if _edit_is_editor_only(file_path, tool_input.get("old_string", ""), tool_input.get("replace_all")):
            edit_context = '#if TOOLS\n' + new_string

        blocked, message = check_code_patterns(edit_context, file_path)
        if blocked:
            return {"block": message}

        updated, notes = _content_fix(input_data)
        if message:
            notes.insert(0, message)
        if updated or notes:
            result = {'context': '\n'.join(notes)}
            if updated:
                result['updatedInput'] = updated
            return result

    return None


def main():
    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid JSON, allow through
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)

    result = process(input_data)
    if result and result.get("block"):
        print(result["block"], file=sys.stderr)
        sys.exit(2)

    if result:
        out = {'hookEventName': 'PreToolUse', 'additionalContext': result['context']}
        if result.get('updatedInput'):
            out['updatedInput'] = result['updatedInput']
        print(json.dumps({'hookSpecificOutput': out}))
        sys.exit(0)

    # All checks passed
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
