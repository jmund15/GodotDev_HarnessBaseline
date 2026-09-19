#!/usr/bin/env python3
"""Regression proof for prompt provenance in the registered routing-state reset hook."""
import json
import os
import subprocess
import sys
import tempfile

import _settings_probe

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETTINGS = _settings_probe.settings_path(os.path.join(ROOT, ".claude"))
BASENAME = "tool_routing_cumulative_reset.py"
NUDGE = os.path.join(ROOT, ".claude", "hooks", "tool_routing_nudge.py")


def registered_hook():
    with open(SETTINGS, encoding="utf-8") as fh:
        settings = json.load(fh)
    for group in settings["hooks"]["UserPromptSubmit"]:
        for hook in group.get("hooks", []):
            if BASENAME in hook.get("command", ""):
                return os.path.join(ROOT, ".claude", "hooks", BASENAME)
    raise AssertionError(f"{BASENAME} is not registered for UserPromptSubmit")


def run(hook, prompt, sid, env):
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": sid,
               "permission_mode": "default", "prompt": prompt}
    return subprocess.run([sys.executable, hook], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)


def run_raw(hook, raw, env):
    return subprocess.run([sys.executable, hook], input=raw,
                          capture_output=True, text=True, timeout=30, env=env)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    hook = registered_hook()
    tmp = tempfile.mkdtemp(prefix="routing_reset_")
    home = os.path.join(tmp, "home")
    state_dir = os.path.join(home, ".claude", ".routing_state")
    os.makedirs(state_dir, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_HOOK_STATE_DIR=state_dir,
               CLAUDE_PROJECT_DIR=ROOT, PYTHONIOENCODING="utf-8")
    sid = "trreset1"
    path = os.path.join(state_dir, sid[:8] + ".json")
    first = "Review the real spell status request with exact source evidence."
    r1 = run(hook, first, sid, env)
    state1 = load(path)

    # Plant peer state plus torn trailing bytes: the reset must salvage, then atomically replace.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(state1, peer_field=True,
                                 pre_nudges_fired_this_turn=["old"])) + " trailing")
    notification = """<task-notification>
Background AI tool returned statuses for the spell search.
</task-notification>"""
    r2 = run(hook, notification, sid, env)
    state2 = load(path)
    cross_session = """<cross-session-message from="peer-session">
Spell status review completed in another session.
</cross-session-message>"""
    r_cross = run(hook, cross_session, sid, env)
    state_cross = load(path)
    agent_message = """<agent-message from="routing-reader">
Spell status review completed by the delegated agent.
</agent-message>"""
    r_agent = run(hook, agent_message, sid, env)
    state_agent = load(path)
    second = "Inspect the real AI status behavior requested by the user."
    r3 = run(hook, second, sid, env)
    state3 = load(path)

    long_sid = "trreset2"
    long_prompt = ("context " * 600
                   + "Extract raw fields from every file and return one copyable entry per input path.")
    r_long = run(hook, long_prompt, long_sid, env)
    long_path = os.path.join(state_dir, long_sid[:8] + ".json")
    long_state = load(long_path)
    nudge_payload = {
        "tool_name": "Read", "session_id": long_sid,
        "tool_input": {"file_path": "C:/repo/input.md"},
    }
    r_nudge = subprocess.run([sys.executable, NUDGE], input=json.dumps(nudge_payload),
                             capture_output=True, text=True, timeout=30, env=env)

    non_object_results = [run_raw(hook, json.dumps(value), env)
                          for value in ("notice", ["notice"])]

    cases = [
        ("registered reset accepts a real UserPromptSubmit payload", r1.returncode == 0),
        ("real user request becomes last_prompt", state1.get("last_prompt") == first),
        ("background task notification does not replace user intent",
         state2.get("last_prompt") == first),
        ("cross-session transport does not replace user intent",
         state_cross.get("last_prompt") == first),
        ("agent-message transport does not replace user intent",
         state_agent.get("last_prompt") == first),
        ("notification still starts a new turn and clears per-turn receipts",
         state2.get("pre_nudges_fired_this_turn") == []),
        ("torn shared state is salvaged", state2.get("peer_field") is True),
        ("real user request after notifications updates intent",
         all(r.returncode == 0 for r in (r2, r_cross, r_agent, r3))
         and state3.get("last_prompt") == second),
        ("last_prompt keeps the full user request beyond 4,000 characters",
         r_long.returncode == 0 and long_state.get("last_prompt") == long_prompt),
        ("routing checks can see bulk-copyable cues past character 4,000",
         r_nudge.returncode == 0 and "read_files" in r_nudge.stdout),
        ("valid non-object JSON payloads are advisory no-ops",
         all(r.returncode == 0 and not r.stdout.strip() and not r.stderr.strip()
             for r in non_object_results)),
        ("hook remains advisory and silent",
         all(not r.stdout.strip() and not r.stderr.strip()
             for r in (r1, r2, r_cross, r_agent, r3, r_long))),
    ]
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
