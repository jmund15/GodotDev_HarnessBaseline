#!/usr/bin/env python3
"""Proof for `hooks/agy_hook_bridge.py`: agy tool calls run this project's Claude Code PreToolUse hooks.

Payload shapes are cut from live agy 1.5.14 hook payloads (2026-11-21): `run_command` {CommandLine},
`read_file` {Path}, `write_file` {Content, Overwrite, Path}, `edit_file` {Path, Target, Replacement}. The first
cases run the real registered guards; the rest swap the registration for planted hooks to pin each outcome.

    python3 .claude/tests/test_agy_hook_bridge.py
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agy_hook_bridge", os.path.join(HERE, "..", "hooks", "agy_hook_bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
REGISTERED = bridge._registered  # the real settings reader; plant() swaps the module's copy
sys.path.insert(0, HERE)
import _settings_probe  # noqa: E402

# A consumer carries a composed settings.json; the baseline template composes at install, so the bridge reads the
# composed copy the probe builds there.
BRIDGE = os.path.join(HERE, "..", "hooks", "agy_hook_bridge.py")
COMPOSED = os.path.isfile(bridge.SETTINGS)
bridge.SETTINGS = _settings_probe.settings_path(os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="agy_bridge_")
WS = tempfile.mkdtemp(prefix="agy_bridge_ws_")
fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f"   [{detail}]"))
    fails += not ok


def call(name, args, conv="c1"):
    return bridge.pre_tool_use({"conversationId": conv, "workspacePaths": [WS], "toolCall": {"name": name, "args": args}})


def spawn(event, payload):
    """The bridge as a process: its registered command where settings.json is composed, else the same module run
    with the probe's composed copy, since a raw template has no settings.json for the command to read."""
    if COMPOSED:
        argv = [sys.executable, BRIDGE, event]
    else:
        loader = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('agy_hook_bridge', sys.argv[1]); "
                  "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); m.SETTINGS = sys.argv[2]; "
                  "sys.argv = [sys.argv[1], sys.argv[3]]; m.main()")
        argv = [sys.executable, "-c", loader, BRIDGE, bridge.SETTINGS, event]
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=120)


# The guard protects the checkout holding the bridge even when the consumer was bootstrapped under system temp. In a
# consumer that is the bridge's ROOT; in the baseline repository ROOT is its `template/` folder, so the target is the
# git top level above it.
import subprocess  # noqa: E402
CHECKOUT = subprocess.run(["git", "-C", bridge.ROOT, "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True).stdout.strip() or bridge.ROOT
delete = 'rm -rf "' + CHECKOUT.replace("\\", "/") + '"'
got = call("run_command", {"CommandLine": delete})
check("a real guard denies a recursive delete", got["decision"] == "deny", got)
import subprocess  # noqa: E402
spawned = spawn("PreToolUse", {"conversationId": "c0", "workspacePaths": [WS], "toolCall": {
    "name": "run_command", "args": {"CommandLine": delete}}})
answer = json.loads(spawned.stdout or "{}")
check("the registered command denies on stdout, from the guard and not from its own failure",
      answer.get("decision") == "deny" and not str(answer.get("reason", "")).startswith("agy_hook_bridge failed"),
      spawned.stdout + spawned.stderr)
got = call("run_command", {"CommandLine": "echo ok"})
check("a plain command is allowed", got == {"decision": "allow"}, got)
check("an unmapped tool is left to the seat", call("browser_click_element", {}) == {"decision": "allow"})

# The Claude input each agy shape maps to.
check("write_file maps to Write", bridge._write({"Content": "a", "Overwrite": True, "Path": "p"})
      == {"file_path": "p", "content": "a"})
check("runtime edit_file maps to Edit", bridge._edit({"Path": "p", "Target": "a", "Replacement": "b"})
      == {"file_path": "p", "old_string": "a", "new_string": "b"})
check("a chunked replace maps to one Edit", bridge._edit({"TargetFile": "p", "ReplacementChunks": [
    {"TargetContent": "a", "ReplacementContent": "b"}, {"TargetContent": "c", "ReplacementContent": "d"}]})
      == {"file_path": "p", "old_string": "a\nc", "new_string": "b\nd"})


# PostToolUse, through the real registered hooks: a full read of a SKILL.md loads that skill for the agy
# conversation, so the harness-edit gate admits the conversation's next `.claude/` write. The payload is cut from a
# live agy 1.5.14 PostToolUse capture (2026-11-22): the PreToolUse fields plus `hookEventName` and `error`.
HOME = tempfile.mkdtemp(prefix="agy_bridge_home_")
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILL = os.path.join(ROOT, ".claude", "skills", "instruction_quality", "SKILL.md").replace("\\", "/")


def post(args, conv, error=""):
    return bridge.post_tool_use({"conversationId": conv, "cwd": WS, "error": error, "hookEventName": "PostToolUse",
                                 "workspacePaths": [WS], "toolCall": {"args": args, "id": "call_1", "name": "read_file"}})


def loaded(conv):
    sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
    import _hook_state
    return _hook_state.skill_in_context("agy-" + conv, "instruction_quality")


check("control: no skill is loaded before a read", not loaded("load"))
check("a post-tool read returns no decision", post({"Path": SKILL}, "load") == {})
check("a full SKILL.md read loads the skill for the conversation", loaded("load"))
post({"Path": SKILL, "StartLine": 1, "EndLine": 5}, "ranged")
check("a read with a range loads nothing", not loaded("ranged"))
post({"Path": SKILL}, "failed", error="file not found")
check("a failed read loads nothing", not loaded("failed"))
spawned = spawn("PostToolUse", {"conversationId": "c0", "workspacePaths": [WS], "error": "",
                                "toolCall": {"name": "read_file", "args": {"Path": "x"}}})
check("the registered PostToolUse command exits 0 with no decision",
      spawned.returncode == 0 and json.loads(spawned.stdout or "null") == {}, spawned.stdout + spawned.stderr)


def plant(body, timeout=None):
    path = os.path.join(TMP, f"hook_{len(os.listdir(TMP))}.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import json, sys, time\npayload = json.load(sys.stdin)\n" + body)
    bridge._registered = lambda tool, event="PreToolUse": [(f'python3 "{path}"', timeout or 10)]
    return path


plant("sys.stderr.write('nope'); sys.exit(2)\n")
check("exit 2 denies with the hook's stderr", call("read_file", {"Path": "x"}) == {"decision": "deny", "reason": "nope"})
plant("print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask', 'permissionDecisionReason': 'why'}}))\n")
check("an ask denies: headless agy cannot ask", call("read_file", {"Path": "x"}) == {"decision": "deny", "reason": "why"})
plant("assert payload['tool_input']['command'] == 'rm -rf x'\n"
      "print(json.dumps({'hookSpecificOutput': {'updatedInput': {'command': 'true'}}}))\n")
got = call("run_command", {"CommandLine": "rm -rf x"})
check("an updatedInput command maps back to CommandLine", got == {"decision": "allow", "overwrite": {"CommandLine": "true"}}, got)
plant("print(json.dumps({'hookSpecificOutput': {'additionalContext': 'mind the gap'}}))\n")
check("an advisory allows the call", call("read_file", {"Path": "x"}, conv="adv") == {"decision": "allow"})
got = bridge.pre_invocation({"conversationId": "adv"})
check("the advisory reaches the next invocation once",
      "mind the gap" in json.dumps(got) and bridge.pre_invocation({"conversationId": "adv"}) == {}, got)
plant("time.sleep(5)\n", timeout=1)
check("a hook that times out denies the call", call("read_file", {"Path": "x"})["decision"] == "deny")
plant("raise RuntimeError('guard bug')\n")
check("a hook that crashes denies the call", call("read_file", {"Path": "x"})["decision"] == "deny")
check("a post hook that crashes leaves the finished call alone", post({"Path": "x"}, "crash") == {})
plant("assert payload['hook_event_name'] == 'PostToolUse'\n"
      "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'after the read'}}))\n")
post({"Path": "x"}, "padv")
check("a post hook's advisory reaches the next invocation", "after the read" in json.dumps(bridge.pre_invocation(
    {"conversationId": "padv"})))

# SessionStart and Stop. agy's contracts (its bundled hooks doc, 1.5.14): SessionStart takes the common fields and
# returns injectSteps; Stop takes continuationActive and returns {"decision": "continue", "reason"} to keep going.
starts = [os.path.basename(c.split('"')[1]) for c, _ in REGISTERED("startup", "SessionStart")]
check("a fresh agy conversation runs the startup SessionStart hooks, not the compact ones",
      "session_context_loader.py" in starts and "compact_directive_anchor.py" not in starts, starts)
plant("assert payload['hook_event_name'] == 'SessionStart' and payload['source'] == 'startup'\n"
      "print(json.dumps({'hookSpecificOutput': {'additionalContext': 'rails here'}}))\n")
got = bridge.session_start({"conversationId": "ss"})
check("a SessionStart advisory arrives as a system message",
      got == {"injectSteps": [{"systemMessage": {"systemMessage": "rails here"}}]}, got)
plant("print('plain context')\n")
check("plain SessionStart stdout is context too", "plain context" in json.dumps(bridge.session_start({})))
plant("raise RuntimeError('loader bug')\n")
check("a SessionStart hook that crashes injects nothing", bridge.session_start({}) == {})
plant("sys.stderr.write('finish R7'); sys.exit(2)\n")
check("a Stop hook's exit 2 keeps agy going with its reason",
      bridge.stop({"conversationId": "st"}) == {"decision": "continue", "reason": "finish R7"})
plant("assert payload['hook_event_name'] == 'Stop' and payload['stop_hook_active'] is True\n"
      "print(json.dumps({'decision': 'block', 'reason': 'amendment'}))\n")
got = bridge.stop({"conversationId": "st", "continuationActive": True})
check("a Stop block maps to continue and the hook sees an active continuation",
      got == {"decision": "continue", "reason": "amendment"}, got)
plant("raise RuntimeError('stop bug')\n")
check("a Stop hook that crashes lets the job stop", bridge.stop({}) == {})
plant("print(json.dumps({'hookSpecificOutput': {'additionalContext': 'later'}}))\n")
call("read_file", {"Path": "x"}, conv="sm")
got = bridge.pre_invocation({"conversationId": "sm"})
check("a queued advisory arrives as a system message",
      got == {"injectSteps": [{"systemMessage": {"systemMessage": "Project hook notes:\nlater"}}]}, got)

print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
sys.exit(1 if fails else 0)
