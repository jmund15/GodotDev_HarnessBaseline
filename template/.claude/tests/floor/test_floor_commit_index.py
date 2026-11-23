"""The index a commit publishes: the loss rows of v1's `test_git_guardrails_harness_commit.py` (both repos).

W: an index write earlier in the same command (kept, drop-list entry D-IW). G1: an index the floor cannot read.
G2: a stale index that reverts a peer's blob. G3: a private index over a moved stage base. S: staged-blob shapes
that publish no loss. M: the must-allow operations (`_must_allow_cases`). The file's proof-required, advisory,
skill, density, layer-closure and push-close sections are V1's Lost column (`test_floor_ported.md`).
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from case import bash


def g(repo, *args, env=None):
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                       env=None if env is None else dict(os.environ, **env))
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout


def write(repo, rel, text="x = 1\n"):
    path = Path(repo, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def make_repo(tmp, name="repo"):
    repo = Path(tmp, name)
    repo.mkdir(parents=True)
    g(repo, "init", "-q")
    g(repo, "config", "user.email", "t@t.example")
    g(repo, "config", "user.name", "t")
    g(repo, "config", "commit.gpgsign", "false")
    write(repo, "README.md", "seed\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "seed")
    return repo


def alt(repo, index, *args):
    g(repo, *args, env={"GIT_INDEX_FILE": str(index)})


def hash_blob(repo, text):
    return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=str(repo), input=text, capture_output=True,
                          text=True, check=True).stdout.strip()


def posix(path):
    return str(path).replace("\\", "/")


NEXT_STEP = ("earlier in this command", "its own Bash call, then commit")


# --- W: same-command index writes -------------------------------------------------------------------------------
W_DENY = [
    "git add -- a.txt && git commit -q -m x", "git add -- $(cat paths) && git commit -F msg",
    "git add a.txt; git commit -q -m x", "cd sub && git add ../a.txt && cd .. && git commit -q -m x",
    "git rm -q --cached README.md && git commit -q -m x", "git reset -q --soft HEAD && git commit -q -m x",
    "git stash && git commit -q -m x", "git add a.txt && git merge --continue",
    "git apply --cached p.diff && git commit -q -m x",
]
W_ALLOW = [
    "git commit -q --allow-empty -m x && git add a.txt", "git add a.txt",
    "git stash list && git commit -q --allow-empty -m x", "git status --short && git commit -q --allow-empty -m x",
    "git apply p.diff && git commit -q --allow-empty -m x", "echo 'git add a.txt && git commit -m x'",
]


@pytest.fixture(scope="module")
def wrepo(tmp_path_factory):
    repo = make_repo(tmp_path_factory.mktemp("w"))
    write(repo, "a.txt", "a\n")
    (repo / "sub").mkdir()
    return repo


@pytest.mark.parametrize("command", W_DENY)
def test_same_command_index_write_denies(wrepo, command):
    got = bash(command, wrepo, session_id="s1")
    if command.startswith("git add -- a.txt &&"):
        # a.txt is untracked here, so the fold cannot take it: the index-write deny stands.
        pass
    assert got.denied and all(n in got.reason for n in NEXT_STEP), got.reason


@pytest.mark.parametrize("command", W_ALLOW)
def test_same_command_index_write_allows(wrepo, command):
    assert bash(command, wrepo, session_id="s1").kind == "allow"


# D-IW (owner, 2026-11-23): the deny stands only where git's commit-msg hook cannot re-check the final index.
@pytest.fixture(scope="module")
def hooked(tmp_path_factory):
    """`wrepo`'s shape with `core.hooksPath=.claude/githooks` and a `commit-msg` hook in place."""
    repo = make_repo(tmp_path_factory.mktemp("hooked"))
    write(repo, "a.txt", "a\n")
    (repo / "sub").mkdir()
    write(repo, ".claude/githooks/commit-msg", "#!/bin/sh\nexit 0\n")
    g(repo, "config", "core.hooksPath", ".claude/githooks")
    return repo


@pytest.mark.parametrize("command", [c for c in W_DENY if "merge --continue" not in c] + [
    "git add a.txt && git merge --continue"])
def test_an_index_write_before_a_hooked_commit_allows(hooked, command):
    assert bash(command, hooked, session_id="s1").kind == "allow"


@pytest.mark.parametrize("command", [
    "git add a.txt && git commit -n -m x", "git add a.txt && git commit --no-verify -q -m x",
    "git add a.txt && git commit -qnm x", "git add a.txt && git -c core.hooksPath=/dev/null commit -q -m x",
    "git stash && git cherry-pick --continue --no-verify",
])
def test_an_index_write_before_a_commit_that_skips_the_hook_denies(hooked, command):
    got = bash(command, hooked, session_id="s1")
    assert got.denied and all(n in got.reason for n in NEXT_STEP), got.reason


def test_a_head_move_before_an_amend_denies_even_when_hooked(hooked):
    """The commit-msg entry re-runs the stage-record and peer-revert checks, not the amend check, which read HEAD
    before the command moved it."""
    got = bash("git reset -q --soft HEAD && git commit --amend -q -m x", hooked, session_id="s1")
    assert got.denied and all(n in got.reason for n in NEXT_STEP), got.reason


def test_a_hooks_path_without_the_commit_msg_hook_denies(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "a.txt", "a\n")
    g(repo, "config", "core.hooksPath", ".claude/githooks")
    got = bash("git add a.txt && git commit -q -m x", repo, session_id="s1")
    assert got.denied and all(n in got.reason for n in NEXT_STEP), got.reason


@pytest.fixture
def probe_root(tmp_path, monkeypatch):
    root = tmp_path / "probe_root"
    for name in ("agy_harness_probe_1", "already_here", "second"):
        repo = root / name
        repo.mkdir(parents=True)
        g(repo, "init", "-q")
        write(repo, "AGENTS.md" if name.startswith("agy") else "f.txt", "prior run\n")
        g(repo, "add", "-A")
        g(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    monkeypatch.setenv("TEMP", posix(root))
    return root


def test_init_on_a_temp_named_repo_that_already_is_one_then_add_and_commit_allows(probe_root):
    command = ('P="$TEMP/agy_harness_probe_1"; mkdir "$P" && mkdir -p "$P/.agents"; cd "$P"; git init -q; '
               'printf "%s\\n" probe > AGENTS.md; git add -A; git -c user.email=t@t -c user.name=t commit -qm probe')
    assert bash(command, probe_root / "agy_harness_probe_1").kind == "allow"


@pytest.mark.parametrize("command,token", [
    ('P="$TEMP/already_here"; cd "$P"; git add -A; git -c user.email=t@t -c user.name=t commit -qm x',
     "earlier in this command"),
    ('P="$TEMP/already_here"; Q="$TEMP/second"; cd "$P"; git init -q; cd "$Q"; git add -A; '
     'git -c user.email=t@t -c user.name=t commit -qm x', "earlier in this command"),
    ('git -C "$UNSET_DIR" add f; git -C "$UNSET_DIR" commit -F msg', "literal `-C` path"),
])
def test_writes_into_a_repo_the_command_never_creates_deny(probe_root, command, token):
    got = bash(command, probe_root / "already_here")
    assert got.denied and token in got.reason


# --- G1: an index the floor cannot read -----------------------------------------------------------------------
@pytest.fixture
def g1(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "a.txt", "a\n")
    built = repo / "built.index"
    alt(repo, built, "read-tree", "HEAD")
    alt(repo, built, "add", "a.txt")
    return repo, posix(built)


@pytest.mark.parametrize("command,tokens", [
    ('IDX="$PWD/alt.index" && GIT_INDEX_FILE="$IDX" git commit -F msg', ("GIT_INDEX_FILE", "#16", "does not exist")),
    ('export GIT_INDEX_FILE="$PWD/alt.index" && git commit -F msg', ("#16", "does not exist")),
    ("GIT_INDEX_FILE={missing} git commit -F msg", ("does not exist", "#16", "build the index in an earlier call")),
])
def test_g1_unreadable_index_denies(g1, command, tokens):
    repo, _ = g1
    got = bash(command.format(missing=posix(repo / "not_built.index")), repo)
    assert got.denied and all(t in got.reason for t in tokens), got.reason


def test_g1_built_index_is_judged_and_allowed(g1):
    repo, built = g1
    msys = "/" + built[0].lower() + built[2:] if built[1] == ":" else built
    for command in ("GIT_INDEX_FILE=%s git commit -F msg" % built, 'IDX=%s && GIT_INDEX_FILE="$IDX" git commit -F msg' % built,
                    "GIT_INDEX_FILE=%s git commit -F msg" % msys):
        assert bash(command, repo).kind == "allow", command


# --- G2: a stale index reverting a peer's blob ----------------------------------------------------------------
def test_g2_temp_index_from_an_older_head(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "peer.txt", "v1\n")
    g(repo, "add", "peer.txt")
    g(repo, "commit", "-q", "-m", "v1")
    stale = repo / "stale.index"
    alt(repo, stale, "read-tree", "HEAD")
    write(repo, "peer.txt", "v2\n")
    g(repo, "commit", "-q", "-am", "v2")
    write(repo, "mine.txt", "mine\n")
    alt(repo, stale, "add", "mine.txt")
    s = posix(stale)
    for command in ("GIT_INDEX_FILE=%s git commit -F msg" % s,
                    'cd "%s" && GIT_INDEX_FILE=%s git commit -F msg 2>&1 | tail -4' % (repo, s),
                    "GIT_INDEX_FILE=%s git commit -F msg > out.log" % s):
        got = bash(command, repo)
        assert got.denied and "peer.txt" in got.reason, command
    write(repo, "peer.txt", "v2\nuncommitted\n")
    got = bash("GIT_INDEX_FILE=%s git commit -F msg" % s, repo)
    assert got.denied and "peer.txt" in got.reason
    write(repo, "peer.txt", "v2\n")
    fresh = repo / "fresh.index"
    alt(repo, fresh, "read-tree", "HEAD")
    alt(repo, fresh, "add", "mine.txt")
    assert bash("GIT_INDEX_FILE=%s git commit -F msg" % posix(fresh), repo).kind == "allow"


def test_g2_head_based_blob_beside_a_peers_unstaged_hunk_allows(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "f.txt", "base\n")
    g(repo, "add", "f.txt")
    g(repo, "commit", "-q", "-m", "base")
    write(repo, "f.txt", "base\nmine\npeer\n")
    g(repo, "update-index", "--cacheinfo", "100644,%s,f.txt" % hash_blob(repo, "base\nmine\n"))
    assert bash("git commit -F msg", repo).kind == "allow"


def test_g2_default_index_staging_old_content(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "g.txt", "one\n")
    g(repo, "add", "g.txt")
    g(repo, "commit", "-q", "-m", "one")
    g(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % hash_blob(repo, "zero\n"))
    got = bash("git commit -F msg", repo)
    assert got.denied and "g.txt" in got.reason
    assert bash("git commit -F msg -- g.txt", repo).kind == "allow"


def test_g2_temp_index_re_adding_a_peer_deleted_file(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "gone.txt", "old\n")
    g(repo, "add", "gone.txt")
    g(repo, "commit", "-q", "-m", "add gone")
    stale = repo / "stale.index"
    alt(repo, stale, "read-tree", "HEAD")
    g(repo, "rm", "-q", "gone.txt")
    g(repo, "commit", "-q", "-m", "peer deletes gone")
    got = bash("GIT_INDEX_FILE=%s git commit -F msg" % posix(stale), repo)
    assert got.denied and "gone.txt" in got.reason


@pytest.mark.parametrize("trailer,kind", [("Claude-Session: https://claude.ai/code/session_OWN", "allow"),
                                          ("Claude-Session: https://claude.ai/code/session_PEER", "deny")])
def test_g2_restoring_a_blob_by_commit_trailer(tmp_path, monkeypatch, trailer, kind):
    home = tmp_path / "home"
    (home / ".claude" / "sessions").mkdir(parents=True)
    (home / ".claude" / "sessions" / "1.json").write_text(
        json.dumps({"pid": 1, "sessionId": "s1", "bridgeSessionId": "session_OWN"}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = make_repo(tmp_path)
    write(repo, "m.txt", "base\n")
    g(repo, "add", "m.txt")
    g(repo, "commit", "-q", "-m", "base")
    base = g(repo, "rev-parse", "HEAD:m.txt").strip()
    write(repo, "m.txt", "base\nadded\n")
    g(repo, "commit", "-q", "-am", "add a line\n\n" + trailer)
    write(repo, "m.txt", "base\nadded\npeer edit\n")
    g(repo, "update-index", "--cacheinfo", "100644,%s,m.txt" % base)
    got = bash("git commit -F msg", repo, session_id="s1")
    assert got.kind == kind
    if kind == "deny":
        assert "m.txt" in got.reason


@pytest.mark.parametrize("side_tip,kind", [("A\n", "allow"), ("C\n", "deny")])
def test_g2_merge_staging_a_parents_blob(tmp_path, side_tip, kind):
    repo = make_repo(tmp_path)
    write(repo, "h.txt", "A\n")
    g(repo, "add", "h.txt")
    g(repo, "commit", "-q", "-m", "A")
    g(repo, "checkout", "-q", "-b", "side")
    write(repo, "h.txt", "B\n")
    g(repo, "commit", "-q", "-am", "A to B")
    g(repo, "branch", "branch")
    write(repo, "h.txt", side_tip)
    g(repo, "commit", "-q", "-am", "side tip")
    g(repo, "checkout", "-q", "branch")
    write(repo, "other.txt", "x\n")
    g(repo, "add", "other.txt")
    g(repo, "commit", "-q", "-m", "branch work")
    subprocess.run(["git", "merge", "--no-commit", "--no-ff", "side"], cwd=str(repo), capture_output=True)
    if side_tip == "C\n":
        g(repo, "update-index", "--cacheinfo", "100644,%s,h.txt" % g(repo, "rev-parse", "HEAD~2:h.txt").strip())
    got = bash("git commit -F msg", repo)
    assert got.kind == kind
    if kind == "deny":
        assert "h.txt" in got.reason


@pytest.mark.parametrize("step", [["rm", "-q", "--cached", "README.md"], ["update-index", "--chmod=+x", "README.md"]])
def test_g2_untrack_and_mode_only_change_are_no_revert(tmp_path, step):
    repo = make_repo(tmp_path)
    g(repo, *step)
    assert bash("git commit -F msg", repo).kind == "allow"


# --- G3: a private index over a moved stage base ---------------------------------------------------------------
def private_index(repo, name, base):
    index = repo / ".git" / ("index-" + name)
    alt(repo, index, "read-tree", base)
    Path(str(index) + ".paths.json").write_text(
        json.dumps({"name": name, "base_head": base, "index": "index-" + name, "paths": {}}), encoding="utf-8")
    return index


@pytest.mark.parametrize("peer", ["add", "modify", "delete"])
def test_g3_private_index_over_a_moved_base_denies(tmp_path, peer):
    repo = make_repo(tmp_path)
    write(repo, "peer.txt", "v1\n")
    g(repo, "add", "peer.txt")
    g(repo, "commit", "-q", "-m", "v1")
    base = g(repo, "rev-parse", "HEAD").strip()
    index = private_index(repo, "mine", base)
    if peer == "add":
        write(repo, "added.txt", "peer\n")
        g(repo, "add", "added.txt")
    elif peer == "modify":
        write(repo, "peer.txt", "v2\n")
        g(repo, "add", "peer.txt")
    else:
        g(repo, "rm", "-q", "peer.txt")
    g(repo, "commit", "-q", "-m", "peer " + peer)
    write(repo, "mine.txt", "mine\n")
    alt(repo, index, "add", "mine.txt")
    got = bash("GIT_INDEX_FILE=%s git commit -F msg" % posix(index), repo)
    assert got.denied and "index_commit" in got.reason and base[:9] in got.reason


def test_g3_current_base_allows_and_an_unreadable_record_denies(tmp_path):
    repo = make_repo(tmp_path)
    index = private_index(repo, "fresh", g(repo, "rev-parse", "HEAD").strip())
    write(repo, "mine.txt", "mine\n")
    alt(repo, index, "add", "mine.txt")
    assert bash("GIT_INDEX_FILE=%s git commit -F msg" % posix(index), repo).kind == "allow"
    Path(str(index) + ".paths.json").write_text("{not json", encoding="utf-8")
    got = bash("GIT_INDEX_FILE=%s git commit -F msg" % posix(index), repo)
    assert got.denied and "index_commit" in got.reason


# --- S: staged-blob shapes that publish no loss ---------------------------------------------------------------
def test_staged_blob_shapes_allow(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, ".claude/hooks/x.py", "x = 1\n")
    write(repo, ".claude/tests/test_x_ok.py", "print('ok')\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "hook")
    write(repo, ".claude/hooks/x.py", "x = 2\n")
    g(repo, "add", ".claude/hooks/x.py")
    write(repo, ".claude/hooks/x.py", "x = 2\npeer = 1\n")
    for command in ("git commit -F msg", "git commit -F msg -- .claude/hooks/x.py", "git commit -a -F msg"):
        assert bash(command, repo).kind == "allow", command
    write(repo, "spec.txt", ".claude/hooks/x.py\n")
    assert bash("git commit -i -F msg --pathspec-from-file=spec.txt", repo).kind == "allow"
    private = repo / "private.idx"
    alt(repo, private, "read-tree", "HEAD")
    alt(repo, private, "update-index", "--cacheinfo", "100644,%s,.claude/hooks/x.py" % hash_blob(repo, "x = 3\n"))
    assert bash('GIT_INDEX_FILE="%s" git commit -F msg' % posix(private), repo).kind == "allow"


# --- M: must-allow operations ------------------------------------------------------------------------------------
@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    g(repo, "init", "-q", "-b", "trunk")
    for k, v in (("user.email", "t@t.example"), ("user.name", "t"), ("commit.gpgsign", "false")):
        g(repo, "config", k, v)
    write(repo, "README.md", "seed\n")
    write(repo, ".gitignore", ".claude/tests/\n.claude/cache/\n.claude/logs/\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "base")
    g(repo, "checkout", "-q", "-b", "feature")
    write(repo, "notes/merged.txt", "merged from feature\n")
    for rel in (".claude/tests/test_present_only_here.py", ".claude/cache/present_only_here.txt",
                ".claude/logs/harness_tests_stamp.json"):
        write(repo, rel, "{}\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "feature")
    g(repo, "checkout", "-q", "trunk")
    return repo


def test_must_allow_merge_in_a_fresh_worktree(origin, tmp_path):
    wt = tmp_path / "merge_wt"
    g(origin, "worktree", "add", "-q", "--detach", str(wt), "trunk")
    assert bash("git merge feature", wt).kind == "allow"


def test_must_allow_the_commit_closing_a_merge_and_merge_continue(origin, tmp_path):
    wt = tmp_path / "close_wt"
    g(origin, "worktree", "add", "-q", "--detach", str(wt), "trunk")
    g(wt, "merge", "-q", "--no-commit", "--no-ff", "feature")
    assert bash("git commit -q -F m", wt).kind == "allow"
    assert bash("git merge --continue", wt).kind == "allow"


def test_must_allow_a_commit_in_a_fresh_clone_lacking_ignored_inputs(origin, tmp_path):
    clone = tmp_path / "clone"
    g(tmp_path, "clone", "-q", str(origin), str(clone))
    write(clone, "README.md", "seed\nedited in a fresh clone\n")
    g(clone, "add", "README.md")
    assert bash("git commit -q -F m", clone).kind == "allow"


def _conflict(tmp_path, verb):
    repo = make_repo(tmp_path)
    write(repo, ".claude/hooks/h.py", "h = 0\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "base hook")
    g(repo, "checkout", "-q", "-b", "upstream")
    write(repo, ".claude/hooks/h.py", "h = 2  # upstream\n")
    g(repo, "commit", "-q", "-am", "upstream")
    g(repo, "checkout", "-q", "-")
    write(repo, ".claude/hooks/h.py", "h = 1  # work\n")
    g(repo, "commit", "-q", "-am", "work")
    subprocess.run(["git", verb, "-q", "upstream"], cwd=str(repo), capture_output=True)
    side = "--theirs" if verb == "merge" else "--ours"
    g(repo, "checkout", side, "--", ".claude/hooks/h.py")
    g(repo, "add", ".claude/hooks/h.py")
    return repo


@pytest.mark.parametrize("verb", ["merge", "rebase"])
def test_must_allow_continue_after_a_conflict_resolved_to_one_side(tmp_path, verb):
    assert bash("git %s --continue" % verb, _conflict(tmp_path, verb)).kind == "allow"


def test_must_allow_a_bare_am_of_a_missing_patch(tmp_path):
    assert bash("git am missing.mbox", make_repo(tmp_path)).kind == "allow"


def test_a_commit_in_an_unresolvable_repository_denies(tmp_path):
    """MaddManager's row: `git -C $UNSET commit` cannot be judged (v1 denied it at the scope check; v2 at the
    unproved-target check, the same deny)."""
    assert bash("git -C $UNSET commit -F msg", make_repo(tmp_path)).denied
