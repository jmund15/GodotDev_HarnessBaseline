#!/usr/bin/env python3
"""Re-runnable proof for hooks/pre_bash_dispatch.py — the single PreToolUse entry for the
Bash/PowerShell/Monitor family.

Parity, not re-derivation: for each payload the twelve sub-hooks run standalone by subprocess
(their own CLI contract) and the dispatcher runs once; the dispatcher's verdict must equal the
native aggregation of the standalone results (any block ⇒ blocked; else deny > ask > allow; every
additionalContext present). A malformed payload and a non-matching tool_name must exit 0 silent.
Also prints the wall-clock of twelve parallel spawns vs one dispatcher spawn (evidence, not an
assertion — timing is machine-bound).

    python3 .claude/tests/test_pre_bash_dispatch.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS_DIR = os.path.normpath(os.path.join(HERE, "..", "hooks"))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
DISPATCH = os.path.join(HOOKS_DIR, "pre_bash_dispatch.py")

sys.path.insert(0, HOOKS_DIR)
import pre_bash_dispatch  # noqa: E402

SUB_HOOKS = [(os.path.basename(m.__file__), list(argv)) for m, argv in pre_bash_dispatch.HOOKS]


def run(cmd, payload_text, env, timeout=90, cwd=REPO):
    return subprocess.run(cmd, input=payload_text, capture_output=True, text=True,
                          timeout=timeout, env=env, encoding="utf-8", cwd=cwd)


def hso(proc):
    text = proc.stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}


def standalone_verdict(payload_text, env):
    """Native aggregation over the twelve standalone runs."""
    blocked, decision, contexts = False, None, []
    rank = {"deny": 3, "ask": 2, "allow": 1}
    for name, argv in SUB_HOOKS:
        p = run([sys.executable, os.path.join(HOOKS_DIR, name)] + argv, payload_text, env)
        if p.returncode == 2:
            blocked = True
            continue
        out = hso(p)
        if out.get("permissionDecision") == "deny":
            blocked = True
        pd = out.get("permissionDecision")
        if pd in rank and rank[pd] > rank.get(decision, 0):
            decision = pd
        ctx = out.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            contexts.append(ctx)
    return blocked, decision, contexts


def dispatcher_verdict(payload_text, env):
    p = run([sys.executable, DISPATCH], payload_text, env)
    out = hso(p)
    blocked = p.returncode == 2 or out.get("permissionDecision") == "deny"
    return blocked, out.get("permissionDecision"), out.get("additionalContext") or "", p


def payload(tool, command, cwd=REPO):
    return json.dumps({"tool_name": tool, "session_id": "pbd" + uuid.uuid4().hex[:8],
                       "cwd": cwd, "tool_input": {"command": command}})


def test_nonfloor_fault_reaches_model_and_preserves_later_checks(monkeypatch):
    monkeypatch.setattr(pre_bash_dispatch.run_call_router, 'route', lambda _: ({}, []))
    def boom():
        raise RuntimeError('planted fault')
    def later():
        print(json.dumps({'hookSpecificOutput': {'additionalContext': 'later check ran'}}))
    bad = SimpleNamespace(__name__='broken_advice', __file__='broken_advice.py', main=boom)
    good = SimpleNamespace(__name__='later', __file__='later.py', main=later)
    code, out, err = pre_bash_dispatch.dispatch(payload('Bash', 'git status --short'), ((bad, ()), (good, ())))
    context = json.loads(out)['hookSpecificOutput']['additionalContext']
    assert code == 0 and 'permissionDecision' not in json.loads(out)['hookSpecificOutput']
    assert 'broken_advice' in context and 'planted fault' in context and 'Repair' in context
    assert 'later check ran' in context and 'planted fault' in err
    bad.FAIL_CLOSED = True
    code, out, err = pre_bash_dispatch.dispatch(payload('Bash', 'git status --short'), ((bad, ()), (good, ())))
    assert code == 2 and out == '' and 'floor guard' in err


import pytest


@pytest.mark.parametrize('name', ('_git_commit', 'sidecar_argv', '_optional_hooks'))
def test_bootstrap_import_faults_fail_closed(tmp_path, name):
    script = (
        "import sys, runpy\n"
        "class Broken:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        f"        if name == {name!r}:\n"
        "            raise ImportError('planted bootstrap fault: ' + name)\n"
        "sys.meta_path.insert(0, Broken())\n"
        f"runpy.run_path({DISPATCH!r}, run_name='__main__')\n")
    for tool in ('Bash', 'PowerShell', 'Read'):
        proc = run([sys.executable, '-c', script], payload(tool, 'git status --short'),
                   dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / 'state')))
        assert proc.returncode == (0 if tool == 'Read' else 2), (name, tool, proc.stderr)
        assert proc.stdout == '' and 'Traceback' not in proc.stderr
        if tool == 'Read':
            assert proc.stderr == ''
        else:
            assert 'BLOCKED' in proc.stderr and name in proc.stderr
            assert 'ImportError: planted bootstrap fault' in proc.stderr


def test_actual_advisory_import_fault_cannot_hide_floor(tmp_path):
    script = (
        "import sys, runpy\n"
        "class Broken:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name in ('hand_pricing_advisory', 'dangerous_shell_guard'):\n"
        "            raise RuntimeError('planted import fault: ' + name)\n"
        "sys.meta_path.insert(0, Broken())\n"
        f"runpy.run_path({DISPATCH!r}, run_name='__main__')\n")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / 'state'))
    proc = run([sys.executable, '-c', script], payload('Bash', 'rm -rf peer'), env)
    assert proc.returncode == 2, proc.stderr
    assert 'dangerous_shell_guard' in proc.stderr and 'planted import fault' in proc.stderr
    assert 'Traceback' not in proc.stderr


def test_each_actual_advisory_import_fault_preserves_git_floor(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / 'file').write_text('base\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'file'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=t', '-c', 'user.email=t@t',
                    'commit', '-qm', 'base'], check=True, capture_output=True)
    (tmp_path / 'file').write_text('peer edit\n', encoding='utf-8')
    for name in [member.__name__ for member, _ in pre_bash_dispatch.HOOKS] + ['run_call_router']:
        if name in ('_git_commit', 'git_guardrails', 'baseline_classification_guard', 'dangerous_shell_guard',
                    'editor_live_build_guard'):
            continue
        script = (
            "import sys, runpy\n"
            "class Broken:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            f"        if name == {name!r}:\n"
            "            raise RuntimeError('planted advisory import fault')\n"
            "sys.meta_path.insert(0, Broken())\n"
            f"runpy.run_path({DISPATCH!r}, run_name='__main__')\n")
        proc = run([sys.executable, '-c', script], payload('Bash', 'git checkout -- file', str(tmp_path)),
                   dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / name)), cwd=str(tmp_path))
        assert proc.returncode == 2 and 'checkout' in proc.stderr, (name, proc.stderr)
        assert 'Traceback' not in proc.stderr, (name, proc.stderr)
        proc = run([sys.executable, '-c', script], payload('Bash', 'git status --short', str(tmp_path)),
                   dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / (name + '-read'))), cwd=str(tmp_path))
        assert proc.returncode == 0 and hso(proc).get('permissionDecision') != 'deny', (name, proc.stderr)
        assert name in hso(proc).get('additionalContext', ''), (name, proc.stdout)


def test_actual_destructive_member_fault_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError('planted destructive member fault')
    monkeypatch.setattr(pre_bash_dispatch.dangerous_shell_guard, 'main', boom)
    code, out, err = pre_bash_dispatch.dispatch(payload('Bash', 'rm -rf peer'))
    assert code == 2 and out == '' and 'dangerous_shell_guard' in err
    assert 'planted destructive member fault' in err


GODOT_MEMBERS = ('cloud_test_enforcer', 'editor_live_build_guard', 'gate_cadence_guard', 'pattern_enforcer',
                 'prototype_containment_guard', 'provisional_totality_guard', 'tres_nullstrip_guard',
                 'tres_script_strip_guard', 'wide_test_guard')


def planted_pure_profile(tmp_path):
    """A project root whose hooks are this tree's, with every godot-layer chain member absent."""
    import shutil
    root = tmp_path / 'proj'
    hooks = root / '.claude' / 'hooks'
    for sub in ('hooks', 'tools'):
        shutil.copytree(os.path.join(HERE, '..', sub), root / '.claude' / sub,
                        ignore=shutil.ignore_patterns('__pycache__'))
    for name in GODOT_MEMBERS:
        (hooks / (name + '.py')).unlink(missing_ok=True)
    subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
    return root


def pure_dispatch(root, command, tmp_path, transcript=None):
    data = json.loads(payload('Bash', command, str(root)))
    if transcript:
        data['transcript_path'] = transcript
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / 'state'), CLAUDE_PROJECT_DIR=str(root))
    return run([sys.executable, str(root / '.claude' / 'hooks' / 'pre_bash_dispatch.py')], json.dumps(data), env,
               cwd=str(root))


def test_pure_profile_denies_recursive_delete_without_godot_members(tmp_path):
    root = planted_pure_profile(tmp_path)
    assert not any((root / '.claude' / 'hooks' / (name + '.py')).exists() for name in GODOT_MEMBERS)
    for command in ('rm -rf /', 'rm -rf ~/peer', 'rm -r /usr/peer', 'rm -f a && rm -rf /usr/peer',
                    'Remove-Item -Recurse -Force ~/peer', 'format C:'):
        proc = pure_dispatch(root, command, tmp_path)
        assert proc.returncode == 2, (command, proc.stdout, proc.stderr)
        assert 'failed to import' not in proc.stderr and 'Traceback' not in proc.stderr, (command, proc.stderr)
        assert 'Dangerous recursive' in proc.stderr or 'Format drive' in proc.stderr, (command, proc.stderr)


def test_pure_profile_destructive_import_fault_denies(tmp_path):
    root = planted_pure_profile(tmp_path)
    (root / '.claude' / 'hooks' / 'dangerous_shell_guard.py').write_text('raise RuntimeError("planted")\n', encoding='utf-8')
    proc = pure_dispatch(root, 'rm -rf peer', tmp_path)
    assert proc.returncode == 2 and 'dangerous_shell_guard' in proc.stderr and 'planted' in proc.stderr, proc.stderr
    assert 'destructive-command check' in proc.stderr, proc.stderr


def test_pure_profile_allows_non_destructive_and_owned_cleanup(tmp_path):
    root = planted_pure_profile(tmp_path)
    sid = str(uuid.uuid4())
    scratch = root / '.claude' / 'scratch' / sid / 'out'
    cache = root / '.claude' / '.cache' / 'regen'
    temp = tempfile.mkdtemp(prefix='pbd_allow_')
    for path in (scratch, cache):
        path.mkdir(parents=True)
    (root / 'README.md').write_text('rm -rf is documented here\n', encoding='utf-8')
    allowed = (
        'ls', 'git status --short', 'grep "rm -rf" README.md', "echo 'rm -rf /'", 'rm -f stale.txt',
        'rm -rf ' + temp.replace('\\', '/'),
        'rm -rf .claude/.cache/regen',
        'rm -rf .claude/scratch/%s/out' % sid,
        'D=$(mktemp -d) && touch "$D/x" && rm -rf "$D"',
    )
    for command in allowed:
        proc = pure_dispatch(root, command, tmp_path, transcript=str(root / (sid + '.jsonl')))
        assert proc.returncode == 0 and hso(proc).get('permissionDecision') != 'deny', (command, proc.stdout, proc.stderr)
        assert 'Traceback' not in proc.stderr, (command, proc.stderr)


def test_actual_advisory_runtime_fault_preserves_git_floor(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError('planted actual adviser fault')
    order = [module for module, _argv in pre_bash_dispatch.HOOKS]
    floor = order.index(pre_bash_dispatch.git_guardrails)
    # Any installed adviser ahead of the floor serves; godot-layer members are absent in a pure profile.
    adviser = next(module for module in order[:floor]
                   if os.path.basename(module.__file__)[:-3] not in pre_bash_dispatch._FLOOR_MEMBERS)
    monkeypatch.setattr(adviser, 'main', boom)
    # Even a broken normalizer cannot skip the later floor member.
    monkeypatch.setattr(pre_bash_dispatch.git_guardrails, 'main', lambda: 2)
    code, out, err = pre_bash_dispatch.dispatch(payload('Bash', 'git checkout -- file'))
    assert code == 2 and out == '' and 'git_guardrails' in err


def test_shared_must_allow_cases(tmp_path):
    from _must_allow_cases import must_allow_cases
    cases, cleanup = must_allow_cases('pre_bash_dispatch')
    try:
        for label, data, cwd, _ in cases:
            commands = [data['tool_input']['command']]
            if 'closing a merge' in label:
                commands.append('git merge --continue')
            for command in commands:
                data['tool_input']['command'] = command
                proc = run([sys.executable, DISPATCH], json.dumps(data),
                           dict(os.environ, HARNESS_HOOK_STATE_DIR=str(tmp_path / 'state')), cwd=cwd)
                assert proc.returncode == 0, (label, proc.stderr)
                assert hso(proc).get('permissionDecision') != 'deny', (label, proc.stdout)
                assert 'Traceback' not in proc.stderr, (label, proc.stderr)
    finally:
        cleanup()


def test_router_fault_reaches_model(monkeypatch):
    def boom(_):
        raise RuntimeError('planted router fault')
    monkeypatch.setattr(pre_bash_dispatch.run_call_router, 'route', boom)
    code, out, err = pre_bash_dispatch.dispatch(payload('Bash', 'git status --short'), ())
    assert code == 0 and 'run_call_router' in json.loads(out)['hookSpecificOutput']['additionalContext']
    assert 'planted router fault' in err


def test_launcher_commit_reaches_each_guard(monkeypatch, tmp_path):
    import shlex
    names = ('baseline_classification_guard', 'duplicate_commit_check', 'prototype_containment_guard',
             'provisional_totality_guard', 'rule_number_guard', 'tres_nullstrip_guard',
             'tres_script_strip_guard', 'triage_commit_check')
    names = [name for name in names if name not in GODOT_MEMBERS or getattr(pre_bash_dispatch, name) is not None]
    monkeypatch.setattr(pre_bash_dispatch.run_call_router, 'route', lambda _: ({}, []))
    original = pre_bash_dispatch._git_commit.commit_invocations
    for name in names:
        member = getattr(pre_bash_dispatch, name)
        argv = next(args for hook, args in pre_bash_dispatch.HOOKS if hook is member)
        calls = []
        # Enter each real member through its parser seam, before its downstream policy.
        def observe(command, cwd):
            result = original(command, cwd)
            calls.extend(result)
            return []
        owner = member if hasattr(member, 'commit_invocations') else pre_bash_dispatch._git_commit
        with monkeypatch.context() as patch:
            patch.setattr(owner, 'commit_invocations', observe)
            for launcher in ('bash -c', 'sh -c', 'bash -lc', 'bash -o pipefail -c', 'eval'):
                calls.clear()
                command = launcher + ' ' + shlex.quote('git commit -m x')
                code, out, err = pre_bash_dispatch.dispatch(payload('Bash', command), ((member, argv),))
                assert code == 0 and calls and all(call.sub == 'commit' for call in calls), (name, launcher, code, out, err)
                calls.clear()
                code, out, err = pre_bash_dispatch.dispatch(
                    payload('Bash', launcher + ' ' + shlex.quote('git status --short')), ((member, argv),))
                assert code == 0 and calls == [], (name, launcher, code, out, err)


def test_git_import_fallback_sees_launcher_bodies():
    for launcher in ('bash -c', 'sh -c', 'bash -lc', 'bash -o pipefail -c', 'eval'):
        stub = pre_bash_dispatch._floor_stub('git_guardrails', 'planted fault', pre_bash_dispatch._runs_git, 'git call')
        code, out, err = pre_bash_dispatch.dispatch(payload('Bash', launcher + " 'git status --short'"), ((stub, ()),))
        assert code == 2 and 'git_guardrails' in err, (launcher, code, out, err)
        code, out, err = pre_bash_dispatch.dispatch(payload('Bash', launcher + " 'printf git'"), ((stub, ()),))
        assert code == 0, (launcher, code, out, err)


def test_git_guardrails_judges_launcher_commit(monkeypatch):
    member = pre_bash_dispatch.git_guardrails
    argv = ()
    monkeypatch.setattr(member, 'base_verdict', lambda *_: 'planted commit floor verdict')
    for launcher in ('bash -c', 'sh -c', 'bash -lc', 'bash -o pipefail -c', 'eval'):
        code, out, err = pre_bash_dispatch.dispatch(payload('Bash', launcher + " 'git commit -m x'"), ((member, argv),))
        assert code == 2 and 'planted commit floor verdict' in err, (launcher, code, out, err)
        code, out, err = pre_bash_dispatch.dispatch(payload('Bash', launcher + " 'git status --short'"), ((member, argv),))
        assert code == 0, (launcher, code, out, err)


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="pbd_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "state"),
               CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8")

    def check(label, cond, detail=""):
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        if not cond:
            failures.append(label + (": " + detail[:300] if detail else ""))

    # Commit normalizers precede every guard that judges their rewritten publication.
    # and its module carries a real (non-stub) `main` -- a broken import would otherwise
    # register a stub silently and this generic parity harness would never notice.
    names = [os.path.basename(m.__file__) for m, _argv in pre_bash_dispatch.HOOKS]
    check("baseline_classification_guard.py normalizes before git_guardrails.py",
          "git_guardrails.py" in names and "baseline_classification_guard.py" in names
          and names.index("baseline_classification_guard.py") < names.index("git_guardrails.py"),
          repr(names))
    check("its import did not fall back to the stub",
          getattr(pre_bash_dispatch, "_BASELINE_GUARD_IMPORT_ERROR", "unset") is None)
    names = [os.path.basename(m.__file__) for m, _argv in pre_bash_dispatch.HOOKS]
    check("approving_gates is directly after sidecar dispatch and before backslash fidelity",
          "sidecar_dispatch_context.py" in names and "approving_gates.py" in names
          and "bash_backslash_fidelity.py" in names
          and names.index("approving_gates.py") == names.index("sidecar_dispatch_context.py") + 1
          and names.index("approving_gates.py") < names.index("bash_backslash_fidelity.py"), repr(names))
    check("approving_gates import did not fall back to its stub",
          getattr(pre_bash_dispatch, "_APPROVING_GATES_IMPORT_ERROR", "unset") is None)
    stub_code, stub_out, stub_err = pre_bash_dispatch.run_hook(
        pre_bash_dispatch._advisory_stub("approving_gates", "ImportError: planted",
                                         "no harness action is auto-approved"), (), "{}")
    check("a failed approver import reports on user stderr and approves nothing",
          stub_code == 1 and stub_out == "" and "approving_gates" in stub_err and "planted" in stub_err,
          repr((stub_code, stub_out, stub_err)))

    # Job A: duplicate_commit_check is an advisory sub-hook, registered directly after
    # triage_commit_check. SUB_HOOKS above derives from HOOKS, so the parity cases below already run
    # it standalone; these two cases pin the registration and its import posture.
    names = [os.path.basename(m.__file__) for m, _argv in pre_bash_dispatch.HOOKS]
    check("duplicate_commit_check.py is registered directly after triage_commit_check.py",
          "triage_commit_check.py" in names and "duplicate_commit_check.py" in names
          and names.index("duplicate_commit_check.py") == names.index("triage_commit_check.py") + 1,
          repr(names))
    check("its import did not fall back to the stub",
          getattr(pre_bash_dispatch, "_DUPLICATE_CHECK_IMPORT_ERROR", "unset") is None)
    dup_code, dup_out, dup_err = pre_bash_dispatch.run_hook(
        pre_bash_dispatch._advisory_stub("duplicate_commit_check", "ImportError: planted",
                                         "no commit duplicate advisory is printed"), (), "{}")
    check("a failed duplicate-check import reports on user stderr and advises nothing",
          dup_code == 1 and dup_out == "" and "duplicate_commit_check" in dup_err and "planted" in dup_err,
          repr((dup_code, dup_out, dup_err)))

    # A floor guard that cannot import must block the action it guards, never crash the dispatcher with a
    # non-2 exit, and never block a command that only names its noun. The rows run in an empty planted
    # repo: in the live checkout a staged harness path makes git_guardrails' stamp check deny first.
    floor_repo = os.path.join(tmp, "floor_repo")
    os.makedirs(floor_repo)
    subprocess.run(["git", "init", "-q"], cwd=floor_repo, check=True, capture_output=True)
    floor_rows = {
        "git_guardrails": (("a git command", "git status --short", True),
                           ("a command that only names a git path", "ls .gitignore", False),
                           ("a command that never names git", "echo hello", False)),
        "baseline_classification_guard": (("a commit", "git commit -m x", True),
                                          ("a command that only names a commit", "echo commit", False)),
    }
    for guard, rows in floor_rows.items():
        break_floor = (
            "import sys, runpy\n"
            "class Planted:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == %r:\n"
            "            raise ImportError('planted floor import failure')\n"
            "sys.meta_path.insert(0, Planted())\n"
            "sys.argv = [%r]\n"
            "runpy.run_path(%r, run_name='__main__')\n" % (guard, DISPATCH, DISPATCH))
        for label, command, should_block in rows:
            proc = run([sys.executable, "-c", break_floor], payload("Bash", command, floor_repo), env, cwd=floor_repo)
            check("a %s import failure %s: %s" % (
                      guard, "denies" if should_block else "does not block", label),
                  (proc.returncode == 2) == should_block and (not should_block or guard in proc.stderr),
                  "exit %s: %s" % (proc.returncode, proc.stderr[-400:]))
    # rule_number_guard is an advisory: its import failure reaches the user's stderr and blocks nothing.
    break_advisory = break_floor.replace(repr(guard), repr("rule_number_guard"), 1)
    proc = run([sys.executable, "-c", break_advisory], payload("Bash", "git commit -m x", floor_repo), env, cwd=floor_repo)
    check("a rule_number_guard import failure reports on stderr and does not block a commit",
          proc.returncode == 0 and "rule_number_guard" in proc.stderr,
          "exit %s: %s" % (proc.returncode, proc.stderr[-400:]))

    def planted_deny_main():
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "planted deny wins"}}))

    planted_deny = SimpleNamespace(__name__="planted_deny", __file__="planted_deny.py",
                                   main=planted_deny_main)
    digest_payload = payload(
        "Bash", "python3 .claude/tools/session_digest.py --session abc123 --brief")
    approver_at = [m for m, _ in pre_bash_dispatch.HOOKS].index(pre_bash_dispatch.approving_gates)
    planted_chain = (*pre_bash_dispatch.HOOKS[:approver_at], (planted_deny, ()),
                     *pre_bash_dispatch.HOOKS[approver_at:])
    project_before = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = REPO
    try:
        control = pre_bash_dispatch.dispatch(digest_payload)
        planted = pre_bash_dispatch.dispatch(digest_payload, hooks=planted_chain)
    finally:
        if project_before is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = project_before

    def decision_of(result):
        try:
            return json.loads(result[1]).get("hookSpecificOutput", {}) if result[1] else {}
        except ValueError:
            return {}
    check("control: the same chain without the plant approves the digest",
          control[0] == 0 and decision_of(control).get("permissionDecision") == "allow", control[2])
    check("a planted deny just ahead of the approver beats its digest approval",
          planted[0] == 0 and decision_of(planted).get("permissionDecision") == "deny"
          and decision_of(planted).get("permissionDecisionReason", "").startswith("planted deny wins"),
          planted[2])
    check("a deny reason reaches the model unchanged, with no dispatcher appendix",
          decision_of(planted).get("permissionDecisionReason", "").rstrip().endswith("planted deny wins"),
          decision_of(planted).get("permissionDecisionReason", "")[-200:])

    # One tool call reads one repo state: the chain runs inside _git_commit.memoized(), so the sub-hooks'
    # repeated read-only git queries spawn git once.
    import _git_commit
    seen_memo = []
    probe = SimpleNamespace(__name__="memo_probe", __file__="memo_probe.py",
                            main=lambda: seen_memo.append(_git_commit._memo is not None) or 0)
    pre_bash_dispatch.dispatch(digest_payload, hooks=((probe, ()),))
    check("the chain runs inside _git_commit.memoized(), and the cache ends with the call",
          seen_memo == [True] and _git_commit._memo is None, "seen=%s after=%r" % (seen_memo, _git_commit._memo))

    # A floor guard that crashes denies; an advisory that crashes stays silent to the model.
    def crash():
        raise RuntimeError("planted crash")
    floor = SimpleNamespace(__name__="planted_floor", __file__="planted_floor.py", main=crash, FAIL_CLOSED=True)
    advisory = SimpleNamespace(__name__="planted_advisory", __file__="planted_advisory.py", main=crash)
    floor_result = pre_bash_dispatch.dispatch(digest_payload, hooks=((floor, ()),))
    advisory_result = pre_bash_dispatch.dispatch(digest_payload, hooks=((advisory, ()),))
    check("a FAIL_CLOSED sub-hook that raises blocks the call and names its crash",
          floor_result[0] == 2 and "planted_floor" in floor_result[2] and "planted crash" in floor_result[2],
          repr(floor_result)[:300])
    check("a sub-hook without FAIL_CLOSED that raises is reported on stderr and the call proceeds",
          advisory_result[0] == 0 and "planted crash" in advisory_result[2], repr(advisory_result)[:300])
    check("git_guardrails and baseline_classification_guard are FAIL_CLOSED",
          getattr(pre_bash_dispatch.git_guardrails, "FAIL_CLOSED", False) is True
          and getattr(pre_bash_dispatch.baseline_classification_guard, "FAIL_CLOSED", False) is True)

    cases = [
        ("Bash", "git status --short | head -3"),
        ("Bash", "rm -rf build_out"),
        ("Bash", 'cd "%s" && git status --short' % REPO.replace("\\", "/")),
        ("Bash", "find . -name x"),
        ("PowerShell", "Get-ChildItem"),
        ("Monitor", "git status"),
    ]
    for tool, command in cases:
        # Distinct session ids: advisories dedupe per session, so a shared id would let the
        # standalone pass consume the nudge the dispatcher pass is then measured against.
        s_blocked, s_decision, s_contexts = standalone_verdict(payload(tool, command), env)
        d_blocked, d_decision, d_context, proc = dispatcher_verdict(payload(tool, command), env)
        label = "%s `%s`" % (tool, command[:40])
        check(label + " — blocked parity (%s)" % s_blocked, s_blocked == d_blocked, proc.stderr)
        if not s_blocked:
            check(label + " — decision parity (%s)" % s_decision, s_decision == d_decision,
                  repr((s_decision, d_decision)))
            check(label + " — every standalone advisory present (%d)" % len(s_contexts),
                  all(c in d_context for c in s_contexts) and bool(d_context) == bool(s_contexts),
                  d_context[:300])
            check(label + " — exit 0", proc.returncode == 0, proc.stderr)

    # A planted violation must FIRE through the dispatcher, not just agree with a silent chain.
    d_blocked, _, _, proc = dispatcher_verdict(payload("Bash", "rm -rf build_out"), env)
    check("rm -rf is blocked through the dispatcher (exit 2, stderr names it)",
          proc.returncode == 2 and "rm" in proc.stderr, proc.stderr)
    check("an exit-2 block carries only the guard's own text",
          "guard's purpose" not in proc.stderr, proc.stderr[-300:])

    p = run([sys.executable, DISPATCH], "not json", env)
    check("malformed payload exits 0 silent", p.returncode == 0 and not p.stdout.strip(), p.stderr)
    p = run([sys.executable, DISPATCH], payload("Read", ""), env)
    check("non-matching tool_name exits 0 silent", p.returncode == 0 and not p.stdout.strip(), p.stderr)

    # Evidence: twelve parallel spawns vs one dispatcher spawn, same benign payload, best of 3.
    text = payload("Bash", "git status --short | head -3")
    best_par, best_one = 1e9, 1e9
    for _ in range(3):
        t0 = time.time()
        procs = [subprocess.Popen([sys.executable, os.path.join(HOOKS_DIR, n)] + a, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=REPO,
                                  text=True, encoding="utf-8") for n, a in SUB_HOOKS]
        for pr in procs:
            pr.communicate(text, timeout=90)
        best_par = min(best_par, time.time() - t0)
        t0 = time.time()
        run([sys.executable, DISPATCH], text, env)
        best_one = min(best_one, time.time() - t0)
    print("timing: 12 parallel spawns %.2fs | dispatcher %.2fs (best of 3)" % (best_par, best_one))

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
    import pytest
    sys.exit(pytest.main([__file__, '-q', '-s']))
