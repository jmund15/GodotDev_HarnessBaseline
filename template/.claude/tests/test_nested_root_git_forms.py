"""A harness root one level below the git toplevel (`template/`): every tool that holds a harness-root-relative
path hands git a form that resolves from the cwd (`rev:./path`, `--relative`, an absolute path), never one that
resolves from the toplevel (`rev:path`, `--stdin-paths`, `--full-tree`, a bare `--name-only`). One real repository
is built once per class; each case calls the owner function on a tracked `.claude/` file and asserts the
root-relative answer."""
import os
import re
import subprocess
import sys
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / "hooks"))
import _checkout_root
import citers
import close_predicates
import drift_scan
import escape_log
import mechanism_census
import restate_scan
import review_ledger
import review_snapshot
import session_audit_inputs as inputs
import task_record
import triage
sys.path.insert(0, str(TOOLS.parent / "scripts"))
import main_check
import rule_retirement

RULE = ".claude/rules/a.md"
SPACED = ".claude/rules/\u00e9.md"  # not printable ASCII: committed_objects reads it through `rev-parse`
OUTSIDE = "outside.txt"


def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
                          cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class NestedRoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.top = Path(cls.tmp.name) / "repo"
        cls.root = cls.top / "template"
        cls.top.mkdir()
        git(cls.top, "init", "-q", "-b", "main")
        for rel, text in ((OUTSIDE, "outside\n"), ("template/" + RULE, "one\n"), ("template/" + SPACED, "one\n")):
            path = cls.top / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())
        git(cls.top, "add", "-A")
        git(cls.top, "commit", "-q", "-m", "base")
        cls.base = git(cls.top, "rev-parse", "HEAD")
        (cls.root / RULE).write_bytes(b"two\n")
        (cls.top / OUTSIDE).write_bytes(b"changed\n")
        git(cls.top, "commit", "-q", "-a", "-m", "edit")
        cls.head = git(cls.top, "rev-parse", "HEAD")
        cls.blob = git(cls.top, "rev-parse", "HEAD:template/" + RULE)
        git(cls.top, "switch", "-q", "-c", "side", cls.base)
        (cls.root / RULE).write_bytes(b"side\n")
        git(cls.top, "commit", "-q", "-a", "-m", "side")
        git(cls.top, "switch", "-q", "main")
        subprocess.run(["git", "merge", "-q", "side"], cwd=cls.top, capture_output=True)
        (cls.root / RULE).write_bytes(b"both\n")
        git(cls.top, "add", "-A")
        git(cls.top, "commit", "-q", "-m", "merged")
        cls.merge = git(cls.top, "rev-parse", "HEAD")
        git(cls.top, "switch", "-q", "--detach", cls.head)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_hash_objects_reads_a_worktree_file_by_its_root_relative_path(self):
        self.assertEqual(review_snapshot.hash_objects(self.root, [RULE]), {RULE: self.blob})

    def test_range_commits_name_root_relative_files_and_skip_those_outside_the_root(self):
        commits = review_snapshot.range_commits(self.root, self.base, self.head)
        self.assertEqual([c["files"] for c in commits], [[RULE]])

    def test_a_merge_resolution_is_named_root_relative_too(self):
        # `--relative` leaves a merge's combined diff on toplevel names; the helper maps those back.
        commits = review_snapshot.range_commits(self.root, self.head, self.merge)
        self.assertEqual(commits[-1]["files"], [RULE])
        self.assertEqual(close_predicates._git_files(self.root, [], [f"{self.head}..{self.merge}"]), {RULE})

    def test_a_commit_snapshot_answers_a_root_relative_path(self):
        found = review_snapshot.open(self.root, review_snapshot.Commit(self.head)).blobs([RULE, "missing.md"])
        self.assertEqual(found[RULE], review_snapshot.Blob(self.blob))
        self.assertEqual(found["missing.md"], review_snapshot.MISSING)

    def test_committed_objects_read_plain_and_unplain_paths(self):
        found = inputs.committed_objects(self.root, self.head, [RULE, SPACED])
        self.assertEqual(found[RULE], (self.blob, "two\n"))
        self.assertEqual(found[SPACED][1], "one\n")

    def test_per_path_diffs_attribute_each_block_to_its_root_relative_subject(self):
        texts = inputs.per_path_diffs(self.root, ["--no-renames", f"{self.base}..{self.head}"], [RULE])
        self.assertIn("+two", texts[RULE])

    def test_citers_read_a_path_at_a_revision(self):
        self.assertEqual(citers.head_text(self.root, RULE), "two\n")
        self.assertEqual(citers.text_at(self.root, self.base, RULE), "one\n")

    def test_restate_scan_reads_revision_texts(self):
        self.assertEqual(restate_scan.revision_texts(self.root, [RULE], self.base), {RULE: "one\n"})

    def test_mechanism_census_finds_a_root_relative_path_at_a_revision(self):
        self.assertTrue(mechanism_census._exists_at(self.root, self.head, RULE))

    def test_a_landed_commit_lists_root_relative_changes(self):
        self.assertEqual([change.path for change in triage.changes_from_commit(str(self.root), self.head)], [RULE])

    def test_a_linked_worktree_shares_the_main_checkouts_ledger_in_its_harness_root(self):
        os.environ.pop("HARNESS_REVIEW_LEDGER", None)
        linked = Path(self.tmp.name) / "linked"
        git(self.top, "worktree", "add", "-q", "--detach", str(linked), self.head)
        expected = (self.root / ".claude/logs/review_ledger.jsonl").resolve()
        self.assertEqual(review_ledger.ledger_path(self.root).resolve(), expected)
        self.assertEqual(review_ledger.ledger_path(linked / "template").resolve(), expected)


ADAPTATION = ".claude/skills/project_subsystems/adaptation.json"
LEDGER = ".claude/logs/review_ledger.jsonl"


class DefaultRoot(unittest.TestCase):
    """A run from a subdirectory of the harness root (the interactive shell's cwd drifts) with no `--repo` takes the
    harness root the run belongs to: the nearest ancestor holding `.claude/`, in a nested root and in a repository
    whose root is its toplevel alike."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.layouts = {}
        for name, rel in (("nested", "template"), ("flat", "")):
            top = Path(cls.tmp.name) / name
            root = top / rel if rel else top
            top.mkdir()
            git(top, "init", "-q", "-b", "main")
            files = {RULE: "one\n",
                     ADAPTATION: json.dumps({"drift_scan": {"sources": ["src.txt"], "consumers": [RULE]}}),
                     LEDGER: json.dumps({"subject": "s", "reviewer": "panel"}) + "\n"}
            for path, text in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(text.encode())
            git(top, "add", "-A")
            git(top, "commit", "-q", "-m", "base")
            sub = root / ".claude/scratch/x"
            sub.mkdir(parents=True)
            cls.layouts[name] = (root, sub)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @contextlib.contextmanager
    def from_subdir(self, sub):
        before, env = os.getcwd(), os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.chdir(sub)
        try:
            yield
        finally:
            os.chdir(before)
            if env is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = env

    def in_each_layout(self, check):
        """`check(root)` run from the layout's `.claude/scratch/x` with no `CLAUDE_PROJECT_DIR`."""
        for name, (root, sub) in self.layouts.items():
            with self.subTest(layout=name), self.from_subdir(sub):
                check(root.resolve())

    def test_the_helper_finds_the_nearest_ancestor_holding_claude(self):
        for name, (root, sub) in self.layouts.items():
            with self.subTest(layout=name):
                self.assertEqual(Path(_checkout_root.harness_root(sub)), root.resolve())
                self.assertEqual(Path(_checkout_root.harness_root(root)), root.resolve())

    def test_the_helper_stops_at_the_repository_boundary(self):
        outer = Path(self.tmp.name) / "outer"
        (outer / ".claude").mkdir(parents=True)
        (outer / "proj/.git").mkdir(parents=True)
        (outer / "proj/sub").mkdir()
        self.assertEqual(Path(_checkout_root.harness_root(outer / "proj/sub")), (outer / "proj/sub").resolve())

    def test_drift_scan_reads_the_root_adaptation(self):
        empty = Path(self.tmp.name) / "empty.diff"
        empty.write_text("", encoding="utf-8")

        def check(root):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(drift_scan.main(["--diff-file", str(empty)]), 0)
            self.assertIn("1 consumer file(s) scanned", out.getvalue())
        self.in_each_layout(check)

    def test_escape_log_resolves_against_the_root(self):
        self.in_each_layout(lambda root: self.assertEqual(Path(escape_log.claude_dir()).resolve(), root / ".claude"))

    def test_the_review_ledger_cli_reads_the_roots_ledger(self):
        os.environ.pop("HARNESS_REVIEW_LEDGER", None)

        def check(root):
            code, out, err = review_ledger.run(["rows", "--subject", "s", "--json"])
            self.assertEqual((code, json.loads(out)[0]["subject"]), (0, "s"), err)
        self.in_each_layout(check)

    def test_main_check_names_the_harness_root_as_its_repo(self):
        self.in_each_layout(lambda root: self.assertEqual(Path(main_check.repo_root(None)).resolve(), root))

    def test_rule_retirement_reads_the_roots_ledger(self):
        os.environ.pop("HARNESS_REVIEW_LEDGER", None)

        def check(root):
            self.assertEqual(rule_retirement._load_ledger({})["rows"][0]["subject"], "s")
        self.in_each_layout(check)

    def test_the_task_record_store_sits_in_the_root(self):
        os.environ.pop("HARNESS_TASK_RECORD_DIR", None)
        self.in_each_layout(lambda root: self.assertEqual(Path(task_record.record_dir()).resolve(),
                                                          root / ".claude/logs/tasks"))


# A tool or script reaches the cwd only for a reason named here, at the number of sites the reason covers, so a new
# default-root read in a listed file still fails. Every other cwd read is a default root, which is
# `CLAUDE_PROJECT_DIR` or `_checkout_root.harness_root()` (a shell's cwd drifts into subdirectories).
CWD_READS = {
    "tools/baseline_sync.py": (1, "project_root is the baseline family's own walk: a pull proof's checkout carries tools/ without hooks/"),
    "tools/index_commit.py": (1, "git index plumbing: its paths are relative to the git toplevel"),
    "tools/dispatch_result.py": (1, "git resolves a worktree's main checkout from any subdirectory"),
    "tools/dispatch_size.py": (1, "the base for relative paths a caller wrote in a dispatch spec"),
    "tools/dispatch_take.py": (1, "the process cwd is the fact a retirement check judges"),
    "tools/machine_slots.py": (1, "records the holder's own cwd as a fact in its lane note"),
    "tools/orchestration_metrics.py": (2, "a diagnostic prints the cwd it searched; the session-store match is a separate use"),
    "tools/sidecar_fanout.py": (5, "the cwd a launched job and its relative inputs resolve against"),
    "scripts/_read_recorder/sitecustomize.py": (2, "records the cwd a child process ran in"),
}
CWD_PATTERN = re.compile(r"os\.getcwd\(\)|\bPath\.cwd\(\)|pathlib\.Path\.cwd\(\)|default=['\"]\.['\"]")


class NoCwdRootDefault(unittest.TestCase):
    def test_no_tool_defaults_its_root_to_the_cwd(self):
        claude = Path(__file__).resolve().parents[1]
        found = {}
        for sub in ("tools", "scripts"):
            for path in sorted((claude / sub).rglob("*.py")):
                rel = path.relative_to(claude).as_posix()
                lines = [str(n) for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                         if CWD_PATTERN.search(line) and not line.lstrip().startswith("#")]
                if len(lines) != CWD_READS.get(rel, (0, ""))[0]:
                    found[rel] = lines
        self.assertEqual(found, {}, "a default root is harness_root(); a real cwd use goes in CWD_READS with its reason")


if __name__ == "__main__":
    unittest.main()
