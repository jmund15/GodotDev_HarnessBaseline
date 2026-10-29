#!/usr/bin/env python3
"""Regression proof for the registered UserPromptSubmit synthesis routing hook."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASENAME = "tool_routing_prompt_synthesis.py"


def registered_hook():
    """The shared input no longer registers this hook (a consumer opts in from its project file), so
    the proof drives the shipped file with the payload a registration would feed it."""
    hook = os.path.join(ROOT, ".claude", "hooks", BASENAME)
    if not os.path.isfile(hook):
        raise AssertionError(f"{BASENAME} is not shipped")
    return hook


def plant_env():
    """The hook narrows its advice when ai-worker is not registered on the machine: plant it registered."""
    root = tempfile.mkdtemp(prefix="trps_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"ai-worker": {"command": "python"}}}, fh)
    return dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_CONFIG_DIR=cfg,
                SC_WORKDIR=os.path.join(root, "nowhere"),
                WORKSTATION_PROFILE_PATH=os.path.join(root, "workstation_profile.json"))


def profile_env(tier, ai_worker):
    """An environment where this machine's tier and ai-worker registration read as planted."""
    root = tempfile.mkdtemp(prefix="trpsprofile_")
    cfg = os.path.join(root, "cfg")
    os.makedirs(os.path.join(cfg, "plugins"))
    servers = {"ai-worker": {"command": "python"}} if ai_worker == "present" else {}
    with open(os.path.join(cfg, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": servers}, fh)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", CLAUDE_CONFIG_DIR=cfg,
               SC_WORKDIR=os.path.join(root, "nowhere"),
               WORKSTATION_PROFILE_PATH=os.path.join(root, "workstation_profile.json"))
    if tier is not None:
        with open(env["WORKSTATION_PROFILE_PATH"], "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "local_tier": tier}, fh)
    return env


def run(hook, prompt, env=None):
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "trps0001",
               "permission_mode": "default", "prompt": prompt}
    result = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=30,
                            env=env or plant_env())
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def run_raw(hook, raw):
    return subprocess.run([sys.executable, hook], input=raw, capture_output=True, text=True,
                          timeout=30, env=plant_env())


SYNTHESIS = """Compare the existing hook files and summarize how routing decisions flow across
multiple modules. I need the exact source evidence preserved, the current contract checked, and a
recommendation about which behavior should remain. Look at these files as one bounded investigation,
but distinguish copyable facts from architectural judgment and line-level edits. Do not assume that
file count or directory names decide the route."""
NOTIFICATION = f"<task-notification>\n{SYNTHESIS}\n</task-notification>"
AGENT_MESSAGE = f'<agent-message from="routing-reader">\n{SYNTHESIS}\n</agent-message>'
CROSS_SESSION = f'<cross-session-message from="peer-session">\n{SYNTHESIS}\n</cross-session-message>'


def main():
    hook = registered_hook()
    output = run(hook, SYNTHESIS)
    lowered = output.lower()
    cases = [
        ("registered synthesis hook emits through UserPromptSubmit stdout",
         output.startswith("<user-prompt-submit-hook>")),
        ("advice preserves direct reads for known focused or line-level evidence",
         "known focused" in lowered and "line-level" in lowered),
        ("advice routes bulk copyable IO to read_files",
         "bulk copyable" in lowered and "read_files" in lowered),
        ("advice routes derived multi-file judgment through orchestration",
         "derived" in lowered and "orchestration" in lowered),
        # The bulk-copyable arm names a tool this machine may not have.
        ("an absent ai-worker names the local route instead of the worker",
         (lambda out: "read_files" not in out and "bounded Read" in out and "subagent" not in out)(
             run(hook, SYNTHESIS, env=profile_env("full", "absent")))),
        # A delegate cannot dispatch, so its advice names no delegate route (orchestration §0 owns delegation).
        ("a delegate's advice names no orchestration, delegate or subagent route",
         (lambda out: out.startswith("<user-prompt-submit-hook>") and "Known focused" in out
          and not any(w in out.lower() for w in ("orchestration", "delegate", "subagent")))(
             run(hook, SYNTHESIS, env=dict(profile_env("full", "absent"), CLAUDE_CODE_SIDECAR="1")))),
        ("a present ai-worker keeps the worker route",
         "read_files" in run(hook, SYNTHESIS, env=profile_env("full", "present"))),
        ("no profile keeps the worker route",
         "read_files" in run(hook, SYNTHESIS, env=profile_env(None, "present"))),
        ("advice rejects count/path routing",
         "not read count or path" in lowered),
        ("background task notification does not emit routing advice",
         run(hook, NOTIFICATION) in ("", "{}")),
        ("agent-message transport does not emit routing advice",
         run(hook, AGENT_MESSAGE) in ("", "{}")),
        ("cross-session transport does not emit routing advice",
         run(hook, CROSS_SESSION) in ("", "{}")),
        # Owner decision R11 (2026-09-14): two cues fire from about eight words.
        ("a short two-cue synthesis request fires",
         run(hook, "Summarize and compare every file in the hooks folder.").startswith("<user-prompt-submit-hook>")),
        ("a two-cue request under eight words stays silent",
         run(hook, "Compare and summarize these.") in ("", "{}")),
        ("an unrelated prompt remains silent",
         run(hook, "Please rename this local variable.") in ("", "{}")),
        ("valid non-object JSON payloads are advisory no-ops",
         all(r.returncode == 0 and r.stdout.strip() in ("", "{}") and not r.stderr.strip()
             for r in (run_raw(hook, raw) for raw in ('"notice"', "[1, 2]", "null", "42")))),
    ]
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
