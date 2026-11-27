"""T1's worktree and clone deletion rule (`hooks/_delete_rule.py`): what a real git answers to each read the rule
makes, and the rule called the way the floor calls it.

`test_floor_retire_verdict.py` proves every verdict on planted facts, in process. This file holds those facts to
reality, and costs a repository each, so a case is here only for a read git itself answers: each read function's
positive and negative on a real repository, built by `_git_fixture` (no spawn) or by seed copy where git can be avoided,
and by git itself only for what git must decide (a submodule's clone, a cherry-pick's patch id, a worktree's status).

- `basket`: one worktree in every state a status line names; every line the verdict rows plant is one git prints
  (`test_every_planted_status_line_is_what_git_prints`).
- registration, branch and open-PR reads; the commits no remote holds (`Lanes`: one tier each, built with fast-import);
  a submodule's status, pushed state and recursion (`nested_root`), and every submodule status line the verdict rows
  plant (`test_every_planted_submodule_status_line_is_what_git_prints`); a submodule whose admin directory is gone and an
  orphan, both judged by the object store; a standalone clone under scratch;
- the rule through the floor (`rm` as the client sends it): each verdict class once, with its deny text.

v1 cases not carried, each with its subject gone (`test_floor_ported.md`): the saved copies (a bundle, tar or patch under
`.claude/scratch` that held a worktree's commits or files: a bundle standing in for a push asks the owner), the
`--retire-check` CLI (now `_delete_rule.py [--created] <path>...`), and the timeout environment variable. The ignored
files and stored edits of a worktree this session created (`own`, `--created`), untracked Godot `.import` files and
Godot import temp files stay allowed, as in v1 (b2 G5).
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import _delete_rule as rule
import _git_commit
import _git_fixture
import _own_worktree
import test_floor_retire_verdict as planted
from _hook_call import copy_repo
from case import bash
from conftest import git, write

PEER_SID = planted.PEER_SID
SUB = ("-c", "protocol.file.allow=always")


def rmtree(path):
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def decided(judge, *args, **kwargs):
    """`judge(*args)` as the floor calls it: one git read memo per decision (`pre_bash_dispatch.dispatch`). Each call
    is its own decision, so a row that changes the repository between calls sees it."""
    with _git_commit.memoized():
        return judge(*args, **kwargs)


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def seeded(root, name, files, branch="main"):
    """A source repository with one commit "seed", written in process (no `git init`, `add` or `commit` spawn). A tree
    `.gitignore` makes `commit_all` take git's own `add` and `commit`, so each seed costs at most two spawns and a
    plain one none."""
    repo = Path(root, name)
    _git_fixture.init_repo(repo, branch=branch)
    for rel, text in files.items():
        write(repo, rel, text)
    _git_fixture.commit_all(repo, "seed")
    return repo


# --- The project every row clones ---------------------------------------------------------------------------------------
SEED = {"a.txt": "a\n", "t2.txt": "t2\n", "t3.txt": "t3\n", "tools/bin/run.sh": "#!/bin/sh\n",
        ".gitignore": ".claude/worktrees/\n.claude/scratch/\n.claude/tmp/\n.godot/\n.devdb/\n.env.local\n"
                      "dist-preview/\nlogs/\ntest-results/\n"}
EXCLUDE = "/bin/\nobj/\n.agents/\n*.tmp\n*.TMP\n.search-index/\n"  # what a project's `.git/info/exclude` holds beside its .gitignore


@pytest.fixture(scope="module")
def project_tree(tmp_path_factory):
    """Git creates the relative submodule link and the checkout once; each project gets its own copy."""
    root = tmp_path_factory.mktemp("retire_project")
    sub_seed = seeded(root, "sub_seed", {"s.txt": "s\n"})
    tree = root / "tree"
    tree.mkdir()
    git(tree, "clone", "-q", "--bare", str(sub_seed), "sub_remote.git")
    proj_seed = seeded(tree, "proj_seed", SEED)
    git(proj_seed, *SUB, "submodule", "add", "-q", "../sub_remote.git", "Sub")
    git(proj_seed, "commit", "-qm", "sub")
    git(tree, "clone", "-q", "--bare", str(proj_seed), "proj_remote.git")
    project = tree / "proj"
    project.mkdir()
    git(project, "clone", "-q", "../proj_remote.git", ".")  # relative to the project, so a copy stays a pair
    with (project / ".git/info/exclude").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(EXCLUDE)
    (project / ".claude/worktrees").mkdir(parents=True)
    (project / ".claude/scratch").mkdir()
    return tree


class Project:
    """An isolated copy of the planted project and its two local remotes."""

    def __init__(self, root, template):
        self.base = Path(root)
        for name in ("proj", "proj_remote.git", "sub_remote.git"):
            copy_repo(template / name, self.base / name)
        self.root = self.base / "proj"
        self.home = self.root / ".claude" / "worktrees"
        self.scratch = self.root / ".claude" / "scratch"

    def wt(self, name, branch=None):
        path = self.home / name
        git(self.root, "worktree", "add", "-q", *(["-b", branch] if branch else ["--detach"]), str(path))
        return path

    def orphan(self, name, edit=None, where=None):
        """A worktree whose admin directory git pruned; `where` puts it beside the project."""
        path = Path(where, name) if where else self.home / name
        git(self.root, "worktree", "add", "-q", "--detach", str(path))
        if edit:
            edit(path)
        admin = (path / ".git").read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
        rmtree(admin if os.path.isabs(admin) else path / admin)
        return path

    def retired(self, token, cwd=None, own=False):
        reasons = []
        got = decided(rule.is_retired_worktree, str(token), str(cwd or self.root), str(self.root), reasons, own=own)
        assert got == (not reasons), (got, reasons)
        return got, reasons


@pytest.fixture(scope="module")
def world(tmp_path_factory, project_tree):
    return Project(tmp_path_factory.mktemp("retire"), project_tree)


@pytest.fixture
def at(world, tmp_path, monkeypatch):
    """The floor judging `world`: CLAUDE_PROJECT_DIR and the cwd point at it, and the system temp dir is a folder
    beside it, so the project is never a temp path (D-TT)."""
    elsewhere = tmp_path / "temp"
    elsewhere.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(elsewhere))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world.root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no_login"))
    monkeypatch.chdir(world.root)
    return world


def rm(command, world, transcript=None):
    return bash(command, world.root, transcript_path=transcript, session_id="retire-rows")


def msys(path):
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


def transcript_of(tmp_path, path, root):
    """A transcript in which this session ran `git worktree add <path>` and saw it succeed."""
    rows = [{"type": "assistant", "timestamp": "2000-01-01T00:00:00.000Z", "cwd": str(root),
             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "w0", "name": "Bash",
                         "input": {"command": "git worktree add " + path.as_posix() + " HEAD"}}]}},
            {"type": "user", "timestamp": "2100-01-01T00:00:00.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w0",
                         "content": "Preparing worktree"}]}}]
    transcript = tmp_path / "made.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _own_worktree.session_created_worktree(str(path), str(transcript))
    return transcript


# --- A worktree in every state a status line names -------------------------------------------------------------------
def make_basket(path):
    """Every state `git status --porcelain --untracked-files=all --ignored=matching` prints a line for, once."""
    write(path, "a.txt", "changed\n")
    (path / "t2.txt").unlink()
    write(path, "t3.txt", "staged edit\n")
    git(path, "add", "t3.txt")
    (path / "t3.txt").unlink()
    for rel in ("notes.md", "extra.txt", "icon.png.import", "icon.png.import.bak", "d/desktop.ini", "src/bin/tool.cs"):
        write(path, rel)
    write(path, "tools/bin/run.sh", "edited\n")
    for rel in (".godot/imported/icon.ctex", ".claude/scratch/%s/gate.out" % PEER_SID,
                ".claude/scratch/job_x/run/args.json", ".claude/tmp/run_x/out.log", ".env.local", "draft.tmp",
                "Visual/icon.png.import119908465.tmp", "Visual/icon.png.import~RF2c3d449d.TMP", "bin/a", "src/obj/b",
                ".agents/c", ".search-index/search.db", ".devdb/local/x", "dist-preview/index.html", "logs/screens/a.png", "test-results/r.txt"):
        write(path, rel)


@pytest.fixture(scope="module")
def basket(world):
    path = world.wt("basket")
    make_basket(path)
    status = git(path, "status", "--porcelain", "--untracked-files=all", "--ignored=matching")
    return path, status


def test_every_planted_status_line_is_what_git_prints(basket):
    """The verdict rows' plants are cut from this: a line they plant that git does not print for the state it names
    would prove a verdict on a status no worktree has."""
    path, status = basket
    printed = set(status.splitlines())
    lines = planted.planted_status_lines()
    assert lines and lines <= printed, sorted(lines - printed)


LISTED_PATTERNS = (".devdb/local", "dist-preview", "logs", "test-results")


def test_the_basket_loses_exactly_the_work(world, basket):
    """`losses` over a real status: what is work stays, what a tool regenerates, a cache or the project lists goes."""
    path, status = basket
    work = {" M a.txt", "MD t3.txt", "?? notes.md", "?? extra.txt", "?? icon.png.import.bak", "?? src/bin/tool.cs",
            " M tools/bin/run.sh", "!! .env.local", "!! draft.tmp", "!! .claude/scratch/"}
    unlisted = {"!! .devdb/", "!! dist-preview/", "!! logs/", "!! test-results/"}

    def lost(patterns=()):
        return {line.split(" (")[0] for line in rule.losses(status, path, patterns, world.root)}
    assert lost() == work | unlisted
    assert lost(LISTED_PATTERNS) == work


def test_a_status_with_losses_denies_through_the_floor_and_names_the_ignored_path(at, basket):
    path, _status = basket
    got = rm("rm -rf .claude/worktrees/basket", at)
    assert got.denied and "!! .claude/scratch" in got.reason and "another session's output folder" in got.reason


def test_an_ignored_folder_holding_no_file_is_one_status_line_that_loses_nothing(world):
    path = world.wt("emptied")
    (path / ".claude/scratch/moved_out").mkdir(parents=True)
    assert git(path, "status", "--porcelain", "--untracked-files=all", "--ignored=matching") == planted.SCRATCH
    assert world.retired(path)[0]


# --- Registration, branch and pushed state of a worktree -------------------------------------------------------------
def test_a_clean_pushed_worktree_retires_and_a_locked_one_does_not(world):
    clean, locked = world.wt("clean"), world.wt("locked")
    git(world.root, "worktree", "lock", str(locked))
    assert world.retired(clean)[0]
    got, reasons = world.retired(locked)
    assert not got and "worktree is locked" in said(reasons)


def said(reasons):
    return " | ".join(reasons)


@pytest.mark.parametrize("prs,want", [([(7, "feat/seven")], False), ([(8, "feat/eight")], True)])
def test_a_sync_branch_of_an_open_pr_keeps_its_checkout(world, prs, want):
    path = world.home / "sync-pr7"
    if not path.exists():
        world.wt("sync-pr7", branch="sync/pr7")
    with patch.object(rule, "_open_prs", lambda root: prs):
        assert world.retired(path)[0] is want


def test_the_verdict_cli_runs_from_its_command_line_and_exits_by_the_verdict(world):
    """The one spawned case: `_delete_rule.py <path>...` imports, judges and exits 0 retired, 1 blocked, 2 with no path.
    The output's every shape is `test_floor_retire_verdict.py`'s."""
    clean, dirty = world.wt("cli_clean"), world.wt("cli_dirty")
    write(dirty, "a.txt", "changed\n")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(world.root))
    cli = [sys.executable, str(Path(rule.__file__).resolve())]

    def run(*args):
        return subprocess.run(cli + list(args), cwd=str(world.root), env=env, capture_output=True, text=True)
    got = run(str(clean), str(dirty))
    lines = got.stdout.splitlines()
    assert got.returncode == 1 and lines[0] == "%s: retired" % clean
    assert lines[1].startswith("%s: blocked: worktree has local" % dirty)
    assert run().returncode == 2


def test_one_object_store_read_names_only_the_unstored_edits(world):
    """`unstored_losses` asks the store about every present dirty file at once and keeps each line's own answer."""
    path = world.wt("mixed_store")
    write(path, "a.txt", "a stored edit\n")
    git(path, "hash-object", "-w", "a.txt")
    write(path, "fresh.txt", "bytes no blob holds\n")
    write(path, "gone.txt", "staged then removed\n")
    git(path, "add", "gone.txt")
    (path / "gone.txt").unlink()
    status = git(path, "status", "--porcelain", "--untracked-files=all")
    assert set(planted.OWNED_STATUS.splitlines()) <= set(status.splitlines()), status
    with _git_commit.memoized():
        found = rule.unstored_losses(status, path)
    assert sorted(line[3:] for line in found) == ["fresh.txt"], found


# --- The commits no remote holds: one live read per tier ---------------------------------------------------------------
class Lanes:
    """A repository with a local `main`, a remote-tracking `origin/main` and lanes branching from them, written with
    one `git fast-import` per commit and no other spawn. HEAD names the lane under judgement; the rule reads commits,
    trees and refs, never the working tree."""

    def __init__(self, path):
        self.path = Path(path)
        _git_fixture.init_repo(self.path)
        self.git_dir = self.path / ".git"
        self.tip = {}
        self.commit("main", {"a.txt": "a\n"}, "seed", root=True)
        self.push()
        write(self.git_dir, "refs/remotes/origin/HEAD", "ref: refs/remotes/origin/main\n")

    def ref(self, name):
        return self.git_dir / Path(*name.split("/"))

    def commit(self, branch, files, subject, root=False):
        """One commit of `files` on `branch`, which exists once a commit is on it (`lane` makes one)."""
        sha = _git_fixture.history(self.git_dir, (files, subject), ref="refs/heads/" + branch, root=root)[0]
        self.tip[branch] = sha
        return sha

    def link_commit(self, branch, links, subject, files=None):
        """One commit on `branch` that sets submodule pointers (a gitlink is a tree entry `history` cannot write)."""
        ref = ("refs/heads/" + branch).encode()
        message = subject.encode()
        stream = [b"commit %s\ncommitter t <t@example.invalid> %d +0000\ndata %d\n%s\nfrom %s^0\n"
                  % (ref, int(time.time()), len(message), message, ref)]
        for path, text in (files or {}).items():
            data = text.encode()
            stream.append(b"M 100644 inline %s\ndata %d\n%s\n" % (path.encode(), len(data), data))
        stream += [b"M 160000 %s %s\n" % (sha.encode(), path.encode()) for path, sha in links.items()]
        done = subprocess.run(["git", "--git-dir", str(self.git_dir), "fast-import", "--quiet"],
                              input=b"".join(stream), capture_output=True)
        assert done.returncode == 0, done.stderr
        self.tip[branch] = (self.git_dir / Path(*("refs/heads/" + branch).split("/"))).read_text().strip()

    def lane(self, name, at="main"):
        write(self.git_dir, "refs/heads/" + name, self.tip[at] + "\n")
        self.tip[name] = self.tip[at]

    def push(self, branch="main"):
        write(self.git_dir, "refs/remotes/origin/" + branch, self.tip[branch] + "\n")

    def pushed(self, lane, **kw):
        """Whether the rule judges `lane`'s commits pushed or landed on a default branch."""
        write(self.git_dir, "HEAD", "ref: refs/heads/%s\n" % lane)
        reasons = []
        with _git_commit.memoized():
            return rule._pushed(self.path, reasons, local_default=True, **kw), reasons


@pytest.fixture(scope="module")
def lanes(tmp_path_factory):
    return Lanes(tmp_path_factory.mktemp("lanes") / "repo")


def test_a_commit_main_holds_by_its_patch_retires_and_one_no_branch_holds_does_not(lanes):
    """The patch tier: a cherry-pick main then edits again is held (the file differs, so only the patch id holds it).
    A lane nothing holds is not, and neither is one another local branch holds."""
    lanes.lane("drifted")
    lanes.commit("drifted", {"d.txt": "branch edit\n"}, "branch edit")
    lanes.commit("main", {"d.txt": "branch edit\n"}, "landed by cherry-pick")
    lanes.commit("main", {"d.txt": "main edited it again\n"}, "main moves on")
    assert lanes.pushed("drifted")[0]
    lanes.lane("ahead")
    lanes.commit("ahead", {"z.txt": "only here\n"}, "only here")
    lanes.lane("elsewhere", at="ahead")
    got, reasons = lanes.pushed("ahead")
    assert not got and "is on no remote branch" in reasons[0]


def test_squashed_commits_retire_and_so_do_squashed_commits_main_then_rewrote(lanes):
    """The squash tier (no file the lane changed differs from main), then the history tier (one commit on main carries
    the lane's content for every path though main moved one of them on)."""
    lanes.lane("squashed")
    lanes.commit("squashed", {"q1.txt": "one\n"}, "q1")
    lanes.commit("squashed", {"q2.txt": "two\n"}, "q2")
    lanes.commit("main", {"q1.txt": "one\n", "q2.txt": "two\n"}, "squash of scratch/squashed")
    assert lanes.pushed("squashed")[0]
    lanes.lane("held_content")
    lanes.commit("held_content", {"h.txt": "v2\n"}, "branch h v2")
    lanes.commit("held_content", {"z2.txt": "z\n"}, "branch z")
    lanes.commit("main", {"h.txt": "v2\n", "z2.txt": "z\n"}, "land h and z in one commit")
    lanes.commit("main", {"h.txt": "v3\n"}, "main moves h past it")
    assert lanes.pushed("held_content")[0]


def test_a_commit_reapplied_on_a_moved_line_is_held_and_a_partial_one_is_not(lanes):
    lanes.commit("main", {"ctx.txt": "one\n"}, "ctx base")
    lanes.lane("reapply")
    lanes.lane("reapply_partial")
    lanes.commit("main", {"ctx.txt": "one\nmain\n"}, "main owns the next line")
    lanes.commit("reapply", {"ctx.txt": "one\nshared fix\n"}, "branch adds the fix")
    lanes.commit("main", {"ctx.txt": "one\nmain\nshared fix\n"}, "main adds the same fix on its line")
    assert lanes.pushed("reapply")[0]
    lanes.lane("partial")
    lanes.commit("partial", {"ctx.txt": "one\nmain\nshared fix\nan extra line\n"}, "branch adds an extra line")
    assert not lanes.pushed("partial")[0]


def test_a_row_main_holds_beside_its_own_edit_merges_to_nothing(lanes):
    lanes.commit("main", {"lock.txt": "A\nB\nC\nD\nE\n"}, "lock base")
    lanes.lane("merged_elsewhere")
    lanes.commit("merged_elsewhere", {"lock.txt": "A\nB\nROW\nC\nD\nE\n"}, "branch adds a row")
    lanes.commit("main", {"lock.txt": "A\nB\nROW\nC\nD\nE2\n"}, "main adds the row and edits E")
    assert lanes.pushed("merged_elsewhere")[0]


@pytest.mark.parametrize("vanish,want", [("local default", True), ("remote default", False)])
def test_a_vanished_default_ref_decides_from_the_refs_that_remain(tmp_path, vanish, want):
    """A lane cherry-picked onto `origin/main` only. With the local default branch gone, the remote's is still scanned
    (the shared ref read fails and each ref is read alone); with `origin/HEAD` naming a ref that is gone, nothing
    holds the lane."""
    repo = Lanes(tmp_path / "repo")
    repo.lane("lane")
    repo.commit("lane", {"lane.txt": "lane\n"}, "lane")
    repo.lane("landing")
    repo.commit("landing", {"lane.txt": "lane\n"}, "lane, landed")
    repo.push("landing")
    write(repo.git_dir, "refs/remotes/origin/main", repo.tip["landing"] + "\n")
    if vanish == "local default":
        repo.ref("refs/heads/main").unlink()
    else:
        write(repo.git_dir, "refs/remotes/origin/HEAD", "ref: refs/remotes/origin/gone\n")
    assert repo.pushed("lane")[0] is want


def test_the_remote_defaults_patch_scan_runs_unless_its_history_lies_inside_the_local_ones(tmp_path):
    """A lane whose patch only a diverged `origin/main` holds, past the history windows (narrowed to one commit
    here), so only the patch scan can hold it: the remote's history is not inside the local default's, so its scan
    runs."""
    repo = Lanes(tmp_path / "repo")
    repo.lane("lane")
    repo.commit("lane", {"a.txt": "lane\n"}, "lane")
    repo.lane("landing")
    repo.commit("landing", {"a.txt": "lane\n"}, "lane, landed")
    repo.commit("landing", {"a.txt": "rewritten\n"}, "main rewrites a.txt")
    repo.push("landing")
    repo.commit("main", {"local.txt": "local only\n"}, "local main moves on its own")
    write(repo.git_dir, "refs/remotes/origin/main", repo.tip["landing"] + "\n")
    with patch.object(rule, "_HISTORY_SCAN", 1), patch.object(rule, "_REAPPLY_SCAN", 1):
        assert repo.pushed("lane")[0]


@pytest.mark.parametrize("landed_pointer,want", [("same", True), ("other", False)])
def test_a_submodule_pointer_landed_with_its_content_is_held_by_history(tmp_path, landed_pointer, want):
    """A lane's two commits (a new file, a submodule pointer) land as one commit main then moves past, so only the
    history scan can hold them; a landed pointer that differs holds nothing. A commit made after a held decision is
    the next decision's HEAD."""
    repo = Lanes(tmp_path / "repo")
    repo.lane("pointer")
    pointer = repo.commit("pointer", {"h.txt": "v2\n"}, "lane h")
    repo.link_commit("pointer", {"Sub": pointer}, "lane pointer")
    repo.link_commit("main", {"Sub": pointer if landed_pointer == "same" else repo.tip["main"]},
                     "land h and the pointer", {"h.txt": "v2\n"})
    repo.commit("main", {"h.txt": "v3\n"}, "main moves h past it")
    assert repo.pushed("pointer")[0] is want
    if want:
        repo.commit("pointer", {"later.txt": "after the decision\n"}, "lane moves on")
        assert not repo.pushed("pointer")[0]


def test_a_submodule_head_pushed_from_the_main_checkout_counts(tmp_path):
    """A worktree's submodule clone keeps stale remote refs; the main checkout's same submodule sees the push."""
    main, clone = tmp_path / "main_sub", tmp_path / "wt_sub"
    for repo in (main, clone):
        _git_fixture.init_repo(repo)
    write(main, "s.txt", "s\n")
    first = _git_fixture.commit_all(main, "first")
    write(main, "s2.txt", "pushed from the main checkout\n")
    second = _git_fixture.commit_all(main, "pushed later")
    write(main / ".git", "refs/remotes/origin/main", second + "\n")
    shutil.copytree(main / ".git" / "objects", clone / ".git" / "objects", dirs_exist_ok=True)
    write(clone / ".git", "refs/remotes/origin/main", first + "\n")
    write(clone / ".git", "HEAD", second + "\n")
    reasons = []
    assert not decided(rule._pushed, clone, reasons)  # alone, the stale ref holds nothing
    assert reasons and "is on no remote branch" in reasons[0]
    reasons = []
    assert decided(rule._pushed, clone, reasons, "submodule Sub: ", also=main) and not reasons


# --- Submodules ----------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def nested_root(tmp_path_factory):
    """A project whose `child` submodule holds a `grand` submodule, with a worktree whose submodules git itself
    initialized, and a second worktree whose submodules are not."""
    base = tmp_path_factory.mktemp("states")
    grand = seeded(base, "grand-source", {"f.txt": "f\n", ".gitignore": "*.local\n.cache/\nempty/\n"})
    child, root = base / "child-source", base / "project"
    for dest in (child, root):
        copy_repo(grand, dest)
    git(child, *SUB, "submodule", "add", "-q", str(grand), "grand")
    git(child, "commit", "-qam", "grandchild")
    git(root, *SUB, "submodule", "add", "-q", str(child), "child")
    git(root, "commit", "-qam", "child")
    git(root, "update-ref", "refs/remotes/proof/main", "HEAD")
    (root / ".claude" / "worktrees").mkdir(parents=True)
    live, bare = root / ".claude/worktrees/live", root / ".claude/worktrees/uninitialized"
    git(root, "worktree", "add", "-q", "--detach", str(live), "HEAD")
    git(live, *SUB, "submodule", "update", "-q", "--init", "--recursive")
    git(root, "worktree", "add", "-q", "--detach", str(bare), "HEAD")
    return root, live, bare


def judged(root, path, own=False):
    reasons = []
    got = decided(rule.is_retired_worktree, str(path), str(root), str(root), reasons, own=own)
    assert got == (not reasons), reasons
    return got, said(reasons)


def test_the_recursion_over_submodules_reads_each_populated_one_and_fails_on_an_unmapped_gitlink(nested_root):
    """`_populated_submodules` (the index in place of `git submodule status --recursive`): every populated submodule
    with its own, none for an uninitialized one, and None for a gitlink `.gitmodules` does not map, at any depth."""
    root, live, bare = nested_root
    with _git_commit.memoized():
        assert rule._populated_submodules(live) == ["child", "child/grand"]
        assert rule._populated_submodules(bare) == []
    for holder in (live, live / "child"):
        git(holder, "update-index", "--add", "--cacheinfo", "160000,%s,Stray" % head(live))
        try:
            with _git_commit.memoized():
                assert rule._populated_submodules(live) is None
        finally:
            git(holder, "update-index", "--force-remove", "Stray")


def test_a_submodule_two_deep_is_judged_by_its_own_status_and_pushed_state(nested_root):
    """The read of a nested submodule's status and of its pushed state: an untracked file, an edit and a commit no
    remote holds keep the worktree."""
    root, live, _bare = nested_root
    assert judged(root, live)[0]  # clean and pushed, every level
    child, grand = live / "child", live / "child/grand"
    write(grand, "n.txt")
    write(child, "f.txt", "e\n")
    got, said_ = judged(root, live)
    assert not got and "submodule child/grand has local files or changes" in said_ \
        and "submodule child has local files or changes" in said_
    (grand / "n.txt").unlink()
    git(child, "checkout", "-q", "--", "f.txt")
    write(grand, "local.txt", "l\n")
    git(grand, "add", "local.txt")
    git(grand, "commit", "-qm", "local only")
    got, said_ = judged(root, live)
    assert not got and "submodule child/grand: HEAD" in said_
    git(grand, "reset", "-q", "--hard", "HEAD~1")


def test_an_ignored_file_in_a_nested_submodule_is_a_status_line_that_loses_work_unless_this_session_made_it(nested_root):
    """v1 test_conformance_j8.py v23 (kept by the port: b2 G5): a unique ignored file loses work, an ignored cache or
    empty folder does not; with `own` the ignored file goes too. The verdicts are planted; this is the status git
    prints for each."""
    _root, live, _bare = nested_root
    for sub_ in (live / "child", live / "child/grand"):
        secret = write(sub_, "secret.local", "irreplaceable fixture evidence")
        status = git(sub_, "status", "--porcelain", "--untracked-files=all", "--ignored=matching")
        assert "!! secret.local" in status.splitlines()
        assert rule.losses(status, sub_) and not rule.losses(status, sub_, own=True)
        secret.unlink()
        write(sub_, ".cache/generated", "cache")
        (sub_ / "empty").mkdir()
        status = git(sub_, "status", "--porcelain", "--untracked-files=all", "--ignored=matching")
        assert "!! .cache/" in status.splitlines() and not rule.losses(status, sub_)


def test_every_planted_submodule_status_line_is_what_git_prints(nested_root):
    """The verdict rows' submodule plants are cut from this: each state they name, made in a submodule one and two deep,
    prints the line they plant."""
    _root, live, _bare = nested_root
    printed = set()
    for sub_ in (live / "child", live / "child/grand"):
        made = [write(sub_, "n.txt"), write(sub_, "secret.local", "s\n"), write(sub_, ".cache/generated", "cache")]
        write(sub_, "f.txt", "e\n")
        try:
            printed |= set(git(sub_, "status", "--porcelain", "--untracked-files=all", "--ignored=matching").splitlines())
        finally:
            for path in made:
                path.unlink()
            (sub_ / ".cache").rmdir()
            git(sub_, "checkout", "-q", "--", "f.txt")
    lines = planted.planted_submodule_lines()
    assert lines and lines <= printed, sorted(lines - printed)


@pytest.fixture(scope="module")
def stored(tmp_path_factory, project_tree):
    """A project whose main checkout holds the submodule's store (`.git/modules/Sub`), which ignores `*.local`."""
    world = Project(tmp_path_factory.mktemp("admin_gone"), project_tree)
    git(world.root, *SUB, "submodule", "update", "-q", "--init")
    with open(world.root / ".git/modules/Sub/info/exclude", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("*.local\n")
    return world


def with_submodule_files(stored_, path, gone):
    """The submodule's files copied from the main checkout into `path`, its `.git` naming a missing admin directory
    when `gone`."""
    sub = path / "Sub"
    sub.mkdir(exist_ok=True)
    for entry in (stored_.root / "Sub").iterdir():
        if entry.is_file() and entry.name != ".git":
            shutil.copy2(entry, sub / entry.name)
    if gone:
        write(sub, ".git", "gitdir: ../.git/worktrees/gone/modules/Sub\n")


def test_a_submodule_whose_admin_directory_is_gone_is_judged_by_the_main_checkouts_store(stored):
    """The admin directory's commits are gone before the delete, so the files are all it can still lose, judged
    against the store: a file a blob holds goes, one no blob holds and an ignored one do not (the latter goes when this
    session created the worktree)."""
    path = stored.wt("held_ag")
    with_submodule_files(stored, path, gone=True)
    assert stored.retired(path)[0]
    unique = write(path / "Sub", "n.txt", "only here\n")
    got, reasons = stored.retired(path)
    assert not got and any("n.txt" in r and "held_ag.Sub.tar" in r for r in reasons), reasons
    unique.unlink()
    write(path / "Sub", "x.local", "local evidence\n")
    assert not stored.retired(path)[0] and stored.retired(path, own=True)[0]


# --- Orphans: nothing git holds is lost --------------------------------------------------------------------------------
def test_an_orphan_goes_only_when_every_file_is_a_blob_or_a_regenerable_entry(world):
    """The object store read: `hash-object` then `cat-file` for each file the empty index leaves untracked, and the
    ignored files one by one. An orphan (its admin directory pruned, or its `.git` gone) that holds only blobs and
    regenerable output goes; one that holds a file no blob holds names it, and an ignored file no cache holds names why
    ownership cannot clear it."""
    outputs = ("bin/a", "src/obj/b", ".agents/c", "d/desktop.ini", ".godot/imported/c.ctex")
    clean = world.orphan("o_clean", edit=lambda p: [write(p, rel) for rel in outputs] and (p / "empty_dir").mkdir())
    assert world.retired(clean)[0]
    gitless = world.orphan("g_clean")
    (gitless / ".git").unlink()
    assert world.retired(gitless)[0]

    def losses(p):
        write(p, "notes.md", "unsaved notes\n")
        write(p, "a.txt", "edited, never committed\n")
        write(p, "src/bin/tool.cs")
        write(p, "tools/bin/run.sh", "edited\n")
        write(p, ".claude/scratch/%s/x.json" % PEER_SID, "{}\n")
        write(p, ".devdb/local/default/backend.sqlite3")
    dirty = world.orphan("o_dirty", edit=losses)
    got, reasons = world.retired(dirty)
    text = said(reasons)
    assert not got and all(n in text for n in ("notes.md", "a.txt", "src/bin/tool.cs", "run.sh", "x.json", "backend.sqlite3"))
    assert rule.ORPHAN_OWNERSHIP in reasons and text.count("x.json") == 1
    assert not world.retired(dirty, own=True)[0]


def test_an_orphan_beside_the_project_is_this_repositorys_only_when_its_admin_directory_is(world, tmp_path, project_tree):
    siblings = tmp_path / "siblings"
    siblings.mkdir()
    assert world.retired(world.orphan("s_clean", where=siblings))[0]
    other = Project(tmp_path / "other_project", project_tree)
    assert not world.retired(other.orphan("s_other", where=siblings))[0]


def test_an_orphan_with_a_submodule_is_judged_by_the_main_checkouts_store(stored):
    """Each file of a populated submodule must be a blob in the main checkout's `.git/modules/Sub` store."""
    path = stored.orphan("o_sub")
    with_submodule_files(stored, path, gone=False)
    assert stored.retired(path)[0]
    write(path / "Sub", "s.txt", "edited\n")
    assert not stored.retired(path)[0]


# --- A standalone clone under scratch ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def clones(world):
    """One pushed clone, copied per state; a state changes its copy by one git call."""
    seed = seeded(world.base, "seed", {"a.txt": "a\n"})
    remote = world.base / "remote.git"
    git(world.base, "clone", "-q", "--bare", str(seed), str(remote))
    template = world.base / "clone_template"
    git(world.base, "clone", "-q", str(remote), str(template))
    fix = world.scratch / "rc_fix"
    fix.mkdir()
    built = {"fix": fix}
    for name in ("clean", "stashed", "ahead", "tagged", "tagged_local", "no_remote", "m"):
        built[name] = fix / name
        shutil.copytree(template, built[name])
    write(built["stashed"], "a.txt", "stash me\n")
    git(built["stashed"], "stash", "-q")
    write(built["ahead"], "b.txt", "b\n")
    git(built["ahead"], "add", "b.txt")
    git(built["ahead"], "commit", "-qm", "local only")
    git(built["tagged"], "tag", "-a", "local-tag", "-m", "t")
    write(built["tagged_local"], "c.txt", "c\n")
    git(built["tagged_local"], "add", "c.txt")
    git(built["tagged_local"], "commit", "-qm", "tagged local")
    git(built["tagged_local"], "tag", "local-only-tag")
    git(built["tagged_local"], "reset", "-q", "--hard", "HEAD~1")
    git(built["no_remote"], "remote", "remove", "origin")
    git(built["m"], "worktree", "add", "-q", str(fix / "w"))
    return built


@pytest.mark.parametrize("name,want,reason", [
    ("clean", True, None), ("tagged", True, None), ("stashed", False, "stash"),
    ("ahead", False, "no remote branch"), ("tagged_local", False, "no remote branch"),
    ("no_remote", False, "no remote"), ("m", False, "linked worktrees"),
])
def test_a_scratch_clone_goes_only_when_clean_and_pushed(world, clones, name, want, reason):
    why = []
    assert decided(rule.is_regenerable_clone, str(clones[name]), str(world.root), str(world.root), why) is want
    if reason:
        assert len(why) == 1 and reason in why[0], why


def test_a_repository_in_a_jobs_tmp_is_judged_by_what_git_holds_in_it(world):
    """Real repositories under `.claude/tmp`: an unborn one and a proof's empty-commit fixture lose nothing (the empty
    tree id is git's), a repository with a committed file loses it."""
    path = world.base / "tmp_repos"
    unborn = path / ".claude/tmp/run_x/probe"
    _git_fixture.init_repo(unborn)
    fixture = path / ".claude/tmp/pe_absent_delete_dxf68feq/.claude/scratch/probe"
    _git_fixture.init_repo(fixture)
    git(fixture, "commit", "-q", "--allow-empty", "-m", "unpushed")
    with _git_commit.memoized():
        assert rule.scratch_problem(".claude/tmp", path, world.root) is None
    work = path / ".claude/tmp/pe_absent_delete_dxf68feq/repo"
    _git_fixture.init_repo(work)
    write(work, "work.txt")
    _git_fixture.commit_all(work, "only here")
    with _git_commit.memoized():
        why = rule.scratch_problem(".claude/tmp", path, world.root)
    assert why and "no remote" in why


def commit_file(path, rel, text):
    write(path, rel, text)
    git(path, "add", rel)
    git(path, "commit", "-qm", "only here: " + rel)


def test_a_bundle_standing_in_for_a_push_holds_a_worktree_head_and_a_clone_commit_only_while_it_lists_them(world,
                                                                                                           clones):
    """`_saved_copy_holds` through `git bundle verify` and `list-heads`: a bundle named for the worktree that lists its
    HEAD retires it, one made before a later commit no longer does; a scratch clone's unpushed commit goes with a
    bundle of its refs. (The tar half is planted; this is the git half.)"""
    saved = world.scratch / "saved"
    saved.mkdir(exist_ok=True)
    path = world.wt("sv_head")
    commit_file(path, "b.txt", "unpushed lane work\n")
    got, reasons = world.retired(path)
    assert not got and "bundle" in said(reasons)
    git(path, "bundle", "create", "-q", str(saved / "sv_head.bundle"), "--all")
    assert world.retired(path)[0]
    commit_file(path, "c.txt", "after the bundle\n")
    got, reasons = world.retired(path)
    assert not got and "bundle" in said(reasons)
    clone = world.scratch / "sv_clone"
    shutil.copytree(clones["clean"], clone)
    commit_file(clone, "n.txt", "unpushed\n")
    root = str(world.root)
    assert not decided(rule.is_regenerable_clone, str(clone), root, root)
    git(clone, "bundle", "create", "-q", str(saved / "sv_clone.bundle"), "--all")
    assert decided(rule.is_regenerable_clone, str(clone), root, root)


def test_a_nested_repository_in_scratch_prints_the_status_lines_the_tar_rows_plant(tmp_path):
    nested = tmp_path / "copy"
    _git_fixture.init_repo(nested)
    write(nested, "kept.txt", "kept work\n")
    write(nested, "side.txt", "side work\n")
    git(nested, "add", "kept.txt")
    printed = set(git(nested, "status", "--porcelain", "--untracked-files=all", "--ignored=matching").splitlines())
    assert planted.NESTED_LINES <= printed


def test_a_submodule_path_holding_a_space_is_read_whole(tmp_path):
    """D18: v1 split `submodule status` on whitespace and skipped such a path unchecked. The two reads the rule parses,
    in the shapes the verdict rows plant: `config -z --get-regexp` rows `key NL value NUL`, `ls-files --stage -z`."""
    repo = tmp_path / "repo"
    _git_fixture.init_repo(repo)
    write(repo, ".gitmodules", '[submodule "Sub Mod"]\n\tpath = Sub Mod\n\turl = ../sub_remote.git\n')
    seed = _git_fixture.commit_all(repo, "seed")
    git(repo, "update-index", "--add", "--cacheinfo", "160000,%s,Sub Mod" % seed)
    write(repo / "Sub Mod", ".git", "gitdir: nowhere\n")
    with _git_commit.memoized():
        assert rule._git(rule._GITMODULES_PATHS, repo) == "submodule.Sub Mod.path\nSub Mod\0"
        assert rule._git(["ls-files", "--stage", "-z"], repo).endswith("160000 %s 0\tSub Mod\0" % seed)
        assert rule._declared_submodules(repo) == ["Sub Mod"]


# --- The rule through the floor ------------------------------------------------------------------------------------------
def test_rm_of_a_worktree_through_the_floor(at):
    clean, dirty = at.wt("f_wt_clean"), at.wt("f_wt_dirty")
    write(dirty, "a.txt", "changed\n")
    assert rm("rm -rf .claude/worktrees/f_wt_clean", at).kind == "allow"
    assert rm("rm -rf .claude/worktrees/f_wt_dirty", at).denied
    if os.name == "nt":
        assert rm("rm -rf " + msys(clean), at).kind == "allow"
        assert rm("rm -rf " + msys(dirty), at).denied


def test_a_worktree_this_session_created_takes_its_ignored_files_and_stored_edits(at, tmp_path):
    """CLAUDE.core.md §Shell Discipline: a worktree this session provably created takes its ignored files with it, and
    a dirty path whose content is already a blob in the store loses nothing (v1's rule; v2's T1 made it an ask, the
    port keeps the allow: b2 G5). The same worktree without the creation proof still denies, and ownership lets stored
    content go, never bytes the object store lacks."""
    path = at.wt("ch_made")
    write(path, ".claude/scratch/notes/x.out", "x\n")
    write(path, "a.txt", "a saved edit\n")
    git(path, "hash-object", "-w", "a.txt")
    transcript = transcript_of(tmp_path, path, at.root)
    assert rm("rm -rf .claude/worktrees/ch_made", at, None).denied
    assert rm("rm -rf .claude/worktrees/ch_made", at, transcript).kind == "allow"
    write(path, "a.txt", "an edit no blob holds\n")
    got = rm("rm -rf .claude/worktrees/ch_made", at, transcript)
    assert got.denied and "a.txt" in got.reason


def test_orphans_through_the_floor(at, tmp_path):
    clean = at.orphan("f_o_clean")
    at.orphan("f_o_json", edit=lambda p: write(p, ".claude/scratch/%s/x.json" % PEER_SID))
    sibling = tmp_path / "siblings"
    sibling.mkdir()
    at.orphan("f_s_new", where=sibling, edit=lambda p: write(p, "notes.md", "unsaved\n"))
    assert rm("rm -rf .claude/worktrees/f_o_clean", at).kind == "allow"
    assert rm("rm -rf .claude/worktrees/f_o_json", at).denied
    got = rm("rm -rf " + (sibling / "f_s_new").as_posix(), at)
    assert got.denied and "notes.md" in got.reason
    assert clean.is_dir()


def test_scratch_clones_through_the_floor(at, clones):
    def rel(name):
        return os.path.relpath(clones[name], at.root).replace("\\", "/")
    for command, want in (("rm -rf " + rel("clean"), "allow"), ("rm -rf " + rel("ahead"), "deny"),
                          ("rm -rf " + os.path.relpath(clones["fix"], at.root).replace("\\", "/"), "deny")):
        assert rm(command, at).kind == want, command


def test_a_clone_inside_a_jobs_scratch_goes_through_the_floor_by_the_clone_rule(at, clones):
    ok, bad = at.wt("f_nested_ok"), at.wt("f_nested_bad")
    shutil.copytree(clones["clean"], ok / ".claude/scratch/pubcopy")
    shutil.copytree(clones["ahead"], bad / ".claude/scratch/pubcopy")
    assert rm("rm -rf .claude/worktrees/f_nested_ok", at).kind == "allow"
    got = rm("rm -rf .claude/worktrees/f_nested_bad", at)
    assert got.denied and "holds a git repository that loses work" in got.reason
    assert rm("rm -rf .claude/worktrees/f_nested_ok/.claude/scratch/pubcopy", at).kind == "allow"
    assert rm("rm -rf .claude/worktrees/f_nested_bad/.claude/scratch/pubcopy", at).denied


# --- Ownership of a backgrounded add: the one read that is git's ---------------------------------------------------------
def test_a_worktree_the_session_added_is_owned_only_when_git_says_it_is_a_linked_worktree(world, tmp_path):
    """`session_created_worktree` reads `rev-parse --show-toplevel --absolute-git-dir --git-common-dir`: a linked
    worktree's git dir differs from the common one. The transcript rows around that read are
    `test_floor_retire_verdict.py`'s."""
    path = world.wt("owned")
    transcript = transcript_of(tmp_path, path, world.root)
    assert _own_worktree.session_created_worktree(str(path), str(transcript))
    assert not _own_worktree.session_created_worktree(str(world.root), str(transcript))
