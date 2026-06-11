#!/usr/bin/env python3
"""
Hook: PreToolUse - Enforce coding patterns before code is written

Blocks violations of architecture rules from CLAUDE.md:
- No GD.Print (use JmoLogger; #if TOOLS editor code exempt)
- No GetNode in _Process (cache in _Ready)
- No [Export] = null! without [RequiredExport]
- No [GlobalClass] Resource missing [Tool] (edit-time half of the cascade gate)
- No dangerous shell commands (recursive deletes, format) — Bash AND PowerShell
"""

import json
import os
import re
import sys


# Patterns to BLOCK (exit code 2) in code content
CODE_BLOCKED_PATTERNS = [
    (r'GD\.Print\s*\(', 'BLOCKED: Use JmoLogger (Debug/Info/Warning/Error) instead of GD.Print'),
]

# Pattern for GetNode in _Process (multi-line detection)
PROCESS_GETNODE_PATTERN = r'(override\s+void\s+_Process|public\s+override\s+void\s+_Process)[^}]*GetNode\s*[<(]'

# Dangerous command patterns — applied to BOTH the Bash and PowerShell tools
# (PowerShell text also reaches Bash via `powershell -Command "..."`).
DANGEROUS_BASH_PATTERNS = [
    # Windows dangerous commands
    (r'del\s+/[sq]', 'BLOCKED: Dangerous recursive delete (del /s or /q)'),
    (r'rd\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rd /s)'),
    (r'rmdir\s+/[sq]', 'BLOCKED: Dangerous recursive directory removal (rmdir /s)'),
    (r'\bformat\s+[a-zA-Z]:', 'BLOCKED: Format drive command detected'),
    # PowerShell recursive delete — Remove-Item and its delete aliases with
    # -Recurse (PS requires at least -recurse-unambiguous spelling; -r alone
    # errors as ambiguous, so matching the full word keeps false positives low).
    (r'(?i)\b(remove-item|ri|del|erase)\b[^|;\n]*\s-recurse\b', 'BLOCKED: Dangerous recursive delete (Remove-Item -Recurse)'),
    # Block rm with any recursive flag cluster. Plain "rm -f <file>" is a safe
    # single-file delete (force = suppress prompt) and is commonly used for temp
    # cleanup, so it is NOT blocked. Recursion requires -r / -R / --recursive —
    # that is what we actually need to block. `git rm` stays allowed via the
    # lookbehind (version-controlled, recoverable).
    # The intermediate (?:[^\s]+\s+)* span catches separated-flag cases like
    # `rm -f -r dir/` or `rm foo -r` that a stricter prefix match would miss.
    (r'(?<!git\s)\brm\s+(?:[^\s]+\s+)*-[a-zA-Z]*[rR]', 'BLOCKED: Dangerous recursive delete (rm -r / -R / -rf)'),
    (r'(?<!git\s)\brm\s+--recursive\b', 'BLOCKED: Dangerous recursive delete (rm --recursive)'),
]


def is_editor_tooling_context(content: str, file_path: str) -> bool:
    """Check if the content is editor-only tooling code where GD.Print is acceptable."""
    # If the content or file contains #if TOOLS, it's editor tooling
    if '#if TOOLS' in content:
        return True
    # Jmodot tool code lives in Tools/ directories
    if 'Tools' in file_path and file_path.endswith('.cs'):
        return True
    return False


def is_test_fixture(file_path: str) -> bool:
    """Check if the file is an agent test fixture (intentionally contains violations)."""
    normalized = file_path.replace('\\', '/')
    return '.claude/tests/agent_fixtures/' in normalized


def check_code_patterns(content: str, file_path: str = "") -> tuple[bool, str]:
    """Check code content for blocked patterns. Returns (blocked, message)."""

    # Skip test fixtures — they intentionally contain violations
    if is_test_fixture(file_path):
        return False, ""

    is_editor = is_editor_tooling_context(content, file_path)

    # Check standard blocked patterns
    for pattern, message in CODE_BLOCKED_PATTERNS:
        # Allow GD.Print in editor tooling code (#if TOOLS)
        if 'GD.Print' in message and is_editor:
            continue
        if re.search(pattern, content):
            return True, message

    # Check for GetNode inside _Process (needs multiline)
    if re.search(PROCESS_GETNODE_PATTERN, content, re.DOTALL | re.MULTILINE):
        return True, 'BLOCKED: Never GetNode() in _Process - cache references in _Ready()'

    # Check for [Export] = null! without [RequiredExport]
    for line in content.split('\n'):
        if '= null!' in line and '[Export' in line and 'RequiredExport' not in line:
            return True, 'BLOCKED: [Export] with = null! must include [RequiredExport]. Use: [Export, RequiredExport]'

    return False, ""


def check_bash_command(command: str) -> tuple[bool, str]:
    """Check bash command for dangerous patterns. Returns (blocked, message)."""
    for pattern, message in DANGEROUS_BASH_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True, message
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
    class declared `: SpellEffect` (an indirect Resource base), not just `: Resource`.
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
    if "/Jmodot/" in norm or "/Tests/" in norm or "/.claude/" in norm or "/addons/" in norm:
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
                f"BLOCKED: [GlobalClass] Resource '{name}' (: {base}) is missing [Tool]. "
                "Godot's [Tool] cascade requires every [GlobalClass] Resource to be [Tool] — "
                "otherwise the editor loads instances as bare Godot.Resource and the "
                "auto-generated setter throws InvalidCastException at load. "
                "Use: [GlobalClass, Tool]. (Policy: Tool Attribute Audit; full detector: "
                ".claude/hooks/tool_cascade_audit.py)")
    return False, ""


def main():
    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid JSON, allow through
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Handle Write tool
    if tool_name == "Write":
        content = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "")

        blocked, message = check_code_patterns(content, file_path)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

        blocked, message = check_tool_cascade(content, file_path)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

    # Handle Edit tool
    elif tool_name == "Edit":
        new_string = tool_input.get("new_string", "")
        file_path = tool_input.get("file_path", "")

        # For Edit, also check if the target file itself is editor tooling
        # (the new_string snippet may not contain #if TOOLS, but the file does)
        edit_context = new_string
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                if '#if TOOLS' in file_content:
                    edit_context = '#if TOOLS\n' + new_string
        except (OSError, IOError):
            pass

        blocked, message = check_code_patterns(edit_context, file_path)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

        blocked, message = check_tool_cascade(new_string, file_path)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

    # Handle Bash + PowerShell tools (same dangerous-command surface)
    elif tool_name in ("Bash", "PowerShell"):
        command = tool_input.get("command", "")

        blocked, message = check_bash_command(command)
        if blocked:
            print(message, file=sys.stderr)
            sys.exit(2)

    # All checks passed
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
