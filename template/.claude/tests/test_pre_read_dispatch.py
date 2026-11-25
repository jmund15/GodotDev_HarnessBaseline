#!/usr/bin/env python3
"""Re-runnable proof for hooks/pre_read_dispatch.py — the PreToolUse read/search chain.

Sub-hooks run in the interpreter: indexed_reference_guard (main advice, delegate block) and
semantic_search_scope_guard (block, or a rewrite through updatedInput) and
video_read_hint (advisory).
Each case below feeds a real PreToolUse payload by subprocess and asserts on the emitted channel: exit 2 +
stderr for a block, hookSpecificOutput for a rewrite or fault note, nothing for a clean call. An exit code
outside {0, 2} is a CRASH, never an allow.

State is redirected with HOME/USERPROFILE/HARNESS_HOOK_STATE_DIR — this never touches
~/.claude/.routing_state/.

    python3 .claude/tests/test_pre_read_dispatch.py
"""
import json
import os
import subprocess
import sys
import tempfile

import _settings_probe

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(CLAUDE, ".."))
DISPATCH = os.path.join(CLAUDE, "hooks", "pre_read_dispatch.py")
SETTINGS = _settings_probe.settings_path(CLAUDE)
SEARCH_TOOL = "mcp__plugin_semantic-search_semantic-search__search"

sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
import _optional_hooks  # noqa: E402

SUBHOOKS = ("indexed_reference_guard", "semantic_search_scope_guard", "video_read_hint")


def run(payload, env, raw=None):
    text = raw if raw is not None else json.dumps(payload)
    r = subprocess.run([sys.executable, DISPATCH], input=text, capture_output=True, text=True,
                       timeout=120, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def context_of(stdout):
    if not stdout or stdout == "{}":
        return ""
    specific = json.loads(stdout).get("hookSpecificOutput") or {}
    assert specific.get("hookEventName") == "PreToolUse", specific
    return specific.get("additionalContext") or ""


def call(tool, tool_input, session="rd000001", **extra):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "tool_input": tool_input}
    payload.update(extra)
    return payload


def registration_cases():
    with open(SETTINGS, encoding="utf-8") as fh:
        settings = json.load(fh)
    commands = []
    for group in settings["hooks"]["PreToolUse"]:
        matcher = group.get("matcher", "")
        if "Read" not in matcher.split("|"):
            continue
        commands.extend(h.get("command", "") for h in group.get("hooks", []))
        yield ("the Read matcher also covers the semantic-search tool", SEARCH_TOOL in matcher.split("|"))
    yield ("settings.json registers pre_read_dispatch.py on the Read matcher",
           any("pre_read_dispatch.py" in c for c in commands))
    yield ("no consolidated sub-hook keeps its own Read registration",
           not any(name + ".py" in c for c in commands for name in SUBHOOKS))
    with open(DISPATCH, encoding="utf-8") as fh:
        source = fh.read()
    yield ("dispatcher no longer imports the retired cascade block",
           "tool_routing_cumulative_block" not in source)
    yield ("the Read PreToolUse surface is exactly one command", len(commands) == 1)


def main():
    tmp = tempfile.mkdtemp(prefix="pre_read_dispatch_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")
    env.pop("HARNESS_ROUTING_HARD_BLOCK_CS_GREP", None)
    cases = list(registration_cases())

    def crash_free(label, rc, out, err, ok):
        if rc not in (0, 2) or "Traceback" in err:
            cases.append((label + " [CRASH rc=%s]" % rc, False))
        else:
            cases.append((label, ok))

    small = os.path.join(tmp, "small.md")
    with open(small, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("a small file\n")

    # --- clean read ---------------------------------------------------------
    rc, out, err = run(call("Read", {"file_path": small}), env)
    crash_free("a clean small Read emits nothing and exits 0", rc, out, err,
               rc == 0 and out in ("", "{}") and err == "")

    # --- indexed_reference_guard: main advice, delegate block ----------------
    # Optional members ship with higher layers; a consumer without one runs the dispatcher's other cases.
    member = _optional_hooks.present
    if member("indexed_reference_guard"):
        indexed = os.path.join(CLAUDE, "commands", "agents", "plan_check_agents.md")
        rc, out, err = run(call("Read", {"file_path": indexed}), env)
        try:
            indexed_advice = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            indexed_advice = ""
        crash_free("main-session indexed Read proceeds with accessor advice on the model channel",
                   rc, out, err, rc == 0 and err == "" and "lens.py get --shared" in indexed_advice)
        rc, out, err = run(call("Read", {"file_path": indexed, "offset": 1, "limit": 40}), env)
        crash_free("a bounded Read of the same reference passes", rc, out, err, rc == 0 and out in ("", "{}"))
        rc, out, err = run(call("Read", {"file_path": indexed}, agent_id="agent-9",
                                agent_type="workflow-subagent"), env)
        crash_free("a subagent deny cites guards/common.md and names the accessor next step",
                   rc, out, err, rc == 2 and "guards/common.md" in err
                   and "read a catalog with an accessor through it" in err
                   and "lens.py get --shared" in err)
        rc, out, err = run(call("Read", {"file_path": indexed, "offset": 1, "limit": 40},
                                agent_id="agent-9", agent_type="workflow-subagent"), env)
        crash_free("a bounded Read passes for a subagent too", rc, out, err, rc == 0)

    # --- video_read_hint: a video or GIF Read names the contact-sheet skill, advisory only ---------------
    if _optional_hooks.present("video_read_hint"):
        rc, out, err = run(call("Read", {"file_path": os.path.join(tmp, "clip.mp4")}), env)
        crash_free("a Read of a video advises the video_frames skill and proceeds", rc, out, err,
                   rc == 0 and err == "" and "video_frames" in context_of(out))
        rc, out, err = run(call("Read", {"file_path": small}), env)
        crash_free("a Read of a text file carries no video advice", rc, out, err,
                   rc == 0 and "video_frames" not in context_of(out))

    # --- semantic_search_scope_guard: correct or block, not subagent-exempt ------------
    sub_root = os.path.join(ROOT, ".claude")
    rc, out, err = run(call(SEARCH_TOOL, {"query": "x", "searchDir": sub_root}), env)
    fixed = (json.loads(out or "{}").get("hookSpecificOutput") or {}).get("updatedInput") or {}
    crash_free("a subdirectory searchDir is corrected through the dispatcher, keeping the query",
               rc, out, err, rc == 0 and fixed.get("restrictToDir") == ".claude" and fixed.get("query") == "x")
    outside = os.path.dirname(ROOT)
    rc, out, err = run(call(SEARCH_TOOL, {"query": "x", "searchDir": outside},
                            agent_id="agent-9", agent_type="workflow-subagent"), env)
    crash_free("a subagent is NOT exempt from the scope block", rc, out, err, rc == 2 and "searchDir" in err)
    rc, out, err = run(call(SEARCH_TOOL, {"query": "x", "searchDir": ROOT,
                                          "restrictToDir": ".claude/auto-memory"}), env)
    crash_free("a root searchDir with a repo-relative restrictToDir passes", rc, out, err,
               rc == 0 and err == "")

    # A present optional member's broken import is reported on the model channel and the call proceeds.
    for name in ('indexed_reference_guard', 'semantic_search_scope_guard', 'video_read_hint'):
        if not member(name):
            continue  # a project that does not carry this optional hook has no import to break
        script = (
            'import sys, runpy\n'
            'class Broken:\n'
            '    def find_spec(self, name, path=None, target=None):\n'
            f'        if name == {name!r}:\n'
            "            raise RuntimeError('planted read import fault')\n"
            'sys.meta_path.insert(0, Broken())\n'
            f'runpy.run_path({DISPATCH!r}, run_name="__main__")\n')
        data = call('Read', {'file_path': small}, session='fault-' + name)
        if name == 'video_read_hint':
            data = call('Read', {'file_path': os.path.join(tmp, 'clip.mp4')}, session='fault-' + name)
        if name == 'indexed_reference_guard':
            data = call('Read', {'file_path': os.path.join(CLAUDE, 'commands', 'agents', 'plan_check_agents.md')},
                        session='fault-' + name)
        proc = subprocess.run([sys.executable, '-c', script], input=json.dumps(data),
                              capture_output=True, text=True, encoding='utf-8', env=env, timeout=120)
        crash_free(name + ' import fault reports on model channel',
                   proc.returncode, proc.stdout, proc.stderr,
                   proc.returncode == 0 and name in context_of(proc.stdout)
                   and 'planted read import fault' in context_of(proc.stdout)
                   and 'Repair' in context_of(proc.stdout))

    # --- malformed payload ------------------------------------------------
    rc, out, err = run(None, env, raw="not json at all")
    crash_free("a malformed payload exits 0 with no output", rc, out, err, rc == 0 and out in ("", "{}"))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
