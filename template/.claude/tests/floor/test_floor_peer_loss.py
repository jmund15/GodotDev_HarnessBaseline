"""Peer loss at commit: amend of another author's HEAD, a stale index that reverts a peer's blob, a moved stage
base. Rows of v1's `test_git_guardrails_amend.py`, `test_git_guardrails_revert_lane.py` (both repos; MaddManager's
copy holds the same cases through a spawned hook) and PushinPotions' `test_peer_loss.py`.

Commit verdicts run through the Bash entry; the parser rows of `test_peer_loss.py` call the parser in process.
"""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from case import bash

NEEDLE = "would revert a peer's commit"
OWN = "Claude-Session: https://claude.ai/code/session_OWN"
PEER = "Claude-Session: https://claude.ai/code/session_PEER"


def g(repo, *args, stdin=None):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, input=stdin.encode() if stdin is not None else None)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")))
    return r.stdout.decode("utf-8", "replace").strip()


def write(repo, name, text):
    Path(repo, name).write_text(text, encoding="utf-8", newline="\n")


def commit(repo, name, text, message):
    write(repo, name, text)
    g(repo, "add", name)
    g(repo, "commit", "-q", "--no-verify", "-m", message)


def make_repo(tmp, label):
    repo = Path(tmp, label)
    repo.mkdir(parents=True)
    g(repo, "init", "-q", "-b", "main")
    g(repo, "config", "user.email", "t@t")
    g(repo, "config", "user.name", "t")
    commit(repo, "seed.txt", "seed\n", "seed")
    return repo


def publish_default(repo):
    g(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    g(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def stage_blob(repo, name, text):
    blob = g(repo, "hash-object", "-w", "--stdin", stdin=text)
    g(repo, "update-index", "--cacheinfo", "100644,%s,%s" % (blob, name))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A planted home whose session registry maps session s1 to bridge `session_OWN` (None: no row)."""
    def make(bridge="session_OWN"):
        path = tmp_path / ("home_%s" % bridge)
        (path / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
        if bridge:
            (path / ".claude" / "sessions" / "1.json").write_text(
                json.dumps({"pid": 1, "sessionId": "s1", "bridgeSessionId": bridge}), encoding="utf-8")
        monkeypatch.setenv("HOME", str(path))
        monkeypatch.setenv("USERPROFILE", str(path))
        return path
    return make


def repo_with_head(tmp, label, trailer):
    repo = make_repo(tmp, label)
    commit(repo, "m.txt", "one\n", "change" + ("\n\n" + trailer if trailer else ""))
    write(repo, "m.txt", "two\n")
    g(repo, "add", "m.txt")
    return repo


# --- test_git_guardrails_amend.py ------------------------------------------------------------------------------
@pytest.mark.parametrize("label,trailer,command,bridge,kind", [
    ("own HEAD", OWN, "git commit --amend --no-edit", "session_OWN", "allow"),
    ("peer HEAD", PEER, "git commit --amend --no-edit", "session_OWN", "deny"),
    ("owner HEAD, no trailer", "", "git commit --amend -m reworded", "session_OWN", "deny"),
    ("a new commit on a peer HEAD", PEER, "git commit -m next", "session_OWN", "allow"),
    ("own trailer unresolvable", OWN, "git commit --amend --no-edit", None, "deny"),
])
def test_amend(tmp_path, home, label, trailer, command, bridge, kind):
    home(bridge)
    repo = repo_with_head(tmp_path, "r", trailer)
    got = bash(command, repo, session_id="s1")
    assert got.kind == kind, got.reason
    if kind == "deny" and label != "own trailer unresolvable":
        assert g(repo, "rev-parse", "--short=9", "HEAD") in got.reason


def test_amend_of_a_lanes_own_commit_allows(tmp_path, home):
    home()
    base = repo_with_head(tmp_path, "base", OWN)
    g(base, "commit", "-q", "-m", "base")
    g(base, "update-ref", "refs/remotes/origin/main", "HEAD")
    lane = tmp_path / "lane"
    g(base, "worktree", "add", "-q", "-b", "lane", str(lane))
    commit(lane, "l.txt", "delegate\n", "delegate work\n\n" + PEER)
    write(lane, "l.txt", "delegate, fixed\n")
    g(lane, "add", "l.txt")
    assert bash("git commit --amend --no-edit", lane, session_id="s1").kind == "allow"


# --- test_git_guardrails_revert_lane.py -----------------------------------------------------------------------
def test_lane_restaging_its_own_replaced_blob_allows_and_below_the_merge_base_denies(tmp_path):
    repo = make_repo(tmp_path, "lane")
    commit(repo, "m.txt", "A\n", "m A")
    commit(repo, "q.txt", "Q1\n", "q1")
    commit(repo, "q.txt", "Q2\n", "peer replaced Q1 before the lane forked")
    publish_default(repo)
    lane = tmp_path / "lane_wt"
    g(repo, "worktree", "add", "-q", "-b", "lane", str(lane))
    commit(lane, "m.txt", "B\n", "lane replaces A with B")
    stage_blob(lane, "m.txt", "A\n")
    write(lane, "m.txt", "A\n")
    assert bash("git commit -F msg", lane).kind == "allow"
    write(lane, "q.txt", "Q2\n")
    stage_blob(lane, "q.txt", "Q1\n")
    write(lane, "q.txt", "Q1\n")
    got = bash("git commit -F msg", lane)
    assert got.denied and NEEDLE in got.reason


def test_shared_checkout_after_a_peer_commit_denies(tmp_path):
    shared = make_repo(tmp_path, "shared")
    commit(shared, "m.txt", "A\n", "m A")
    publish_default(shared)
    commit(shared, "m.txt", "B\n", "peer replaces A with B in the shared checkout")
    g(shared, "worktree", "add", "-q", "-b", "other", str(tmp_path / "shared_wt"))
    stage_blob(shared, "m.txt", "A\n")
    write(shared, "m.txt", "A\n")
    got = bash("git commit -F msg", shared)
    assert got.denied and NEEDLE in got.reason


def revert_cases(tmp):
    """(label, repo, expected kind, needle) for the three revert-in-progress rows."""
    out = []
    repo = make_repo(tmp, "revert")
    commit(repo, "f.txt", "a\n.\n.\n.\nbx\n", "c0")
    commit(repo, "f.txt", "a\n.\n.\n.\nb\n", "c1 replaces the c0 blob")
    commit(repo, "f.txt", "ax\n.\n.\n.\nb\n", "c2, the commit to revert")
    commit(repo, "f.txt", "ax\n.\n.\n.\nbx\n", "c3")
    g(repo, "revert", "--no-commit", "HEAD~1")
    out.append(("revert result equals an older replaced blob", repo, "allow", None))
    repo = make_repo(tmp, "revert_other")
    commit(repo, "g.txt", "g0\n", "g0")
    commit(repo, "g.txt", "g1\n", "peer changes g")
    commit(repo, "f.txt", "f1\n", "the commit to revert")
    g(repo, "revert", "--no-commit", "HEAD")
    stage_blob(repo, "g.txt", "g0\n")
    write(repo, "g.txt", "g0\n")
    out.append(("a revert still denies a path the reverted commit never changed", repo, "deny", "g.txt"))
    repo = make_repo(tmp, "revert_merge")
    commit(repo, "f.txt", "A\n", "c0")
    commit(repo, "f.txt", "B\n", "c1 replaces the c0 blob")
    g(repo, "checkout", "-q", "-b", "side")
    commit(repo, "f.txt", "C\n", "side replaces B")
    g(repo, "checkout", "-q", "main")
    g(repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    g(repo, "revert", "--no-commit", "-m", "1", "HEAD")
    out.append(("revert -m 1 of a merge stages the mainline parent's blob", repo, "allow", None))
    return out


def test_revert_in_progress(tmp_path):
    for label, repo, kind, needle in revert_cases(tmp_path):
        got = bash("git commit -F msg", repo)
        assert got.kind == kind, label
        if needle:
            assert needle in got.reason, label


def test_pathspec_commits_publish_the_worktree(tmp_path):
    repo = make_repo(tmp_path, "pathspec")
    commit(repo, "f.txt", "A\n", "f A")
    commit(repo, "f.txt", "B\n", "peer replaces A with B")
    stage_blob(repo, "f.txt", "A\n")
    write(repo, "f.txt", "A\n")
    spec = tmp_path / "paths.txt"
    spec.write_text("f.txt\n", encoding="utf-8", newline="\n")
    spec = str(spec).replace("\\", "/")
    assert bash("git commit -F msg -- f.txt", repo).kind == "allow"
    assert bash("git commit -F msg --pathspec-from-file=%s" % spec, repo).kind == "allow"
    assert bash("git commit -F msg --pathspec-from-file %s" % spec, repo).kind == "allow"
    got = bash("git commit -F msg", repo)
    assert got.denied and NEEDLE in got.reason


# --- test_peer_loss.py (PushinPotions) ------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path, "repo")


def test_reintroduced_path_is_judged_in_the_repository_git_env_selects(tmp_path, repo):
    import dangerous_shell_guard as dsg
    other = make_repo(tmp_path, "other")
    commit(repo, "removed.txt", "original\n", "add a file")
    g(repo, "rm", "removed.txt")
    g(repo, "commit", "-qm", "peer deletes file")
    env = {"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(other), "GIT_INDEX_FILE": str(tmp_path / "private.index")}
    dsg.run_git(["read-tree", "HEAD"], str(other), env)
    blob = g(repo, "hash-object", "-w", "--stdin", stdin="original\n")
    dsg.run_git(["update-index", "--add", "--cacheinfo", f"100644,{blob},removed.txt"], str(other), env)
    assert dsg.revert_verdict([], str(other), None) is None
    assert "removed.txt" in (dsg.revert_verdict([], str(other), env) or "")


def test_staged_versus_dirty_same_path_picks_the_published_bytes(repo):
    from _commit_scope import publication
    from _git_commit import staged_paths
    commit(repo, "staged.txt", "base\n", "base")
    commit(repo, "selected.txt", "base\n", "base")
    stage_blob(repo, "staged.txt", "only in the index\n")
    write(repo, "staged.txt", "only in the worktree\n")
    write(repo, "selected.txt", "selected\n")
    rest = ["-im", "include", "--", "selected.txt"]
    paths, failed = staged_paths(rest, str(repo))
    assert failed is None and paths == {"staged.txt", "selected.txt"}
    assert publication("commit", rest, str(repo)).sources == {"staged.txt": "index", "selected.txt": "worktree"}


def test_pathspec_without_dash_i_reads_the_index_view_for_staged_paths(repo):
    from _git_commit import commit_source, staged_paths
    commit(repo, "staged.txt", "base\n", "base")
    commit(repo, "selected.txt", "base\n", "base")
    stage_blob(repo, "staged.txt", "only in the index\n")
    write(repo, "selected.txt", "selected\n")
    paths, failed = staged_paths(["--", "selected.txt"], str(repo))
    assert failed is None and paths == {"selected.txt"}
    assert commit_source(["--", "selected.txt"], str(repo)) == "worktree"


def test_unknown_source_is_distinct_from_worktree(repo):
    from _git_commit import commit_source
    assert commit_source(["-i", "--pathspec-from-file=-"], str(repo)) != commit_source(["--", "x"], str(repo))


def test_stage_record_base_mismatch_denies(tmp_path, repo):
    import dangerous_shell_guard as dsg
    from _commit_scope import stage_record_path
    index = tmp_path / "private.index"
    base = g(repo, "rev-parse", "HEAD")
    env = {"GIT_INDEX_FILE": str(index)}
    record = stage_record_path(index)
    record.write_text('{"base_head": "%s"}' % base, encoding="utf-8")
    assert dsg.stage_record_loss(str(repo), env) is None
    commit(repo, "peer.txt", "peer\n", "peer change")
    loss = dsg.stage_record_loss(str(repo), env)
    assert (loss.kind, loss.base, loss.head) == ("stage_record", base, g(repo, "rev-parse", "HEAD"))
    record.write_text("{", encoding="utf-8")
    assert dsg.stage_record_loss(str(repo), env).base is None


def test_stage_record_base_mismatch_denies_through_the_entry(tmp_path, repo):
    from _commit_scope import stage_record_path
    index = tmp_path / "private.index"
    g(repo, "read-tree", "HEAD")
    stage_record_path(index).write_text('{"base_head": "%s"}' % g(repo, "rev-parse", "HEAD"), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "read-tree", "HEAD"], env=dict(os.environ, GIT_INDEX_FILE=str(index)),
                   check=True, capture_output=True)
    assert bash("GIT_INDEX_FILE=%s git commit -F msg" % str(index).replace("\\", "/"), repo).kind == "allow"
    commit(repo, "peer.txt", "peer\n", "peer change")
    got = bash("GIT_INDEX_FILE=%s git commit -F msg" % str(index).replace("\\", "/"), repo)
    assert got.denied and "index_commit.py" in got.reason


def test_lane_provenance_allows(tmp_path, repo):
    import dangerous_shell_guard as dsg
    commit(repo, "f", "old\n", "old")
    g(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    lane = tmp_path / "lane"
    g(repo, "worktree", "add", "-qb", "lane", str(lane))
    commit(lane, "f", "new\n", "lane")
    stage_blob(lane, "f", "old\n")
    write(lane, "f", "old\n")
    assert dsg.stale_loss([], str(lane)) is None
    assert dsg.amend_loss(["--amend"], str(lane)) is None


def test_amend_owned_by_this_session_allows(repo):
    import dangerous_shell_guard as dsg
    trailer = "claude.ai/code/session_test"
    commit(repo, "f", "old\n", "change\n\nClaude-Session: https://" + trailer)
    with patch.object(dsg, "session_trailer", return_value=trailer):
        assert dsg.amend_loss(["--amend"], str(repo), session_id="s1") is None
    assert dsg.amend_loss(["--amend"], str(repo)).kind == "amend"


def test_merge_in_progress_allows(repo):
    import dangerous_shell_guard as dsg
    commit(repo, "f", "base\n", "base")
    g(repo, "checkout", "-qb", "side")
    commit(repo, "f", "incoming\n", "incoming")
    g(repo, "checkout", "-q", "main")
    g(repo, "merge", "--no-ff", "--no-commit", "side")
    assert dsg.stale_loss([], str(repo)) is None


def test_mode_only_change_allows(repo):
    import dangerous_shell_guard as dsg
    g(repo, "update-index", "--chmod=+x", "seed.txt")
    assert dsg.stale_loss([], str(repo)) is None


def test_stale_blob_signature_within_bounded_history(repo):
    import dangerous_shell_guard as dsg
    commit(repo, "f", "old\n", "old")
    commit(repo, "f", "peer\n", "peer")
    stage_blob(repo, "f", "old\n")
    write(repo, "f", "not HEAD\n")
    assert dsg.stale_loss([], str(repo)).paths == ("f",)
    with patch.object(dsg, "REVERT_HISTORY", 1):
        g(repo, "commit", "--allow-empty", "--only", "-qm", "later")
        assert dsg.stale_loss([], str(repo)) is None
