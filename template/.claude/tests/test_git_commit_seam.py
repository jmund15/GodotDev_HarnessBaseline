#!/usr/bin/env python3
"""Proof for hooks/_git_commit.py, the one commit parser every commit guard imports. Each case plants a
bypass shape that used to slip past at least one guard.

    python -m pytest .claude/tests/test_git_commit_seam.py -q
"""
import os
import shlex
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import _git_commit as gc  # noqa: E402
import _git_run  # noqa: E402

CWD = "/work"


def git(repo, *args, env=None):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def write(repo, name, text, mode="w"):
    with open(os.path.join(repo, name), mode, encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    """One repo with a.txt committed; every test copies it instead of running five git commands."""
    repo = str(tmp_path_factory.mktemp("gcseam_seed"))
    git(repo, "init", "-q", "--template=")  # no sample hooks: the per-test copy stays a handful of files
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    write(repo, "a.txt", "a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


@pytest.fixture
def make_repo(seed, tmp_path):
    made = []

    def make():
        dest = str(tmp_path / ("repo%d" % len(made)))
        made.append(dest)
        shutil.copytree(seed, dest)
        return dest
    return make


def invocations(command, cwd=CWD):
    return gc.commit_invocations(command, cwd)


def only(command, cwd=CWD):
    found = invocations(command, cwd)
    assert len(found) == 1, found
    return found[0]


# --- detection ---------------------------------------------------------------

def test_a_git_commit_inside_a_heredoc_body_is_not_an_invocation():
    assert invocations("cat > m <<'EOF'\ngit commit -am x\nEOF\ngit status") == []


def test_a_real_commit_after_and_and_is_found_with_its_rest():
    call = only("git add -A && git commit -F msg -- .claude")
    assert call.sub == "commit" and call.rest == ["-F", "msg", "--", ".claude"]


def test_dash_C_retargets_the_repo():
    assert only('git -C "/repo/wt" commit -m x').cwd.replace("\\", "/").endswith("/repo/wt")


def test_a_preceding_cd_retargets_the_repo():
    assert only("cd /elsewhere && git commit -m x").cwd.replace("\\", "/").endswith("/elsewhere")


def test_an_inline_env_prefix_is_captured():
    assert only("HARNESS_ALLOW_X=1 git commit -m x").inline_env.get("HARNESS_ALLOW_X") == "1"


def test_merge_and_cherry_pick_are_commit_like():
    assert [f.sub for f in invocations("git merge feature && git cherry-pick abc123")] == ["merge", "cherry-pick"]


def test_2_and_1_is_a_redirect_not_a_separator_or_a_pathspec():
    call = only("GIT_INDEX_FILE=i git commit -F msg 2>&1 | tail -4")
    assert call.rest == ["-F", "msg"] and call.git_env.get("GIT_INDEX_FILE") == "i"


@pytest.mark.parametrize("shape", ["> out.log", ">out.log", "2>/dev/null", "&> log", ">> log 2>&1", "< /dev/null"])
def test_a_redirect_is_not_a_pathspec(shape):
    assert only("git commit -m x %s" % shape).rest == ["-m", "x"]


def test_a_heredoc_operator_is_not_a_pathspec():
    assert only("git commit -F - <<'EOF'\nmsg\nEOF").rest == ["-F", "-"]


def test_a_quoted_greater_than_stays_in_its_argument():
    assert only('git commit -m "a > b" -- p').rest == ["-m", "a > b", "--", "p"]


def test_a_bare_ampersand_still_separates_commands():
    assert only("git status & git commit -m x").rest == ["-m", "x"]


def test_a_quoted_mention_inside_another_git_command_is_not_a_commit():
    assert invocations('git log --grep="git commit -am"') == []


# Live misfire 2026-09-14: a quoted `|` split `rg -c 'git stash|git checkout --|git restore'` into a
# `git checkout --` segment, and git_guardrails denied a read-only search.
@pytest.mark.parametrize("command,want", [
    ("rg -c 'git stash|git checkout --|git restore' f.txt", ["rg -c 'git stash|git checkout --|git restore' f.txt"]),
    ('grep "a && git reset --hard" f && git status', ['grep "a && git reset --hard" f', "git status"]),
    ("echo a | git checkout -- x", ["echo a", "git checkout -- x"]),
    ("echo 'a | git checkout -- x", ["echo 'a", "git checkout -- x"]),
], ids=["single-quoted-separators", "double-quoted-separators", "unquoted-pipe-splits", "unbalanced-quote-splits-all"])
def test_segments_split_on_unquoted_separators_only(command, want):
    assert list(gc.segments(command)) == want


def test_a_backslash_newline_continues_the_commit_line_so_every_pathspec_after_it_is_read():
    rests = [f.rest for f in invocations("git commit -F m -- a \\\n  b")]
    assert rests == [["-F", "m", "--", "a", "b"]]


LAUNCHERS = ("bash -c", "sh -c", "bash -lc", "bash -o pipefail -c", "eval")


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_a_launcher_finds_the_commit_and_its_body_cwd_and_env(launcher):
    body = "cd child; export GIT_INDEX_FILE=alt.index; git -C deeper commit -m x"
    call = only(launcher + " " + shlex.quote(body))
    assert call.cwd == gc.resolve_cd(gc.resolve_cd(CWD, "child"), "deeper")
    assert call.git_env == {"GIT_INDEX_FILE": "alt.index"}


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_a_launcher_with_a_read_only_body_commits_nothing(launcher):
    assert invocations(launcher + " " + shlex.quote("git status --short")) == []


def test_env_launcher_cwd_and_unset_select_the_body_context():
    call = only("export GIT_INDEX_FILE=outer; env -u GIT_INDEX_FILE -C child bash -c 'git commit -m x'")
    assert call.cwd == gc.resolve_cd(CWD, "child") and call.git_env == {"GIT_INDEX_FILE": None}


def test_a_non_exported_variable_is_not_inherited_by_a_shell():
    assert only("IDX=outer; bash -c 'GIT_INDEX_FILE=$IDX git commit -m x'").git_env == {"GIT_INDEX_FILE": "$IDX"}


def test_a_shell_body_inherits_exported_git_env():
    assert only("export GIT_INDEX_FILE=outer; bash -c 'git commit -m x'").git_env == {"GIT_INDEX_FILE": "outer"}


def test_a_shell_inline_env_does_not_leak_back_to_its_parent():
    inner, outer = invocations("GIT_INDEX_FILE=inner bash -c 'git commit -m x'; git commit -m y")
    assert inner.git_env == {"GIT_INDEX_FILE": "inner"} and outer.git_env == {}


def test_a_child_shell_cwd_and_export_do_not_leak_back():
    call = only("bash -c 'cd child; export GIT_INDEX_FILE=inner'; git commit -m x")
    assert call.cwd == CWD and call.git_env == {}


def test_eval_cwd_and_export_changes_persist_in_its_parent():
    call = only("eval 'cd child; export GIT_INDEX_FILE=inner'; git commit -m x")
    assert call.cwd == gc.resolve_cd(CWD, "child") and call.git_env == {"GIT_INDEX_FILE": "inner"}


def test_a_bash_n_body_executes_no_commit():
    assert invocations("bash -nc 'git commit -m x'") == []


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_a_launcher_folds_a_tracked_add_inside_its_body(launcher, make_repo):
    repo = make_repo()
    command = launcher + " " + shlex.quote("git add a.txt && git commit -m x")
    folded = gc.fold_tracked_adds(command, repo)
    assert folded is not None
    calls = invocations(folded, repo)
    assert len(calls) == 1 and calls[0].rest == ["-m", "x", "--", "a.txt"]


def test_folding_does_not_move_an_outer_double_quoted_expansion_into_a_child_shell(make_repo):
    assert gc.fold_tracked_adds('bash -c "git add a.txt && git commit -m $MESSAGE"', make_repo()) is None


def test_a_known_outer_double_quoted_expansion_resolves_before_launching_the_shell():
    assert only('IDX=outer; bash -c "GIT_INDEX_FILE=$IDX git commit -m x"').git_env == {"GIT_INDEX_FILE": "outer"}


# --- rewrite coordinates -----------------------------------------------------

def test_a_direct_invocation_carries_its_outer_source_offset_and_an_empty_body_path():
    command = "echo before; git -c core.editor=x commit -m direct"
    call = only(command)
    assert call.subcommand_end == command.index("commit") + len("commit") and call.body_path == ()


def test_a_nested_invocation_carries_the_enclosing_source_word_spans_and_quote_styles():
    command = 'bash -c "sh -c \'git commit -m nested\'"; git commit -m direct'
    nested, direct = invocations(command)
    dq_end = command.index('"', command.index('"') + 1) + 1
    assert nested.subcommand_end == command.index("commit") + len("commit")
    assert [(w.start, w.end, w.quote) for w in nested.body_path] == [
        (command.index('"'), dq_end, '"'),
        (command.index("'"), command.index("'", command.index("'") + 1) + 1, "'")]
    assert direct.subcommand_end == command.rindex("commit") + len("commit") and direct.body_path == ()


def test_an_unsafe_body_has_no_rewrite_coordinates_and_the_later_direct_offset_survives():
    command = "bash -c 'git com'\"mit -m nested\"; git commit -m direct"
    nested, direct = invocations(command)
    assert nested.subcommand_end is None and nested.body_path is None
    assert direct.subcommand_end == command.rindex("commit") + len("commit")


def test_rewrite_metadata_defaults_preserve_legacy_construction_and_equality():
    direct = invocations("bash -c 'git com'\"mit -m nested\"; git commit -m direct")[1]
    legacy = gc.CommitInvocation(direct.sub, direct.rest, direct.cwd, direct.inline_env,
                                 direct.git_env, direct.assignments)
    assert legacy.subcommand_end is None and legacy.body_path is None and legacy == direct


# --- parse_commit_args -------------------------------------------------------

@pytest.mark.parametrize("args,want", [
    (["-am", "x"], (True, False, [])),
    (["--amend", "--no-edit"], (False, True, [])),
    (["-F", "m", "--", "a", "b"], (False, False, ["a", "b"])),
], ids=["dash-am-is-a-plus-m", "amend", "pathspec-after-double-dash"])
def test_parse_commit_args(args, want):
    assert gc.parse_commit_args(args) == want


# --- staged_paths / incoming_paths against a real repo -----------------------

@pytest.fixture
def dirty(make_repo):
    repo = make_repo()
    write(repo, "a.txt", "dirty\n", "a")
    return repo


def test_an_empty_index_and_a_plain_commit_publish_no_paths(dirty):
    assert gc.staged_paths([], dirty) == (set(), None)


def test_dash_a_includes_the_dirty_tracked_file(dirty):
    assert gc.staged_paths(["-a", "-m", "x"], dirty)[0] == {"a.txt"}


def test_amend_republishes_heads_paths(dirty):
    assert "a.txt" in gc.staged_paths(["--amend", "--no-edit"], dirty)[0]


def test_a_git_failure_reports_the_failing_subcommand(tmp_path):
    paths, err = gc.staged_paths([], str(tmp_path))
    assert paths is None and "diff --cached" in err


@pytest.fixture
def staged_elsewhere(dirty):
    write(dirty, "b.txt", "staged elsewhere\n")
    git(dirty, "add", "b.txt")
    return dirty


def test_a_pathspec_only_commit_publishes_only_its_pathspec(staged_elsewhere):
    assert gc.staged_paths(["-m", "x", "--", "a.txt"], staged_elsewhere)[0] == {"a.txt"}


def test_dash_i_adds_the_index_to_the_pathspec(staged_elsewhere):
    assert gc.staged_paths(["-i", "-m", "x", "--", "a.txt"], staged_elsewhere)[0] == {"a.txt", "b.txt"}


def test_pathspec_from_file_is_read_as_the_pathspec(staged_elsewhere):
    write(staged_elsewhere, "spec.txt", "a.txt\n")
    assert gc.staged_paths(["-m", "x", "--pathspec-from-file=spec.txt"], staged_elsewhere)[0] == {"a.txt"}


def test_pathspec_from_file_dash_cannot_be_read_so_it_fails(staged_elsewhere):
    paths, err = gc.staged_paths(["-m", "x", "--pathspec-from-file=-"], staged_elsewhere)
    assert paths is None and "pathspec-from-file" in err


# S4: staged_paths' pathspec scope -- fixes the known defect where another session's staged files block
# or stale a pathspec commit. This seam judges the committed set, not .claude/ scope; the guard judges scope.
@pytest.fixture
def two_staged(make_repo):
    repo = make_repo()
    for name in ("other.txt", "mine.txt"):
        write(repo, name, name + "\n")
        git(repo, "add", name)
    return repo


def test_a_pathspec_commit_excludes_another_staged_file(two_staged):
    assert gc.staged_paths(["-m", "x", "--", "mine.txt"], two_staged)[0] == {"mine.txt"}


@pytest.mark.parametrize("flag", ["-i", "--include"])
def test_dash_i_and_include_keep_the_whole_index_despite_the_pathspec(flag, two_staged):
    assert gc.staged_paths([flag, "-m", "x", "--", "mine.txt"], two_staged)[0] == {"mine.txt", "other.txt"}


@pytest.fixture(scope="module")
def feature_repo(seed, tmp_path_factory):
    """Read-only for its tests (incoming_paths only reads), so one copy serves them all."""
    repo = str(tmp_path_factory.mktemp("gcseam_feature") / "repo")
    shutil.copytree(seed, repo)
    git(repo, "checkout", "-q", "-b", "feature")
    os.makedirs(os.path.join(repo, ".claude", "hooks"))
    write(repo, os.path.join(".claude", "hooks", "h.py"), "h = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature hook")
    git(repo, "checkout", "-q", "-")
    return repo


def test_merge_branch_reports_the_branchs_changed_paths(feature_repo):
    assert ".claude/hooks/h.py" in gc.incoming_paths("merge", ["feature"], feature_repo)[0]


def test_merge_no_commit_reports_nothing_because_the_later_commit_is_judged(feature_repo):
    assert gc.incoming_paths("merge", ["--no-commit", "feature"], feature_repo)[0] == set()


def test_cherry_pick_reports_the_revs_paths(feature_repo):
    assert ".claude/hooks/h.py" in gc.incoming_paths("cherry-pick", ["feature"], feature_repo)[0]


@pytest.mark.parametrize("flags", [["-F", "msg.txt"], ["--file", "msg.txt"], ["-m", "a message"], ["-s", "ort"],
                                   ["--strategy", "ort"], ["-X", "theirs"], ["--strategy-option", "theirs"]],
                         ids=lambda flags: flags[0])
def test_merge_reads_the_branch_not_an_options_value(flags, feature_repo):
    paths, failed = gc.incoming_paths("merge", flags + ["feature"], feature_repo)
    assert failed is None and ".claude/hooks/h.py" in paths


def test_revert_reads_the_rev_not_the_mainline_number(feature_repo):
    paths, failed = gc.incoming_paths("revert", ["-m", "1", "feature"], feature_repo)
    assert failed is None and ".claude/hooks/h.py" in paths


# --- bypass_declared ---------------------------------------------------------

def test_bypass_declared_reads_the_inline_var_the_hook_env_and_neither(monkeypatch):
    monkeypatch.delenv("HARNESS_X_TEST", raising=False)
    assert gc.bypass_declared({"HARNESS_X_TEST": "1"}, "HARNESS_X_TEST")
    assert not gc.bypass_declared({}, "HARNESS_X_TEST")
    monkeypatch.setenv("HARNESS_X_TEST", "1")
    assert gc.bypass_declared({}, "HARNESS_X_TEST")


# --- the command's GIT_* environment -----------------------------------------
# A commit through another index (`GIT_INDEX_FILE=<f> git commit`, or an earlier `export`) publishes THAT index.
# Guards that read the default index judged content the commit never had.

@pytest.mark.parametrize("command,want", [
    ("export GIT_INDEX_FILE=/tmp/alt.index && git commit -m x", {"GIT_INDEX_FILE": "/tmp/alt.index"}),
    ("GIT_INDEX_FILE=/tmp/alt.index git commit -m x", {"GIT_INDEX_FILE": "/tmp/alt.index"}),
    ("export GIT_INDEX_FILE=/tmp/alt.index; unset GIT_INDEX_FILE; git commit -m x", {"GIT_INDEX_FILE": None}),
    ('IDX=/tmp/alt.index && GIT_INDEX_FILE="$IDX" git commit -m x', {"GIT_INDEX_FILE": "/tmp/alt.index"}),
], ids=["export", "inline", "unset-clears", "once-assigned-literal-variable-expands"])
def test_the_commands_git_index_reaches_the_commit(command, want):
    assert only(command).git_env == want


def test_a_non_git_export_is_neither_git_env_nor_an_inline_bypass():
    call = only("export HARNESS_ALLOW_X=1 && git commit -m x")
    assert call.git_env == {} and call.inline_env == {}


@pytest.fixture
def alt_repo(make_repo):
    repo = make_repo()
    alt_index = os.path.join(repo, "alt.index")
    env = dict(os.environ, GIT_INDEX_FILE=alt_index)
    git(repo, "read-tree", "HEAD", env=env)
    write(repo, "alt_only.txt", "alt\n")
    git(repo, "add", "alt_only.txt", env=env)
    return repo, alt_index


def test_staged_paths_reads_the_index_the_command_names(alt_repo):
    repo, alt_index = alt_repo
    assert gc.staged_paths([], repo, env={"GIT_INDEX_FILE": alt_index})[0] == {"alt_only.txt"}


def test_staged_paths_without_it_still_reads_the_default_index(alt_repo):
    assert gc.staged_paths([], alt_repo[0])[0] == set()


def test_an_unexpandable_index_fails_instead_of_reading_an_empty_index(alt_repo):
    paths, problem = gc.staged_paths([], alt_repo[0], env={"GIT_INDEX_FILE": "$IDX"})
    assert paths is None and "not a literal path" in (problem or "")


def test_a_missing_index_fails_instead_of_reading_an_empty_index(alt_repo):
    repo = alt_repo[0]
    paths, problem = gc.staged_paths([], repo, env={"GIT_INDEX_FILE": os.path.join(repo, "none.index")})
    assert paths is None and "does not exist" in (problem or "")


# --- commit_message: the message a commit carries, read through the same parser ---------------

@pytest.mark.parametrize("rest,want", [
    (["-m", "subject", "-m", "Triage: reach=local"], "subject\n\nTriage: reach=local"),
    (["--message=one"], "one"),
    (["-mtwo"], "two"),
    (["-am", "combined"], "combined"),
    (["-m", "s", "--trailer", "Triage: reach=local"], "s\n\nTriage: reach=local"),
    (["-m", "s", "--trailer", "Reviewed-by=Z"], "s\n\nReviewed-by: Z"),
    (["-F", "-"], None),
    ([], None),
    (["-a"], None),
], ids=["repeated-m-joins-paragraphs", "message-equals", "attached-m", "combined-am", "trailer-line",
        "trailer-equals", "stdin-is-none", "editor-commit-is-none", "dash-a-editor-is-none"])
def test_commit_message(rest, want):
    assert gc.commit_message(rest, CWD) == want


def test_commit_message_reads_dash_F_relative_to_the_commits_cwd(tmp_path):
    write(str(tmp_path), "msg.txt", "from file\n\nTriage: reach=shared\n")
    assert (gc.commit_message(["-F", "msg.txt"], str(tmp_path)) or "").startswith("from file")
    assert "Triage: reach=shared" in (gc.commit_message(["--file=msg.txt"], str(tmp_path)) or "")


def test_commit_message_is_none_for_an_unreadable_dash_F_file(tmp_path):
    assert gc.commit_message(["-F", "absent.txt"], str(tmp_path)) is None


# --- in_project: the one home for "does this commit belong to this project" ------------------
# `triage_commit_check` and `duplicate_commit_check` both gate on it; a second copy would drift.

def test_the_projects_own_root_and_a_subdirectory_are_in_the_project(make_repo):
    own = make_repo()
    os.makedirs(os.path.join(own, "sub"))
    assert gc.in_project(own, own) is True and gc.in_project(os.path.join(own, "sub"), own) is True


def test_another_repo_and_a_non_repo_directory_are_not_the_project(make_repo, tmp_path):
    own = make_repo()
    plain = tmp_path / "norepo"
    plain.mkdir()
    assert gc.in_project(make_repo(), own) is False and gc.in_project(str(plain), own) is False


def test_a_linked_worktree_shares_the_projects_common_dir(make_repo, tmp_path):
    own = make_repo()
    wt = str(tmp_path / "gcseam_wt")
    git(own, "worktree", "add", "-q", wt)
    assert gc.in_project(wt, own) is True


# --- repository-selecting globals: the commit targets the repo they name, not the caller's cwd ---

@pytest.mark.parametrize("command,from_own,expected", [
    ("git --git-dir={own_git} commit -m x", False, True),
    ("git --git-dir {own_git} commit -m x", False, True),
    ("GIT_DIR={own_git} git commit -m x", False, True),
    ("git -C {own} --git-dir=.git commit -m x", False, True),
    ("git --git-dir={foreign_git} commit -m x", True, False),
], ids=["git-dir-from-outside", "git-dir-separate-value-token", "GIT_DIR-prefix-from-outside",
        "relative-git-dir-under-C", "git-dir-naming-a-foreign-repo-from-the-project"])
def test_in_project_follows_the_repository_selecting_global(command, from_own, expected, make_repo):
    own, foreign = make_repo().replace("\\", "/"), make_repo().replace("\\", "/")
    command = command.format(own=own, own_git=own + "/.git", foreign_git=foreign + "/.git")
    inv = only(command, own if from_own else foreign)
    assert gc.in_project(inv.cwd, own, inv.git_env) is expected


def test_work_tree_travels_in_git_env_beside_git_dir(make_repo):
    own, foreign = make_repo().replace("\\", "/"), make_repo().replace("\\", "/")
    inv = only("git --git-dir=%s/.git --work-tree=%s commit -m x" % (own, own), foreign)
    assert inv.git_env.get("GIT_WORK_TREE") == own and inv.git_env.get("GIT_DIR") == own + "/.git"


# --- memoized: one dispatch reads one repo state, so a repeated read-only query runs once ---------

@pytest.fixture
def spawns(monkeypatch):
    """`spawns(fn)`: how many git processes `fn` started."""
    spawned = []
    real_run = _git_run.subprocess.run

    def counting_run(argv, *a, **k):
        spawned.append(list(argv))
        return real_run(argv, *a, **k)

    def count(fn):
        with monkeypatch.context() as patch:
            patch.setattr(_git_run.subprocess, "run", counting_run)
            fn()
        return len(spawned)
    return count


def test_memoized_a_repeated_read_only_query_inside_the_block_runs_git_once(make_repo, spawns):
    own = make_repo()

    def twice_inside():
        with gc.memoized():
            first = gc.run_git(["rev-parse", "HEAD"], own)
            assert first and first == gc.run_git(["rev-parse", "HEAD"], own)
    assert spawns(twice_inside) == 1


def test_memoized_outside_the_block_every_query_runs_git(make_repo, spawns):
    own = make_repo()
    assert spawns(lambda: [gc.run_git(["rev-parse", "HEAD"], own) for _ in range(2)]) == 2


def test_memoized_a_query_under_another_git_environment_runs_again(make_repo, spawns):
    own = make_repo()

    def other_env():
        with gc.memoized():
            gc.run_git(["rev-parse", "HEAD"], own)
            gc.run_git(["rev-parse", "HEAD"], own, {"GIT_INDEX_FILE": os.path.join(own, ".git", "index")})
    assert spawns(other_env) == 2


def test_memoized_a_command_outside_the_read_only_set_always_runs(make_repo, spawns):
    own = make_repo()

    def writes():
        with gc.memoized():
            gc.run_git(["tag", "-f", "memo-probe"], own)
            gc.run_git(["tag", "-f", "memo-probe"], own)
    assert spawns(writes) == 2


def test_memoized_a_read_only_subcommand_after_dash_c_options_is_still_cached(make_repo, spawns):
    own = make_repo()

    def options_first():
        with gc.memoized():
            for _ in range(2):
                gc.run_git(["-c", "core.quotepath=off", "diff", "--name-only", "HEAD"], own)
    assert spawns(options_first) == 1


# --- commit_source / index_sourced_paths -------------------------------------

@pytest.fixture
def mixed(make_repo):
    repo = make_repo()
    for name in ("b.txt", "c.txt"):
        write(repo, name, "one\n")
    git(repo, "add", "b.txt", "c.txt")
    git(repo, "commit", "-q", "-m", "more")
    for name in ("a.txt", "b.txt"):
        write(repo, name, "two\n", "a")
    git(repo, "add", "b.txt")
    return repo


def test_commit_source_dash_i_with_every_other_staged_path_equal_to_its_working_copy_reads_the_worktree(mixed):
    assert gc.commit_source(["-i", "--", "a.txt"], mixed) == "worktree"


def test_commit_source_dash_i_with_another_staged_path_that_differs_from_its_working_copy_is_unreadable(mixed):
    write(mixed, "b.txt", "three\n", "a")
    assert gc.commit_source(["-i", "--", "a.txt"], mixed) is None


def test_commit_source_without_a_repo_the_legacy_answer_stands():
    assert gc.commit_source(["-i", "--", "a.txt"]) == "worktree"


def test_commit_source_a_commit_with_no_dash_i_is_judged_on_its_own_rule_beside_the_same_dirty_file(mixed):
    assert gc.commit_source(["--", "a.txt"], mixed) == "worktree" and gc.commit_source(["-m", "x"], mixed) == "index"


def test_index_sourced_paths_dash_i_takes_from_the_index_only_the_paths_its_pathspec_file_does_not_name(mixed):
    write(mixed, "spec.txt", "a.txt\n")
    assert gc.index_sourced_paths(["-i", "--pathspec-from-file=spec.txt"], mixed, None, ["a.txt", "b.txt"]) == {"b.txt"}
    assert gc.index_sourced_paths(["-m", "x"], mixed, None, ["a.txt"]) == {"a.txt"}
    assert gc.index_sourced_paths(["--", "a.txt"], mixed, None, ["b.txt"]) == set()


def test_commit_source_dash_i_whose_only_staged_path_is_its_pathspec_path_reverted_in_the_worktree_reads_the_worktree(
        make_repo):
    repo = make_repo()
    write(repo, "a.txt", "staged\n", "a")
    git(repo, "add", "a.txt")
    write(repo, "a.txt", "a\n")
    assert gc.commit_source(["-i", "--", "a.txt"], repo) == "worktree"


@pytest.fixture
def unborn(tmp_path):
    repo = str(tmp_path / "unborn")
    os.makedirs(os.path.join(repo, ".claude", "commands"))
    git(repo, "init", "-q")
    write(repo, os.path.join(".claude", "commands", "x.md"), "x\n")
    git(repo, "add", "-A")
    return repo


def test_a_pathspec_commit_in_a_repo_with_no_head_reads_its_staged_path_not_a_failed_diff(unborn):
    assert gc.staged_paths(["-m", "x", "--", ".claude/commands/x.md"], unborn) == ({".claude/commands/x.md"}, None)


def test_an_index_commit_in_a_repo_with_no_head_reads_its_staged_path(unborn):
    assert gc.staged_paths(["-m", "x"], unborn) == ({".claude/commands/x.md"}, None)
