"""One shell lexer: no hook reads shell text with its own splitter (`hooks/_shell_lex.py` owns it).

An `ast` scan of `hooks/*.py` fails on `shlex.split`, on `re.split` whose pattern names a shell operator, and on
`str.split` of a literal `&&`, `|` or `;`. `shlex.quote` and `shlex.join` build text and stay allowed.
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
    "hand_pricing_advisory": "unwired by M1 (pre_bash_dispatch UNWIRED); its deletion waits on an origin check",
    "merge_build_check": "splits the configured build command into argv to run it; it judges no tool call",
    "model_ladder_gate": "splits a Bash call at operators to find a dispatch launch (the sidecar dispatch gate)",
    "model_name_guard": "splits a model-name list in written content at spaces, `|` and commas; not shell text",
    "rail_probe_guard": "splits a Bash call at operators to find a rail probe; a v1 process guard, not a loss check",
    "running_script_edit_guard": "splits process-table rows and their command lines; v2 migrated it (0c57747)",
    "task_brief_facts": "splits a brief's fact line at `;`; not shell text",
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
