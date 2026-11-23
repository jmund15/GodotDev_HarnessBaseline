"""Re-runnable proof for hooks/compound_cd_approver.py's `adaptation.json` `read_only_commands`
seam (Design Doc §8).

Three shapes: adaptation.json absent -> the built-in SAFE_SEGMENT_COMMANDS only; present with
a valid entry -> that command joins the allowlist; a wrong-typed `read_only_commands` (not a
list) -> the default (built-in set only) plus one stderr line. Validation cases: an entry
matching NEVER_SAFE (e.g. "rm") is rejected and never joins the allowlist; an entry failing
`^[a-z0-9_.-]+$` is rejected too. Each case loads the hook copy from a
scratch `.claude` tree (`_adaptation_fixture`) through `check(payload)`, the entry the floor calls.

    python -m pytest .claude/tests/test_compound_cd_approver_adaptation.py -q
"""
import contextlib
import importlib.util
import io
import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _adaptation_fixture as fx  # noqa: E402

HOOK_NAME = "compound_cd_approver.py"
REAL_HOOK = os.path.join(fx.REAL_HOOKS, HOOK_NAME)
DOTNET = "cd /repo && dotnet build"


_COPIES = itertools.count()


def run_hook(hook_path, command):
    """(stdout JSON text, stderr text, 0) of `check` on a Bash payload, from the hook copy at `hook_path`."""
    spec = importlib.util.spec_from_file_location("cd_approver_copy_%d" % next(_COPIES), hook_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = module.check({"tool_name": "Bash", "tool_input": {"command": command}})
    return (json.dumps(out) if out else ""), err.getvalue().strip(), 0


def is_allowed(stdout):
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision") == "allow"
    except (json.JSONDecodeError, AttributeError):
        return False


@pytest.fixture
def adapted():
    """Make a scratch `.claude` tree seeded with an adaptation.json (or none); returns the copied hook's path."""
    made = []

    def make(seed):
        tree = fx.make_scratch(HOOK_NAME, REAL_HOOK, seed=seed, extra_hook_files=("_shell_lex.py",))
        made.append(tree)
        return os.path.join(tree, "hooks", HOOK_NAME)

    yield make
    for tree in made:
        fx.rm(tree)


def test_an_absent_adaptation_json_does_not_auto_approve_an_unlisted_command(adapted):
    out, _err, _rc = run_hook(adapted(None), DOTNET)
    assert not is_allowed(out)


def test_a_valid_read_only_entry_is_auto_approved(adapted):
    out, _err, _rc = run_hook(adapted({"read_only_commands": ["dotnet"]}), DOTNET)
    assert is_allowed(out)


def test_a_valid_read_only_entry_writes_no_stderr(adapted):
    _out, err, _rc = run_hook(adapted({"read_only_commands": ["dotnet"]}), DOTNET)
    assert err == ""


def test_a_builtin_command_still_auto_approves_alongside_a_project_entry(adapted):
    out, _err, _rc = run_hook(adapted({"read_only_commands": ["dotnet"]}), "cd /repo && git status")
    assert is_allowed(out)


def test_a_wrong_typed_read_only_commands_never_joins_the_allowlist(adapted):
    out, _err, _rc = run_hook(adapted({"read_only_commands": "dotnet"}), DOTNET)
    assert not is_allowed(out)


def test_a_wrong_typed_read_only_commands_writes_one_stderr_line_naming_the_key(adapted):
    _out, err, _rc = run_hook(adapted({"read_only_commands": "dotnet"}), DOTNET)
    assert err.count("\n") == 0 and "read_only_commands" in err


def test_never_safe_rm_is_never_added_to_the_allowlist(adapted):
    out, _err, _rc = run_hook(adapted({"read_only_commands": ["rm"]}), "cd /repo && rm -rf x")
    assert not is_allowed(out)


def test_the_never_safe_rejection_names_the_entry_on_stderr(adapted):
    _out, err, _rc = run_hook(adapted({"read_only_commands": ["rm"]}), "cd /repo && rm -rf x")
    assert "rm" in err


def test_the_shape_regex_rejects_an_entry_with_a_space_or_uppercase(adapted):
    out, err, _rc = run_hook(adapted({"read_only_commands": ["Dotnet Build"]}), DOTNET)
    assert not is_allowed(out) and "read_only_commands" in err


# An allow covers the whole command, so none of these carriers may ride it.
CARRIERS = [
    ("`$(...)` falls through", "cd . && git log $(python3 evil.py)"),
    ("backticks fall through", "cd . && git log `python3 evil.py`"),
    ("a write redirection falls through", "cd . && echo x > .claude/settings.json"),
    ("a lone `&` falls through", "cd . && ls & rm -rf x"),
    ("a newline falls through", "cd . && ls\nrm -rf x"),
    ("`git commit` falls through", "cd . && git commit -am x"),
    ("`git push` falls through", "cd . && git push --force origin main"),
    ("`git -c` falls through", "cd . && git -c alias.x=!sh x"),
    ("`git diff --output` falls through", "cd . && git diff --output=.claude/settings.json"),
    ("`find -delete` falls through", "cd . && find . -delete"),
    ("`find -exec` falls through", "cd . && find . -exec rm {} +"),
    ("`sort -o` falls through", "cd . && sort -o .claude/settings.json x"),
]

READ_ONLY = [
    ("read-only git still auto-approves", "cd /repo && git log --oneline -5"),
    ("a read-only pipe still auto-approves", "cd /repo && git status --short | head -5"),
    ("quoted grep still auto-approves", "cd /repo && grep -n 'a b' x.py"),
    ("a separator inside quotes is not a segment", "cd /repo && grep -n 'a && rm b' x.py"),
]


@pytest.mark.parametrize("command", [c for _, c in CARRIERS], ids=[label for label, _ in CARRIERS])
def test_a_carrier_is_not_auto_approved(command):
    out, _err, _rc = run_hook(REAL_HOOK, command)
    assert not is_allowed(out)


@pytest.mark.parametrize("command", [c for _, c in READ_ONLY], ids=[label for label, _ in READ_ONLY])
def test_a_read_only_command_is_auto_approved(command):
    out, _err, _rc = run_hook(REAL_HOOK, command)
    assert is_allowed(out)


def test_a_non_bash_tool_and_a_non_cd_command_return_nothing():
    for payload in ({"tool_name": "PowerShell", "tool_input": {"command": "cd /repo && git status"}},
                    {"tool_name": "Bash", "tool_input": {"command": "git status"}},
                    {"tool_name": "Bash", "tool_input": {}}):
        spec = importlib.util.spec_from_file_location("cd_approver_none", REAL_HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.check(payload) is None
