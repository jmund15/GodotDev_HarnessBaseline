#!/usr/bin/env python3
"""Re-runnable proof for hooks/run_call_router.py through the live pre_bash_dispatch.

Two rewrites, both through `updatedInput`, never a permission decision:
1. Code from main, data from the target (cadence_godot2_report D1). A linked worktree's own
   `.claude/scripts/{run_test_suite,verify,regression_gate,run_integration_batched}.ps1` is frozen at its branch point,
   so an invocation of one runs main's copy with `-Repo <worktree>` instead. Only the invocation shape is rewritten:
   a read-only mention, main's own script, another repo's worktree and a submodule pass untouched.
2. In a subagent or a `claude -p` child, a foreground test, build or gate call starts with run_in_background: true and
   an additionalContext line naming how to wait. The orchestrator, an already-backgrounded call, a quick read-only
   flag and a mention pass untouched.

Cases feed real PreToolUse payloads on stdin against a planted linked worktree (a `.git` file whose gitdir sits under
this repo's `.git/worktrees/`) and assert on the emitted channel. An exit outside {0, 2} or a traceback is CRASH.

    python3 .claude/tests/test_run_call_router.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HERE, "..", "hooks", "pre_bash_dispatch.py")
TMP = tempfile.mkdtemp(prefix="rcr_")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
MAIN = REPO.replace("\\", "/")
GATE = "regression_gate"
GATE_SCRIPT = GATE + ".ps1"
NAMES = ("run_test_suite", "verify", GATE, "run_integration_batched")


def plant_checkout(name, gitdir):
    root = os.path.join(TMP, name)
    os.makedirs(os.path.join(root, ".claude", "scripts"), exist_ok=True)
    with open(os.path.join(root, ".git"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gitdir: %s\n" % gitdir)
    return root.replace("\\", "/")


WT = plant_checkout("wt-pr101", MAIN + "/.git/worktrees/wt-pr101")
FOREIGN = plant_checkout("foreign-wt", "C:/elsewhere/other/.git/worktrees/foreign-wt")
SUBMODULE = plant_checkout("submodule", MAIN + "/.git/modules/Jmodot")
PROJECTS = os.path.join(TMP, "projects")


def plant_agent_tool(session, agent):
    sub = os.path.join(PROJECTS, session, "subagents")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "agent-%s.meta.json" % agent), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"agentType": "general-purpose"}, fh)
    return os.path.join(PROJECTS, session + ".jsonl")


def call(command, tool="Bash", cwd=REPO, agent="", agent_type="workflow-subagent", entrypoint="cli",
         background=None, transcript=""):
    tool_input = {"command": command}
    if background is not None:
        tool_input["run_in_background"] = background
    payload = {"session_id": "rcr00001", "hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd,
               "tool_input": tool_input}
    if agent:
        payload.update(agent_id=agent, agent_type=agent_type)
    if transcript:
        payload["transcript_path"] = transcript
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HARNESS_HOOK_STATE_DIR=os.path.join(TMP, uuid.uuid4().hex),
               CLAUDE_CODE_ENTRYPOINT=entrypoint, CLAUDE_PROJECT_DIR=REPO)
    if GATE_SCRIPT in command:
        tool_input['command'] += ' # gate: final'
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=env, cwd=REPO)
    if r.returncode not in (0, 2) or "Traceback" in r.stderr:
        return {"crash": r.stderr[-400:]}
    try:
        out = json.loads(r.stdout or "{}").get("hookSpecificOutput") or {}
    except ValueError:
        out = {}
    if r.returncode == 2:
        out["blocked"] = r.stderr[-300:]
    if out.get('updatedInput', {}).get('command', '').endswith(' # gate: final'):
        out['updatedInput']['command'] = out['updatedInput']['command'][:-len(' # gate: final')]
    return out


def rewritten(out):
    return (out.get("updatedInput") or {}).get("command")


def backgrounded(out):
    return (out.get("updatedInput") or {}).get("run_in_background") is True


RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((bool(ok), label, "" if ok else "  (%s)" % str(detail)[:400]))


def untouched(label, out, command):
    """No rewrite: no updatedInput, or one that leaves both the command and the background flag as sent."""
    ui = out.get("updatedInput")
    ok = "crash" not in out and "blocked" not in out and (ui is None or (ui.get("command") == command
                                                                         and not ui.get("run_in_background")))
    check(label, ok, out)


def main():
    # --- 1. worktree script -> main's script + -Repo (orchestrator: no backgrounding) ---
    for name in NAMES:
        cmd = "pwsh -NoProfile -File %s/.claude/scripts/%s.ps1 -X 1" % (WT, name)
        out = call(cmd)
        want = "pwsh -NoProfile -File %s/.claude/scripts/%s.ps1 -Repo %s -X 1" % (MAIN, name, WT)
        check("a worktree's %s.ps1 invocation runs main's copy with -Repo <worktree>" % name, rewritten(out) == want,
              out)
        check("...only a non-detached gate is backgrounded in the orchestrator",
              backgrounded(out) == (name == GATE), out)
    out = call("pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % WT)
    check("the redirect names what it did in additionalContext", "-Repo" in (out.get("additionalContext") or ""), out)

    cmd = "cd %s && pwsh -NoProfile -ExecutionPolicy Bypass -File .claude/scripts/verify.ps1 -Scope AI" % WT
    out = call(cmd)
    check("a relative path after `cd <worktree> &&` resolves against that worktree",
          rewritten(out) == "cd %s && pwsh -NoProfile -ExecutionPolicy Bypass -File %s/.claude/scripts/"
                            "verify.ps1 -Repo %s -Scope AI" % (WT, MAIN, WT), out)
    cmd = "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter X"
    out = call(cmd, cwd=WT)
    check("a relative path resolves against the payload cwd when it is a worktree",
          rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Repo %s "
                            "-Filter X" % (MAIN, WT), out)
    cmd = "& '%s/.claude/scripts/%s' -StaticOnly" % (WT, GATE_SCRIPT)
    out = call(cmd, tool="PowerShell")
    check("a PowerShell-tool call operator invocation is redirected, quotes kept",
          rewritten(out) == "& '%s/.claude/scripts/%s' -Repo %s -StaticOnly" % (MAIN, GATE_SCRIPT, WT), out)
    cmd = "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Repo %s -Scope AI" % (WT, WT)
    out = call(cmd)
    check("a call already carrying -Repo gets main's path and no second -Repo",
          rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Repo %s -Scope AI" % (MAIN, WT), out)

    for label, cmd in (
            ("a read-only `cat` of a worktree script", "cat %s/.claude/scripts/verify.ps1" % WT),
            ("a grep of a worktree script", "grep -n Repo %s/.claude/scripts/run_test_suite.ps1" % WT),
            ("a `git show` of a worktree script", "git -C %s show HEAD:.claude/scripts/verify.ps1" % WT),
            ("a pwsh -Command that reads the script", 'pwsh -NoProfile -Command "Get-Content %s/.claude/scripts/'
                                                       'verify.ps1"' % WT),
            ("a heredoc fed to python that quotes the invocation", "python3 - <<'EOF'\nprint('pwsh -File %s/.claude/"
                                                                    "scripts/verify.ps1')\nEOF" % WT),
            ("main's own script", "pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI"),
            ("a worktree's engine_run.ps1 (it already takes --path)", "pwsh -NoProfile -File %s/.claude/scripts/"
                                                                       "engine_run.ps1 --path . --import" % WT),
            ("another repository's worktree", "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % FOREIGN),
            ("a submodule checkout", "pwsh -NoProfile -File %s/.claude/scripts/verify.ps1 -Scope AI" % SUBMODULE)):
        expected = 'MSYS_NO_PATHCONV=1 ' + cmd if 'git show' in label else cmd
        untouched("must-allow, no runner rewrite: %s" % label, call(cmd), expected)

    # --- 2. subagent / claude -p child: foreground test, build or gate call -> run_in_background ---
    for label, cmd in (
            ("run_test_suite", "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 -Filter X -Label X"),
            ("verify", "pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI"),
            (GATE, "pwsh -NoProfile -File .claude/scripts/" + GATE_SCRIPT),
            ("run_integration_batched", "pwsh -NoProfile -File .claude/scripts/run_integration_batched.ps1 -Only AI"),
            ("engine_run", "pwsh -NoProfile -File .claude/scripts/engine_run.ps1 --path . --import"),
            ("dotnet build", "dotnet build"),
            ("dotnet test after cd", "cd C:/x && dotnet test --filter Y"),
            ("an env-prefixed dotnet build", "MSYS_NO_PATHCONV=1 dotnet build App.csproj")):
        out = call(cmd, agent="agent-wf")
        ctx = out.get("additionalContext") or ""
        check("a Workflow subagent's foreground %s call starts in the background" % label,
              backgrounded(out) and rewritten(out) == cmd, out)
        check("...with a line naming the 270000 ms until-loop wait", "until" in ctx and "270000" in ctx, ctx)
    out = call("dotnet test --filter Y", entrypoint="sdk-cli")
    check("a claude -p child's foreground dotnet test starts in the background",
          backgrounded(out) and "until" in (out.get("additionalContext") or ""), out)
    out = call("dotnet build", agent="agent-tool", agent_type="general-purpose",
               transcript=plant_agent_tool("rcr00001", "agent-tool"))
    ctx = out.get("additionalContext") or ""
    check("an Agent-tool subagent's dotnet build starts in the background, told its exit wakes it",
          backgrounded(out) and "wakes you" in ctx and "until" not in ctx, out)
    cmd = "pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Filter X" % WT
    out = call(cmd, agent="agent-wf")
    check("a subagent's worktree run is both redirected to main and backgrounded",
          backgrounded(out) and rewritten(out) == "pwsh -NoProfile -File %s/.claude/scripts/run_test_suite.ps1 -Repo "
                                                  "%s -Filter X" % (MAIN, WT), out)

    for label, cmd, kw in (
            ("the orchestrator's foreground dotnet build", "dotnet build", {}),
            ("the orchestrator's foreground verify run", "pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope AI",
             {}),
            ("a subagent's already-backgrounded run", "pwsh -NoProfile -File .claude/scripts/run_test_suite.ps1 "
                                                      "-Filter X", {"agent": "agent-wf", "background": True}),
            ("a subagent's grep naming dotnet test", 'grep -n "dotnet test" .claude/skills/testing/SKILL.md',
             {"agent": "agent-wf"}),
            ("a subagent's echo naming dotnet build", "echo dotnet build", {"agent": "agent-wf"}),
            ("a subagent's cat of verify.ps1", "cat .claude/scripts/verify.ps1", {"agent": "agent-wf"}),
            ("a subagent's quick verify -SelfTest", "pwsh -NoProfile -File .claude/scripts/verify.ps1 -SelfTest",
             {"agent": "agent-wf"}),
            ("a subagent's dotnet --info", "dotnet --info", {"agent": "agent-wf"})):
        out = call(cmd, **kw)
        ui = out.get("updatedInput")
        ok = "crash" not in out and "blocked" not in out and (
            ui is None or (ui.get("command") == cmd and ui.get("run_in_background") == kw.get("background")))
        check("untouched: %s" % label, ok, out)

    for cmd in ('pwsh -File .claude/scripts/' + GATE_SCRIPT,
                '& ".claude/scripts/%s" -StaticOnly' % GATE_SCRIPT):
        out = call(cmd, tool='PowerShell' if cmd.startswith('&') else 'Bash')
        check('a foreground non-detached gate is backgrounded in the orchestrator', backgrounded(out), out)
    cmd = 'pwsh -File .claude/scripts/%s -Detach' % GATE_SCRIPT
    untouched('a detached gate in the orchestrator is unchanged', call(cmd), cmd)

    for cmd in ('pwsh -Command "dotnet build"', 'env TEST=1 dotnet test'):
        out = call(cmd, agent='agent-wf')
        check('wrapped native build/test action is backgrounded: ' + cmd, backgrounded(out), out)
    cmd = 'pwsh -Command "Get-Content .claude/scripts/verify.ps1"'
    untouched('wrapped PowerShell mention stays untouched in a delegate', call(cmd, agent='agent-wf'), cmd)

    # A router fault must cost only the rewrite: the guard chain after it still decides.
    probe = (
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import run_call_router, pre_bash_dispatch\n"
        "def boom(payload): raise RuntimeError('planted router fault')\n"
        "run_call_router.route = boom\n"
        "payload = {'session_id': 'rcr00002', 'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',\n"
        "           'cwd': %r, 'tool_input': {'command': 'find . -type f | xargs grep -l needle'}}\n"
        "code, out, err = pre_bash_dispatch.dispatch(json.dumps(payload))\n"
        "print(json.dumps({'code': code, 'out': out, 'err': err}))\n"
    ) % (os.path.normpath(os.path.join(HERE, "..", "hooks")), REPO)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HARNESS_HOOK_STATE_DIR=os.path.join(TMP, uuid.uuid4().hex),
               CLAUDE_PROJECT_DIR=REPO)
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, encoding="utf-8",
                       timeout=120, env=env, cwd=REPO)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
        denied = res["code"] == 2 or '"deny"' in res["out"]
    except (ValueError, IndexError, KeyError):
        res, denied = r.stderr[-400:], False
    check("a router fault leaves the guard chain deciding (planted fault, recursive grep still denied)", denied, res)

    for label, plant in (
            ('absent router', "original = _optional_hooks.present\n"
                              "_optional_hooks.present = lambda name: name != 'run_call_router' and original(name)\n"),
            ('router import fault', "original = _optional_hooks.importlib.import_module\n"
                                    "def importing(name):\n"
                                    " if name == 'run_call_router': raise RuntimeError('planted import fault')\n"
                                    " return original(name)\n"
                                    "_optional_hooks.importlib.import_module = importing\n")):
        probe = ("import json, sys\n"
                 "sys.path.insert(0, %r)\n"
                 "import _optional_hooks\n" + plant +
                 "import pre_bash_dispatch\n"
                 "payload = {'tool_name': 'Bash', 'cwd': %r,\n"
                 "           'tool_input': {'command': 'find . -type f | xargs grep -l needle'}}\n"
                 "code, out, err = pre_bash_dispatch.dispatch(json.dumps(payload))\n"
                 "print(json.dumps({'code': code, 'out': out, 'err': err}))\n"
                 ) % (os.path.normpath(os.path.join(HERE, '..', 'hooks')), REPO)
        r = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, encoding='utf-8',
                           timeout=120, env=env, cwd=REPO)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
            hso = json.loads(res['out']).get('hookSpecificOutput', {})
            context = hso.get('additionalContext', '')
            fault_visible = ('run_call_router fault:' in context and
                             'planted import fault' in context and
                             'Repair .claude/hooks/run_call_router.py.' in context)
            ok = (r.returncode == 0 and res['code'] == 0 and
                  hso.get('permissionDecision') == 'deny' and
                  'RECURSIVE GREP DENIED' in hso.get('permissionDecisionReason', '') and
                  fault_visible == (label == 'router import fault'))
        except (ValueError, IndexError, KeyError):
            res, ok = r.stderr[-400:], False
        check(label + ' leaves the guard chain deciding', ok, res)
    router = os.path.join(HERE, '..', 'hooks', 'run_call_router.py')
    with open(router, encoding='utf-8') as source:
        text = source.read()
    check('the route-only router has no dead main or dynamic sys import',
          'def main(' not in text and "__import__('sys')" not in text)

    failures = 0
    for ok, label, detail in RESULTS:
        failures += not ok
        print("%-4s %s%s" % ("ok" if ok else "FAIL", label, detail))
    print("\n%d/%d cases pass" % (len(RESULTS) - failures, len(RESULTS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
