"""Re-runnable proof for hooks/editor_live_build_guard.py.

Each case feeds a real PreToolUse payload. HARNESS_LIVE_EDITOR_CHECKOUTS names the checkouts the guard treats as
having a live editor, so no Godot process is needed.

    python3 .claude/tests/test_editor_live_build_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "editor_live_build_guard.py")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TWIN = tempfile.mkdtemp(prefix="elbg-live-")   # a planted checkout root: any dir with a .git, never a live worktree
os.makedirs(os.path.join(TWIN, ".git"), exist_ok=True)


def run(command, live=(), cwd=REPO, tool="Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd}
    env = dict(os.environ, HARNESS_LIVE_EDITOR_CHECKOUTS=os.pathsep.join(live))
    result = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                            text=True, timeout=30, env=env)
    assert result.returncode in (0, 2) and "Traceback" not in result.stderr, ("CRASH", result.returncode, result.stderr)
    return result


def denied(result):
    if result.returncode == 2:
        return True
    out = json.loads(result.stdout or "{}").get("hookSpecificOutput", {})
    return out.get("permissionDecision") == "deny"


def test_ignore_editor_on_live_checkout_is_denied():
    command = f'pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Repo "{TWIN}" -IgnoreEditor -Filter X'
    result = run(command, live=[TWIN])
    assert denied(result), result
    assert "without -IgnoreEditor" in result.stderr and "twin" in result.stderr, result.stderr


def test_ignore_editor_without_live_editor_passes():
    command = f'pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Repo "{TWIN}" -IgnoreEditor -Filter X'
    assert not denied(run(command, live=[]))
    assert not denied(run(command, live=[REPO]))


def test_runner_without_override_passes_on_live_checkout():
    command = f'pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Repo "{TWIN}" -Filter X'
    assert not denied(run(command, live=[TWIN]))


def test_dotnet_build_in_live_checkout_is_denied():
    for command in (f'cd "{TWIN}" && dotnet build {{PROJECT_NAME}}.csproj', 'dotnet test --filter X'):
        cwd = REPO if command.startswith("cd") else TWIN
        result = run(command, live=[TWIN], cwd=cwd)
        assert denied(result), command
        assert "run_test_suite.ps1" in result.stderr and "twin" in result.stderr, result.stderr
    assert denied(run(f'dotnet build "{TWIN}/{{PROJECT_NAME}}.csproj"', live=[TWIN]))


def test_dotnet_build_elsewhere_passes():
    assert not denied(run('dotnet build {{PROJECT_NAME}}.csproj', live=[TWIN], cwd=REPO))
    assert not denied(run('dotnet --info', live=[TWIN], cwd=TWIN))


def test_mentions_are_not_actions():
    assert not denied(run("printf '%s' 'dotnet build'", live=[TWIN], cwd=TWIN))
    assert not denied(run("dotnet build", live=[TWIN], cwd=TWIN, tool="Read"))


def test_ignore_editor_name_value_spelling_is_denied():
    command = f'pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Repo "{TWIN}" -IgnoreEditor:$true -Filter X'
    result = run(command, live=[TWIN])
    assert denied(result), result


def _liveness_with_missing_probe(platform):
    """_is_live(TWIN) as the hook computes it when neither tasklist nor pwsh can start (a Linux cloud VM)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("editor_live_build_guard_probe", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("probe binary absent")

    saved = (hook.subprocess.run, sys.platform, os.environ.pop("HARNESS_LIVE_EDITOR_CHECKOUTS", None))
    hook.subprocess.run, sys.platform = missing, platform
    try:
        return hook._is_live(hook._root(TWIN))
    finally:
        hook.subprocess.run, sys.platform = saved[0], saved[1]
        if saved[2] is not None:
            os.environ["HARNESS_LIVE_EDITOR_CHECKOUTS"] = saved[2]


def test_non_windows_has_no_editor_to_protect():
    assert _liveness_with_missing_probe("linux") is False


def test_windows_probe_failure_still_denies():
    assert _liveness_with_missing_probe("win32") is True


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
