#!/usr/bin/env python3
"""Proof for the compaction-scoped model-ladder readiness marker."""
import importlib.util
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"


def load(name):
    path = HOOKS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE = HERE / "fixtures" / "model_ladder_gate_bash_result.json"
SETTINGS = HERE.parent / "settings.base.json"
BARE = "python3 .claude/tools/model_registry.py for-role executor fanout"


def _capacity_cache(tmp):
    """A fresh cache reading for every plan-quota transport: `for-role` never probes the machine's plans."""
    tools = str(HERE.parent / "tools")
    for where in (tools, str(HERE)):
        if where not in sys.path:
            sys.path.insert(0, where)
    import model_registry
    from _capacity_fixtures import write_capacity_cache
    return write_capacity_cache(Path(tmp) / "capacity.json", model_registry.load())


def _bash_credit_cases(cases, gate, hook_state, project):
    """A staged `for-role` receipt credits only the stdout that reaches context. Each payload's `tool_response`
    takes its keys from the captured real shape in FIXTURE and the run's own stdout; the non-zero-exit case adds
    `exitCode`, a key the captured shape lacks."""
    import contextlib
    import io
    try:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fixture = {}
    shape = fixture.get("for_role") or {}
    cases.append(("the captured Bash result fixture loads", bool(shape.get("toolUseResult"))))
    tools = str(HERE.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import model_registry
    cache = _capacity_cache(tempfile.mkdtemp(prefix="ladder_gate_capacity_"))
    saved_cache = os.environ.get("PROVIDER_CAPACITY_CACHE")
    os.environ["PROVIDER_CAPACITY_CACHE"] = str(cache)

    def run_role(session, args):
        previous = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = session
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = model_registry.main(list(args))
        finally:
            if previous is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = previous
        return rc, out.getvalue()

    def bash(session, command=None, **response):
        return {"tool_name": "Bash", "session_id": session,
                "tool_input": {"command": command or BARE},
                "tool_response": dict(shape.get("toolUseResult") or {}, **response)}

    def stage(session, text, tiers=("executor", "fanout")):
        stage_fn, digest = getattr(hook_state, "stage_ladder_receipt", None), getattr(hook_state, "stdout_sha256", None)
        if stage_fn is None or digest is None:
            return False
        return bool(stage_fn(session, digest(text), list(tiers)))

    def failure(session):
        reader = getattr(hook_state, "ladder_receipt_failure", None)
        return reader(session) if reader else None

    try:
        rc, full = run_role("bashcredit1", ["for-role", "executor", "fanout", "--from", "anthropic"])
        cases.append(("a bare for-role run prints its table", rc == 0 and bool(full.strip())))
        delivered = gate.mark_loaded(bash("bashcredit1", stdout=full.replace("\n", "\r\n")))
        cases.append(("a delivered for-role stdout (CRLF, as the client returns it) credits the staged tiers",
                      delivered is True and hook_state.ladder_slice_tiers("bashcredit1") == ["executor", "fanout"]))
        captured = (fixture.get("for_role") or {}).get("toolUseResult", {}).get("stdout") or ""
        rc, full = run_role("bashtrim1", ["for-role", "executor", "fanout", "--from", "anthropic"])
        cases.append(("the client trims the trailing newline off a Bash stdout (the captured result ends without one), "
                      "and a bare for-role run so delivered still credits its tiers",
                      rc == 0 and full.endswith("\n") and bool(captured) and not captured.endswith("\n")
                      and gate.mark_loaded(bash("bashtrim1", stdout=full.rstrip("\n"))) is True
                      and hook_state.ladder_slice_tiers("bashtrim1") == ["executor", "fanout"]))

        rc, full = run_role("bashhead1", ["for-role", "executor", "--from", "anthropic"])
        head = "".join(full.splitlines(True)[:5])
        piped = gate.mark_loaded(bash("bashhead1", command=BARE + " | head -5", stdout=head))
        cases.append(("a for-role run piped through head credits nothing and records why",
                      rc == 0 and not piped and hook_state.ladder_slice_tiers("bashhead1") == []
                      and bool(failure("bashhead1"))))

        stage("bashtail1", full)
        cases.append(("a for-role run piped through tail credits nothing",
                      not gate.mark_loaded(bash("bashtail1", command=BARE + " | tail -5",
                                                stdout="".join(full.splitlines(True)[-5:])))))
        stage("bashgrep1", full)
        line = next((l for l in full.splitlines(True) if "role" in l), "")
        cases.append(("a for-role run piped through grep credits nothing",
                      not gate.mark_loaded(bash("bashgrep1", command=BARE + " | grep -i role", stdout=line))))
        stage("bashredir1", full)
        cases.append(("a for-role run redirected to a file credits nothing",
                      not gate.mark_loaded(bash("bashredir1", command=BARE + " > .claude/scratch/role.txt",
                                                stdout=""))))
        stage("bashexit1", full)
        cases.append(("a non-zero exit never credits even with matching stdout",
                      not gate.mark_loaded(bash("bashexit1", stdout=full, exitCode=2))))
        persisted = fixture.get("persisted") or {}
        ptext = (persisted.get("toolUseResult") or {}).get("stdout") or ""
        stage("bashpersist1", ptext)
        cases.append(("a client-persisted result never credits",
                      not gate.mark_loaded({"tool_name": "Bash", "session_id": "bashpersist1",
                                            "tool_input": {"command": BARE},
                                            "tool_response": persisted.get("toolUseResult") or {}})))
        stage("bashintr1", full)
        cases.append(("an interrupted call never credits",
                      not gate.mark_loaded(bash("bashintr1", stdout=full, interrupted=True))))
        stage("bashmention1", full)
        cases.append(("a quoted mention of the command never credits",
                      not gate.mark_loaded(bash("bashmention1", command='echo "%s"' % BARE, stdout=full))))
        stage("bashoth1", full)
        cases.append(("another session's payload never credits",
                      not gate.mark_loaded(bash("bashoth2", stdout=full))))
        stage("bashcompact1", full)
        hook_state.clear_compaction_keys("bashcompact1")
        cases.append(("a post-compaction payload never credits",
                      not gate.mark_loaded(bash("bashcompact1", stdout=full))))

        hook_state.mark_skill_in_context("bashdn1", "orchestration")
        hook_state.mark_ladder_slice("bashdn1", ["fanout"])
        reason, _ = gate.doctrine_check({"session_id": "bashdn1", "cwd": str(project)},
                                        tiers=lambda: {"executor": ["fold"], "fanout": ["lens-a"]})
        cases.append(("the deny names each missing tier and the call's consumer of it",
                      "executor (fold)" in (reason or "") and "lens-a" not in (reason or "")))
        hook_state.mark_skill_in_context("bashdn3", "orchestration")
        reason, _ = gate.doctrine_check({"session_id": "bashdn3", "cwd": str(project)},
                                        tiers=lambda: ["executor", "architect"])
        cases.append(("a caller that names no consumer gets the bare tier, never an invented one",
                      "for executor, architect" in (reason or "") or "for architect, executor" in (reason or "")))
        hook_state.mark_skill_in_context("bashdn2", "orchestration")
        reason, _ = gate.doctrine_check({"session_id": "bashdn2", "cwd": str(project)},
                                        tiers=lambda: {"executor": ["fold"]}, embed_slice=True)
        cases.append(("the embedded-slice step names the tier it embeds",
                      "read the ladder slice for executor (fold) below" in (reason or "")))
        hook_state.mark_skill_in_context("bashfail1", "orchestration")
        stage("bashfail1", full, ["executor"])
        gate.mark_loaded(bash("bashfail1", command=BARE + " | head -5", stdout=head))
        reason, _ = gate.doctrine_check({"session_id": "bashfail1", "cwd": str(project)},
                                        tiers=lambda: ["executor"])
        cases.append(("the next deny names the recorded failed delivery",
                      "did not reach context" in (reason or "")))

        rc, out = run_role("bashjson1", ["for-role", "architect", "--json", "--from", "anthropic"])
        state = hook_state.read_json_salvage(hook_state.state_path("bashjson1"))
        cases.append(("--json exits cleanly and stages no receipt",
                      rc == 0 and out.lstrip().startswith("{") and "ladder_receipt" not in state))
        cases.append(("a --json call never credits",
                      not gate.mark_loaded({"tool_name": "Bash", "session_id": "bashjson1",
                                            "tool_input": {"command": "python3 .claude/tools/model_registry.py "
                                                                     "for-role architect --json"},
                                            "tool_response": {"stdout": out, "stderr": "", "interrupted": False}})))
        import subprocess
        cli_sid = "bashcli1"
        _, cli_stdout = run_role(cli_sid, ["for-role", "executor", "--from", "anthropic"])
        proc = subprocess.run([sys.executable, str(HOOKS / "model_ladder_gate.py"), "--bash-credit"],
                              input=json.dumps(bash(cli_sid, stdout=cli_stdout)), capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        cases.append(("the registered --bash-credit entry credits a delivered run on stdin",
                      proc.returncode == 0 and not proc.stdout.strip()
                      and hook_state.ladder_slice_tiers(cli_sid) == ["executor"]))
    finally:
        if saved_cache is None:
            os.environ.pop("PROVIDER_CAPACITY_CACHE", None)
        else:
            os.environ["PROVIDER_CAPACITY_CACHE"] = saved_cache

    try:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    entries = [entry for item in ((settings.get("hooks") or {}).get("PostToolUse") or [])
               if item.get("matcher") == "Bash" for entry in item.get("hooks") or []]
    cases.append(("settings.base.json registers the if-filtered for-role Bash credit",
                  any(entry.get("if") == "Bash(*model_registry.py*for-role*)"
                      and "model_ladder_gate.py" in str(entry.get("command") or "") for entry in entries)))


def main():
    gate_path = HOOKS / "model_ladder_gate.py"
    if not gate_path.is_file():
        print("FAIL model_ladder_gate.py exists")
        return 1
    state = tempfile.mkdtemp(prefix="ladder_gate_state_")
    project = Path(tempfile.mkdtemp(prefix="ladder_gate_project_"))
    ladder = project / ".claude" / "reference" / "model_ladder_evidence.md"
    ladder.parent.mkdir(parents=True)
    ladder.write_text("# Model Ladder\n", encoding="utf-8")
    old = dict(os.environ)
    os.environ["HARNESS_HOOK_STATE_DIR"] = state
    os.environ["CLAUDE_PROJECT_DIR"] = str(project)
    try:
        gate = load("model_ladder_gate")
        hook_state = load("_hook_state")
        sid = "ladder01"

        def payload(tool="Read", path=None, **extra):
            tool_input = {"file_path": str(path or ladder)}
            tool_input.update(extra)
            return {"tool_name": tool, "session_id": sid, "tool_input": tool_input}

        cases = []
        cases.append(("full exact-path Read arms the marker", gate.mark_loaded(payload())))
        cases.append(("a loaded ladder authorizes a dispatch", gate.claim_loaded(sid)))
        cases.append(("a dispatch does not consume the marker", gate.claim_loaded(sid)))
        cases.append(("offset Read cannot arm", not gate.mark_loaded(payload(offset=1))))
        cases.append(("limited Read cannot arm", not gate.mark_loaded(payload(limit=20))))
        cases.append(("Grep cannot substitute for a full load", not gate.mark_loaded(payload(tool="Grep"))))
        cases.append(("a prose-adjacent file cannot arm", not gate.mark_loaded(
            payload(path=project / ".claude" / "plans" / "mentions-model-ladder.md"))))
        failed_read = payload()
        failed_read["tool_response"] = {"is_error": True}
        cases.append(("a failed Read cannot arm", not gate.mark_loaded(failed_read)))

        gate.mark_loaded(payload())
        hook_state.clear_compaction_keys(sid)
        cases.append(("PreCompact clears readiness", not gate.claim_loaded(sid)))

        other = dict(payload())
        other["session_id"] = "subagent02"
        gate.mark_loaded(other)
        cases.append(("a subagent session cannot arm the parent", not gate.claim_loaded(sid)
                      and gate.claim_loaded("subagent02")))

        gate.mark_loaded(payload())
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _n: gate.claim_loaded(sid), range(2)))
        cases.append(("two concurrent dispatches both see the loaded ladder", claims == [True, True]))

        worktree = project / ".claude" / "worktrees" / "wt1"
        (worktree / ".claude" / "reference").mkdir(parents=True)
        wt_ladder = worktree / ".claude" / "reference" / "model_ladder_evidence.md"
        wt_ladder.write_text("# stale copy\n", encoding="utf-8")
        wt_sid = "ladderwt"
        wt_payload = {"tool_name": "Read", "session_id": wt_sid, "cwd": str(worktree),
                      "tool_input": {"file_path": ".claude/reference/model_ladder_evidence.md"}}
        named = getattr(gate, "expected_path", lambda _p: "")(wt_payload)
        cases.append(("expected_path names the primary ladder absolutely from a worktree cwd",
                      os.path.isabs(named) and os.path.normcase(named)
                      == os.path.normcase(os.path.realpath(ladder))))
        abs_sid = "ladderab"
        abs_payload = dict(wt_payload, session_id=abs_sid, tool_input={"file_path": named or "x"})
        cases.append(("a Read of the path the denial names arms the gate",
                      gate.mark_loaded(abs_payload) and gate.claim_loaded(abs_sid)))
        stale_payload = dict(wt_payload, session_id="ladderst", tool_input={"file_path": str(wt_ladder)})
        cases.append(("a Read of the worktree's stale copy cannot arm",
                      not gate.mark_loaded(stale_payload)))

        _bash_credit_cases(cases, gate, hook_state, project)

        path = Path(hook_state.state_path(sid))
        path.write_text("{not-json", encoding="utf-8")
        cases.append(("corrupt state fails closed", not gate.claim_loaded(sid)))
    finally:
        os.environ.clear()
        os.environ.update(old)

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


def test_unread_ladder_is_unmet_on_every_dispatch_until_a_compaction_clears_it(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    gate = load('model_ladder_gate')
    hooks_state = load('_hook_state')
    payload = {'session_id': 'cadence1', 'cwd': str(tmp_path)}
    hooks_state.mark_skill_in_context('cadence1', 'orchestration')
    for _ in range(2):
        reason, fault = gate.doctrine_check(payload)
        assert fault is None and 'model_ladder_evidence.md' in reason and 'Skill(skill="orchestration")' not in reason
    hooks_state.clear_compaction_keys('cadence1')
    reason, _ = gate.doctrine_check(payload)
    assert 'Skill(skill="orchestration")' in reason and 'model_ladder_evidence.md' in reason
    # One load route per document, and the step after the load is a re-check, never an unchanged retry.
    assert 'SKILL.md' not in reason and 're-issue' in reason and 'unchanged' not in reason, reason


def test_bounded_full_read_meets_the_ladder_precondition(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    gate = load('model_ladder_gate')
    hooks_state = load('_hook_state')
    # Coverage fields captured from ffd4976e's successful full ladder Read.
    payload = {'session_id': 'bounded1', 'tool_name': 'Read', 'tool_input': {
        'file_path': gate.expected_path({}), 'limit': 100}, 'tool_response': {
        'file': {'startLine': 1, 'numLines': 68, 'totalLines': 68}}}
    assert gate.mark_loaded(payload) is True
    assert gate.claim_loaded('bounded1') is True
    hooks_state.mark_skill_in_context('bounded1', 'orchestration')
    assert gate.doctrine_check(payload) == (None, None)


def test_partial_or_failed_bounded_read_leaves_the_ladder_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    gate = load('model_ladder_gate')
    payload = {'session_id': 'partial1', 'tool_name': 'Read', 'tool_input': {
        'file_path': gate.expected_path({}), 'limit': 20}, 'tool_response': {
        'file': {'startLine': 1, 'numLines': 20, 'totalLines': 68}}}
    assert gate.mark_loaded(payload) is False
    assert 'model_ladder_evidence.md' in gate.doctrine_check(payload)[0]
    payload['tool_response'] = {'is_error': True, 'file': {'startLine': 1, 'numLines': 68, 'totalLines': 68}}
    assert gate.mark_loaded(payload) is False


def test_unwritable_state_fails_open_with_a_fault_note(tmp_path, monkeypatch):
    # A process guard never blocks on its own fault (instruction_quality §16): a state file the lock cannot
    # write would otherwise deny every dispatch, since no load could ever be recorded.
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    gate = load('model_ladder_gate')
    monkeypatch.setattr(gate, 'update_json_locked', lambda path, updater: (False, None))
    reason, fault = gate.doctrine_check({'session_id': 'fault001', 'cwd': str(tmp_path)})
    assert reason is None and 'could not read its session state' in fault and 'model_ladder_gate' in fault


SLICE_CMD = 'python3 .claude/tools/model_registry.py for-role'


def _sliced_session(tmp_path, monkeypatch, sid, covered, skill=True):
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    gate = load('model_ladder_gate')
    hooks_state = load('_hook_state')
    if skill:
        hooks_state.mark_skill_in_context(sid, 'orchestration')
    if covered:
        assert hooks_state.mark_ladder_slice(sid, covered)
    return gate, hooks_state, {'session_id': sid, 'cwd': str(tmp_path)}


def test_a_slice_covering_the_calls_tiers_meets_the_ladder_precondition(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice001', ['architect'])
    assert gate.doctrine_check(payload, tiers=lambda: ['architect']) == (None, None)


def test_a_slice_covers_every_tier_the_call_implies(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice002', ['architect', 'fanout'])
    assert gate.doctrine_check(payload, tiers=lambda: ['fanout', 'architect']) == (None, None)


def test_a_slice_missing_a_pinned_tier_denies_naming_the_command_for_that_tier(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice003', ['architect'])
    reason, fault = gate.doctrine_check(payload, tiers=lambda: ['architect', 'executor'], embed_slice=False)
    assert fault is None and '%s executor' % SLICE_CMD in reason, reason
    assert 'architect' not in reason.split(SLICE_CMD)[1].split('.')[0], reason
    assert 're-issue' in reason


def test_a_full_read_still_passes_whatever_tiers_the_call_implies(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice004', [])
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    read = {'session_id': 'slice004', 'tool_name': 'Read', 'tool_input': {'file_path': gate.expected_path({})}}
    assert gate.mark_loaded(read) is True
    assert gate.doctrine_check(payload, tiers=lambda: ['architect', 'scout']) == (None, None)


def test_a_call_whose_tiers_cannot_be_read_falls_back_to_the_full_read(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice005', ['architect'])
    for tiers in (None, lambda: None, lambda: [], lambda: (_ for _ in ()).throw(RuntimeError('x'))):
        reason, _ = gate.doctrine_check(payload, tiers=tiers)
        assert 'model_ladder_evidence.md' in reason and SLICE_CMD not in reason, reason


def test_a_compaction_clears_the_slice(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'slice006', ['architect'])
    hooks_state.clear_compaction_keys('slice006')
    reason, _ = gate.doctrine_check(payload, tiers=lambda: ['architect'], embed_slice=False)
    assert '%s architect' % SLICE_CMD in reason, reason


def test_one_deny_names_the_skill_and_the_slice_together(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'slice007', [], skill=False)
    reason, _ = gate.doctrine_check(payload, tiers=lambda: ['architect'], embed_slice=True)
    assert 'Skill(skill="orchestration")' in reason and 'read the ladder slice for architect below' in reason, reason
    assert reason.index('## Role definitions') > reason.index('re-issue')


def test_the_slice_record_is_per_session(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'slice008', ['architect'])
    other = dict(payload, session_id='slice009')
    hooks_state.mark_skill_in_context('slice009', 'orchestration')
    assert gate.doctrine_check(other, tiers=lambda: ['architect'])[0] is not None


def _tools_registry():
    import sys
    tools = str(HERE.parent / 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import model_registry
    return model_registry


def test_the_deny_embeds_the_slice_for_the_missing_tier_only_and_records_it(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'embed001', ['fanout'])
    registry = _tools_registry()
    reason, fault = gate.doctrine_check(payload, tiers=lambda: ['fanout', 'executor'], embed_slice=True)
    assert fault is None and reason.endswith(registry.ladder_slice(['executor'])), reason
    assert gate.SLICE_BELOW_STEP.format(which="executor") in reason, reason
    assert gate.SLICE_STEP.format(which="executor", tiers="executor") not in reason, reason
    assert 're-issue' in reason and 'DENIED' in reason, reason
    executor_lines = set(registry.ladder_slice(['executor']).splitlines())
    only_architect = [l for l in registry.ladder_slice(['fanout']).splitlines() if l not in executor_lines]
    assert only_architect and not any(l in reason for l in only_architect)
    assert hooks_state.ladder_slice_tiers('embed001') == ['executor', 'fanout']


def test_the_call_passes_once_the_deny_embedded_its_slice(tmp_path, monkeypatch):
    gate, _, payload = _sliced_session(tmp_path, monkeypatch, 'embed002', [])
    assert gate.doctrine_check(payload, tiers=lambda: ['executor'], embed_slice=True)[0] is not None
    assert gate.doctrine_check(payload, tiers=lambda: ['executor']) == (None, None)


def test_a_slice_failure_names_the_command_and_records_nothing(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'embed003', [])
    registry = _tools_registry()

    def broken(tiers, *a, **k):
        raise registry.RegistryError('no ladder')
    monkeypatch.setattr(registry, 'ladder_slice', broken)
    reason, _ = gate.doctrine_check(payload, tiers=lambda: ['executor'])
    assert '%s executor' % SLICE_CMD in reason, reason
    assert hooks_state.ladder_slice_tiers('embed003') == []
    assert gate.doctrine_check(payload, tiers=lambda: ['executor'])[0] is not None


def test_a_caller_whose_deny_cannot_reach_the_session_gets_the_command_and_no_record(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'embed004', [])
    reason, _ = gate.doctrine_check(payload, tiers=lambda: ['executor'], embed_slice=False)
    assert '%s executor' % SLICE_CMD in reason and hooks_state.ladder_slice_tiers('embed004') == []


def test_unknown_tiers_and_the_skill_step_embed_nothing(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'embed005', [], skill=False)
    reason, _ = gate.doctrine_check(payload)
    assert 'Skill(skill="orchestration")' in reason and 'model_ladder_evidence.md' in reason
    assert '## Role definitions' not in reason and hooks_state.ladder_slice_tiers('embed005') == []


def test_the_default_records_no_slice_so_an_undelivered_deny_cannot_pass_the_next_call(tmp_path, monkeypatch):
    gate, hooks_state, payload = _sliced_session(tmp_path, monkeypatch, 'embed006', [])
    reason, _ = gate.doctrine_check(payload, tiers=lambda: ['executor'])
    assert '%s executor' % SLICE_CMD in reason, reason
    assert hooks_state.ladder_slice_tiers('embed006') == []
    assert gate.doctrine_check(payload, tiers=lambda: ['executor'])[0] is not None


if __name__ == "__main__":
    raise SystemExit(main())
