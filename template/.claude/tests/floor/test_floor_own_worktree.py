"""Ownership of a live worktree, proven from the transcript (`_own_worktree.session_created_worktree`): the
discards and `reset --hard` a session may make in a worktree it created, and the recursive deletes of a worktree,
where ownership lets a worktree's ignored files and stored edits go (v1's rule, kept by the port: b2 G5). Rows of
the game project's `test_git_guardrails_checkout_own_worktree.py` (MaddManager's
copy is a subset) and of `test_pattern_enforcer_own_worktree.py`, the delete half of the same ownership proof
(it imports `dangerous_shell_guard` as `pe`; it is not on T1's file list, but its cases are floor cases, R1).

`ROW` below is that live transcript row, captured from a session that ran `git worktree add`, inline.
"""
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from case import bash, derived
from conftest import (LIVE_BACKGROUND_NOTICE, LIVE_LANE_CHAIN, LIVE_LANE_CREATED, LIVE_LANE_HEREDOC,
                      LIVE_TASK_NOTIFICATION, fill, write)

ROW = {"type": "assistant", "message": {"role": "assistant", "content": [{
    "type": "tool_use", "id": "toolu_01CsZ5wFhVRWCR8EetcJb7H3", "name": "Bash",
    "input": {"command": "cd \"C:/work/project\" && git status --short -- .claude/scripts/harness_tests.py "
                         ".claude/adaptation.json .claude/rules/harness_tooling.md .claude/hooks/foreground_wait_guard.py "
                         ".claude/tests/test_harness_tests_*.py .claude/tests/test_harness_close_cadence.py; git worktree "
                         "add -q -b scratch/cut-runner .claude/worktrees/cut-runner HEAD && git -C "
                         ".claude/worktrees/cut-runner log --oneline -1",
              "description": "Check runner files are clean and create the runner job's worktree"},
    "caller": {"type": "direct"}}]}}
ADD = "git worktree add .claude/worktrees/mine HEAD"
# The ownership probe: `reset --hard` of the clean worktree passes only when this session created it.
PROBE = "git -C .claude/worktrees/mine reset --hard"


def g(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=True).stdout


class Own:
    """v1's `OwnWorktree` fixture: a repo with a pushed `main` and `.claude/worktrees/mine` added inside the
    [started, finished] window the transcript rows carry."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.root = tmp / "repo"
        self.root.mkdir()
        g(self.root, "init", "-q", "-b", "main")
        (self.root / ".gitignore").write_text(".claude/scratch/\nnotes/\n", encoding="utf-8")
        g(self.root, "add", ".gitignore")
        g(self.root, "commit", "-qm", "init")
        g(self.root, "remote", "add", "origin", str(tmp / "origin.git"))
        g(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        g(self.root, "config", "branch.main.remote", "origin")
        g(self.root, "config", "branch.main.merge", "refs/heads/main")
        self.started = (datetime.now(timezone.utc) - timedelta(milliseconds=100)).isoformat()
        g(self.root, "worktree", "add", "-q", "-b", "scratch/mine", ".claude/worktrees/mine", "HEAD")
        self.finished = datetime.now(timezone.utc).isoformat()
        write(self.root, ".claude/worktrees/mine/notes/evidence.txt", "a job's own scratch\n")
        self.wt = self.root / ".claude/worktrees/mine"

    def copy_to(self, tmp):
        """This fixture copied under `tmp`, `.git` included, with the worktree's two link files repointed and the
        worktree `.git` file's mtime kept (the ownership check compares it with the transcript window)."""
        import copy
        import shutil
        other = copy.copy(self)
        other.tmp, other.root = tmp, tmp / "repo"
        shutil.copytree(self.root, other.root, symlinks=True)
        other.wt = other.root / ".claude/worktrees/mine"
        link, admin = other.wt / ".git", other.root / ".git/worktrees/mine/gitdir"
        stamp = (self.wt / ".git").stat()
        for path, text in ((link, "gitdir: %s\n" % (other.root / ".git/worktrees/mine").as_posix()),
                           (admin, "%s\n" % link.as_posix())):
            with open(path, "r+", encoding="utf-8", newline="\n") as fh:  # git hides `.git`; "w" cannot open it
                fh.write(text)
                fh.truncate()
        os.utime(link, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return other

    def created_row(self, command=None):
        row = json.loads(json.dumps(ROW))
        row["cwd"] = str(self.root)
        row["timestamp"] = self.started
        block = row["message"]["content"][0]
        block["input"]["command"] = command if command is not None else block["input"]["command"].replace(
            "cut-runner", "mine").replace("C:/work/project", self.root.as_posix())
        return row

    def result_row(self, output="c85bc63ba fix(overnight): the no-regret audit call carries the drive's task and plan flags",
                   is_error=False):
        return {"type": "user", "timestamp": self.finished, "cwd": str(self.root),
                "message": {"role": "user", "content": [{"type": "tool_result",
                            "tool_use_id": ROW["message"]["content"][0]["id"], "is_error": is_error,
                            "content": output}]},
                "toolUseResult": "Error: " + output if is_error else {"stdout": "", "stderr": "", "interrupted": False}}

    def rows(self):
        return [self.created_row(), self.result_row()]

    def transcript(self, rows):
        path = self.tmp / "transcript.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return path

    def run(self, command, rows=None, cwd=None):
        rows = self.rows() if rows is None else rows
        return bash(command, cwd or self.root, session_id="own-discard", transcript_path=self.transcript(rows))

    def code(self, command, rows=None, cwd=None):
        return 2 if self.run(command, rows, cwd).denied else 0

    def dirty(self, root):
        (root / ".gitignore").write_text("dirty edit\n", encoding="utf-8")

    def blocked(self, rows, command=PROBE):
        """True when `command` (by default the ownership probe) is denied under the transcript `rows`."""
        return self.run(command, rows).denied


@pytest.fixture(scope="session")
def _own_template(tmp_path_factory):
    saved = {k: os.environ.pop(k) for k in [k for k in os.environ if k.startswith("GIT_")]}
    try:
        return Own(tmp_path_factory.mktemp("own_template"))
    finally:
        os.environ.update(saved)


@pytest.fixture
def own(tmp_path, monkeypatch, _own_template):
    """v1's fixture, built once and copied per case (`.git` included), so no state crosses cases."""
    elsewhere = tmp_path.parent / ("elsewhere_" + tmp_path.name)
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    fixture = _own_template.copy_to(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(fixture.root))
    return fixture


# --- discards in the owned worktree (test_git_guardrails_checkout_own_worktree.py) -----------------------------
def test_clean_owned_worktree_checkout_allows(own):
    own.dirty(own.wt)
    for verb in ("checkout --", "restore"):
        assert own.code('git -C "%s" %s .gitignore' % (own.wt.as_posix(), verb)) == 0


def test_bare_checkout_from_inside_own_worktree_allows(own):
    own.dirty(own.wt)
    for command in ("git checkout -- .gitignore", "git checkout HEAD -- .gitignore", "git restore .gitignore"):
        assert own.code(command, cwd=own.wt) == 0, command
    assert own.code('cd "%s" && git checkout -- .gitignore && git status --short' % own.wt.as_posix()) == 0
    own.dirty(own.root)
    assert own.code("git checkout HEAD -- .gitignore") == 2


def test_subdirectory_and_preceding_cd_allow(own):
    (own.wt / "nested").mkdir()
    own.dirty(own.wt)
    assert own.code('cd "%s/nested"; git restore ../.gitignore' % own.wt.as_posix()) == 0


def test_once_assigned_literal_is_checked_at_its_real_path(own):
    command = 'P=.gitignore; git restore "$P"'
    assert own.code(command) == 0
    own.dirty(own.root)
    assert own.code(command) == 2


def test_repeated_chdir_uses_each_previous_directory(own):
    own.dirty(own.wt)
    own.dirty(own.root)
    peer = own.root.as_posix()
    assert own.code('git -C "%s" -C . restore .gitignore' % peer, cwd=own.wt) == 2
    assert own.code('git -C "%s" -C .claude/worktrees/mine restore .gitignore' % peer, cwd=own.wt) == 0
    assert own.code('P="%s"; git -C "$P" -C "$PWD" restore .gitignore' % peer, cwd=own.wt) == 0


def _add_submodule(own, parent):
    src = own.tmp / "subsrc"
    if not src.exists():
        src.mkdir()
        g(src, "init", "-q", "-b", "main")
        (src / "lib.txt").write_text("lib\n", encoding="utf-8")
        g(src, "add", "lib.txt")
        g(src, "commit", "-qm", "init")
    g(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", src.as_posix(), "Sub")
    sub = parent / "Sub"
    (sub / "lib.txt").write_text("dirty edit\n", encoding="utf-8")
    return sub


def test_submodule_owner_resolves(own):
    sub = _add_submodule(own, own.wt)
    assert own.code('git -C "%s" checkout -- lib.txt' % sub.as_posix()) == 0
    assert own.code("git -C Sub restore lib.txt", cwd=own.wt) == 0


def test_submodule_of_shared_checkout_denies(own):
    sub = _add_submodule(own, own.root)
    assert own.code('git -C "%s" checkout -- lib.txt' % sub.as_posix()) == 2


def test_orphan_worktree_uses_the_explicit_object_store(own):
    from _delete_rule import is_retired_worktree
    shutil.rmtree(own.wt / "notes", ignore_errors=True)
    own.dirty(own.wt)
    shutil.rmtree(own.root / ".git/worktrees/mine")
    lost = []
    assert not is_retired_worktree(".claude/worktrees/mine", str(own.root), str(own.root), lost)
    assert any(".gitignore" in reason for reason in lost)
    g(own.root, "hash-object", "-w", str(own.wt / ".gitignore"))
    assert is_retired_worktree(".claude/worktrees/mine", str(own.root), str(own.root))


def _tuple_loop_rows(own, command=None):
    command = command or (
        'cd "C:/work/App"; for spec in "wt1 feat/alpha" "wt2 feat/beta"; '
        'do set -- $spec; git worktree add .claude/worktrees/$1 -b $2 main 2>&1 | tail -1; '
        'git -C .claude/worktrees/$1 submodule update --init Lib 2>&1 | tail -2; done')
    command = command.replace("C:/work/App", own.root.as_posix()).replace("wt1", "mine").replace("wt2", "mine2")
    result = own.result_row(
        "HEAD is now at cf1ee7e32 merge: fix/alpha — a new batch segment runs in a batch of its own\n"
        "Cloning into 'C:/work/App/.claude/worktrees/wt1/Lib'...\n"
        "Submodule path 'Lib': checked out 'd2b1b7acdcfb98e213fffffe023c2e4bb684598a'\n"
        "HEAD is now at cf1ee7e32 merge: fix/alpha — a new batch segment runs in a batch of its own\n"
        "Cloning into 'C:/work/App/.claude/worktrees/wt2/Lib'...\n"
        "Submodule path 'Lib': checked out 'd2b1b7acdcfb98e213fffffe023c2e4bb684598a'")
    return [own.created_row(command), result]


def test_literal_tuple_loop_creation_allows_discard(own):
    from _own_worktree import _first_visible_add, _unrolled_loops
    own.dirty(own.wt)
    rows = _tuple_loop_rows(own)
    for verb in ("checkout --", "restore"):
        assert own.code("W=.claude/worktrees/mine; git -C $W %s .gitignore" % verb, rows) == 0
    command = rows[0]["message"]["content"][0]["input"]["command"]
    assert set(_first_visible_add(_unrolled_loops(command) or "", str(own.root))) == {
        own.wt, own.root / ".claude/worktrees/mine2"}
    assert own.code("git branch -D peer", rows) == 2


def test_loop_with_echo_between_adds_counts_every_iteration(own):
    from _own_worktree import _first_visible_add, _unrolled_loops
    wt = own.root / ".claude/worktrees"
    command = ('cd "%s"; for n in mine mine2; do git worktree add -q -b fx-$n .claude/worktrees/$n HEAD && '
               'echo ok-$n; done; git worktree list' % own.root.as_posix())
    assert set(_first_visible_add(_unrolled_loops(command), str(own.root))) == {wt / "mine", wt / "mine2"}
    for between in ("exit 1", "cd ..", "echo $X"):
        command = "git worktree add .claude/worktrees/mine HEAD; %s; git worktree add .claude/worktrees/mine2 HEAD" % between
        assert _first_visible_add(command, str(own.root)) == [wt / "mine"], between
    command = ("git worktree add .claude/worktrees/mine HEAD && echo ok && "
               "git worktree add .claude/worktrees/mine2 HEAD")
    assert _first_visible_add(command, str(own.root)) == [wt / "mine"]


def test_tuple_loop_ambiguous_creation_stays_denied(own):
    own.dirty(own.wt)
    base = _tuple_loop_rows(own)[0]["message"]["content"][0]["input"]["command"]
    target = 'git -C "%s" restore .gitignore' % own.wt.as_posix()
    for command in (base.replace("set -- $spec", "set -- $OTHER"), base.replace("feat/alpha", "feat/*"),
                    base.replace("set -- $spec;", "set -- $spec; continue;"),
                    base.replace("set -- $spec;", "set -- $spec; shift;"), base.replace("$2 main", "$3 main"),
                    base.replace("for spec", "IFS=/; for spec"), base.replace("done", "done || true")):
        assert own.code(target, _tuple_loop_rows(own, command)) == 2, command
    rows = _tuple_loop_rows(own)
    rows[1]["message"]["content"][0]["content"] = "fatal: add failed"
    assert own.code(target, rows) == 2


def test_shared_checkout_and_unknown_evidence_deny(own):
    own.dirty(own.root)
    for command in ("git checkout -- .gitignore", "git restore .gitignore", "git checkout -- .",
                    "git restore --source=HEAD -- .gitignore"):
        assert own.code(command) == 2, command


@pytest.mark.parametrize("form", ["abs", "rel", "cd", "cwd"])
def test_reset_hard_of_clean_own_worktree_allows_in_every_form(own, form):
    command, cwd = {"abs": ('git -C "%s" reset --hard HEAD' % own.wt.as_posix(), None),
                    "rel": ("git -C .claude/worktrees/mine reset --hard HEAD", None),
                    "cd": ('cd "%s" && git reset --hard HEAD' % own.wt.as_posix(), None),
                    "cwd": ("git reset --hard HEAD", own.wt)}[form]
    assert own.code(command, cwd=cwd) == 0


def test_reset_hard_of_own_worktree_with_an_edit_denies(own):
    own.dirty(own.wt)
    assert own.code('git -C "%s" reset --hard HEAD' % own.wt.as_posix()) == 2
    assert own.code("git reset --hard HEAD", cwd=own.wt) == 2


def test_reset_hard_in_shared_checkout_or_a_peer_worktree_denies(own):
    assert own.code("git reset --hard") == 2
    assert own.code('git -C "%s" reset --hard HEAD' % own.root.as_posix(), cwd=own.wt) == 2
    assert own.code('git -C "%s" reset --hard HEAD' % own.wt.as_posix(), rows=[]) == 2
    row = own.created_row('git worktree add "%s/other/mine" HEAD' % own.tmp.as_posix())
    assert own.code('git -C "%s" reset --hard HEAD' % own.wt.as_posix(), [row, own.result_row()]) == 2


def test_same_basename_peer_and_peer_recreation_deny(own):
    own.dirty(own.wt)
    row = own.created_row('git worktree add "%s/other/mine" HEAD' % own.tmp.as_posix())
    assert own.code('git -C "%s" restore .gitignore' % own.wt.as_posix(), [row, own.result_row()]) == 2
    rows = own.rows()
    g(own.root, "worktree", "remove", "--force", ".claude/worktrees/mine")
    g(own.root, "worktree", "add", "-q", "-b", "peer", ".claude/worktrees/mine", "HEAD")
    own.dirty(own.wt)
    assert own.code('git -C "%s" checkout -- .gitignore' % own.wt.as_posix(), rows) == 2


def test_missing_failed_or_timestamp_free_creation_denies(own):
    own.dirty(own.wt)
    failed = own.result_row()
    failed["message"]["content"][0]["is_error"] = True
    no_time = own.created_row()
    no_time.pop("timestamp")
    for rows in ([], [own.created_row()], [own.created_row(), failed], [no_time, own.result_row()]):
        assert own.code('git -C "%s" restore .gitignore' % own.wt.as_posix(), rows) == 2


SKIPPED_ADDS = ["false && {add}; sleep 2", "{add} || true", "true || {add}", "if false; then {add}; fi; true",
                'false && W=.claude/worktrees/mine; git worktree add "$W" HEAD']


@pytest.mark.parametrize("shape", SKIPPED_ADDS)
def test_skipped_or_masked_add_cannot_claim_creation(own, shape):
    row = own.created_row(shape.format(add=ADD))
    own.dirty(own.wt)
    assert own.code('git -C "%s" restore .gitignore' % own.wt.as_posix(), [row, own.result_row()]) == 2
    assert own.blocked([row, own.result_row()])


def test_all_must_deny_riders(own):
    own.dirty(own.wt)
    own.dirty(own.root)
    allow = 'git -C "%s" restore .gitignore' % own.wt.as_posix()
    peer = 'git -C "%s" restore .gitignore' % own.root.as_posix()
    for rider in ('echo "$(%s)"' % peer, 'echo "`%s`"' % peer, "sudo " + peer, "env " + peer,
                  "printf .gitignore | xargs " + peer,
                  'W="%s"; W="%s"; git -C "$W" restore .gitignore' % (own.wt.as_posix(), own.root.as_posix()),
                  'P=other; P=.gitignore; git restore "$P"'):
        assert own.code(allow + "; " + rider) == 2, rider


def test_unresolved_mutations_cannot_lend_ownership_to_aliases(own, monkeypatch):
    own.dirty(own.wt)
    own.dirty(own.root)
    monkeypatch.setenv("PEER", own.root.as_posix())
    for mutation in ("A=$PEER", "A+=$PEER", "unset A", "read A"):
        command = 'A="%s"; %s; B=$A; FINAL=$B; git -C "$FINAL" restore .gitignore' % (own.wt.as_posix(), mutation)
        assert own.code(command) == 2, mutation
        for original in (own.wt, own.root / ".claude/.cache/proof"):
            command = 'A="%s"; %s; B=$A; FINAL=$B; rm -rf "$FINAL"' % (original.as_posix(), mutation)
            assert own.run(command).denied, (mutation, original)


@pytest.mark.parametrize("command", ['printf "%s" "git restore .gitignore"', 'sudo printf "%s" git restore .gitignore',
                                     'printf input | xargs printf "%s" git restore .gitignore'])
def test_read_only_mentions_allow(own, command):
    assert own.code(command) == 0


LAUNCHERS = ("bash -c", "bash -lc", "bash -o errexit -c", "bash -O extglob -c", "sh -c", "eval", "env X=1 bash -c")


def test_shell_launcher_discards_cannot_borrow_ownership(own):
    """MaddManager's deny rows: an owned-worktree discard inside a shell launcher does not borrow the worktree's
    ownership (the game project allowed the non-sudo forms; drop-list D-LB)."""
    own.dirty(own.wt)
    body = 'git -C "%s" checkout -- .gitignore' % own.wt.as_posix()
    for launcher in LAUNCHERS + ("sudo bash -c",):
        assert own.code(launcher + " " + shlex.quote(body)) == 2, launcher
    assert own.code("bash -c " + shlex.quote("sh -c " + shlex.quote(body))) == 2
    own.dirty(own.root)
    peer = 'git -C "%s" checkout -- .gitignore' % own.root.as_posix()
    for launcher in LAUNCHERS + ("sudo bash -c",):
        assert own.code(launcher + " " + shlex.quote(peer)) == 2, launcher


def test_substitution_and_shell_heredoc_discards_cannot_borrow_ownership(own):
    """F38: a discard in a `$(...)`, backtick or process substitution, or in a heredoc a shell reads, runs in a child
    shell like a `bash -c` body and borrows no worktree ownership."""
    own.dirty(own.wt)
    body = 'git -C "%s" restore .gitignore' % own.wt.as_posix()
    for command in ('echo "$(%s)"' % body, "echo `%s`" % body, "cat <(%s)" % body, "bash <<'EOF'\n%s\nEOF" % body):
        assert own.code(command) == 2, command
    assert own.code(body) == 0


@pytest.mark.parametrize("body", ["git reset --hard", "git clean -fd", "git push --force", "git branch -D peer",
                                  "git stash clear", "git stash drop", "git reflog expire --all", "git gc --prune=now",
                                  "git filter-branch", "git filter-repo"])
def test_every_floor_git_predicate_inside_a_launcher(own, body):
    assert own.code("bash -c " + shlex.quote(body)) == 2


@pytest.mark.parametrize("command", [
    *[launcher + " " + shlex.quote("git status --short; git show HEAD:.gitignore") for launcher in LAUNCHERS + ("sudo bash -c",)],
    "bash -nc 'git checkout -- .'", "sh -n -c 'git checkout -- .'",
    "bash -c 'git clean -nd; git restore --staged file'", "printf '%s' \"bash -c 'git checkout -- .'\""])
def test_read_only_launcher_bodies_and_noexec_allow(own, command):
    assert own.code(command) == 0


def test_redirected_repository_does_not_inherit_ownership(own):
    own.dirty(own.root)
    assert own.code('git -C "%s" --work-tree="%s" restore .gitignore' % (own.wt.as_posix(), own.root.as_posix())) == 2


# --- recursive delete of the owned worktree (test_pattern_enforcer_own_worktree.py) -----------------------------
def test_created_by_this_session_owns_it_for_discards(own):
    assert not own.blocked(own.rows())


def test_an_owned_worktree_takes_its_ignored_files_with_it(own):
    """CLAUDE.core.md §Shell Discipline: "Ownership means this session provably created the worktree; only then may
    every ignored file go with it." v2's T1 made this an ask; the v1 port keeps the allow (b2, G5). A transcript that
    does not prove the creation still denies, naming the ignored file."""
    assert own.run("rm -rf .claude/worktrees/mine").kind == "allow"
    got = own.run("rm -rf .claude/worktrees/mine", rows=[])
    assert got.denied and "!! notes/" in got.reason


def test_chained_delete_of_a_removable_worktree(own):
    (own.wt / "notes/evidence.txt").unlink()
    for command in ('cd "%s" && rm -rf .claude/worktrees/mine' % own.root.as_posix(),
                    "rm -rf .claude/worktrees/mine; git worktree prune"):
        assert not own.blocked(own.rows(), command), command
    assert own.blocked(own.rows(), 'cd "%s" && rm -rf .claude/worktrees/mine peer' % own.root.as_posix())


def test_an_unpushed_worktree_names_failed_retirement(own):
    (own.wt / "notes/evidence.txt").unlink()
    write(own.wt, "local.txt", "local only")
    g(own.wt, "add", "local.txt")
    g(own.wt, "commit", "-qm", "local")
    head = g(own.wt, "rev-parse", "--short", "HEAD").strip()
    got = own.run("rm -rf .claude/worktrees/mine")
    assert got.denied and ("not retired: HEAD " + head + " is on no remote branch") in got.reason


def test_unowned_worktree_names_the_retirement_failure(own):
    got = own.run("rm -rf .claude/worktrees/mine", rows=[])
    assert got.denied and "is not retired" in got.reason and "worktree has local files or changes" in got.reason


@pytest.mark.parametrize("command,output", [
    ("{add} 2>&1 | tail -1; git -C .claude/worktrees/mine log --oneline -1", "HEAD is now at 0d317e543 init\n0d317e543 init"),
    ("git worktree add -q -b x .claude/worktrees/mine HEAD 2>&1 | tail -2; H=$(git rev-parse --short HEAD); echo \"$H\"",
     "0d317e5"),
    ("{add}; true", ""),
    ("cd {root}; {add} 2>&1 | tail -2; true", ""),
    ("cd {root}; H=$(git rev-parse --short HEAD); git grep -n x; {add} 2>&1 | tail -1", ""),
])
def test_first_add_with_visible_errors_owns_whatever_follows(own, command, output):
    row = own.created_row(command.format(add=ADD, root=own.root.as_posix()))
    assert not own.blocked([row, own.result_row(output)])


@pytest.mark.parametrize("command,output", [
    ("{add} 2>&1 | tail -1; true", "fatal: '.claude/worktrees/mine' already exists"),
    ("{add}; true", "error: could not lock config file"),
    ("{add} 2>/dev/null; true", ""), ("{add} 2>&1 | grep -v fatal; true", ""), ("{add} 2>&1 | head -1; true", ""),
    ("cd elsewhere; {add} 2>&1 | tail -1; true", ""), ("git grep -q x && {add} 2>&1 | tail -1", ""),
    ("set -e; {add} 2>&1 | tail -1", ""), ("exit 0; {add}", ""),
])
def test_failed_or_hidden_add_output_is_not_ours(own, command, output):
    assert own.blocked([own.created_row(command.format(add=ADD)), own.result_row(output)])


OTHER_ADD = "git worktree add -q -b other .claude/worktrees/other HEAD"


@pytest.mark.parametrize("command,output,blocked", [
    ("{other} && {add}; git worktree list", "", False), ("{other}; {add}", "", False),
    ("cd {root} && {other} && {add} 2>&1 | tail -1", "", False),
    ("{other} && {add}; git worktree list", "fatal: '.claude/worktrees/other' already exists", True),
    ("{other} && {add} 2>/dev/null; true", "", True), ("{other} && {add} || true", "", True),
])
def test_each_leading_add_with_visible_errors_counts(own, command, output, blocked):
    row = own.created_row(command.format(other=OTHER_ADD, add=ADD, root=own.root.as_posix()))
    assert own.blocked([row, own.result_row(output)]) is blocked


def test_visible_add_survives_a_later_commands_exit_status(own):
    row = own.created_row("git worktree add -q -b x .claude/worktrees/mine main 2>&1 | tail -2; python3 - <<'EOF'\n"
                          "raise SystemExit(1)\nEOF")
    assert not own.blocked([row, own.result_row(
        'Exit code 1\nTraceback (most recent call last):\r\n  File "<stdin>", line 7, in <module>\r\n'
        "FileNotFoundError: [Errno 2] No such file or directory: '.claude/worktree", is_error=True)])
    for output in ("Exit code 128\nfatal: '.claude/worktrees/mine' already exists",
                   'PreToolUse:Bash hook error: [python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/pre_bash_dispatch.py"]: '
                   "BLOCKED commit (CLAUDE.core.md §Shell Discipline: an index write runs in its own Bash call)",
                   "Permission for this action was denied by the Claude Code auto mode classifier. "
                   "Reason: [Interfere With Workloads]."):
        assert own.blocked([row, own.result_row(output, is_error=True)]), output[:30]


def _loop_row(own, command, shift=0.0):
    row = own.created_row(command)
    if shift:
        row["timestamp"] = datetime.fromtimestamp(datetime.fromisoformat(row["timestamp"]).timestamp() + shift,
                                                  timezone.utc).isoformat()
    return row


@pytest.mark.parametrize("loop", [
    "for j in mine mine2; do git worktree add -q -b claude/finish-$j .claude/worktrees/$j 88bb2dea0 2>&1 | tail -1; done; "
    "git worktree list | grep -c finish-",
    "for k in mine mine2; do git worktree add -q -b claude/finish-$k .claude/worktrees/$k main; done; git worktree list",
    'for k in mine mine2\ndo\n  git worktree add -q -b x-${k} ".claude/worktrees/${k}" HEAD\ndone',
    'cd "{root}" && for k in mine mine2; do git worktree add -q -b x-$k .claude/worktrees/$k HEAD; done',
    None])
def test_literal_loop_adds_prove_every_listed_worktree(own, loop):
    g(own.root, "worktree", "add", "-q", "-b", "scratch/mine2", ".claude/worktrees/mine2", "HEAD")
    own.finished = datetime.now(timezone.utc).isoformat()
    both = PROBE + " && git -C .claude/worktrees/mine2 reset --hard"
    if loop is None:
        row = _loop_row(own, "for j in mine2; do git worktree add -q -b x-$j .claude/worktrees/$j HEAD; done")
        assert own.blocked([row, own.result_row("")], both)
    else:
        assert not own.blocked([_loop_row(own, loop.replace("{root}", own.root.as_posix())), own.result_row("")], both)


LOOP_ADD = "git worktree add -q -b x-$j .claude/worktrees/$j HEAD"


@pytest.mark.parametrize("command", [
    "for j in $X; do %s; done" % LOOP_ADD, "for j in $(echo mine); do %s; done" % LOOP_ADD,
    "for j in mine $(echo a); do %s; done" % LOOP_ADD, "for j in min*; do %s; done" % LOOP_ADD,
    "for j in {mine,a}; do %s; done" % LOOP_ADD, "for j in mine; do for i in a b; do %s; done; done" % LOOP_ADD,
    "for j in mine; do %s 2>/dev/null; done" % LOOP_ADD, "for j in mine; do %s || true; done" % LOOP_ADD,
    "for j in mine; do %s; done || true" % LOOP_ADD, "for j in mine; do %s; done | tail -1" % LOOP_ADD,
    "for j in mine; do %s; done 2>/dev/null" % LOOP_ADD, "for j in mine; do %s & done" % LOOP_ADD,
    "for j in mine; do break; %s; done" % LOOP_ADD, 'for j in mine; do [ -z "$X" ] && continue; %s; done' % LOOP_ADD,
    "for j in mine; do %s-$OTHER; done" % LOOP_ADD, "for j in other another; do %s; done" % LOOP_ADD,
    "while true; do %s; done" % LOOP_ADD])
def test_loop_that_cannot_prove_each_add_stays_unproven(own, command):
    assert own.blocked([_loop_row(own, command), own.result_row("")])


def test_loop_with_a_failure_line_or_foreign_timestamp_stays_unproven(own):
    command = "for j in mine; do git worktree add -q -b x-$j .claude/worktrees/$j HEAD; done"
    for text in ("fatal: '.claude/worktrees/mine' already exists", "error: could not lock config"):
        assert own.blocked([_loop_row(own, command), own.result_row(text)])
    early = _loop_row(own, command, shift=-7200)
    own.finished = datetime.fromtimestamp(datetime.fromisoformat(early["timestamp"]).timestamp() + 60,
                                          timezone.utc).isoformat()
    assert own.blocked([early, own.result_row("")])


def _lineage_blocked(own, entries=(("parent", "clear"),), parent_head=(), began=60):
    """True when the ownership probe is denied in `child.jsonl`, which lists `entries` as its `derived-from` row and
    whose first row is stamped `began` seconds after the worktree was created; `parent.jsonl` holds the creation
    (after `parent_head` rows)."""
    parent = [*parent_head, own.created_row(), own.result_row()]
    (own.tmp / "parent.jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent), encoding="utf-8")
    first = datetime.fromisoformat(own.finished) + timedelta(seconds=began)
    child = [derived("child", *entries), {"type": "user", "timestamp": first.isoformat(), "message": {"role": "user", "content": "go"}}]
    (own.tmp / "child.jsonl").write_text("".join(json.dumps(r) + "\n" for r in child), encoding="utf-8")
    return bash(PROBE, own.root, session_id="own-discard", transcript_path=own.tmp / "child.jsonl").denied


def test_creation_in_the_clear_parent_transcript_counts(own):
    assert not _lineage_blocked(own)
    assert not _lineage_blocked(own, (("parent", "clear"), ("child", "compact")))


def test_an_env_var_alone_reaches_no_earlier_transcript(own, monkeypatch):
    (own.tmp / "sid-earlier.jsonl").write_text(
        json.dumps(own.created_row()) + "\n" + json.dumps(own.result_row()) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-earlier")
    assert own.blocked([])


@pytest.mark.parametrize("label,entries,head", [
    ("a resume entry between the clear and the parent", (("parent", "clear"), ("parent", "resume"), ("child", "compact")), ()),
    ("a parent that was itself resumed", (("parent", "clear"),), [derived("parent", ("parent", "resume"))]),
    ("a parent reached by a subprocess entry", (("parent", "subprocess"), ("child", "compact")), ()),
])
def test_creation_behind_a_resume_is_another_processs(own, label, entries, head):
    assert _lineage_blocked(own, entries, head)


def test_creation_stamped_after_the_child_began_is_another_processs(own):
    assert _lineage_blocked(own, began=-60)


def test_other_full_path_with_same_basename_is_not_ours(own):
    row = own.created_row('git -C "%s" worktree add -b peer "%s" HEAD' % (own.root.as_posix(),
                                                                          (own.tmp / "other/mine").as_posix()))
    assert own.blocked([row, own.result_row()])


def test_recreated_path_does_not_inherit_ownership(own):
    rows = own.rows()
    g(own.root, "worktree", "remove", "--force", ".claude/worktrees/mine")
    g(own.root, "worktree", "add", "-q", "-b", "peer-recreated", ".claude/worktrees/mine", "HEAD")
    write(own.root, ".claude/worktrees/mine/notes/peer.txt", "peer evidence")
    assert own.blocked(rows)


@pytest.mark.parametrize("rider", ['echo "$(rm -rf peer)"', 'echo "`rm -rf peer`"', "sudo rm -rf peer",
                                   "env rm -rf peer", "printf peer | xargs rm -rf",
                                   'W=.claude/worktrees/mine; W=peer; rm -rf "$W"'])
def test_delete_riders_remain_denied(own, rider):
    assert own.run("rm -rf .claude/worktrees/mine; " + rider).denied


def test_untracked_nonworktree_scratch_directory_is_removable(own):
    write(own.root, ".claude/scratch/made/evidence", "irreplaceable")
    assert own.run("rm -rf .claude/scratch/made", rows=[]).kind == "allow"


def test_peer_worktree_in_temp_is_not_plain_temp_cleanup(own, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setenv("TMP", str(own.tmp.parent))
    monkeypatch.setenv("TEMP", str(own.tmp.parent))
    assert bash("rm -rf .claude/worktrees/mine", own.root).denied


def test_clone_with_ignored_evidence_is_not_recoverable(own):
    clone = own.root / ".claude/scratch/probe"
    g(own.root, "clone", "-q", str(own.root), str(clone))
    write(clone, "notes/evidence", "ignored evidence")
    assert own.run("rm -rf .claude/scratch/probe", rows=[]).denied


def test_owner_answer_does_not_decide_a_scratch_delete(own):
    write(own.root, ".claude/scratch/made/evidence", "a study output")
    said = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Should I delete .claude/scratch/made? My pick: yes."}]}}
    typed = {"type": "user", "uuid": "owner-yes", "message": {"role": "user", "content": "Yes"}}
    assert own.run("rm -rf .claude/scratch/made", rows=[]).kind == "allow"
    assert own.run("rm -rf .claude/scratch/made", rows=[said, typed]).kind == "allow"


def test_creation_named_only_in_prose_stays_denied(own):
    prose = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "git worktree add -q -b scratch/mine .claude/worktrees/mine HEAD"}]}}
    assert own.blocked([prose])


def test_scratch_output_goes_with_any_pushed_worktree_but_not_a_loose_file_or_a_session_folder(own):
    """MaddManager's form (`7b8e6e66`): a job's output folder goes; a loose file directly in scratch is evidence
    (the game project allowed a loose file; drop-list D-LS, kept by the owner 2026-11-23)."""
    delete = "rm -rf .claude/worktrees/mine"
    (own.wt / "notes/evidence.txt").unlink()
    write(own.wt, ".claude/scratch/a-job/output.txt", "a job's output")
    assert not own.blocked([], delete)
    loose = own.wt / ".claude/scratch/notes.txt"
    loose.write_text("a file nobody filed under a job", encoding="utf-8")
    assert own.blocked([], delete)
    loose.unlink()
    write(own.wt, ".claude/scratch/947c1a2b-0000-4000-8000-123456789abc/record.json", "{}")
    assert own.blocked([], delete)


def test_another_sessions_worktree_stays_denied(own):
    assert own.blocked([])


# --- an empty pipeline segment in a recorded call (`split_at('', '|')` is `[]`) --------------------------------
CREATE = "git worktree add -q -b scratch/mine .claude/worktrees/mine HEAD"


@pytest.mark.parametrize("tail", [";\n", " ; ; ", " && ;", " |", "\n\n", " ;\n# done"])
def test_a_blank_or_trailing_segment_after_the_add_does_not_crash_the_floor(own, tail):
    own.dirty(own.wt)
    rows = [own.created_row(CREATE + tail), own.result_row()]
    assert not own.run("git -C .claude/worktrees/mine checkout HEAD -- .gitignore", rows).denied


def test_head_checkout_of_a_file_in_the_own_worktree_allows_after_a_blank_segment(own):
    own.dirty(own.wt)
    rows = [own.created_row(CREATE + ";\n;"), own.result_row()]
    assert not own.run("git checkout HEAD -- .gitignore", rows, cwd=own.wt).denied


def test_blank_segment_helpers_return_nothing_not_raise():
    import _own_worktree as ow
    for segment in ("", "  ", "|", "# note"):
        assert ow._visible_git_invocation(segment, "/x") == []
        assert ow._visible_add_target(segment, "/x") == []
    for command in ("", "# note", "a && # c", " && "):
        assert ow._first_visible_add(command, "/x") == []


def test_a_blank_segment_does_not_hide_a_peer_checkout(own):
    own.dirty(own.root)
    rows = [own.created_row(CREATE + ";\n"), own.result_row()]
    assert own.run("git checkout HEAD -- .gitignore", rows, cwd=own.root).denied


# --- a project's create tool is one more visible add (H9) ------------------------------------------------------
# The commands, notices and result lines are the live rows `conftest` keeps; a case fills in only names and paths.
SINGLE = 'cd "<root>" && python3 .claude/tools/example_lane_tool.py create <name> --from main'


def _create_rows(own, command, output=None, background=False, notify=True):
    """The transcript rows of a call running `command` (live shapes), whose tool result or background
    notification carries `output`: a foreground call ends at its result, a background one at its notification."""
    command = fill(command, root=own.root.as_posix(), name="mine", first="wl-leaks")
    output = fill(LIVE_LANE_CREATED, path=own.wt, name="mine") if output is None else output
    cwd = str(own.root)
    rows = [{"type": "assistant", "timestamp": own.started, "cwd": cwd, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_create", "name": "Bash", "input": {"command": command}}]}}]
    shown = output
    if background:
        done = own.tmp / "tasks" / "blw86ytnu.output"
        write(done.parent, done.name, output + "\n\n[exited with code 0]\n")
        shown = fill(LIVE_BACKGROUND_NOTICE, task="blw86ytnu", output=done, cwd=cwd)
    rows.append({"type": "user", "timestamp": own.started if background else own.finished, "cwd": cwd,
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_create",
                                                          "is_error": False, "content": shown}]},
                 "toolUseResult": {"stdout": shown, "stderr": "", "interrupted": False}})
    if background and notify:
        rows.append({"type": "user", "timestamp": own.finished, "cwd": cwd,
                     "origin": {"kind": "task-notification", "producer": "session-task"},
                     "message": {"role": "user", "content": fill(LIVE_TASK_NOTIFICATION, task="blw86ytnu",
                                                                  call="toolu_create", output=done)}})
    return rows


ROOT = Path("/repo/.claude/worktrees")


def _names(command):
    """The worktrees `_first_visible_add` names for `command` run in /repo, under the planted project rows."""
    import _own_worktree as ow
    command = fill(command, root="/repo", name="mine", first="wl-leaks")
    return [p.name for p in ow._first_visible_add(command, "/repo", ROOT)]


@pytest.mark.parametrize("command,want", [
    pytest.param(SINGLE, ["mine"], id="single"),
    pytest.param("false; " + SINGLE, ["mine"], id="after-false: a `;` never skips"),
    pytest.param(LIVE_LANE_CHAIN, ["wl-leaks", "mine"], id="chain: the second create is named too"),
    pytest.param(LIVE_LANE_HEREDOC, ["mine"], id="heredoc after the create"),
    pytest.param("false && " + SINGLE, [], id="skipped by &&"),
    pytest.param("exit 1; " + SINGLE, [], id="exit before"),
    pytest.param(SINGLE + " || true", [], id="masked by ||"),
    pytest.param(SINGLE.replace("--from main", "--from $BASE"), [], id="expansion"),
    pytest.param(SINGLE + " --import 2>&1 | tail -6 | cut -c1-250", ["mine"],
                 id="tail then a cut from column 1: every line start, and so any fatal:, stays visible"),
    pytest.param(SINGLE + " 2>&1 | cut -c5-80", [], id="a cut past column 1 can hide fatal:"),
    pytest.param(SINGLE + " 2>&1 | cut -c1-3", [], id="a cut shorter than fatal: cuts the marker"),
    pytest.param(SINGLE + " 2>&1 | cut -c-5", [], id="an open-start cut shorter than error: cuts the marker"),
    pytest.param(SINGLE + " 2>&1 | tail -6 | grep created", [], id="a filter that drops lines can hide fatal:"),
])
def test_a_listed_create_is_named_by_the_one_visible_add_analysis(create_tools, command, want):
    assert _names(command) == want


def test_a_tool_the_project_does_not_list_is_named_by_nothing(create_tools):
    create_tools("another_tool.py")
    assert _names(SINGLE) == []
    create_tools()
    assert _names(SINGLE) == []


@pytest.mark.parametrize("command,background", [(LIVE_LANE_CHAIN, True), (LIVE_LANE_HEREDOC, False)],
                         ids=["chain-backgrounded", "heredoc-foreground"])
def test_a_registered_create_proves_on_the_live_result_shapes(own, create_tools, command, background):
    assert not own.blocked(_create_rows(own, command, background=background))


@pytest.mark.parametrize("command,kwargs", [
    pytest.param(LIVE_LANE_CHAIN, {"output": "error: lane wl-leaks exists\n" + fill(LIVE_LANE_CREATED, path="x", name="mine")},
                 id="first-create-failed"),
    pytest.param(SINGLE.replace("<name>", "other"), {}, id="another-name"),
    pytest.param(SINGLE, {"background": True, "notify": False}, id="no-completion-notice"),
])
def test_a_create_that_failed_or_never_completed_proves_nothing(own, create_tools, command, kwargs):
    assert own.blocked(_create_rows(own, command, **kwargs))


# --- the delete preview reads ownership from the session, and a deny names it when it is unmet --------------------
@pytest.fixture
def preview(own, tmp_path, monkeypatch, capsys):
    """`_delete_rule.main([<own worktree>, ...])` as the session runs it: its id in the environment and its
    transcript where the client keeps it (`CLAUDE_CONFIG_DIR`)."""
    import _delete_rule as rule
    sid = "6f3a2c1e-0b7d-4e55-9a21-3c8d5e7f9a10"
    config = tmp_path / "login"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.chdir(own.root)

    def run(rows, *flags):
        write(config, "projects/p/%s.jsonl" % sid, "".join(json.dumps(r) + "\n" for r in rows))
        code = rule.main([*flags, str(own.wt)])
        return code, capsys.readouterr().out
    run.rule = rule
    return run


def test_the_preview_without_a_flag_proves_ownership_from_the_transcript(own, preview):
    code, out = preview(own.rows())
    assert code == 0 and "retired" in out


def test_the_preview_of_an_unproven_worktree_blocks_and_names_ownership(own, preview):
    code, out = preview([])
    assert code == 1 and preview.rule.OWNERSHIP_UNPROVEN in out


def test_the_created_flag_is_a_labelled_hypothetical(own, preview):
    code, out = preview([], "--created")
    assert code == 0 and "retired" in out and "--created" in out


def _take_retire_line(own, tmp_path):
    """`dispatch_result.py take`'s RETIRE line for the own worktree, from an ended job record planted in `tmp_path`."""
    import sys
    tools = str(Path(__file__).resolve().parents[2] / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import dispatch_take
    record = write(tmp_path, "out/job.record.json", '{"label": "job"}')
    for suffix, text in ((".out", ""), (".exit", "0")):
        Path(str(record) + suffix).write_text(text, encoding="utf-8")
    lines, _ = dispatch_take.take(str(record), "job", str(own.root), workdir=str(own.wt))
    return next(line for line in lines if line.startswith("RETIRE"))


def test_take_reads_ownership_from_the_session_as_the_preview_does(own, preview, tmp_path):
    preview(own.rows())  # plants this session's transcript proving the create
    assert "retired: safe to delete" in _take_retire_line(own, tmp_path)
    preview([])
    assert "blocked" in _take_retire_line(own, tmp_path)


def test_a_deny_names_ownership_only_when_ownership_would_have_cleared_the_loss(own):
    import _delete_rule as rule
    delete = "rm -rf .claude/worktrees/mine"
    assert rule.OWNERSHIP_UNPROVEN in own.run(delete, rows=[]).reason
    own.dirty(own.wt)  # a tracked edit no object holds is a loss for the creator too
    got = own.run(delete, rows=[])
    assert got.denied and rule.OWNERSHIP_UNPROVEN not in got.reason


# --- a deny's retry passes: an edit saved to a preserve ref discards from any directory of a peer's worktree --------
@pytest.fixture
def nested(own):
    """A peer's worktree whose tracked `sub/f.txt` carries an edit, with `sub` its cwd (the checkout's top is above
    it, as the harness's own `template/` sits below its repository)."""
    sub = own.wt / "sub"
    write(own.wt, "sub/f.txt", "tracked\n")
    g(own.wt, "add", "sub/f.txt")
    g(own.wt, "commit", "-qm", "track f")
    write(own.wt, "sub/f.txt", "edited\n")
    return sub


def _save(own, sub):
    g(sub, "update-ref", "refs/preserve/saved", g(sub, "hash-object", "-w", "f.txt").strip())


def test_the_stored_check_reads_a_path_relative_to_a_subdirectory_cwd(own, nested):
    import _delete_rule
    assert _delete_rule.worktree_unstored(str(nested), ["f.txt"]) == ["f.txt"]
    _save(own, nested)
    assert _delete_rule.worktree_unstored(str(nested), ["f.txt"]) == []


@pytest.mark.parametrize("command", ['git -C "{sub}" restore f.txt', 'cd "{sub}" && git checkout -- f.txt'],
                         ids=["dash-C", "cd"])
def test_a_saved_edit_discards_from_a_subdirectory_of_a_peers_worktree(own, nested, command):
    _save(own, nested)
    assert not own.run(command.format(sub=nested.as_posix()), rows=[]).denied


def test_an_unsaved_edit_stays_denied_from_a_subdirectory(own, nested):
    assert own.run('cd "%s" && git checkout -- f.txt' % nested.as_posix(), rows=[]).denied
