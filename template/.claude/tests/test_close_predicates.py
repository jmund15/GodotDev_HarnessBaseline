"""Cases for tools/close_predicates.py: one planted true, one planted false and one null input per
predicate; an unknown stem exits 2; a predicate that raises records null and exits 0; a planted
mutant that turns null into false fails the null cases."""
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_load_census as census_cases

TOOLS = Path(__file__).resolve().parents[1] / "tools"
MODULE_PATH = TOOLS / "close_predicates.py"
sys.path.insert(0, str(TOOLS))


def load(path=MODULE_PATH, name="close_predicates_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cp = load()
SID = "abcd1234-0000-0000-0000-000000000000"
REGISTRY = (
    "conventions:\n  reserved_paths: [.claude/, Jmodot/, logs/]\nsubsystems:\n"
    "  - id: spell\n    paths: [Spells/, Synergies/]\n  - id: wizard\n    paths: [Wizard/]\n")


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def digest(repo, **fields):
    data = {"friction": [], "user_messages": [], "files_modified_counts": {}}
    data.update(fields)
    write(Path(repo) / "logs" / f"session_digest_{SID[:8]}.json", json.dumps(data))


def transcript(repo, *inputs):
    lines = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "id": f"t{i}", "input": inp}]}}) for i, inp in enumerate(inputs)]
    path = Path(repo) / "transcript" / f"{SID}.jsonl"
    write(path, "\n".join(lines) + "\n")
    return str(path)


def run(stem, repo, transcript_path=None, session=SID, live=False):
    ctx = cp.Ctx(repo, session, transcript_path, live=live)
    return cp.evaluate([stem], ctx)[stem]


WINDOW = ("2026-01-02T09:00:00.000Z", "2026-01-02T11:00:00.000Z")
INSIDE = "2026-01-02T10:00:00+00:00"
OUTSIDE = "2025-12-01T10:00:00+00:00"


def git(repo, *args, date=INSIDE):
    git_out(repo, *args, date=date)


def git_out(repo, *args, date=INSIDE):
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    result = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                            capture_output=True, text=True, check=True, env=env)
    return result.stdout


def commit(repo, relpath, date=INSIDE):
    """A commit no transcript records, as a peer session's inside the same window is."""
    write(Path(repo) / relpath, relpath)
    git(repo, "add", "--", relpath, date=date)
    git(repo, "commit", "-q", "-m", relpath, date=date)


_KEYS = itertools.count()


TRAILER = "Claude-Session: https://claude.ai/code/session_OWN"
PEER_TRAILER = "Claude-Session: https://claude.ai/code/session_PEER"


def append_record(transcript, record):
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def attach_trailer(transcript, trailer=TRAILER):
    """The attachment a remote session writes, whose `commit` line the attribution reminder renders."""
    url = trailer.split(": ", 1)[1]
    append_record(transcript, {"type": "attachment", "timestamp": WINDOW[0], "attachment": {
        "type": "remote_session_change", "url": url, "commit": trailer, "pr": url}})


def append_shell_result(transcript, text, name="Bash", command="git commit -m x"):
    """The tool_use and tool_result pair a real transcript holds for one shell call."""
    key = f"c{next(_KEYS)}"
    records = [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": key, "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": key, "content": text}]}}]
    with open(transcript, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def bash_commit(repo, transcript, relpath, date=INSIDE):
    """A commit whose summary the session's own shell output prints."""
    write(Path(repo) / relpath, relpath)
    git(repo, "add", "--", relpath, date=date)
    printed = git_out(repo, "commit", "-m", relpath, date=date)
    append_shell_result(transcript, printed)
    return printed


def cut_commit(repo, transcript, relpath, date=INSIDE, trailer=None):
    """A commit the session ran as `git commit ... | tail -1`: the summary line never reaches the transcript."""
    write(Path(repo) / relpath, relpath)
    git(repo, "add", "--", relpath, date=date)
    message = relpath + (f"\n\n{trailer}" if trailer else "")
    printed = git_out(repo, "commit", "-m", message, date=date)
    append_shell_result(transcript, printed.strip().splitlines()[-1] + "\n",
                        command=f'git commit -m "{relpath}" | tail -1')
    return git_out(repo, "rev-parse", "HEAD").strip()


def session_repo(repo):
    """A git repo with one commit before the window and a transcript spanning WINDOW; returns the transcript."""
    git(repo, "init", "-q")
    commit(repo, "README.md", date=OUTSIDE)
    lines = [json.dumps({"type": "user", "timestamp": WINDOW[0], "message": {"content": "go"}}),
             json.dumps({"type": "assistant", "timestamp": WINDOW[1], "message": {"content": []}})]
    path = Path(repo) / "transcript" / f"{SID}.jsonl"
    write(path, "\n".join(lines) + "\n")
    git(repo, "add", "--", "transcript", date=OUTSIDE)
    git(repo, "commit", "-q", "-m", "transcript", date=OUTSIDE)
    return str(path)


class StubCensus:
    def __init__(self, rows=(), errors=(), unmeasured=()):
        self.rows, self.errors, self.unmeasured = list(rows), list(errors), list(unmeasured)

    def build_report(self, root, profile_hooks_flag=False, need_budget_hooks=False):
        return {"source_errors": self.errors, "prompt_hooks_unmeasured": self.unmeasured}

    def over_baseline(self, report):
        return self.rows


class StubMetrics:
    def __init__(self, session_dir="sd", runs=(), sidecar=()):
        self.session_dir, self.runs, self.sidecar = session_dir, runs, sidecar

    def find_session_dir(self, sid):
        return self.session_dir

    def collect_session_sidecar(self, session):
        return list(self.sidecar)


class PredicateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_reindex_true_false_null(self):
        for path in ("guide.md", ".claude/auto-memory/note.md", "room.tscn", "spell.tres",
                     "data.json", "shader.gdshader"):
            ctx = cp.Ctx(self.repo)
            ctx._changed = ([path], None)
            self.assertIs(cp.evaluate(["reindex_search"], ctx)["reindex_search"]["value"], True, path)
        ctx._changed = (["Wizard/Wizard.cs", "tool.py"], None)
        self.assertIs(cp.evaluate(["reindex_search"], ctx)["reindex_search"]["value"], False)
        ctx._changed = (None, "scope unavailable")
        self.assertIsNone(cp.evaluate(["reindex_search"], ctx)["reindex_search"]["value"])

    def test_worklog_sweep_true_false_null(self):
        path = transcript(self.repo, {"file_path": "DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md"})
        ctx = cp.Ctx(self.repo, SID, path)
        ctx._changed = ([], None)
        row = cp.evaluate(["worklog"], ctx)["worklog"]
        self.assertIs(row["value"], True)
        self.assertIs(row["basis"]["completion"], True)
        self.assertIs(row["basis"]["add"], False)
        path = transcript(self.repo, {"file_path": "Wizard/Wizard.cs"})
        append_record(path, {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Deferred the UI cleanup to a follow-up."}]}})
        ctx = cp.Ctx(self.repo, SID, path)
        ctx._changed = ([], None)
        row = cp.evaluate(["worklog"], ctx)["worklog"]
        self.assertIs(row["value"], True)
        self.assertIs(row["basis"]["add"], True)
        self.assertIs(row["basis"]["completion"], False)
        path = transcript(self.repo, {"file_path": "Wizard/Wizard.cs"})
        ctx = cp.Ctx(self.repo, SID, path)
        ctx._changed = ([], None)
        self.assertIs(cp.evaluate(["worklog"], ctx)["worklog"]["value"], False)
        ctx._changed = ([".claude/worklog-titles.md"], None)
        self.assertIs(cp.evaluate(["worklog"], ctx)["worklog"]["basis"]["completion"], True)
        ctx._changed = (None, "scope unavailable")
        self.assertIsNone(cp.evaluate(["worklog"], ctx)["worklog"]["value"])
        self.assertIsNone(run("worklog", self.repo)["value"])

    def test_worklog_sweep_ignores_owner_quotes_and_sidechains(self):
        path = transcript(self.repo, {"file_path": "Wizard/Wizard.cs"})
        for rec in ({"type": "user", "message": {"content": "Please defer this."}},
                    {"type": "assistant", "isSidechain": True, "message": {"content": "Deferred peer work."}}):
            append_record(path, rec)
        ctx = cp.Ctx(self.repo, SID, path)
        ctx._changed = ([], None)
        self.assertIs(cp.evaluate(["worklog"], ctx)["worklog"]["value"], False)
        append_record(path, {"type": "assistant", "message": {"content": "I deferred the cleanup."}})
        self.assertIs(cp.evaluate(["worklog"], ctx)["worklog"]["basis"]["add"], True)
        write(path, "{invalid json\n")
        self.assertIsNone(cp.evaluate(["worklog"], ctx)["worklog"]["value"])

    def test_worklog_recipe_read_is_not_an_item_touch(self):
        path = transcript(self.repo, {"file_path": ".claude/commands/worklog.md"})
        ctx = cp.Ctx(self.repo, SID, path)
        ctx._changed = ([".claude/commands/worklog.md"], None)
        row = cp.evaluate(["worklog"], ctx)["worklog"]
        self.assertIs(row["value"], False)
        self.assertIs(row["basis"]["completion"], False)

    def test_new_predicates_use_real_session_scope(self):
        path = session_repo(self.repo)
        digest(self.repo)
        bash_commit(self.repo, path, "Docs/usage.md")
        self.assertIs(run("reindex_search", self.repo, path)["value"], True)
        self.assertIs(run("worklog", self.repo, path)["value"], False)
        bash_commit(self.repo, path, ".claude/worklog-titles.md")
        self.assertIs(run("worklog", self.repo, path)["basis"]["completion"], True)

    def test_autolearn_true_false_null(self):
        digest(self.repo, friction=[{"id": "F1"}])
        self.assertIs(run("autolearn", self.repo)["value"], True)
        digest(self.repo, user_messages=[{"signals": ["correction"]}])
        self.assertIs(run("autolearn", self.repo)["value"], True)
        digest(self.repo, user_messages=[{"signals": []}])
        row = run("autolearn", self.repo)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"], {"friction": 0, "prompt_signals": 0})
        (self.repo / "logs" / f"session_digest_{SID[:8]}.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(run("autolearn", self.repo)["value"])

    def test_autolearn_null_when_digest_absent(self):
        row = run("autolearn", self.repo)
        self.assertIsNone(row["value"])
        self.assertIn("absent", row["basis"]["error"])

    def test_routing_audit_true_false_null(self):
        log = self.repo / "logs" / "routing_audit.jsonl"
        write(log, '{"a": 1}\n')
        self.assertIs(run("routing_audit", self.repo)["value"], True)
        write(log, "\n")
        self.assertIs(run("routing_audit", self.repo)["value"], False)
        log.unlink()
        row = run("routing_audit", self.repo)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["source"], "absent")
        log.mkdir()
        self.assertIsNone(run("routing_audit", self.repo)["value"])

    def test_orchestration_metrics_true_false_null(self):
        original = cp._load_tool
        try:
            sd = self.repo / "sd"
            write(sd / "workflows" / "run1.json", "{}")
            cp._load_tool = lambda name: StubMetrics(str(sd))
            self.assertIs(run("orchestration_metrics", self.repo)["value"], True)
            cp._load_tool = lambda name: StubMetrics(str(sd), sidecar=[{"row": 1}])
            (sd / "workflows" / "run1.json").unlink()
            self.assertIs(run("orchestration_metrics", self.repo)["value"], True)
            cp._load_tool = lambda name: StubMetrics(str(sd))
            self.assertIs(run("orchestration_metrics", self.repo)["value"], False)
            cp._load_tool = lambda name: StubMetrics(None)
            self.assertIsNone(run("orchestration_metrics", self.repo)["value"])
        finally:
            cp._load_tool = original

    def test_hot_set_review_true_false_null(self):
        original = cp._load_tool
        try:
            rows = [{"budget": "standing_bytes", "files": [".claude/CLAUDE.md"]}]
            cp._load_tool = lambda name: StubCensus(rows)
            row = run("hot_set_review", self.repo)
            self.assertIs(row["value"], True)
            self.assertEqual(row["basis"]["rows"], rows)
            cp._load_tool = lambda name: StubCensus()
            self.assertIs(run("hot_set_review", self.repo)["value"], False)
            cp._load_tool = lambda name: StubCensus(errors=["missing standing file"])
            self.assertIsNone(run("hot_set_review", self.repo)["value"])
            cp._load_tool = lambda name: StubCensus(unmeasured=["prompt_member.py"])
            row = run("hot_set_review", self.repo)
            self.assertIsNone(row["value"])
            self.assertEqual(row["basis"]["prompt_hooks_unmeasured"], ["prompt_member.py"])
            cp._load_tool = lambda name: StubCensus(rows, unmeasured=["prompt_member.py"])
            self.assertIs(run("hot_set_review", self.repo)["value"], True)
        finally:
            cp._load_tool = original

    def test_hot_set_review_true_for_an_oversized_body_row_and_its_gate_text_names_it(self):
        original = cp._load_tool
        try:
            rows = [{"budget": "oversized_body", "measured": 25_000, "baseline": 24_576, "delta": 424,
                     "files": [".claude/skills/big/SKILL.md"]}]
            cp._load_tool = lambda name: StubCensus(rows)
            row = run("hot_set_review", self.repo)
            self.assertIs(row["value"], True)
            self.assertEqual(row["basis"]["rows"], rows)
        finally:
            cp._load_tool = original
        text = cp.PREDICATES["hot_set_review"][1]
        self.assertIn("oversized skill or command body", text)
        self.assertIn("tripwire budget", text)

    def test_hot_set_review_not_configured_is_null_with_basis_error(self):
        original = cp._load_tool
        census = original("load_census")
        try:
            class Unconfigured(StubCensus):
                NotConfigured = census.NotConfigured

                def over_baseline(self, report):
                    raise self.NotConfigured("no tripwire baseline")

            cp._load_tool = lambda name: Unconfigured()
            row = run("hot_set_review", self.repo)
            self.assertIsNone(row["value"])
            self.assertEqual(row["basis"], {"error": "not-configured"})
        finally:
            cp._load_tool = original

    def test_hot_set_review_on_a_consumer_without_a_tripwire_is_not_configured(self):
        root = self.repo / ".claude"
        home = census_cases.make_complete_root(root)
        with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
            row = run("hot_set_review", self.repo)
        self.assertIsNone(row["value"])
        self.assertEqual(row["basis"], {"error": "not-configured"})

    def test_sync_subsystems_shape_density_false_null(self):
        tr = session_repo(self.repo)
        write(self.repo / ".claude" / "skills" / "project_subsystems" / "SKILL.md", REGISTRY)
        known = {str(self.repo / "Spells" / "a.cs"): 1}
        digest(self.repo, files_modified_counts=known)
        row = run("sync_subsystems", self.repo, tr)
        self.assertIs(row["value"], False)
        digest(self.repo, files_modified_counts={str(self.repo / "NewArea" / "x.cs"): 1})
        row = run("sync_subsystems", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["shape"], ["NewArea"])
        density = {str(self.repo / "Wizard" / f"f{i}.cs"): 1 for i in range(cp.DENSITY_THRESHOLD)}
        digest(self.repo, files_modified_counts=density)
        row = run("sync_subsystems", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["density"], {"wizard": cp.DENSITY_THRESHOLD})
        reserved = {str(self.repo / "logs" / "x.json"): 1, str(self.repo / ".claude" / "a.md"): 1}
        digest(self.repo, files_modified_counts=reserved)
        self.assertIs(run("sync_subsystems", self.repo, tr)["value"], False)

    def test_sync_subsystems_null_when_registry_or_digest_absent(self):
        tr = session_repo(self.repo)
        digest(self.repo, files_modified_counts={str(self.repo / "NewArea" / "x.cs"): 1})
        self.assertIsNone(run("sync_subsystems", self.repo, tr)["value"])
        write(self.repo / ".claude" / "skills" / "project_subsystems" / "SKILL.md", REGISTRY)
        (self.repo / "logs" / f"session_digest_{SID[:8]}.json").unlink()
        self.assertIsNone(run("sync_subsystems", self.repo, tr)["value"])

    def test_regression_gate_true_false_null(self):
        tr = session_repo(self.repo)
        digest(self.repo, files_modified_counts={str(self.repo / "Wizard" / "A.cs"): 2})
        self.assertIs(run("regression_gate", self.repo, tr)["value"], True)
        digest(self.repo, files_modified_counts={str(self.repo / "Wizard" / "a.tscn"): 2})
        self.assertIs(run("regression_gate", self.repo, tr)["value"], False)
        digest(self.repo, files_modified_counts={str(self.repo / "src" / "App.tsx"): 1})
        self.assertIs(run("regression_gate", self.repo, tr)["value"], True)
        digest(self.repo, files_modified_counts={str(self.repo / ".claude" / "tools" / "t.py"): 1})
        self.assertIs(run("regression_gate", self.repo, tr)["value"], False)
        (self.repo / "logs" / f"session_digest_{SID[:8]}.json").unlink()
        self.assertIsNone(run("regression_gate", self.repo, tr)["value"])

    def test_roadmap_predicates_true_false_null(self):
        plans = self.repo / ".claude" / "plans"
        write(plans / "bound.md", "# P\n\n**Roadmap:** Roadmap/foo.md Part 3\n")
        write(plans / "adhoc.md", "# P\n\n**Roadmap:** ad-hoc — owner request\n")
        for stem in ("roadmap_atlas", "update_roadmap"):
            with self.subTest(stem=stem):
                bound = transcript(self.repo, {"file_path": str(plans / "bound.md")})
                row = run(stem, self.repo, bound)
                self.assertIs(row["value"], True)
                self.assertEqual(row["basis"]["roadmap_plans"], ["bound.md"])
                adhoc = transcript(self.repo, {"file_path": str(plans / "adhoc.md")}, {"command": "ls"})
                self.assertIs(run(stem, self.repo, adhoc)["value"], False)
                vault = transcript(self.repo, {"file_path": "C:/Vault/{{PROJECT_NAME}}/Roadmap/roadmap.md"})
                self.assertIs(run(stem, self.repo, vault)["value"], True)
                command = transcript(self.repo, {"command": "run /update_roadmap now"})
                self.assertIs(run(stem, self.repo, command)["value"], True)
                self.assertIsNone(cp.evaluate([stem], cp.Ctx(self.repo, None, None))[stem]["value"])

    def test_roadmap_ignores_the_command_file_and_archived_plan_paths(self):
        plans = self.repo / ".claude" / "plans"
        write(plans / "archive" / "old.md", "**Roadmap:** Roadmap/foo.md Part 1\n")
        path = transcript(self.repo, {"file_path": ".claude/commands/update_roadmap.md"},
                          {"file_path": ".claude/plans/archive/old.md"})
        self.assertIs(run("roadmap_atlas", self.repo, path)["value"], False)

    def test_roadmap_a_named_plan_that_cannot_be_read_is_null_not_false(self):
        plans = self.repo / ".claude" / "plans"
        (plans / "unreadable.md").mkdir(parents=True)
        for stem in ("roadmap_atlas", "update_roadmap"):
            with self.subTest(stem=stem):
                gone = transcript(self.repo, {"file_path": str(plans / "gone.md")})
                self.assertIsNone(run(stem, self.repo, gone)["value"])
                blocked = transcript(self.repo, {"file_path": str(plans / "unreadable.md")})
                self.assertIsNone(run(stem, self.repo, blocked)["value"])

    def test_roadmap_scans_plan_paths_inside_list_inputs(self):
        plans = self.repo / ".claude" / "plans"
        write(plans / "bound.md", "# P\n\n**Roadmap:** Roadmap/foo.md Part 3\n")
        listed = transcript(self.repo, {"paths": [str(plans / "bound.md")]})
        for stem in ("roadmap_atlas", "update_roadmap"):
            with self.subTest(stem=stem):
                row = run(stem, self.repo, listed)
                self.assertIs(row["value"], True)
                self.assertEqual(row["basis"]["roadmap_plans"], ["bound.md"])

    def test_harness_prune_true_false_null(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                           capture_output=True, check=True)
        git("init", "-q")
        self.assertIs(run("harness_prune", self.repo)["value"], False)
        write(self.repo / ".claude" / "plans" / "p.md", "x")
        self.assertIs(run("harness_prune", self.repo)["value"], True)
        (self.repo / ".claude" / "plans" / "p.md").unlink()
        write(self.repo / ".claude" / "scratch" / "entry.txt", "x")
        self.assertIs(run("harness_prune", self.repo)["value"], True)
        (self.repo / ".claude" / "scratch" / "entry.txt").unlink()
        self.assertIsNone(run("harness_prune", Path(self.tmp.name) / "not-a-repo")["value"])

    def test_roadmap_skips_a_record_that_is_not_an_object(self):
        path = transcript(self.repo, {"command": "ls"})
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('[1, 2]\n"text"\n')
        row = run("roadmap_atlas", self.repo, path)
        self.assertIs(row["value"], False)

    def test_harness_prune_ignores_tool_owned_scratch_folders(self):
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], capture_output=True, check=True)
        for name in ("session_end", "boot", "kfm", "eval_out", "linux_preview"):
            write(self.repo / ".claude" / "scratch" / name / "kept.json", "{}")
        row = run("harness_prune", self.repo)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["scratch_entries"], 0)
        write(self.repo / ".claude" / "scratch" / "leftover" / "x.txt", "x")
        self.assertIs(run("harness_prune", self.repo)["value"], True)

    def test_routing_audit_counts_only_rows_since_the_last_aggregation(self):
        log = self.repo / "logs" / "routing_audit.jsonl"
        stats = self.repo / "logs" / "routing_audit_stats.json"
        write(stats, json.dumps({"generated_at": "2026-01-02T10:00:00+00:00"}))
        write(log, '{"ts": "2026-01-02T09:00:00+00:00"}\n')
        row = run("routing_audit", self.repo)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["rows_since"], 0)
        write(log, '{"ts": "2026-01-02T09:00:00+00:00"}\n{"ts": "2026-01-02T11:00:00Z"}\n')
        row = run("routing_audit", self.repo)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["rows_since"], 1)
        write(log, '{"ts": "2026-01-02T09:00:00+00:00"}\n{"no_ts": 1}\n')
        self.assertIsNone(run("routing_audit", self.repo)["value"])
        write(stats, "{not json")
        self.assertIsNone(run("routing_audit", self.repo)["value"])

    def test_parity_with_session_end_check_gated_set(self):
        import session_end_check
        self.assertEqual(set(cp.PREDICATES), set(session_end_check.GATED))
        for stem, (fn, text) in cp.PREDICATES.items():
            self.assertTrue(callable(fn) and isinstance(text, str) and text, stem)

    def test_session_end_check_derives_its_gated_set_from_the_predicates(self):
        import close_predicates as live
        live.PREDICATES["planted_stem"] = (lambda ctx: (False, {}), "planted")
        try:
            checker = load(TOOLS / "session_end_check.py", "session_end_check_planted")
            self.assertIn("planted_stem", checker.GATED)
        finally:
            del live.PREDICATES["planted_stem"]


class SessionChangeTests(unittest.TestCase):
    """changed_files is the digest counter united with the commits and dirty files the transcript shows."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self.tmp.cleanup)
        write(self.repo / ".claude" / "skills" / "project_subsystems" / "SKILL.md", REGISTRY)
        digest(self.repo)

    def test_a_cs_change_committed_without_an_edit_call_gates_the_regression_run(self):
        tr = session_repo(self.repo)
        self.assertIs(run("regression_gate", self.repo, tr)["value"], False)
        bash_commit(self.repo, tr, "Wizard/Delegated.cs")
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Delegated.cs"])

    def test_a_peer_commit_inside_the_window_is_not_the_sessions_change(self):
        tr = session_repo(self.repo)
        commit(self.repo, "Wizard/Old.cs", date=OUTSIDE)
        commit(self.repo, "Wizard/Peer.cs")
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["commits"], 0)

    def test_a_fast_forward_range_is_the_sessions_own_change(self):
        tr = session_repo(self.repo)
        branch = git_out(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "checkout", "-q", "-b", "feat")
        write(self.repo / "Wizard" / "Ff.cs", "x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "ff work")
        git(self.repo, "checkout", "-q", branch)
        append_shell_result(tr, git_out(self.repo, "merge", "--ff-only", "feat"))
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Ff.cs"])

    def test_a_root_commit_printed_by_the_session_is_its_own_change(self):
        repo = self.repo
        git(repo, "init", "-q")
        tr = transcript(repo, {"command": "ls"})
        write(repo / "Wizard" / "First.cs", "x")
        git(repo, "add", "-A")
        printed = git_out(repo, "commit", "-m", "first")
        self.assertIn("(root-commit)", printed)
        append_shell_result(tr, printed)
        row = run("regression_gate", repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/First.cs"])

    def test_a_hash_the_repo_no_longer_has_is_skipped_and_counted(self):
        tr = session_repo(self.repo)
        append_shell_result(tr, "[main 0123456789abcdef0123456789abcdef01234567] gone\n")
        bash_commit(self.repo, tr, "Wizard/Kept.cs")
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Kept.cs"])
        self.assertEqual(row["basis"]["missing_commits"], 1)
        self.assertEqual(row["basis"]["commits"], 1)

    def test_a_lone_missing_hash_is_false_and_not_null(self):
        tr = session_repo(self.repo)
        append_shell_result(tr, "[main 0123456789abcdef0123456789abcdef01234567] gone\n")
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"], {"code_files": 0, "sample": [], "identity": "transcript", "commits": 0,
                                        "missing_commits": 1, "commit_invocations": 1, "unmatched_invocations": 0,
                                        "uncommitted_named": 0})

    def test_a_committed_density_refresh_reaches_sync_subsystems(self):
        tr = session_repo(self.repo)
        for i in range(cp.DENSITY_THRESHOLD):
            bash_commit(self.repo, tr, f"Wizard/f{i}.gd")
        row = run("sync_subsystems", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["density"], {"wizard": cp.DENSITY_THRESHOLD})

    def test_a_worktree_path_is_classified_by_its_repo_relative_path(self):
        tr = session_repo(self.repo)
        worktree = self.repo / ".claude" / "worktrees" / "wt1"
        digest(self.repo, files_modified_counts={
            str(worktree / "Wizard" / f"f{i}.cs"): 1 for i in range(cp.DENSITY_THRESHOLD)})
        row = run("sync_subsystems", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["density"], {"wizard": cp.DENSITY_THRESHOLD})
        digest(self.repo, files_modified_counts={str(worktree / "NewArea" / "x.gd"): 1})
        self.assertEqual(run("sync_subsystems", self.repo, tr)["basis"]["shape"], ["NewArea"])

    def test_one_file_seen_through_a_worktree_and_a_commit_counts_once(self):
        tr = session_repo(self.repo)
        bash_commit(self.repo, tr, "Wizard/A.cs")
        digest(self.repo, files_modified_counts={
            str(self.repo / ".claude" / "worktrees" / "wt1" / "Wizard" / "A.cs"): 1,
            str(self.repo / "Wizard" / "A.cs"): 1})
        self.assertEqual(run("regression_gate", self.repo, tr)["basis"]["sample"], ["Wizard/A.cs"])

    def test_uncommitted_changes_count_only_when_the_live_session_names_them(self):
        tr = session_repo(self.repo)
        write(self.repo / "Wizard" / "Pending.cs", "x")
        self.assertIs(run("regression_gate", self.repo, tr)["value"], False)
        row = run("regression_gate", self.repo, tr, live=True)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["uncommitted_named"], 0)
        named = transcript(self.repo, {"command": "git add Wizard/Pending.cs"})
        row = run("regression_gate", self.repo, named, live=True)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Pending.cs"])
        self.assertEqual(row["basis"]["uncommitted_named"], 1)

    def test_a_worktree_prefixed_dirty_path_named_by_its_repo_path_counts(self):
        session_repo(self.repo)
        write(self.repo / ".claude" / "worktrees" / "wt1" / "Wizard" / "A.cs", "x")
        tr = transcript(self.repo, {"file_path": "Wizard/A.cs"})
        row = run("regression_gate", self.repo, tr, live=True)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/A.cs"])

    def test_a_failing_git_call_is_null_never_false(self):
        repo = Path(self.tmp.name) / "not-a-repo"
        repo.mkdir()
        digest(repo)
        tr = transcript(repo, {"command": "git commit -m x"})
        append_shell_result(tr, "[main abc1234] x\n")
        for stem in ("regression_gate", "sync_subsystems"):
            with self.subTest(stem=stem):
                row = run(stem, repo, tr)
                self.assertIsNone(row["value"])
                self.assertIn("git", row["basis"]["error"])

    def test_a_commit_cut_by_tail_with_no_trailer_is_null_with_the_unmatched_count(self):
        tr = session_repo(self.repo)
        cut_commit(self.repo, tr, "Wizard/Cut.cs")
        for stem in ("regression_gate", "sync_subsystems"):
            with self.subTest(stem=stem):
                row = run(stem, self.repo, tr)
                self.assertIsNone(row["value"])
                self.assertEqual(row["basis"]["unmatched_invocations"], 1)

    def test_the_sessions_trailer_names_a_commit_whose_summary_was_cut(self):
        tr = session_repo(self.repo)
        attach_trailer(tr)
        cut_commit(self.repo, tr, "Wizard/Cut.cs", trailer=TRAILER)
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Cut.cs"])
        self.assertEqual(row["basis"]["identity"], "trailer")

    def test_a_peers_trailer_or_one_before_the_window_is_not_the_sessions(self):
        tr = session_repo(self.repo)
        attach_trailer(tr)
        cut_commit(self.repo, tr, "Wizard/Earlier.cs", date=OUTSIDE, trailer=TRAILER)
        cut_commit(self.repo, tr, "Wizard/Peer.cs", trailer=PEER_TRAILER)
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], False)
        self.assertEqual(row["basis"]["commits"], 0)

    def test_a_non_fast_forward_merge_is_null_without_a_trailer_and_read_through_one(self):
        tr = session_repo(self.repo)
        branch = git_out(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "checkout", "-q", "-b", "feat")
        write(self.repo / "Wizard" / "Merged.cs", "x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"delegate work\n\n{TRAILER}")
        git(self.repo, "checkout", "-q", branch)
        commit(self.repo, "Docs/side.md")
        printed = git_out(self.repo, "merge", "--no-ff", "-m", "merge feat", "feat")
        self.assertNotIn("Updating", printed)
        append_shell_result(tr, printed, command="git merge --no-ff feat")
        self.assertIsNone(run("regression_gate", self.repo, tr)["value"])
        attach_trailer(tr)
        row = run("regression_gate", self.repo, tr)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Merged.cs"])

    def test_a_sync_merge_of_the_base_adds_only_its_resolution(self):
        tr = session_repo(self.repo)
        attach_trailer(tr)
        base = git_out(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(self.repo, "checkout", "-q", "-b", "feat")
        write(self.repo / "Wizard" / "Mine.cs", "x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"my work\n\n{TRAILER}")
        git(self.repo, "checkout", "-q", base)
        commit(self.repo, "Wizard/Peer.cs")
        git(self.repo, "checkout", "-q", "feat")
        printed = git_out(self.repo, "merge", "--no-ff", "-m", "merge base", "-m", TRAILER, base)
        append_shell_result(tr, printed, command=f"git merge --no-ff {base}")
        files, reason = cp.Ctx(self.repo, SID, tr).changed_files()
        self.assertIsNone(reason)
        self.assertIn("Wizard/Mine.cs", files)
        self.assertNotIn("Wizard/Peer.cs", files)

    def test_a_dirty_path_named_in_backslash_form_counts(self):
        session_repo(self.repo)
        write(self.repo / "Wizard" / "Pending.cs", "x")
        named = transcript(self.repo, {"command": "Set-Content " + "\\".join(["C:", "Repo", "Wizard", "Pending.cs"])})
        row = run("regression_gate", self.repo, named, live=True)
        self.assertIs(row["value"], True)
        self.assertEqual(row["basis"]["sample"], ["Wizard/Pending.cs"])

    def test_a_candidate_matches_only_as_a_whole_path(self):
        self.assertFalse(cp._names_path("-", "git add - and more"))
        self.assertFalse(cp._names_path("-", "a - b"))
        self.assertTrue(cp._names_path("Wizard/Pending.cs", "git add Wizard/Pending.cs"))
        self.assertFalse(cp._names_path("Wizard/Pending.cs", "git add Wizard/Pending.cs.bak"))
        self.assertTrue(cp._names_path("Wizard/Pending.cs", "Set-Content C:/Repo/Wizard/Pending.cs"))

    def test_a_stray_dash_file_is_not_named_by_a_dash_in_the_transcript(self):
        session_repo(self.repo)
        write(self.repo / "-", "x")
        named = transcript(self.repo, {"command": "echo a - b"})
        facts = cp._transcript_facts(named, {"-"})
        self.assertEqual(facts.named, set())

    def test_a_gitignored_session_write_is_dropped_from_the_scope(self):
        tr = session_repo(self.repo)
        write(self.repo / ".gitignore", ".claude/scratch/\n")
        bash_commit(self.repo, tr, "Wizard/Cut.cs")
        digest(self.repo, files_modified_counts={str(self.repo / ".claude" / "scratch" / "x.txt"): 1,
                                                 str(self.repo / "Wizard" / "Edited.cs"): 1})
        files, reason = cp.Ctx(self.repo, SID, tr).changed_files()
        self.assertIsNone(reason)
        self.assertEqual(files, ["Wizard/Cut.cs", "Wizard/Edited.cs"])

    def test_a_session_edit_inside_a_submodule_stays_in_the_scope(self):
        tr = session_repo(self.repo)
        src = Path(self.tmp.name) / "libsrc"
        src.mkdir()
        git(src, "init", "-q")
        write(src / "Core.cs", "x")
        git(src, "add", "-A")
        git(src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "lib")
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", src.as_posix(), "Lib")
        write(self.repo / ".gitignore", ".claude/scratch/\n")
        digest(self.repo, files_modified_counts={str(self.repo / "Lib" / "Core.cs"): 1,
                                                 str(self.repo / ".claude" / "scratch" / "x.txt"): 1})
        files, reason = cp.Ctx(self.repo, SID, tr).changed_files()
        self.assertIsNone(reason)
        self.assertEqual(files, ["Lib/Core.cs"])

    def test_scope_emits_the_sessions_commits_and_files_from_one_source(self):
        tr = session_repo(self.repo)
        attach_trailer(tr)
        cut = cut_commit(self.repo, tr, "Wizard/Cut.cs", trailer=TRAILER)
        bash_commit(self.repo, tr, "Tools/Bash.gd")
        printed = git_out(self.repo, "rev-parse", "HEAD").strip()
        digest(self.repo, files_modified_counts={str(self.repo / "Wizard" / "Edited.cs"): 1})
        out = Path(self.tmp.name) / "scope.json"
        code = cp.main(["--scope", "--json", str(out), "--repo", str(self.repo), "--session", SID,
                        "--transcript", tr])
        self.assertEqual(code, 0)
        scope = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(scope["identity"], "trailer")
        self.assertEqual(set(scope["commits"]), {cut, printed})
        self.assertEqual(scope["files"], ["Tools/Bash.gd", "Wizard/Cut.cs", "Wizard/Edited.cs"])
        self.assertIsNone(scope["error"])

    def test_scope_that_cannot_be_read_exits_one_with_no_file_list(self):
        tr = session_repo(self.repo)
        cut_commit(self.repo, tr, "Wizard/Cut.cs")
        out = Path(self.tmp.name) / "scope.json"
        code = cp.main(["--scope", "--json", str(out), "--repo", str(self.repo), "--session", SID,
                        "--transcript", tr])
        self.assertEqual(code, 1)
        scope = json.loads(out.read_text(encoding="utf-8"))
        self.assertIsNone(scope["files"])
        self.assertIn("commit invocation", scope["error"])
        self.assertEqual(scope["basis"]["unmatched_invocations"], 1)

    def test_a_transcript_with_no_readable_record_is_null(self):
        session_repo(self.repo)
        tr = transcript(self.repo, {"command": "ls"})
        write(tr, "not json at all\n")
        self.assertIsNone(run("regression_gate", self.repo, tr)["value"])


class NullCases:
    """One input each predicate cannot read, so a false answer there is a mutant."""

    @staticmethod
    def failing(module):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            bad = []
            for stem in ("autolearn", "sync_subsystems", "regression_gate", "roadmap_atlas", "update_roadmap"):
                if module.evaluate([stem], module.Ctx(repo, None, None))[stem]["value"] is not None:
                    bad.append(stem)
            return bad


class CliTests(unittest.TestCase):
    def test_unknown_stem_exits_two_with_usage(self):
        result = subprocess.run([sys.executable, str(MODULE_PATH), "--only", "bogus"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_json_output_shape_and_a_raising_predicate_is_null_with_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "predicate_autolearn.json"
            original = cp.PREDICATES["autolearn"]

            def boom(ctx):
                raise RuntimeError("planted")
            cp.PREDICATES["autolearn"] = (boom, original[1])
            try:
                code = cp.main(["--only", "autolearn", "--json", str(out), "--repo", tmp, "--session", SID])
            finally:
                cp.PREDICATES["autolearn"] = original
            self.assertEqual(code, 0)
            row = json.loads(out.read_text(encoding="utf-8"))["autolearn"]
            self.assertIsNone(row["value"])
            self.assertIn("planted", row["basis"]["error"])
            self.assertEqual(set(row), {"value", "predicate", "basis"})


class MutantTests(unittest.TestCase):
    def test_real_module_reads_null_for_every_unreadable_input(self):
        self.assertEqual(NullCases.failing(cp), [])

    def test_a_copy_returning_false_for_a_null_input_is_caught(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        mutant_source = source.replace('return None, {"error": reason, **ctx.git_basis}\n    registry',
                                       'return False, {"error": reason, **ctx.git_basis}\n    registry')
        self.assertNotEqual(source, mutant_source)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "close_predicates_mutant.py"
            path.write_text(mutant_source, encoding="utf-8")
            mutant = load(path, "close_predicates_mutant")
            self.assertIn("sync_subsystems", NullCases.failing(mutant))


if __name__ == "__main__":
    unittest.main()
