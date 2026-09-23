#!/usr/bin/env python3
"""Re-runnable proof for hooks/post_edit_dispatch.py — the PostToolUse Write|Edit chain.

Seven separate settings.json commands became one dispatcher. Every sub-hook's advisory has
to survive, and they now merge into ONE additionalContext payload instead of seven
competing ones. Each case feeds a real PostToolUse payload by subprocess and asserts on the
emitted channel.

PostToolUse has no blocking sub-hook today, so the hard-block contract (a block chained
both before and after a non-blocking sub-hook still aborts the tool call) and the
fault-isolation contract run in-process against a synthetic chain.

State is redirected with HARNESS_HOOK_STATE_DIR/HOME/USERPROFILE — this never touches
~/.claude/.routing_state/.

    python3 .claude/tests/test_post_edit_dispatch.py
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
DISPATCH = os.path.join(HOOKS, "post_edit_dispatch.py")
SETTINGS = _settings_probe.settings_path(CLAUDE)

SUBHOOKS = ("check_logger_tag_prefix.py", "plan_memory_reminder.py",
            "design_surface_reminder.py", "load_steps_validator.py",
            "tres_format_guard.py", "harness_growth_guard.py",
            "self_eval_archive_guard.py", "retire_trigger_advisory.py")

GAME_PLAN = """# Spell AI status behavior

Implement the game behavior with deterministic coverage and retain the authored data
contract. The spell applies a status effect through the AI blackboard while the critter
is active. Tests cover activation, transition, expiry, and visible gameplay outcome.
The work remains within the named game systems and does not alter harness routing.
Review the existing abstractions before implementation and keep the status effect
loosely coupled to spell delivery.

## Critical files

`Spells/Effects/ArcStatusEffect.cs`, `NPCs/AI/CritterBlackboard.cs`,
`Tests/Logic/Spells/ArcStatusEffectTests.cs`.
The spell, AI behavior, and status effect are all production game scope.
"""

# load_steps says 9; the file carries 1 ext_resource + 0 sub_resource, so 2 is correct.
# The same file also lacks script_class= in its header, which is tres_format_guard's case.
BAD_TRES = """[gd_resource type="Resource" load_steps=9 format=3]

[ext_resource type="Script" path="res://Scripts/Probe.cs" id="1_probe"]

[resource]
script = ExtResource("1_probe")
value = 1
"""


def run(payload, env, script=None):
    r = subprocess.run([sys.executable, script or DISPATCH], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120, env=env,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def run_raw(text, env):
    r = subprocess.run([sys.executable, DISPATCH], input=text, capture_output=True,
                       text=True, timeout=120, env=env, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "")


def context(stdout):
    if not stdout or stdout == "{}":
        return ""
    specific = (json.loads(stdout).get("hookSpecificOutput") or {})
    assert specific.get("hookEventName") == "PostToolUse", specific
    return specific.get("additionalContext") or ""


def written(path, content, session="post0001", cwd=None, tool="Write"):
    payload = {"hook_event_name": "PostToolUse", "tool_name": tool, "session_id": session,
               "tool_input": {"file_path": path, "content": content},
               "tool_response": {"filePath": path}}
    if cwd:
        payload["cwd"] = cwd
    return payload


def plant(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def load_dispatch_module(blocked_import=None):
    spec = importlib.util.spec_from_file_location("post_edit_dispatch_probe", DISPATCH)
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
    with open(SETTINGS, encoding="utf-8") as fh:
        settings = json.load(fh)
    commands = []
    for group in settings["hooks"]["PostToolUse"]:
        if group.get("matcher") != "Write|Edit":
            continue
        commands.extend(h.get("command", "") for h in group.get("hooks", []))
    yield ("settings.json registers post_edit_dispatch.py for Write|Edit",
           any("post_edit_dispatch.py" in c for c in commands))
    yield ("no consolidated sub-hook keeps its own Write|Edit registration",
           not any(name in c for c in commands for name in SUBHOOKS))
    yield ("the Write|Edit PostToolUse surface is exactly one command", len(commands) == 1)


def main():
    tmp = tempfile.mkdtemp(prefix="post_edit_dispatch_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")
    cases = list(registration_cases())

    # --- clean edit -------------------------------------------------------
    clean_cs = plant(os.path.join(tmp, "repo", "Scripts", "Clean.cs"),
                     "public class Clean { public int Value = 1; }\n")
    rc, out, err = run(written(clean_cs, "public int Value = 1;"), env)
    cases.append(("a clean edit emits nothing and exits 0", rc == 0 and out in ("", "{}")))

    # --- check_logger_tag_prefix ------------------------------------------
    rc, out, err = run(written(clean_cs, 'JmoLogger.Info(this, "plain message");'), env)
    cases.append(("check_logger_tag_prefix still warns about an untagged call",
                  rc == 0 and "[logger_tag_prefix]" in context(out)))

    # --- plan_memory_reminder ---------------------------------------------
    plan_path = os.path.join(tmp, "repo", ".claude", "plans", "b2-game-fixture.md")
    rc, out, err = run(written(plan_path, GAME_PLAN, session="postplan"), env)
    cases.append(("plan_memory_reminder still fires on a game-scope plan",
                  rc == 0 and "Plan touches" in context(out)))

    # --- design_surface_reminder ------------------------------------------
    rc, out, err = run(written(clean_cs, "[Export] public bool Enabled = false;",
                               session="postdsgn"), env)
    cases.append(("design_surface_reminder still fires on an [Export] bool",
                  rc == 0 and "design_litmus" in context(out)))

    # --- load_steps_validator + tres_format_guard, merged -----------------
    tres = plant(os.path.join(tmp, "repo", "Data", "probe.tres"), BAD_TRES)
    rc, out, err = run(written(tres, BAD_TRES, session="posttres"), env)
    merged = context(out)
    cases.append(("load_steps_validator still reports a header mismatch",
                  rc == 0 and "[load_steps_validator]" in merged))
    cases.append(("tres_format_guard still reports a non-canonical header",
                  rc == 0 and "[tres-format-guard]" in merged))
    cases.append(("two advisories merge into ONE additionalContext payload",
                  out.count("additionalContext") == 1))

    # --- harness_growth_guard ---------------------------------------------
    repo = os.path.join(tmp, "growth")
    big_md = plant(os.path.join(repo, ".claude", "commands", "big.md"), "- " + "large " * 500)
    rc, out, err = run(written(big_md, "x", session="postgrow", cwd=repo), env)
    cases.append(("harness_growth_guard still reports a size/density measurement",
                  rc == 0 and "[harness-growth]" in context(out)))

    # --- self_eval_archive_guard ------------------------------------------
    archive = plant(os.path.join(tmp, "repo", "self_evaluate_archive.json"), "not json")
    rc, out, err = run(written(archive, "not json", session="postarch"), env)
    cases.append(("self_eval_archive_guard still reports an invalid archive",
                  rc == 0 and "[self-eval-archive-guard]" in context(out)))

    # --- retire_trigger_advisory --------------------------------------------
    no_trigger_md = ("---\nname: no-trigger\ndescription: fixture\n---\n\nBody.\n")
    memory_repo = os.path.join(tmp, "repo")
    plant(os.path.join(memory_repo, ".claude", "auto-memory", "archive", "probe.md"), no_trigger_md)
    memory_path = os.path.join(memory_repo, ".claude", "auto-memory", "archive", "probe.md")
    payload_memory = {"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": "postmem",
                      "cwd": memory_repo,
                      "tool_input": {"file_path": memory_path, "content": no_trigger_md},
                      "tool_response": {"type": "create", "filePath": memory_path}}
    rc, out, err = run(payload_memory, env)
    cases.append(("retire_trigger_advisory stays silent on a new memory file with no trigger",
                  rc == 0 and "[retire-trigger-advisory]" not in context(out)))
    malformed_md = ("---\nname: malformed\ndescription: fixture\nretire_when:\n---\n\nBody.\n")
    payload_malformed = dict(payload_memory, session_id="postmemm",
                             tool_input={"file_path": memory_path, "content": malformed_md})
    rc, out, err = run(payload_malformed, env)
    cases.append(("retire_trigger_advisory fires through the dispatcher on malformed retire_when",
                  rc == 0 and "[retire-trigger-advisory]" in context(out)))
    update_memory = dict(payload_memory, session_id="postmemu",
                         tool_response={"type": "update", "filePath": memory_path})
    rc, out, err = run(update_memory, env)
    cases.append(("retire_trigger_advisory stays silent on an update Write",
                  rc == 0 and "[retire-trigger-advisory]" not in context(out)))

    # --- malformed payload ------------------------------------------------
    rc, out, err = run_raw("not json at all", env)
    cases.append(("a malformed payload exits 0 with no output", rc == 0 and out in ("", "{}")))

    # --- dispatcher contract, synthetic chain ------------------------------
    cases.append(("E8: one sub-hook import fault cannot prevent the dispatcher from loading",
                  load_dispatch_module("harness_growth_guard") is not None))
    module = load_dispatch_module()
    noisy = ("noisy", lambda payload: {"context": "advisory-from-noisy"})
    blocker = ("blocker", lambda payload: {"block": "BLOCKED: planted"})
    boom_calls = []

    def boom(payload):
        boom_calls.append(1)
        raise RuntimeError("planted sub-hook fault")

    payload = written(clean_cs, "var x = 1;")

    rc, out, err = module.dispatch(payload, chain=(blocker, noisy))
    cases.append(("a block BEFORE a non-blocking sub-hook aborts the call",
                  rc == 2 and "BLOCKED: planted" in err and "advisory-from-noisy" not in out))

    rc, out, err = module.dispatch(payload, chain=(noisy, blocker))
    cases.append(("a block AFTER a non-blocking sub-hook still aborts the call",
                  rc == 2 and "BLOCKED: planted" in err and "advisory-from-noisy" not in out))

    rc, out, err = module.dispatch(payload, chain=(("plan_memory_reminder", boom), noisy))
    cases.append(("E8: a runtime fault preserves later output and names its hook",
                  rc == 0 and bool(boom_calls) and "advisory-from-noisy" in context(out)
                  and "plan_memory_reminder" in err))

    rc, out, err = module.dispatch(payload, chain=(noisy, ("plan_memory_reminder", boom)))
    cases.append(("E8: a runtime fault preserves prior output and names its hook",
                  rc == 0 and "advisory-from-noisy" in context(out)
                  and "plan_memory_reminder" in err))

    original_import_module = module.importlib.import_module

    def fail_import(name):
        if name == "harness_growth_guard":
            raise ImportError("planted import fault")
        return original_import_module(name)

    module.importlib.import_module = fail_import
    try:
        rc, out, err = module.dispatch(
            payload,
            chain=(("harness_growth_guard", "advisory"), noisy),
        )
    finally:
        module.importlib.import_module = original_import_module
    cases.append(("E8: an import fault preserves later output and names its hook",
                  rc == 0 and "advisory-from-noisy" in context(out)
                  and "harness_growth_guard" in err))

    def slow(payload):
        time.sleep(0.2)
        return {"context": "too-late"}

    module.SUBHOOK_TIMEOUT = 0.05
    started = time.monotonic()
    rc, out, err = module.dispatch(payload, chain=(noisy, ("slow_advisory", slow)))
    elapsed = time.monotonic() - started
    cases.append(("E8: a hung late check times out without losing prior warnings",
                  elapsed < 0.15 and "advisory-from-noisy" in context(out)
                  and "slow_advisory" in err and "timeout" in err))

    huge = ("huge_advisory", lambda payload: {"context": "x" * 1_000_000})
    rc, out, err = module.dispatch(payload, chain=(noisy, huge))
    cases.append(("E8: an oversized late context is bounded without losing prior warnings",
                  "advisory-from-noisy" in context(out) and len(out) < 100_000
                  and "huge_advisory" in err and "truncated" in err))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    for label in failures:
        print("  FAIL " + label)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
