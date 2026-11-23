"""The floor's project-member slot: `adaptation.json` `hook_members.pre_bash` names project modules whose
`check(payload)` the floor runs after its shared members. A listed member runs and its deny denies; an unlisted
module never runs; a listed member that cannot import or raises denies a shell call (the floor's fail posture)."""
import json
import sys

import pytest

import _optional_hooks
from case import bash, decide

DENIER = ('def check(payload):\n'
          '    open(__file__ + ".ran", "a").close()\n'
          '    if "FORBIDDEN" in payload["tool_input"].get("command", ""):\n'
          '        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",\n'
          '                "permissionDecisionReason": "project guard"}}\n')


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A planted `.claude` whose hooks dir the floor's loader reads; `plant(listed, **modules)` writes it."""
    claude = tmp_path / ".claude"
    (claude / "hooks").mkdir(parents=True)
    (claude / "skills" / "project_subsystems").mkdir(parents=True)
    monkeypatch.setattr(_optional_hooks, "HOOKS_DIR", claude / "hooks")
    monkeypatch.syspath_prepend(str(claude / "hooks"))

    def plant(listed, **modules):
        for name, text in modules.items():
            (claude / "hooks" / (name + ".py")).write_text(text, encoding="utf-8", newline="\n")
        (claude / "skills" / "project_subsystems" / "adaptation.json").write_text(
            json.dumps({"hook_members": {"pre_bash": listed}}), encoding="utf-8")
        return claude / "hooks"
    yield plant
    for name in ("proj_guard", "proj_broken", "proj_raises", "proj_unlisted"):
        sys.modules.pop(name, None)


def test_a_listed_project_member_runs_and_its_deny_denies(project):
    hooks = project(["proj_guard"], proj_guard=DENIER)
    assert bash("echo FORBIDDEN").denied
    assert (hooks / "proj_guard.py.ran").exists()
    assert not bash("echo fine").denied


def test_an_unlisted_module_never_runs(project):
    hooks = project(["proj_guard"], proj_guard=DENIER, proj_unlisted=DENIER)
    assert bash("echo FORBIDDEN").denied
    assert not (hooks / "proj_unlisted.py.ran").exists()


def test_a_listed_member_that_cannot_import_denies_a_shell_call_only(project):
    project(["proj_broken"], proj_broken="import module_that_does_not_exist\n")
    assert bash("echo fine").denied
    assert not decide({"tool_name": "Monitor", "tool_input": {"command": "echo fine"},
                       "hook_event_name": "PreToolUse"}).denied


def test_a_listed_member_that_raises_denies(project):
    project(["proj_raises"], proj_raises="def check(payload):\n    raise RuntimeError('boom')\n")
    assert bash("echo fine").denied


def test_a_listed_member_with_no_file_is_skipped(project):
    project(["proj_gone"])
    assert not bash("echo fine").denied
