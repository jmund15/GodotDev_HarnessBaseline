"""The delete rule's verdicts (`hooks/_delete_rule.py`) proved on planted facts, in process: no git, shell or child.

A verdict is computed from facts: git's answers (a status, a registration listing, the commits no remote holds, the
files an object store lacks) and the files the rule reads itself. Each row plants both: a folder tree under a temp
project, and for `rule._git` the answer to every read by name (`Git`). A read nobody planted fails the row, so a new
read is one entry in `READS` and its rows.

What a real git answers for each read is `test_floor_retire.py`: its live cases hold each read function to a real
repository, positive and negative. Two of them hold every status line planted here to what git prints for the state it
names: `test_every_planted_status_line_is_what_git_prints` a worktree's (`planted_status_lines`), and
`test_every_planted_submodule_status_line_is_what_git_prints` a submodule's own (`planted_submodule_lines`).
"""
import json
import os
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import _delete_rule as rule
import _git_commit
import _own_worktree
from case import bash
from conftest import LIVE_BACKGROUND_NOTICE, fill, write

PEER_SID = "5eed5eed-aaaa-4bbb-8ccc-0000000000ff"


# --- The git answers a row plants ------------------------------------------------------------------------------------
# argv prefix -> read name, most specific first. The answer is the row's plant for (place, name), else `CLEAN[name]`;
# a `None` in CLEAN has no safe default and a row must plant it. A plant of None is a read that failed.
READS = [
    (("worktree", "list"), "listing"),
    (("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"), "default"),
    (("symbolic-ref", "-q", "--short", "HEAD"), "branch"),
    (("rev-list", "HEAD", "--not", "--remotes"), "unpushed"),
    (("rev-list", "--parents", "HEAD", "--not"), "parents"),
    (("rev-list", "--cherry-pick"), "picked"),
    (("merge-base", "--is-ancestor"), "ancestor"),
    (("rev-list", "--all", "-n", "1"), "anycommit"),
    (("rev-list", "--branches", "--tags", "--not", "--remotes"), "offremote"),
    (("rev-parse", "--show-toplevel"), "toplevel"),
    (("rev-parse", "--path-format=absolute", "--git-common-dir"), "common"),
    (("rev-parse", "--short", "HEAD"), "short"),
    (("rev-parse", "HEAD", "HEAD^{tree}"), "head"),
    (("rev-parse", "--verify", "-q"), "verify"),
    (("branch", "-r", "--contains"), "contains"),
    (("status",), "status"),
    (("remote",), "remote"),
    (("stash", "list"), "stash"),
    (("log", "--all", "--format=%T"), "trees"),
    (("config", "-f", ".gitmodules", "-z"), "gitmodules"),
    (("config", "-f", ".gitmodules", "--list"), "gitmodules_list"),
    (("ls-files", "--stage", "-z"), "stage"),
    (("ls-files", "--others", "--ignored"), "ignored"),
    (("ls-files", "--others", "--exclude-standard"), "others"),
    (("hash-object", "--stdin-paths"), "hash"),
    (("cat-file", "--batch-check"), "store"),
]
CLEAN = {"listing": None, "default": "", "branch": "", "unpushed": "", "anycommit": "", "offremote": "",
         "toplevel": None, "common": None, "short": "abc\n", "head": "abc def\n", "verify": "", "status": "",
         "remote": "origin\n", "stash": "", "trees": "", "gitmodules": "", "gitmodules_list": "", "stage": "",
         "ignored": "", "others": "", "contains": "", "parents": "", "picked": "", "ancestor": ""}
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def read_name(args):
    for prefix, name in READS:
        if tuple(args[:len(prefix)]) == prefix:
            return name
    if args[0] == "rev-parse" and any(a.endswith("^{commit}") for a in args):
        return "refids"
    raise AssertionError("a read the plant does not name: git %s" % " ".join(args))


class Git:
    """Stands in for `rule._git`. `places` names the folders a row plants facts under; `stored` lists the files
    (by their tail) whose content the object store holds: `hash-object` ids each path, `cat-file --batch-check`
    answers `missing` for an id whose path is not among them."""

    def __init__(self, plant):
        self.plant, self.ids = plant, {}

    def where(self, cwd):
        here = os.path.normcase(str(cwd))
        return next((name for path, name in self.plant.places if os.path.normcase(str(path)) == here), here)

    def __call__(self, args, cwd, stdin=None, env=None, timeout=None):
        name, here = read_name(args), self.where(cwd)
        if name == "hash":
            out = []
            for path in stdin.split("\n")[:-1]:
                ident = "%040x" % len(self.ids)
                self.ids[ident] = path
                out.append(ident)
            return "".join(i + "\n" for i in out)
        if name == "store":
            return "".join("%s %s\n" % (i, "blob 1" if self.holds(self.ids[i]) else "missing") for i in stdin.split())
        if (here, name) in self.plant.plants:
            return self.plant.plants[(here, name)]
        if CLEAN[name] is None:
            raise AssertionError("a row must plant %s at %s" % (name, here))
        return CLEAN[name]

    def holds(self, path):
        norm = path.replace("\\", "/")
        return any(norm == s or norm.endswith("/" + s) for s in self.plant.stored)


def no_read(args, cwd, *more, **kw):
    raise AssertionError("an unexpected read: git %s" % " ".join(args))


def decided(judge, *args, **kwargs):
    """`judge(*args)` as the floor calls it: one git read memo per decision (`pre_bash_dispatch.dispatch`)."""
    with _git_commit.memoized():
        return judge(*args, **kwargs)


class Plant:
    """A project under `tmp` with a registered linked worktree at `.claude/worktrees/<name>`; `plants` are the git
    answers by `(place, read)`, `files` and `dirs` write what the rule reads from disk."""

    def __init__(self, tmp, name="wt"):
        self.base = Path(tmp)
        self.root = self.base / "proj"
        self.home = self.root / ".claude" / "worktrees"
        self.target = self.home / name
        self.admin = self.root / ".git" / "worktrees" / name
        self.scratch = self.root / ".claude" / "scratch"
        self.places, self.plants, self.stored = [(self.root, "root"), (self.target, "wt")], {}, ()
        self.scratch.mkdir(parents=True)
        self.target.mkdir(parents=True)
        self.admin.mkdir(parents=True)
        write(self.target, ".git", "gitdir: %s\n" % self.admin.as_posix())
        self.put("root", listing="worktree %s\n\n" % self.target.as_posix(), common=str(self.root / ".git"))

    def put(self, place, **reads):
        for name, value in reads.items():
            self.plants[(place, name)] = value

    def write(self, files=(), dirs=(), at=None):
        for rel, text in dict(files).items():
            write(at or self.target, rel, text)
        for rel in dirs:
            Path(at or self.target, rel).mkdir(parents=True, exist_ok=True)

    def place(self, path, name):
        self.places.append((path, name))

    def submodule(self, name, status="", unpushed="", broken=False, **reads):
        """A populated submodule below the worktree: its `.gitmodules` row and gitlink in the holder's reads, its own
        reads under `name`, and a `.git` link that names a missing admin directory when `broken` (its store, the
        main checkout's `.git/modules/<name>`, stays)."""
        holder, _, leaf = name.rpartition("/")
        sub = self.target / name
        sub.mkdir(parents=True, exist_ok=True)
        admin = self.root / ".git" / "modules" / name
        write(sub, ".git", "gitdir: %s\n" % (admin / "gone" if broken else admin).as_posix())
        admin.mkdir(parents=True, exist_ok=True)
        self.place(sub, name)
        self.put(name, status=status, unpushed=unpushed, **reads)
        cfg, stage = (self.plants.get((holder or "wt", r), "") or "" for r in ("gitmodules", "stage"))
        self.put(holder or "wt", gitmodules=cfg + "submodule.%s.path\n%s\0" % (leaf, leaf),
                 stage=stage + "160000 abc 0\t%s\0" % leaf)
        write(self.target / holder if holder else self.target, ".gitmodules", "[submodule]\n")

    def judge(self, token=None, own=False, prs=(), git=None):
        reasons = []
        git = git or Git(self)
        with patch.object(rule, "_git", git), patch.object(rule, "_open_prs", lambda root: prs):
            got = decided(rule.is_retired_worktree, str(self.target if token is None else token), str(self.root), str(self.root),
                          reasons, own=own)
        assert got == (not reasons), (got, reasons)
        return got, reasons


@pytest.fixture
def plant(tmp_path):
    return Plant(tmp_path)


def said(reasons):
    return " | ".join(reasons)


LIVE_OUTPUTS_STATUS = "!! .devdb/\n!! .env.local\n!! dist-preview/\n!! logs/\n!! test-results/\n"


# --- Linked worktrees: one row per verdict ---------------------------------------------------------------------------
SCRATCH = "!! .claude/scratch/\n"
# Each row: `status` is what `git status --porcelain --untracked-files=all --ignored=matching` prints in the state the
# row names (the `basket` fixture in test_floor_retire makes git print every one); `files` and `dirs` are what the rule reads
# beside it; `want` is whether the worktree retires; `says` are the tokens its reasons carry.
WORKTREES = {
    "clean_and_pushed": ({}, True, ()),
    "godot_cache": ({"status": "!! .godot/\n", "files": {".godot/imported/icon.ctex": "c\n"}}, True, ()),
    "an_emptied_scratch_folder": ({"status": SCRATCH, "dirs": [".claude/scratch/moved_out"]}, True, ()),
    "godot_import_sidecar": ({"status": "?? icon.png.import\n"}, True, ()),
    "godot_import_temp": ({"status": "!! Visual/icon.png.import119908465.tmp\n"
                                     "!! Visual/icon.png.import~RF2c3d449d.TMP\n",
                           "files": {"Visual/icon.png.import119908465.tmp": "t\n",
                                     "Visual/icon.png.import~RF2c3d449d.TMP": "t\n"}}, True, ()),
    "a_tracked_file_gone": ({"status": " D t2.txt\n"}, True, ()),
    "a_staged_edit_then_gone": ({"status": "MD t3.txt\n"}, False, ("MD t3.txt",)),
    "gone_beside_a_new_file": ({"status": " D t2.txt\n?? extra.txt\n", "files": {"extra.txt": "x\n"}}, False,
                               ("?? extra.txt",)),
    "a_jobs_scratch_output": ({"status": SCRATCH, "files": {".claude/scratch/job_x/run/args.json": "{}\n",
                                                           ".claude/scratch/job_x/notes.md": "n\n"}}, True, ()),
    "a_peers_gate_position_file": ({"status": SCRATCH, "files": {
        ".claude/scratch/gate_positions/%s.json" % PEER_SID: "{}\n"}}, True, ()),
    "a_jobs_tmp_log": ({"status": "!! .claude/tmp/\n", "files": {".claude/tmp/run_x/out.log": "log\n"}}, True, ()),
    "a_loose_file_in_scratch": ({"status": SCRATCH, "files": {".claude/scratch/notes.md": "n\n"}}, False,
                                ("loose file in the scratch folder",)),
    "a_peers_output_folder": ({"status": SCRATCH, "files": {".claude/scratch/%s/gate.out" % PEER_SID: "VERDICT=PASS\n"}},
                              False, ("another session's output folder", "!! .claude/scratch")),
    "another_ignored_file": ({"status": "!! .env.local\n"}, False, ("!! .env.local",)),
    "an_ignored_draft": ({"status": "!! draft.tmp\n"}, False, ("!! draft.tmp",)),
    "an_untracked_file": ({"status": "?? notes.md\n"}, False, ("?? notes.md",)),
    "a_file_beside_a_godot_import": ({"status": "?? icon.png.import.bak\n"}, False, ("?? icon.png.import.bak",)),
    "an_edit": ({"status": " M a.txt\n"}, False, (" M a.txt",)),
    "an_unpushed_commit": ({"unpushed": "abc\n"}, False, ("HEAD abc is on no remote branch",)),
    "a_locked_worktree": ({"listing": "locked"}, False, ("worktree is locked",)),
    "an_unregistered_worktree": ({"listing": ""}, False, ("worktree is not registered",)),
    "a_branch_of_an_open_pr": ({"branch": "feat/seven\n", "prs": [(7, "feat/seven")]}, False, ("open PR #7",)),
    "a_branch_of_another_pr": ({"branch": "feat/seven\n", "prs": [(8, "feat/eight")]}, True, ()),
    "a_sync_branch_of_an_open_pr": ({"branch": "sync/pr7\n", "prs": [(7, "feat/seven")]}, False, ("open PR #7",)),
    "a_sync_branch_of_another_pr": ({"branch": "sync/pr7\n", "prs": [(8, "feat/eight")]}, True, ()),
    "an_unreadable_pr_list": ({"branch": "feat/seven\n", "prs": None}, False, ("cannot read open PRs",)),
    "a_detached_head_reads_no_pr_list": ({"prs": None}, True, ()),
}


@pytest.mark.parametrize("name", WORKTREES)
def test_a_linked_worktrees_verdict(plant, name):
    kw, want, says = WORKTREES[name]
    plant.put("wt", **{k: kw[k] for k in ("status", "unpushed", "branch") if k in kw})
    if "listing" in kw:
        plant.put("root", listing=kw["listing"] and "worktree %s\nlocked\n" % plant.target.as_posix())
    plant.write(kw.get("files", ()), kw.get("dirs", ()))
    got, reasons = plant.judge(prs=kw.get("prs", ()))
    assert got is want, reasons
    assert all(token in said(reasons) for token in says), reasons


def test_every_unmet_condition_reaches_one_deny(plant):
    plant.put("wt", status="?? notes.md\n?? extra.txt\n?? icon.png.import.bak\n", unpushed="abc\n")
    plant.write({"notes.md": "n\n", "extra.txt": "x\n", "icon.png.import.bak": "b\n"})
    reasons = plant.judge()[1]
    assert len(reasons) == 2 and all(n in reasons[0] for n in ("notes.md", "extra.txt", "icon.png.import.bak"))
    assert "no remote branch" in reasons[1]


# Every read fails closed with a reason that names it: (place, read, planted answer, the reason's start).
FAILED_READS = [
    ("root", "listing", None, "cannot read worktree registration"),
    ("wt", "status", None, "cannot read worktree status"),
    ("wt", "unpushed", None, "cannot verify HEAD reachability"),
    ("wt", "unpushed", "abc\n", "HEAD abc is on no remote branch (save a bundle of its refs under .claude/scratch "
                                "named for the worktree, then retry)"),
    ("wt", "stage", None, "cannot read submodule status"),
    ("wt", "stage", "160000 abc 0\tOther\0", "cannot read submodule status"),
    ("Sub", "stage", None, "cannot read submodule status"),
    ("wt", "gitmodules", None, "cannot read submodule status"),
    ("Sub", "status", None, "submodule Sub: cannot read status"),
    ("Sub", "unpushed", None, "submodule Sub: cannot verify HEAD reachability"),
    ("Sub", "unpushed", "abc\n", "submodule Sub: HEAD abc is on no remote branch (push it to a remote, then retry)"),
    ("Sub", "status", "?? n.txt\n", "submodule Sub has local files or changes: ?? n.txt"),
]


@pytest.mark.parametrize("place,read,answer,reason", FAILED_READS, ids=["%s.%s=%s" % (r[0], r[1], r[2] is None)
                                                                         for r in FAILED_READS])
def test_each_failed_read_or_condition_is_its_own_reason(plant, place, read, answer, reason):
    plant.submodule("Sub")
    plant.put(place, **{read: answer})
    if (place, read) == ("wt", "gitmodules"):
        plant.put("wt", gitmodules_list="submodule.Sub.path=Sub\n")
    got, reasons = plant.judge()
    assert not got and len(reasons) == 1 and reasons[0].startswith(reason), reasons


# --- Targets the rule does not judge ----------------------------------------------------------------------------------
@pytest.mark.parametrize("token", ["{home}", ".claude/worktrees/wt/Sub", "{elsewhere}", ".claude/worktrees/wt/../wt",
                                   ".claude/worktrees/w*", "$HOME/x", "~/x", ""])
def test_a_target_the_rule_does_not_judge_keeps_the_delete(plant, tmp_path, token):
    """The home itself, a path inside a worktree, a live worktree beside the home, and every token that is no literal
    path."""
    write(plant.target / "Sub", ".git", "gitdir: %s\n" % plant.admin.as_posix())
    elsewhere = tmp_path / "elsewhere_wt"
    write(elsewhere, ".git", "gitdir: %s\n" % plant.admin.as_posix())
    assert not plant.judge(token.format(home=plant.home, elsewhere=elsewhere))[0]


def test_a_clean_worktree_of_another_project_is_not_this_ones(plant, tmp_path):
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    reasons = []
    with patch.object(rule, "_git", Git(plant)):
        assert not decided(rule.is_retired_worktree, str(plant.target), str(other), str(other), reasons) and reasons


def test_every_token_form_of_one_worktree_reaches_the_same_verdict(plant):
    forms = [".claude/worktrees/wt", str(plant.target), plant.target.as_posix()]
    if os.name == "nt":
        drive, rest = os.path.splitdrive(str(plant.target))
        forms.append("/" + drive.rstrip(":").lower() + rest.replace("\\", "/"))
    assert all(plant.judge(form)[0] for form in forms)
    plant.put("wt", status=" M a.txt\n")
    assert not any(plant.judge(form)[0] for form in forms)


def test_the_project_itself_is_never_a_retired_worktree(plant):
    assert not plant.judge(plant.root)[0]


def test_an_unregistered_empty_directory_loses_nothing(plant):
    stray = plant.home / "stray"
    stray.mkdir()
    assert plant.judge(stray)[0]


# --- Which default branches `_pushed` consults ----------------------------------------------------------------------
@pytest.mark.parametrize("ids,scanned", [("c1 c1 t1 t1\n", ["refs/heads/main"]),
                                         ("c1 c2 t1 t2\n", ["refs/heads/main", "origin/main"])])
def test_a_remote_default_naming_the_local_defaults_commit_is_scanned_once(plant, ids, scanned):
    """`_landed_on` reads only the ref's commit, so a second scan of the same commit is the same answer."""
    plant.put("wt", unpushed="abc\n", default="origin/main\n")
    plant.put("wt", refids=ids)
    seen = []
    with patch.object(rule, "_landed_on", lambda repo, ref, *_: seen.append(ref) or False):
        got, reasons = plant.judge()
    assert not got and "is on no remote branch" in reasons[0] and seen == scanned


@pytest.mark.parametrize("answer,vanish", [("", "no origin/HEAD, so no default branch is scanned"),
                                           ("origin/main\n", "the local default branch is gone")])
def test_a_default_ref_the_repository_lacks_scans_what_remains(plant, answer, vanish):
    """`_default_refs`: with `origin/HEAD` unset nothing is scanned; with the local default branch missing the shared
    `rev-parse` fails and the remote's is still scanned alone."""
    plant.put("wt", unpushed="abc\n", default=answer, refids=None, verify="")
    seen = []
    with patch.object(rule, "_landed_on", lambda repo, ref, *_: seen.append(ref) or False):
        plant.judge()
    assert seen == ([] if not answer else ["origin/main"]), vanish


# --- Admin directory gone: a submodule judged by its files ------------------------------------------------------------
def tar(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as archive:
        archive.add(str(src), arcname=Path(src).name)


UNIQUE = ("n.txt", "only here\n")
IGNORED = ("x.local", "local evidence\n")
EDITED = ("s.txt", "an edit no blob holds\n")
# (name, what the store lacks, saved copy, own, retires). A copy is a tar named for the worktree: of the submodule
# folder ("sub") or of the whole worktree ("whole"); "stale" is a submodule tar the file changed after.
ADMIN_GONE = [
    ("held", None, None, False, True),
    ("held_own", None, None, True, True),
    ("unique", UNIQUE, None, False, False),
    ("unique_own", UNIQUE, None, True, False),
    ("unique_sub_tar", UNIQUE, "sub", False, True),
    ("unique_stale_tar", UNIQUE, "stale", False, False),
    ("ignored", IGNORED, None, False, False),
    ("ignored_own", IGNORED, None, True, True),
    ("ignored_whole_tar", IGNORED, "whole", False, True),
    ("edited", EDITED, None, False, False),
]


@pytest.mark.parametrize("name,lacks,copy,own,want", ADMIN_GONE, ids=[c[0] for c in ADMIN_GONE])
def test_a_submodule_whose_admin_directory_is_gone_is_judged_by_its_files(tmp_path, name, lacks, copy, own, want):
    """The admin directory's commits are gone before the delete, so the files are all it can still lose: judged
    against the main checkout's `.git/modules/<path>` store for every worktree, an ignored one a loss unless this
    session created it, and a tar under scratch named for the worktree stands in for what git lacks."""
    plant = Plant(tmp_path, name + "_ag")
    plant.submodule("Sub", broken=True)
    sub = plant.target / "Sub"
    write(sub, "s.txt", "from the seed\n")
    seen = ["s.txt"]
    if lacks:
        write(sub, *lacks)
        seen.append(lacks[0])
    if lacks is IGNORED:
        plant.put("Sub", ignored="%s\0" % lacks[0], others="s.txt\0")
    else:
        plant.put("Sub", others="".join("%s\0" % f for f in seen))
    plant.stored = () if lacks is EDITED else ("s.txt",)
    if copy:
        tar(plant.target if copy == "whole" else sub, plant.scratch / "saved" / ("%s_ag.Sub.tgz" % name))
    if copy == "stale":
        write(sub, lacks[0], "changed after the tar\n")
    got, reasons = plant.judge(own=own)
    assert got is want, reasons
    if not want:
        assert any(lacks[0] in r and "%s_ag.Sub.tar" % name in r for r in reasons), reasons


# --- Every submodule state ---------------------------------------------------------------------------------------------
SUBMODULES = {
    "uninitialized": ({}, None),
    "detached_clean": ({"child": {}}, None),
    "nested_clean": ({"child": {}, "child/grand": {}}, None),
    "untracked_in_child": ({"child": {"status": "?? n.txt\n"}}, "submodule child has local files or changes"),
    "edited_in_child": ({"child": {"status": " M f.txt\n"}}, "submodule child has local files or changes"),
    "untracked_in_grand": ({"child": {}, "child/grand": {"status": "?? n.txt\n"}},
                           "submodule child/grand has local files or changes"),
    "unpushed_in_child": ({"child": {"unpushed": "abc\n"}}, "submodule child: HEAD"),
    "unpushed_in_grand": ({"child": {}, "child/grand": {"unpushed": "abc\n"}}, "submodule child/grand: HEAD"),
    "a_space_in_the_path": ({"Sub Mod": {}}, None),
    "an_unmapped_gitlink_in_a_child": ({"child": {"stage": "160000 abc 0\tStray\0"}}, "cannot read submodule status"),
    "a_dirty_submodule_whose_path_holds_a_space": ({"Sub Mod": {"status": "?? n.txt\n"}},
                                                    "submodule Sub Mod has local files or changes"),
}


@pytest.mark.parametrize("name", SUBMODULES)
def test_each_submodule_state_decides_the_worktree(plant, name):
    subs, reason = SUBMODULES[name]
    for path, reads in subs.items():
        plant.submodule(path, **reads)
    got, reasons = plant.judge()
    assert got == (reason is None), reasons
    assert reason is None or reason in said(reasons), reasons


# --- Ignored files in a worktree this session created ----------------------------------------------------------------
IGNORED_SECRET, IGNORED_CACHE = "!! secret.local\n", "!! .cache/\n"


@pytest.mark.parametrize("own", [False, True])
def test_an_ignored_file_in_a_nested_submodule_keeps_the_worktree_unless_this_session_made_it(plant, own):
    """v1 test_conformance_j8.py v23: a unique ignored file loses work, an ignored cache or empty folder does not; in a
    worktree this session created, the ignored file goes with it (v1's `allow_ignored`, kept by the port: b2 G5)."""
    plant.submodule("child", status=IGNORED_SECRET)
    plant.write({"child/secret.local": "irreplaceable fixture evidence"})
    assert plant.judge(own=own)[0] is own
    plant.put("child", status=IGNORED_CACHE)
    plant.write({"child/.cache/generated": "cache"}, ["child/empty"])
    assert plant.judge(own=own)[0]


OWNED_STATUS = "?? fresh.txt\n M a.txt\nAD gone.txt\n"  # what git prints for the three states the live read tells apart


@pytest.mark.parametrize("status,present,stored,want", [
    ("!! .claude/scratch/\n", {".claude/scratch/notes/x.out": "x\n"}, (), True),
    (" M a.txt\n", {"a.txt": "a saved edit\n"}, ("a.txt",), True),
    (" M a.txt\n", {"a.txt": "an edit no blob holds\n"}, (), False),
    (OWNED_STATUS, {"fresh.txt": "bytes no blob holds\n", "a.txt": "a stored edit\n"},
     ("a.txt",), False),
])
def test_an_owned_worktree_loses_only_what_the_object_store_lacks(plant, status, present, stored, want):
    """`unstored_losses`: every ignored entry goes, and a dirty path loses nothing when its file is gone or its content
    is a blob in the store; one read asks the store about every present file at once."""
    plant.write(present)
    plant.put("wt", status=status)
    plant.stored = stored
    got, reasons = plant.judge(own=True)
    assert got is want, reasons
    if not want:
        lost = {line[3:] for line in reasons[0].split(": ", 1)[1].split("; ")}
        assert lost == ({"fresh.txt"} if "fresh.txt" in status else {"a.txt"}), reasons


# --- Orphans: nothing git holds is lost --------------------------------------------------------------------------------
def orphan(plant, name="orphan", where=None, link=True, repo=None):
    """A worktree whose admin directory git pruned, under the home or at `where`."""
    path = Path(where or plant.home, name)
    path.mkdir(parents=True, exist_ok=True)
    if link:
        admin = Path(repo or plant.root) / ".git" / "worktrees" / name
        write(path, ".git", "gitdir: %s\n" % admin.as_posix())
        (admin.parent).mkdir(parents=True, exist_ok=True)
    plant.place(path, "orphan:" + name)
    return path


def judge_orphan(plant, path, ignored="", others="", stored=("a.txt",), own=False, token=None):
    plant.put("orphan:" + path.name, ignored=ignored, others=others)
    plant.stored = stored
    return plant.judge(token or path, own=own)


LISTED = {"regenerable_ignored": [".devdb/local", ".devdb/preview*", ".env.local", "dist-preview", "logs",
                                  "test-results"]}
OUTPUTS = ".devdb/local/default/backend.sqlite3\0.devdb/preview.env\0.env.local\0dist-preview/index.html\0" \
          "logs/screens/a.png\0test-results/r.txt\0"
JSON = ".claude/scratch/%s/x.json\0" % PEER_SID
# (id, ignored files, untracked files with the tracked ones (the empty index lists them all), retires, says)
ORPHANS = {
    "o_clean": ("", "a.txt\0", True, ()),
    "o_godot": (".godot/imported/c.ctex\0", "a.txt\0", True, ()),
    "o_json": (JSON, "a.txt\0", False, ("x.json", "not a regenerable cache")),
    "o_scratch": (".claude/scratch/x.out\0", "a.txt\0", False, ("x.out",)),
    "o_new": ("", "a.txt\0notes.md\0", False, ("notes.md",)),
    "o_dirty": ("", "a.txt\0", False, ("a.txt",)),
}


@pytest.mark.parametrize("name", ORPHANS)
def test_an_orphan_goes_only_when_git_holds_its_content(plant, name):
    ignored, others, want, says = ORPHANS[name]
    stored = () if name == "o_dirty" else ("a.txt",)
    path = orphan(plant, name)
    got, reasons = judge_orphan(plant, path, ignored, others, stored)
    assert got is want and all(s in said(reasons) for s in says), reasons


def test_an_orphan_deny_names_its_loss_once_and_why_ownership_cannot_clear_it(plant):
    got, reasons = judge_orphan(plant, orphan(plant, "once_ignored"), ignored=JSON, own=True)
    assert not got and said(reasons).count("x.json") == 1 and rule.ORPHAN_OWNERSHIP in reasons
    got, reasons = judge_orphan(plant, orphan(plant, "once_notes"), others="a.txt\0notes.md\0")
    assert not got and said(reasons).count("notes.md") == 1 and rule.ORPHAN_OWNERSHIP not in reasons


def test_an_orphan_deny_names_every_file_up_to_the_cap(plant):
    three = judge_orphan(plant, orphan(plant, "many_o3"), others="a_notes.md\0b_notes.md\0c_notes.md\0", stored=())[1]
    assert len(three) == 1 and all(n in three[0] for n in ("a_notes.md", "b_notes.md", "c_notes.md"))
    many = judge_orphan(plant, orphan(plant, "many_cap"), others="".join("f%02d.md\0" % i for i in range(11)),
                        stored=())[1]
    assert "f00.md" in many[0] and "(and 3 more)" in many[0] and "f10.md" not in many[0]


def test_a_gitless_leftover_is_judged_by_the_same_held_content_test(plant):
    clean = orphan(plant, "g_clean", link=False)
    assert judge_orphan(plant, clean, others="a.txt\0")[0]
    new = orphan(plant, "g_new", link=False)
    assert not judge_orphan(plant, new, others="a.txt\0notes.md\0")[0]


def test_an_orphan_with_a_submodule_needs_the_main_checkouts_store(plant):
    path = orphan(plant, "o_sub")
    write(path, ".gitmodules", "[submodule]\n")
    write(path / "Sub", "s.txt", "s\n")
    plant.put("orphan:o_sub", gitmodules="submodule.Sub.path\nSub\0")
    plant.place(path / "Sub", "orphan:o_sub:Sub")
    plant.put("orphan:o_sub:Sub", others="s.txt\0")
    assert not judge_orphan(plant, path, others="a.txt\0", stored=("a.txt",))[0]  # no store at .git/modules/Sub
    (plant.root / ".git" / "modules" / "Sub").mkdir(parents=True)
    assert judge_orphan(plant, path, others="a.txt\0", stored=("a.txt", "s.txt"))[0]
    assert not judge_orphan(plant, path, others="a.txt\0", stored=("a.txt",))[0]  # the submodule file is edited


@pytest.mark.parametrize("edited", [False, True])
def test_an_orphan_whose_submodule_path_holds_a_space_goes_only_when_clean(plant, edited):
    path = orphan(plant, "sp_o")
    write(path, ".gitmodules", "[submodule]\n")
    write(path / "Sub Mod", "s.txt", "s\n")
    (plant.root / ".git" / "modules" / "Sub Mod").mkdir(parents=True)
    plant.put("orphan:sp_o", gitmodules="submodule.Sub Mod.path\nSub Mod\0")
    plant.place(path / "Sub Mod", "orphan:sp_o:Sub Mod")
    plant.put("orphan:sp_o:Sub Mod", others="s.txt\0")
    got, reasons = judge_orphan(plant, path, others="a.txt\0", stored=("a.txt",) + (() if edited else ("s.txt",)))
    assert got is not edited and (not edited or any("Sub Mod/s.txt" in r for r in reasons)), reasons


# Orphans as sibling folders of the project, judged like those under `.claude/worktrees/`.
def siblings(plant, tmp_path, name, other_repo=False, live=False):
    where = tmp_path / "siblings"
    other = tmp_path / "other_repo"
    path = orphan(plant, name, where=where, repo=other if other_repo else None)
    if live:
        write(path, ".git", "gitdir: %s\n" % plant.admin.as_posix())
    return path


@pytest.mark.parametrize("name,kw,args,want", [
    ("s_clean", {}, {"others": "a.txt\0"}, True),
    ("s_godot", {}, {"ignored": ".godot/imported/c.ctex\0", "others": "a.txt\0"}, True),
    ("s_new", {}, {"others": "a.txt\0notes.md\0"}, False),
    ("s_json", {}, {"ignored": JSON, "others": "a.txt\0"}, False),
    ("s_other", {"other_repo": True}, {"others": "a.txt\0"}, False),
    ("s_live", {"live": True}, {"others": "a.txt\0"}, False),
    ("s_out", {}, {"ignored": OUTPUTS, "others": "a.txt\0"}, False),
])
def test_a_sibling_orphan_of_this_repository(plant, tmp_path, name, kw, args, want):
    path = siblings(plant, tmp_path, name, **kw)
    assert judge_orphan(plant, path, **args)[0] is want


def listed(plant, value):
    write(plant.root, ".claude/skills/project_subsystems/adaptation.json",
          value if isinstance(value, str) else json.dumps(value))


@pytest.mark.parametrize("name,ignored,others,stored,want", [
    ("s_out", OUTPUTS, "a.txt\0", ("a.txt",), True),
    ("s_out_dirty", OUTPUTS, "a.txt\0", (), False),
    ("s_out_extra", OUTPUTS + ".devdb/tmp/an.py\0", "a.txt\0", ("a.txt",), False),
    ("s_out_nested", "src/.devdb/local/x.db\0", "a.txt\0", ("a.txt",), False),
    ("s_out_logs", "src/logs/unique.txt\0", "a.txt\0", ("a.txt",), False),
])
def test_the_projects_listed_outputs_go_and_nothing_else(plant, tmp_path, name, ignored, others, stored, want):
    listed(plant, LISTED)
    path = siblings(plant, tmp_path, name)
    got, reasons = judge_orphan(plant, path, ignored, others, stored)
    assert got is want, reasons
    if name == "s_out_extra":
        assert ".devdb/tmp/an.py" in said(reasons)


def test_a_project_listed_output_of_another_repository_is_no_orphan_of_this_one(plant, tmp_path):
    listed(plant, LISTED)
    assert not judge_orphan(plant, siblings(plant, tmp_path, "s_out_other", other_repo=True), OUTPUTS, "a.txt\0")[0]


@pytest.mark.parametrize("name,files,status,want", [
    ("live_out", {".devdb/local/x": "", ".devdb/preview.env": "", ".env.local": "", "dist-preview/index.html": "",
                  "logs/screens/a.png": "", "test-results/r.txt": ""}, "", True),
    ("live_extra", {".devdb/local/x": "", ".devdb/tmp/an.py": ""}, "", False),
])
def test_a_live_worktrees_listed_outputs_go_and_nothing_else(plant, name, files, status, want):
    listed(plant, LISTED)
    plant.write(files)
    plant.put("wt", status=LIVE_OUTPUTS_STATUS if want else "!! .devdb/\n")
    got, reasons = plant.judge()
    assert got is want and (want or ".devdb" in said(reasons)), reasons


@pytest.mark.parametrize("bad", ['{"regenerable_ignored": ".devdb/local"}', '{"regenerable_ignored": ["*"]}',
                                 '{"regenerable_ignored": ["../**", "/c/x", ""]}', "{not json"])
def test_a_broken_output_list_only_narrows_what_may_go(plant, tmp_path, bad):
    listed(plant, bad)
    assert not judge_orphan(plant, siblings(plant, tmp_path, "s_out"), OUTPUTS, "a.txt\0")[0]


def test_the_projects_listed_outputs_include_a_test_runs_files(tmp_path):
    write(tmp_path, ".claude/skills/project_subsystems/adaptation.json",
          json.dumps({"regenerable_ignored": [".runsettings", "logs", "addons/*/libs/~*"]}))
    patterns = rule.project_patterns(tmp_path)
    for name in (".runsettings", "logs/run.log", "addons/debug_draw_3d/libs/~libdd3d.windows.editor.x86_64.dll"):
        assert rule._listed(name, patterns), name


# Owner-picked outputs (U62078): `.agents/`, `bin/`, `obj/` when git ignores them and `desktop.ini` regenerate; a
# lookalike name, an unignored `bin/` and an edited tracked file are work. One row per verdict, run through both
# readers of the rule's answer: `losses` (a linked worktree's status) and `_unheld_files` (an orphan's listing).
OUTPUT_ROWS = {
    "all_four": ({"status": "!! bin/\n!! src/obj/\n!! .agents/\n?? d/desktop.ini\n"},
                 {"ignored": "bin/a\0src/obj/b\0.agents/c\0", "others": "a.txt\0d/desktop.ini\0"}, True),
    "bin_unignored": ({"status": "?? src/bin/tool.cs\n"}, {"others": "a.txt\0src/bin/tool.cs\0"}, False),
    "bin_tracked_edit": ({"status": " M tools/bin/run.sh\n"}, {"others": "a.txt\0tools/bin/run.sh\0"}, False),
}


@pytest.mark.parametrize("name", OUTPUT_ROWS)
def test_a_worktrees_owner_picked_outputs_go_and_work_stays(plant, name):
    status, _orphan, want = OUTPUT_ROWS[name]
    plant.put("wt", **status)
    assert plant.judge()[0] is want


@pytest.mark.parametrize("name", OUTPUT_ROWS)
def test_an_orphans_owner_picked_outputs_go_and_work_stays(plant, name):
    _status, reads, want = OUTPUT_ROWS[name]
    path = orphan(plant, "f_o_" + name)
    assert judge_orphan(plant, path, stored=("a.txt",), **reads)[0] is want


# --- What a status line loses -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,lost", [
    (" D gone.txt\n", []), (" M missing.txt\n", [" M missing.txt"]),
    (' M "caf\\303\\251.txt"\n', [' M "caf\\303\\251.txt"']), ("?? icon.png.import\n", []),
    ("?? notes.import.md\n", ["?? notes.import.md"]),
])
def test_what_a_status_line_loses(tmp_path, status, lost):
    assert rule.losses(status, tmp_path) == lost


@pytest.mark.parametrize("status,made,lost", [
    ("!! bin/\n", "bin/Debug/app.dll", False), ("!! src/obj/\n", "src/obj/project.assets.json", False),
    ("!! .agents/\n", ".agents/workflows/run.md", False), ("?? Art/desktop.ini\n", "Art/desktop.ini", False),
    ("!! Art/desktop.ini\n", "Art/desktop.ini", False), ("?? bin/tool.cs\n", "bin/tool.cs", True),
    ("!! Binary/\n", "Binary/x.dat", True), ("?? Art/desktop.ini.bak\n", "Art/desktop.ini.bak", True),
])
def test_what_a_status_line_loses_for_the_owner_picked_outputs(tmp_path, status, made, lost):
    """U62078: `.agents/`, `bin/`, `obj/` (when git ignores them) and `desktop.ini` regenerate; a lookalike name or an
    unignored `bin/` is work."""
    write(tmp_path, made, "x\n")
    assert bool(rule.losses(status, tmp_path)) is lost


# --- A clone under scratch -----------------------------------------------------------------------------------------------
def clone_plant(tmp_path, **reads):
    """A standalone clone under the project's scratch whose reads answer `reads` (a clean pushed clone by default)."""
    plant = Plant(tmp_path)
    clone = plant.scratch / "rc_fix" / "clone"
    (clone / ".git").mkdir(parents=True)
    plant.place(clone, "clone")
    plant.put("clone", toplevel=clone.as_posix() + "\n", listing="worktree %s\n\n" % clone.as_posix(), **reads)
    return plant, clone


CLONES = {
    "clean": ({}, None),
    "cached": ({"status": "!! .search-index/\n"}, None),
    "tagged": ({}, None),
    "dirty": ({"status": " M a.txt\n"}, "local files or changes"),
    "untracked": ({"status": "?? notes.md\n"}, "local files or changes"),
    "noted": ({"status": "!! .env.local\n"}, ".env.local"),
    "stashed": ({"stash": "stash@{0}: WIP on main\n", "verify": "5ta5h\n"}, "stash"),
    "ahead": ({"offremote": "abc\n"}, "no remote branch"),
    "tagged_local": ({"offremote": "abc\n"}, "no remote branch"),
    "no_remote": ({"remote": "", "anycommit": "abc\n", "offremote": "abc\n"}, "no remote"),
    "linked_worktrees": ({"listing": "worktree /a\n\nworktree /b\n\n"}, "linked worktrees"),
    "an_unreadable_status": ({"status": None}, "cannot read clone status"),
}


@pytest.mark.parametrize("name", CLONES)
def test_a_scratch_clone_goes_only_when_clean_and_pushed(tmp_path, name):
    reads, reason = CLONES[name]
    plant, clone = clone_plant(tmp_path)
    plant.put("clone", **reads)
    if name == "cached":
        plant.write({".search-index/search.db": "x"}, at=clone)
    why = []
    with patch.object(rule, "_git", Git(plant)):
        got = decided(rule.is_regenerable_clone, str(clone), str(plant.root), str(plant.root), why)
    assert got is (reason is None) and (reason is None or (len(why) == 1 and reason in why[0])), why


def test_a_scratch_target_that_is_no_standalone_clone_keeps_the_delete(tmp_path):
    plant, clone = clone_plant(tmp_path)
    linked = plant.scratch / "rc_fix" / "w"
    write(linked, ".git", "gitdir: /elsewhere/.git/worktrees/w\n")
    plain = plant.scratch / "rc_fix" / "plain_dir"
    write(plain, "evidence.txt")
    for path in (linked, plain):
        why = []
        with patch.object(rule, "_git", Git(plant)):
            assert not decided(rule.is_regenerable_clone, str(path), str(plant.root), str(plant.root), why)
        assert "not a standalone clone" in why[0]


@pytest.mark.parametrize("token", [".claude/scratch", "{scratch}", "{outside}", "{clean}/../clean",
                                   ".claude/scratch/rc_*/clean", "$HOME/clean", "~/clean", "{fix}/missing", ""])
def test_a_token_the_clone_rule_cannot_judge_keeps_the_delete(tmp_path, token):
    plant, clone = clone_plant(tmp_path)
    outside = tmp_path / "clone"
    (outside / ".git").mkdir(parents=True)
    token = token.format(scratch=plant.scratch, outside=outside, clean=clone, fix=clone.parent)
    with patch.object(rule, "_git", no_read):  # no read is made: a token that is no literal or no scratch clone
        assert not decided(rule.is_regenerable_clone, token, str(plant.root), str(plant.root))


def test_a_clean_clone_in_every_token_form(tmp_path):
    plant, clone = clone_plant(tmp_path)
    forms = [os.path.relpath(clone, plant.root).replace("\\", "/"), str(clone)]
    if os.name == "nt":
        drive, rest = os.path.splitdrive(str(clone))
        forms.append("/" + drive.rstrip(":").lower() + rest.replace("\\", "/"))
    with patch.object(rule, "_git", Git(plant)):
        assert all(decided(rule.is_regenerable_clone, form, str(plant.root), str(plant.root)) for form in forms)


# --- Repositories inside a worktree's scratch and tmp -----------------------------------------------------------------
PLANT_DIR = ".claude/tmp/pe_absent_delete_dxf68feq"
# (id, folders holding a `.git` and the reads each plants, files beside them, the ignored entry git prints, retires).
# A clone's reads are a clean pushed clone's unless planted; `trees` is `git log --all --format=%T`.
INNER = {
    "tmp_unborn": ({".claude/tmp/run_x/probe": {}}, (), "!! .claude/tmp/", True),
    "tmp_work": ({".claude/tmp/run_x/probe": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n"}}, (), "!! .claude/tmp/", False),
    "tmp_file": ({".claude/tmp/run_x/probe": {"status": "?? notes.md\n"}}, (".claude/tmp/run_x/probe/notes.md",),
                 "!! .claude/tmp/", False),
    "plant_work": ({}, (PLANT_DIR + "/.claude/scratch/%s/o.txt" % PEER_SID,), "!! .claude/tmp/", True),
    "plant_repo": ({PLANT_DIR + "/repo": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n", "trees": "t1\n"}}, (), "!! .claude/tmp/",
                   False),
    "plant_fixture": ({PLANT_DIR + "/.claude/scratch/probe": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n", "trees": EMPTY_TREE + "\n"}},
                      (PLANT_DIR + "/.claude/scratch/%s/out/job.log" % PEER_SID,), "!! .claude/tmp/", True),
    "plant_remote": ({PLANT_DIR + "/repo": {"anycommit": "abc\n", "offremote": "abc\n", "trees": EMPTY_TREE + "\n"}},
                     (), "!! .claude/tmp/", False),
    "plant_dirty": ({PLANT_DIR + "/repo": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n", "trees": EMPTY_TREE + "\n",
                                           "status": "?? notes.md\n"}}, (), "!! .claude/tmp/", False),
    "plant_beside": ({".claude/tmp/notes/probe": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n"}},
                     (PLANT_DIR + "/.claude/scratch/%s/o.txt" % PEER_SID,), "!! .claude/tmp/", False),
    "plant_like": ({".claude/tmp/pe_absent_delete_dxf68feq0/probe": {"anycommit": "abc\n", "remote": "", "offremote": "abc\n"}}, (),
                   "!! .claude/tmp/", False),
    "nested": ({".claude/scratch/clone": {"status": "?? notes.md\n"}}, (".claude/scratch/clone/notes.md",),
               "!! .claude/scratch/", False),
}


@pytest.mark.parametrize("name", INNER)
def test_a_job_worktrees_scratch_and_tmp_repositories(plant, name):
    repos, files, line, want = INNER[name]
    plant.write({f: "x\n" for f in files})
    for rel, reads in repos.items():
        repo = plant.target / rel
        write(repo, ".git/HEAD", "ref: refs/heads/main\n")
        plant.place(repo, rel)
        plant.put(rel, toplevel=repo.as_posix() + "\n", listing="worktree %s\n\n" % repo.as_posix(), **reads)
    plant.put("wt", status=line + "\n")
    got, reasons = plant.judge()
    assert got is want, reasons


@pytest.mark.parametrize("name,makes,want", [
    ("stripped", ".claude/scratch/recon_fixture_x/repo/.git/objects/ab/cdef", True),
    ("stripped_ref", ".claude/scratch/x/repo/.git/refs/heads/work", False),
])
def test_a_git_directory_that_names_no_commit_is_no_repository(plant, name, makes, want):
    """A `.git` with no HEAD and no ref holds loose objects no commit names; one with a ref holds a commit."""
    plant.write({makes: "0" * 40 + "\n"})
    plant.put("wt", status=SCRATCH)
    repo = plant.target / makes.split("/.git/")[0]
    plant.place(repo, "inner")
    plant.put("inner", toplevel=None, listing=None)
    assert plant.judge()[0] is want


@pytest.mark.parametrize("name,reads,want,says", [
    ("ok", {}, True, ""), ("bad", {"offremote": "abc\n"}, False, "no remote branch"),
    ("link", None, False, "linked worktree, not a clean pushed clone")])
def test_a_clone_under_a_jobs_scratch_is_judged_by_the_clone_rule(plant, name, reads, want, says):
    """A clean pushed clone goes; one with a commit no remote holds, or a linked worktree in place of a clone, keeps
    the job's worktree."""
    repo = plant.target / ".claude" / "scratch" / "pubcopy"
    if reads is None:
        write(repo, ".git", "gitdir: /elsewhere/.git/worktrees/tpl\n")
        plant.place(repo, name)
        plant.put(name, listing="")
    else:
        write(repo, ".git/HEAD", "ref: refs/heads/main\n")
        plant.place(repo, name)
        plant.put(name, toplevel=repo.as_posix() + "\n", listing="worktree %s\n\n" % repo.as_posix(), **reads)
    plant.put("wt", status=SCRATCH)
    got, reasons = plant.judge()
    assert got is want and says in said(reasons), reasons


# --- The verdict CLI ----------------------------------------------------------------------------------------------------
def test_the_verdict_cli_prints_each_worktree(plant, capsys, monkeypatch):
    dirty = plant.home / "cli_dirty"
    write(dirty, ".git", "gitdir: %s\n" % plant.admin.as_posix())
    plant.place(dirty, "dirty")
    plant.put("dirty", status=" M a.txt\n")
    plant.put("root", listing="worktree %s\n\nworktree %s\n\n" % (plant.target.as_posix(), dirty.as_posix()))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plant.root))
    monkeypatch.chdir(plant.root)
    with patch.object(rule, "_git", Git(plant)), patch.object(rule, "_open_prs", lambda r: []), \
            patch.object(rule, "session_owns", lambda path: False):
        assert rule.main([str(plant.target)]) == 0
        assert rule.main([str(plant.target), str(dirty)]) == 1
        lines = capsys.readouterr().out.splitlines()
        assert rule.main([]) == 2
    assert lines[0] == "%s: retired" % plant.target and lines[-1].startswith("%s: blocked: worktree has local" % dirty)


def test_the_created_hypothetical_judges_an_orphan_as_the_guard_does(plant, capsys, monkeypatch):
    path = orphan(plant, "o_hypothetical")
    plant.put("orphan:o_hypothetical", ignored=JSON, others="a.txt\0")
    plant.stored = ("a.txt",)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plant.root))
    monkeypatch.chdir(plant.root)
    with patch.object(rule, "_git", Git(plant)), \
            patch.object(rule, "_open_prs", lambda r: []):
        assert rule.main(["--created", str(path)]) == 1
    assert "blocked" in capsys.readouterr().out


def planted_status_lines():
    """Every status line the rows above plant: `test_floor_retire.py` holds each to what git prints."""
    texts = [kw.get("status", "") for kw, _want, _says in WORKTREES.values()]
    texts += [status["status"] for status, _orphan, _want in OUTPUT_ROWS.values()]
    texts += [line + "\n" for *_rest, line, _want in INNER.values()]
    texts += [reads.get("status") or "" for reads, _reason in CLONES.values()]
    texts += [LIVE_OUTPUTS_STATUS, SCRATCH]
    return {line for text in texts for line in text.splitlines() if line}


def planted_submodule_lines():
    """Every line the submodule rows plant as a submodule's own status: `test_floor_retire.py` holds each to what git
    prints in a nested submodule."""
    texts = [reads.get("status", "") for subs, _reason in SUBMODULES.values() for reads in subs.values()]
    texts += [answer for place, read, answer, _reason in FAILED_READS if place == "Sub" and read == "status" and answer]
    texts += [IGNORED_SECRET, IGNORED_CACHE]
    return {line for text in texts for line in text.splitlines() if line}


@pytest.mark.parametrize("ancestor,flags", [("", [True, False]), (None, [True, True])])
def test_the_remote_defaults_patch_scan_is_skipped_only_when_its_history_lies_inside_the_local_ones(plant, ancestor,
                                                                                                    flags):
    """`_pushed`: when the local default holds neither HEAD nor its patches and the remote's history is inside it, the
    remote's patch scan (the costliest read) cannot find more, so it is skipped; any other relation scans it."""
    plant.put("wt", unpushed="abc\n", default="origin/main\n", refids="c1 c2 t1 t2\n", parents="c0 p0\n",
              picked="c0\n", ancestor=ancestor)
    seen = []
    with patch.object(rule, "_landed_on", lambda repo, ref, tree=None, patches=True: seen.append(patches) or False):
        plant.judge()
    assert seen == flags


# --- The rule through the floor: the guard's own decisions, on planted verdicts ------------------------------------------
@pytest.fixture
def floor(plant, tmp_path, monkeypatch):
    """The floor judging `plant`'s project; the rule reads the planted facts, the guard's own reads meet no repository."""
    guard_temp = tmp_path / "guard_temp"
    guard_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(guard_temp))
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(guard_temp))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plant.root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no_login"))
    monkeypatch.chdir(plant.root)
    with patch.object(rule, "_git", Git(plant)), patch.object(rule, "_open_prs", lambda root: []):
        yield plant


def rm(command, plant, transcript=None):
    return bash(command, plant.root, transcript_path=transcript, session_id="retire-rows")


def peer_login(tmp_path, monkeypatch, *rows):
    """A login whose `sessions/<pid>.json` registry holds one live session per `(name, sessionId, cwd)` row."""
    login = tmp_path / "login"
    (login / "sessions").mkdir(parents=True)
    for pid, (peer, sid, cwd) in enumerate(rows, 4000):
        (login / "sessions" / ("%d.json" % pid)).write_text(json.dumps(
            {"pid": pid, "sessionId": sid, "name": peer, "status": "idle", "kind": "interactive", "cwd": str(cwd)}),
            encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(login))


@pytest.mark.parametrize("where,want", [("in", "deny"), ("inside", "deny"), ("other", "allow"), ("self", "allow"),
                                        ("none", "allow")])
def test_a_live_peer_session_working_in_a_worktree_keeps_it(floor, tmp_path, monkeypatch, where, want):
    sid = "5eed5eed-aaaa-4bbb-8ccc-00000000000%d" % ("in inside other self none".split().index(where) + 1)
    cwd = {"in": floor.target, "inside": floor.target / "Sub", "other": floor.home / "elsewhere",
           "self": floor.target}.get(where)
    if cwd is not None:
        peer_login(tmp_path, monkeypatch, ("peer-" + where, sid, cwd))
    else:
        peer_login(tmp_path, monkeypatch)
    transcript = tmp_path / (sid + ".jsonl") if where == "self" else None
    got = rm("rm -rf .claude/worktrees/wt", floor, transcript)
    assert got.kind == want
    if where == "in":
        assert "peer-in" in got.reason


def test_a_chained_delete_needs_every_target_retired_and_the_home_stays(floor):
    dirty = floor.home / "dirty"
    write(dirty, ".git", "gitdir: %s\n" % floor.admin.as_posix())
    floor.place(dirty, "dirty")
    floor.put("dirty", status=" M a.txt\n")
    floor.put("root", listing="worktree %s\n\nworktree %s\n\n" % (floor.target.as_posix(), dirty.as_posix()))
    assert rm("rm -rf .claude/worktrees/wt", floor).kind == "allow"
    assert rm("rm -rf .claude/worktrees/wt && rm -rf .claude/worktrees/dirty", floor).denied
    assert rm("rm -rf .claude/worktrees", floor).denied


def test_the_floors_redirects_and_pipes_change_no_scratch_clone_verdict(floor):
    clean, dirty = floor.scratch / "rc_fix" / "clean", floor.scratch / "rc_fix" / "dirty"
    for clone, name, reads in ((clean, "clean", {}), (dirty, "dirty", {"status": " M a.txt\n"})):
        write(clone, ".git/HEAD", "ref: refs/heads/main\n")
        floor.place(clone, name)
        floor.put(name, toplevel=clone.as_posix() + "\n", listing="worktree %s\n\n" % clone.as_posix(), **reads)
    write(floor.scratch / "rc_fix" / "plain_dir", "evidence.txt")
    for command, want in (("rm -rf .claude/scratch/rc_fix/clean 2>&1 | tail -1", "allow"),
                          ("rm -rf .claude/scratch/rc_fix/clean > /dev/null", "allow"),
                          ("rm -rf .claude/scratch/rc_fix/clean 2>/dev/null", "allow"),
                          ("rm -rf .claude/scratch/rc_fix/dirty 2>&1", "deny"),
                          ("rm -rf .claude/scratch/rc_fix/clean .claude/scratch/rc_fix/plain_dir", "allow"),
                          ("rm -rf .claude/scratch/rc_fix/clean && rm -rf .claude/scratch/rc_fix/dirty", "deny"),
                          ("rm -rf .claude/scratch", "deny")):
        assert rm(command, floor).kind == want, command


@pytest.mark.parametrize("where,leaf,command", [("lo/empty_leftover/redcheck", None, "allow"),
                                                ("lo/filled_leftover/redcheck", "notes.txt", "allow"),
                                                ("lo/git_leftover/redcheck", ".git/", "deny")])
def test_leftovers_under_scratch(floor, where, leaf, command):
    folder = floor.scratch / where
    folder.mkdir(parents=True)
    if leaf == ".git/":
        (folder / ".git").mkdir()
    elif leaf:
        write(folder, leaf, "keep\n")
    assert rm("rm -r .claude/scratch/" + where, floor).kind == command


def test_an_empty_directory_outside_scratch_keeps_its_deny(floor):
    (floor.root / ".claude/rc_empty_outside").mkdir()
    assert rm("rm -r .claude/rc_empty_outside", floor).denied


def test_scratch_folders_holding_no_repository(floor, tmp_path, monkeypatch):
    write(floor.scratch, "copytree/big.bin")
    write(floor.scratch, "peers/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d/out.txt")
    write(floor.scratch, "no_ids/f.txt")
    unsaved = floor.scratch / "unsaved_repo"
    write(unsaved, ".git/HEAD", "ref: refs/heads/main\n")
    write(unsaved, "n.txt")
    floor.place(unsaved, "unsaved")
    floor.put("unsaved", remote="", anycommit="abc\n", offremote="abc\n", toplevel=unsaved.as_posix() + "\n",
              listing="worktree %s\n\n" % unsaved.as_posix())
    held = floor.scratch / "heldtree" / "wt"
    write(held, ".git", "gitdir: %s\n" % floor.admin.as_posix())
    floor.put("root", listing="worktree %s\n\nworktree %s\n\n" % (floor.target.as_posix(), held.as_posix()))
    verdicts = {}
    for command, want in (("rm -rf .claude/scratch/copytree", "allow"), ("rm -rf .claude/scratch/heldtree", "deny"),
                          ("rm -rf .claude/scratch/unsaved_repo", "deny"),
                          ("rm -rf .claude/scratch/peers/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "deny"),
                          ("rm -rf .claude/scratch/peers/*", "deny"), ("rm -rf .claude/scratch/no_ids/*", "allow")):
        verdicts[command] = rm(command, floor)
        assert verdicts[command].kind == want, command
    assert "push its commits to a remote" in verdicts["rm -rf .claude/scratch/unsaved_repo"].reason
    peer_login(tmp_path, monkeypatch, ("peer-scratch", "5eed5eed-aaaa-4bbb-8ccc-000000000007", floor.scratch / "copytree"))
    assert "peer-scratch" in rm("rm -rf .claude/scratch/copytree", floor).reason


# --- Ownership of a backgrounded add -------------------------------------------------------------------------------------
START, NOTICE, BORN, DONE, LATE = ("2026-10-24T09:00:00.000Z", "2026-10-24T09:00:02.000Z", "2026-10-24T09:00:03.000Z",
                                   "2026-10-24T09:00:05.000Z", "2026-10-24T09:01:00.000Z")
VISIBLE = ('cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD && '
           'W=.claude/worktrees/{name} && git -C $W status --short')
PLAIN = 'cd "{repo}" && git worktree add -q -b scratch/{name} .claude/worktrees/{name} HEAD'
OK_OUTPUT = "Preparing worktree (new branch 'scratch/{name}')\nHEAD is now at 1234abc init\n"


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
def test_a_backgrounded_add_is_owned_only_on_its_completed_notice(plant, tmp_path, name, command, output, kwargs, want):
    """The transcript's rows decide, given git's one answer (a linked worktree: its git dir is not the common one);
    `test_floor_retire.test_a_worktree_the_session_added_is_owned_only_when_git_says_it_is_a_linked_worktree` is that
    answer from a real worktree."""
    notify, status = kwargs.get("notify", True), kwargs.get("status", "completed")
    exit_code, born, out_file = kwargs.get("exit_code", 0), kwargs.get("born", BORN), kwargs.get("out_file", True)
    task = "bg" + name
    out = tmp_path / "tasks" / (task + ".output")
    out.parent.mkdir(parents=True)
    if out_file:
        out.write_text(output.replace("{name}", name), encoding="utf-8")
    wt = plant.home / name
    write(wt, ".git", "gitdir: %s\n" % (plant.root / ".git" / "worktrees" / name).as_posix())
    stamp = datetime.fromisoformat(born.replace("Z", "+00:00")).timestamp()
    os.utime(wt / ".git", (stamp, stamp))
    notice = fill(LIVE_BACKGROUND_NOTICE, task=task, output=out, cwd=plant.root)
    rows = [{"type": "assistant", "isSidechain": False, "timestamp": START, "cwd": str(plant.root),
             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_" + name, "name": "Bash",
                         "input": {"command": command.format(repo=plant.root.as_posix(), name=name)}}]}},
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
    linked = "%s\n%s\n%s\n" % (wt, plant.root / ".git" / "worktrees" / name, plant.root / ".git")
    with patch.object(_own_worktree, "_git", lambda args, cwd: linked):
        assert _own_worktree.session_created_worktree(str(wt), str(transcript)) is want


def test_a_submodule_whose_files_git_cannot_list_is_the_owners_call_even_with_a_tar(tmp_path):
    """A read that failed names no file, so no tar can hold it: the deny says the owner decides."""
    plant = Plant(tmp_path, "unread_ag")
    plant.submodule("Sub", broken=True)
    write(plant.target / "Sub", "s.txt", "from the seed\n")
    plant.put("Sub", others=None)
    tar(plant.target, plant.scratch / "saved" / "unread_ag.Sub.tgz")
    got, reasons = plant.judge()
    assert not got and any("the owner decides" in r and "cannot list files" in r for r in reasons), reasons


# --- A saved copy stands in for a push (S-F3: v1 `_saved_copy_holds`) ------------------------------------------------------
NESTED_LINES = {"A  kept.txt", "?? side.txt"}  # what git prints for the nested repository below; `test_floor_retire` checks


@pytest.mark.parametrize("copy,want", [(None, False), ("tar", True), ("tar missing a file", False)])
def test_a_nested_copy_in_a_worktrees_scratch_a_tar_holds_retires(plant, copy, want):
    """A repository with a staged and an untracked file, no commit and no remote: it goes with the worktree only when a
    tar under the project's scratch, named for it, holds the same bytes of every file its status would lose."""
    name = "sv_nest_" + (copy or "none").replace(" ", "_")
    nested = plant.target / ".claude" / "scratch" / "job" / (name + "_copy")
    write(nested, ".git/HEAD", "ref: refs/heads/main\n")
    write(nested, "kept.txt", "kept work\n")
    write(nested, "side.txt", "side work\n")
    plant.place(nested, "nested")
    plant.put("nested", toplevel=nested.as_posix() + "\n", listing="worktree %s\n\n" % nested.as_posix(),
              status="A  kept.txt\n?? side.txt\n", remote="")
    plant.put("wt", status=SCRATCH)
    if copy:
        tar(nested, plant.scratch / "saved" / (name + "_copy.tgz"))
    if copy == "tar missing a file":
        write(nested, "extra.txt", "added after the tar\n")
        plant.put("nested", status="A  kept.txt\n?? side.txt\n?? extra.txt\n")
    got, reasons = plant.judge()
    assert got is want, reasons


@pytest.mark.parametrize("held", [False, True])
def test_a_bundle_standing_in_for_a_push_is_found_by_name_and_must_hold_every_commit(plant, held):
    """`_saved_copy_holds` reads the bundle's heads through git (`test_floor_retire` proves that read on a real
    bundle); a bundle that verifies and lists HEAD preserves a worktree's unpushed commit."""
    plant.put("wt", unpushed="abc\n", head="abc def\n")
    write(plant.scratch, "wt.bundle", "a bundle\n")
    with patch.object(rule, "_bundle_holds", lambda path, head, repo: held):
        got, reasons = plant.judge()
    assert got is held, reasons
