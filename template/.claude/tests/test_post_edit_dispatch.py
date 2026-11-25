#!/usr/bin/env python3
"""Re-runnable proof for hooks/post_edit_dispatch.py — the PostToolUse Write|Edit chain.

Separate settings.json commands became one dispatcher. Every sub-hook's advisory has
to survive, and they merge into ONE additionalContext payload. Each case feeds a real PostToolUse payload by subprocess and asserts on the
emitted channel.

PostToolUse is advisory-only. Retired block/deny results cannot erase another
advisory. These and the fault-isolation cases run against a synthetic chain.

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
sys.path.insert(0, HOOKS)
import _optional_hooks  # noqa: E402

# check_logger_tag_prefix ships with a higher layer; a consumer without it runs only the dispatcher's own cases.
LOGGER_TAGS = _optional_hooks.present("check_logger_tag_prefix")
SETTINGS = _settings_probe.settings_path(CLAUDE)

SUBHOOKS = ("check_logger_tag_prefix",)

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
           not any(name + ".py" in c for c in commands for name in SUBHOOKS))
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
    if LOGGER_TAGS:
        logger_payload = written(clean_cs, 'JmoLogger.Info(this, "plain message");', tool="Edit")
        logger_payload["tool_input"] = {"file_path": clean_cs, "old_string": "var x = 1;",
                                        "new_string": 'JmoLogger.Info(this, "plain message");'}
        rc, out, err = run(logger_payload, env)
        cases.append(("check_logger_tag_prefix still warns about an untagged call",
                      rc == 0 and "[logger_tag_prefix]" in context(out)))

    # --- design_surface_reminder ------------------------------------------
    if _optional_hooks.present("design_surface_reminder"):
        rc, out, err = run(written(clean_cs, "[Export] public bool Enabled = false;", session="postdsgn"), env)
        cases.append(("design_surface_reminder fires on an [Export] bool through the dispatcher",
                      rc == 0 and "design_litmus" in context(out)))
        rc, out, err = run(written(clean_cs, "public int Value = 2;", session="postdsg2"), env)
        cases.append(("a plain .cs edit draws no design reminder", rc == 0 and "design_litmus" not in context(out)))
        md = plant(os.path.join(tmp, "repo", "Docs", "n.md"), "[Export] public bool Enabled = false;\n")
        rc, out, err = run(written(md, "[Export] public bool Enabled = false;", session="postdsg3"), env)
        cases.append(("the reminder is for .cs text only", rc == 0 and "design_litmus" not in context(out)))

    # Resource normalization now belongs to PreToolUse.
    tres = plant(os.path.join(tmp, "repo", "Data", "probe.tres"), BAD_TRES)
    rc, out, err = run(written(tres, BAD_TRES, session="posttres"), env)
    cases.append(("resource edits get no post-edit formatting advisory", rc == 0 and not context(out)))
    module = load_dispatch_module()
    cases.append(("only PreToolUse owns resource formatting",
                  'tres_format_guard' not in dict(module.CHAIN)))

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

    for retired in (blocker, ("denier", lambda payload: {"deny": "planted deny"})):
        for chain in ((retired, noisy), (noisy, retired)):
            rc, out, err = module.dispatch(payload, chain=chain)
            cases.append((f"retired {retired[0]} preserves advisories in either position",
                          rc == 0 and context(out) == "advisory-from-noisy"
                          and "permissionDecision" not in out and not err))

    for name in ("duplicate_test_double_guard", "floorcell_mutation_seam_guard",
                 "test_suite_gate_coverage_guard", "trail_mutation_seam_guard"):
        hook_path = os.path.join(HOOKS, name + ".py")
        if not os.path.isfile(hook_path):
            continue  # a project that does not carry this guard has no branch to retire
        with open(hook_path, encoding="utf-8") as fh:
            source = fh.read()
        cases.append((f"{name} retains standalone checks, not a hook branch",
                      "def standalone(" in source and "def hook(" not in source
                      and "--hook" not in source))

    rc, out, err = module.dispatch(payload, chain=(("check_logger_tag_prefix", boom), noisy))
    cases.append(("E8: a runtime fault preserves later output and names its hook",
                  rc == 0 and bool(boom_calls) and "advisory-from-noisy" in context(out)
                  and "check_logger_tag_prefix" in err))

    rc, out, err = module.dispatch(payload, chain=(noisy, ("check_logger_tag_prefix", boom)))
    cases.append(("E8: a runtime fault preserves prior output and names its hook",
                  rc == 0 and "advisory-from-noisy" in context(out)
                  and "check_logger_tag_prefix" in err))

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
        time.sleep(2.0)
        return {"context": "too-late"}

    module.SUBHOOK_TIMEOUT = 0.05
    started = time.monotonic()
    rc, out, err = module.dispatch(payload, chain=(noisy, ("slow_advisory", slow)))
    elapsed = time.monotonic() - started
    # The subhook sleeps 2 s; returning inside 1 s is the decision (the timeout, not the sleep, ended
    # it). A tighter bound tested the host's scheduler, not the timeout.
    cases.append(("E8: a hung late check times out without losing prior warnings",
                  elapsed < 1.0 and "advisory-from-noisy" in context(out)
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
