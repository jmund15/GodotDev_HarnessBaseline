"""T1's worktree and clone deletion rule (`hooks/_delete_rule.py`), ported from v1's three-part
`test_regenerable_clone.py` (both repos) and the two retirement cases of `test_conformance_j8.py`.

Rows call the rule directly (`is_retired_worktree`, `is_regenerable_clone`, `lossless_orphan_worktree`) or the floor
the way the client does (`case.bash`). Each group shares one fixture project per module and each row builds its own
uniquely named worktree or clone in it, so no row reads another's state; a row that changes the project's shared
state (its submodule store, its `adaptation.json`) builds its own project.

v1 cases not carried, each with its subject gone (`test_floor_ported.md`): the saved copies (a bundle, tar or patch
under `.claude/scratch` that held a worktree's commits or files), the ignored files and stored edits of a worktree
this session created (`allow_ignored`, `--created`), untracked Godot `.import` files and Godot import temp files,
the `--retire-check` CLI (now `_delete_rule.py <path>...`), and the timeout environment variable. T1 lists these
allows as lost: each now asks the owner once. Their deny cases are kept below in their no-saved-copy form.
"""
import atexit
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import _delete_rule as rule
import _own_worktree
from case import bash
from conftest import git, write

PEER_SID = "5eed5eed-aaaa-4bbb-8ccc-0000000000ff"
SUB = ("-c", "protocol.file.allow=always")


def rmtree(path):
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def head(repo):
    return git(repo, "rev-parse", "HEAD").strip()


def seeded(root, name, files, branch="main"):
    repo = Path(root, name)
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", branch)
    for rel, text in files.items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    return repo


def project_remote(root):
    """A bare remote of a fixture project carrying a submodule (`git worktree remove` refuses every worktree that
    carries one, so a retired worktree goes by a recursive delete). Built once per session in a template folder and
    copied per project (the submodule URL is relative, so a copied pair stays a pair); a row that pushes changes only
    its own copy."""
    if not _TEMPLATE:
        _TEMPLATE.append(Path(tempfile.mkdtemp(prefix="retire_remotes_")))
        atexit.register(rmtree, _TEMPLATE[0])
        _build_remotes(_TEMPLATE[0])
    for name in ("sub_remote.git", "proj_remote.git", "proj_seed"):
        shutil.copytree(_TEMPLATE[0] / name, Path(root, name))
    return Path(root, "proj_remote.git")


_TEMPLATE = []


def _build_remotes(root):
    sub_seed = seeded(root, "sub_seed", {"s.txt": "s\n"})
    sub_remote = Path(root, "sub_remote.git")
    git(root, "clone", "-q", "--bare", str(sub_seed), str(sub_remote))
    proj_seed = seeded(root, "proj_seed", {
        "a.txt": "a\n", ".gitignore": ".claude/worktrees/\n.claude/scratch/\n.claude/tmp/\n.godot/\n.devdb/\n"
                                      ".env.local\ndist-preview/\nlogs/\ntest-results/\n"})
    git(proj_seed, *SUB, "submodule", "add", "-q", "../sub_remote.git", "Sub")
    git(proj_seed, "commit", "-qm", "sub")
    git(root, "clone", "-q", "--bare", str(proj_seed), str(Path(root, "proj_remote.git")))


class Project:
    """A clone of `project_remote` with `.claude/worktrees/` and `.claude/scratch/` homes."""

    def __init__(self, root, remote=None):
        self.base = Path(root)
        self.remote = remote or project_remote(self.base)
        self.root = self.base / "proj"
        git(self.base, "clone", "-q", str(self.remote), str(self.root))
        self.home = self.root / ".claude" / "worktrees"
        self.home.mkdir(parents=True)
        self.scratch = self.root / ".claude" / "scratch"
        self.scratch.mkdir(parents=True)

    def wt(self, name, submodule=False, branch=None):
        path = self.home / name
        git(self.root, "worktree", "add", "-q", *(["-b", branch] if branch else ["--detach"]), str(path))
        if submodule:
            git(path, *SUB, "submodule", "update", "-q", "--init")
        return path

    def job(self, name, *edits):
        """A worktree on `scratch/<name>` with one commit per `(file, text)` edit."""
        path = self.wt(name, branch="scratch/" + name)
        for i, (rel, text) in enumerate(edits):
            write(path, rel, text)
            git(path, "add", rel)
            git(path, "commit", "-qm", "%s %d" % (name, i))
        return path

    def orphan(self, name, submodule=False, edit=None, where=None):
        """A worktree whose admin directory git pruned; `where` puts it beside the project."""
        path = Path(where, name) if where else self.home / name
        git(self.root, "worktree", "add", "-q", "--detach", str(path))
        if submodule:
            git(path, *SUB, "submodule", "update", "-q", "--init")
        if edit:
            edit(path)
        admin = (path / ".git").read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
        rmtree(admin if os.path.isabs(admin) else path / admin)
        return path

    def clone(self, name, remote, under=None):
        path = Path(under or self.scratch, name)
        git(self.base, "clone", "-q", str(remote), str(path))
        return path

    def retired(self, token, cwd=None):
        reasons = []
        got = rule.is_retired_worktree(str(token), str(cwd or self.root), str(self.root), reasons)
        assert got == (not reasons), (got, reasons)
        return got, reasons


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return Project(tmp_path_factory.mktemp("retire"))


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


def peer_login(tmp_path, monkeypatch, *rows):
    """A login whose `sessions/<pid>.json` registry holds one live session per `(name, sessionId, cwd)` row."""
    login = tmp_path / "login"
    (login / "sessions").mkdir(parents=True)
    for pid, (peer, sid, cwd) in enumerate(rows, 4000):
        (login / "sessions" / ("%d.json" % pid)).write_text(json.dumps(
            {"pid": pid, "sessionId": sid, "name": peer, "status": "idle", "kind": "interactive", "cwd": str(cwd)}),
            encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(login))


def msys(path):
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")


# --- Linked worktrees: the retired-worktree cases ----------------------------------------------------------------
def test_a_clean_pushed_worktree_retires_in_every_token_form(world):
    clean = world.wt("clean")
    assert world.retired(".claude/worktrees/clean")[0]
    assert world.retired(clean)[0]
    if os.name == "nt":
        assert world.retired(msys(clean))[0]


@pytest.mark.parametrize("name,setup", [
    ("with_sub", lambda w, p: None),
    ("cache", lambda w, p: write(p, ".godot/imported/icon.ctex", "c\n")),
    ("emptied", lambda w, p: (p / ".claude/scratch/moved_out").mkdir(parents=True)),
])
def test_regenerable_entries_retire(world, name, setup):
    path = world.wt(name, submodule=name == "with_sub")
    setup(world, path)
    assert world.retired(path)[0]


@pytest.mark.parametrize("name,setup,reason", [
    ("evidence", lambda p: write(p, ".claude/scratch/%s/gate.out" % PEER_SID, "VERDICT=PASS\n"),
     "another session's output folder"),
    ("untracked", lambda p: write(p, "notes.md", "n\n"), "?? notes.md"),
    ("dirty", lambda p: write(p, "a.txt", "changed\n"), " M a.txt"),
    ("ahead", lambda p: (write(p, "b.txt", "b\n"), git(p, "add", "b.txt"), git(p, "commit", "-qm", "local only")),
     "is on no remote branch"),
    ("sub_dirty", lambda p: write(p, "Sub/s.txt", "changed\n"), "submodule Sub has local files or changes"),
    ("locked", None, "worktree is locked"),
    # v1's Godot import exemption is gone (T1's regenerable output outside the named caches, plan-approved loss).
    ("imports", lambda p: write(p, "icon.png.import", "generated\n"), "?? icon.png.import"),
])
def test_a_loss_keeps_the_worktree(world, name, setup, reason):
    path = world.wt(name, submodule=name.startswith("sub"))
    if name == "locked":
        git(world.root, "worktree", "lock", str(path))
    else:
        setup(path)
    got, reasons = world.retired(path)
    assert not got and reason in " | ".join(reasons), reasons
    if name == "dirty" and os.name == "nt":
        assert not world.retired(msys(path))[0]


@pytest.mark.parametrize("token", ["{home}", "{home}/clean2/Sub", "{elsewhere}", ".claude/worktrees/clean2/../clean2",
                                   ".claude/worktrees/cl*", "$HOME/x", "~/x", ""])
def test_targets_the_rule_does_not_judge_keep_the_delete(world, tmp_path, token):
    world.wt("clean2") if not (world.home / "clean2").exists() else None
    elsewhere = tmp_path / "elsewhere_wt"
    if "{elsewhere}" in token:
        git(world.root, "worktree", "add", "-q", "--detach", str(elsewhere))
    token = token.format(home=world.home, elsewhere=elsewhere)
    assert not world.retired(token)[0]


def test_an_unregistered_empty_directory_loses_nothing(world):
    (world.home / "stray").mkdir()
    assert world.retired(world.home / "stray")[0]


def test_a_clean_worktree_of_another_project_is_not_this_ones(world):
    clean = world.wt("clean3")
    assert not rule.is_retired_worktree(str(clean), str(world.base / "proj_seed"), str(world.base / "proj_seed"))


@pytest.mark.parametrize("prs,want", [([(7, "feat/seven")], False), ([(8, "feat/eight")], True), (None, False)])
def test_a_sync_branch_of_an_open_pr_keeps_its_checkout(world, prs, want):
    path = world.home / "sync-pr7"
    if not path.exists():
        world.wt("sync-pr7", branch="sync/pr7")
    with patch.object(rule, "_open_prs", lambda root: prs):
        assert world.retired(path)[0] is want


def test_a_branch_that_heads_an_open_pr_keeps_its_checkout(world):
    path = world.wt("pr_head", branch="feat/open-pr-head")
    with patch.object(rule, "_open_prs", lambda root: [(9, "feat/open-pr-head")]):
        got, reasons = world.retired(path)
    assert not got and "open PR #9" in reasons[0]


_LISTING = "worktree {wt}\n\n"
_DEFAULTS = {"worktree": _LISTING, "status": "", "rev-list": "", "submodule": " abc Sub (heads/main)\n",
             "sub_status": "", "sub_pushed": "", "rev-parse": "abc\n", "branch": "", "symbolic-ref": "",
             "config": "submodule.Sub.path Sub\n"}


@pytest.mark.parametrize("overrides,reason", [
    ({"worktree": None}, "cannot read worktree registration"),
    ({"worktree": ""}, "worktree is not registered"),
    ({"worktree": "locked"}, "worktree is locked"),
    ({"status": None}, "cannot read worktree status"),
    ({"status": " M a.txt\n"}, "worktree has local files or changes:  M a.txt"),
    ({"rev-list": None}, "cannot verify HEAD reachability"),
    ({"rev-list": "abc\n"}, "HEAD abc is on no remote branch (push it to a remote, then retry)"),
    ({"submodule": None}, "cannot read submodule status"),
    ({"submodule": " bad\n"}, "cannot parse submodule status"),
    ({"config": None}, "cannot read submodule status"),
    ({"sub_status": None}, "submodule Sub: cannot read status"),
    ({"sub_pushed": None}, "submodule Sub: cannot verify HEAD reachability"),
    ({"sub_pushed": "abc\n"}, "submodule Sub: HEAD abc is on no remote branch (push it to a remote, then retry)"),
    ({"sub_status": "?? local.txt\n"}, "submodule Sub has local files or changes: ?? local.txt"),
])
def test_each_failed_read_or_condition_is_its_own_reason(world, overrides, reason):
    """v1's planted-reads table: every read the rule makes fails closed with a reason naming it."""
    path = world.home / "planted"
    if not path.exists():
        world.wt("planted", submodule=True)
    listing = _LISTING.format(wt=path.as_posix())

    def planted(args, repo, stdin=None, env=None, timeout=None):
        key = args[0]
        if Path(repo) == path / "Sub":
            key = {"status": "sub_status", "rev-list": "sub_pushed"}.get(key, key)
        if key == "config" and "--list" in args:
            return "submodule.sub.path=Sub\n"
        value = overrides.get(key, _DEFAULTS.get(key, ""))
        if key == "worktree" and value is not None:
            value = listing.rstrip() + "\nlocked\n" if value == "locked" else (listing if value == _LISTING else value)
        return value
    with patch.object(rule, "_git", side_effect=planted):
        got, reasons = world.retired(path)
    assert not got and len(reasons) == 1 and reasons[0].startswith(reason), reasons


def test_every_unmet_condition_reaches_one_deny(world):
    both = world.wt("two_blocks")
    write(both, "notes.md", "n\n")
    write(both, "b2.txt", "b\n")
    git(both, "add", "b2.txt")
    git(both, "commit", "-qm", "local only")
    got, reasons = world.retired(both)
    assert len(reasons) == 2 and "notes.md" in reasons[0] and "no remote branch" in reasons[1]


def test_one_deny_names_every_unheld_file(world):
    path = world.wt("many_l3")
    for n in ("a_notes.md", "b_notes.md", "c_notes.md"):
        write(path, n, "unsaved " + n + "\n")
    reasons = [r for r in world.retired(path)[1] if "local files" in r]
    assert len(reasons) == 1 and all(n in reasons[0] for n in ("a_notes.md", "b_notes.md", "c_notes.md"))


def test_a_submodule_head_pushed_from_the_main_checkout_counts(tmp_path):
    """A worktree's submodule clone keeps stale remote refs; the main checkout's same submodule sees the push."""
    world = Project(tmp_path)
    git(world.root, *SUB, "submodule", "update", "-q", "--init")
    sub_main = world.root / "Sub"
    before = head(sub_main)
    write(sub_main, "s2.txt", "pushed from the main checkout\n")
    git(sub_main, "add", "s2.txt")
    git(sub_main, "commit", "-qm", "pushed later")
    git(sub_main, "push", "-q", "origin", "HEAD:main")
    git(sub_main, "fetch", "-q", "origin")
    git(world.root, "add", "Sub")
    git(world.root, "commit", "-qm", "bump Sub")
    git(world.root, "push", "-q", "origin", "HEAD:main")
    git(world.root, "fetch", "-q", "origin")
    stale = world.wt("stale_sub_refs", submodule=True)
    git(stale / "Sub", "update-ref", "refs/remotes/origin/main", before)
    assert world.retired(stale)[0]


def test_a_submodule_whose_admin_directory_is_gone_denies(world):
    """v1 judged its files only for a worktree this session created; the rule now denies it outright."""
    path = world.wt("b_held", submodule=True)
    admin = (path / "Sub/.git").read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
    rmtree(admin if os.path.isabs(admin) else path / "Sub" / admin)
    got, reasons = world.retired(path)
    assert not got and any("Sub" in r and "missing admin directory" in r for r in reasons), reasons


def test_a_lane_landed_by_rebase_retires_and_a_local_commit_does_not(tmp_path):
    world = Project(tmp_path)
    ahead = world.wt("ahead")
    write(ahead, "b.txt", "b\n")
    git(ahead, "add", "b.txt")
    git(ahead, "commit", "-qm", "local only")
    rebased = world.wt("rebased")
    write(rebased, "c.txt", "c\n")
    git(rebased, "add", "c.txt")
    git(rebased, "commit", "-qm", "lane commit")
    write(world.root, "c.txt", "c\n")
    git(world.root, "add", "c.txt")
    git(world.root, "commit", "-qm", "landed by rebase")
    git(world.root, "push", "-q", "origin", "main")
    git(world.root, "fetch", "-q", "origin")
    assert world.retired(rebased)[0]
    assert not world.retired(ahead)[0]


# --- Nested submodules (v1 test_conformance_j8.py v23) ----------------------------------------------------------
def test_an_ignored_file_in_a_nested_submodule_keeps_the_worktree(tmp_path):
    def init(name):
        return seeded(tmp_path, name, {".gitignore": "*.local\n.cache/\nempty/\n"})
    grand, child, root = init("grand-source"), init("child-source"), init("project")
    git(child, *SUB, "submodule", "add", "-q", str(grand), "grand")
    git(child, "commit", "-qam", "grandchild")
    git(root, *SUB, "submodule", "add", "-q", str(child), "child")
    git(root, "commit", "-qam", "child")
    git(root, "update-ref", "refs/remotes/proof/main", "HEAD")
    target = root / ".claude/worktrees/retired"
    target.parent.mkdir(parents=True)
    git(root, "worktree", "add", "-q", "--detach", str(target), "HEAD")
    git(target, *SUB, "submodule", "update", "--init", "--recursive", "-q")

    def qualifies():
        return rule.is_retired_worktree(str(target), str(root), str(root))
    assert qualifies()
    for sub in (target / "child", target / "child/grand"):
        unique = sub / "secret.local"
        unique.write_text("irreplaceable fixture evidence", encoding="utf-8")
        assert not qualifies(), sub
        unique.unlink()
        write(sub, ".cache/generated", "cache")
        assert qualifies(), sub
        (sub / "empty").mkdir()
        assert qualifies(), sub


# --- Through the floor: linked worktrees ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,setup,want", [
    ("ch_clean", None, "allow"),
    ("ch_dirty", lambda p: write(p, "a.txt", "changed\n"), "deny"),
    ("ch_evidence", lambda p: write(p, ".claude/scratch/%s/gate.out" % PEER_SID, "x\n"), "deny"),
])
def test_rm_of_a_worktree_through_the_floor(at, name, setup, want):
    path = at.wt(name)
    if setup:
        setup(path)
    assert rm("rm -rf .claude/worktrees/" + name, at).kind == want
    if os.name == "nt":
        assert rm("rm -rf " + msys(path), at).kind == want


def test_a_chained_delete_needs_every_target_retired_and_the_home_stays(at):
    at.wt("ch2_clean")
    dirty = at.wt("ch2_dirty")
    write(dirty, "a.txt", "changed\n")
    assert rm("rm -rf .claude/worktrees/ch2_clean && rm -rf .claude/worktrees/ch2_dirty", at).denied
    assert rm("rm -rf .claude/worktrees", at).denied


def test_the_deny_names_the_ignored_path(at):
    path = at.wt("ch_named")
    write(path, ".claude/scratch/%s/gate.out" % PEER_SID, "x\n")
    assert "!! .claude/scratch" in rm("rm -rf .claude/worktrees/ch_named", at).reason


def test_a_worktree_this_session_created_is_judged_like_any_other(at, tmp_path):
    """T1 (plan-approved loss): a creation proof no longer lets ignored files or stored edits go with it."""
    path = at.wt("ch_made")
    write(path, ".claude/scratch/notes/x.out", "x\n")
    write(path, "a.txt", "a saved edit\n")
    git(path, "hash-object", "-w", "a.txt")
    rows = [{"type": "assistant", "timestamp": "2000-01-01T00:00:00.000Z", "cwd": str(at.root),
             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "w0", "name": "Bash",
                         "input": {"command": "git worktree add " + path.as_posix() + " HEAD"}}]}},
            {"type": "user", "timestamp": "2100-01-01T00:00:00.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w0",
                         "content": "Preparing worktree"}]}}]
    transcript = tmp_path / "made.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _own_worktree.session_created_worktree(str(path), str(transcript))
    assert rm("rm -rf .claude/worktrees/ch_made", at, transcript).denied


@pytest.mark.parametrize("where,want", [("in", "deny"), ("inside", "deny"), ("other", "allow"), ("self", "allow"),
                                        ("none", "allow")])
def test_a_live_peer_session_working_in_a_worktree_keeps_it(at, tmp_path, monkeypatch, where, want):
    clean = at.wt("peer_" + where)
    other = at.wt("peer_other_" + where)
    sid = "5eed5eed-aaaa-4bbb-8ccc-00000000000%d" % ("in inside other self none".split().index(where) + 1)
    cwd = {"in": clean, "inside": clean / "Sub", "other": other, "self": clean}.get(where)
    if cwd is not None:
        peer_login(tmp_path, monkeypatch, ("peer-" + where, sid, cwd))
    else:
        peer_login(tmp_path, monkeypatch)
    transcript = tmp_path / (sid + ".jsonl") if where == "self" else None
    got = rm("rm -rf .claude/worktrees/peer_" + where, at, transcript)
    assert got.kind == want
    if where == "in":
        assert "peer-in" in got.reason


# --- Landed job worktrees (part 3) -----------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    project = Project(tmp_path_factory.mktemp("landed"))
    with open(project.root / ".git" / "info" / "exclude", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("*.tmp\n*.TMP\nstray.tmp.keep\n")
    return project


def test_a_commit_cherry_picked_onto_the_local_default_branch_retires(landed):
    picked = landed.job("picked", ("p.txt", "picked\n"))
    git(landed.root, "cherry-pick", head(picked))
    assert landed.retired(picked)[0]


def test_commits_squashed_onto_the_local_default_branch_retire(landed):
    squashed = landed.job("squashed", ("q1.txt", "one\n"), ("q2.txt", "two\n"))
    git(landed.root, "merge", "--squash", "scratch/squashed")
    git(landed.root, "commit", "-qm", "squash of scratch/squashed")
    assert landed.retired(squashed)[0]


def test_a_commit_no_default_branch_holds_keeps_the_worktree(landed):
    assert not landed.retired(landed.job("ahead", ("z.txt", "only here\n")))[0]


def test_a_commit_held_only_by_another_local_branch_keeps_the_worktree(landed):
    side = landed.job("side", ("s.txt", "side\n"))
    git(landed.root, "branch", "elsewhere", head(side))
    assert not landed.retired(side)[0]


def test_a_cherry_pick_main_edited_again_is_still_held_by_patch(landed):
    drifted = landed.job("drifted", ("d.txt", "branch edit\n"))
    git(landed.root, "cherry-pick", head(drifted))
    write(landed.root, "d.txt", "main edited it again\n")
    git(landed.root, "commit", "-qam", "main moves on")
    assert landed.retired(drifted)[0]


def test_squashed_commits_whose_file_main_rewrote_stay_held_by_history(landed):
    sq2 = landed.job("sq2", ("w.txt", "1\n"), ("w.txt", "2\n"))
    git(landed.root, "merge", "--squash", "scratch/sq2")
    git(landed.root, "commit", "-qm", "squash of scratch/sq2")
    write(landed.root, "w.txt", "3\n")
    git(landed.root, "commit", "-qam", "main rewrites w.txt")
    assert landed.retired(sq2)[0]


def test_a_commit_reapplied_on_a_moved_line_is_held_and_a_partial_one_is_not(landed):
    write(landed.root, "ctx.txt", "one\n")
    git(landed.root, "add", "ctx.txt")
    git(landed.root, "commit", "-qm", "ctx base")
    reapply = landed.wt("reapply", branch="scratch/reapply")
    write(landed.root, "ctx.txt", "one\nmain\n")
    git(landed.root, "commit", "-qam", "main owns the next line")
    write(reapply, "ctx.txt", "one\nshared fix\n")
    git(reapply, "commit", "-qam", "branch adds the fix")
    write(landed.root, "ctx.txt", "one\nmain\nshared fix\n")
    git(landed.root, "commit", "-qam", "main adds the same fix on its line")
    assert landed.retired(reapply)[0]
    partial = landed.wt("reapply_partial", branch="scratch/reapply_partial")
    write(partial, "ctx.txt", "one\nmain\nshared fix\nan extra line\n")
    git(partial, "commit", "-qam", "branch adds an extra line")
    assert not landed.retired(partial)[0]


def test_content_landed_in_one_commit_main_then_moved_past_is_held(landed):
    held = landed.wt("held_content", branch="scratch/held_content")
    write(held, "h.txt", "v2\n")
    git(held, "add", "h.txt")
    git(held, "commit", "-qm", "branch h v2")
    write(held, "z.txt", "z\n")
    git(held, "add", "z.txt")
    git(held, "commit", "-qm", "branch z")
    write(landed.root, "h.txt", "v2\n")
    write(landed.root, "z.txt", "z\n")
    git(landed.root, "add", "h.txt", "z.txt")
    git(landed.root, "commit", "-qm", "land h and z in one commit")
    write(landed.root, "h.txt", "v3\n")
    git(landed.root, "commit", "-qam", "main moves h past it")
    assert landed.retired(held)[0]


def test_a_row_main_holds_beside_its_own_edit_merges_to_nothing(landed):
    write(landed.root, "lock.txt", "A\nB\nC\nD\nE\n")
    git(landed.root, "add", "lock.txt")
    git(landed.root, "commit", "-qm", "lock base")
    merged = landed.wt("merged_elsewhere", branch="scratch/merged_elsewhere")
    write(merged, "lock.txt", "A\nB\nROW\nC\nD\nE\n")
    git(merged, "commit", "-qam", "branch adds a row")
    write(landed.root, "lock.txt", "A\nB\nROW\nC\nD\nE2\n")
    git(landed.root, "commit", "-qam", "main adds the row and edits E")
    assert landed.retired(merged)[0]


@pytest.mark.parametrize("name,setup,want", [
    ("gone", lambda p: (p / "a.txt").unlink(), True),
    ("gone_staged", lambda p: (write(p, "a.txt", "staged edit\n"), git(p, "add", "a.txt"), (p / "a.txt").unlink()),
     False),
    ("gone_new", lambda p: (write(p, "extra.txt", "x\n"), (p / "a.txt").unlink()), False),
    ("scratch", lambda p: (write(p, ".claude/scratch/job_x/run/args.json", "{}\n"),
                           write(p, ".claude/scratch/job_x/notes.md", "launcher notes\n")), True),
    ("loose", lambda p: write(p, ".claude/scratch/notes.md", "launcher notes\n"), False),
    ("peer", lambda p: write(p, ".claude/scratch/%s/out.txt" % PEER_SID, "peer\n"), False),
    ("peer_file", lambda p: write(p, ".claude/scratch/gate_positions/%s.json" % PEER_SID, "{}\n"), True),
    ("tmp_log", lambda p: write(p, ".claude/tmp/run_x/out.log", "log\n"), True),
    ("other_ignored", lambda p: write(p, ".env.local", "TOKEN=1\n"), False),
    ("other_tmp", lambda p: write(p, "draft.tmp", "an author's draft\n"), False),
    # v1 let Godot import temp files go; T1 drops regenerable output outside the named caches (plan-approved loss).
    ("import_tmp", lambda p: (write(p, "Visual/icon.png.import119908465.tmp", "t\n"),
                              write(p, "Visual/icon.png.import~RF2c3d449d.TMP", "t\n")), False),
])
def test_a_job_worktrees_status_entries(landed, name, setup, want):
    path = landed.job(name)
    setup(path)
    assert landed.retired(path)[0] is want


def _repo(path, *, commit=None, files=(), remote=None, empty_commit=False):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for rel in files:
        write(path, rel, "x\n")
    if commit:
        git(path, "add", "-A")
        git(path, "commit", "-qm", commit)
    if empty_commit:
        git(path, "commit", "-q", "--allow-empty", "-m", "unpushed")
    if remote:
        git(path, "remote", "add", "origin", remote)
    return path


PLANT = ".claude/tmp/pe_absent_delete_dxf68feq"


@pytest.mark.parametrize("name,build,want", [
    ("tmp_unborn", lambda p: _repo(p / ".claude/tmp/run_x/probe"), True),
    ("tmp_work", lambda p: _repo(p / ".claude/tmp/run_x/probe", empty_commit=True), False),
    ("tmp_file", lambda p: (_repo(p / ".claude/tmp/run_x/probe"), write(p, ".claude/tmp/run_x/probe/notes.txt")), False),
    ("plant_work", lambda p: write(p, PLANT + "/.claude/scratch/%s/o.txt" % PEER_SID), True),
    ("plant_repo", lambda p: _repo(p / PLANT / "repo", commit="only here", files=("work.txt",)), False),
    ("plant_fixture", lambda p: (_repo(p / PLANT / ".claude/scratch/probe", empty_commit=True),
                                 write(p, PLANT + "/.claude/scratch/%s/out/job.log" % PEER_SID)), True),
    ("plant_remote", lambda p: _repo(p / PLANT / "repo", empty_commit=True, remote="https://example.invalid/r.git"),
     False),
    ("plant_dirty", lambda p: (_repo(p / PLANT / "repo", empty_commit=True), write(p, PLANT + "/repo/new.txt")), False),
    ("plant_beside", lambda p: (write(p, PLANT + "/.claude/scratch/%s/o.txt" % PEER_SID),
                                _repo(p / ".claude/tmp/notes/probe", empty_commit=True)), False),
    ("plant_like", lambda p: _repo(p / ".claude/tmp/pe_absent_delete_dxf68feq0/probe", empty_commit=True), False),
    ("nested", lambda p: (_repo(p / ".claude/scratch/clone"), write(p, ".claude/scratch/clone/w.txt")), False),
    ("stripped", lambda p: write(p, ".claude/scratch/recon_fixture_x/repo/.git/objects/ab/cdef", "blob\n"), True),
    ("stripped_ref", lambda p: write(p, ".claude/scratch/x/repo/.git/refs/heads/work", "0" * 40 + "\n"), False),
])
def test_a_job_worktrees_scratch_and_tmp_repositories(landed, name, build, want):
    path = landed.job(name)
    build(path)
    assert landed.retired(path)[0] is want


@pytest.fixture(scope="module")
def inner_remote(landed):
    seed = seeded(landed.base, "inner_seed", {"i.txt": "i\n"})
    remote = landed.base / "inner_remote.git"
    git(landed.base, "clone", "-q", "--bare", str(seed), str(remote))
    return remote


def test_a_clone_under_a_jobs_scratch_is_judged_by_the_clone_rule(landed, inner_remote):
    nested_ok = landed.job("nested_ok")
    ok_clone = landed.clone("pubcopy", inner_remote, nested_ok / ".claude/scratch")
    nested_bad = landed.job("nested_bad")
    bad_clone = landed.clone("pubcopy", inner_remote, nested_bad / ".claude/scratch")
    write(bad_clone, "i.txt", "unpushed\n")
    git(bad_clone, "commit", "-qam", "only here")
    nested_link = landed.job("nested_link")
    link_clone = nested_link / ".claude/scratch/tpl"
    git(landed.root, "worktree", "add", "-q", "--detach", str(link_clone))
    root = str(landed.root)
    why = []
    assert rule.is_regenerable_clone(str(ok_clone), root, root)
    assert not rule.is_regenerable_clone(str(bad_clone), root, root, why) and "no remote branch" in why[0]
    why = []
    assert not rule.is_regenerable_clone(str(link_clone), root, root, why) and "not a standalone clone" in why[0]
    assert landed.retired(nested_ok)[0]
    assert not landed.retired(nested_bad)[0]
    assert not landed.retired(nested_link)[0]


def test_the_projects_listed_outputs_include_a_test_runs_files(tmp_path):
    write(tmp_path, ".claude/skills/project_subsystems/adaptation.json",
          json.dumps({"regenerable_ignored": [".runsettings", "logs", "addons/*/libs/~*"]}))
    listed = rule.project_patterns(tmp_path)
    for name in (".runsettings", "logs/run.log", "addons/debug_draw_3d/libs/~libdd3d.windows.editor.x86_64.dll"):
        assert rule._listed(name, listed), name


@pytest.fixture
def at_landed(landed, tmp_path, monkeypatch):
    elsewhere = tmp_path / "temp"
    elsewhere.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(elsewhere))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(landed.root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no_login"))
    monkeypatch.chdir(landed.root)
    return landed


def test_landed_job_worktrees_through_the_floor(at_landed, inner_remote, tmp_path, monkeypatch):
    lp = at_landed
    picked = lp.job("f_picked", ("fp.txt", "picked\n"))
    git(lp.root, "cherry-pick", head(picked))
    lp.job("f_scratch")
    write(lp.home / "f_scratch", ".claude/scratch/job_x/run/args.json", "{}\n")
    lp.job("f_ahead", ("fz.txt", "only here\n"))
    ok = lp.job("f_nested_ok")
    lp.clone("pubcopy", inner_remote, ok / ".claude/scratch")
    bad = lp.job("f_nested_bad")
    bad_clone = lp.clone("pubcopy", inner_remote, bad / ".claude/scratch")
    write(bad_clone, "i.txt", "unpushed\n")
    git(bad_clone, "commit", "-qam", "only here")
    for command, want in (("rm -rf .claude/worktrees/f_picked", "allow"), ("rm -rf .claude/worktrees/f_scratch", "allow"),
                          ("rm -rf .claude/worktrees/f_ahead", "deny"), ("rm -rf .claude/worktrees/f_nested_ok", "allow"),
                          ("rm -rf .claude/worktrees/f_nested_bad", "deny"),
                          ("rm -rf .claude/worktrees/f_nested_ok/.claude/scratch/pubcopy", "allow"),
                          ("rm -rf .claude/worktrees/f_nested_bad/.claude/scratch/pubcopy", "deny"),
                          ("rm -rf .claude/worktrees/f_scratch/.claude/scratch", "deny")):
        assert rm(command, lp).kind == want, command
    assert "holds a git repository that loses work" in rm("rm -rf .claude/worktrees/f_nested_bad", lp).reason
    peer_login(tmp_path, monkeypatch, ("peer-landed", "5eed5eed-aaaa-4bbb-8ccc-000000000006", picked))
    assert rm("rm -rf .claude/worktrees/f_picked", lp).denied


# --- Orphaned worktrees ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,edit,want", [
    ("o_clean", None, True),
    ("o_godot", lambda p: (write(p, ".godot/imported/c.ctex", "c\n"), (p / "empty_ignored_dir").mkdir()), True),
    ("o_json", lambda p: write(p, ".claude/scratch/%s/x.json" % PEER_SID, "{}\n"), False),
    ("o_scratch", lambda p: write(p, ".claude/scratch/x.out", "x\n"), False),
    ("o_new", lambda p: write(p, "notes.md", "unsaved notes\n"), False),
    ("o_dirty", lambda p: write(p, "a.txt", "edited, never committed\n"), False),
])
def test_an_orphan_goes_only_when_git_holds_its_content(world, name, edit, want):
    path = world.orphan(name, edit=edit)
    reasons = []
    assert rule.lossless_orphan_worktree(str(path), str(world.root), str(world.root), reasons) is want
    assert world.retired(path)[0] is want
    if name == "o_json":
        assert len(reasons) == 1 and "x.json" in reasons[0] and "not a regenerable cache" in reasons[0]


def test_an_orphan_with_a_submodule_needs_the_main_checkouts_store(tmp_path):
    world = Project(tmp_path)
    o_sub = world.orphan("o_sub", submodule=True)
    assert not world.retired(o_sub)[0]
    git(world.root, *SUB, "submodule", "update", "-q", "--init")
    assert world.retired(o_sub)[0]
    edited = world.orphan("o_sub_dirty", submodule=True, edit=lambda p: write(p, "Sub/s.txt", "edited\n"))
    assert not world.retired(edited)[0]


def test_a_gitless_leftover_is_judged_by_the_same_held_content_test(world):
    g_clean = world.orphan("g_clean")
    (g_clean / ".git").unlink()
    assert world.retired(g_clean)[0]
    g_new = world.orphan("g_new", edit=lambda p: write(p, "notes.md", "unsaved notes\n"))
    (g_new / ".git").unlink()
    assert not world.retired(g_new)[0]


def test_an_orphan_deny_names_every_file_up_to_the_cap(world):
    three = world.orphan("many_o3", edit=lambda p: [write(p, n, "x\n") for n in ("a_notes.md", "b_notes.md", "c_notes.md")])
    got = []
    rule.lossless_orphan_worktree(str(three), str(world.root), str(world.root), got)
    assert len(got) == 1 and all(n in got[0] for n in ("a_notes.md", "b_notes.md", "c_notes.md"))
    many = world.orphan("many_cap", edit=lambda p: [write(p, "f%02d.md" % i, "x%d\n" % i) for i in range(11)])
    got = []
    rule.lossless_orphan_worktree(str(many), str(world.root), str(world.root), got)
    assert got and "f00.md" in got[0] and "(and 3 more)" in got[0] and "f10.md" not in got[0]


@pytest.fixture(scope="module")
def siblings(world, tmp_path_factory):
    """Orphans made as sibling folders of the project, judged like those under `.claude/worktrees/`."""
    where = tmp_path_factory.mktemp("siblings")
    other = Project(tmp_path_factory.mktemp("other"), world.remote)

    def outputs(p):
        for rel in (".devdb/local/default/backend.sqlite3", ".devdb/preview.env", ".env.local", "dist-preview/index.html",
                    "logs/screens/a.png", "test-results/r.txt"):
            write(p, rel)
    built = {
        "s_clean": world.orphan("s_clean", where=where),
        "s_godot": world.orphan("s_godot", where=where, edit=lambda p: write(p, ".godot/imported/c.ctex")),
        "s_new": world.orphan("s_new", where=where, edit=lambda p: write(p, "notes.md", "unsaved notes\n")),
        "s_json": world.orphan("s_json", where=where, edit=lambda p: write(p, ".claude/scratch/%s/n.json" % PEER_SID)),
        "s_other": other.orphan("s_other", where=where),
        "s_out": world.orphan("s_out", where=where, edit=outputs),
        "s_out_dirty": world.orphan("s_out_dirty", where=where, edit=lambda p: (outputs(p), write(p, "a.txt", "edit\n"))),
        "s_out_extra": world.orphan("s_out_extra", where=where,
                                    edit=lambda p: (outputs(p), write(p, ".devdb/tmp/an.py"))),
        "s_out_nested": world.orphan("s_out_nested", where=where, edit=lambda p: write(p, "src/.devdb/local/x.db")),
        "s_out_logs": world.orphan("s_out_logs", where=where, edit=lambda p: write(p, "src/logs/unique.txt")),
        "s_out_other": other.orphan("s_out_other", where=where, edit=outputs),
    }
    live = where / "s_live"
    git(world.root, "worktree", "add", "-q", "--detach", str(live))
    built["s_live"] = live
    built["live_out"] = world.wt("live_out")
    outputs(built["live_out"])
    built["live_extra"] = world.wt("live_extra")
    outputs(built["live_extra"])
    write(built["live_extra"], ".devdb/tmp/an.py")
    return built


@pytest.mark.parametrize("name,want", [("s_clean", True), ("s_godot", True), ("s_new", False), ("s_json", False),
                                       ("s_other", False), ("s_live", False), ("s_out", False)])
def test_a_sibling_orphan_of_this_repository(world, siblings, name, want):
    assert world.retired(siblings[name])[0] is want


def test_the_project_itself_is_never_a_retired_worktree(world):
    assert not world.retired(world.root)[0]


LISTED = {"regenerable_ignored": [".devdb/local", ".devdb/preview*", ".env.local", "dist-preview", "logs", "test-results"]}


@pytest.mark.parametrize("name,want", [("s_out", True), ("s_out_dirty", False), ("s_out_extra", False),
                                       ("s_out_nested", False), ("s_out_logs", False), ("s_out_other", False),
                                       ("live_out", True), ("live_extra", False)])
def test_the_projects_listed_outputs_go_and_nothing_else(world, siblings, name, want):
    path = world.root / ".claude/skills/project_subsystems/adaptation.json"
    write(world.root, ".claude/skills/project_subsystems/adaptation.json", json.dumps(LISTED))
    try:
        assert world.retired(siblings[name])[0] is want
    finally:
        path.unlink()


@pytest.mark.parametrize("bad", ['{"regenerable_ignored": ".devdb/local"}', '{"regenerable_ignored": ["*"]}',
                                 '{"regenerable_ignored": ["../**", "/c/x", ""]}', "{not json"])
def test_a_broken_output_list_only_narrows_what_may_go(world, siblings, bad):
    path = world.root / ".claude/skills/project_subsystems/adaptation.json"
    write(world.root, ".claude/skills/project_subsystems/adaptation.json", bad)
    try:
        assert not world.retired(siblings["s_out"])[0]
    finally:
        path.unlink()


def test_sibling_orphans_through_the_floor(at, siblings):
    def cmd(name):
        return "rm -rf " + siblings[name].as_posix()
    assert rm(cmd("s_clean"), at).kind == "allow"
    assert rm(cmd("s_godot"), at).kind == "allow"
    assert "notes.md" in rm(cmd("s_new"), at).reason
    for name in ("s_json", "s_other", "s_live", "s_out", "s_out_other"):
        assert rm(cmd(name), at).denied, name
    write(at.root, ".claude/skills/project_subsystems/adaptation.json", json.dumps(LISTED))
    try:
        assert rm(cmd("s_out"), at).kind == "allow"
        assert "a.txt" in rm(cmd("s_out_dirty"), at).reason
        assert ".devdb/tmp/an.py" in rm(cmd("s_out_extra"), at).reason
        assert rm(cmd("s_out_other"), at).denied
        assert rm(cmd("live_out"), at).kind == "allow"
    finally:
        (at.root / ".claude/skills/project_subsystems/adaptation.json").unlink()


def test_orphans_under_the_worktree_home_through_the_floor(at, tmp_path, monkeypatch):
    o_clean = at.orphan("f_o_clean")
    at.orphan("f_o_godot", edit=lambda p: write(p, ".godot/imported/c.ctex"))
    at.orphan("f_o_new", edit=lambda p: write(p, "notes.md", "unsaved\n"))
    at.orphan("f_o_json", edit=lambda p: write(p, ".claude/scratch/%s/x.json" % PEER_SID))
    for name, want in (("f_o_clean", "allow"), ("f_o_godot", "allow"), ("f_o_new", "deny"), ("f_o_json", "deny")):
        assert rm("rm -rf .claude/worktrees/" + name, at).kind == want, name
    peer_login(tmp_path, monkeypatch, ("peer-orphan", "5eed5eed-aaaa-4bbb-8ccc-000000000006", o_clean))
    assert rm("rm -rf .claude/worktrees/f_o_clean", at).denied


# --- Standalone clones under scratch (part 1) ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def clones(world):
    seed = seeded(world.base, "seed", {"a.txt": "a\n"})
    remote = world.base / "remote.git"
    git(world.base, "clone", "-q", "--bare", str(seed), str(remote))
    fix = world.scratch / "rc_fix"
    built = {"remote": remote, "fix": fix}
    for name in ("clean", "dirty", "untracked", "cached", "noted", "stashed", "ahead", "tagged", "tagged_local",
                 "no_remote", "m"):
        built[name] = world.clone(name, remote, fix)
    write(built["dirty"], "a.txt", "changed\n")
    write(built["untracked"], "new.txt", "n\n")
    write(built["cached"], ".git/info/exclude", ".search-index/\n")
    write(built["cached"], ".search-index/search.db")
    write(built["noted"], ".git/info/exclude", "notes.local\n")
    write(built["noted"], "notes.local", "keep me\n")
    write(built["stashed"], "a.txt", "stash me\n")
    git(built["stashed"], "stash", "-q")
    write(built["ahead"], "b.txt", "b\n")
    git(built["ahead"], "add", "b.txt")
    git(built["ahead"], "commit", "-qm", "local only")
    git(built["tagged"], "tag", "-a", "local-tag", "-m", "t")
    write(built["tagged_local"], "c.txt", "c\n")
    git(built["tagged_local"], "add", "c.txt")
    git(built["tagged_local"], "commit", "-qm", "tagged local")
    git(built["tagged_local"], "reset", "-q", "--hard", "HEAD~1")
    git(built["tagged_local"], "tag", "local-only-tag", "HEAD@{1}")
    git(built["no_remote"], "remote", "remove", "origin")
    built["w"] = fix / "w"
    git(built["m"], "worktree", "add", "-q", str(built["w"]))
    built["plain"] = fix / "plain_dir"
    write(built["plain"], "evidence.txt")
    return built


@pytest.mark.parametrize("name,want,reason", [
    ("clean", True, None), ("cached", True, None), ("tagged", True, None),
    ("dirty", False, "local files or changes"), ("untracked", False, "local files or changes"),
    ("noted", False, "notes.local"), ("stashed", False, "stash"), ("ahead", False, "no remote branch"),
    ("tagged_local", False, "no remote branch"), ("no_remote", False, "no remote"), ("m", False, "linked worktrees"),
    ("w", False, "not a standalone clone"), ("plain", False, "not a standalone clone"),
])
def test_a_scratch_clone_goes_only_when_clean_and_pushed(world, clones, name, want, reason):
    why = []
    assert rule.is_regenerable_clone(str(clones[name]), str(world.root), str(world.root), why) is want
    if reason:
        assert len(why) == 1 and reason in why[0], why


def test_a_clean_clone_in_every_token_form(world, clones):
    root = str(world.root)
    rel = os.path.relpath(clones["clean"], world.root).replace("\\", "/")
    assert rule.is_regenerable_clone(rel, root, root)
    if os.name == "nt":
        assert rule.is_regenerable_clone(msys(clones["clean"]), root, root)


@pytest.mark.parametrize("token", [".claude/scratch", "{scratch}", "{outside}", "{clean}/../clean",
                                   ".claude/scratch/rc_*/clean", "$HOME/clean", "~/clean", "{fix}/missing", ""])
def test_a_token_the_clone_rule_cannot_judge_keeps_the_delete(world, clones, tmp_path, token):
    outside = tmp_path / "clone"
    if "{outside}" in token:
        git(tmp_path, "clone", "-q", str(clones["remote"]), str(outside))
    token = token.format(scratch=world.scratch, outside=outside, clean=clones["clean"], fix=clones["fix"])
    assert not rule.is_regenerable_clone(token, str(world.root), str(world.root))


def test_scratch_clones_through_the_floor(at, clones):
    def rel(name):
        return os.path.relpath(clones[name], at.root).replace("\\", "/")
    for command, want in (
            ("rm -rf " + rel("clean"), "allow"), ("rm -rf " + rel("dirty"), "deny"), ("rm -rf " + rel("cached"), "allow"),
            ("rm -rf " + rel("clean") + " 2>&1 | tail -1", "allow"), ("rm -rf " + rel("clean") + " > /dev/null", "allow"),
            ("rm -rf " + rel("clean") + " 2>/dev/null", "allow"), ("rm -rf " + rel("dirty") + " 2>&1", "deny"),
            ("rm -rf " + rel("clean") + " " + rel("plain"), "allow"),
            ("rm -rf " + os.path.relpath(clones["fix"], at.root).replace("\\", "/"), "deny"),
            ("rm -rf " + rel("clean") + " && rm -rf " + rel("dirty"), "deny"), ("rm -rf .claude/scratch", "deny")):
        assert rm(command, at).kind == want, command
    assert "notes.local" in rm("rm -rf " + rel("noted"), at).reason


@pytest.mark.parametrize("name,build,want", [
    ("empty_leftover/redcheck", lambda p: p.mkdir(parents=True), "allow"),
    ("filled_leftover/redcheck", lambda p: write(p, "notes.txt", "keep\n"), "allow"),
    ("git_leftover/redcheck", lambda p: (p / ".git").mkdir(parents=True), "deny"),
])
def test_leftovers_under_scratch(at, name, build, want):
    build(at.scratch / "lo" / name)
    assert rm("rm -r .claude/scratch/lo/" + name, at).kind == want


def test_an_empty_directory_outside_scratch_keeps_its_deny(at):
    (at.root / ".claude/rc_empty_outside").mkdir()
    assert rm("rm -r .claude/rc_empty_outside", at).denied


def test_scratch_folders_holding_no_repository(at, tmp_path, monkeypatch):
    write(at.scratch, "copytree/big.bin")
    git(at.root, "worktree", "add", "-q", "--detach", str(at.scratch / "heldtree/wt"))
    unsaved = _repo(at.scratch / "unsaved_repo", commit="only here", files=("n.txt",))
    write(at.scratch, "peers/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d/out.txt")
    write(at.scratch, "no_ids/f.txt")
    for command, want in (("rm -rf .claude/scratch/copytree", "allow"), ("rm -rf .claude/scratch/heldtree", "deny"),
                          ("rm -rf .claude/scratch/unsaved_repo", "deny"),
                          ("rm -rf .claude/scratch/peers/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "deny"),
                          ("rm -rf .claude/scratch/peers/*", "deny"), ("rm -rf .claude/scratch/no_ids/*", "allow")):
        assert rm(command, at).kind == want, command
    assert "push its commits to a remote" in rm("rm -rf .claude/scratch/unsaved_repo", at).reason
    assert unsaved.is_dir()
    peer_login(tmp_path, monkeypatch, ("peer-scratch", "5eed5eed-aaaa-4bbb-8ccc-000000000007", at.scratch / "copytree"))
    assert "peer-scratch" in rm("rm -rf .claude/scratch/copytree", at).reason


# --- What a status line loses ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,lost", [
    (" D gone.txt\n", []), (" M missing.txt\n", [" M missing.txt"]),
    (' M "caf\\303\\251.txt"\n', [' M "caf\\303\\251.txt"']), ("?? icon.png.import\n", ["?? icon.png.import"]),
])
def test_what_a_status_line_loses(tmp_path, status, lost):
    assert rule.losses(status, tmp_path) == lost


def test_the_verdict_cli_prints_each_worktree(world, capsys, monkeypatch):
    clean = world.wt("cli_clean")
    dirty = world.wt("cli_dirty")
    write(dirty, "a.txt", "changed\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world.root))
    monkeypatch.chdir(world.root)
    assert rule.main([str(clean)]) == 0
    assert rule.main([str(clean), str(dirty)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "%s: retired" % clean and lines[-1].startswith("%s: blocked: worktree has local" % dirty)
    assert rule.main([]) == 2


# --- Ownership of a backgrounded add (part 3; the proof `_own_worktree` keeps for discards) ---------------------------
START, NOTICE, BORN, DONE, LATE = ("2026-10-24T09:00:00.000Z", "2026-10-24T09:00:02.000Z", "2026-10-24T09:00:03.000Z",
                                   "2026-10-24T09:00:05.000Z", "2026-10-24T09:01:00.000Z")
VISIBLE = ('cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD && '
           'W=.claude/worktrees/{name} && git -C $W status --short')
PLAIN = 'cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD'
OK_OUTPUT = "Preparing worktree (new branch 'scratch/{name}')\nHEAD is now at 1234abc init\n"


@pytest.fixture(scope="module")
def bg_repo(tmp_path_factory):
    return seeded(tmp_path_factory.mktemp("bg"), "bg_repo", {"README.md": "bg\n"})


@pytest.mark.parametrize("name,command,output,kwargs,want", [
    ("a", VISIBLE, OK_OUTPUT, {}, True),
    ("b", VISIBLE, OK_OUTPUT, {"notify": False}, False),
    ("c", VISIBLE, "fatal: a branch named 'scratch/c' already exists\n", {}, False),
    ("d", VISIBLE, OK_OUTPUT, {"born": LATE}, False),
    ("e", PLAIN, OK_OUTPUT, {"exit_code": 0}, True),
    ("f", PLAIN, OK_OUTPUT, {"exit_code": 1}, False),
    ("g", VISIBLE, OK_OUTPUT, {"status": "killed"}, False),
    ("h", VISIBLE, OK_OUTPUT, {"out_file": False}, False),
])
def test_a_backgrounded_add_is_owned_only_on_its_completed_notice(bg_repo, tmp_path, name, command, output, kwargs,
                                                                   want):
    from datetime import datetime
    notify, status = kwargs.get("notify", True), kwargs.get("status", "completed")
    exit_code, born, out_file = kwargs.get("exit_code", 0), kwargs.get("born", BORN), kwargs.get("out_file", True)
    task = "bg" + name
    out = tmp_path / "tasks" / (task + ".output")
    out.parent.mkdir(parents=True)
    if out_file:
        out.write_text(output.replace("{name}", name), encoding="utf-8")
    wt = bg_repo / ".claude" / "worktrees" / name
    git(bg_repo, "worktree", "add", "-q", "-b", "scratch/" + name, str(wt), "HEAD")
    stamp = datetime.fromisoformat(born.replace("Z", "+00:00")).timestamp()
    os.utime(wt / ".git", (stamp, stamp))
    notice = ("Command running in background with ID: %s. Output is being written to: %s. You will be notified when "
              "it completes. To check interim output, use Read on that file path.\nSession cwd remains %s; directory "
              "changes made by the backgrounded command do not apply to subsequent commands." % (task, out, bg_repo))
    rows = [{"type": "assistant", "isSidechain": False, "timestamp": START, "cwd": str(bg_repo),
             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_" + name, "name": "Bash",
                         "input": {"command": command.format(repo=bg_repo.as_posix(), name=name)}}]}},
            {"type": "user", "isSidechain": False, "timestamp": NOTICE,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_" + name,
                                                      "is_error": False, "content": notice}]},
             "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False}}]
    if notify:
        rows.append({"type": "queue-operation", "operation": "enqueue", "timestamp": DONE, "sessionId": "bg-proof",
                     "content": "<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>toolu_%s</tool-use-id>\n"
                                "<output-file>%s</output-file>\n<status>%s</status>\n<summary>Background command "
                                "\\\"add\\\" completed (exit code %d)</summary>\n</task-notification>"
                                % (task, name, out, status, exit_code)})
    transcript = tmp_path / ("bg_%s.jsonl" % name)
    transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _own_worktree.session_created_worktree(str(wt), str(transcript)) is want


def test_a_saved_bundle_no_longer_retires_an_unpushed_worktree(world):
    """T1 (plan-approved loss): v1 let a bundle or tar under `.claude/scratch` stand in for a push; the rule asks."""
    path = world.wt("bundled")
    write(path, "only.txt", "bundled work\n")
    git(path, "add", "only.txt")
    git(path, "commit", "-qm", "unpushed lane commit")
    (world.scratch / "saved").mkdir(exist_ok=True)
    git(path, "bundle", "create", "-q", str(world.scratch / "saved" / "bundled.bundle"), "--all")
    got, reasons = world.retired(path)
    assert not got and "is on no remote branch" in reasons[0]
