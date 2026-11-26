#!/usr/bin/env python3
"""Proof that `_git_fixture` writes what `git init`, `git add -A` and `git commit` write, for each tree it takes in
process and each tree it hands to git.

    python3 .claude/tests/test_git_fixture.py

Every case builds the same tree twice, once through the fixture and once through real git with the same identity and
clock, and compares the commit ids: equal ids mean equal trees, parents, authors and messages."""
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_fixture as fx  # noqa: E402
from _case_pool import run_cases  # noqa: E402

STAMP = 1700000000


def _both():
    """(fixture repo, real-git repo), each with the fixture identity and the clock fixed to STAMP."""
    root = Path(tempfile.mkdtemp(prefix="git_fixture_proof_"))
    ours, real = root / "ours", root / "real"
    fx.init_repo(ours)
    real.mkdir()
    fx.git(real, "init", "-q", "-b", "main")
    with open(real / ".git" / "config", "a", encoding="utf-8", newline="\n") as handle:
        handle.write(fx._CONFIG)
    fx.time.time = lambda: STAMP
    return ours, real


def _put(files, *repos):
    for repo in repos:
        for rel, data in files.items():
            target = repo / rel
            if data is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _commit_both(ours, real, message):
    os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = "%d +0000" % STAMP  # for git's own commits
    try:
        return fx.commit_all(ours, message), fx.commit_with_git(real, message)
    finally:
        del os.environ["GIT_AUTHOR_DATE"], os.environ["GIT_COMMITTER_DATE"]


def _same(ours, real, message):
    mine, theirs = _commit_both(ours, real, message)
    assert mine == theirs, "commit ids differ: %s vs real git %s\n%s\n%s" % (
        mine, theirs, fx.git(ours, "ls-files", "-s").decode(), fx.git(real, "ls-files", "-s").decode())
    assert fx.git(ours, "ls-files", "-s") == fx.git(real, "ls-files", "-s")
    return mine


def _clean(repo):
    assert fx.git(repo, "status", "--porcelain") == b"", fx.git(repo, "status", "--porcelain").decode()
    fx.git(repo, "fsck", "--strict")


def case_a_plain_tree_and_its_edits_commit_as_git_commits_them():
    ours, real = _both()
    _put({"a.txt": b"one\n", "dir/b.py": b"two\n", "dir.x/c": b"three\n", "dir-1/q": b"q\n", "empty": b"",
          "deep/er/est/f": b"f\n", "na\xc3\xafve.md": b"unicode name\n", "bin.dat": b"\x00\x01\xff\xfe" * 100}, ours, real)
    first = _same(ours, real, "seed")
    _clean(ours)
    _put({"a.txt": None, "dir/b.py": b"edited\n", "new/n.txt": b"n\n", "deep/er/est/f": None}, ours, real)
    second = _same(ours, real, "second\n\nwith a body")
    _clean(ours)
    assert fx.git(ours, "rev-parse", second + "^").decode().strip() == first
    assert fx.branch_sha(ours) == second


def case_a_gitignore_is_handed_to_git_and_still_keeps_the_ignored_file_out():
    ours, real = _both()
    _put({".gitignore": b"skip.txt\nbuild/\n", "keep.txt": b"k\n", "skip.txt": b"s\n", "build/o": b"o\n"}, ours, real)
    _same(ours, real, "seed")
    assert b"skip.txt" not in fx.git(ours, "ls-files") and b"keep.txt" in fx.git(ours, "ls-files")
    _clean(ours)


def case_a_file_with_a_carriage_return_is_normalised_as_the_configured_git_does():
    ours, real = _both()
    for repo in (ours, real):
        fx.git(repo, "config", "core.autocrlf", "true")
    _put({"crlf.txt": b"a\r\nb\r\n", "lf.txt": b"a\nb\n"}, ours, real)
    _same(ours, real, "seed")
    assert fx.git(ours, "cat-file", "-p", "HEAD:crlf.txt") == b"a\nb\n"


def case_an_exec_bit_set_in_the_index_survives_the_next_commit():
    ours, real = _both()
    for repo in (ours, real):
        fx.git(repo, "config", "core.filemode", "false")
    _put({"tool.sh": b"#!/bin/sh\n", "plain.txt": b"p\n"}, ours, real)
    _same(ours, real, "seed")
    for repo in (ours, real):
        fx.git(repo, "update-index", "--chmod=+x", "tool.sh")
    _put({"tool.sh": b"#!/bin/sh\necho changed\n"}, ours, real)
    _same(ours, real, "edit")
    assert fx.git(ours, "ls-files", "-s").decode().splitlines()[1].startswith("100755")


def case_a_posix_exec_bit_is_read_from_the_file_when_the_repository_trusts_modes():
    if os.name == "nt":
        return
    ours, real = _both()
    for repo in (ours, real):
        fx.git(repo, "config", "core.filemode", "true")
    _put({"tool.sh": b"#!/bin/sh\n", "plain.txt": b"p\n"}, ours, real)
    for repo in (ours, real):
        os.chmod(repo / "tool.sh", os.stat(repo / "tool.sh").st_mode | stat.S_IXUSR)
    _same(ours, real, "seed")


def case_a_nested_repository_is_handed_to_git():
    ours, real = _both()
    _put({"top.txt": b"t\n"}, ours, real)
    os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = "%d +0000" % STAMP  # equal inner commits
    for repo in (ours, real):
        inner = repo / "vendor" / "lib"
        inner.mkdir(parents=True)
        fx.git(inner, "init", "-q")
        (inner / "x").write_text("x\n")
        fx.git(inner, "-c", "user.email=a@b", "-c", "user.name=a", "add", "-A")
        fx.git(inner, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "inner")
    del os.environ["GIT_AUTHOR_DATE"], os.environ["GIT_COMMITTER_DATE"]
    assert not fx._commit_in_process(ours, "unused"), "a nested repository was committed in process"
    _same(ours, real, "seed")


def case_a_commit_that_changes_no_tree_is_refused_as_git_refuses_it():
    ours, real = _both()
    for repo in (ours, real):  # an unborn branch with nothing to add
        for commit in (fx.commit_all, fx.commit_with_git):
            _refused(commit, repo, "empty")
    _put({"a.txt": b"one\n"}, ours, real)
    _same(ours, real, "seed")
    head = fx.branch_sha(ours)
    for commit in (fx.commit_all, fx.commit_with_git):
        _refused(commit, ours, "unchanged")
    assert fx.branch_sha(ours) == head
    _put({"a.txt": b"one\n"}, ours)  # rewritten with the same bytes: still the parent's tree
    _refused(fx.commit_all, ours, "rewritten")
    _put({"a.txt": b"two\n"}, ours, real)
    _same(ours, real, "changed")
    fx.git(ours, "repack", "-a", "-d", "-q")  # the parent is now packed: its tree comes from one rev-parse
    _refused(fx.commit_all, ours, "packed unchanged")
    _put({"a.txt": b"three\n"}, ours, real)
    _same(ours, real, "changed again")


def _refused(commit, repo, message):
    try:
        commit(repo, message)
    except subprocess.CalledProcessError as exc:
        assert exc.returncode == 1, exc
        return
    raise AssertionError("%s wrote a commit for a tree equal to its parent's" % commit.__name__)


def case_history_writes_dated_commits_on_the_branch_and_checks_the_tree_out():
    root = Path(tempfile.mkdtemp(prefix="git_fixture_proof_"))
    work = root / "work"
    fx.init_repo(work)
    _put({"keep": b"k\n", "gone": b"g\n"}, work)
    seed = fx.commit_all(work, "seed")
    first, second = fx.history(work, ({"a.txt": "a\n", "gone": None}, "first", STAMP),
                               ({"a.txt": "b\n"}, "second", STAMP + 60))
    assert fx.branch_sha(work) == second
    assert fx.git(work, "rev-parse", first + "^").decode().strip() == seed
    assert fx.git(work, "rev-parse", second + "^").decode().strip() == first
    assert fx.git(work, "log", "-3", "--format=%ct %s").decode().split("\n")[:2] == ["%d second" % (STAMP + 60),
                                                                                  "%d first" % STAMP]
    assert (work / "a.txt").read_text() == "b\n" and not (work / "gone").exists()
    _clean(work)
    assert not (work / ".git" / "history.marks").exists()


def case_history_replaces_a_ref_of_a_bare_repository_when_forced_as_a_root():
    root = Path(tempfile.mkdtemp(prefix="git_fixture_proof_"))
    bare = root / "state.git"
    fx.init_repo(bare, bare=True)
    (old,) = fx.history(bare, ({"s.json": "1\n"}, "old", 0), ref="refs/heads/state", root=True)
    (new,) = fx.history(bare, ({"s.json": "2\n"}, "new", 0), ref="refs/heads/state", root=True, force=True)
    assert old != new and fx.git(bare, "rev-parse", "refs/heads/state").decode().strip() == new
    assert fx.git(bare, "show", "state:s.json") == b"2\n"
    assert fx.git(bare, "rev-list", "--count", "state").decode().strip() == "1"


def case_init_repo_writes_the_layout_git_init_writes():
    root = Path(tempfile.mkdtemp(prefix="git_fixture_proof_"))
    fx.init_repo(root / "work")
    fx.init_repo(root / "bare.git", bare=True)
    fx.init_repo(root / "trunk", branch="trunk")
    (root / "real").mkdir()
    fx.git(root / "real", "init", "-q", "-b", "main")

    def keys(repo):
        return {line.split("=")[0] for line in fx.git(repo, "config", "--local", "--list").decode().splitlines()}

    assert keys(root / "real") <= keys(root / "work"), keys(root / "real") - keys(root / "work")
    assert keys(root / "work") - keys(root / "real") == {"user.email", "user.name", "gc.auto", "maintenance.auto"}
    assert fx.git(root / "bare.git", "config", "core.bare").strip() == b"true"
    assert fx.git(root / "work", "symbolic-ref", "HEAD").strip() == b"refs/heads/main"
    assert fx.git(root / "trunk", "symbolic-ref", "HEAD").strip() == b"refs/heads/trunk"
    assert fx.git(root / "work", "status", "--porcelain") == b""


def case_a_pushed_history_clones_and_commits_on_top():
    root = Path(tempfile.mkdtemp(prefix="git_fixture_proof_"))
    work = root / "work"
    fx.init_repo(work)
    _put({"a": b"a\n"}, work)
    first = fx.commit_all(work, "seed")
    remote = fx.bare_remote(root)
    fx.push_main(work, remote)
    clone = root / "clone"
    fx.git(root, "clone", "-q", remote.as_uri(), str(clone))
    assert fx.git(clone, "rev-parse", "HEAD").decode().strip() == first
    _put({"b": b"b\n"}, clone)
    second = fx.commit_all(clone, "on the clone")
    assert fx.git(clone, "rev-parse", second + "^").decode().strip() == first
    assert fx.branch_sha(clone) == second
    _clean(clone)


def main():
    return run_cases([value for name, value in globals().items() if name.startswith("case_")])


if __name__ == "__main__":
    sys.exit(main())
