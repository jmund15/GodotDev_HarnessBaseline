#!/usr/bin/env python3
"""Re-runnable proof for hooks/_git_commit.py — the one commit parser every commit guard
imports. Each case plants a bypass shape that used to slip past at least one guard.

    python3 .claude/tests/test_git_commit_seam.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import _git_commit as gc  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


_REPOS_TO_CLEAN = []


def make_repo():
    repo = tempfile.mkdtemp(prefix="gcseam_")
    _REPOS_TO_CLEAN.append(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


_WORKTREES = []


def _run():
    cases = []
    cwd = "/work"

    # --- detection ---------------------------------------------------------------
    found = gc.commit_invocations("cat > m <<'EOF'\ngit commit -am x\nEOF\ngit status", cwd)
    cases.append(("a `git commit` inside a heredoc body is not an invocation", found == []))

    found = gc.commit_invocations("git add -A && git commit -F msg -- .claude", cwd)
    cases.append(("a real commit after && is found with its rest",
                  len(found) == 1 and found[0].sub == "commit" and found[0].rest == ["-F", "msg", "--", ".claude"]))

    found = gc.commit_invocations('git -C "/repo/wt" commit -m x', cwd)
    cases.append(("-C retargets the repo", found and found[0].cwd.replace("\\", "/") == "/work/repo/wt"
                  or (found and found[0].cwd.replace("\\", "/").endswith("/repo/wt"))))

    found = gc.commit_invocations("cd /elsewhere && git commit -m x", cwd)
    cases.append(("a preceding cd retargets the repo",
                  found and found[0].cwd.replace("\\", "/").endswith("/elsewhere")))

    found = gc.commit_invocations("HARNESS_ALLOW_X=1 git commit -m x", cwd)
    cases.append(("inline env prefix is captured", found and found[0].inline_env.get("HARNESS_ALLOW_X") == "1"))

    found = gc.commit_invocations("git merge feature && git cherry-pick abc123", cwd)
    cases.append(("merge and cherry-pick are commit-like", [f.sub for f in found] == ["merge", "cherry-pick"]))

    found = gc.commit_invocations("GIT_INDEX_FILE=i git commit -F msg 2>&1 | tail -4", cwd)
    cases.append(("`2>&1` is a redirect, not a separator or a pathspec",
                  len(found) == 1 and found[0].rest == ["-F", "msg"]
                  and found[0].git_env.get("GIT_INDEX_FILE") == "i"))

    for shape in ("> out.log", ">out.log", "2>/dev/null", "&> log", ">> log 2>&1", "< /dev/null"):
        found = gc.commit_invocations("git commit -m x %s" % shape, cwd)
        cases.append(("redirect `%s` is not a pathspec" % shape, found and found[0].rest == ["-m", "x"]))

    found = gc.commit_invocations("git commit -F - <<'EOF'\nmsg\nEOF", cwd)
    cases.append(("a heredoc operator is not a pathspec", found and found[0].rest == ["-F", "-"]))

    found = gc.commit_invocations('git commit -m "a > b" -- p', cwd)
    cases.append(("a quoted `>` stays in its argument", found and found[0].rest == ["-m", "a > b", "--", "p"]))

    found = gc.commit_invocations("git status & git commit -m x", cwd)
    cases.append(("a bare `&` still separates commands", found and found[0].rest == ["-m", "x"]))

    found = gc.commit_invocations('git log --grep="git commit -am"', cwd)
    cases.append(("a quoted mention inside another git command is not a commit", found == []))

    # Live misfire 2026-09-14: a quoted `|` split `rg -c 'git stash|git checkout --|git restore'`
    # into a `git checkout --` segment, and git_guardrails denied a read-only search.
    cases.append(("a separator inside single quotes does not split a segment",
                  list(gc.segments("rg -c 'git stash|git checkout --|git restore' f.txt"))
                  == ["rg -c 'git stash|git checkout --|git restore' f.txt"]))
    cases.append(("a separator inside double quotes does not split a segment",
                  list(gc.segments('grep "a && git reset --hard" f && git status'))
                  == ['grep "a && git reset --hard" f', "git status"]))
    cases.append(("an unquoted pipe still splits a segment",
                  list(gc.segments("echo a | git checkout -- x")) == ["echo a", "git checkout -- x"]))
    cases.append(("an unbalanced quote falls back to splitting every separator",
                  list(gc.segments("echo 'a | git checkout -- x")) == ["echo 'a", "git checkout -- x"]))
    cases.append(("a backslash-newline continues the commit line, so every pathspec after it is read",
                  [f.rest for f in gc.commit_invocations("git commit -F m -- a \\\n  b", cwd)] == [["-F", "m", "--", "a", "b"]]))
    cases.append(("a backslash-newline continuation leaves no lone backslash pathspec",
                  "\\" not in [w for f in gc.commit_invocations("git commit -F m -- a \\\n  b", cwd) for w in f.rest]))

    import shlex
    launchers = ("bash -c", "sh -c", "bash -lc", "bash -o pipefail -c", "eval")
    for launcher in launchers:
        body = 'cd child; export GIT_INDEX_FILE=alt.index; git -C deeper commit -m x'
        found = gc.commit_invocations(launcher + ' ' + shlex.quote(body), cwd)
        cases.append((launcher + ' finds the commit and its body cwd/env',
                      len(found) == 1 and found[0].cwd == gc.resolve_cd(gc.resolve_cd(cwd, 'child'), 'deeper')
                      and found[0].git_env == {'GIT_INDEX_FILE': 'alt.index'}))
        cases.append((launcher + ' read-only body commits nothing',
                      gc.commit_invocations(launcher + ' ' + shlex.quote('git status --short'), cwd) == []))
    found = gc.commit_invocations("export GIT_INDEX_FILE=outer; env -u GIT_INDEX_FILE -C child bash -c 'git commit -m x'", cwd)
    cases.append(('env launcher cwd/unset selects the body context',
                  len(found) == 1 and found[0].cwd == gc.resolve_cd(cwd, 'child')
                  and found[0].git_env == {'GIT_INDEX_FILE': None}))
    found = gc.commit_invocations("IDX=outer; bash -c 'GIT_INDEX_FILE=$IDX git commit -m x'", cwd)
    cases.append(('a non-exported variable is not inherited by a shell',
                  len(found) == 1 and found[0].git_env == {'GIT_INDEX_FILE': '$IDX'}))
    found = gc.commit_invocations("export GIT_INDEX_FILE=outer; bash -c 'git commit -m x'", cwd)
    cases.append(('a shell body inherits exported git env',
                  len(found) == 1 and found[0].git_env == {'GIT_INDEX_FILE': 'outer'}))
    found = gc.commit_invocations("GIT_INDEX_FILE=inner bash -c 'git commit -m x'; git commit -m y", cwd)
    cases.append(('a shell inline env does not leak back to its parent',
                  len(found) == 2 and found[0].git_env == {'GIT_INDEX_FILE': 'inner'} and found[1].git_env == {}))
    found = gc.commit_invocations("bash -c 'cd child; export GIT_INDEX_FILE=inner'; git commit -m x", cwd)
    cases.append(('a child shell cwd/export does not leak back',
                  len(found) == 1 and found[0].cwd == cwd and found[0].git_env == {}))
    found = gc.commit_invocations("eval 'cd child; export GIT_INDEX_FILE=inner'; git commit -m x", cwd)
    cases.append(('eval cwd/export changes persist in its parent',
                  len(found) == 1 and found[0].cwd == gc.resolve_cd(cwd, 'child')
                  and found[0].git_env == {'GIT_INDEX_FILE': 'inner'}))
    cases.append(('bash -n body executes no commit',
                  gc.commit_invocations("bash -nc 'git commit -m x'", cwd) == []))

    fold_repo = make_repo()
    for launcher in launchers:
        command = launcher + ' ' + shlex.quote('git add a.txt && git commit -m x')
        folded = gc.fold_tracked_adds(command, fold_repo)
        calls = gc.commit_invocations(folded or command, fold_repo)
        cases.append((launcher + ' folds tracked add inside its body',
                      folded is not None and len(calls) == 1 and calls[0].rest == ['-m', 'x', '--', 'a.txt']))

    dynamic = 'bash -c "git add a.txt && git commit -m $MESSAGE"'
    cases.append(('folding does not move outer double-quoted expansion into a child shell',
                  gc.fold_tracked_adds(dynamic, fold_repo) is None))
    found = gc.commit_invocations('IDX=outer; bash -c "GIT_INDEX_FILE=$IDX git commit -m x"', cwd)
    cases.append(('a known outer double-quoted expansion resolves before launching the shell',
                  len(found) == 1 and found[0].git_env == {'GIT_INDEX_FILE': 'outer'}))

    # --- rewrite coordinates ---------------------------------------------------------
    command = "echo before; git -c core.editor=x commit -m direct"
    call, = gc.commit_invocations(command, cwd)
    cases.append(('direct invocation carries its outer source offset and empty body path',
                  getattr(call, 'subcommand_end', None) == command.index('commit') + len('commit')
                  and getattr(call, 'body_path', None) == ()))
    command = 'bash -c "sh -c \'git commit -m nested\'"; git commit -m direct'
    nested, direct = gc.commit_invocations(command, cwd)
    path = getattr(nested, 'body_path', None)
    cases.append(('nested invocation carries enclosing source word spans and quote styles',
                  getattr(nested, 'subcommand_end', None) == command.index('commit') + len('commit')
                  and path is not None and [(w.start, w.end, w.quote) for w in path] == [
                      (command.index('"'), command.index('"', command.index('"') + 1) + 1, '"'),
                      (command.index("'"), command.index("'", command.index("'") + 1) + 1, "'")]
                  and getattr(direct, 'subcommand_end', None) == command.rindex('commit') + len('commit')
                  and getattr(direct, 'body_path', None) == ()))
    command = "bash -c 'git com'\"mit -m nested\"; git commit -m direct"
    nested, direct = gc.commit_invocations(command, cwd)
    cases.append(('unsafe body has no rewrite coordinates without losing the later direct offset',
                  getattr(nested, 'subcommand_end', 'missing') is None
                  and getattr(nested, 'body_path', 'missing') is None
                  and getattr(direct, 'subcommand_end', None) == command.rindex('commit') + len('commit')))

    legacy = gc.CommitInvocation(direct.sub, direct.rest, direct.cwd, direct.inline_env,
                                 direct.git_env, direct.assignments)
    cases.append(('rewrite metadata defaults preserve legacy construction and equality',
                  legacy.subcommand_end is None and legacy.body_path is None and legacy == direct))

    # --- parse_commit_args ------------------------------------------------------------
    cases.append(("-am reads as -a + -m <msg>", gc.parse_commit_args(["-am", "x"]) == (True, False, [])))
    cases.append(("--amend is read", gc.parse_commit_args(["--amend", "--no-edit"]) == (False, True, [])))
    cases.append(("pathspec after -- is read", gc.parse_commit_args(["-F", "m", "--", "a", "b"]) == (False, False, ["a", "b"])))

    # --- staged_paths / incoming_paths against a real repo ------------------------------
    repo = make_repo()
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("dirty\n")
    paths, err = gc.staged_paths([], repo)
    cases.append(("empty index, plain commit -> no paths", paths == set() and err is None))
    paths, _ = gc.staged_paths(["-a", "-m", "x"], repo)
    cases.append(("-a includes the dirty tracked file", paths == {"a.txt"}))
    paths, _ = gc.staged_paths(["--amend", "--no-edit"], repo)
    cases.append(("--amend republishes HEAD's paths", "a.txt" in paths))
    paths, err = gc.staged_paths([], tempfile.mkdtemp(prefix="gcseam_norepo_"))
    cases.append(("git failure reports the failing subcommand", paths is None and "diff --cached" in err))
    with open(os.path.join(repo, "b.txt"), "w") as fh:
        fh.write("staged elsewhere\n")
    git(repo, "add", "b.txt")
    paths, _ = gc.staged_paths(["-m", "x", "--", "a.txt"], repo)
    cases.append(("pathspec-only commit publishes only its pathspec", paths == {"a.txt"}))
    paths, _ = gc.staged_paths(["-i", "-m", "x", "--", "a.txt"], repo)
    cases.append(("-i adds the index to the pathspec", paths == {"a.txt", "b.txt"}))
    with open(os.path.join(repo, "spec.txt"), "w") as fh:
        fh.write("a.txt\n")
    paths, _ = gc.staged_paths(["-m", "x", "--pathspec-from-file=spec.txt"], repo)
    cases.append(("--pathspec-from-file is read as the pathspec", paths == {"a.txt"}))
    paths, err = gc.staged_paths(["-m", "x", "--pathspec-from-file=-"], repo)
    cases.append(("--pathspec-from-file=- cannot be read -> failure", paths is None and "pathspec-from-file" in err))

    # --- S4: staged_paths' pathspec scope -- fixes the known defect where another session's
    # staged files block or stale a pathspec commit -------------------------------------
    repo2 = make_repo()
    with open(os.path.join(repo2, "other.txt"), "w") as fh:
        fh.write("other\n")
    git(repo2, "add", "other.txt")
    with open(os.path.join(repo2, "mine.txt"), "w") as fh:
        fh.write("mine\n")
    git(repo2, "add", "mine.txt")

    paths, _ = gc.staged_paths(["-m", "x", "--", "mine.txt"], repo2)
    cases.append(("S4: a pathspec commit excludes another staged file",
                  paths == {"mine.txt"}))
    cases.append(("S4: an added file outside .claude/ still appears here -- this seam does not "
                  "judge SCOPE, only the committed set; the guard judges .claude/ scope",
                  "mine.txt" in paths))

    paths, _ = gc.staged_paths(["-i", "-m", "x", "--", "mine.txt"], repo2)
    cases.append(("S4: -i keeps the whole index despite the pathspec",
                  paths == {"mine.txt", "other.txt"}))
    paths, _ = gc.staged_paths(["--include", "-m", "x", "--", "mine.txt"], repo2)
    cases.append(("S4: --include is the same flag as -i",
                  paths == {"mine.txt", "other.txt"}))

    git(repo, "checkout", "-q", "-b", "feature")
    with open(os.path.join(repo, ".claude", "hooks", "h.py") if os.makedirs(os.path.join(repo, ".claude", "hooks"), exist_ok=True) is None else "", "w") as fh:
        fh.write("h = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature hook")
    git(repo, "checkout", "-q", "master") if subprocess.run(["git", "rev-parse", "--verify", "master"], cwd=repo, capture_output=True).returncode == 0 else git(repo, "checkout", "-q", "main")
    paths, _ = gc.incoming_paths("merge", ["feature"], repo)
    cases.append(("merge <branch> reports the branch's changed paths", ".claude/hooks/h.py" in paths))
    paths, _ = gc.incoming_paths("merge", ["--no-commit", "feature"], repo)
    cases.append(("merge --no-commit reports nothing (the later commit is judged)", paths == set()))
    paths, _ = gc.incoming_paths("cherry-pick", ["feature"], repo)
    cases.append(("cherry-pick <rev> reports the rev's paths", ".claude/hooks/h.py" in paths))
    for flags in (["-F", "msg.txt"], ["--file", "msg.txt"], ["-m", "a message"], ["-s", "ort"],
                  ["--strategy", "ort"], ["-X", "theirs"], ["--strategy-option", "theirs"]):
        paths, failed = gc.incoming_paths("merge", flags + ["feature"], repo)
        cases.append(("merge %s <value> <branch> reads the branch, not the option's value" % flags[0],
                      failed is None and paths is not None and ".claude/hooks/h.py" in paths))
    paths, failed = gc.incoming_paths("revert", ["-m", "1", "feature"], repo)
    cases.append(("revert -m <parent> <rev> reads the rev, not the mainline number",
                  failed is None and paths is not None and ".claude/hooks/h.py" in paths))

    # --- bypass_declared ----------------------------------------------------------------
    os.environ.pop("HARNESS_X_TEST", None)
    cases.append(("bypass: inline VAR=1", gc.bypass_declared({"HARNESS_X_TEST": "1"}, "HARNESS_X_TEST")))
    cases.append(("bypass: absent -> False", not gc.bypass_declared({}, "HARNESS_X_TEST")))
    os.environ["HARNESS_X_TEST"] = "1"
    cases.append(("bypass: hook env VAR=1", gc.bypass_declared({}, "HARNESS_X_TEST")))

    # --- the command's GIT_* environment ----------------------------------------------
    # A commit through another index (`GIT_INDEX_FILE=<f> git commit`, or an earlier `export`)
    # publishes THAT index. Guards that read the default index judged content the commit never had.
    found = gc.commit_invocations("export GIT_INDEX_FILE=/tmp/alt.index && git commit -m x", cwd)
    git_env = getattr(found[0], "git_env", {}) if found else {}
    cases.append(("an exported GIT_INDEX_FILE reaches a later commit", git_env.get("GIT_INDEX_FILE") == "/tmp/alt.index"))
    found = gc.commit_invocations("GIT_INDEX_FILE=/tmp/alt.index git commit -m x", cwd)
    git_env = getattr(found[0], "git_env", {}) if found else {}
    cases.append(("an inline GIT_INDEX_FILE is part of the commit's git env", git_env.get("GIT_INDEX_FILE") == "/tmp/alt.index"))
    found = gc.commit_invocations("export GIT_INDEX_FILE=/tmp/alt.index; unset GIT_INDEX_FILE; git commit -m x", cwd)
    git_env = getattr(found[0], "git_env", None) if found else None
    cases.append(("unset GIT_INDEX_FILE clears it for a later commit", git_env == {"GIT_INDEX_FILE": None}))
    found = gc.commit_invocations("export HARNESS_ALLOW_X=1 && git commit -m x", cwd)
    cases.append(("a non-git export is neither git env nor an inline bypass",
                  found and getattr(found[0], "git_env", None) == {} and found[0].inline_env == {}))
    alt_repo = make_repo()
    alt_index = os.path.join(alt_repo, "alt.index")
    alt_env = dict(os.environ, GIT_INDEX_FILE=alt_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=alt_repo, env=alt_env, check=True, capture_output=True)
    with open(os.path.join(alt_repo, "alt_only.txt"), "w") as fh:
        fh.write("alt\n")
    subprocess.run(["git", "add", "alt_only.txt"], cwd=alt_repo, env=alt_env, check=True, capture_output=True)
    try:
        paths, _ = gc.staged_paths([], alt_repo, env={"GIT_INDEX_FILE": alt_index})
    except TypeError:
        paths = None
    cases.append(("staged_paths reads the index the command names", paths == {"alt_only.txt"}))
    paths, _ = gc.staged_paths([], alt_repo)
    cases.append(("staged_paths without it still reads the default index", paths == set()))
    found = gc.commit_invocations('IDX=/tmp/alt.index && GIT_INDEX_FILE="$IDX" git commit -m x', cwd)
    cases.append(("a once-assigned literal variable expands into the git env",
                  found and found[0].git_env.get("GIT_INDEX_FILE") == "/tmp/alt.index"))
    paths, problem = gc.staged_paths([], alt_repo, env={"GIT_INDEX_FILE": "$IDX"})
    cases.append(("an unexpandable index fails instead of reading an empty index",
                  paths is None and "not a literal path" in (problem or "")))
    paths, problem = gc.staged_paths([], alt_repo, env={"GIT_INDEX_FILE": os.path.join(alt_repo, "none.index")})
    cases.append(("a missing index fails instead of reading an empty index",
                  paths is None and "does not exist" in (problem or "")))

    # --- commit_message: the message a commit carries, read through the same parser ------------
    def message(rest, cwd="/work"):
        return gc.commit_message(rest, cwd)

    cases.append(("commit_message joins repeated -m as paragraphs",
                  message(["-m", "subject", "-m", "Triage: reach=local"]) == "subject\n\nTriage: reach=local"))
    cases.append(("commit_message reads --message=<text> and an attached -m<text>",
                  message(["--message=one"]) == "one" and message(["-mtwo"]) == "two"))
    cases.append(("commit_message reads the -m of a combined -am",
                  message(["-am", "combined"]) == "combined"))
    msg_repo = tempfile.mkdtemp(prefix="gcmsg_")
    with open(os.path.join(msg_repo, "msg.txt"), "w", encoding="utf-8") as fh:
        fh.write("from file\n\nTriage: reach=shared\n")
    cases.append(("commit_message reads -F <file> relative to the commit's cwd",
                  (message(["-F", "msg.txt"], msg_repo) or "").startswith("from file")
                  and "Triage: reach=shared" in (message(["--file=msg.txt"], msg_repo) or "")))
    cases.append(("commit_message reads --trailer as a trailer line",
                  message(["-m", "s", "--trailer", "Triage: reach=local"]) == "s\n\nTriage: reach=local"
                  and message(["-m", "s", "--trailer", "Reviewed-by=Z"]) == "s\n\nReviewed-by: Z"))
    cases.append(("commit_message is None for -F - (stdin)", message(["-F", "-"]) is None))
    cases.append(("commit_message is None for an editor commit", message([]) is None and message(["-a"]) is None))
    cases.append(("commit_message is None for an unreadable -F file", message(["-F", "absent.txt"], msg_repo) is None))

    # --- in_project: the one home for "does this commit belong to this project" -------------------
    # `triage_commit_check` and `duplicate_commit_check` both gate on it; a second copy would drift.
    def in_project(cwd, root):
        return gc.in_project(cwd, root)

    own = make_repo()
    os.makedirs(os.path.join(own, "sub"))
    cases.append(("in_project: the project's own root is in the project", in_project(own, own) is True))
    cases.append(("in_project: a subdirectory of the project is in the project",
                  in_project(os.path.join(own, "sub"), own) is True))
    foreign = make_repo()
    cases.append(("in_project: another repo is not the project", in_project(foreign, own) is False))
    cases.append(("in_project: a directory that is no repo is not the project",
                  in_project(tempfile.mkdtemp(prefix="gcseam_norepo_"), own) is False))
    wt = os.path.join(own, "..", "gcseam_wt%s" % os.path.basename(own))
    git(own, "worktree", "add", "-q", os.path.abspath(wt))
    _WORKTREES.append((own, os.path.abspath(wt)))
    cases.append(("in_project: a linked worktree shares the project's common dir",
                  in_project(os.path.abspath(wt), own) is True))

    # --- repository-selecting globals: the commit targets the repo they name, not the caller's cwd --------
    own, foreign = own.replace("\\", "/"), foreign.replace("\\", "/")
    own_git, foreign_git = own + "/.git", foreign + "/.git"

    def targeted(command, base):
        found = gc.commit_invocations(command, base)
        return found[0] if found else None

    for label, command, base, expected in (
            ("--git-dir from an outside cwd", "git --git-dir=%s commit -m x" % own_git, foreign, True),
            ("--git-dir with a separate value token", "git --git-dir %s commit -m x" % own_git, foreign, True),
            ("a GIT_DIR prefix from an outside cwd", "GIT_DIR=%s git commit -m x" % own_git, foreign, True),
            ("a relative --git-dir under -C", "git -C %s --git-dir=.git commit -m x" % own, foreign, True),
            ("--git-dir naming a foreign repo from the project cwd",
             "git --git-dir=%s commit -m x" % foreign_git, own, False)):
        inv = targeted(command, base)
        got = inv is not None and gc.in_project(inv.cwd, own, inv.git_env)
        cases.append(("in_project follows %s (got %r)" % (label, got), got is expected))
    inv = targeted("git --git-dir=%s --work-tree=%s commit -m x" % (own_git, own), foreign)
    cases.append(("--work-tree travels in git_env beside --git-dir",
                  inv is not None and inv.git_env.get("GIT_WORK_TREE") == own
                  and inv.git_env.get("GIT_DIR") == own_git))

    # --- memoized: one dispatch reads one repo state, so a repeated read-only query runs once ---------
    spawned = []
    real_run = gc.subprocess.run

    def counting_run(argv, *a, **k):
        spawned.append(list(argv))
        return real_run(argv, *a, **k)

    def spawns(fn):
        spawned[:] = []
        gc.subprocess.run = counting_run
        try:
            fn()
        finally:
            gc.subprocess.run = real_run
        return len(spawned)

    memoized = getattr(gc, "memoized", None)
    if memoized is None:
        cases.append(("memoized: _git_commit offers a per-dispatch query cache", False))
    else:
        def twice_inside():
            with memoized():
                first = gc.run_git(["rev-parse", "HEAD"], own)
                second = gc.run_git(["rev-parse", "HEAD"], own)
                assert first and first == second
        cases.append(("memoized: a repeated read-only query inside the block runs git once", spawns(twice_inside) == 1))
        cases.append(("memoized: outside the block every query runs git",
                      spawns(lambda: [gc.run_git(["rev-parse", "HEAD"], own) for _ in range(2)]) == 2))

        def other_env():
            with memoized():
                gc.run_git(["rev-parse", "HEAD"], own)
                gc.run_git(["rev-parse", "HEAD"], own, {"GIT_INDEX_FILE": os.path.join(own, ".git", "index")})
        cases.append(("memoized: a query under another git environment runs again", spawns(other_env) == 2))

        def writes():
            with memoized():
                gc.run_git(["tag", "-f", "memo-probe"], own)
                gc.run_git(["tag", "-f", "memo-probe"], own)
        cases.append(("memoized: a command outside the read-only set always runs", spawns(writes) == 2))

        def options_first():
            with memoized():
                gc.run_git(["-c", "core.quotepath=off", "diff", "--name-only", "HEAD"], own)
                gc.run_git(["-c", "core.quotepath=off", "diff", "--name-only", "HEAD"], own)
        cases.append(("memoized: a read-only subcommand after -c options is still cached", spawns(options_first) == 1))

    mixed = make_repo()
    for name in ("b.txt", "c.txt"):
        with open(os.path.join(mixed, name), "w") as fh:
            fh.write("one\n")
    git(mixed, "add", "b.txt", "c.txt")
    git(mixed, "commit", "-q", "-m", "more")
    for name in ("a.txt", "b.txt"):
        with open(os.path.join(mixed, name), "a") as fh:
            fh.write("two\n")
    git(mixed, "add", "b.txt")
    include = ["-i", "--", "a.txt"]
    cases.append(("commit_source: -i with every other staged path equal to its working copy reads the worktree",
                  gc.commit_source(include, mixed) == "worktree"))
    with open(os.path.join(mixed, "b.txt"), "a") as fh:
        fh.write("three\n")
    cases.append(("commit_source: -i with another staged path that differs from its working copy is unreadable",
                  gc.commit_source(include, mixed) is None))
    cases.append(("commit_source: without a repo to read the legacy answer stands",
                  gc.commit_source(include) == "worktree"))
    cases.append(("commit_source: a commit with no -i is judged on its own rule beside the same dirty file",
                  gc.commit_source(["--", "a.txt"], mixed) == "worktree" and gc.commit_source(["-m", "x"], mixed) == "index"))
    with open(os.path.join(mixed, "spec.txt"), "w") as fh:
        fh.write("a.txt\n")
    cases.append(("index_sourced_paths: -i takes from the index only the paths its --pathspec-from-file does not name",
                  gc.index_sourced_paths(["-i", "--pathspec-from-file=spec.txt"], mixed, None, ["a.txt", "b.txt"]) == {"b.txt"}
                  and gc.index_sourced_paths(["-m", "x"], mixed, None, ["a.txt"]) == {"a.txt"}
                  and gc.index_sourced_paths(["--", "a.txt"], mixed, None, ["b.txt"]) == set()))

    reverted = make_repo()
    with open(os.path.join(reverted, "a.txt"), "a") as fh:
        fh.write("staged\n")
    git(reverted, "add", "a.txt")
    with open(os.path.join(reverted, "a.txt"), "w") as fh:
        fh.write("a\n")
    cases.append(("commit_source: -i whose only staged path is its pathspec path, reverted in the worktree, reads the worktree",
                  gc.commit_source(["-i", "--", "a.txt"], reverted) == "worktree"))

    unborn = tempfile.mkdtemp(prefix="gcseam_unborn_")
    _REPOS_TO_CLEAN.append(unborn)
    git(unborn, "init", "-q")
    os.makedirs(os.path.join(unborn, ".claude", "commands"))
    with open(os.path.join(unborn, ".claude", "commands", "x.md"), "w") as fh:
        fh.write("x\n")
    git(unborn, "add", "-A")
    cases.append(("a pathspec commit in a repo with no HEAD reads its staged path, not a failed diff",
                  gc.staged_paths(["-m", "x", "--", ".claude/commands/x.md"], unborn) == ({".claude/commands/x.md"}, None)))
    cases.append(("an index commit in a repo with no HEAD reads its staged path",
                  gc.staged_paths(["-m", "x"], unborn) == ({".claude/commands/x.md"}, None)))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


def main():
    try:
        return _run()
    finally:
        for repo, wt in _WORKTREES:
            subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)
        for repo in _REPOS_TO_CLEAN:
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
