"""One shell lexer: no hook reads shell text with its own splitter (`hooks/_shell_lex.py` owns it).

An `ast` scan of `hooks/*.py` fails on `shlex.split`, and, in a module that reads a tool call's command, on `re.split`
whose pattern names a shell operator and on `str.split` of a literal `&&`, `|` or `;`. `shlex.quote` and `shlex.join`
build text and stay allowed.
"""
import ast
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
OWNER = "_shell_lex.py"
# hook name -> why it still parses shell text itself; empty when every hook reads through the lexer. The loss floor
# (dangerous_shell_guard, kill_guard, _git_commit, _delete_rule, _own_worktree, _shell_writes, _command_text) reads
# only through it; these v1 chain members and helpers were not migrated by the floor port (b2), each named with why.
ALLOWED: dict[str, str] = {
    "approving_gates": "shlex-splits a gate call to approve it; a v1 approver, not a loss check",
    "compound_cd_approver": "shlex-splits each cd-compound segment; v2 migrated it with its check(payload) form "
                            "(2cacd3e), which v1's main() member does not take",
    "foreground_wait_guard": "splits commands into stages and wait conditions with re/shlex; v2 migrated it with "
                             "its check(payload) form (2cacd3e), and b1 merged two callers' contract into v1's",
    "merge_build_check": "splits the configured build command into argv to run it; it judges no tool call",
    "model_ladder_gate": "splits a Bash call at operators to find a dispatch launch (the sidecar dispatch gate)",
    "running_script_edit_guard": "splits process-table rows and their command lines; v2 migrated it (0c57747)",
    "unbounded_scan_guard": "its volume and scope advisories read scan paths with shlex; its recursive-grep deny "
                            "reads through the lexer",
}
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


def _reads_command(tree):
    """True when the module reads a tool call's command: `x["command"]` or `x.get("command")`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _literal(node.slice) == "command":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and node.args and _literal(node.args[0]) == "command":
            return True
    return False


def parsers(source):
    """(line, what) of each hand-rolled shell split in `source`. `shlex.split` is shell parsing wherever it runs. A
    split at an operator is shell parsing only in a module that reads a tool call's command; one that never does
    (a markdown table row, a model list) splits some other text."""
    tree = ast.parse(source)
    shell = _reads_command(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        first = _literal(node.args[0]) if node.args else None
        if name == "shlex.split":
            found.append((node.lineno, name))
        elif name == "re.split" and shell and first is not None and any(op in first for op in OPERATORS):
            found.append((node.lineno, f"re.split({first!r})"))
        elif shell and isinstance(node.func, ast.Attribute) and node.func.attr == "split" and first in OPERATORS:
            found.append((node.lineno, f"str.split({first!r})"))
    return found


def test_scan_sees_each_banned_form():
    src = "\n".join(["import shlex, re", "shlex.split(c)", "re.split(r'[;|]', t)", "c.split('&&')", "c.split('|')",
                     "c.split(';')", "shlex.quote(c)", "shlex.join(c)", "c.split()", "c.split(',')",
                     "c = payload['tool_input']['command']"])
    assert [line for line, _ in parsers(src)] == [2, 3, 4, 5, 6]


def test_a_splitter_is_shell_parsing_only_where_a_command_is_read():
    table = "\n".join(["row = [cell for cell in line.strip('|').split('|')]", "re.split(r'[;|]', row)",
                       "parts = text.split(';')"])
    assert parsers(table) == []
    assert [what for _, what in parsers("import shlex\nshlex.split(row)\n" + table)] == ["shlex.split"]
    assert len(parsers(table + "\nc = tool_input.get('command')")) == 3


def test_no_hook_parses_shell_text_outside_the_lexer():
    offenders = {}
    for path in sorted(HOOKS.glob("*.py")):
        if path.name == OWNER or path.stem in ALLOWED:
            continue
        found = parsers(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = found
    assert not offenders, f"read shell text through hooks/_shell_lex.py: {offenders}"


def test_allow_list_names_live_files_that_still_offend():
    # A name whose file this checkout lacks belongs to a layer the consumer did not take (merge_build_check: godot).
    for name in ALLOWED:
        path = HOOKS / (name + ".py")
        assert not path.exists() or parsers(path.read_text(encoding="utf-8")), f"{name} is clean: drop it from ALLOWED"
