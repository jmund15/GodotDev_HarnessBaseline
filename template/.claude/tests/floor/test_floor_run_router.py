"""`hooks/run_call_router.py`, a `_floor.HELPER_NAMES` helper: v1's `test_run_call_router.py` cases (59, through the
deleted `pre_bash_dispatch`) as rows through `_floor.decide`.

Two rewrites, both through `updatedInput`, never a permission decision:
1. Code from main, data from the target. A linked worktree's own `.claude/scripts/{run_test_suite,verify,
   regression_gate,run_integration_batched}.ps1` is frozen at its branch point, so an invocation of one runs main's copy
   with `-Repo <worktree>`. A read-only mention, main's own script, another repo's worktree and a submodule pass.
2. In a subagent or a `claude -p` child, a foreground test, build or gate call starts with run_in_background: true and
   a context line naming how to wait; a non-detached gate is backgrounded for the orchestrator too.
A planted linked worktree is a `.git` file whose gitdir sits under this checkout's `.git/worktrees/`.
"""
import json
import os

import pytest

import _floor
import run_call_router
from conftest import CLAUDE

MAIN = CLAUDE.parent.as_posix()
GATE = "regression_gate"
NAMES = ("run_test_suite", "verify", GATE, "run_integration_batched")


@pytest.fixture
def plant(tmp_path, monkeypatch, project_values):
    """Planted checkouts (a linked worktree of this checkout, another repo's worktree, a submodule) and a fresh hook
    state dir; the caller is the interactive session unless a row says otherwise."""
    def checkout(name, gitdir):
        root = tmp_path / name
        (root / ".claude" / "scripts").mkdir(parents=True)
        (root / ".git").write_text("gitdir: %s\n" % gitdir, encoding="utf-8")
        return root.as_posix()
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    return {"wt": checkout("wt-pr101", MAIN + "/.git/worktrees/wt-pr101"),
            "foreign": checkout("foreign-wt", "C:/elsewhere/other/.git/worktrees/foreign-wt"),
            "submodule": checkout("submodule", MAIN + "/.git/modules/Jmodot"),
            "projects": tmp_path / "projects"}


def call(command, tool="Bash", cwd=MAIN, agent="", agent_type="workflow-subagent", background=None, transcript="",
         session="rcr00001"):
    """The floor's hookSpecificOutput for one call ({} when it allows with no output)."""
    tool_input = {"command": command}
    if background is not None:
        tool_input["run_in_background"] = background
    payload = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd,
               "tool_input": tool_input}
    if agent:
        payload.update(agent_id=agent, agent_type=agent_type)
    if transcript:
        payload["transcript_path"] = transcript
    return (_floor.decide(payload) or {}).get("hookSpecificOutput") or {}


def rewritten(out):
    return (out.get("updatedInput") or {}).get("command")


def backgrounded(out):
    return (out.get("updatedInput") or {}).get("run_in_background") is True


def untouched(out, command, background=None):
    ui = out.get("updatedInput")
    return "permissionDecision" not in out and (
        ui is None or (ui.get("command") == command and ui.get("run_in_background") == background))


# --- 1. a worktree's script runs main's copy with -Repo -----------------------------------------------------------
@pytest.mark.parametrize("name", NAMES)
def test_a_worktree_script_runs_mains_copy_with_repo(plant, name):
    wt = plant["wt"]
    out = call("pwsh -NoProfile -File %s/.claude/scripts/%s.ps1 -X 1" % (wt, name))
    assert rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/%s.ps1 -Repo %s -X 1" % (MAIN, name, wt)
    assert backgrounded(out) == (name == GATE)  # only a non-detached gate is backgrounded for the orchestrator


def test_the_redirect_is_explained_once_per_window_and_applied_every_time(plant):
    command = "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % plant["wt"]
    first, again = call(command, session="redirect-once"), call(command, session="redirect-once")
    assert first.get("additionalContext") and not again.get("additionalContext")
    assert rewritten(first) == rewritten(again) != ""


def test_a_relative_path_after_cd_into_the_worktree_resolves_there(plant):
    wt = plant["wt"]
    out = call("cd %s && pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/scripts/verify.ps1 -Scope AI" % wt)
    assert rewritten(out) == ("cd %s && pwsh -NoProfile -ExecutionPolicy Bypass -File %s/.claude/scripts/verify.ps1 "
                              "-Repo %s -Scope AI" % (wt, MAIN, wt))


def test_a_relative_path_resolves_against_a_worktree_cwd(plant):
    wt = plant["wt"]
    out = call("pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter X", cwd=wt)
    assert rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Repo %s -Filter X" % (MAIN, wt)


def test_a_powershell_call_operator_invocation_is_redirected_with_its_quotes(plant):
    wt = plant["wt"]
    out = call("& '%s/.claude/scripts/%s.ps1' -StaticOnly" % (wt, GATE), tool="PowerShell")
    assert rewritten(out) == "& '%s/.claude/scripts/%s.ps1' -Repo %s -StaticOnly" % (MAIN, GATE, wt)


def test_a_project_without_router_entrypoints_gets_no_redirect(plant, project_values):
    project_values({"protected": [], "router_entrypoints": []})
    out = call("pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % plant["wt"])
    assert untouched(out, "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % plant["wt"])


def test_a_call_already_carrying_repo_gets_mains_path_and_no_second_repo(plant):
    wt = plant["wt"]
    out = call("pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Repo %s -Scope AI" % (wt, wt))
    assert rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Repo %s -Scope AI" % (MAIN, wt)


NOT_RUNS = {
    "a read-only cat of a worktree script": "cat {wt}/.claude/scripts/verify.ps1",
    "a grep of a worktree script": "grep -n Repo {wt}/.claude/scripts/run_test_suite.ps1",
    "a git show of a worktree script": "git -C {wt} show HEAD:.claude/scripts/verify.ps1",
    "a pwsh -Command that reads the script": 'pwsh -NoProfile -Command "Get-Content {wt}/.claude/scripts/verify.ps1"',
    "a heredoc fed to python that quotes the invocation":
        "python3 - <<'EOF'\nprint('pwsh -File {wt}/.claude/scripts/verify.ps1')\nEOF",
    "main's own script": "pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI",
    "a worktree's engine_run.ps1 (it already takes --path)": "pwsh -NoProfile -File {wt}/.claude/scripts/engine_run.ps1 --path . --import",
    "another repository's worktree": "pwsh -NoProfile -File {foreign}/.claude/scripts/verify.ps1 -Scope AI",
    "a submodule checkout": "pwsh -NoProfile -File {submodule}/.claude/scripts/verify.ps1 -Scope AI",
}


@pytest.mark.parametrize("label", list(NOT_RUNS))
def test_no_runner_rewrite(plant, label):
    command = NOT_RUNS[label].format(**plant)
    assert untouched(call(command), command)


# --- 2. a delegate's foreground run starts in the background -----------------------------------------------------
DELEGATE_RUNS = {
    "run_test_suite": "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter X -Label X",
    "verify": "pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI",
    GATE: "pwsh -NoProfile -File .claude/scripts/%s.ps1" % GATE,
    "run_integration_batched": "pwsh -NoProfile -File .claude/scripts/run_integration_batched.ps1 -Only AI",
    "engine_run": "pwsh -NoProfile -File .claude/scripts/engine_run.ps1 --path . --import",
    "dotnet build": "dotnet build",
    "dotnet test after cd": "cd C:/x && dotnet test --filter Y",
    "an env-prefixed dotnet build": "MSYS_NO_PATHCONV=1 dotnet build App.csproj",
}


@pytest.mark.parametrize("label", list(DELEGATE_RUNS))
def test_a_workflow_subagents_foreground_run_starts_in_the_background(plant, label):
    out = call(DELEGATE_RUNS[label], agent="agent-wf")
    assert backgrounded(out) and rewritten(out) == DELEGATE_RUNS[label]
    assert "until" in out.get("additionalContext", "") and "270000" in out["additionalContext"]


def test_a_claude_p_childs_foreground_dotnet_test_starts_in_the_background(plant, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    out = call("dotnet test --filter Y")
    assert backgrounded(out) and "until" in out.get("additionalContext", "")


def test_an_agent_tool_subagent_is_told_its_exit_wakes_it(plant):
    sub = plant["projects"] / "rcr00001" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-agent-tool.meta.json").write_text(json.dumps({"agentType": "general-purpose"}), encoding="utf-8")
    out = call("dotnet build", agent="agent-tool", agent_type="general-purpose",
               transcript=str(plant["projects"] / "rcr00001.jsonl"))
    assert backgrounded(out) and out.get("additionalContext") and "until" not in out["additionalContext"]


def test_a_subagents_worktree_run_is_redirected_and_backgrounded(plant):
    wt = plant["wt"]
    out = call("pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Filter X" % wt, agent="agent-wf")
    assert backgrounded(out)
    assert rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Repo %s -Filter X" % (MAIN, wt)


UNTOUCHED = {
    "the orchestrator's foreground dotnet build": ("dotnet build", {}),
    "the orchestrator's foreground verify run": ("pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI", {}),
    "a subagent's already-backgrounded run": ("pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter X",
                                              {"agent": "agent-wf", "background": True}),
    "a subagent's grep naming dotnet test": ('grep -n "dotnet test" .claude/skills/testing/SKILL.md', {"agent": "agent-wf"}),
    "a subagent's echo naming dotnet build": ("echo dotnet build", {"agent": "agent-wf"}),
    "a subagent's cat of verify.ps1": ("cat .claude/scripts/verify.ps1", {"agent": "agent-wf"}),
    "a subagent's quick verify -SelfTest": ("pwsh -NoProfile -File .claude/scripts/verify.ps1 -SelfTest", {"agent": "agent-wf"}),
    "a subagent's dotnet --info": ("dotnet --info", {"agent": "agent-wf"}),
    "a detached gate in the orchestrator": ("pwsh -File .claude/scripts/%s.ps1 -Detach" % GATE, {}),
    "a wrapped PowerShell mention in a delegate": ('pwsh -Command "Get-Content .claude/scripts/verify.ps1"',
                                                   {"agent": "agent-wf"}),
}


@pytest.mark.parametrize("label", list(UNTOUCHED))
def test_untouched(plant, label):
    command, kw = UNTOUCHED[label]
    assert untouched(call(command, **kw), command, kw.get("background"))


@pytest.mark.parametrize("command,tool", [("pwsh -File .claude/scripts/%s.ps1" % GATE, "Bash"),
                                          ('& ".claude/scripts/%s.ps1" -StaticOnly' % GATE, "PowerShell")],
                         ids=["pwsh -File", "call operator"])
def test_a_foreground_non_detached_gate_is_backgrounded_for_the_orchestrator(plant, command, tool):
    assert backgrounded(call(command, tool=tool))


@pytest.mark.parametrize("command", ['pwsh -Command "dotnet build"', "env TEST=1 dotnet test"])
def test_a_wrapped_native_build_or_test_is_backgrounded_in_a_delegate(plant, command):
    assert backgrounded(call(command, agent="agent-wf"))


# --- a router fault costs only the rewrite ----------------------------------------------------------------------
def test_a_router_fault_leaves_the_rest_of_the_floor_deciding(plant, monkeypatch):
    def boom(payload):
        raise RuntimeError("planted router fault")
    monkeypatch.setattr(run_call_router, "route", boom)
    assert call("find . -type f | xargs grep -l needle").get("permissionDecision") == "deny"
    assert call("dotnet build", agent="agent-wf") == {}


def test_an_absent_router_leaves_the_rest_of_the_floor_deciding(plant, monkeypatch):
    monkeypatch.setattr(_floor, "HELPERS", tuple(h for h in _floor.HELPERS if h.__name__ != "run_call_router"))
    assert call("find . -type f | xargs grep -l needle").get("permissionDecision") == "deny"


def test_the_router_is_a_registered_helper_ahead_of_the_backslash_repair():
    names = list(_floor.HELPER_NAMES)
    assert names.index("run_call_router") < names.index("bash_backslash_fidelity")


def test_the_router_has_no_dead_main_or_dynamic_import():
    text = (CLAUDE / "hooks" / "run_call_router.py").read_text(encoding="utf-8")
    assert "def main(" not in text and "__import__('sys')" not in text
