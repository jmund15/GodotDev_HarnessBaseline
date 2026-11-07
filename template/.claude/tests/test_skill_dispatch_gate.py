"""Compaction-scoped governance from the dispatch's actual reading, not a job claim."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import _hook_state as state
import model_ladder_gate as gate
import skill_load_marker as marker


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    for name in ("alpha", "beta"):
        skill = tmp_path / ".claude" / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# " + name + "\n", encoding="utf-8")
    payload = {"session_id": "govern01", "cwd": str(tmp_path), "tool_name": "Workflow",
               "tool_input": {"args": {"agents": [{"prompt": "Use `.claude/skills/alpha/reference/brief.md`."}]}}}
    state.mark_skill_in_context("govern01", "orchestration")
    gate.mark_loaded({**payload, "tool_name": "Read", "tool_input": {"file_path": gate.expected_path(payload)}})
    return tmp_path, payload


def load_skill(payload, name="alpha", agent_id=""):
    # The full Read route is also available to sidecars whose tool grant has no Skill tool.
    marker.process({**payload, "agent_id": agent_id, "tool_name": "Read",
                    "tool_input": {"file_path": str(Path(payload["cwd"]) / ".claude/skills" / name / "SKILL.md")}})


def test_clean_planted_violation_clean_and_compaction(session):
    root, payload = session
    load_skill(payload)
    assert gate.doctrine_check(payload) == (None, None), "clean loaded dispatch"
    state.clear_compaction_keys(payload["session_id"])
    reason, fault = gate.doctrine_check(payload)
    assert fault is None and 'Skill(skill="alpha")' in reason, "planted lost-context violation"
    assert 'Skill(skill="orchestration")' in reason and "model_ladder_evidence.md" in reason
    assert "re-issue" in reason and "orchestration §0" in reason
    load_skill(payload)
    state.mark_skill_in_context(payload["session_id"], "orchestration")
    gate.mark_loaded({**payload, "tool_name": "Read", "tool_input": {"file_path": gate.expected_path(payload)}})
    assert gate.doctrine_check(payload) == (None, None), "restored clean dispatch"


def test_summary_history_and_job_assertion_cannot_replace_load(session):
    _, payload = session
    state.update_json_locked(state.state_path(payload["session_id"]), lambda s: s.update(skills_loaded=["alpha"]))
    payload["tool_input"]["args"]["skillsLoaded"] = ["alpha"]
    assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]
    load_skill(payload, agent_id="child")
    assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]
    load_skill(payload)
    assert gate.doctrine_check(payload) == (None, None)


@pytest.mark.parametrize("slot", ["promptPath", "promptFile", "contextFiles", "mustRead", "contextPath", "contextPrefixPath"])
def test_every_reading_slot_is_observed(session, slot):
    root, payload = session
    brief = root / "brief.md"
    brief.write_text("Use `.claude/skills/alpha/reference/check.md`.\n", encoding="utf-8")
    job = {slot: [str(brief)] if slot in ("contextFiles", "mustRead") else str(brief)}
    payload["tool_input"]["args"] = ({slot: str(brief), "jobs": [{}]} if slot.startswith("contextP")
                                        else {"chains": [{"jobs": [job]}]})
    assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]


def test_project_owned_artifacts_and_all_unmet_skills(session):
    root, payload = session
    config = root / ".claude/hooks/skill_dispatch.project.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"owners": {"beta": ["design/locked/**"]}}), encoding="utf-8")
    payload["tool_input"]["args"]["agents"][0]["prompt"] += ' Build `design/locked/new.html` and use `.claude/skills/beta/SKILL.md`.'
    reason, fault = gate.doctrine_check(payload)
    assert fault is None and 'Skill(skill="alpha")' in reason and 'Skill(skill="beta")' in reason
    assert reason.count('Skill(skill="beta")') == 1
    load_skill(payload)
    load_skill(payload, "beta")
    assert gate.doctrine_check(payload) == (None, None)


def test_unowned_work_and_adjacent_names_are_untouched(session):
    _, payload = session
    for text in ("Fix shared/math.ts", "alpha is a word, not a skill reference", "`.claude/skills/absent/SKILL.md`"):
        payload["tool_input"]["args"] = {"agents": [{"prompt": text}]}
        assert gate.doctrine_check(payload) == (None, None)


@pytest.mark.parametrize("route", ["fanout", "direct", "inline-direct"])
def test_sidecar_routes_use_the_same_reading_contract(session, route):
    root, payload = session
    prompt = root / "brief.md"
    prompt.write_text("Use `.claude/skills/alpha/reference/brief.md`.\n", encoding="utf-8")
    jobs = root / "jobs.json"
    jobs.write_text(json.dumps([{"label": "seat", "promptFile": str(prompt)}]), encoding="utf-8")
    commands = {"fanout": f'python3 .claude/tools/sidecar_fanout.py "{jobs}"',
                "direct": f'bash .claude/scripts/any_sidecar.sh -f "{prompt}"',
                "inline-direct": "bash .claude/scripts/any_sidecar.sh 'Use `.claude/skills/alpha/SKILL.md`.'"}
    payload.update(tool_name="Bash", tool_input={"command": commands[route]})
    assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]
    load_skill(payload)
    assert gate.doctrine_check(payload) == (None, None)


def test_no_dispatch_and_read_only_fanout_mentions_do_not_gate(session):
    _, payload = session
    for cmd in ('git show HEAD:.claude/skills/alpha/SKILL.md',
                'python3 .claude/tools/sidecar_fanout.py jobs.json --dry-run',
                'python3 .claude/tools/sidecar_fanout.py --status jobs.json',
                'python3 .claude/tools/sidecar_fanout.py --help'):
        payload.update(tool_name="Bash", tool_input={"command": cmd})
        assert gate.doctrine_check(payload) == (None, None)


def test_workflow_real_hook_channel_clean_violation_clean(session):
    root, payload = session
    command = [sys.executable, str(HOOKS / "dispatch_mechanism_guard.py")]
    def invoke():
        result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                env=dict(os.environ), cwd=root)
        assert result.returncode in (0, 2) and "Traceback" not in result.stderr, result.stderr
        return json.loads(result.stdout or "{}")
    load_skill(payload)
    assert invoke().get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    state.clear_compaction_keys(payload["session_id"])
    blocked = invoke()["hookSpecificOutput"]
    assert blocked["permissionDecision"] == "deny" and 'Skill(skill="alpha")' in blocked["permissionDecisionReason"]
    load_skill(payload)
    state.mark_skill_in_context(payload["session_id"], "orchestration")
    gate.mark_loaded({**payload, "tool_name": "Read", "tool_input": {"file_path": gate.expected_path(payload)}})
    assert invoke().get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_project_artifact_alone_binds_its_skill(session):
    root, payload = session
    config = root / ".claude/hooks/skill_dispatch.project.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"owners": {"beta": ["design/locked/**"]}}), encoding="utf-8")
    payload["tool_input"]["args"] = {"agents": [{"prompt": "Build `design/locked/new.html`."}]}
    reason, fault = gate.doctrine_check(payload)
    assert fault is None and 'Skill(skill="beta")' in reason and 'Skill(skill="alpha")' not in reason


def test_unreadable_source_reports_lost_check_instead_of_claiming_no_owner(session):
    root, payload = session
    payload["tool_input"]["args"] = {"jobs": [{"promptPath": str(root / "missing.md")}]}
    reason, fault = gate.doctrine_check(payload)
    assert reason is None and "check incomplete" in fault and "missing.md" in fault


@pytest.mark.parametrize("content", ["{broken", "[]", '{"owners": []}'])
def test_bad_project_config_does_not_disable_skill_directory_observation(session, content):
    root, payload = session
    config = root / ".claude/hooks/skill_dispatch.project.json"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    reason, fault = gate.doctrine_check(payload)
    assert 'Skill(skill="alpha")' in reason and "check incomplete" in fault


def test_absolute_and_windows_style_skill_paths(session):
    root, payload = session
    skill = root / ".claude/skills/alpha/SKILL.md"
    for path in (skill.as_posix(), str(skill).replace("/", "\\")):
        payload["tool_input"]["args"] = {"agents": [{"prompt": f"Use `{path}:10`."}]}
        assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]


def test_foreign_project_reference_does_not_load_a_same_named_local_skill(session):
    root, payload = session
    foreign = root.parent / "other-project/.claude/skills/alpha/SKILL.md"
    payload["tool_input"]["args"] = {"agents": [{"prompt": f"Audit `{foreign.as_posix()}`."}]}
    assert gate.doctrine_check(payload) == (None, None)


def test_partial_read_or_grep_cannot_replace_skill_load(session):
    root, payload = session
    for tool, ti in (("Grep", {"path": str(root / ".claude/skills/alpha/SKILL.md")}),
                     ("Read", {"file_path": str(root / ".claude/skills/alpha/SKILL.md"), "limit": 1})):
        marker.process({**payload, "tool_name": tool, "tool_input": ti})
        assert 'Skill(skill="alpha")' in gate.doctrine_check(payload)[0]
    marker.process({**payload, "tool_name": "Skill", "tool_input": {"skill": "alpha"}})
    assert gate.doctrine_check(payload) == (None, None)


def test_long_unbroken_prose_does_not_exhaust_the_workflow_hook_budget(session):
    _, payload = session
    import dispatch_mechanism_guard
    payload["tool_input"]["args"]["agents"][0]["prompt"] = (
        "x" * (dispatch_mechanism_guard.MAX_BRIEF_BYTES + 100) + "\nUse `.claude/skills/alpha/SKILL.md`.\n")
    code = ("import json,sys; sys.path.insert(0,sys.argv[1]); import skill_dispatch_gate as g; "
            "print(json.dumps(g.required_skills(json.load(sys.stdin))))")
    result = subprocess.run([sys.executable, "-c", code, str(HOOKS)], input=json.dumps(payload),
                            capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [["alpha"], []]


def test_foreground_fanout_checks_newly_written_jobs_before_detaching(session, monkeypatch, capsys):
    root, payload = session
    import sidecar_dispatch_context as sidecar
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", payload["session_id"])
    monkeypatch.delenv("SIDECAR_DISPATCH_OWNER", raising=False)
    prompt = root / "new-brief.md"
    prompt.write_text("Use `.claude/skills/alpha/reference/brief.md`.\n", encoding="utf-8")
    jobs = root / "new-jobs.json"
    import model_registry
    alias = next(row["alias"] for row in model_registry.load()["models"] if "architect" in row.get("roles", []))
    jobs.write_text(json.dumps([{"label": "seat", "alias": alias, "effort": "high", "kind": "author",
                                "promptFile": str(prompt)}]), encoding="utf-8")
    reason = sidecar.fanout_doctrine_refusal([str(jobs), "--detach"], str(root))
    assert 'Skill(skill="alpha")' in reason
    load_skill(payload)
    assert sidecar.fanout_doctrine_refusal([str(jobs), "--detach"], str(root)) is None
    config = root / ".claude/hooks/skill_dispatch.project.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    assert sidecar.fanout_doctrine_refusal([str(jobs), "--detach"], str(root)) is None
    assert "Governing-skill check incomplete" in capsys.readouterr().err
