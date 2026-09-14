"""Re-runnable proof for hooks/harness_edit_skill_reminder.py (B5 cadence + the deny gate).

The advisory is delivered once per session and re-armed by a compaction; the DENY path is
unconditional and must stay that way. Cases feed real PreToolUse payloads and assert on the
emitted channel.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_harness_edit_skill_reminder.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "harness_edit_skill_reminder.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "hesr0001"
HARNESS = "C:/repo/.claude/skills/testing/SKILL.md"
HOOKPY = "C:/repo/.claude/hooks/budget_posture.py"
NOT_HARNESS = "C:/repo/src/Domain/Service.cs"
MEMORY = "C:/repo/.claude/auto-memory/gotcha_x.md"

ALLOW, DENY, NUDGE = "allow", "deny", "nudge"


def run(hook, payload, env):
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, env=env)
    out = (r.stdout or "").strip()
    if not out or out == "{}":
        return ALLOW, ""
    doc = json.loads(out)
    hook_out = doc.get("hookSpecificOutput") or {}
    if hook_out.get("permissionDecision") == "deny":
        return DENY, hook_out.get("permissionDecisionReason", "")
    if hook_out.get("additionalContext"):
        return NUDGE, hook_out["additionalContext"]
    return ALLOW, out


def precompact(env, session=SID):
    """Fire the real PreCompact hook — it prints prose, not hook JSON."""
    subprocess.run([sys.executable, PRECOMPACT],
                   input=json.dumps({"session_id": session, "trigger": "auto"}),
                   capture_output=True, text=True, timeout=60, env=env)


def edit(path, env, session=SID, tool="Edit"):
    return run(HOOK, {"tool_name": tool, "session_id": session,
                      "tool_input": {"file_path": path}}, env)


def mark_skill_loaded(state_dir, session):
    """What skill_load_marker.py writes after a real Skill(instruction_quality) call."""
    path = os.path.join(state_dir, session[:8] + ".json")
    state = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    state["skills_loaded"] = ["instruction_quality"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def main():
    tmp = tempfile.mkdtemp(prefix="hesrstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    got, reason = edit(HARNESS, env)
    cases.append(("a harness edit is denied until the skill is loaded",
                  got == DENY and "instruction_quality" in reason))

    mark_skill_loaded(tmp, SID)
    got, reason = edit(HARNESS, env)
    cases.append(("the first edit after loading gets the advisory",
                  got == NUDGE and "Core Code Conventions" in reason))

    got, _ = edit(HOOKPY, env)
    cases.append(("a second harness edit in the same session is silent", got == ALLOW))

    precompact(env)
    got, reason = edit(HARNESS, env)
    cases.append(("a compaction re-arms the advisory",
                  got == NUDGE and "instruction_quality" in reason))

    # Negatives.
    got, _ = edit(NOT_HARNESS, env)
    cases.append(("a production .cs file is not a harness surface", got == ALLOW))
    got, _ = edit(MEMORY, env)
    cases.append(("auto-memory is excluded", got == ALLOW))
    got, _ = run(HOOK, {"tool_name": "Bash", "session_id": SID,
                        "tool_input": {"command": "ls"}}, env)
    cases.append(("an adjacent tool is untouched", got == ALLOW))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
