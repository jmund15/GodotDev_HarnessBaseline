#!/usr/bin/env python3
"""Re-runnable proof for hooks/pre_edit_dispatch.py — the PreToolUse Write|Edit chain.

Four separate settings.json commands became one dispatcher. Consolidation is only safe if
every sub-hook keeps the channel it had: pattern_enforcer blocks with exit 2 + stderr,
harness_edit_skill_reminder denies with a permissionDecision, readonly_lens_write_guard
writes an audit note to stderr without blocking, and running_script_edit_guard advises
through additionalContext. Each case below feeds a real PreToolUse payload by subprocess
and asserts on the emitted channel.

Five contract cases run the dispatcher in-process against a synthetic chain, because the
real chain cannot express them: a hard block positioned AFTER a non-blocking sub-hook, and
a sub-hook that raises. Both are dispatcher invariants, not sub-hook behavior.

The planted pattern_enforcer violation is assembled at runtime — spelled literally, this
file would deny its own Write.

State is redirected with HARNESS_HOOK_STATE_DIR/HOME/USERPROFILE — this never touches
~/.claude/.routing_state/.

    python3 .claude/tests/test_pre_edit_dispatch.py
"""
import builtins
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

import _settings_probe

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(CLAUDE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
TOOLS = os.path.join(CLAUDE, "tools")
sys.path.insert(0, TOOLS)
from sidecar_launch import git_bash  # noqa: E402

DISPATCH = os.path.join(HOOKS, "pre_edit_dispatch.py")
SETTINGS = _settings_probe.settings_path(CLAUDE)

SUBHOOKS = ("pattern_enforcer.py", "harness_edit_skill_reminder.py",
            "readonly_lens_write_guard.py", "running_script_edit_guard.py")

# Assembled, never spelled: pattern_enforcer denies any Write whose content carries it.
EXPORT_NULL_VIOLATION = "[Export] public Node Thing = " + "null" + "!;"


def run(payload, env):
    r = subprocess.run([sys.executable, DISPATCH], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120, env=env,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def run_raw(text, env):
    r = subprocess.run([sys.executable, DISPATCH], input=text, capture_output=True,
                       text=True, timeout=120, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def hook_output(stdout):
    """(permissionDecision, additionalContext) from a dispatcher stdout payload."""
    if not stdout or stdout == "{}":
        return None, ""
    specific = (json.loads(stdout).get("hookSpecificOutput") or {})
    assert specific.get("hookEventName") == "PreToolUse", specific
    return specific.get("permissionDecision"), specific.get("additionalContext") or ""


def edit(path, new_string, session="pre00001", **extra):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": session,
               "tool_input": {"file_path": path, "old_string": "x", "new_string": new_string}}
    payload.update(extra)
    return payload


def write_state(state_dir, session, obj):
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, session[:8] + ".json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def load_dispatch_module(blocked_import=None):
    spec = importlib.util.spec_from_file_location("pre_edit_dispatch_probe", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == blocked_import:
            raise ImportError("planted import fault")
        return original_import(name, *args, **kwargs)

    try:
        builtins.__import__ = guarded_import
        spec.loader.exec_module(module)
    except ImportError:
        return None
    finally:
        builtins.__import__ = original_import
    return module


def registration_cases():
    """settings.json registers the dispatcher for Write|Edit and no sub-hook separately."""
    with open(SETTINGS, encoding="utf-8") as fh:
        settings = json.load(fh)
    commands = []
    for group in settings["hooks"]["PreToolUse"]:
        if group.get("matcher") != "Write|Edit":
            continue
        commands.extend(h.get("command", "") for h in group.get("hooks", []))
    yield ("settings.json registers pre_edit_dispatch.py for Write|Edit",
           any("pre_edit_dispatch.py" in c for c in commands))
    yield ("no consolidated sub-hook keeps its own Write|Edit registration",
           not any(name in c for c in commands for name in SUBHOOKS))
    yield ("the Write|Edit PreToolUse surface is exactly one command", len(commands) == 1)


def live_script_instance(tmp):
    """Launch a real `bash <.claude/scripts/...sh>` so the guard has something to find."""
    script_dir = os.path.join(tmp, "repo", ".claude", "scripts")
    os.makedirs(script_dir, exist_ok=True)
    script = os.path.join(script_dir, "harness_b2_probe.sh")
    with open(script, "w", newline="\n", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env bash\nsleep 120\n")
    bash = git_bash()
    if not bash:
        return script, None
    proc = subprocess.Popen([bash, script.replace("\\", "/")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)  # let the process table catch up before the scan
    if proc.poll() is not None:
        return script, None
    return script, proc


def main():
    tmp = tempfile.mkdtemp(prefix="pre_edit_dispatch_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")
    cases = list(registration_cases())

    game_cs = os.path.join(ROOT, "Scripts", "Spells", "SpellInstance.cs")

    # --- clean edit -------------------------------------------------------
    rc, out, err = run(edit(game_cs, "var x = 1;"), env)
    cases.append(("a clean edit emits nothing and exits 0",
                  rc == 0 and out in ("", "{}") and err == ""))

    # --- pattern_enforcer: hard block, first in the chain ------------------
    rc, out, err = run(edit(game_cs, EXPORT_NULL_VIOLATION), env)
    cases.append(("pattern_enforcer still blocks with exit 2 + stderr",
                  rc == 2 and "BLOCKED" in err))
    cases.append(("a blocked call emits no advisory on stdout", out in ("", "{}")))

    # --- harness_edit_skill_reminder: deny, then advisory ------------------
    harness_md = os.path.join(CLAUDE, "commands", "explore.md")
    rc, out, err = run(edit(harness_md, "text", session="predeny1"), env)
    decision, ctx = hook_output(out)
    cases.append(("harness_edit_skill_reminder still denies an unguarded harness edit",
                  rc == 0 and decision == "deny"))

    write_state(state_dir, "preload1", {"skills_loaded": ["instruction_quality"]})
    rc, out, err = run(edit(harness_md, "text", session="preload1"), env)
    decision, ctx = hook_output(out)
    cases.append(("with the skill loaded the reminder advises instead of denying",
                  rc == 0 and decision is None and "instruction_quality" in ctx))

    # --- readonly_lens_write_guard: non-blocking stderr note ---------------
    marker = os.path.join(state_dir, "readonly-prelens1.json")
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump({"expires_at": time.time() + 600,
                   "allow_prefixes": [os.path.join(tmp, "allowed")]}, fh)
    rc, out, err = run(edit(game_cs, "var x = 1;", session="prelens1", agent_id="agent-42"), env)
    cases.append(("readonly_lens_write_guard still reports on stderr without blocking",
                  rc == 0 and "[readonly-lens-write]" in err))

    # --- running_script_edit_guard: additionalContext advisory -------------
    script, proc = live_script_instance(tmp)
    if proc is None:
        cases.append(("running_script_edit_guard planted case COULD NOT RUN (no bash)", False))
    else:
        try:
            rc, out, err = run(edit(script, "echo hi", session="prerun01"), env)
            decision, ctx = hook_output(out)
            cases.append(("running_script_edit_guard still advises through additionalContext",
                          rc == 0 and "RUNNING INSTANCE DETECTED" in ctx))
        finally:
            proc.kill()
            proc.wait(timeout=30)

    # --- malformed payload ------------------------------------------------
    rc, out, err = run_raw("not json at all", env)
    cases.append(("a malformed payload exits 0 with no output", rc == 0 and out in ("", "{}")))

    # --- dispatcher contract, synthetic chain ------------------------------
    cases.append(("E4: an advisory import fault cannot prevent the dispatcher from loading",
                  load_dispatch_module("readonly_lens_write_guard") is not None))
    cases.append(("E4: an enforcement import fault cannot prevent the dispatcher from loading",
                  load_dispatch_module("pattern_enforcer") is not None))
    module = load_dispatch_module()
    noisy = ("noisy", lambda payload: {"context": "advisory-from-noisy"})
    blocker = ("blocker", lambda payload: {"block": "BLOCKED: planted"})
    denier = ("denier", lambda payload: {"deny": "planted deny"})
    boom_calls = []

    def boom(payload):
        boom_calls.append(1)
        raise RuntimeError("planted sub-hook fault")

    payload = edit(game_cs, "var x = 1;")

    rc, out, err = module.dispatch(payload, chain=(blocker, noisy))
    cases.append(("a block BEFORE a non-blocking sub-hook aborts the call",
                  rc == 2 and "BLOCKED: planted" in err and "advisory-from-noisy" not in out))

    rc, out, err = module.dispatch(payload, chain=(noisy, blocker))
    cases.append(("a block AFTER a non-blocking sub-hook still aborts the call",
                  rc == 2 and "BLOCKED: planted" in err and "advisory-from-noisy" not in out))

    rc, out, err = module.dispatch(payload, chain=(noisy, denier))
    decision, ctx = hook_output(out)
    cases.append(("a permissionDecision deny AFTER a non-blocking sub-hook still aborts",
                  rc == 0 and decision == "deny" and "advisory-from-noisy" not in (ctx or "")))

    rc, out, err = module.dispatch(payload, chain=(("pattern_enforcer", boom), noisy))
    decision, ctx = hook_output(out)
    cases.append(("E4: an enforcement runtime fault fails closed and names its hook",
                  rc == 0 and decision == "deny" and "pattern_enforcer" in err))

    rc, out, err = module.dispatch(payload, chain=(noisy, ("running_script_edit_guard", boom)))
    decision, ctx = hook_output(out)
    cases.append(("E4: an advisory runtime fault preserves prior output and names its hook",
                  rc == 0 and "advisory-from-noisy" in (ctx or "")
                  and "running_script_edit_guard" in err))

    rc, out, err = module.dispatch(
        payload,
        chain=(("pattern_enforcer", lambda payload: "invalid result"),),
    )
    decision, ctx = hook_output(out)
    cases.append(("E4: a malformed enforcement result fails closed and names its hook",
                  rc == 0 and decision == "deny" and "pattern_enforcer" in err))

    original_import_module = module.importlib.import_module

    def fail_pattern_import(name):
        if name == "pattern_enforcer":
            raise ImportError("planted import fault")
        return original_import_module(name)

    module.importlib.import_module = fail_pattern_import
    try:
        rc, out, err = module.dispatch(payload, chain=(("pattern_enforcer", "enforcement"),))
    finally:
        module.importlib.import_module = original_import_module
    decision, ctx = hook_output(out)
    cases.append(("E4: an enforcement import fault fails closed and names its hook",
                  rc == 0 and decision == "deny" and "pattern_enforcer" in err))

    # A faulted guard that denied every edit would also deny the edit that repairs it.
    repair = edit(os.path.join(HOOKS, "pattern_enforcer.py"), "repaired = True")
    rc, out, err = module.dispatch(repair, chain=(("pattern_enforcer", boom),))
    decision, ctx = hook_output(out)
    cases.append(("E4: an enforcement runtime fault still allows the edit that repairs a hook",
                  rc == 0 and decision != "deny" and "pattern_enforcer" in err))
    module.importlib.import_module = fail_pattern_import
    try:
        rc, out, err = module.dispatch(repair, chain=(("pattern_enforcer", "enforcement"),))
    finally:
        module.importlib.import_module = original_import_module
    decision, ctx = hook_output(out)
    cases.append(("E4: an enforcement import fault still allows repairing a hook file",
                  rc == 0 and decision != "deny" and "pattern_enforcer" in err))
    helper_repair = edit(os.path.join(HOOKS, "_hook_state.py"), "repaired = True")
    rc, out, err = module.dispatch(helper_repair, chain=(("pattern_enforcer", boom),))
    decision, ctx = hook_output(out)
    cases.append(("E4: an enforcement fault still allows repairing a shared hook helper",
                  rc == 0 and decision != "deny"))

    def fail_advisory_import(name):
        if name == "running_script_edit_guard":
            raise ImportError("planted import fault")
        return original_import_module(name)

    module.importlib.import_module = fail_advisory_import
    try:
        rc, out, err = module.dispatch(
            payload,
            chain=(("running_script_edit_guard", "advisory"), noisy),
        )
    finally:
        module.importlib.import_module = original_import_module
    decision, ctx = hook_output(out)
    cases.append(("E4: an advisory import fault preserves later output and names its hook",
                  rc == 0 and "advisory-from-noisy" in (ctx or "")
                  and "running_script_edit_guard" in err))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
