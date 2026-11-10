#!/usr/bin/env python3
"""Re-runnable proof for hooks/duplicate_commit_check.py -- the advisory that prints each near-duplicate
pair a commit's changed instruction text forms with an existing rule.

Real PreToolUse payloads go on stdin against a planted project (its own `instruction_scope`, rule files
and seed commit), and the assertion is on the emitted channel: `additionalContext` with exit 0, never a
deny. Each case that expects `{}` has a sibling that fires on an equivalent fixture, so a hook that has
gone silent cannot pass this proof. The budget case imports the module and moves its start mark, so the
skip line is reached without waiting for a real overrun; the merge cases build the merge state with
`--no-commit` and judge `git merge --continue`.

Every case spawns the hook and, through it, the scan, so the cases run through `_case_pool` in parallel:
serial, the whole proof outruns the harness's per-proof timeout on a loaded machine.

    python3 .claude/tests/test_duplicate_commit_check_project.py
"""
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.normpath(os.path.join(HERE, ".."))
HOOKS = os.path.join(CLAUDE, "hooks")
HOOK = os.path.join(HOOKS, "duplicate_commit_check.py")
TOOL = os.path.join(CLAUDE, "tools", "restate_scan.py")
sys.path.insert(0, HOOKS)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

from _case_pool import run_cases  # noqa: E402
import _settings_probe  # noqa: E402

# The same planted near-copy the restate_scan proof uses: one word apart, normative on both sides.
OWNER = ("Never run a second native job while the five-hour window is above its threshold; the guard denies "
         "it and names the reset.")
NEAR = ("Never run a second native job while the five-hour window is over its threshold; the guard denies it "
        "and names the reset.")
SCOPE = {"paths": [".claude/rules/*.md"], "rows_dir": ".claude/instruction_rows"}


def _rm(path):
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_onerror)


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                          check=True, env=env, stdin=subprocess.DEVNULL).stdout


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_project(tmp, name, files, scope=SCOPE):
    """A planted project with its own `instruction_scope`, one seed commit, and `files` under it."""
    root = os.path.join(tmp, name)
    os.makedirs(root)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    if scope is not None:
        write(root, ".claude/skills/project_subsystems/adaptation.json", json.dumps({"instruction_scope": scope}))
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    return root


def payload(root, command, tool="Bash"):
    return {"tool_name": tool, "session_id": "dupcheck", "cwd": root, "tool_input": {"command": command}}


def env_for(root, extra=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root, PYTHONIOENCODING="utf-8")
    env.pop("GIT_INDEX_FILE", None)
    env.update(extra or {})
    return env


def run_hook(root, data, extra=None):
    """(exit code, stdout, stderr) of the hook run standalone on a real payload."""
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(data), capture_output=True, text=True,
                          encoding="utf-8", timeout=300, env=env_for(root, extra), cwd=root)
    return proc.returncode, proc.stdout, proc.stderr


def context(root, data, extra=None):
    """(additionalContext, (code, stdout, stderr)). '' for `{}` or for nothing; "CRASH ..." for an exit
    outside {0, 2} or a traceback, so no must-silent case reads a crash as silence."""
    code, out, err = run_hook(root, data, extra)
    text = out.strip()
    if code not in (0, 2) or "Traceback (most recent call last)" in err:
        return "CRASH exit=%s" % code, (code, out, err)
    if code != 0 or not text or text == "{}":
        return "", (code, out, err)
    try:
        hso = json.loads(text).get("hookSpecificOutput", {})
    except ValueError:
        return "", (code, out, err)
    return hso.get("additionalContext", ""), (code, out, err)


def pairs_named(ctx):
    """The (new, existing) location pairs a context reports, as `path:line` strings."""
    return re.findall(r"new (\S+:\d+) ⇄ existing (\S+:\d+)", ctx)


def rule_group(root, group_id, *flags):
    return subprocess.run([sys.executable, TOOL, "--repo", root, "rule", group_id, "distinct",
                           "--reason", "two windows, two limits"] + list(flags),
                          capture_output=True, text=True, encoding="utf-8", cwd=root)


def hold(ok, what, detail=""):
    if not ok:
        raise AssertionError("%s :: %s" % (what, str(detail)[:300]))


def hold_eq(got, want, what, detail=""):
    hold(got == want, "%s (got %r, want %r)" % (what, got, want), detail)


# ---- the cases -------------------------------------------------------------------------------------
def test_no_net_new_sentence_skips_scan(tmp_path, monkeypatch):
    import duplicate_commit_check as hook
    root = make_project(str(tmp_path), 'scope', {'.claude/rules/a.md': '# Before\n\n' + OWNER + '\n'})
    write(root, '.claude/rules/a.md', '# After\n\n' + OWNER + '\n')
    git(root, 'add', '-A')
    marker = tmp_path / 'scan_called'
    scanner = tmp_path / 'scanner.py'
    scanner.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("called")\nprint("[]")\n', encoding='utf-8')
    monkeypatch.setattr(hook, 'RESTATE_SCAN', str(scanner))
    monkeypatch.setattr(hook, '_STARTED', time.monotonic())
    real_run = hook.subprocess.run
    def run(*args, **kwargs):
        kwargs.setdefault('stdin', subprocess.DEVNULL)
        return real_run(*args, **kwargs)
    monkeypatch.setattr(hook.subprocess, 'run', run)
    invocation = next(iter(hook.commit_invocations('git commit -m x', root)))
    assert hook._check(invocation, root) is None
    assert not marker.exists(), 'the expensive duplicate scan ran without a net-new sentence'
    write(root, '.claude/rules/a.md', '# After\n\n' + OWNER + '\n' + NEAR + '\n')
    git(root, 'add', '-A')
    assert hook._check(invocation, root) is None
    assert marker.exists(), 'new rule must still run the scan'


def case_staged_near_copy_advises():
    """A near-copy staged against an existing rule prints the pair, and the hook only advises."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "dup", {".claude/rules/a.md": OWNER + "\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold_eq(pairs_named(ctx), [(".claude/rules/b.md:1", ".claude/rules/a.md:1")],
                "one pair line naming both locations", (ctx, err))
        hold("/rule_consistency" in ctx and "rule " in ctx and "distinct --reason" in ctx,
             "the advisory names /rule_consistency as the adjudicator and the ruling command", ctx)
        hold(code == 0 and "permissionDecision" not in out and "additionalContext" in out,
             "exit 0, additionalContext, no permissionDecision", (code, out, err))
    finally:
        _rm(tmp)


def case_a_mentioned_commit_is_not_an_invocation():
    """A heredoc body and an echo naming the command are not invocations, on a fixture that fires."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "mention", {".claude/rules/a.md": OWNER + "\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        for label, command in (("heredoc", "cat > m <<'EOF'\ngit commit -m x\nEOF"),
                               ("echo", 'echo "git commit -m x"')):
            ctx, res = context(root, payload(root, command))
            hold_eq(ctx, "", "a %s naming `git commit` prints nothing" % label, res)
    finally:
        _rm(tmp)


def case_out_of_scope_is_silent_and_its_sibling_fires():
    """Only an in-scope change is scanned; adding one to the same commit turns the advisory on."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "outofscope", {".claude/rules/a.md": OWNER + "\n"})
        write(root, "notes.txt", "a note beside the rules\n")
        git(root, "add", "-A")
        hold_eq(context(root, payload(root, "git commit -m x"))[0], "",
                "a commit whose only change is outside instruction_scope prints nothing")
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        hold_eq(len(pairs_named(context(root, payload(root, "git commit -m x"))[0])), 1,
                "its sibling fires: one in-scope near-copy in the same commit advises")
    finally:
        _rm(tmp)


def case_no_instruction_scope_is_silent():
    """Undeclared scope skips duplicate discovery and stays silent."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "noscope", {".claude/rules/a.md": OWNER + "\n"}, scope=None)
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold(code == 0 and not pairs_named(ctx) and "permissionDecision" not in out,
             "missing scope prints no duplicate pair and no deny", (ctx, err))
    finally:
        _rm(tmp)


def case_empty_index_is_silent():
    """A commit that publishes nothing has nothing to scan."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "empty", {".claude/rules/a.md": OWNER + "\n"})
        hold_eq(context(root, payload(root, "git commit -m x"))[0], "",
                "a plain commit with nothing staged prints nothing")
    finally:
        _rm(tmp)


def case_ruling_command_rules_the_group_after_the_commit():
    """The printed ruling command carries the pre-commit HEAD sha, and finds the group once HEAD moved."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "ruled", {".claude/rules/a.md": OWNER + "\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        before = context(root, payload(root, "git commit -m x"))[0]
        git(root, "commit", "-q", "-m", "the near-copy lands")
        m = re.search(r"restate_scan\.py rule ([0-9a-f]+) distinct --reason \"<why>\" --changed --base (\S+)",
                      before)
        hold(m is not None, "the printed ruling command carries the group id and a base ref", before)
        sha = git(root, "rev-parse", "HEAD~1").strip()
        hold_eq(m.group(2), sha, "the printed --base is the pre-commit HEAD sha, not HEAD")
        rc = rule_group(root, m.group(1), "--changed", "--base", m.group(2))
        rows = os.path.join(root, ".claude", "instruction_rows", "rulings.jsonl")
        hold(rc.returncode == 0 and os.path.isfile(rows),
             "the printed ruling command, run after the commit, rules that group (rc %d)" % rc.returncode,
             rc.stdout + rc.stderr)
    finally:
        _rm(tmp)


def case_ruled_pair_is_suppressed():
    """A pair ruled distinct stops printing while the same sentence stays staged."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "suppress", {".claude/rules/a.md": OWNER + "\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        fired = context(root, payload(root, "git commit -m x"))[0]
        hold_eq(len(pairs_named(fired)), 1, "control: the fixture prints the pair before any ruling", fired)
        gid = re.search(r"rule ([0-9a-f]+) distinct", fired)
        hold(gid is not None, "the advisory printed a group id", fired)
        rc = rule_group(root, gid.group(1), "--changed", "--staged", "--base", "HEAD")
        hold(rc.returncode == 0, "the group id the hook printed is the one the staged scan resolves (rc %d)"
             % rc.returncode, rc.stdout + rc.stderr)
        after = context(root, payload(root, "git commit -m x"))[0]
        hold_eq(pairs_named(after), [], "the ruled duplicate is suppressed")
    finally:
        _rm(tmp)


def case_alternate_index_is_the_source():
    """The hook reads the index the command names, not the default one."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "altindex", {".claude/rules/a.md": OWNER + "\n",
                                              ".claude/rules/b.md": "Placeholder for b, a rule of its own.\n"})
        alt = os.path.join(root, "alt.index").replace("\\", "/")
        aenv = dict(os.environ, GIT_INDEX_FILE=alt)
        git(root, "read-tree", "HEAD", env=aenv)
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "--", ".claude/rules/b.md", env=aenv)
        write(root, ".claude/rules/b.md", "Placeholder for b, a rule of its own.\n")
        hold_eq(context(root, payload(root, "git commit -m x"))[0], "",
                "control: the default index holds nothing, so the same commit prints nothing")
        ctx, _ = context(root, payload(root, "GIT_INDEX_FILE=%s git commit -m x" % alt))
        hold_eq(pairs_named(ctx), [(".claude/rules/b.md:1", ".claude/rules/a.md:1")],
                "a duplicate only in GIT_INDEX_FILE=<other> is still advised", ctx)
    finally:
        _rm(tmp)


def case_missing_index_exits_one_on_stderr():
    """An index the commit does not have is a read failure: exit 1 and a message, not a crash."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "missing", {".claude/rules/a.md": OWNER + "\n"})
        absent = os.path.join(root, "no_such.index").replace("\\", "/")
        ctx, (code, out, err) = context(root, payload(root, "GIT_INDEX_FILE=%s git commit -m x" % absent))
        hold(code == 1 and out.strip() == "" and "does not exist" in err and "Traceback" not in err,
             "an index the commit does not have exits 1 on stderr and prints no advisory",
             (code, out, err))
    finally:
        _rm(tmp)


def case_worktree_only_duplicate_is_advised():
    """A pathspec commit and `-a` publish the worktree, so a duplicate there is judged."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "worktree", {".claude/rules/a.md": OWNER + "\n",
                                              ".claude/rules/b.md": "Placeholder for b, a rule of its own.\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        for command in ("git commit -m x -- .claude/rules/b.md", "git commit -am x"):
            hold_eq(len(pairs_named(context(root, payload(root, command))[0])), 1,
                    "`%s` advises on a duplicate that exists only in the worktree" % command)
    finally:
        _rm(tmp)


def case_merge_arrival_is_not_advised():
    """A merge is judged on the paths it authors, so a near-copy from the incoming ref is not centered."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "merge", {".claude/rules/a.md": OWNER + "\n",
                                           ".claude/rules/b.md": "Placeholder for b, a rule of its own.\n",
                                           ".claude/rules/c.md": "Placeholder for c, another rule.\n"})
        trunk = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(root, "checkout", "-q", "-b", "feature")
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "commit", "-q", "-am", "feature adds a near-copy")
        git(root, "checkout", "-q", trunk)
        write(root, ".claude/rules/c.md", "Placeholder for c, a different rule.\n")
        git(root, "commit", "-q", "-am", "the trunk moves on")
        git(root, "merge", "--no-commit", "feature")
        hold_eq(context(root, payload(root, "git merge --continue"))[0], "",
                "a merge that brings a near-copy in from a ref prints nothing")
        write(root, ".claude/rules/b.md", NEAR.replace("the reset", "the next reset") + "\n")
        git(root, "add", "--", ".claude/rules/b.md")
        ctx, _ = context(root, payload(root, "git merge --continue"))
        hold_eq(len(pairs_named(ctx)), 1, "its sibling fires: a resolution that authors the sentence", ctx)
    finally:
        _rm(tmp)


def case_over_budget_prints_the_skip_line():
    """Past its budget the hook names the skipped scan and the command to run, never silence."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        import duplicate_commit_check as dcc
        root = make_project(tmp, "budget", {".claude/rules/a.md": OWNER + "\n"})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        saved = dcc._STARTED
        dcc._STARTED = time.monotonic() - (dcc.BUDGET_SECONDS + 5)
        os.environ["CLAUDE_PROJECT_DIR"] = root
        kept_in, kept_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(payload(root, "git commit -m x"))), io.StringIO()
        try:
            code = dcc.main()
            text = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = kept_in, kept_out
            dcc._STARTED = saved
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        hold(code == 0 and "skipped" in text and "restate_scan.py pairs --changed" in text,
             "one line naming the skipped scan and the command to run", text)
        hold("permissionDecision" not in text, "the skip line is an advisory, not a deny", text)
    finally:
        _rm(tmp)


def case_budget_constant_fits_the_settings_timeout():
    """The budget constant and the later sub-hooks fit the composed timeout, and the check has teeth."""
    import duplicate_commit_check as dcc

    def timeout_of(settings):
        """The `timeout` on a settings document's entry that runs pre_bash_dispatch.py, or None."""
        for entries in (settings.get("hooks") or {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    if "pre_bash_dispatch" in str(hook.get("command", "")):
                        return hook.get("timeout")
        return None

    def fits(base, project, composed, budget, later):
        declared = {t for t in (timeout_of(base), timeout_of(project)) if t is not None}
        return (len(declared) == 1 and timeout_of(composed) in declared
                and budget + later <= timeout_of(composed))

    def load(name):
        with open(os.path.join(CLAUDE, name), encoding="utf-8") as fh:
            return json.load(fh)

    base, project, composed = load("settings.base.json"), load("settings.project.json"), _settings_probe.settings(CLAUDE)
    budget, later = dcc.BUDGET_SECONDS, dcc.LATER_HOOK_SECONDS
    hold(fits(base, project, composed, budget, later),
         "the budget constant plus the later sub-hooks fit the composed timeout (%.1f + %.1f <= %s)"
         % (budget, later, timeout_of(composed)))
    planted = dict(project)
    planted["hooks"] = dict(project.get("hooks") or {})
    planted["hooks"]["PreToolUse"] = list(planted["hooks"].get("PreToolUse", [])) + [{
        "matcher": "Bash",
        "hooks": [{"type": "command", "timeout": 60,
                   "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/pre_bash_dispatch.py"'}]}]
    hold(not fits(base, planted, composed, budget, later),
         "the same check fails when settings.project.json declares a different timeout (planted)")
    hold(not fits(base, project, composed, budget + 10, later),
         "the same check fails when the budget grows past the timeout (planted)")


def case_single_channel_pairs_go_to_the_report_file():
    """Only a pair two or more channels agree on is printed. One-channel pairs (the scan's recall tail) go to the report file with every ruling command, and the context names it."""
    import duplicate_commit_check as dcc
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        members = [{"hash": h, "path": ".claude/rules/%s.md" % h, "line": 1} for h in ("c", "s", "w")]
        groups = [{"id": "g1", "center": "c", "citedOwner": None, "members": members,
                   "pairs": [{"a": "c", "b": "s", "channels": ["lexical", "anchor"]},
                             {"a": "c", "b": "w", "channels": ["lexical"]}]},
                  {"id": "g2", "center": "c", "citedOwner": None, "members": members,
                   "pairs": [{"a": "c", "b": "w", "channels": ["citation"]}]}]
        ctx = dcc._block(groups, "abc123", tmp)
        named = re.findall(r"(\.claude/logs/duplicate_scan_\w+\.txt)", ctx)
        hold(len(named) == 1, "the context names one report file", ctx)
        report = named[0]
        hold_eq(pairs_named(ctx), [(".claude/rules/c.md:1", ".claude/rules/s.md:1")],
                "only the two-channel pair is printed", ctx)
        path = os.path.join(tmp, *report.split("/"))
        hold(report in ctx and "2 more" in ctx, "the context names the report and the pairs it holds", ctx)
        with open(path, encoding="utf-8") as fh:
            full = fh.read()
        hold_eq(len(pairs_named(full)), 3, "the report holds every pair", full)
        hold("rule g1 distinct" in full and "rule g2 distinct" in full, "the report holds every ruling command", full)
        hold("rule g2 distinct" not in ctx, "a group with no printed pair gets no ruling line in the context", ctx)
        weak_only = [dict(groups[1], id="g3")]
        ctx = dcc._block(weak_only, "abc123", tmp)
        hold("more" not in ctx and "1 pair(s) share one channel only" in ctx,
             "with no pair printed, the count line does not say 'more'", ctx)
    finally:
        _rm(tmp)


def case_a_crashing_hook_is_not_silence():
    """A hook that exits 1 on a traceback is a CRASH to the helper, never the empty context a silent case accepts."""
    global HOOK
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    saved = HOOK
    try:
        root = make_project(tmp, "crash", {".claude/rules/a.md": OWNER + "\n"})
        HOOK = os.path.join(tmp, "broken_hook.py")
        write(tmp, "broken_hook.py", "import no_such_module_for_the_crash_case\n")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold(code == 1 and ctx.startswith("CRASH"), "an import failure is classified CRASH, not ''", (ctx, code, err))
    finally:
        HOOK = saved
        _rm(tmp)


def case_a_linked_worktree_commit_is_judged_in_that_worktree():
    """A commit staged in a linked worktree is scanned against that worktree's index, not the session checkout's."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "main", {".claude/rules/a.md": OWNER + "\n"})
        wt = os.path.join(tmp, "wt")
        git(root, "worktree", "add", "-q", wt)
        write(wt, ".claude/rules/b.md", NEAR + "\n")
        git(wt, "add", "-A")
        ctx, res = context(root, payload(wt, "git commit -m x"))
        hold_eq(pairs_named(ctx), [(".claude/rules/b.md:1", ".claude/rules/a.md:1")],
                "the worktree's staged near-copy is advised", (ctx, res))
        hold(os.path.isdir(os.path.join(wt, ".claude", "logs")) and not os.path.isdir(os.path.join(root, ".claude", "logs")),
             "the report lands under the worktree that was committed")
    finally:
        _rm(tmp)


def case_a_commit_aimed_by_git_dir_from_outside_is_judged():
    """`git --git-dir=<project>/.git commit` run from another directory is this project's commit; the
    sibling aimed at a foreign repo from the project's directory stays silent."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "aimed", {".claude/rules/a.md": OWNER + "\n"})
        foreign = make_project(tmp, "foreign", {"x.txt": "x\n"}, scope=None)
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        posix = root.replace("\\", "/")
        ctx, res = context(root, payload(foreign, "git --git-dir=%s/.git --work-tree=%s commit -m x" % (posix, posix)))
        hold_eq(len(pairs_named(ctx)), 1, "a --git-dir commit from outside the project is advised", (ctx, res))
        ctx, res = context(root, payload(root, "git --git-dir=%s/.git commit -m x" % foreign.replace("\\", "/")))
        hold_eq(ctx, "", "a foreign repo aimed from the project's directory is silent", res)
    finally:
        _rm(tmp)


def case_a_malformed_declared_scope_is_reported_not_silent():
    """A declared scope with paths but no rows_dir is a failure on stderr (exit 1), not a silent skip."""
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        root = make_project(tmp, "badscope", {".claude/rules/a.md": OWNER + "\n"},
                            scope={"paths": [".claude/rules/*.md"]})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold(code == 1 and "instruction_scope" in err and "rows_dir" in err and "permissionDecision" not in out,
             "the invalid scope reaches the advisory failure channel without a deny", (ctx, code, out, err))
        root = make_project(tmp, "emptypaths", {".claude/rules/a.md": OWNER + "\n"}, scope={"paths": []})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold(code == 1 and "instruction_scope.paths" in err and "permissionDecision" not in out,
             "a declared scope with empty paths is reported, not skipped as undeclared", (ctx, code, out, err))
        root = make_project(tmp, "declareddefault", {".claude/rules/a.md": OWNER + "\n"},
                            scope={"paths": [], "rows_dir": None})
        write(root, ".claude/rules/b.md", NEAR + "\n")
        git(root, "add", "-A")
        ctx, (code, out, err) = context(root, payload(root, "git commit -m x"))
        hold(code == 1 and "instruction_scope" in err and "permissionDecision" not in out,
             "a declared scope equal to the default is reported, not skipped as undeclared", (ctx, code, out, err))
    finally:
        _rm(tmp)


def case_reports_are_per_content_atomic_and_bounded():
    """Two different reports never share a file, an identical one reuses its file, and only the newest REPORT_KEEP stay."""
    import duplicate_commit_check as dcc
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    try:
        def groups_for(tag):
            members = [{"hash": h, "path": ".claude/rules/%s%s.md" % (h, tag), "line": 1} for h in ("c", "s")]
            return [{"id": "g" + tag, "center": "c", "citedOwner": None, "members": members,
                     "pairs": [{"a": "c", "b": "s", "channels": ["lexical"]}]}]
        first = re.findall(r"(\.claude/logs/\S+\.txt)", dcc._block(groups_for("1"), "abc", tmp))
        second = re.findall(r"(\.claude/logs/\S+\.txt)", dcc._block(groups_for("2"), "abc", tmp))
        again = re.findall(r"(\.claude/logs/\S+\.txt)", dcc._block(groups_for("1"), "abc", tmp))
        hold(first and second and first != second, "different evidence gets its own report file", (first, second))
        hold_eq(first, again, "identical evidence reuses its report file")
        for n in range(dcc.REPORT_KEEP + 5):
            dcc._block(groups_for("x%d" % n), "abc", tmp)
        kept = [n for n in os.listdir(os.path.join(tmp, ".claude", "logs")) if n.startswith(dcc.REPORT_STEM)]
        hold_eq(len(kept), dcc.REPORT_KEEP, "reports beyond the newest REPORT_KEEP are dropped", kept)
        leftovers = [n for n in os.listdir(os.path.join(tmp, ".claude", "logs")) if n.endswith(".tmp")]
        hold_eq(leftovers, [], "no scratch file outlives its write")
    finally:
        _rm(tmp)


def case_a_report_a_peer_prunes_mid_listing_still_writes():
    """A report another commit deletes between the listing and its mtime read does not fail this commit's write."""
    import duplicate_commit_check as dcc
    tmp = tempfile.mkdtemp(prefix="dupcheck_")
    folder = os.path.join(tmp, "logs")

    class _PeerPrunes:
        def __getattr__(self, name):
            return getattr(os, name)

        def listdir(self, path):
            names = os.listdir(path)
            return names + ([dcc.REPORT_STEM + "pruned.txt"] if os.path.normpath(path) == folder else [])

    real = dcc.os
    dcc.os = _PeerPrunes()
    try:
        dcc._write_report(os.path.join(folder, dcc.REPORT_STEM + "mine.txt"), "pairs\n")
        hold(os.path.exists(os.path.join(folder, dcc.REPORT_STEM + "mine.txt")), "the report is written")
    except OSError as exc:
        hold(False, "a peer's prune surfaced as a write failure", exc)
    finally:
        dcc.os = real
        _rm(tmp)


CASES = (case_a_report_a_peer_prunes_mid_listing_still_writes,case_single_channel_pairs_go_to_the_report_file,
         case_staged_near_copy_advises,
         case_a_mentioned_commit_is_not_an_invocation,
         case_out_of_scope_is_silent_and_its_sibling_fires,
         case_no_instruction_scope_is_silent,
         case_empty_index_is_silent,
         case_ruling_command_rules_the_group_after_the_commit,
         case_ruled_pair_is_suppressed,
         case_alternate_index_is_the_source,
         case_missing_index_exits_one_on_stderr,
         case_worktree_only_duplicate_is_advised,
         case_merge_arrival_is_not_advised,
         case_over_budget_prints_the_skip_line,
         case_budget_constant_fits_the_settings_timeout,
         case_a_crashing_hook_is_not_silence,
         case_a_linked_worktree_commit_is_judged_in_that_worktree,
         case_a_commit_aimed_by_git_dir_from_outside_is_judged,
         case_a_malformed_declared_scope_is_reported_not_silent,
         case_reports_are_per_content_atomic_and_bounded)


def main():
    return run_cases(CASES)


if __name__ == "__main__":
    legacy_rc = main()
    import pytest
    pytest_rc = pytest.main([__file__, '-q', '-s'])
    sys.exit(legacy_rc or pytest_rc)
