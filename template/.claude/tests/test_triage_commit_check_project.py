#!/usr/bin/env python3
"""Proof for hooks/triage_commit_check.py: the advisory commit check that prints any field the staged
diff raises above the recorded triage.

Real PreToolUse payloads go on stdin against a planted git repo that carries a planted registry and one
seed commit made before any case (an unborn branch would skip branch-gated logic silently). Each case that
expects `{}` has a sibling on the same fixture that fires, so a silent hook cannot pass this proof.

    python3 .claude/tests/test_triage_commit_check_project.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.normpath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
TOOLS = os.path.join(CLAUDE, "tools")
HOOK = os.path.join(HOOKS, "triage_commit_check.py")
TASK_RECORD = os.path.join(TOOLS, "task_record.py")
sys.path.insert(0, HOOKS)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
from _ledger_rows import ledger_row  # noqa: E402
from pre_bash_dispatch import run_hook  # noqa: E402
import _git_commit  # noqa: E402
import triage_commit_check  # noqa: E402
sys.stdout.reconfigure(encoding="utf-8")

FAILURES = []
OWED_LINE = ("[review] %d staged file(s) not reviewed since they changed: %d edited after a reviewed blob, "
             "%d never reviewed. A fix applied outside a fold records with post_check.py review --findings <file> "
             "--applied <ids> --files <paths> (orchestrator_action_protocol.md Step 4); how any other change is "
             "checked is the session's choice under orchestration §2 Later rounds.")
RAISE_HEAD = "[triage] the staged diff raises the recorded triage; raise the trailer, or re-triage with task_record.py triage --reason:"
NO_TRIAGE = ("[triage] this commit landed with no triage recorded: record it now with task_record.py triage, "
             "or carry a Triage: trailer on the next commit (grammar: triage.py --help).")
TRAILER = ("Triage: reach=%s reversibility=clean design=settled grounding=verified oracle=%s "
           "size=one-context")
GITLINK_A = "a" * 40
GITLINK_B = "b" * 40

TAGS = [
    {"id": "wiring", "paths": ["**/*.tscn", "**/*.tres"],
     "hunk": r"^\[(?:node|connection)\b|ExtResource|NodePath", "sets": {"oracle": "runtime"},
     "probe": "launch the scene", "probe_validated": None, "source": "test:wiring"},
    {"id": "jmodot-pointer", "paths": ["Jmodot"], "sets": {"reach": "external"}, "source": "test:gitlink"},
    {"id": "guard-logic", "paths": ["hooks/**"], "sets": {"reach": "shared", "oracle": "new-test"},
     "source": "test:guard"},
]


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:400] if detail else ""))


def git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          encoding="utf-8", env=env).stdout


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_project(tmp, name, tags=TAGS):
    """A planted repo with a registry and its seed commit."""
    root = os.path.join(tmp, name)
    os.makedirs(root)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    write(root, ".claude/skills/project_subsystems/adaptation.json", json.dumps({"sensitivity_tags": tags}))
    write(root, "Dungeon/a.tres", '[gd_resource type="Resource"]\n[resource]\ndamage = 4\nlabel = "x"\n')
    write(root, "Dungeon/a.tscn", '[gd_scene format=3]\n[node name="Root" type="Node3D"]\n')
    write(root, "hooks/g.py", "x = 1\n")
    write(root, "notes.txt", "notes\n")
    git(root, "add", "-A")
    git(root, "update-index", "--add", "--cacheinfo", "160000,%s,Jmodot" % GITLINK_A)
    git(root, "commit", "-q", "-m", "seed")
    return root


def stage(root, *paths):
    git(root, "add", "--", *paths)


def add_node(root, rel="Dungeon/a.tscn"):
    with open(os.path.join(root, rel), "a", encoding="utf-8", newline="\n") as fh:
        fh.write('[node name="Hit" type="Area3D" parent="."]\n')


def payload(root, command, session="sess-triage-1", tool="Bash"):
    return {"tool_name": tool, "session_id": session, "cwd": root, "tool_input": {"command": command}}


class Harness:
    def __init__(self, tmp):
        self.state = os.path.join(tmp, "state")
        self.records = os.path.join(tmp, "records")
        os.makedirs(self.state)
        os.makedirs(self.records)
        self.keys = ""
        self.ledger = os.path.join(tmp, "review_ledger.jsonl")

    def env(self, project, session=None):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project, HARNESS_HOOK_STATE_DIR=self.state,
                   HARNESS_TASK_RECORD_DIR=self.records, PYTHONIOENCODING="utf-8",
                   HARNESS_REVIEW_LEDGER=self.ledger)
        if self.keys is None:
            env.pop("HARNESS_REVIEW_CANDIDATE_KEYS", None)
        else:
            env["HARNESS_REVIEW_CANDIDATE_KEYS"] = self.keys
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return env

    def run(self, project, data, process=False):
        """(exit code, stdout, stderr) of the hook on a payload: in this process, or standalone with `process`."""
        env = self.env(project)
        if process:
            proc = subprocess.run([sys.executable, HOOK], input=json.dumps(data), capture_output=True, text=True,
                                  encoding="utf-8", timeout=60, env=env, cwd=CLAUDE)
            return proc.returncode, proc.stdout, proc.stderr
        saved_env, saved_cwd = dict(os.environ), os.getcwd()
        os.environ.clear()
        os.environ.update(env)
        os.chdir(CLAUDE)
        try:
            with _git_commit.memoized():
                return run_hook(triage_commit_check, [], json.dumps(data))
        finally:
            os.chdir(saved_cwd)
            os.environ.clear()
            os.environ.update(saved_env)

    def context(self, project, data):
        """additionalContext the hook emitted ('' for `{}` or nothing), and the raw result."""
        code, out, err = self.run(project, data)
        text = out.strip()
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            parsed = {"unparseable": text}
        hso = parsed.get("hookSpecificOutput", {}) if isinstance(parsed, dict) else {}
        return hso.get("additionalContext", ""), (code, out, err), parsed

    def record(self, project, task, session, reach, oracle, paths):
        env = self.env(project, session)
        subprocess.run([sys.executable, TASK_RECORD, "open", task, "--session", session, "--title", task],
                       capture_output=True, text=True, encoding="utf-8", env=env, check=True)
        proc = subprocess.run([sys.executable, TASK_RECORD, "triage", task, "--reach", reach, "--reversibility",
                               "clean", "--design", "settled", "--grounding", "verified", "--oracle", oracle,
                               "--size", "one-context", "--paths", *paths],
                              capture_output=True, text=True, encoding="utf-8", env=env)
        return proc


def is_silent(result):
    ctx, (code, out, err), parsed = result
    return code == 0 and ctx == "" and parsed in ({}, None) and not err.strip()


def test_unknown_size_keeps_commit_admission_closed(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    import triage_commit_check as hook
    captured = []
    invocation = SimpleNamespace(cwd=str(tmp_path), git_env={}, rest=[])
    for recorded in (None, {'reach': 'shared', 'kind': 'mechanical'}):
        with patch.object(hook, 'staged_paths', return_value=(['hooks/g.py'], None)), \
                patch.object(hook, 'authored_paths', side_effect=lambda paths, *_: paths), \
                patch.object(hook.triage, 'changes_from_index', return_value=[]), \
                patch.object(hook.triage, '_toplevel', return_value=str(tmp_path)), \
                patch.object(hook.triage, 'derive', return_value=hook.triage.Result('shared', frozenset(), [], [])), \
                patch.object(hook, 'commit_message', return_value='fix'), \
                patch.object(hook, '_recorded', return_value=(recorded, None)), \
                patch.object(hook, '_triage_block', return_value=(None, None)), \
                patch.object(hook, '_premises', return_value=None), \
                patch.object(hook, '_owed', side_effect=lambda _i, _p, fields, _s: captured.append(fields)):
            hook._check(invocation, 'unknown-size', [])
    assert [(f.kind, f.size) for f in captured] == [('decision', 'milestones'), ('mechanical', 'milestones')]
    assert hook.triage.admits('harness-quality-seat', captured[0], 'normal').admitted
    assert hook.triage.admits('candidate-lenses', captured[1], 'normal', gate_green=True).admitted


def test_standing_raise_keeps_unknown_size_closed(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    import triage_commit_check as hook
    recorded = {'reach': 'local', 'oracle': frozenset()}
    raised = hook.triage.Raise('reach', 'local', 'standing', [])
    invocation = SimpleNamespace(cwd=str(tmp_path), git_env={}, rest=[], sub='commit')
    with patch.object(hook.triage, 'changes_from_index', return_value=[]), \
            patch.object(hook.triage, '_toplevel', return_value=str(tmp_path)), \
            patch.object(hook.triage, 'derive', return_value=hook.triage.Result('standing', frozenset(), [], [])), \
            patch.object(hook, 'commit_message', return_value='fix'), \
            patch.object(hook, '_recorded', return_value=(recorded, None, False)), \
            patch.object(hook.triage, 'raises', return_value=[raised]), \
            patch.object(hook.triage, 'rigor', return_value='normal'), \
            patch.object(hook.triage, 'current', return_value=hook.triage.Fields()) as current:
        hook._triage_block(invocation, 'unknown-size', [], ['hooks/g.py'])
    assert current.return_value.size == 'milestones'
    assert current.return_value.kind == 'decision'


def test_one_width_uses_the_architecture_sweep(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    import triage_commit_check as hook
    import review_ledger
    seen = []

    def owed(_repo, _hashes, *, mandates):
        seen.append(mandates)
        return []

    for keys in (None, '', 'cq.D', 'cq.D,architecture-sweep'):
        with patch.dict(os.environ), \
                patch.object(hook, "_blobs", return_value={}), \
                patch.object(hook.triage, "rigor", return_value="normal"), \
                patch.object(review_ledger, "owed_staged", side_effect=owed):
            if keys is None:
                os.environ.pop('HARNESS_REVIEW_CANDIDATE_KEYS', None)
            else:
                os.environ['HARNESS_REVIEW_CANDIDATE_KEYS'] = keys
            assert hook._owed(SimpleNamespace(cwd=str(tmp_path)), ["fix.md"],
                              hook.triage.Fields(paths=("fix.md",)), "one-width") is None
        assert seen[-1] == ["architecture-sweep"], (keys, seen)
    assert seen == [["architecture-sweep"]] * 4, seen


def test_sized_width_counts_the_design_floor_and_thorough_counts_the_roster(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    import triage_commit_check as hook
    import review_ledger
    seen = []

    def owed(_repo, _hashes, *, mandates):
        seen.append(mandates)
        return []

    fields = hook.triage.Fields(paths=("fix.md",), size="milestones", kind="decision")
    for rigor in ("normal", "thorough"):
        with patch.dict(os.environ, {"HARNESS_REVIEW_CANDIDATE_KEYS": "cq.D,architecture-sweep"}), \
                patch.object(hook, "_blobs", return_value={}), \
                patch.object(hook.triage, "rigor", return_value=rigor), \
                patch.object(review_ledger, "owed_staged", side_effect=owed):
            hook._owed(SimpleNamespace(cwd=str(tmp_path)), ["fix.md"], fields, "sized-" + rigor)
    assert seen[0] == ["architecture-sweep"], seen
    assert set(seen[1]) == {"architecture-sweep", "cq.D"}, seen


def test_mechanical_one_context_leaves_review_to_session_end(tmp_path):
    import review_ledger
    h = Harness(str(tmp_path))
    h.keys = "architecture-sweep"
    root = make_project(str(tmp_path), "mechanical-owed")
    blob = git(root, "rev-parse", "HEAD:notes.txt").strip()
    head = git(root, "rev-parse", "HEAD").strip()
    row = ledger_row("architecture-sweep", review_ledger.rule_hash("architecture-sweep"),
                     head, {"notes.txt": blob})
    with open(h.ledger, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    write(root, "notes.txt", "notes changed since review\n")
    stage(root, "notes.txt")
    for kind, size, expected in (("mechanical", "one-context", ""),
                                ("decision", "one-context", OWED_LINE % (1, 1, 0)),
                                ("mechanical", "milestones", OWED_LINE % (1, 1, 0))):
        trailer = (TRAILER % ("shared", "gate")).replace("size=one-context", "size=" + size)
        write(root, "msg.txt", "change reviewed notes\n\n" + trailer + " kind=" + kind + "\n")
        ctx, raw, _ = h.context(root, payload(root, "git commit -F msg.txt",
                                             session="owed-" + kind + "-" + size))
        assert raw[0] == 0 and not raw[2].strip(), raw
        assert ctx == expected, (kind, size, ctx, raw)


def test_mechanical_one_context_skips_owed_at_every_rigor(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    import triage_commit_check as hook
    import review_ledger
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    fields = hook.triage.Fields(kind="mechanical", size="one-context", paths=("notes.txt",))
    for rigor in ("light", "normal", "thorough"):
        with patch.object(hook.triage, "rigor", return_value=rigor), \
                patch.object(hook, "_blobs", return_value={"notes.txt": "new"}), \
                patch.object(review_ledger, "owed_staged", return_value=[]) as owed:
            assert hook._owed(SimpleNamespace(cwd=str(tmp_path)), ["notes.txt"], fields, rigor) is None
            owed.assert_not_called()


def test_premises_missing_quote_and_once(tmp_path, monkeypatch):
    import triage_commit_check as hook
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    root = make_project(str(tmp_path), "premises")
    write(root, ".claude/rules/example.md", "rule\n")
    stage(root, ".claude/rules/example.md")
    from _git_commit import commit_invocations
    write(root, "msg.txt", 'decision\n\n```premises\n{"claim":"quote", "evidence":[{"file":"notes.txt","quote":"missing quote"}]}\n```\n')
    inv = next(iter(commit_invocations("git commit -F msg.txt", root)))
    fields = hook.triage.Fields(paths=(".claude/rules/example.md",))
    assert "UNVERIFIED" in hook._premises(inv, "proof-session", fields)
    write(root, "msg.txt", "decision without claims\n")
    assert "cause, count or ruling" in hook._premises(inv, "proof-session", fields)
    assert hook._premises(inv, "proof-session", fields) is None


def test_premises_ask_is_keyed_by_commit_message(tmp_path, monkeypatch):
    import triage_commit_check as hook
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    root = make_project(str(tmp_path), "premises-ask-commit")
    write(root, ".claude/rules/example.md", "rule\n")
    stage(root, ".claude/rules/example.md")
    from _git_commit import commit_invocations
    inv = next(iter(commit_invocations("git commit -F msg.txt", root)))
    fields = hook.triage.Fields(paths=(".claude/rules/example.md",))
    write(root, "msg.txt", "first decision without claims\n")
    assert "cause, count or ruling" in hook._premises(inv, "ask-commit-session", fields)
    write(root, "msg.txt", "second decision without claims\n")
    assert "cause, count or ruling" in hook._premises(inv, "ask-commit-session", fields)


def test_prose_premises_note_names_fence_form(tmp_path, monkeypatch):
    import triage_commit_check as hook
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    root = make_project(str(tmp_path), "premises-prose-fence")
    write(root, ".claude/rules/example.md", "rule\n")
    stage(root, ".claude/rules/example.md")
    from _git_commit import commit_invocations
    write(root, "msg.txt", "decision\n\npremises:\n- the count is 3\n")
    inv = next(iter(commit_invocations("git commit -F msg.txt", root)))
    fields = hook.triage.Fields(paths=(".claude/rules/example.md",))
    note = hook._premises(inv, "prose-fence-session", fields)
    assert note and "```premises" in note and "JSONL" in note, note


def test_prose_premises_report_each_new_message_without_deny(tmp_path):
    h = Harness(str(tmp_path))
    root = make_project(str(tmp_path), "prose-premises")
    write(root, ".claude/rules/example.md", "rule\n")
    stage(root, ".claude/rules/example.md")
    session = "prose-premises"
    command = "git commit -F msg.txt -- .claude/rules/example.md"
    trailer = TRAILER % ("shared", "gate") + " kind=decision\n"
    write(root, "msg.txt", "decision without claims\n\n" + trailer)
    ctx, raw, _ = h.context(root, payload(root, command, session=session))
    assert raw[0] == 0 and "cause, count or ruling" in ctx, raw
    messages = (
        "premises:\n- Failure handling forbade auto-fix and required owner response even when\n  the repair was fully in scope.",
        "Premises: current rule lines 29 and 81 still banned every per-frame lookup;\npattern_enforcer.py:127 already asks the stable-path question after S14.",
    )
    for message in messages:
        write(root, "msg.txt", "decision\n\n" + message + "\n\n" + trailer)
        ctx, raw, parsed = h.context(root, payload(root, command, session=session))
        assert raw[0] == 0 and "permissionDecision" not in parsed.get("hookSpecificOutput", {}), raw
        assert "prose" in ctx and "fenced" in ctx and "evidence_contract.md" in ctx, ctx
        repeated, raw, _ = h.context(root, payload(root, command, session=session))
        assert raw[0] == 0 and "[premises]" not in repeated, raw
    write(root, "msg.txt", 'decision\n\n```premises\n{"claim":"notes exist","evidence":[{"file":"notes.txt","quote":"notes"}]}\n```\n\n' + trailer)
    ctx, raw, _ = h.context(root, payload(root, command, session=session))
    assert raw[0] == 0 and "[premises]" not in ctx, raw


def test_owed_line_once_for_changed_set(tmp_path, monkeypatch):
    import triage_commit_check as hook
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "state"))
    assert hook._once("owed-session", "review-owed", "changed pair") == "changed pair"
    assert hook._once("owed-session", "review-owed", "changed pair") is None
    assert hook._once("owed-session", "review-owed", "new pair") == "new pair"


def test_standing_reach_names_admission_not_declaration(tmp_path):
    h = Harness(str(tmp_path))
    root = make_project(str(tmp_path), "standing")
    git(root, "rm", "--cached", "Jmodot")
    git(root, "commit", "-q", "-m", "remove unpopulated gitlink for -a case")
    write(root, ".claude/CLAUDE.md", "standing instruction\n")
    stage(root, ".claude/CLAUDE.md")
    for kind, expected in (("decision", "admitted"), ("mechanical", "not admitted")):
        write(root, "msg.txt", "change standing text\n\n" + TRAILER % ("shared", "gate")
              + " kind=" + kind + "\n")
        for command in ("git commit -F msg.txt", "git commit -a -F msg.txt",
                        "git commit -F msg.txt -- .claude/CLAUDE.md"):
            ctx, raw, parsed = h.context(root, payload(root, command, session="standing-" + kind))
            assert raw[0] == 0, raw
            assert "computed reach=standing" in ctx, ctx
            assert "harness-quality-seat: " + expected in ctx, ctx
            assert "raise the trailer" not in ctx and "re-triage" not in ctx, ctx
            assert "updatedInput" not in parsed.get("hookSpecificOutput", {}), parsed
    write(root, "msg.txt", "standing and wiring\n\n" + TRAILER % ("shared", "gate") + "\n")
    add_node(root)
    stage(root, "Dungeon/a.tscn")
    ctx, raw, _ = h.context(root, payload(root, "git commit -F msg.txt", session="standing-oracle"))
    assert raw[0] == 0, raw
    assert "computed reach=standing" in ctx and "oracle adds runtime" in ctx, ctx
    raise_block = ctx.split(RAISE_HEAD, 1)[1]
    assert "reach shared -> standing" not in raise_block, ctx


def test_triage_above_attribution_paragraph_is_recorded(tmp_path):
    h = Harness(str(tmp_path))
    root = make_project(str(tmp_path), "attribution-gap")
    write(root, "notes.txt", "notes v2\n")
    stage(root, "notes.txt")
    write(root, "msg.txt", "change notes\n\n" + TRAILER % ("local", "gate")
          + "\n\nClaude-Session: https://claude.ai/code/session_proof\n")
    result = h.context(root, payload(root, "git commit -F msg.txt", session="attribution-gap"))
    assert is_silent(result), result


def test_launcher_derived_trailers(tmp_path):
    import _git_commit
    h = Harness(str(tmp_path))
    root = make_project(str(tmp_path), "launcher")
    write(root, "notes.txt", "notes v2\n")
    write(root, "hooks/g.py", "x = 2\n")
    stage(root, "notes.txt", "hooks/g.py")
    local = "Triage-Derived: reach=local oracle=none"
    shared = "Triage-Derived: reach=shared oracle=new-test"
    commands = (
        ("bash -c 'git commit -m nested -- notes.txt'", [local]),
        ("bash -c 'git commit -m nested -- notes.txt'; git commit -m direct -- hooks/g.py", [local, shared]),
        ('env sh -c "git commit -m nested -- notes.txt"; git commit -m direct -- hooks/g.py', [local, shared]),
        ('bash -c "sh -c \'git commit -m nested -- notes.txt\'"; git commit -m direct -- hooks/g.py', [local, shared]),
        ("eval 'git commit -m nested -- notes.txt'; git commit -m direct -- hooks/g.py", [local, shared]),
        ("bash -n -c 'git commit -m ignored -- notes.txt'; git commit -m direct -- hooks/g.py", [shared]),
        ("bash -c 'git com'\"mit -m nested -- notes.txt\"; git commit -m direct -- hooks/g.py", [None, shared]),
        ("BODY='git commit -m nested -- notes.txt'; bash -c \"$BODY\"; git commit -m direct -- hooks/g.py", [None, shared]),
    )
    for command, expected in commands:
        ctx, raw, parsed = h.context(root, payload(root, command, session="launcher-derived"))
        updated = parsed.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
        calls = _git_commit.commit_invocations(updated, root)
        messages = [_git_commit.commit_message(call.rest, call.cwd) or "" for call in calls]
        check("launcher derived trailer stays with its commit: " + command,
              raw[0] == 0 and len(messages) == len(expected) and all(
                  trailer in message if trailer else "Triage-Derived:" not in message
                  for message, trailer in zip(messages, expected)), (raw, updated, messages))
        if expected[0] is None:
            check("unsafe launcher body stays unchanged: " + command,
                  updated.split(';')[0] == command.split(';')[0], updated)
        original = _git_commit.commit_invocations(command, root)
        check("launcher rewrite preserves commit arguments: " + command,
              [call.rest for call in calls] == [
                  call.rest if trailer is None else ["--trailer", trailer] + call.rest
                  for call, trailer in zip(original, expected)], updated)


def main(tmp):
    for case in (test_launcher_derived_trailers, test_one_width_uses_the_architecture_sweep,
                 test_mechanical_one_context_leaves_review_to_session_end,
                 test_standing_reach_names_admission_not_declaration,
                 test_triage_above_attribution_paragraph_is_recorded):
        directory = os.path.join(tmp, case.__name__)
        os.makedirs(directory)
        case(directory)
        check(case.__name__, True)
    h = Harness(tmp)
    commit = "git commit -F msg.txt"

    # --- value-only data edit vs node/signal edit, under reach=local oracle=gate ----------------
    root = make_project(tmp, "p1")
    write(root, "msg.txt", "fix: change damage\n\n" + TRAILER % ("local", "gate") + "\n")
    write(root, "Dungeon/a.tres", '[gd_resource type="Resource"]\n[resource]\ndamage = 5\nlabel = "x"\n')
    stage(root, "Dungeon/a.tres")
    res = h.context(root, payload(root, commit))
    check("a value-only data edit prints no raise", is_silent(res), res)
    git(root, "reset", "-q", "--hard")
    add_node(root)
    stage(root, "Dungeon/a.tscn")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("a node edit under reach=local oracle=gate prints oracle runtime with its probe, unvalidated",
          RAISE_HEAD in ctx and "oracle adds runtime: tag wiring (Dungeon/a.tscn); probe: launch the scene [unvalidated]" in ctx,
          raw)
    check("the raise names no path of the owner doc (K1)", "execution_depth" not in ctx, ctx)
    git(root, "reset", "-q", "--hard")
    with open(os.path.join(root, "Dungeon", "a.tscn"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write('[connection signal="body_entered" from="Hit" to="." method="_on"]\n')
    stage(root, "Dungeon/a.tscn")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("a signal edit prints oracle runtime", "oracle adds runtime: tag wiring" in ctx, raw)
    ctx, raw, _ = h.context(root, payload(root, 'git commit -m "fix: y" -m "%s"' % (TRAILER % ("local", "gate"))))
    check("a trailer given by -m is read", "oracle adds runtime" in ctx, raw)
    write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate+runtime") + "\n")
    check("the same edit under a trailer that names runtime prints nothing",
          is_silent(h.context(root, payload(root, commit))))
    write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    git(root, "reset", "-q", "--hard")

    # --- staged gitlink -----------------------------------------------------------------------
    git(root, "update-index", "--cacheinfo", "160000,%s,Jmodot" % GITLINK_B)
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("a staged gitlink under a reach=local trailer prints the raise to external",
          "reach local -> external: tag jmodot-pointer (Jmodot)" in ctx, raw)
    write(root, "msg.txt", "chore: bump\n\n" + TRAILER % ("external", "gate") + "\n")
    check("the same gitlink under reach=external prints nothing", is_silent(h.context(root, payload(root, commit))))
    write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    git(root, "reset", "-q", "--hard")

    # --- no trailer and no record; unreadable trailer -----------------------------------------
    write(root, "notes.txt", "notes v2\n")
    stage(root, "notes.txt")
    ctx, raw, parsed = h.context(root, payload(root, 'git commit -m "plain message"', session="sess-none"))
    updated = parsed.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
    check("no trailer and local diff gets a derived trailer without context",
          raw[0] == 0 and ctx == "" and '--trailer "Triage-Derived: reach=local oracle=none"' in updated, raw)
    write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    check("a satisfying trailer on the same staged change prints nothing",
          is_silent(h.context(root, payload(root, commit, session="sess-none"))))
    write(root, "msg.txt", "fix: x\n\n" + (TRAILER % ("galactic", "gate")) + "\n")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("an unreadable trailer prints its reason",
          "[triage] Triage: trailer unreadable (" in ctx and "galactic" in ctx and "grammar: triage.py --help." in ctx
          and NO_TRIAGE not in ctx, raw)
    write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    git(root, "reset", "-q", "--hard")

    # --- a task record satisfies, or is raised over -------------------------------------------
    add_node(root)
    stage(root, "Dungeon/a.tscn")
    rec = h.record(root, "rec-high", "sess-rec-high", "shared", "gate+runtime", ["Dungeon/a.tscn"])
    check("the satisfying record was stored", rec.returncode == 0, rec.stderr)
    plain = 'git commit -m "no trailer here"'
    # A satisfying record prints no advice; the hook writes the record's own triage into the commit as
    # its `Triage:` trailer, which the next per-commit read (`owed`, the escape log) needs.
    ctx, (code, out, err), parsed = h.context(root, payload(root, plain, session="sess-rec-high"))
    rewrite = (parsed.get("hookSpecificOutput") or {}).get("updatedInput", {}).get("command", "")
    check("a satisfying task record prints nothing",
          code == 0 and ctx == "" and not err.strip() and '--trailer "Triage: reach=shared' in rewrite,
          (ctx, out, err))
    rec = h.record(root, "rec-low", "sess-rec-low", "local", "gate", ["notes.txt"])
    check("the low record was stored", rec.returncode == 0, rec.stderr)
    ctx, raw, _ = h.context(root, payload(root, plain, session="sess-rec-low"))
    check("a task record below the derived oracle prints the raise", "oracle adds runtime" in ctx, raw)
    git(root, "reset", "-q", "--hard")

    # --- pathspec commit reads the working tree ------------------------------------------------
    add_node(root)
    ctx, raw, _ = h.context(root, payload(root, commit + " -- Dungeon/a.tscn"))
    check("an unstaged scene-wiring edit committed by pathspec prints the raise", "oracle adds runtime" in ctx, raw)
    res = h.context(root, payload(root, commit))
    check("the same unstaged edit under a plain commit (nothing staged) prints nothing", is_silent(res), res)
    git(root, "reset", "-q", "--hard")

    # --- negatives on a fixture that fires ----------------------------------------------------
    add_node(root)
    stage(root, "Dungeon/a.tscn")
    check("control: the fixture fires on a real commit",
          "oracle adds runtime" in h.context(root, payload(root, commit))[0])
    for label, command, tool in (
            ("git log --grep commit", "git log --grep commit", "Bash"),
            ('echo "git commit"', 'echo "git commit"', "Bash"),
            ("a quoted mention inside another git command", 'git log --grep="git commit -am"', "Bash"),
            ("a PowerShell git status", "git status", "PowerShell"),
            ("a heredoc body naming the commit", "cat > m <<'EOF'\ngit commit -F msg.txt\nEOF", "Bash"),
            ("a non-Bash tool", commit, "Read")):
        res = h.context(root, payload(root, command, tool=tool))
        check("negative: %s prints nothing" % label, is_silent(res), res)
    git(root, "reset", "-q", "--hard")

    # --- a worktree of the planted repo is checked; a second repo with its own .claude stays silent
    wt = os.path.join(tmp, "p1-wt")
    git(root, "worktree", "add", "-q", wt, "-b", "wt-branch")
    write(wt, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    add_node(wt)
    stage(wt, "Dungeon/a.tscn")
    ctx, raw, _ = h.context(root, payload(wt, commit))
    check("a commit in a worktree of the project is checked", "oracle adds runtime" in ctx, raw)
    other = make_project(tmp, "other")
    write(other, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
    add_node(other)
    stage(other, "Dungeon/a.tscn")
    res = h.context(root, payload(other, commit))
    check("a commit in a second repo that holds its own .claude/ stays silent", is_silent(res), res)
    ctx, raw, _ = h.context(other, payload(other, commit))
    check("control: the second repo fires when it is the project", "oracle adds runtime" in ctx, raw)
    res = h.context(root, payload(other, "git -C %s commit -F msg.txt" % other.replace("\\", "/"), session="s2"))
    check("`git -C <second repo> commit` from the project's cwd stays silent", is_silent(res), res)
    ctx, raw, _ = h.context(root, payload(tmp, "git -C %s commit -F msg.txt" % wt.replace("\\", "/")))
    check("`git -C <worktree> commit` from elsewhere is checked", "oracle adds runtime" in ctx, raw)

    # --- a read error goes to stderr with exit 1, never to the model ----------------------------
    add_node(root)
    stage(root, "Dungeon/a.tscn")
    code, out, err = h.run(root, payload(root, "GIT_INDEX_FILE=/nonexistent/idx git commit -F msg.txt"), process=True)
    check("an unreadable index is a stderr line with exit 1 and nothing on the model channel",
          code == 1 and "additionalContext" not in out and "index" in err.lower(), (code, out, err))
    git(root, "reset", "-q", "--hard")

    # --- the check writes no shared state --------------------------------------------------------
    check("the check wrote no hook state", os.listdir(h.state) == [], os.listdir(h.state))

    # --- a merge commit reads only the paths the merge authored ------------------------------------
    merge_root = make_project(tmp, "merge")
    branch = git(merge_root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(merge_root, "checkout", "-q", "-b", "feature")
    add_node(merge_root)
    git(merge_root, "commit", "-q", "-am", "feature: add node")
    git(merge_root, "checkout", "-q", branch)
    write(merge_root, "notes.txt", "notes on main\n")
    git(merge_root, "commit", "-q", "-am", "main: notes")
    write(merge_root, "msg.txt", "merge: feature\n\n" + TRAILER % ("local", "gate") + "\n")
    git(merge_root, "merge", "--no-commit", "--no-ff", "feature")
    res = h.context(merge_root, payload(merge_root, commit))
    check("a merge commit reads only authored paths: the incoming scene edit raises nothing", is_silent(res), res)
    with open(os.path.join(merge_root, "Dungeon", "a.tscn"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write('[node name="Authored" type="Node3D" parent="."]\n')
    stage(merge_root, "Dungeon/a.tscn")
    ctx, raw, _ = h.context(merge_root, payload(merge_root, commit))
    check("a scene edit authored during the merge raises", "oracle adds runtime: tag wiring" in ctx, raw)

    # --- derived trailers preserve real shell/message shapes and remain idempotent ----------------
    previous_root = root
    root = make_project(tmp, "derived-shapes")
    write(root, "notes.txt", "notes v2\n")
    stage(root, "notes.txt")
    write(root, "plain.txt", "fix: file message\n")
    shapes = (
        'git commit -F plain.txt -- notes.txt',
        'git commit -m "$(cat <<\'EOF\'\nfix: heredoc\n\ngit commit is message text\nEOF\n)" -- notes.txt',
        'git -C "' + root.replace("\\", "/") + '" commit -m "plain" 2>/dev/null',
        'git commit -m "one"; git commit -m "two"',
        '2>/dev/null git commit -m "redirection first"',
        'git -c core.editor="unused value" commit -m "quoted global value"',
        'cat > data <<\'EOF\'\ngit commit -m "body"\nEOF\ngit commit -m "real"',
    )
    for command in shapes:
        ctx, raw, parsed = h.context(root, payload(root, command, session="derived-shapes"))
        updated = parsed.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command", "")
        import _git_commit
        count = len(_git_commit.commit_invocations(command, root))
        check("derived rewrite preserves shell bytes: " + command.splitlines()[0],
              raw[0] == 0 and ctx == "" and updated.count(' --trailer "Triage-Derived: reach=local oracle=none"') == count
              and updated.replace(' --trailer "Triage-Derived: reach=local oracle=none"', '') == command, raw)
        rewritten_invocations = _git_commit.commit_invocations(updated, root)
        check("derived options belong to commit arguments",
              len(rewritten_invocations) == count and all(
                  "Triage-Derived: reach=local oracle=none" in (_git_commit.commit_message(i.rest, i.cwd) or "")
                  for i in rewritten_invocations), updated)
        check("derived rewrite is idempotent", is_silent(h.context(root, payload(root, updated, session="derived-shapes"))))
    write(root, "hooks/g.py", "x = 2\n")
    stage(root, "hooks/g.py")
    ctx, raw, parsed = h.context(root, payload(root, 'git commit -m "shared"', session="derived-shapes"))
    check("derived shared reach speaks once in this call and records only derived fields",
          "derived reach=shared oracle=new-test" in ctx and "Triage-Derived:" in raw[1] and "kind=" not in raw[1], raw)

    root = previous_root
    # --- through the dispatcher -----------------------------------------------------------------------
    import pre_bash_dispatch  # noqa: E402
    import triage as triage_module  # noqa: E402
    names = [os.path.basename(m.__file__) for m, _argv in pre_bash_dispatch.HOOKS]
    check("triage_commit_check is registered among the advisories, before sidecar_dispatch_context",
          "triage_commit_check.py" in names and "sidecar_dispatch_context.py" in names
          and names.index("triage_commit_check.py") < names.index("sidecar_dispatch_context.py")
          and names.index("triage_commit_check.py") > names.index("foreground_wait_guard.py"), names)
    check("its import did not fall back to the stub",
          getattr(pre_bash_dispatch, "_TRIAGE_CHECK_IMPORT_ERROR", "unset") is None)
    stub_code, stub_out, stub_err = pre_bash_dispatch.run_hook(
        pre_bash_dispatch._advisory_stub("triage_commit_check", "ImportError: planted",
                                         "no commit triage advisory is printed"), (), "{}")
    check("a failed import registers a stub that reports on stderr, exit 1, nothing on the model channel",
          stub_code == 1 and stub_out == "" and "triage_commit_check" in stub_err and "planted" in stub_err,
          repr((stub_code, stub_out, stub_err)))

    add_node(root)
    stage(root, "Dungeon/a.tscn")
    chain = ((pre_bash_dispatch.triage_commit_check, ()),) if hasattr(pre_bash_dispatch, "triage_commit_check") else ()
    saved = dict(os.environ)
    os.environ.update(CLAUDE_PROJECT_DIR=root, HARNESS_HOOK_STATE_DIR=h.state, HARNESS_TASK_RECORD_DIR=h.records)
    real_derive = triage_module.derive
    try:
        code, out, err = pre_bash_dispatch.dispatch(json.dumps(payload(root, commit)), hooks=chain)
        check("control: the dispatcher carries the raise as additionalContext",
              code == 0 and "oracle adds runtime" in out, (code, out, err))

        def boom(*_args, **_kwargs):
            raise RuntimeError("planted crash")
        triage_module.derive = boom
        code, out, err = pre_bash_dispatch.dispatch(json.dumps(payload(root, commit)), hooks=chain)
        check("a crashed advisory stays non-blocking and names the fault on the model channel",
              code == 0 and "permissionDecision" not in out and "additionalContext" in out
              and "triage_commit_check exited 1" in out and "planted crash" in out,
              (code, out, err))
        check("the dispatcher's stderr carries the exit-1 line for the crashed check",
              "[pre_bash_dispatch] triage_commit_check exited 1" in err and "planted crash" in err, err)
    finally:
        triage_module.derive = real_derive
        os.environ.clear()
        os.environ.update(saved)

    # --- E18: the owed candidate review ----------------------------------------------------------
    import review_ledger
    h.keys = "architecture-sweep"
    owed = ""
    root = make_project(tmp, "p_owed")
    write(root, "msg.txt", "fix: x\n\n" + (TRAILER % ("shared", "gate")).replace(
        "size=one-context", "size=milestones") + "\n")
    write(root, "notes.txt", "notes v2\n")
    stage(root, "notes.txt")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("a commit over an uncovered staged file prints the owed line and nothing else", ctx == owed, raw)
    ctx, raw, _ = h.context(root, payload(root, 'git commit -m "plain message"', session="sess-owed-none"))
    check("a no-triage commit over an uncovered file rewrites and keeps the owed line",
          NO_TRIAGE not in ctx and owed in ctx and 'Triage-Derived:' in raw[1], raw)
    write(root, "Dungeon/b.txt", "b\n")
    stage(root, "Dungeon/b.txt")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("never-reviewed files alone stay silent", ctx == "", raw)
    git(root, "reset", "-q", "--hard")
    write(root, "notes.txt", "notes v3\n")
    stage(root, "notes.txt")
    blob = git(root, "rev-parse", ":notes.txt").strip()
    head = git(root, "rev-parse", "HEAD").strip()
    row = ledger_row("architecture-sweep", review_ledger.rule_hash("architecture-sweep"), head, {"notes.txt": blob})
    with open(h.ledger, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    check("a staged file the ledger covers at its content prints nothing",
          is_silent(h.context(root, payload(root, commit))))
    write(root, "notes.txt", "notes v4\n")
    stage(root, "notes.txt")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("the same file edited after its review prints the split count", ctx == OWED_LINE % (1, 1, 0), raw)
    check("the same changed owed set prints once", is_silent(h.context(root, payload(root, commit))))
    changed_blob = git(root, "rev-parse", ":notes.txt").strip()
    derived = dict(row, files={"notes.txt": changed_blob}, **{"from": {"notes.txt": blob}})
    with open(h.ledger, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(derived) + "\n")
    check("derived coverage suppresses the advisory", is_silent(h.context(root, payload(root, commit))))
    with open(h.ledger, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    write(root, "notes.txt", "notes v3\n")
    check("a worktree-source commit (-a) over the covered content prints nothing",
          is_silent(h.context(root, payload(root, "git commit -a -F msg.txt"))))
    write(root, "notes.txt", "notes v5\n")
    ctx, raw, _ = h.context(root, payload(root, "git commit -a -F msg.txt"))
    check("a worktree-source commit (-a) over edited content prints the split count", ctx == OWED_LINE % (1, 1, 0), raw)
    git(root, "reset", "-q", "--hard")
    os.remove(h.ledger)

    add_node(root)
    stage(root, "Dungeon/a.tscn")
    os.makedirs(h.ledger)
    chain = ((pre_bash_dispatch.triage_commit_check, ()),) if hasattr(pre_bash_dispatch, "triage_commit_check") else ()
    saved = dict(os.environ)
    os.environ.update(CLAUDE_PROJECT_DIR=root, HARNESS_HOOK_STATE_DIR=h.state, HARNESS_TASK_RECORD_DIR=h.records,
                      HARNESS_REVIEW_CANDIDATE_KEYS="cq.C", HARNESS_REVIEW_LEDGER=h.ledger)
    try:
        write(root, "msg.txt", "fix: x\n\n" + TRAILER % ("local", "gate") + "\n")
        code, out, err = pre_bash_dispatch.dispatch(json.dumps(payload(root, commit)), hooks=chain)
        context = json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "") if out.strip() else ""
        check("an unreadable ledger exits 0 with the raise block and the did-not-run line, no owed line",
              code == 0 and "oracle adds runtime" in context
              and "[review] owed check did not run: ledger unreadable:" in context and h.ledger in context
              and "run review_ledger.py uncovered" in context
              and "candidate review owed" not in context, (code, out, err))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    os.rmdir(h.ledger)

    # The hook counts the unconditional set only: the override is popped, not set empty.
    import lens
    h.keys = None
    git(root, "reset", "-q", "--hard")
    write(root, "notes.txt", "notes v6\n")
    stage(root, "notes.txt")
    blob = git(root, "rev-parse", ":notes.txt").strip()
    head = git(root, "rev-parse", "HEAD").strip()
    _, lenses = lens.parse("session_audit")
    live = sorted({key for item in lenses if "conditional" not in (getattr(item, "admission", None) or [])
                   for key in item.mandates})

    def plant(skip=None):
        rows = [ledger_row(key, review_ledger.rule_hash(key), head, {"notes.txt": blob})
                for key in live if key != skip]
        with open(h.ledger, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(row) + "\n" for row in rows))

    plant()
    check("rows for every unconditional mandate leave a staged file with no owed line",
          is_silent(h.context(root, payload(root, commit))))
    plant(skip="cq.C")
    ctx, raw, _ = h.context(root, payload(root, commit))
    check("a never-reviewed mandate alone stays silent", ctx == "", raw)
    os.remove(h.ledger)
    h.keys = ""

    print("\n%d failure(s)" % len(FAILURES))
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--legacy"]:
        _tmp = tempfile.mkdtemp(prefix="triage_check_")
        try:
            sys.exit(main(_tmp))
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
    from _case_pool import run_sharded
    # The legacy checks run in a child while the pytest cases shard across processes; a red pytest case ends both.
    legacy = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--legacy"], stdin=subprocess.DEVNULL)
    pytest_rc = run_sharded(__file__)
    if pytest_rc:
        legacy.kill()
    sys.exit(pytest_rc or legacy.wait())
