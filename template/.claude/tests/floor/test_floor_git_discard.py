"""Git discards and history rewrites: the loss cases of v1's `test_git_guardrails_advice.py` as rows.

PushinPotions origin/main sections 0-3, plus MaddManager main's stash-drop rows (its guard admits a drop whose
changes a ref tip already holds, `82413b2c`). Sections 4-5 of the PushinPotions file assert advisories and the
proof-required block, which V1's Lost column retires; `test_floor_ported.md` lists them.

A row is (fixture, command, expected kind, a token the deny names or None). Fixtures are read-only, so each is
built once per module.
"""
import os

import pytest

from case import bash
from conftest import git, write

CITE = "the owner's explicit go-ahead first"
LOSS_CITE = "an unsaved edit would be lost"


def _repo(factory, name):
    path = factory.mktemp(name)
    git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    return path


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    out = {}
    # f.txt carries an uncommitted edit; clean.txt, crlf.txt (CRLF-only rewrite) committed; staged.txt staged.
    repo = _repo(tmp_path_factory, "advice")
    for name in ("f.txt", "clean.txt", "crlf.txt", "staged.txt"):
        write(repo, name, "one\ntwo\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, "f.txt", "one\nmine\n")
    (repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    write(repo, "staged.txt", "one\nstaged\n")
    git(repo, "add", "staged.txt")
    out["repo"] = repo

    merge = _repo(tmp_path_factory, "merge")
    for name in ("a.json", "a.tscn"):
        write(merge, name, "base\n")
    git(merge, "add", ".")
    git(merge, "commit", "-q", "-m", "base")
    git(merge, "checkout", "-q", "-b", "other")
    for name in ("a.json", "a.tscn"):
        write(merge, name, "theirs\n")
    git(merge, "commit", "-q", "-am", "theirs")
    git(merge, "checkout", "-q", "-")
    for name in ("a.json", "a.tscn"):
        write(merge, name, "ours\n")
    git(merge, "commit", "-q", "-am", "ours")
    git(merge, "merge", "-q", "other", check=False)
    out["merge"] = merge

    ref = _repo(tmp_path_factory, "ref")
    for name in ("a.txt", "d.txt", "s.txt", "gone.txt"):
        write(ref, name, "c1\n")
    git(ref, "add", ".")
    git(ref, "commit", "-q", "-m", "c1")
    git(ref, "tag", "c1")
    for name in ("a.txt", "d.txt", "s.txt", "n.txt", "new.txt", "gone.txt"):
        write(ref, name, "c2\n")
    git(ref, "add", ".")
    git(ref, "commit", "-q", "-m", "c2")
    git(ref, "tag", "c2")
    git(ref, "checkout", "-q", "c1")
    write(ref, "d.txt", "mine\n")
    write(ref, "s.txt", "staged\n")
    git(ref, "add", "s.txt")
    write(ref, "n.txt", "local\n")
    os.remove(ref / "gone.txt")
    out["ref"] = ref

    stashed = _repo(tmp_path_factory, "stashed")
    write(stashed, "a.txt", "base\n")
    git(stashed, "add", "a.txt")
    git(stashed, "commit", "-q", "-m", "base")
    write(stashed, "a.txt", "kept\n")
    git(stashed, "stash", "-q")
    write(stashed, "a.txt", "loose\n")
    git(stashed, "stash", "-q")
    git(stashed, "update-ref", "refs/preserve/kept", git(stashed, "rev-parse", "stash@{1}").strip())
    out["stashed"] = stashed

    cache = _repo(tmp_path_factory, "pyc")
    for name in ("f.txt", "clean.txt"):
        write(cache, name, "one\ntwo\n")
    write(cache, "pkg/__pycache__/m.cpython-314.pyc", "old\n")
    write(cache, "pkg/m.cpython-314.pyc", "old\n")
    git(cache, "add", "-f", ".")
    git(cache, "commit", "-q", "-m", "tracked caches")
    write(cache, "f.txt", "one\nmine\n")
    write(cache, "pkg/__pycache__/m.cpython-314.pyc", "new\n")
    write(cache, "pkg/m.cpython-314.pyc", "new\n")
    out["pyc"] = cache

    saved = _repo(tmp_path_factory, "saved")
    for name in ("f.txt", "clean.txt"):
        write(saved, name, "one\ntwo\n")
    write(saved, "g.txt", "two\nmore\n")
    write(saved, "sub/x.txt", "one\n")
    git(saved, "add", ".")
    git(saved, "commit", "-q", "-m", "base")
    write(saved, "f.txt", "one\nmine\n")
    write(saved, "g.txt", "two\nlocal g\n")
    write(saved, "sub/x.txt", "edited\n")
    git(saved, "hash-object", "-w", "f.txt")
    out["saved"] = saved
    saved2 = _repo(tmp_path_factory, "saved2")
    for name in ("f.txt", "g.txt"):
        write(saved2, name, "one\n")
    git(saved2, "add", ".")
    git(saved2, "commit", "-q", "-m", "base")
    write(saved2, "f.txt", "mine f\n")
    write(saved2, "g.txt", "mine g\n")
    git(saved2, "hash-object", "-w", "f.txt")
    git(saved2, "hash-object", "-w", "g.txt")
    out["saved2"] = saved2
    return out


def _squashed(factory, name):
    """main carries a cherry-pick of `sq`'s one commit; `uniq` holds a commit no other ref has; `other` sits at the
    base; origin/HEAD names a ref carrying the cherry-pick."""
    repo = factory.mktemp(name)
    git(repo, "init", "-q", "-b", "main")
    write(repo, "a.txt", "base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "branch", "other")
    for branch, file in (("sq", "sq.txt"), ("uniq", "u.txt")):
        git(repo, "checkout", "-q", "-b", branch, "main")
        write(repo, file, branch + "\n")
        git(repo, "add", file)
        git(repo, "commit", "-q", "-m", branch)
    git(repo, "checkout", "-q", "main")
    git(repo, "cherry-pick", "sq")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return repo


ROWS = [
    # sec0: discards of a real edit, and their citations
    ("repo", "git checkout -- f.txt", "deny", "refs/preserve/"),
    ("repo", "git restore f.txt", "deny", "refs/preserve/"),
    ("repo", "git reset --hard", "deny", "git reset --keep"),
    ("repo", "git stash drop", "deny", "refs/preserve/"),
    ("repo", "git push --force", "deny", "rebase"),
    ("repo", "git clean -f", "deny", "-n"),
    ("repo", "git gc --prune=now", "deny", "git gc"),
    ("repo", "git stash clear", "deny", CITE),
    ("repo", "git reflog expire --expire=now --all", "deny", CITE),
    ("repo", "git filter-branch -- --all", "deny", CITE),
    ("repo", "git restore --staged f.txt", "allow", None),
    ("repo", "git checkout -- clean.txt", "allow", None),
    ("repo", "git checkout HEAD -- clean.txt", "allow", None),
    ("repo", "git restore clean.txt", "allow", None),
    ("repo", "git checkout -- crlf.txt", "allow", None),
    ("repo", "git checkout -- clean.txt crlf.txt", "allow", None),
    ("repo", "git checkout -- clean.txt f.txt", "deny", None),
    ("repo", "git checkout -- .", "deny", None),
    ("repo", "git checkout .", "deny", None),
    ("repo", "git restore .", "deny", None),
    ("repo", "git checkout -- '*.txt'", "deny", None),
    ("repo", "git restore --staged --worktree staged.txt", "deny", None),
    ("repo", "git restore -SW staged.txt", "deny", None),
    ("repo", "git restore -s HEAD staged.txt", "deny", None),
    ("repo", "git checkout -f -- f.txt", "deny", None),
    ("repo", "git checkout --theirs -- f.txt", "deny", None),
    ("repo", "git status", "allow", None),
    ("merge", "git checkout --theirs -- a.json", "allow", None),
    ("merge", "git checkout --ours -- a.json", "allow", None),
    ("merge", "git checkout --theirs -- a.tscn", "deny", None),
    ("ref", "git checkout c2 -- a.txt", "allow", None),
    ("ref", "git checkout c2 -- new.txt", "allow", None),
    ("ref", "git checkout c2 -- d.txt", "deny", None),
    ("ref", "git checkout c2 -- s.txt", "deny", None),
    ("ref", "git checkout c2 -- n.txt", "deny", None),
    ("ref", "git checkout c2 -- gone.txt", "deny", None),
    ("ref", "git checkout c2 -- a.txt d.txt", "deny", None),
    # sec1: stash drops, bytecode caches
    ("stashed", "git stash drop stash@{1}", "allow", None),
    ("stashed", "git stash drop 'stash@{1}'", "allow", None),
    ("stashed", "git stash drop -q stash@{1}", "allow", None),
    ("stashed", "git stash drop --quiet stash@{0}", "deny", None),
    ("stashed", "git stash drop --index stash@{1}", "deny", None),
    ("stashed", "git stash drop stash@{0}", "deny", "stash@{0}"),
    ("stashed", "git stash drop", "deny", None),
    ("stashed", "git stash drop stash@{9}", "deny", None),
    ("stashed", "git stash clear", "deny", None),
    ("pyc", "git checkout -- pkg/__pycache__/m.cpython-314.pyc", "allow", None),
    ("pyc", "git checkout -- pkg/__pycache__", "allow", None),
    ("pyc", "git restore pkg/__pycache__/m.cpython-314.pyc", "allow", None),
    ("pyc", "git restore pkg/__pycache__", "allow", None),
    ("pyc", "git checkout HEAD -- pkg/__pycache__", "allow", None),
    ("pyc", "git checkout -- pkg/__pycache__ f.txt", "deny", None),
    ("pyc", "git checkout -- pkg/m.cpython-314.pyc", "deny", None),
    ("pyc", "git checkout -- pkg", "deny", None),
    ("pyc", "git checkout -- pkg/__pycache__/../../f.txt", "deny", None),
    ("pyc", "git restore ':!pkg/__pycache__'", "deny", None),
    ("pyc", "git checkout -- ':(exclude)pkg/__pycache__'", "deny", None),
    # sec2: edits git already stores, and the retry step each shape names
    ("saved", "git restore f.txt", "allow", None),
    ("saved", "git checkout -- f.txt", "allow", None),
    ("saved", "git restore f.txt g.txt", "deny", None),
    ("saved", "git checkout -- f.txt g.txt", "deny", None),
    ("saved", "git restore g.txt", "deny", None),
    ("saved2", "git restore f.txt g.txt", "allow", None),
    ("saved", "git checkout HEAD -- f.txt", "deny", "git restore --source=HEAD -- "),
    ("saved", "git restore --source=HEAD -- f.txt", "allow", None),
    ("saved", "git checkout -- .", "deny", "name each file"),
    ("saved", "git checkout .", "deny", "name each file"),
    ("saved", "git checkout -- '*.txt'", "deny", "name each file"),
    ("saved", "git checkout -- sub", "deny", "name each file"),
    ("saved", "git restore .", "deny", "name each file"),
    ("saved", "git restore sub", "deny", "name each file"),
    ("saved", "git restore -W f.txt", "deny", "--worktree"),
    ("saved", "git restore --worktree f.txt", "deny", "--worktree"),
    ("saved", "git restore -s HEAD f.txt", "deny", "--source=<ref>"),
    # FL1: each discard judged from git's own option parse (bundles, split flags, long prefixes, `+refspec`,
    # operands without `--`), and a discard a shell-fed heredoc runs.
    ("repo", "git reset --har", "deny", None),
    ("repo", "git reset --ha", "deny", None),
    ("repo", "git clean --forc", "deny", None),
    ("repo", "git clean --fo -d", "deny", None),
    ("repo", "git clean -f -e -n", "deny", None),
    ("repo", "git clean -fen", "deny", None),
    ("repo", "git clean -f --exclude -n", "deny", None),
    ("repo", "git push --forc origin main", "deny", None),
    ("repo", "git push --force-with origin main", "deny", None),
    ("repo", "git push origin +main", "deny", None),
    ("repo", "git push origin +HEAD:main", "deny", None),
    ("repo", "git checkout f.txt", "deny", None),
    ("repo", "git checkout HEAD f.txt", "deny", None),
    ("repo", "git checkout -f", "deny", None),
    ("repo", "git checkout --force", "deny", None),
    ("repo", "git restore --staged --work f.txt", "deny", None),
    ("repo", "git gc --prun=now", "deny", None),
    ("repo", "bash <<'EOF'\ngit reset --hard\nEOF", "deny", None),
    ("repo", "git checkout clean.txt", "allow", None),
    ("repo", "git clean -fn", "allow", None),
]


@pytest.mark.parametrize("fixture,command,kind,token", ROWS, ids=["%s:%s" % (r[0], r[1]) for r in ROWS])
def test_discard(fixtures, fixture, command, kind, token):
    repo = fixtures[fixture]
    got = bash(command, repo, session_id="s1")
    assert got.kind == kind, got.reason
    if token:
        assert token.lower() in got.reason.lower()


@pytest.mark.parametrize("command,token", [
    ("git checkout -- f.txt", LOSS_CITE), ("git restore f.txt", LOSS_CITE), ("git reset --hard", CITE),
    ("git push --force", CITE), ("git clean -f", CITE), ("git stash drop", CITE), ("git gc --prune=now", CITE),
])
def test_discard_cites_its_rule(fixtures, command, token):
    got = bash(command, fixtures["repo"])
    assert got.denied and token in got.reason


@pytest.mark.parametrize("command", ["git checkout -- f.txt", "git restore f.txt", "git reset --hard"])
def test_discard_recommends_no_stash_and_no_show_overwrite(fixtures, command):
    """MaddManager's rows: the stash list and a `git show > path` overwrite are shared-checkout hazards."""
    got = bash(command, fixtures["repo"])
    assert got.denied and "git stash" not in got.reason and "git show" not in got.reason


def test_observed_loss_denials_name_the_loss_not_an_owner_go_ahead(tmp_path_factory, fixtures):
    sq = _squashed(tmp_path_factory, "sq_loss")
    for command, repo in (("git checkout -- f.txt", fixtures["repo"]), ("git restore f.txt", fixtures["repo"]),
                          ("git branch -D uniq", sq)):
        got = bash(command, repo)
        assert got.denied and CITE not in got.reason and ("would be lost" in got.reason or "lose" in got.reason)


@pytest.fixture(scope="module")
def sq(tmp_path_factory):
    return _squashed(tmp_path_factory, "sq")


@pytest.fixture(scope="module")
def sq_fixture_branch(tmp_path_factory):
    """v1 added `scratch/fx` at uniq's tip after the uniq rows ran: a ref outside the deleted set holds it then."""
    repo = _squashed(tmp_path_factory, "sq_fx")
    git(repo, "branch", "scratch/fx", "uniq")
    return repo


@pytest.mark.parametrize("command,kind", [
    ("git branch -D sq", "allow"),
    ("git branch -D uniq", "deny"),
    ("git branch -D sq uniq", "deny"),
    ("git branch -D nosuch", "deny"),
    ("git branch -D -r origin/main", "deny"),
    # FL1: a force delete in every spelling git accepts.
    *[("git branch %s" % flags, "deny") for flags in (
        "-df uniq", "-fd uniq", "-d -f uniq", "-f -d uniq", "-qD uniq", "-Dq uniq", "--delete -f uniq",
        "-d --force uniq", "--force --delete uniq", "--del --force uniq", "--delete --forc uniq", "uniq -d -f",
        "-d uniq --force", "-d -f -- uniq")],
    ("git -C . branch -df uniq", "deny"),
    ("git --no-pager branch -d -f uniq", "deny"),
    ("git -c core.x=1 branch -df uniq", "deny"),
    ("git --config-env core.x=HOME branch -D uniq", "deny"),
    ("bash <<'EOF'\ngit branch -D uniq\nEOF", "deny"),
    ("git branch -df sq", "allow"),
])
def test_branch_delete_on_main(sq, command, kind):
    assert bash(command, sq).kind == kind


@pytest.mark.parametrize("command,kind", [("git branch -D scratch/fx", "allow"), ("git branch -D scratch/fx uniq", "deny")])
def test_branch_delete_of_a_scratch_fixture_branch(sq_fixture_branch, command, kind):
    assert bash(command, sq_fixture_branch).kind == kind


@pytest.mark.parametrize("command", ["git branch -D sq", "git branch --delete --force sq"])
def test_branch_delete_held_by_origin_head_only(tmp_path_factory, command):
    repo = _squashed(tmp_path_factory, "sq_other")
    git(repo, "checkout", "-q", "other")
    assert bash(command, repo).kind == "allow"


def test_branch_delete_holders(tmp_path_factory):
    """sec3: a ref outside the deleted set holding the tip admits the delete; the deny names every unmet
    branch and the preserve step only where a preserve would help."""
    held = _squashed(tmp_path_factory, "held")
    got = bash("git branch -D uniq", held)
    assert got.denied and "uniq" in got.reason and "refs/preserve/" in got.reason
    got = bash("git branch -D uniq nosuch", held)
    assert got.denied and "uniq" in got.reason and "nosuch" in got.reason
    got = bash("git branch -D nosuch", held)
    assert got.denied and "nosuch" in got.reason and "refs/preserve/" not in got.reason
    git(held, "branch", "uniq2", "uniq")
    git(held, "update-ref", "refs/remotes/origin/uniq", "uniq")
    for command in ("git branch -D uniq", "git branch -D uniq uniq2", "git branch --delete --force uniq"):
        assert bash(command, held).kind == "allow", command
    git(held, "update-ref", "-d", "refs/remotes/origin/uniq")
    assert bash("git branch -D uniq uniq2", held).denied
    assert bash("git branch -D uniq", held).kind == "allow"
    git(held, "branch", "-D", "uniq2")
    git(held, "tag", "kept-uniq", "uniq")
    assert bash("git branch -D uniq", held).kind == "allow"
    git(held, "tag", "-d", "kept-uniq")
    git(held, "update-ref", "refs/preserve/uniq", "uniq")
    assert bash("git branch -D uniq", held).kind == "allow"
    assert bash("git branch -D -r origin/main", held).denied


# --- MaddManager: a stash drop whose every change a ref tip already holds -------------------------------------
STASHED_WORK = {"a.txt": "a-new\n", "b.txt": "b-new\n", "gone.txt": None}


def _stash_scenario(factory, untracked=False, index_text=None):
    repo = factory.mktemp("held_stash")
    git(repo, "init", "-q", "-b", "main")
    for name in ("a.txt", "b.txt", "gone.txt"):
        write(repo, name, "base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feat")
    if index_text is not None:
        write(repo, "a.txt", index_text)
        git(repo, "add", "a.txt")
    write(repo, "a.txt", "a-new\n")
    write(repo, "b.txt", "b-new\n")
    os.remove(repo / "gone.txt")
    if untracked:
        write(repo, "n.txt", "n-new\n")
    git(repo, "stash", "push", "-q", *(["-u"] if untracked else []))
    return repo


def _land(repo, files):
    for name, text in files.items():
        if text is None:
            os.remove(repo / name)
        else:
            write(repo, name, text)
    git(repo, "add", "-A", ".")
    git(repo, "commit", "-q", "-m", "land")


STASH_HELD = [
    ("edits and deletion match a branch tip", {}, STASHED_WORK, ["main"], "allow"),
    ("only a tag holds the matching tip", {}, STASHED_WORK, ["tag", "main", "drop-feat"], "allow"),
    ("the untracked part matches a branch tip too", {"untracked": True},
     dict(STASHED_WORK, **{"n.txt": "n-new\n"}), ["main"], "allow"),
    ("the staged part equals the held worktree part", {"index_text": "a-new\n"}, STASHED_WORK, ["main"], "allow"),
    ("a partial match stays blocked", {}, {"a.txt": "a-new\n"}, [], "deny"),
    ("an untracked file no ref holds stays blocked", {"untracked": True}, STASHED_WORK, [], "deny"),
    ("an untracked file held with other content", {"untracked": True},
     dict(STASHED_WORK, **{"n.txt": "other\n"}), [], "deny"),
    ("a branch that moved past the stashed change", {}, STASHED_WORK, ["later"], "deny"),
    ("a branch since deleted leaves nothing to match", {}, STASHED_WORK, ["main", "drop-feat"], "deny"),
    ("a detached HEAD holds no ref", {}, STASHED_WORK, ["detach-first"], "deny"),
    ("a staged version no ref holds", {"index_text": "a-staged\n"}, STASHED_WORK, ["main"], "deny"),
]


@pytest.mark.parametrize("label,scenario,work,steps,kind", STASH_HELD, ids=[r[0] for r in STASH_HELD])
def test_stash_drop_held_by_a_ref_tip(tmp_path_factory, label, scenario, work, steps, kind):
    repo = _stash_scenario(tmp_path_factory, **scenario)
    if "detach-first" in steps:
        git(repo, "checkout", "-q", "--detach")
    _land(repo, work)
    for step in steps:
        if step == "main":
            git(repo, "checkout", "-q", "main")
        elif step == "tag":
            git(repo, "tag", "held")
        elif step == "drop-feat":
            git(repo, "branch", "-D", "feat")
        elif step == "later":
            _land(repo, {"a.txt": "a-later\n"})
    got = bash("git stash drop stash@{0}", repo)
    assert got.kind == kind, got.reason
    if kind == "deny":
        assert "git update-ref refs/preserve/" in got.reason and "stash@{0}" in got.reason


# One unproven call per `CASES` row: each row parses its own sample and denies it when no repository can be read.
CASE_SAMPLES = ["checkout -- f.txt", "restore f.txt", "reset --hard", "clean -fd", "push -f", "branch -D x",
                "stash drop", "stash clear", "reflog expire", "gc --prune=now", "filter-branch"]


def test_every_discard_case_has_its_deny_row():
    import dangerous_shell_guard as dsg
    claimed = []
    for sample in CASE_SAMPLES:
        sub, *rest = sample.split()
        rows = [case for case in dsg.CASES if case.parse(sub, tuple(rest), None) is not None]
        assert len(rows) == 1, sample
        claimed.append(rows[0])
        assert dsg.verdict([sub, *rest], None), sample
    assert len(set(map(id, claimed))) == len(dsg.CASES)
