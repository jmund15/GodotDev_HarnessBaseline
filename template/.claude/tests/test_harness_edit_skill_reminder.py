"""Re-runnable proof for hooks/harness_edit_skill_reminder.py cadence and advisory channel.

The reminder fires once per compaction, with or without a loaded skill. Cases feed real
PreToolUse payloads and assert on the emitted channel.

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
sys.path.insert(0, HOOKS)
HOOK = os.path.join(HOOKS, "harness_edit_skill_reminder.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")
MARKER = os.path.join(HOOKS, "skill_load_marker.py")
POST_READ = os.path.join(HOOKS, "post_read_dispatch.py")
SKILL_MD = "C:/repo/.claude/skills/instruction_quality/SKILL.md"

from _claude_scope import harness_tail, in_nested_checkout  # noqa: E402

SID = "hesr0001"
HARNESS = "C:/repo/.claude/skills/testing/SKILL.md"
HOOKPY = "C:/repo/.claude/hooks/budget_posture.py"
NOT_HARNESS = "C:/repo/Spells/Fire/FireSpell.cs"
MEMORY = "C:/repo/.claude/auto-memory/gotcha_x.md"
LADDER = "C:/repo/.claude/reference/model_ladder_evidence.md"
TAGS = "C:/repo/.claude/skills/project_subsystems/adaptation.json"
LENSES = "C:/repo/.claude/commands/agents/session_audit_agents.md"

# A checkout at <repo>/.claude/worktrees/<name>/ carries a `.claude` segment in
# EVERY path inside it, so these are the two cells that separate the worktree
# root from the harness surface. Distinct sessions give each one a fresh
# advisory window, so ALLOW/NUDGE reflects the classification, not the dedup.
WT = "C:/repo/.claude/worktrees/sync-pr110"
WT_UNLOADED = "hesr0002"   # nothing loaded — a misclassification would DENY
WT_LOADED = "hesr0003"     # loaded — a fresh window, so a NUDGE proves harness
WT_LOADED2 = "hesr0004"

ALLOW, DENY, NUDGE = "allow", "deny", "nudge"


def run(hook, payload, env):
    r = subprocess.run([sys.executable, hook], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
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
                   capture_output=True, text=True, timeout=60,
                   env=dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"]))


def edit(path, env, session=SID, tool="Edit", agent=""):
    payload = {"tool_name": tool, "session_id": session, "tool_input": {"file_path": path}}
    if agent:
        payload["agent_id"] = agent
    return run(HOOK, payload, env)


def read(path, env, session, tool_input=None):
    """The real PostToolUse Read dispatcher, fed the Read payload shape of test_post_read_dispatch.py."""
    payload = {"tool_name": "Read", "session_id": session,
               "tool_input": dict(tool_input or {}, file_path=path),
               "tool_response": {"content": "# Instruction Quality"}}
    r = subprocess.run([sys.executable, POST_READ], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def mark_skill_loaded(state_dir, session, agent=""):
    """The real skill_load_marker, fed the PostToolUse payload of a Skill(instruction_quality) call."""
    payload = {"tool_name": "Skill", "session_id": session, "tool_input": {"skill": "instruction_quality"}}
    if agent:
        payload["agent_id"] = agent
    r = subprocess.run([sys.executable, MARKER],
                       input=json.dumps(payload),
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, HARNESS_HOOK_STATE_DIR=state_dir, PYTHONIOENCODING="utf-8"))
    assert r.returncode == 0, r.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="hesrstate_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    got, reason = edit(HARNESS, env)
    cases.append(("a harness edit before the skill is in context is denied, naming its one load route and the re-check",
                  got == DENY and "skills/instruction_quality/SKILL.md" in reason
                  and 'Skill(skill="instruction_quality")' in reason and "with no Skill tool" not in reason
                  and "re-issue" in reason))
    got, reason = edit(HOOKPY, env)
    cases.append(("a hook edit is denied the same way, naming all four class files and ending on the sections read",
                  got == DENY and all(f in reason for f in (
                      "skills_and_commands.md", "orchestration.md", "hooks.md", "claims_and_wording.md"))
                  and "against the sections you read" in reason
                  and "sections for its file class" not in reason))
    got, _ = edit(HARNESS, env)
    cases.append(("a second edit is denied again: a denial consumes no latch", got == DENY))

    mark_skill_loaded(tmp, SID)
    got, reason = edit(HARNESS, env)
    cases.append(("once the skill is loaded the edit passes with the reminder",
                  got == NUDGE and "instruction_quality" in reason))
    got, _ = edit(HARNESS, env)
    cases.append(("a second harness edit in the same session is silent", got == ALLOW))
    got, _ = edit(HOOKPY, env)
    cases.append(("and so is a hook edit", got == ALLOW))

    # A dispatched subagent shares the parent's session_id; its payload carries `agent_id`. Two Haiku
    # delegates edited doctrine unloaded on 2026-10-12 because the parent's load passed them.
    got, _ = edit(HARNESS, env, agent="agentsub01")
    cases.append(("a subagent's edit is denied while only its parent has loaded the skill", got == DENY))
    mark_skill_loaded(tmp, SID, agent="agentsub01")
    got, reason = edit(HARNESS, env, agent="agentsub01")
    cases.append(("the subagent's own load passes its edit", got in (NUDGE, ALLOW)))
    got, _ = edit(HARNESS, env, agent="agentsub02")
    cases.append(("a sibling subagent's load does not pass another subagent", got == DENY))
    mark_skill_loaded(tmp, "hesr0009", agent="agentsub03")
    got, _ = edit(HARNESS, env, session="hesr0009")
    cases.append(("a subagent's load does not pass its parent", got == DENY))

    precompact(env)
    got, reason = edit(HARNESS, env)
    cases.append(("a compaction drops the skill from context, so the gate denies again", got == DENY))
    mark_skill_loaded(tmp, SID)
    got, reason = edit(HARNESS, env)
    cases.append(("reloading after the compaction re-arms the advisory",
                  got == NUDGE and "instruction_quality" in reason))

    # A maintained artifact's editing rules reach every direct edit, after the latch too.
    got, reason = edit(LADDER, env)
    cases.append(("a ladder edit gets its maintaining command's rules after the reminder latched",
                  got == NUDGE and "/ladder_ingest" in reason))
    # The owner command does not load on a direct edit, so the note carries the cell rule itself
    # (/codify Step 0), held word for word to its owner so the copy cannot drift.
    with open(os.path.join(HERE, "..", "commands", "ladder_ingest.md"), encoding="utf-8") as fh:
        owner = fh.read()
    rule = "A cell states whatever decides a pin within the tiers the row claims"
    note = reason[reason.find(rule):] if rule in reason else ""
    note = note[:note.find("`/ladder_ingest`")].strip() if "`/ladder_ingest`" in note else note.strip()
    flat = lambda text: " ".join(text.split())
    cases.append(("the ladder note carries the cell rule, word for word as /ladder_ingest states it",
                  bool(note) and flat(note) in flat(owner)))
    # The cell-scope bullets live in the owner's list, so each is held to the note on its own.
    scope_keys = ("`effort` owns every rung", "An `effort` cell gives a start rung", "A clause is as wide as the evidence", "A rung earns a place",
                  "Every shape term", "Version takeover audits")
    bullets = [line[2:].replace("**", "") for line in owner.splitlines()
               if line.startswith("- **") and any(key in line for key in scope_keys)]
    cases.append(("the ladder note carries each cell-scope bullet, word for word as /ladder_ingest states it",
                  len(bullets) == len(scope_keys) and all(flat(b) in flat(reason) for b in bullets)))
    cases.append(("the ladder note carries the routing-verdict rule too, through its last sentence",
                  "Every work shape a source measured gets a routing verdict" in note
                  and "fails a name no ladder row covers." in note))
    got, reason = edit(LADDER, env)
    cases.append(("and on every later ladder edit", got == NUDGE and "/ladder_ingest" in reason))
    mark_skill_loaded(tmp, "hesr0007")
    got, reason = edit(HARNESS, env, session="hesr0007")
    cases.append(("another harness file gets no ladder note",
                  got == NUDGE and "/ladder_ingest" not in reason))
    mark_skill_loaded(tmp, "hesr0008")
    got, reason = edit(TAGS, env, session="hesr0008")
    cases.append(("a sensitivity-tag registry edit gets the tag-authoring rules",
                  got == NUDGE and "re-implements a guard" in reason and "§Sensitivity tags" in reason))
    mark_skill_loaded(tmp, "hesr0009")
    got, reason = edit(LENSES, env, session="hesr0009")
    cases.append(("a lens-registry edit gets the admission-label rule",
                  got == NUDGE and "admission: conditional" in reason and "§Mandate admission" in reason))

    # A Workflow delegate has no Skill tool: a full Read of SKILL.md is its load, a bounded Read is not.
    got, _ = edit(HARNESS, env, session="hesr0010")
    cases.append(("a delegate's harness edit is denied before any load", got == DENY))
    read(SKILL_MD, env, "hesr0010", {"offset": 60, "limit": 10})
    got, _ = edit(HARNESS, env, session="hesr0010")
    cases.append(("a bounded SKILL.md Read does not load the skill", got == DENY))
    read(SKILL_MD, env, "hesr0010")
    got, reason = edit(HARNESS, env, session="hesr0010")
    cases.append(("a full SKILL.md Read loads the skill: the edit passes", got == NUDGE))
    got, _ = edit(HOOKPY, env, session="hesr0010")
    cases.append(("and a hook edit passes too, since the marker is the same", got in (ALLOW, NUDGE)))
    read("C:/repo/.claude/rules/harness_tooling.md", env, "hesr0011")
    got, _ = edit(HARNESS, env, session="hesr0011")
    cases.append(("a full Read of another harness file does not load the skill", got == DENY))
    read("C:/repo/.claude/skills/testing/SKILL.md", env, "hesr0012")
    got, _ = edit(HARNESS, env, session="hesr0012")
    cases.append(("a full Read of another skill's SKILL.md does not load instruction_quality", got == DENY))
    got, reason = edit(HARNESS, env, session="hesr0013")
    cases.append(("the deny names the Read route as well as Skill, and the re-check",
                  got == DENY and 'Skill(skill="instruction_quality")' in reason
                  and "Read `skills/instruction_quality/SKILL.md` in full, no offset or limit" in reason))

    # Negatives.
    got, _ = edit(NOT_HARNESS, env)
    cases.append(("a production .cs file is not a harness surface", got == ALLOW))
    got, _ = edit(MEMORY, env)
    cases.append(("auto-memory is excluded", got == ALLOW))
    got, _ = run(HOOK, {"tool_name": "Bash", "session_id": SID,
                        "tool_input": {"command": "ls"}}, env)
    cases.append(("an adjacent tool is untouched", got == ALLOW))

    cases.append(("uppercase drive/backslash harness paths keep their tail spelling",
                  harness_tail(r"C:\\repo\\.CLAUDE\\worktrees\\SYNC-PR110\\.CLAUDE\\Hooks\\Rule.md")
                  == "Hooks/Rule.md"))
    cases.append(("dot segments are removed from an in-repo harness tail",
                  harness_tail("C:/repo/.claude/./skills/../rules/rule.md")
                  == "rules/rule.md"))
    cases.append(("dot segments that escape a worktree do not create a harness path",
                  harness_tail("C:/repo/.claude/worktrees/sync-pr110/./.claude/./hooks/../../Spells/FireSpell.cs")
                  is None))
    cases.append(("uppercase worktree game paths remain outside the harness",
                  harness_tail(r"C:\\repo\\.CLAUDE\\worktrees\\SYNC-PR110\\Spells\\FireSpell.cs")
                  is None))

    # Any .claude/.cache/<name>-worktrees/<checkout>/ layout re-bases the same way; other .cache dirs do not.
    cases.append(("a game file in a .claude/.cache/task-worktrees checkout is outside the harness",
                  harness_tail("C:/repo/.claude/.cache/task-worktrees/x/Visual/a.md") is None))
    cases.append(("a .cache dir not named *worktrees is not a checkout",
                  not in_nested_checkout("C:/repo/.claude/.cache/baseline-repo/x/Visual/a.md")))
    cases.append(("that checkout's own .claude/ tail is its harness surface",
                  harness_tail("C:/repo/.claude/.cache/task-worktrees/x/.claude/rules/r.md") == "rules/r.md"))

    # The third layout, .claude/.cache/baseline-worktrees/<name>/ (baseline_sync / baseline_publish).
    cases.append(("a game file in a .claude/.cache/baseline-worktrees checkout is outside the harness",
                  harness_tail("C:/repo/.claude/.cache/baseline-worktrees/x/Visual/a.md") is None))
    cases.append(("that checkout's own .claude/ tail is its harness surface too",
                  harness_tail("C:/repo/.claude/.cache/baseline-worktrees/x/.claude/rules/r.md") == "rules/r.md"))

    # A cache mirror under .claude/.cache/ is not guidance this session authors.
    got, _ = edit("C:/repo/.claude/.cache/baseline-repo/skills/x/SKILL.md", env, session="hesr0005")
    cases.append(("a .claude/.cache/ mirror file is not gated", got == ALLOW))
    cache_surface = "C:/repo/.claude/.cache/task-worktrees/x/.claude/skills/x/SKILL.md"
    got, _ = edit(cache_surface, env, session="hesr0006")
    cases.append(("a cache-hosted checkout's own .claude/ surface is gated", got == DENY))
    mark_skill_loaded(tmp, "hesr0006")
    got, _ = edit(cache_surface, env, session="hesr0006")
    cases.append(("and advised once the skill is loaded", got == NUDGE))

    # A worktree nested under .claude/worktrees/ re-bases onto its own root.
    got, reason = edit(WT + "/Visual/CREDITS.md", env, session=WT_UNLOADED)
    cases.append(("a repo file inside a .claude/worktrees checkout is not gated",
                  got == ALLOW))

    mark_skill_loaded(tmp, WT_LOADED)
    got, reason = edit(WT + "/.claude/hooks/foo.py", env, session=WT_LOADED)
    cases.append(("that checkout's own .claude/ surface is still gated",
                  got == NUDGE))

    mark_skill_loaded(tmp, WT_LOADED2)
    got, reason = edit(WT + "/.claude/CLAUDE.md", env, session=WT_LOADED2)
    cases.append(("and so is its CLAUDE.md", got == NUDGE))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
