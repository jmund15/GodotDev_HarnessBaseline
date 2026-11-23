"""One shell lexer: no hook reads shell text with its own splitter (`hooks/_shell_lex.py` owns it).

An `ast` scan of `hooks/*.py` fails on `shlex.split`, on `re.split` whose pattern names a shell operator, and on
`str.split` of a literal `&&`, `|` or `;`. `shlex.quote` and `shlex.join` build text and stay allowed.
"""
import ast
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
OWNER = "_shell_lex.py"
# file name -> why it still parses shell text itself; empty when every hook reads through the lexer.
ALLOWED: dict[str, str] = {}
OPERATORS = ("&&", "|", ";")


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def parsers(source):
    """(line, what) of each hand-rolled shell split in `source`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        first = _literal(node.args[0]) if node.args else None
        if name == "shlex.split":
            found.append((node.lineno, name))
        elif name == "re.split" and first is not None and any(op in first for op in OPERATORS):
            found.append((node.lineno, f"re.split({first!r})"))
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "split" and first in OPERATORS:
            found.append((node.lineno, f"str.split({first!r})"))
    return found


def test_scan_sees_each_banned_form():
    src = "\n".join(["import shlex, re", "shlex.split(c)", "re.split(r'[;|]', t)", "c.split('&&')", "c.split('|')",
                     "c.split(';')", "shlex.quote(c)", "shlex.join(c)", "c.split()", "c.split(',')"])
    assert [line for line, _ in parsers(src)] == [2, 3, 4, 5, 6]


def test_no_hook_parses_shell_text_outside_the_lexer():
    offenders = {}
    for path in sorted(HOOKS.glob("*.py")):
        if path.name == OWNER or path.name in ALLOWED:
            continue
        found = parsers(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = found
    assert not offenders, f"read shell text through hooks/_shell_lex.py: {offenders}"


def test_allow_list_names_live_files_that_still_offend():
    for name in ALLOWED:
        assert parsers((HOOKS / name).read_text(encoding="utf-8")), f"{name} is clean: drop it from ALLOWED"
