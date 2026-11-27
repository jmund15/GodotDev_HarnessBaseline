"""The snapshot owner: one typed answer per path, and the three absences never collapse into each other."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
import review_snapshot as rs  # noqa: E402
from _bounded_run import ProcessTree  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root):
    """A repository with one committed file, one committed deletion and one untracked file."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "a@b")
    _git(root, "config", "user.name", "a")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "kept.txt", "kept\n")
    _write(root, "gone.txt", "gone\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one")
    _git(root, "rm", "-q", "gone.txt")
    _git(root, "commit", "-qm", "two")


class SnapshotSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs_")
        self.root = Path(self.tmp) / "repo"
        build(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_commit_index_and_disk_agree_on_a_present_file(self):
        commit = rs.open(self.root, rs.Commit("HEAD"))
        disk = rs.open(self.root, rs.Disk())
        self.assertIsInstance(commit.hash("kept.txt"), rs.Blob)
        self.assertEqual(commit.hash("kept.txt").sha, disk.hash("kept.txt").sha)
        _write(self.root, "kept.txt", "kept\nchanged\n")
        _git(self.root, "add", "kept.txt")
        staged = rs.open(self.root, rs.Index()).hash("kept.txt")
        self.assertIsInstance(staged, rs.Blob)
        self.assertNotEqual(staged.sha, commit.hash("kept.txt").sha)
        self.assertEqual(staged.sha, rs.open(self.root, rs.Disk()).hash("kept.txt").sha)
        self.assertEqual(commit.hash("kept.txt").sha,
                         _git(self.root, "rev-parse", "HEAD:kept.txt").stdout.strip())

    def test_a_command_asks_each_repository_fact_once_and_the_next_command_asks_again(self):
        asked, real, real_commit = [], rs._call, rs._commit_of

        def spy(args, root, input=None):
            asked.append(tuple(args[:2]))
            return real(args, root, input=input)

        def commit_spy(root, rev):
            asked.append(("commit of", rev))
            return real_commit(root, rev)

        def reads():
            for snap in [rs.open(self.root, rs.Disk()) for _ in range(3)]:
                snap.hash("kept.txt")
                snap.head
        with patch.multiple(rs, _call=spy, _commit_of=commit_spy):
            with rs.repo_facts():
                reads()
            within, asked[:] = list(asked), []
            reads()
            outside, asked[:] = list(asked), []
            _git(self.root, "commit", "-q", "--allow-empty", "-m", "three")
            with rs.repo_facts():
                head = rs.open(self.root, rs.Disk()).head
        self.assertEqual(within.count(("rev-parse", "--show-toplevel")), 0, "the root is the repo as opened")
        self.assertEqual(within.count(("rev-parse", "--git-dir")), 0, "a healthy checkout costs no git-dir probe")
        self.assertEqual(within.count(("commit of", "HEAD")), 1)
        self.assertEqual(outside.count(("commit of", "HEAD")), 3)
        self.assertEqual(head, _git(self.root, "rev-parse", "HEAD").stdout.strip())

    def test_the_submodule_list_is_asked_once_per_command_for_every_path_split_against_it(self):
        asked, real = [], rs._call

        def spy(args, root, input=None):
            asked.append(tuple(args[:2]))
            return real(args, root, input=input)

        with patch.object(rs, "_call", spy):
            with rs.repo_facts():
                for path in ("kept.txt", "a/b.txt", "c.txt"):
                    rs.split_submodule(self.root, path)
            within, asked[:] = asked.count(("ls-files", "--stage")), []
            for path in ("kept.txt", "a/b.txt", "c.txt"):
                rs.split_submodule(self.root, path)
        self.assertEqual((within, asked.count(("ls-files", "--stage"))), (1, 3))

    def test_a_command_hashes_each_file_once_across_the_snapshots_it_opens_and_the_next_command_hashes_again(self):
        asked, real = [], rs._call

        def spy(args, root, input=None):
            asked.append(args[0])
            return real(args, root, input=input)

        def two_snapshots():
            return [rs.open(self.root, rs.Disk()).hash("kept.txt").sha for _ in range(2)]

        with patch.object(rs, "_call", spy):
            with rs.repo_facts():
                first = two_snapshots()
            within, asked[:] = asked.count("hash-object"), []
            _write(self.root, "kept.txt", "kept\nedited\n")
            with rs.repo_facts():
                second = two_snapshots()
        self.assertEqual((within, asked.count("hash-object")), (1, 1))
        self.assertEqual(first[0], first[1])
        self.assertEqual(second[0], second[1])
        self.assertNotEqual(first[0], second[0], "a later command reads the edited bytes")

    def _spy_spawns(self):
        """(one-shot argv heads, object reader count) the block below spawns, through the module's two git seams."""
        asked, readers = [], []
        real_call, real_reader = rs._call, rs._batch_process

        def call(args, root, input=None):
            asked.append(tuple(args[:2]))
            return real_call(args, root, input=input)

        def reader(root):
            proc = real_reader(root)
            readers.append(proc)
            return proc
        return asked, readers, patch.multiple(rs, _call=call, _batch_process=reader)

    def test_a_command_reads_its_objects_through_one_process_that_sees_what_moves_while_it_runs(self):
        kept = _git(self.root, "rev-parse", "HEAD:kept.txt").stdout.strip()
        data = (self.root / "kept.txt").read_bytes()
        tree = _git(self.root, "rev-parse", "HEAD^{tree}").stdout.strip()
        _git(self.root, "branch", "side", "HEAD~1")
        asked, readers, spy = self._spy_spawns()
        with spy:
            with rs.repo_facts():
                head = rs.open(self.root, rs.Disk()).head
                self.assertTrue(rs.commit_present(self.root, head))
                self.assertEqual(rs.object_kinds(self.root, [kept, tree, "f" * 40]),
                                 {kept: "blob", tree: "tree", "f" * 40: "missing"})
                self.assertEqual(rs.cat_blob(self.root, kept), data)
                self.assertIsNone(rs.cat_blob(self.root, tree), "a tree is not a blob")
                snap = rs.open(self.root, rs.Commit("HEAD"))
                self.assertEqual(snap.commit_for("side"), _git(self.root, "rev-parse", "HEAD~1").stdout.strip())
                # A concurrent writer: an object stored and a ref moved or deleted after the process started.
                fresh = subprocess.run(["git", "-C", str(self.root), "hash-object", "-w", "--stdin"], input=b"new\n",
                                       capture_output=True).stdout.decode().strip()
                self.assertEqual(rs.cat_blob(self.root, fresh), b"new\n")
                _git(self.root, "branch", "-f", "side", "HEAD")
                self.assertEqual(snap.commit_for("side"), head)
                _git(self.root, "branch", "-D", "side")
                self.assertIsNone(snap.commit_for("side"))
                self.assertIsNone(snap.commit_for("no-such-ref"))
                within = list(asked)
            self.assertEqual(len(readers), 1, "one object reader serves the whole command")
            self.assertIsNotNone(readers[0].poll(), "the reader ends with its command")
            self.assertFalse([a for a in within if a[0] in ("cat-file", "rev-parse")], within)
            self.assertEqual(rs.cat_blob(self.root, kept), data)
            self.assertTrue(rs.commit_present(self.root, head))
        self.assertEqual(len(readers), 3, "outside a command each question opens and closes its own")
        self.assertTrue(all(proc.poll() is not None for proc in readers))

    def test_a_reader_that_died_is_replaced_and_a_missing_git_reads_as_unreadable(self):
        kept = _git(self.root, "rev-parse", "HEAD:kept.txt").stdout.strip()
        asked, readers, spy = self._spy_spawns()
        with spy:
            with rs.repo_facts():
                self.assertTrue(rs.commit_present(self.root, "HEAD"))
                ProcessTree(readers[0]).end()  # the tree: under the runner's PATH `git` is a launcher
                self.assertEqual(rs.cat_blob(self.root, kept), (self.root / "kept.txt").read_bytes())
        self.assertEqual(len(readers), 2)
        hung = []

        def silent(root):  # a stand-in for a git that never answers: the bound, not git, is what this decides
            proc = subprocess.Popen([sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(60)"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            hung.append(proc)
            return proc
        with patch.multiple(rs, _batch_process=silent, _remaining=lambda default: 0.5):
            with rs.repo_facts():
                self.assertIsNone(rs.cat_blob(self.root, kept))
        self.assertEqual(len(hung), 1, "a question that outlived its bound is not asked again")
        self.assertIsNotNone(hung[0].poll())
        with patch.object(rs, "_batch_process", side_effect=OSError("no git")):
            with rs.repo_facts():
                self.assertIsNone(rs.open(self.root, rs.Disk()).head)
                self.assertFalse(rs.commit_present(self.root, "HEAD"))
                self.assertEqual(rs.object_kinds(self.root, [kept]), {kept: "unreadable"})
                self.assertIsNone(rs.cat_blob(self.root, kept))

    def test_a_base_the_head_sits_on_and_a_range_from_head_ask_no_history_walk(self):
        head = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        parent = _git(self.root, "rev-parse", "HEAD~1").stdout.strip()
        asked, _readers, spy = self._spy_spawns()
        with spy:
            with rs.repo_facts():
                self.assertEqual(rs.merge_base(self.root, "HEAD", head), head)
                snap = rs.open(self.root, rs.Disk())
                self.assertFalse(snap.merge_touched(head, "kept.txt"))
                quiet = list(asked)
                self.assertEqual(rs.merge_base(self.root, parent, head), parent)
                self.assertIsNone(rs.merge_base(self.root, "no-such-ref", head))
                self.assertFalse(snap.merge_touched(parent, "kept.txt"))
        self.assertFalse([a for a in quiet if a[0] in ("merge-base", "rev-list")], quiet)
        self.assertEqual((asked.count(("merge-base", parent)), asked.count(("merge-base", "no-such-ref"))), (1, 0),
                         "a base that names no commit has no merge base")
        self.assertEqual(asked.count(("rev-list", "--merges")), 1)

    def test_every_reader_of_the_main_checkout_shares_one_question_per_command(self):
        sys.path.insert(0, str(ROOT / ".claude/hooks"))
        import post_check
        import review_ledger
        import task_record
        import verify_claims
        _write(self.root, "tpl/x.txt", "x\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "nested root")
        _git(self.root, "worktree", "add", "-q", str(Path(self.tmp) / "lane"), "-b", "lane")
        main = self.root.resolve()
        lane_root = Path(self.tmp).resolve() / "lane" / "tpl"
        asked, real = [], subprocess.run

        def run(argv, *args, **kwargs):
            asked.extend(word for word in argv if word == "rev-parse")
            return real(argv, *args, **kwargs)
        task_record._store_root.cache_clear()
        verify_claims._main_root.cache_clear()
        names = ("HARNESS_REVIEW_LEDGER", "HARNESS_TASK_RECORD_DIR")
        with patch.dict("os.environ", {}), patch.object(subprocess, "run", run):
            for name in names:
                os.environ.pop(name, None)
            with rs.repo_facts():
                answers = (review_ledger.ledger_path(lane_root), task_record.record_dir(str(lane_root)),
                           post_check._common_dir(lane_root), verify_claims._main_root(str(lane_root)))
        self.assertEqual(asked, ["rev-parse"], asked)
        self.assertEqual(answers[0], main / "tpl" / ".claude" / "logs" / "review_ledger.jsonl")
        self.assertEqual(Path(answers[1]), main / ".claude" / "logs" / "tasks")
        self.assertEqual(answers[2], (main / ".git").resolve())
        self.assertEqual(answers[3], main)
        task_record._store_root.cache_clear()
        verify_claims._main_root.cache_clear()

    def test_a_command_reads_present_blobs_at_a_revision_without_a_listing_and_keeps_each_absence_distinct(self):
        _write(self.root, "sub/f.txt", "f\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "sub")
        kept = _git(self.root, "rev-parse", "HEAD:kept.txt").stdout.strip()
        nested = _git(self.root, "rev-parse", "HEAD:sub/f.txt").stdout.strip()
        asked, _readers, spy = self._spy_spawns()
        with spy:
            with rs.repo_facts():
                present = rs.open(self.root, rs.Commit("HEAD")).blobs(["kept.txt", "sub/f.txt"])
                listed = list(asked)
                absent = rs.open(self.root, rs.Commit("HEAD")).blobs(["never.txt", "gone.txt", "sub", "sub//f.txt"])
        self.assertEqual(present, {"kept.txt": rs.Blob(kept), "sub/f.txt": rs.Blob(nested)})
        self.assertNotIn("ls-tree", [a[0] for a in listed], "a present blob needs no listing")
        self.assertEqual(absent["never.txt"], rs.MISSING)
        self.assertIsInstance(absent["gone.txt"], rs.Deleted)
        self.assertEqual(absent["sub"], rs.MISSING, "a directory is not a file")
        self.assertEqual(absent["sub//f.txt"], rs.MISSING, "a path is read as written, never normalized to another")
        gone_object = self.root / ".git/objects" / kept[:2] / kept[2:]
        gone_object.chmod(0o700)
        gone_object.unlink()
        with rs.repo_facts():
            self.assertIsInstance(rs.open(self.root, rs.Commit("HEAD")).hash("kept.txt"), rs.Unreadable,
                                  "an entry whose object is gone is unreadable, never absent")

    def test_a_commit_that_deleted_the_file_is_its_tombstone_without_a_walk_and_every_other_deletion_walks(self):
        head = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        main_branch = _git(self.root, "symbolic-ref", "--short", "HEAD").stdout.strip()
        _git(self.root, "checkout", "-q", "-b", "side")
        _git(self.root, "rm", "-q", "kept.txt")
        _git(self.root, "commit", "-qm", "side deletes")
        side = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        _git(self.root, "checkout", "-q", main_branch)
        _write(self.root, "other.txt", "o\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "main moves")
        _git(self.root, "merge", "-q", "--no-ff", "-m", "merge", "side")
        merge = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        asked, _readers, spy = self._spy_spawns()
        with spy:
            with rs.repo_facts():
                here = rs.open(self.root, rs.Commit(head)).deleted("gone.txt")
                walked_here = asked.count(("log", "-1"))
                through_merge = rs.open(self.root, rs.Commit(merge)).deleted("kept.txt")
            _write(self.root, "staged.txt", "s\n")
            _git(self.root, "add", "staged.txt")
            _git(self.root, "commit", "-qm", "staged")
            _git(self.root, "rm", "-q", "--cached", "staged.txt")
            with rs.repo_facts():
                staged = rs.open(self.root, rs.Index()).deleted("staged.txt")
        self.assertEqual((here, walked_here), (rs.Deleted("deleted:" + head), 0))
        self.assertEqual(through_merge, rs.Deleted("deleted:" + side), "a merge's deletion is the side's, by the walk")
        self.assertEqual(staged, rs.MISSING, "an index read's absence says nothing of HEAD's history")

    def test_a_revision_or_index_read_of_more_paths_than_one_command_line_holds_answers_each(self):
        # Windows caps a command line at 32,767 characters; a range's files can name more than that. Each absent path
        # asks its own deletion history, so the case names few paths, each long.
        absent = ["absent/%s/%02d.txt" % ("d" * 3800, n) for n in range(9)]
        for source in (rs.Commit("HEAD"), rs.Index()):
            got = rs.open(self.root, source).blobs(["kept.txt"] + absent)
            self.assertIsInstance(got["kept.txt"], rs.Blob, source)
            self.assertEqual({type(got[path]) for path in absent}, {rs.Missing}, source)

    def test_a_disk_read_outside_git_hashes_its_bytes_and_a_refusing_git_leaves_the_file_unreadable(self):
        bare = Path(self.tmp) / "plain"
        _write(bare, "f.txt", "bytes\n")
        got = rs.open(bare, rs.Disk()).hash("f.txt")
        self.assertEqual(got, rs.Blob("sha256:" + hashlib.sha256((bare / "f.txt").read_bytes()).hexdigest()))
        with patch.object(rs, "hash_objects", side_effect=OSError("object store is read-only")):
            refused = rs.open(self.root, rs.Disk()).hash("kept.txt")
        self.assertIsInstance(refused, rs.Unreadable)
        self.assertIn("read-only", refused.reason)

    def test_a_recorded_row_reads_its_own_map(self):
        sha = rs.open(self.root, rs.Commit("HEAD")).hash("kept.txt").sha
        snap = rs.open(self.root, rs.Recorded({"files": {"kept.txt": sha, "gone.txt": "deleted:" + "f" * 40}}))
        self.assertEqual(snap.hash("kept.txt"), rs.Blob(sha))
        self.assertEqual(snap.hash("gone.txt"), rs.Deleted("deleted:" + "f" * 40))
        self.assertEqual(snap.hash("other.txt"), rs.MISSING)

    def test_missing_and_a_committed_deletion_stay_distinct(self):
        snap = rs.open(self.root, rs.Commit("HEAD"))
        self.assertEqual(snap.hash("never.txt"), rs.MISSING)
        gone = snap.hash("gone.txt")
        self.assertIsInstance(gone, rs.Deleted)
        self.assertTrue(gone.tombstone.startswith("deleted:"))
        self.assertEqual(rs.open(self.root, rs.Disk()).hash("gone.txt"), rs.MISSING)

    def test_unreadable_history_is_not_a_deletion(self):
        sha = rs.open(self.root, rs.Commit("HEAD")).hash("kept.txt").sha
        object_path = self.root / ".git/objects" / sha[:2] / sha[2:]
        object_path.chmod(0o700)
        object_path.unlink()
        snap = rs.open(self.root, rs.Commit("HEAD"))
        self.assertIsInstance(snap.hash("kept.txt"), rs.Unreadable,
                              "a removed object is not absent: it is unreadable")
        self.assertIsInstance(snap.deleted("kept.txt"), rs.Unreadable,
                              "unreadable history must never resemble a deletion")
        self.assertEqual(snap.hash("never.txt"), rs.MISSING)

    def test_linked_worktree_path_reads_its_own_checkout(self):
        _git(self.root, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt")
        wt = self.root / ".claude/worktrees/wt"
        _write(wt, "branch.txt", "branch\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "branch")
        sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
        snap = rs.open(self.root, rs.Commit(sha))
        self.assertIsInstance(snap.hash(".claude/worktrees/wt/branch.txt"), rs.Blob)

    def test_a_foreign_checkout_at_the_worktree_alias_is_not_borrowed(self):
        foreign = self.root / ".claude/worktrees/other"
        foreign.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(foreign)], check=True)
        _write(foreign, "x.txt", "foreign\n")
        _git(foreign, "add", "-A")
        _git(foreign, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "f")
        result = rs.open(self.root, rs.Commit("HEAD")).hash(".claude/worktrees/other/x.txt")
        self.assertNotIsInstance(result, rs.Blob)

    def test_nested_submodule_pin_shift_lists_the_files_between_pins(self):
        inner = Path(self.tmp) / "inner"
        subprocess.run(["git", "init", "-q", str(inner)], check=True)
        _git(inner, "config", "user.email", "a@b")
        _git(inner, "config", "user.name", "a")
        _write(inner, "g.cs", "one\n")
        _git(inner, "add", "-A")
        _git(inner, "commit", "-qm", "one")
        old = _git(inner, "rev-parse", "HEAD").stdout.strip()
        subprocess.run(["git", "-C", str(self.root), "-c", "protocol.file.allow=always",
                        "submodule", "add", "-q", str(inner), "lib/core"], check=True)
        _git(self.root, "commit", "-qm", "add sub")
        _write(inner, "g.cs", "two\n")
        _git(inner, "add", "-A")
        _git(inner, "commit", "-qm", "two")
        new = _git(inner, "rev-parse", "HEAD").stdout.strip()
        _git(self.root / "lib/core", "fetch", "-q")
        _git(self.root / "lib/core", "checkout", "-q", new)
        _git(self.root, "add", "lib/core")
        _git(self.root, "commit", "-qm", "bump")
        self.assertEqual(rs.gitlink_files(self.root, "lib/core", old, new), {"lib/core/g.cs"})
        self.assertIsInstance(rs.open(self.root, rs.Commit("HEAD")).hash("lib/core/g.cs"), rs.Blob)

    def test_range_commits_names_files_and_a_clean_merge_resolves_none(self):
        main_branch = _git(self.root, "symbolic-ref", "--short", "HEAD").stdout.strip()
        _git(self.root, "checkout", "-q", "-b", "side")
        _write(self.root, "side.txt", "side\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "side")
        _git(self.root, "checkout", "-q", main_branch)
        _write(self.root, "main.txt", "main\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "main")
        base = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        _git(self.root, "merge", "-q", "--no-ff", "-m", "merge", "side")
        merge = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        commits = {c["sha"]: c["files"] for c in rs.range_commits(self.root, base, merge)}
        self.assertEqual(commits[merge], [], "a clean merge authors no resolution")
        self.assertEqual(sorted(f for files in commits.values() for f in files), ["side.txt"])
        _write(self.root, "main.txt", "kept\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "plain")
        plain = rs.range_commits(self.root, merge, "HEAD")
        self.assertEqual(plain[0]["files"], ["main.txt"])


if __name__ == "__main__":
    sys.exit(unittest.main())
