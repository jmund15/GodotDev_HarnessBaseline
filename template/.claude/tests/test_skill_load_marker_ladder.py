"""Proof cases for the ladder injection on Skill(orchestration) (hooks/skill_load_marker.py) and the
Workflow-time fallback it replaces (hooks/workflow_provider_guard.py): the rows arrive BEFORE a pin is
chosen, and only once per session."""
import json
import os
import re
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transport_fixture import hook_env  # noqa: E402

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
MARKER = os.path.join(HOOKS, "skill_load_marker.py")
GUARD = os.path.join(HOOKS, "workflow_provider_guard.py")
TIER = "(?:orchestrator|architect|executor|fanout|scout)"


def run(hook, payload):
    out = subprocess.run([sys.executable, hook], env=hook_env(), input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout).get("hookSpecificOutput", {}) if out.stdout.strip() else {}


def test_orchestration_load_injects_ladder_and_workflow_then_stays_quiet():
    sid = uuid.uuid4().hex     # state files key on the first 8 chars of the id — a shared prefix collides
    h = run(MARKER, {"tool_name": "Skill", "session_id": sid, "tool_input": {"skill": "orchestration"}})
    ctx = h.get("additionalContext", "")
    assert h.get("hookEventName") == "PostToolUse" and "[role ladder" in ctx and "luna:" in ctx, h  # family names: a new version never churns them
    # compact rows `model: tier, cost N` in ladder order, plus the pointer to the full table; never a cut cell
    assert "model_ladder_evidence.md" in ctx and "in ladder order" in ctx and "your choice" in ctx, ctx
    # rows are asserted by family name and tier-token shape; an effort, a model id or a family's tier claims
    # change with the data and the owner's rulings
    for family in ("fable", "opus", "sonnet", "haiku"):
        # a supplementary tier prints in place (`architect (supplementary)/executor`) or after the plain
        # tiers (`executor +architect (supplementary)`)
        mark = r"%s(?: \(supplementary\))?" % TIER
        assert re.search(r"\b%s: %s(?:/%s)*(?: \+%s \(supplementary\))?," % (family, mark, mark, TIER), ctx), (family, ctx)
    assert not re.search(r"claude-[a-z]+-\d|gpt-\d", ctx), ctx
    # Compact per row, not a total: the ladder gains rows by design. A row is a name, a tier list or its role
    # cell's short phrase, and an effort; a pasted role or ± cell runs to hundreds of characters.
    rows = ctx.split("model_ladder_evidence.md. ", 1)[-1].split("; ")
    assert "(effort:" not in ctx and all(len(r) <= 80 for r in rows), [r for r in rows if len(r) > 80]
    # the same session's Workflow call no longer repeats the rows
    g = run(GUARD, {"tool_name": "Workflow", "session_id": sid, "tool_input": {"name": "review-fanout"}})
    assert "[role ladder" not in g.get("additionalContext", ""), g


def test_workflow_authoring_load_names_the_dispatch_engine_once():
    sid = uuid.uuid4().hex
    payload = {"tool_name": "Skill", "session_id": sid, "tool_input": {"skill": "workflow-authoring"}}
    ctx = run(MARKER, payload).get("additionalContext", "")
    assert "/delegate" in ctx and "orchestration" in ctx and "Workflow(name=" not in ctx, ctx
    assert run(MARKER, payload) == {}, "fires once per compaction"
    sub = dict(payload, session_id=uuid.uuid4().hex, agent_id="sub1")
    assert run(MARKER, sub) == {}, "a subagent's load never gets the parent's note"


def test_other_skills_inject_nothing():
    h = run(MARKER, {"tool_name": "Skill", "session_id": "ladder-proof-other", "tool_input": {"skill": "testing"}})
    assert h == {}, h


def test_workflow_without_the_skill_gets_no_ladder_fallback():
    sid = uuid.uuid4().hex     # state files key on the first 8 chars of the id — a shared prefix collides
    g = run(GUARD, {"tool_name": "Workflow", "session_id": sid, "tool_input": {"name": "review-fanout"}})
    # the doctrine gate denies a call without the ladder in context; an admitted call repeats no rows
    assert "[role ladder" not in g.get("additionalContext", ""), g


def _read_marks(tool_input, tool="Read", tool_response=None):
    """skills_loaded after one PostToolUse payload, under a throwaway state dir."""
    import tempfile
    state = tempfile.mkdtemp()
    sid = uuid.uuid4().hex
    payload = {"tool_name": tool, "session_id": sid, "tool_input": tool_input}
    if tool_response is not None:
        payload["tool_response"] = tool_response
    out = subprocess.run([sys.executable, MARKER], env=hook_env(HARNESS_HOOK_STATE_DIR=state),
                         input=json.dumps(payload),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    path = os.path.join(state, sid[:8] + ".json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return json.load(handle).get("skills_loaded") or []


SKILL_MD = os.path.join(os.path.dirname(HOOKS), "skills", "instruction_quality", "SKILL.md")


def test_full_skill_read_marks_the_skill_loaded():
    assert _read_marks({"file_path": SKILL_MD}) == ["instruction_quality"]


def test_partial_skill_read_marks_nothing():
    assert _read_marks({"file_path": SKILL_MD, "limit": 40}) == []
    assert _read_marks({"file_path": SKILL_MD, "offset": 10}) == []


def test_errored_full_skill_read_marks_nothing():
    assert _read_marks({"file_path": SKILL_MD}, tool_response={"is_error": True}) == []
    assert _read_marks({"file_path": SKILL_MD}, tool_response={"is_error": False}) == ["instruction_quality"]


def test_other_reads_mark_nothing():
    other = os.path.join(os.path.dirname(HOOKS), "skills", "instruction_quality", "reference", "x.md")
    assert _read_marks({"file_path": other}) == []
    assert _read_marks({"file_path": os.path.join(os.path.dirname(HOOKS), "CLAUDE.md")}) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
