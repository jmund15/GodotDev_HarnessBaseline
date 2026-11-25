"""Cases for tools/load_census.py: listing totals sum correctly over a fixture
tree, a planted oversized body without a selector is an over-baseline review row that --budgets passes, a planted
oversized description is a tripwire row, the hook profile skips
shared-state hooks by name and reports one row per remaining registered hook,
--json mirrors the console numbers, the transitive-load heuristic counts
an unconditional markdown reference while ignoring a phase-gated one, and
`tools/load_census.*.json` profiles supply rule bundles, budgets, entrypoints,
hook sets, SessionStart skips and the code-edit probe."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import _settings_probe

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hook_call  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
MODULE_PATH = TOOLS_DIR / "load_census.py"

spec = importlib.util.spec_from_file_location("load_census", MODULE_PATH)
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class _Proc:
    def __init__(self, code, out, err):
        self.returncode, self.stdout, self.stderr = code, out, err


def run_tool(args, env=None):
    """`tools/load_census.py`'s CLI, called in process: the same three fields a spawned run returns."""
    code, out, err, _ = _hook_call.call(str(MODULE_PATH), {}, env=env, argv=list(args))
    return _Proc(code, out, err)


FIXTURE_PROFILE = {"rule_bundles": {"cs": {"glob": "**/*.cs"}, "owner": {"rule": "owner_rule.md"}}}


def write_profile(root: Path, name: str, profile: dict) -> None:
    write(root / "tools" / f"load_census.{name}.json", json.dumps(profile))


def make_minimal_root(root: Path) -> None:
    """A small-but-complete `.claude/`-shaped fixture: one command, one skill,
    standing files, a settings.json with no hooks, no rules, and one census
    profile declaring a glob bundle and a named-rule bundle."""
    write(root / "commands" / "alpha.md", "---\ndescription: a short command.\n---\n\n# Alpha\n")
    write(root / "skills" / "beta" / "SKILL.md", "---\ndescription: a short skill.\n---\n\n# Beta\n")
    write(root / "CLAUDE.md", "project claude md\n")
    write(root / "auto-memory" / "MEMORY.md", "memory index\n")
    write(root / "settings.json", json.dumps({"hooks": {}}))
    write_profile(root, "fixture", FIXTURE_PROFILE)


def make_complete_root(root: Path) -> Path:
    """Add every source that a production census requires and return its fake home."""
    make_minimal_root(root)
    for entry in lc.ENTRYPOINTS:
        path = root / entry
        if path.exists():
            continue
        if path.name == "SKILL.md":
            write(path, "---\ndescription: fixture skill.\n---\n\n# Fixture skill\n")
        else:
            write(path, "---\ndescription: fixture entry point.\n---\n\n# Fixture entry point\n")
    write(root / "rules" / "fixture.md", "---\npaths:\n  - \"**/*.cs\"\n---\n\n# Fixture rule\n")
    # Every listed known-oversized body exists at its recorded size; a missing one is stale.
    for rel, size in lc.KNOWN_OVERSIZED_NO_SELECTOR.items():
        head = b"---\ndescription: fixture oversized body.\n---\n\n"
        target = root.parent / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(head + b"x" * (size - len(head)))
    home = root.parent / "fake-home"
    write(home / ".claude" / "CLAUDE.md", "user claude md\n")
    return home


class DeltaTests(unittest.TestCase):
    def test_home_selected_repo_style_counts_as_standing_in_both_snapshots(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / ".claude"
            home = repo / "fake-home"
            make_minimal_root(root)
            write(home / ".claude/settings.json", json.dumps({"outputStyle": "inherited"}))
            style = root / "output-styles/inherited.md"
            write(style, "old\n")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            write(style, "old\nmore\n")
            self.assertIn(str(style), [r["path"] for r in lc.standing_census(root, home=home)["rows"]])
            with patch.object(lc.Path, "home", return_value=home):
                delta = lc.context_delta(root, "HEAD")
            self.assertEqual(delta["standing"], 5)
            self.assertEqual(delta["conditional"], 0)
            row = next(r for r in delta["rows"] if r["path"] == ".claude/output-styles/inherited.md")
            self.assertEqual(row["before"], ("standing", 4))
            self.assertEqual(row["after"], ("standing", 9))

    def test_delta_reports_standing_and_conditional_bytes_from_git_base(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / ".claude"
            make_minimal_root(root)
            write(root / "CLAUDE.md", "@CLAUDE.core.md\n")
            write(root / "CLAUDE.core.md", "core\n")
            write(root / "rules" / "always.md", "always\n")
            write(root / "rules" / "conditional.md", '---\npaths:\n  - "**/*.cs"\n---\n\nrule\n')
            write(root / "commands" / "retired.md", "retired\n")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            write(root / "CLAUDE.core.md", "core\nmore\n")
            write(root / "rules" / "always.md", "always\nmore\n")
            conditional = root / "rules" / "conditional.md"
            write(conditional, conditional.read_text() + "longer\n")
            (root / "commands" / "retired.md").unlink()
            write(root / "skills" / "new" / "SKILL.md", "new\n")
            write(root / "tools" / "irrelevant.py", "tool changes do not load as guidance\n")
            proc = run_tool(["--root", str(root), "--delta", "HEAD"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("context cost: standing +10 B; conditional +3 B;", proc.stdout)

    def test_delta_rejects_an_invalid_base(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
            proc = run_tool(["--root", str(repo / ".claude"), "--delta", "not-a-revision"])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not-a-revision", proc.stderr, proc.stderr)
            self.assertNotIn("context cost:", proc.stdout)

    def test_delta_reads_a_repo_whose_toplevel_sits_above_the_census_root(self):
        """The base's own layout: `.claude` sits under `template/`, and the git toplevel is the repo root.
        Every path form the delta uses must agree from the census root's parent, not the toplevel."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / "template" / ".claude"
            make_minimal_root(root)
            write(root / "CLAUDE.md", "@CLAUDE.core.md\n")
            write(root / "CLAUDE.core.md", "core\n")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            write(root / "CLAUDE.core.md", "core\nmore\n")
            write(root / "skills" / "new" / "SKILL.md", "new\n")
            proc = run_tool(["--root", str(root), "--delta", "HEAD"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("context cost: standing +5 B; conditional +4 B;", proc.stdout)

    def test_an_untracked_settings_style_is_unchanged_in_both_snapshots(self):
        """`settings.local.json` is normally untracked; a style it selects must read the same bytes before
        and after, or `--delta` charges the whole unchanged style file as newly standing."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / ".claude"
            make_minimal_root(root)
            write(root / "output-styles" / "local-style.md", "styled\n")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            write(root / "settings.local.json", json.dumps({"outputStyle": "local-style"}))
            delta = lc.context_delta(root, "HEAD")
            self.assertEqual((delta["standing"], delta["conditional"]), (0, 0))
            self.assertEqual(delta["rows"], [])


class ListingTotalTests(unittest.TestCase):
    def test_listing_total_equals_sum_over_fixture_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "commands" / "gamma.md", "---\ndescription: a second, slightly longer command.\n---\n")
            report = lc.listing_census(root)
            self.assertEqual(report["total_bytes"], sum(r["desc_bytes"] for r in report["rows"]))
            self.assertEqual(report["count"], 3)
            self.assertEqual(report["commands"], 2)
            self.assertEqual(report["skills"], 1)


class WorkflowListingTests(unittest.TestCase):
    """F1: workflow `meta.description` strings appear in the always-loaded skill listing."""

    def test_workflow_description_bytes_count_in_listing_total(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            before = lc.listing_census(root)["total_bytes"]
            write(root / "workflows" / "probe_flow.js", textwrap.dedent("""
                export const meta = {
                  name: 'probe-flow',
                  description: 'Probe workflow that it\\'s counted',
                  phases: [{ title: 'One', detail: 'not a description: never counted' }],
                }

                const schema = { answer: { type: 'string', description: 'schema text, never counted' } }
            """))
            report = lc.listing_census(root)
            rows = [r for r in report["rows"] if r["kind"] == "workflow"]
            self.assertEqual([(r["name"], r["desc_bytes"]) for r in rows],
                             [("probe-flow", len("Probe workflow that it's counted"))])
            self.assertEqual(report["total_bytes"], before + len("Probe workflow that it's counted"))

    def test_workflow_when_to_use_counts_with_its_separator(self):
        # The client lists a workflow as `name: description - whenToUse`.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "workflows" / "probe_flow.js", textwrap.dedent("""
                export const meta = {
                  name: 'probe-flow',
                  description: 'Probe workflow',
                  whenToUse: 'Before a probe run',
                }
            """))
            rows = [r for r in lc.listing_census(root)["rows"] if r["kind"] == "workflow"]
            self.assertEqual([(r["name"], r["desc_bytes"]) for r in rows],
                             [("probe-flow", len("Probe workflow - Before a probe run"))])


class HookMatcherTests(unittest.TestCase):
    """F2: the client's rule. A matcher of only [A-Za-z0-9_|, -] is an exact name list split on
    [|,]; any other matcher is an unanchored regex search."""

    def test_client_matcher_semantics(self):
        for matcher in ("Write, Edit", "Edit ,Write", "^(?:Write|Edit)$", ".*Edit", "Write|Edit", "*", ""):
            with self.subTest(matcher=matcher):
                self.assertTrue(lc.hook_matches(matcher, "Edit"))
        for matcher in ("Read", "Edits", "NotebookEdit", "^(?:Agent|mcp__.*)$"):
            with self.subTest(matcher=matcher):
                self.assertFalse(lc.hook_matches(matcher, "Edit"))


class StandingImportTests(unittest.TestCase):
    """A standing file's `@path` lines load the imported file, recursively, each once."""

    def _census(self, td, claude_md, extra):
        root = Path(td) / ".claude"
        home = make_complete_root(root)
        write(root / "CLAUDE.md", claude_md)
        for rel, text in extra.items():
            write(root / rel, text)
        return lc.standing_census(root, home=home), root

    def test_two_imports_one_importing_a_third_all_count(self):
        with tempfile.TemporaryDirectory() as td:
            base_root = Path(td) / "base" / ".claude"
            make_complete_root(base_root)
            before = lc.standing_census(base_root, home=base_root.parent / "fake-home")["total_bytes"]
            md = "top\n@a.md\n@sub/b.md\n"
            report, root = self._census(Path(td) / "run", md, {
                "a.md": "a" * 40, "sub/b.md": "b" * 30 + "\n@../c.md\n", "c.md": "c" * 20})
            self.assertEqual(report["source_errors"], [])
            names = {Path(r["path"]).name for r in report["rows"]}
            self.assertTrue({"a.md", "b.md", "c.md"} <= names, names)
            sizes = {Path(r["path"]).name: r["bytes"] for r in report["rows"]}
            top = (root / "CLAUDE.md").stat().st_size
            old_top = (base_root / "CLAUDE.md").stat().st_size
            self.assertEqual(report["total_bytes"],
                             before - old_top + top + sizes["a.md"] + sizes["b.md"] + sizes["c.md"])

    def test_import_cycle_terminates_and_counts_each_file_once(self):
        with tempfile.TemporaryDirectory() as td:
            report, root = self._census(td, "@a.md\n", {"a.md": "@b.md\n", "b.md": "@a.md\n@CLAUDE.md\n"})
            self.assertEqual(report["source_errors"], [])
            paths = [r["path"] for r in report["rows"]]
            for name in ("CLAUDE.md", "a.md", "b.md"):
                self.assertEqual(paths.count(str(root / name)), 1, (name, paths))

    def test_missing_import_is_a_source_error(self):
        with tempfile.TemporaryDirectory() as td:
            report, _ = self._census(td, "@ghost.md\n", {})
            self.assertTrue(any("ghost.md" in e for e in report["source_errors"]), report["source_errors"])

    def test_import_inside_a_fence_or_past_four_hops_is_not_followed(self):
        with tempfile.TemporaryDirectory() as td:
            md = "```\n@fenced.md\n```\n@h1.md\n"
            report, _ = self._census(td, md, {
                "fenced.md": "f", "h1.md": "@h2.md\n", "h2.md": "@h3.md\n", "h3.md": "@h4.md\n",
                "h4.md": "@h5.md\n", "h5.md": "x"})
            names = {Path(r["path"]).name for r in report["rows"]}
            self.assertNotIn("fenced.md", names)
            self.assertIn("h4.md", names)
            self.assertNotIn("h5.md", names)

    def test_escaped_space_path_and_absolute_path_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "abs.md"
            write(target, "z" * 9)
            md = "@Design\\ Docs/x.md\n@" + str(target) + "\n"
            report, _ = self._census(td, md, {"Design Docs/x.md": "y"})
            self.assertEqual(report["source_errors"], [])
            names = {Path(r["path"]).name for r in report["rows"]}
            self.assertTrue({"x.md", "abs.md"} <= names, names)


class OutputStyleStandingTests(unittest.TestCase):
    """F3: the active output style loads every session."""

    def test_active_output_style_bytes_count_in_standing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            before = lc.standing_census(root, home=home)["total_bytes"]
            write(root / "settings.json", json.dumps({"hooks": {}, "outputStyle": "loser"}))
            write(root / "settings.local.json", json.dumps({"outputStyle": "terse"}))
            write(root / "output-styles" / "terse.md", "t" * 300)
            write(home / ".claude" / "output-styles" / "loser.md", "l" * 50)
            report = lc.standing_census(root, home=home)
            self.assertEqual(report["source_errors"], [])
            self.assertEqual(report["total_bytes"], before + 300)

    def test_user_level_style_file_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            before = lc.standing_census(root, home=home)["total_bytes"]
            write(home / ".claude" / "settings.json", json.dumps({"outputStyle": "plain"}))
            write(home / ".claude" / "output-styles" / "plain.md", "p" * 70)
            report = lc.standing_census(root, home=home)
            self.assertEqual(report["source_errors"], [])
            self.assertEqual(report["total_bytes"], before + 70)

    def test_named_style_without_file_is_a_source_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "settings.local.json", json.dumps({"outputStyle": "ghost"}))
            fails = lc.check_budgets(lc.build_report(root, home=home))
            self.assertTrue(any("output style" in f and "ghost" in f for f in fails), fails)

    def test_no_style_set_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            report = lc.standing_census(root, home=home)
            self.assertEqual(report["source_errors"], [])


class BudgetFailureTests(unittest.TestCase):
    def run_cli(self, root: Path, *extra_args: str):
        home = root.parent / "fake-home"
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        return run_tool(["--root", str(root), "--budgets", *extra_args], env=env)

    def test_planted_oversized_body_without_selector_is_a_review_row_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "skills" / "beta" / "huge.md", "x" * (lc.OVERSIZED_THRESHOLD + 500))
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"tripwire oversized_body\b")
            rows = [r for r in lc.over_baseline(lc.build_report(root, home=home))
                    if r["budget"] == "oversized_body"]
            size = (root / "skills" / "beta" / "huge.md").stat().st_size
            self.assertEqual(rows, [{"budget": "oversized_body", "measured": size,
                                     "baseline": lc.OVERSIZED_THRESHOLD, "delta": size - lc.OVERSIZED_THRESHOLD,
                                     "files": [".claude/skills/beta/huge.md"]}])

    def test_planted_oversized_description_is_a_tripwire_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write_profile(root, "project", {"budgets": {"listing_bytes": [None, 100]}})
            big_desc = "d" * 400
            for name in ("huge_a", "huge_b"):
                write(root / "commands" / f"{name}.md", f"---\ndescription: {big_desc}\n---\n")
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"tripwire listing_bytes\b")

    def test_over_baseline_standing_tree_exits_zero_with_one_tripwire_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write_profile(root, "project", {"budgets": {"standing_bytes": [None, 100]}})
            write(root / "CLAUDE.md", "s" * 600)
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"tripwire standing_bytes\b")
            rows = lc.over_baseline(lc.build_report(root, home=home, need_budget_hooks=True))
            self.assertEqual([r["budget"] for r in rows], ["standing_bytes"])

    def test_memory_index_over_its_own_baseline_is_its_own_tripwire_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write_profile(root, "project", {"budgets": {"memory_hot_bytes": [None, 20]}})
            write(root / "auto-memory" / "MEMORY.md", "m" * 220)
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r"tripwire memory_hot_bytes\b")
            rows = lc.over_baseline(lc.build_report(root, home=home, need_budget_hooks=True))
            self.assertEqual([r["budget"] for r in rows], ["memory_hot_bytes"])
            self.assertEqual(rows[0]["files"], [".claude/auto-memory/MEMORY.md"])

    def test_a_project_with_no_tripwire_baseline_gets_no_tripwire_row(self):
        """The base ships cap budgets only: with no baseline in a profile, the project's own text
        crosses no tripwire and --budgets prints none."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "CLAUDE.md", "s" * 5_000)
            write(root / "auto-memory" / "MEMORY.md", "m" * 5_000)
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotRegex(result.stdout, r"tripwire (standing_bytes|memory_hot_bytes)\b")
            self.assertEqual(lc.over_baseline(lc.build_report(root, home=home, need_budget_hooks=True)), [])

    def test_rule_bundle_over_its_cap_still_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write_profile(root, "budget", {"budgets": {"cs_rule_bundle_bytes": [10, 5]}})
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("cs_rule_bundle_bytes", result.stdout)

    def test_clean_fixture_tree_passes_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class OverBaselineTests(unittest.TestCase):
    """over_baseline reads the merged budget map: a budget whose cap is None is a tripwire; a tripwire
    the report cannot measure gives an `error: unmeasured` row, never a silent skip."""

    def test_a_bundle_tripwire_names_the_bundles_own_files(self):
        """A tripwire for a rule bundle resolves to that bundle's files, never another budget's."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "rules" / "owner_rule.md", '---\npaths:\n  - "**/*.cs"\n---\n\nowner\n')
            write_profile(root, "ownertrip", {"budgets": {"owner_rule_bundle_bytes": [None, 1]}})
            report = lc.build_report(root, home=home)
            rows = [r for r in lc.over_baseline(report) if r["budget"] == "owner_rule_bundle_bytes"]
            self.assertEqual(len(rows), 1, rows)
            self.assertIn(".claude/rules/owner_rule.md", rows[0]["files"])

    def test_a_tripwire_the_report_cannot_measure_gives_an_unmeasured_row(self):
        report = {"root": "/nowhere", "listing": {"total_bytes": 0},
                  "standing": {"total_bytes": 0, "rows": []}, "rules": {"bundles": {}},
                  "hook_chain": {"edit_subprocesses": 0}, "oversized_bodies": [],
                  "budgets": {"no_such_budget": (None, 1)}}
        self.assertEqual(lc.over_baseline(report),
                         [{"budget": "no_such_budget", "measured": None, "baseline": 1, "delta": None,
                           "error": "unmeasured"}])

    def test_no_tripwire_and_no_oversized_row_gives_no_rows(self):
        report = {"root": "/nowhere", "listing": {"total_bytes": 0},
                  "standing": {"total_bytes": 0, "rows": []}, "rules": {"bundles": {}},
                  "hook_chain": {"edit_subprocesses": 0}, "oversized_bodies": [],
                  "budgets": {"edit_hook_chain_subprocesses": (4, 2)}}
        self.assertEqual(lc.over_baseline(report), [])

    def test_row_carries_delta_files_and_external_marks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo" / ".claude"
            make_complete_root(root)
            home = Path(td) / "outside-home"
            write(home / ".claude" / "CLAUDE.md", "user claude md\n")
            report = lc.build_report(root, home=home, need_budget_hooks=True)
            report["budgets"]["standing_bytes"] = (None, report["standing"]["total_bytes"] - 10)
            rows = lc.over_baseline(report)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["delta"], 10)
            self.assertEqual(row["measured"] - row["baseline"], 10)
            self.assertIn(".claude/CLAUDE.md", row["files"])
            external = [f for f in row["files"] if f.startswith("external:")]
            self.assertEqual(len(external), 1, row["files"])
            self.assertIn("outside-home", external[0])

    def test_baseline_commit_names_the_commit_that_set_the_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            root = repo / ".claude"
            home = make_complete_root(root)
            write(root / "CLAUDE.md", "s" * 2000)
            tool = root / "tools" / "load_census.py"

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                    cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            git("init", "-q")
            write(tool, 'BUDGETS = {\n    "standing_bytes": (None, 1_234),\n}\n')
            git("add", "-A")
            git("commit", "-q", "-m", "set baseline")
            baseline_sha = git("rev-parse", "HEAD")
            write(tool, tool.read_text(encoding="utf-8") + "elsewhere = (9, 1_234),\n")
            git("commit", "-q", "-am", "unrelated literal")
            report = lc.build_report(root, home=home, need_budget_hooks=True)
            report["budgets"]["standing_bytes"] = (None, 1_234)
            rows = lc.over_baseline(report)
            self.assertEqual(rows[0]["baselineCommit"], baseline_sha)

    def test_baseline_commit_follows_a_profile_supplied_tripwire(self):
        """A tripwire the project profile supplies holds no BUDGETS literal, so its baselineCommit is
        the commit that recorded the profile literal, not None."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            root = repo / ".claude"
            home = make_complete_root(root)
            write(root / "CLAUDE.md", "s" * 2000)
            profile = root / "tools" / "load_census.project.json"

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                    cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            git("init", "-q")
            write(profile, json.dumps({"budgets": {"standing_bytes": [None, 100]}}))
            git("add", "-A")
            git("commit", "-q", "-m", "record the profile baseline")
            baseline_sha = git("rev-parse", "HEAD")
            write(profile, json.dumps({"budgets": {"standing_bytes": [None, 200]}}))
            git("commit", "-q", "-am", "re-base the profile baseline")
            re_base_sha = git("rev-parse", "HEAD")
            report = lc.build_report(root, home=home, need_budget_hooks=True)
            self.assertEqual(report["budgets"]["standing_bytes"], (None, 200))
            self.assertEqual(lc.over_baseline(report)[0]["baselineCommit"], re_base_sha)
            report["budgets"]["standing_bytes"] = (None, 100)
            self.assertEqual(lc.over_baseline(report)[0]["baselineCommit"], baseline_sha)


class SelectorRegistryTests(unittest.TestCase):
    def test_every_tool_with_a_catalog_counts_as_a_selector(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            body = root / "skills" / "s" / "reference" / "details.md"
            write(body, "x\n")
            write(root / "tools" / "anysel.py",
                  "from pathlib import Path\n"
                  "CATALOG = Path(__file__).resolve().parent.parent / 'skills' / 's' / 'reference' / 'details.md'\n")
            write(root / "tools" / "other.py", "# names CATALOG only in a comment\nX = 1\n")
            self.assertIn(body.resolve(), lc.load_selector_registry(root))

    def test_live_known_failure_modes_catalog_has_a_selector(self):
        live = Path(lc.__file__).resolve().parent.parent
        catalog = live / "reference" / "known_failure_modes_entries.md"
        self.assertIn(catalog.resolve(), lc.load_selector_registry(live))


class ListingPopulationTests(unittest.TestCase):
    """The census counts only entries the model's listing can show: a hidden or path-scoped
    entry, or a command in a subdirectory, is not in the listing and must not be budgeted."""

    def test_hidden_path_scoped_and_nested_entries_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_minimal_root(root)
            write(root / "commands" / "hidden.md", "---\ndisable-model-invocation: true\ndescription: x\n---\n")
            write(root / "commands" / "agents" / "lens.md", "---\ndescription: nested reference\n---\n")
            write(root / "skills" / "scoped" / "SKILL.md", "---\ndescription: scoped\npaths:\n  - \"a/**\"\n---\n")
            write(root / "skills" / "quiet" / "SKILL.md", "---\ndescription: quiet\ndisable-model-invocation: true\n---\n")
            names = {(r["kind"], r["name"]) for r in lc.listing_census(root)["rows"]}
            self.assertEqual(names, {("command", "commands/alpha.md"), ("skill", "beta")})


class ListingFieldTests(unittest.TestCase):
    """A skill is listed as `description` plus `when_to_use`; each listed file is read once."""

    def test_when_to_use_reads_a_block_scalar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            write(path, "---\ndescription: d\nwhen_to_use: >-\n  on a planted\n  trigger\nname: x\n---\n")
            self.assertEqual(lc.when_to_use(lc.frontmatter(path)), "on a planted\n  trigger")
            self.assertEqual(lc.when_to_use(None), "")

    def test_skill_row_counts_description_plus_when_to_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_minimal_root(root)
            write(root / "skills" / "beta" / "SKILL.md", "---\ndescription: abc\nwhen_to_use: de\n---\n")
            rows = {r["name"]: r for r in lc.listing_census(root)["rows"]}
            self.assertEqual(rows["beta"]["desc_bytes"], len("abc de"))

    def test_listing_census_reads_each_file_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_minimal_root(root)
            reads = []
            real = Path.read_text

            def counted(self_path, *a, **k):
                reads.append(self_path.name)
                return real(self_path, *a, **k)

            Path.read_text = counted
            try:
                lc.listing_census(root)
            finally:
                Path.read_text = real
            md = [n for n in reads if n.endswith(".md")]
            self.assertEqual(len(md), len(set(md)), reads)


class ListingCapsTests(unittest.TestCase):
    """Per-kind listed-text caps (LISTING_CAPS). The over-cap report always prints with
    --budgets; only --caps turns a violation into a failure, because the caps bind only when an
    entry is authored or edited (instruction_quality §8)."""

    def run_cli(self, root: Path, *extra_args: str):
        home = root.parent / "fake-home"
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        return run_tool(["--root", str(root), "--budgets", *extra_args], env=env)

    def test_planted_301b_skill_description_fails_only_with_caps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "skills" / "beta" / "SKILL.md",
                  "---\ndescription: " + "a" * 301 + "\n---\n\n# Beta\n")
            without_caps = self.run_cli(root)
            self.assertEqual(without_caps.returncode, 0, without_caps.stdout + without_caps.stderr)
            self.assertIn("beta", without_caps.stdout)
            with_caps = self.run_cli(root, "--caps")
            self.assertEqual(with_caps.returncode, 1, with_caps.stdout + with_caps.stderr)
            self.assertIn("skill", with_caps.stdout)
            self.assertIn("beta", with_caps.stdout)

    def test_300b_skill_description_passes_at_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "skills" / "beta" / "SKILL.md",
                  "---\ndescription: " + "a" * 300 + "\n---\n\n# Beta\n")
            result = self.run_cli(root, "--caps")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_planted_61b_command_description_fails_only_with_caps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "commands" / "alpha.md",
                  "---\ndescription: " + "a" * 61 + "\n---\n\n# Alpha\n")
            without_caps = self.run_cli(root)
            self.assertEqual(without_caps.returncode, 0, without_caps.stdout + without_caps.stderr)
            with_caps = self.run_cli(root, "--caps")
            self.assertEqual(with_caps.returncode, 1, with_caps.stdout + with_caps.stderr)
            self.assertIn("command", with_caps.stdout)
            self.assertIn("alpha", with_caps.stdout)

    def test_60b_command_description_passes_at_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "commands" / "alpha.md",
                  "---\ndescription: " + "a" * 60 + "\n---\n\n# Alpha\n")
            result = self.run_cli(root, "--caps")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_planted_61b_workflow_description_fails_only_with_caps(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "workflows" / "probe_flow.js", textwrap.dedent(f"""
                export const meta = {{
                  name: 'probe-flow',
                  description: '{"a" * 61}',
                }}
            """))
            without_caps = self.run_cli(root)
            self.assertEqual(without_caps.returncode, 0, without_caps.stdout + without_caps.stderr)
            with_caps = self.run_cli(root, "--caps")
            self.assertEqual(with_caps.returncode, 1, with_caps.stdout + with_caps.stderr)
            self.assertIn("workflow", with_caps.stdout)
            self.assertIn("probe-flow", with_caps.stdout)

    def test_60b_workflow_description_passes_at_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "workflows" / "probe_flow.js", textwrap.dedent(f"""
                export const meta = {{
                  name: 'probe-flow',
                  description: '{"a" * 60}',
                }}
            """))
            result = self.run_cli(root, "--caps")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SourceValidityTests(unittest.TestCase):
    def _report(self, root):
        return lc.build_report(root, home=root.parent / "fake-home")

    def test_missing_standing_file_fails_budget_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            (root / "CLAUDE.md").unlink()
            report = self._report(root)
            fails = lc.check_budgets(report)
            self.assertTrue(any("missing standing file" in f and "CLAUDE.md" in f for f in fails), fails)

    def test_missing_entrypoint_fails_budget_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            (root / lc.ENTRYPOINTS[0]).unlink()
            report = self._report(root)
            fails = lc.check_budgets(report)
            self.assertTrue(any("missing entrypoint" in f and lc.ENTRYPOINTS[0] in f for f in fails), fails)

    def test_malformed_rule_paths_frontmatter_fails_budget_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "rules" / "broken.md", "---\npaths: [\\\"**/*.cs\\\"]\n---\n\nBroken\n")
            report = self._report(root)
            self.assertNotIn("broken.md", report["rules"]["cs_bundle_files"])
            fails = lc.check_budgets(report)
            self.assertTrue(any("malformed rule frontmatter" in f and "broken.md" in f
                                for f in fails), fails)

    def test_rule_in_a_subdirectory_without_paths_fails_budget_check(self):
        # The client loads every rules/** file; one without paths: frontmatter loads in every session.
        # Live defect 2026-09-14: three rules/reference/*_examples.md files (11.6 KB) escaped a rules/*.md glob.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "rules" / "reference" / "examples.md", "# Examples\n\nNo frontmatter.\n")
            fails = lc.check_budgets(self._report(root))
            self.assertTrue(any("malformed rule frontmatter" in f and "examples.md" in f
                                for f in fails), fails)


class KnownOversizedStalenessTests(unittest.TestCase):
    """A KNOWN_OVERSIZED_NO_SELECTOR entry that is no longer an oversized selectorless body masks its
    own regrowth, so the list must stay exact (completion assessment R9, 2026-09-14)."""

    def _report_with_known(self, root, known):
        saved = lc.KNOWN_OVERSIZED_NO_SELECTOR
        # The fixture tree carries every production entry at its recorded size; keep those listed.
        lc.KNOWN_OVERSIZED_NO_SELECTOR = {**saved, **known}
        try:
            return lc.build_report(root, home=root.parent / "fake-home")
        finally:
            lc.KNOWN_OVERSIZED_NO_SELECTOR = saved

    def test_known_entry_that_shrank_fails_budget_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "skills" / "small" / "SKILL.md", "---\ndescription: >-\n  small\n---\n\nsmall\n")
            report = self._report_with_known(root, {".claude/skills/small/SKILL.md": 30_000})
            fails = lc.check_budgets(report)
            self.assertTrue(any("KNOWN_OVERSIZED_NO_SELECTOR" in f and "skills/small/SKILL.md" in f
                                for f in fails), fails)

    def _big(self, root):
        path = root / "skills" / "big" / "SKILL.md"
        write(path, "---\ndescription: >-\n  big\n---\n\n" + "x" * (lc.OVERSIZED_THRESHOLD + 10) + "\n")
        return path.stat().st_size

    def test_known_entry_still_oversized_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            size = self._big(root)
            report = self._report_with_known(root, {".claude/skills/big/SKILL.md": size})
            fails = lc.check_budgets(report)
            self.assertEqual([f for f in fails if "KNOWN_OVERSIZED_NO_SELECTOR" in f], [], fails)

    def test_known_entry_that_grew_past_five_percent_is_a_review_row_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            size = self._big(root)
            recorded = int(size / 1.06)
            report = self._report_with_known(root, {".claude/skills/big/SKILL.md": recorded})
            self.assertEqual([f for f in lc.check_budgets(report) if "KNOWN_OVERSIZED_NO_SELECTOR" in f], [])
            rows = [r for r in lc.over_baseline(report) if r["budget"] == "oversized_body"]
            self.assertEqual(rows, [{"budget": "oversized_body", "measured": size, "baseline": recorded,
                                     "delta": size - recorded, "files": [".claude/skills/big/SKILL.md"]}])
            within = self._report_with_known(root, {".claude/skills/big/SKILL.md": int(size / 1.04)})
            self.assertEqual([r for r in lc.over_baseline(within) if r["budget"] == "oversized_body"], [])

    def test_known_entry_whose_file_is_missing_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            report = self._report_with_known(root, {".claude/skills/gone/SKILL.md": 30_000})
            fails = lc.check_budgets(report)
            self.assertTrue(any("KNOWN_OVERSIZED_NO_SELECTOR" in f and "skills/gone/SKILL.md" in f
                                for f in fails), fails)


class NamedGlobBundleTests(unittest.TestCase):
    def test_rule_bundle_sums_every_rule_sharing_one_glob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "rules" / "owner_rule.md",
                  "---\npaths:\n  - \"**/HSM/**\"\n  - \"**/States/**\"\n---\n\nOwner\n")
            write(root / "rules" / "sibling.md",
                  "---\npaths:\n  - \"**/States/**\"\n  - \"**/Other/**\"\n---\n\nSibling, shares one glob\n")
            write(root / "rules" / "unrelated.md",
                  "---\npaths:\n  - \"**/Other/**\"\n---\n\nUnrelated: no glob shared with the owner itself\n")
            rules = lc.rules_census(root)
            self.assertEqual(sorted(rules["owner_bundle_files"]), ["owner_rule.md", "sibling.md"])
            owner_bytes = (root / "rules" / "owner_rule.md").stat().st_size
            sibling_bytes = (root / "rules" / "sibling.md").stat().st_size
            self.assertEqual(rules["owner_bundle_bytes"], owner_bytes + sibling_bytes)

    def test_rule_bundle_empty_when_rule_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "rules" / "fixture.md", "---\npaths:\n  - \"**/*.tscn\"\n---\n\nNo owner rule here\n")
            rules = lc.rules_census(root)
            self.assertEqual(rules["owner_bundle_files"], [])
            self.assertEqual(rules["owner_bundle_bytes"], 0)

    def test_no_profile_means_no_bundles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            (root / "tools" / "load_census.fixture.json").unlink()
            write(root / "rules" / "fixture.md", "---\npaths:\n  - \"**/*.cs\"\n---\n\nRule\n")
            rules = lc.rules_census(root)
            self.assertEqual(rules["bundles"], {})
            self.assertNotIn("cs_bundle_bytes", rules)


class ProfileTests(unittest.TestCase):
    def test_profile_bundle_budget_fails_when_exceeded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write_profile(root, "zbudget", {"budgets": {"cs_rule_bundle_bytes": [10, 5]}})
            report = lc.build_report(root, home=home)
            fails = lc.check_budgets(report)
            self.assertTrue(any(f.startswith("cs_rule_bundle_bytes") for f in fails), fails)

    def test_profile_entrypoint_missing_fails_budget_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write_profile(root, "extra", {"entrypoints": ["commands/layer_only.md"]})
            fails = lc.check_budgets(lc.build_report(root, home=home))
            self.assertTrue(any("missing entrypoint" in f and "commands/layer_only.md" in f for f in fails), fails)

    def test_unreadable_profile_is_a_source_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "tools" / "load_census.broken.json", "{not json")
            fails = lc.check_budgets(lc.build_report(root, home=home))
            self.assertTrue(any("unreadable census profile" in f and "broken" in f for f in fails), fails)

    def test_profile_hook_sets_union_with_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write_profile(root, "hooks", {"shared_state_hook_skip": ["layer_skip.py"],
                                          "isolated_shared_state_hooks": ["layer_iso.py"]})
            skipped, isolated = lc._hook_sets(root)
            self.assertTrue(lc.SHARED_STATE_HOOK_SKIP | {"layer_skip.py"} <= skipped)
            self.assertTrue(lc.ISOLATED_SHARED_STATE_HOOKS | {"layer_iso.py"} <= isolated)

    def test_code_edit_probe_only_when_a_profile_declares_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            cases = [label for label, _ in lc.build_payloads(root)["PreToolUse"]]
            self.assertNotIn("edit-code", cases)
            write_profile(root, "probe", {"code_edit_probe": {"path": "src/Example.cs"}})
            payloads = lc.build_payloads(root)
            edit = dict(payloads["PreToolUse"])["edit-code"]
            self.assertEqual(Path(edit["tool_input"]["file_path"]), root.parent / "src" / "Example.cs")

    def test_profile_session_start_skip_extends_the_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write_profile(root, "sessionstart", {"session_start_skip": {"layer_start.py": "starts a daemon"}})
            self.assertEqual(lc._session_start_skips(root).get("layer_start.py"), "starts a daemon")
            self.assertIn("session_context_loader.py", lc._session_start_skips(root))

    def test_malformed_session_start_case_input_is_a_source_error_not_a_traceback(self):
        """A malformed `guards/tool_profiles.json` fails the SessionStart case builder; it must become one
        named source error that `--budgets` reports, not a traceback that hides every other budget."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "guards" / "tool_profiles.json", json.dumps({"no_kinds": {}}))
            settings = {"hooks": {"SessionStart": [{"hooks": [{
                "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/session_model_rails.py"',
                "timeout": 5, "type": "command"}]}]}}
            write(root / "settings.json", json.dumps(settings))
            report = lc.build_report(root, home=home, need_session_start_hooks=True)
            errors = report["source_errors"]
            self.assertTrue(any("session_model_rails.py" in e for e in errors), errors)
            self.assertTrue(any(e.startswith("session_start case input invalid") for e in errors), errors)

    def test_a_malformed_model_registry_is_a_session_start_source_error(self):
        """The rails cases also read the model registry, whose malformed-file error is its own type; it
        must land in `source_errors` beside the guards file's failure, not crash the report."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            write(root / "reference" / "external_models.json", "{not json")
            settings = {"hooks": {"SessionStart": [{"hooks": [{
                "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/session_model_rails.py"',
                "timeout": 5, "type": "command"}]}]}}
            write(root / "settings.json", json.dumps(settings))
            report = lc.build_report(root, home=home, need_session_start_hooks=True)
            self.assertTrue(any("RegistryError" in e and "session_model_rails.py" in e
                                for e in report["source_errors"]), report["source_errors"])

    def test_a_layer_session_start_skip_ships_in_its_profile_not_the_tool(self):
        """Only base-registered scripts belong in `SESSION_START_SKIP`; a layer's own script is skipped
        through that layer's `session_start_skip` profile key."""
        layer_script = "layer_only_guard.py"
        self.assertNotIn(layer_script, lc.SESSION_START_SKIP)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "tools" / "load_census.somelayer.json",
                  json.dumps({"session_start_skip": {layer_script: "planted layer script"}}))
            self.assertIn(layer_script, lc._session_start_skips(root))


class HookProfileTests(unittest.TestCase):
    def test_shared_state_hooks_skipped_one_row_per_remaining_hook(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            echo_hook = root / "hooks" / "echo_hook.py"
            write(echo_hook, textwrap.dedent("""
                import sys
                sys.stdin.read()
                sys.stdout.write("ok")
            """))
            skip_name = sorted(lc.SHARED_STATE_HOOK_SKIP)[0]
            settings = {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [
                            {"command": f'{sys.executable} "{root / "hooks" / skip_name}"'},
                            {"command": f'{sys.executable} "{echo_hook}"'},
                        ]}
                    ]
                }
            }
            rows = lc.profile_hooks(root, settings, events=["UserPromptSubmit"])
            self.assertEqual(len(rows), 2)
            skipped = [r for r in rows if r["hook"] == skip_name]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["status"], "skipped(shared-state)")
            real = [r for r in rows if r["hook"] == "echo_hook.py"]
            self.assertEqual(len(real), 1)
            self.assertEqual(real[0]["status"], 0)
            self.assertEqual(real[0]["stdout_bytes"], 2)


class JsonMirrorsConsoleTests(unittest.TestCase):
    def test_json_matches_console_listing_total(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            json_path = Path(td) / "out.json"
            result = run_tool(["--root", str(root), "--json", str(json_path)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(json_path.read_text(encoding="utf-8"))
            printed_total = data["listing"]["total_bytes"]
            self.assertIn(f"description bytes total: {printed_total:,}", result.stdout)
            self.assertEqual(data["standing"]["total_bytes"],
                              sum(r["bytes"] for r in data["standing"]["rows"]))


class TwoPassPromptEmissionTests(unittest.TestCase):
    """`prompt_emission_bytes` must measure the STEADY state (every later
    prompt pays it), not the first-prompt spike a fire-once hook only pays
    once per session."""

    def _settings_for(self, root: Path, *hook_names: str) -> dict:
        return {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"command": f'{sys.executable} "{root / "hooks" / n}"'} for n in hook_names]}
                ]
            }
        }

    def test_first_prompt_only_hook_has_zero_steady_emission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            hook = root / "hooks" / "fire_once_hook.py"
            write(hook, textwrap.dedent("""
                import json, os, sys
                state_dir = os.environ.get("HARNESS_HOOK_STATE_DIR")
                marker = os.path.join(state_dir, "fired.marker")
                if not os.path.exists(marker):
                    open(marker, "w").close()
                    sys.stdin.read()
                    sys.stdout.write("first-prompt-only line")
                else:
                    sys.stdin.read()
            """))
            settings = self._settings_for(root, "fire_once_hook.py")
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            row = next(r for r in rows if r["hook"] == "fire_once_hook.py")
            self.assertGreater(row["stdout_bytes_first"], 0)
            self.assertEqual(row["stdout_bytes_steady"], 0)
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual(excluded, [])
            self.assertGreater(first_total, 0)
            self.assertEqual(steady_total, 0)

    def test_profile_starts_no_capacity_warm_between_passes(self):
        # A posture hook that warms a cold band cache on pass 1 re-emits on pass 2 when
        # the warm lands, so the steady figure flapped by the posture line's size.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            warmed = Path(td) / "band_warmed.marker"
            hook = root / "hooks" / "warming_posture_hook.py"
            write(hook, textwrap.dedent(f"""
                import os, sys
                sys.stdin.read()
                marker = {str(warmed)!r}
                if os.path.exists(marker):
                    sys.stdout.write("band changed: posture re-emitted")
                elif os.environ.get("HARNESS_CAPACITY_WARM") != "0":
                    open(marker, "w").close()
            """))
            settings = self._settings_for(root, "warming_posture_hook.py")
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            row = next(r for r in rows if r["hook"] == "warming_posture_hook.py")
            self.assertEqual(row["stdout_bytes_steady"], 0)
            self.assertFalse(warmed.exists())

    def test_prompt_hooks_over_baseline_are_a_tripwire_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            baseline = 100
            hook = root / "hooks" / "always_emits_hook.py"
            write(hook, textwrap.dedent(f"""
                import sys
                sys.stdin.read()
                sys.stdout.write("x" * {baseline + 100})
            """))
            settings = self._settings_for(root, "always_emits_hook.py")
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual(excluded, [])
            self.assertGreater(steady_total, baseline)
            # `root` names the planted tree: the baseline commit is asked of its folder, not of this checkout.
            report = {"root": str(root),
                      "prompt_emission_first_bytes": first_total,
                      "prompt_emission_steady_bytes": steady_total,
                      "listing": {"total_bytes": 0}, "standing": {"total_bytes": 0, "rows": []},
                      "rules": {"bundles": {}},
                      "hook_chain": {"edit_subprocesses": 0},
                      "oversized_bodies": [],
                      "budgets": {**lc.BUDGETS, "prompt_emission_bytes": (None, baseline)}}
            self.assertEqual(lc.check_budgets(report), [])
            rows = lc.over_baseline(report)
            self.assertEqual([r["budget"] for r in rows], ["prompt_emission_bytes"])
            self.assertEqual(rows[0]["measured"], steady_total)

    def test_non_isolatable_hook_reported_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            hook = root / "hooks" / "cannot_isolate_hook.py"
            write(hook, "import sys\nsys.stdin.read()\n")
            settings = self._settings_for(root, "cannot_isolate_hook.py")
            saved = dict(lc.NON_ISOLATABLE_SHARED_STATE_HOOKS)
            lc.NON_ISOLATABLE_SHARED_STATE_HOOKS["cannot_isolate_hook.py"] = "writes a fixed machine-wide path with no env seam"
            try:
                rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            finally:
                lc.NON_ISOLATABLE_SHARED_STATE_HOOKS.clear()
                lc.NON_ISOLATABLE_SHARED_STATE_HOOKS.update(saved)
            row = next(r for r in rows if r["hook"] == "cannot_isolate_hook.py")
            self.assertIn("cannot-isolate", row["run_note"])
            self.assertIn("no env seam", row["run_note"])
            self.assertEqual(row["status_first"], "-")

    def test_isolated_shared_state_hooks_write_nothing_outside_temp_dirs(self):
        """Runs the REAL shell_census.py from this repo against the real settings.json, and proves the
        real shared paths it would otherwise touch are untouched: ~/.claude/.routing_state (the
        _hook_state.py default HARNESS_HOOK_STATE_DIR) and this project's real `.claude/.cache`
        (shell_census.py's cache file when CLAUDE_PROJECT_DIR is not overridden). The isolation set is
        seeded here: no hook this tree registers needs it (the dispatcher's chain is not shared-state),
        so the case plants the member and the set together."""
        # Targeted files, not whole-directory listings: this repo runs
        # concurrent harness sessions that legitimately write OTHER entries
        # into these SHARED directories while this test runs (a peer lane's
        # own `write_json_atomic` call), so a directory-wide before/after
        # diff is flaky under real concurrency. The exact file an unisolated
        # run WOULD produce is deterministic instead: `_hook_state.py`
        # derives `<session_id[:8]>.json` from `build_payloads`'s fixed
        # synthetic session id ("census0001" -> "census00.json"), and
        # `shell_census.py` always writes `.claude/.cache/shell-census.json`
        # under CLAUDE_PROJECT_DIR. Neither must exist/change if isolation held.
        real_state_file = Path(os.path.expanduser("~/.claude/.routing_state")) / "census00.json"
        real_cache_file = lc.DEFAULT_ROOT / ".cache" / "shell-census.json"

        def snapshot(f: Path):
            return f.stat().st_mtime if f.exists() else None

        before_state = snapshot(real_state_file)
        before_cache = snapshot(real_cache_file)

        settings = _settings_probe.settings(lc.DEFAULT_ROOT)
        # The shared input registers the prompt dispatcher; this plant adds a direct registration of
        # the real write seam, with the set seeded so the case does not depend on production's list.
        settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", []).append(
            {"hooks": [{"type": "command", "timeout": 25,
                        "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/shell_census.py"'}]})
        saved = set(lc.ISOLATED_SHARED_STATE_HOOKS)
        lc.ISOLATED_SHARED_STATE_HOOKS.add("shell_census.py")
        try:
            rows = lc.profile_user_prompt_hooks_two_pass(lc.DEFAULT_ROOT, settings)
        finally:
            lc.ISOLATED_SHARED_STATE_HOOKS.clear()
            lc.ISOLATED_SHARED_STATE_HOOKS.update(saved)

        after_state = snapshot(real_state_file)
        after_cache = snapshot(real_cache_file)

        self.assertIsNone(after_state, "isolation failed: real ~/.claude/.routing_state/census00.json was written")
        self.assertEqual(before_cache, after_cache,
                         "isolation failed: real .claude/.cache/shell-census.json changed")

        isolated_names = {r["hook"] for r in rows if r.get("isolated")}
        self.assertIn("shell_census.py", isolated_names)
        for row in rows:
            if row["hook"] == "shell_census.py" and row["status_first"] != "-":
                self.assertIn(row["status_first"], (0, "0"), row)
                self.assertIn(row["status_steady"], (0, "0"), row)

    def test_the_real_prompt_chain_keeps_the_real_project_cache_untouched(self):
        """The registered chain (prompt_git_state_delta, sidecar_watch_rearm, overnight_ask_guard) and the
        reaper honor their cache-dir overrides: a real run writes nothing into this tree's `.claude/.cache`,
        so a renamed or dropped override fails here rather than in a consumer's checkout."""
        settings = _settings_probe.settings(lc.DEFAULT_ROOT)
        cache = lc.DEFAULT_ROOT / ".cache"

        def chain_cache_files():
            if not cache.is_dir():
                return {}
            return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in cache.iterdir()
                    if p.name.startswith("git-state-") or p.name == "runaway-scan-reaper.stamp"}

        before = chain_cache_files()
        rows = lc.profile_user_prompt_hooks_two_pass(lc.DEFAULT_ROOT, settings)
        self.assertEqual(before, chain_cache_files(),
                         "the real prompt chain wrote into the real .claude/.cache")

        chain = {r["hook"]: r for r in rows if r["hook"] in (
            "prompt_git_state_delta.py", "sidecar_watch_rearm.py", "overnight_ask_guard.py")}
        self.assertEqual(set(chain), {"prompt_git_state_delta.py", "sidecar_watch_rearm.py",
                                      "overnight_ask_guard.py"}, sorted(r["hook"] for r in rows))
        # The git-state delta is the member whose cache write the override exists for; the guard is a
        # declared shared-state skip, so a "-" row is its correct outcome.
        self.assertIn(chain["prompt_git_state_delta.py"]["status_first"], (0, "0"),
                      chain["prompt_git_state_delta.py"])
        for row in chain.values():
            if row["status_first"] != "-":
                self.assertIn(row["status_first"], (0, "0"), row)


class UnmeasuredPromptHookTests(unittest.TestCase):
    """A prompt hook that crashes or times out emitted nothing because it did not run to
    completion, not because it is silent: its zero bytes must not pass the emission budget."""

    def _root_with_hook(self, td, body):
        root = Path(td) / ".claude"
        make_complete_root(root)
        hook = root / "hooks" / "probe_hook.py"
        write(hook, body)
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f'{sys.executable} "{hook}"'}]}]}}
        write(root / "settings.json", json.dumps(settings))
        return root

    def test_crashing_prompt_hook_fails_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root_with_hook(td, "import sys\nsys.stdin.read()\nraise SystemExit(3)\n")
            report = lc.build_report(root, need_budget_hooks=True,
                                     home=root.parent / "fake-home")
            fails = lc.check_budgets(report)
            self.assertTrue(any("unmeasured" in f and "probe_hook.py" in f for f in fails), fails)

    def test_clean_prompt_hook_passes_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root_with_hook(td, "import sys\nsys.stdin.read()\nprint('ok')\n")
            report = lc.build_report(root, need_budget_hooks=True,
                                     home=root.parent / "fake-home")
            self.assertEqual([f for f in lc.check_budgets(report) if "unmeasured" in f], [])


class IsolatedHookCwdTests(unittest.TestCase):
    def test_isolated_hook_runs_with_an_isolated_cwd_and_payload_cwd(self):
        """An isolated shared-state hook that resolves its root from the payload `cwd` or
        `os.getcwd()` (activity_registry.py does) must see the throwaway project, not this checkout."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            probe = Path(td) / "probe.json"
            hook = root / "hooks" / "cwd_probe_hook.py"
            write(hook, textwrap.dedent(f"""
                import json, os, sys
                payload = json.load(sys.stdin)
                with open({str(probe)!r}, "w", encoding="utf-8") as fh:
                    json.dump({{"cwd": os.getcwd(), "payload_cwd": payload.get("cwd"),
                               "project": os.environ.get("CLAUDE_PROJECT_DIR")}}, fh)
            """))
            settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f'{sys.executable} "{hook}"'}]}]}}
            saved = set(lc.ISOLATED_SHARED_STATE_HOOKS)
            lc.ISOLATED_SHARED_STATE_HOOKS.add("cwd_probe_hook.py")
            try:
                lc.profile_user_prompt_hooks_two_pass(root, settings)
            finally:
                lc.ISOLATED_SHARED_STATE_HOOKS.clear()
                lc.ISOLATED_SHARED_STATE_HOOKS.update(saved)
            seen = json.loads(probe.read_text(encoding="utf-8"))
            project = os.path.normcase(os.path.realpath(seen["project"]))
            self.assertEqual(os.path.normcase(os.path.realpath(seen["cwd"])), project, seen)
            self.assertEqual(os.path.normcase(os.path.realpath(seen["payload_cwd"] or "")), project, seen)
            self.assertNotEqual(project, os.path.normcase(os.path.realpath(str(lc.DEFAULT_ROOT.parent))))

    def test_non_isolated_hook_cache_overrides_point_outside_the_checkout(self):
        """A prompt hook the census runs without isolation (prompt_git_state_delta.py, the reaper) keeps its
        cache writes out of the project's `.claude/.cache` through the hooks' own cache-dir overrides."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            probe = Path(td) / "probe.json"
            hook = root / "hooks" / "env_probe_hook.py"
            write(hook, textwrap.dedent(f"""
                import json, os, sys
                sys.stdin.read()
                with open({str(probe)!r}, "w", encoding="utf-8") as fh:
                    json.dump({{k: os.environ.get(k) for k in ("HARNESS_GIT_STATE_CACHE_DIR", "HARNESS_REAPER_CACHE_DIR")}}, fh)
            """))
            settings = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f'{sys.executable} "{hook}"'}]}]}}
            lc.profile_user_prompt_hooks_two_pass(root, settings)
            seen = json.loads(probe.read_text(encoding="utf-8"))
            project_cache = os.path.normcase(os.path.realpath(str(root / ".cache")))
            for name, value in seen.items():
                self.assertTrue(value, "%s is not set for a non-isolated hook" % name)
                self.assertFalse(os.path.normcase(os.path.realpath(value)).startswith(project_cache), seen)


class RegisteredTimeoutTests(unittest.TestCase):
    def test_prompt_hook_runs_under_its_registered_timeout(self):
        """The live harness kills a hook at its registered `timeout`; a profiler that waits longer
        counts output the harness would never deliver."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            hook = root / "hooks" / "slow_hook.py"
            write(hook, "import sys, time\nsys.stdin.read()\ntime.sleep(3)\nprint('late')\n")
            settings = {"hooks": {"UserPromptSubmit": [{"hooks": [
                {"command": f'{sys.executable} "{hook}"', "timeout": 1}]}]}}
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            row = next(r for r in rows if r["hook"] == "slow_hook.py")
            self.assertEqual(row["status_first"], "TIMEOUT")
            self.assertEqual(lc.unmeasured_prompt_hooks(rows), ["slow_hook.py (exit TIMEOUT/TIMEOUT)"])


class GeneralRegisteredTimeoutTests(unittest.TestCase):
    def test_general_profiler_uses_registered_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            hook = root / "hooks" / "slow_pretool_hook.py"
            write(hook, "import sys, time\nsys.stdin.read()\ntime.sleep(3)\nprint('late')\n")
            settings = {"hooks": {"PreToolUse": [{"hooks": [
                {"command": f'{sys.executable} "{hook}"', "timeout": 1}]}]}}
            rows = lc.profile_hooks(root, settings, events=["PreToolUse"])
            row = next(r for r in rows if r["hook"] == "slow_pretool_hook.py")
            self.assertEqual(row["status"], "TIMEOUT")
            self.assertEqual(row["ms"], 1000)


class LiveMachineExclusionTests(unittest.TestCase):
    def test_live_machine_hook_reported_separately_and_excluded_from_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            settings = {
                "hooks": {"UserPromptSubmit": [{"hooks": [
                    {"command": f'{sys.executable} "{root / "hooks" / "shell_census.py"}"'}
                ]}]}
            }
            # shell_census.py doesn't exist in this fixture root -- copy the real one.
            import shutil as _shutil
            (root / "hooks").mkdir(parents=True, exist_ok=True)
            _shutil.copy(lc.DEFAULT_ROOT / "hooks" / "shell_census.py", root / "hooks" / "shell_census.py")
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            row = next(r for r in rows if r["hook"] == "shell_census.py")
            self.assertTrue(row["excluded_from_budget"])
            self.assertTrue(row["exclude_reason"])
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual(first_total, 0)
            self.assertEqual(steady_total, 0)
            self.assertEqual(len(excluded), 1)


class PromptDispatcherExpansionTests(unittest.TestCase):
    """A registered `prompt_dispatch.py` is expanded into one row per `CHAIN` member, each profiled
    as if registered directly under the member's `CHAIN` budget; the dispatcher gets no row."""

    DISPATCHER = "prompt_dispatch.py"
    POISON = 'raise RuntimeError("the census must read CHAIN, never run the dispatcher")\n'

    def _root(self, td, chain_source: str) -> Path:
        root = Path(td) / ".claude"
        make_minimal_root(root)
        write(root / "hooks" / self.DISPATCHER, chain_source + "\n" + self.POISON)
        return root

    def _register(self, root: Path, timeout: int = 35) -> dict:
        return {"hooks": {"UserPromptSubmit": [{"hooks": [
            {"command": f'{sys.executable} "{root / "hooks" / self.DISPATCHER}"', "timeout": timeout}]}]}}

    def test_registered_dispatcher_expands_to_one_row_per_member(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, 'CHAIN: tuple = (("stub_a", 5), ("stub_b", 5))')
            write(root / "hooks" / "stub_a.py", "import sys\nsys.stdin.read()\nsys.stdout.write('aaa')\n")
            write(root / "hooks" / "stub_b.py", "import sys\nsys.stdin.read()\nsys.stdout.write('bb')\n")
            rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
            self.assertEqual([r["hook"] for r in rows], ["stub_a.py", "stub_b.py"])
            self.assertEqual([r["stdout_bytes_first"] for r in rows], [3, 2])
            self.assertEqual([r["status_first"] for r in rows], [0, 0])
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual((first_total, steady_total, excluded), (5, 5, []))

    def test_member_runs_under_its_chain_budget_not_the_registered_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "CHAIN = ((\"slow_member\", 1),)")
            write(root / "hooks" / "slow_member.py", "import sys, time\nsys.stdin.read()\ntime.sleep(3)\nprint('late')\n")
            rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root, timeout=35))
            self.assertEqual([r["hook"] for r in rows], ["slow_member.py"])
            self.assertEqual(rows[0]["status_first"], "TIMEOUT")

    def test_crashing_member_is_still_reported_unmeasured(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, 'CHAIN = (("stub_ok", 5), ("stub_crash", 5))')
            write(root / "hooks" / "stub_ok.py", "import sys\nsys.stdin.read()\nprint('fine')\n")
            write(root / "hooks" / "stub_crash.py", "import sys\nsys.stdin.read()\nraise SystemExit(3)\n")
            rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
            self.assertEqual(lc.unmeasured_prompt_hooks(rows), ["stub_crash.py (exit 3/3)"])

    def test_live_machine_and_isolation_apply_by_member_basename(self):
        import shutil as _shutil
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, 'CHAIN = (("shell_census", 25),)')
            _shutil.copy(lc.DEFAULT_ROOT / "hooks" / "shell_census.py", root / "hooks" / "shell_census.py")
            saved = set(lc.ISOLATED_SHARED_STATE_HOOKS)
            lc.ISOLATED_SHARED_STATE_HOOKS.add("shell_census.py")  # the planted member, not this checkout's registration
            try:
                rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
            finally:
                lc.ISOLATED_SHARED_STATE_HOOKS.clear()
                lc.ISOLATED_SHARED_STATE_HOOKS.update(saved)
            self.assertEqual([r["hook"] for r in rows], ["shell_census.py"])
            self.assertTrue(rows[0]["isolated"])
            self.assertTrue(rows[0]["excluded_from_budget"])
            self.assertTrue(rows[0]["exclude_reason"])
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual((first_total, steady_total, len(excluded)), (0, 0, 1))

    def test_absent_member_is_skipped_with_no_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, 'CHAIN = (("stub_a", 5), ("not_adopted", 5))')
            write(root / "hooks" / "stub_a.py", "import sys\nsys.stdin.read()\nsys.stdout.write('aaa')\n")
            rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
            self.assertEqual([r["hook"] for r in rows], ["stub_a.py"])
            self.assertEqual(lc.unmeasured_prompt_hooks(rows), [])
            rows = lc.profile_hooks(root, self._register(root), events=["UserPromptSubmit"])
            self.assertEqual([r["hook"] for r in rows], ["stub_a.py"])

    def test_unreadable_chain_yields_one_visible_dispatcher_row(self):
        cases = {
            "non-literal": "def _members():\n    return ()\nCHAIN = _members()",
            "no chain": "OTHER = (('a', 1),)",
            "wrong shape": "CHAIN = ('stub_a', 5)",
            "empty": "CHAIN = ()",
        }
        for label, source in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                root = self._root(td, source)
                rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
                self.assertEqual([r["hook"] for r in rows], [self.DISPATCHER])
                self.assertIn("unreadable", rows[0]["run_note"])
                self.assertIn("CHAIN", rows[0]["run_note"])
                self.assertEqual(len(lc.unmeasured_prompt_hooks(rows)), 1)

    def test_missing_dispatcher_file_yields_one_visible_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            rows = lc.profile_user_prompt_hooks_two_pass(root, self._register(root))
            self.assertEqual([r["hook"] for r in rows], [self.DISPATCHER])
            self.assertIn("unreadable", rows[0]["run_note"])
            self.assertEqual(len(lc.unmeasured_prompt_hooks(rows)), 1)

    def test_direct_registration_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "hooks" / "direct_hook.py", "import sys\nsys.stdin.read()\nsys.stdout.write('dd')\n")
            write(root / "hooks" / "prompt_dispatch_helper.py", "import sys\nsys.stdin.read()\nsys.stdout.write('h')\n")
            settings = {"hooks": {"UserPromptSubmit": [{"hooks": [
                {"command": f'{sys.executable} "{root / "hooks" / "direct_hook.py"}"', "timeout": 7},
                {"command": f'{sys.executable} "{root / "hooks" / "prompt_dispatch_helper.py"}"'}]}]}}
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            self.assertEqual([r["hook"] for r in rows], ["direct_hook.py", "prompt_dispatch_helper.py"])
            self.assertEqual([r["stdout_bytes_first"] for r in rows], [2, 1])
            self.assertFalse(any(r["excluded_from_budget"] or r["isolated"] for r in rows))

    def test_general_profiler_expands_the_dispatcher_too(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, 'CHAIN = (("stub_a", 5), ("stub_b", 5))')
            write(root / "hooks" / "stub_a.py", "import sys\nsys.stdin.read()\nsys.stdout.write('aaa')\n")
            write(root / "hooks" / "stub_b.py", "import sys\nsys.stdin.read()\nsys.stdout.write('bb')\n")
            rows = lc.profile_hooks(root, self._register(root), events=["UserPromptSubmit"])
            self.assertEqual([r["hook"] for r in rows], ["stub_a.py", "stub_b.py"])
            self.assertEqual([r["stdout_bytes"] for r in rows], [3, 2])

    def test_general_profiler_names_an_unreadable_chain(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "CHAIN = compute()")
            rows = lc.profile_hooks(root, self._register(root), events=["UserPromptSubmit"])
            self.assertEqual([r["hook"] for r in rows], [self.DISPATCHER])
            self.assertIn("unreadable", str(rows[0]["status"]))


class TransitiveLoadHeuristicTests(unittest.TestCase):
    def test_unconditional_reference_counted_phase_gated_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "reference" / "foo.md", "unconditional target\n")
            write(root / "reference" / "bar.md", "gated target, should never be summed\n")
            write(root / "commands" / "entry.md", textwrap.dedent("""
                ---
                description: entry point fixture.
                ---

                See `reference/foo.md` for the pattern.
                When doing X, read `reference/bar.md` instead.
            """))
            result = lc.transitive_load("commands/entry.md", root)
            self.assertIn(".claude/reference/foo.md", result["referenced"])
            self.assertNotIn(".claude/reference/foo.md", result["gated_excluded"])
            self.assertIn(".claude/reference/bar.md", result["gated_excluded"])
            self.assertNotIn(".claude/reference/bar.md", result["referenced"])
            foo_bytes = (root / "reference" / "foo.md").stat().st_size
            self.assertEqual(result["referenced_bytes"], foo_bytes)
            self.assertEqual(result["total_bytes"], result["entry_bytes"] + foo_bytes)

    def test_shape_gated_reference_is_excluded(self):
        """A reference handed out for one plan shape only (code vs meta) is not an
        every-invocation load. plan_check 1g names design_litmus and scene_authoring for
        code plans only; the census summed them into every plan_check invocation."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            write(root / "reference" / "always.md", "unconditional target\n")
            write(root / "rules" / "litmus.md", "code-shape target, never summed\n")
            write(root / "rules" / "meta_only.md", "meta-shape target, never summed\n")
            write(root / "commands" / "entry.md", textwrap.dedent("""
                ---
                description: entry point fixture.
                ---

                Read `reference/always.md` first.
                Code design lenses receive `rules/litmus.md` whole plus the family rows.
                For meta plans the doctrine lens reads `rules/meta_only.md`.
            """))
            result = lc.transitive_load("commands/entry.md", root)
            self.assertEqual(result["referenced"], [".claude/reference/always.md"])
            self.assertIn(".claude/rules/litmus.md", result["gated_excluded"])
            self.assertIn(".claude/rules/meta_only.md", result["gated_excluded"])
            self.assertEqual(result["referenced_bytes"],
                             (root / "reference" / "always.md").stat().st_size)


class JsonOutputTests(unittest.TestCase):
    """`--json PATH` is an output path; a reader who mistakes it for an input must not lose that file."""

    def run_cli(self, root: Path, out: Path):
        home = root.parent / "fake-home"
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
        return run_tool(["--root", str(root), "--json", str(out)], env=env)

    def test_an_existing_non_report_file_is_refused_and_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            target = root / "skills" / "beta" / "SKILL.md"
            write(target, "# a skill someone meant to census\n")
            before = target.read_bytes()
            result = self.run_cli(root, target)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("output path", result.stderr)
            self.assertEqual(target.read_bytes(), before)

    def test_a_new_path_and_a_prior_report_are_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            out = Path(td) / "out" / "census.json"
            self.assertEqual(self.run_cli(root, out).returncode, 0)
            self.assertIn("listing", json.loads(out.read_text(encoding="utf-8")))
            self.assertEqual(self.run_cli(root, out).returncode, 0)


class ReattachCapTests(unittest.TestCase):
    """The client re-attaches an invoked skill or command body cut at SKILL_REATTACH_CHARS characters, so a
    longer body returns truncated. The census imports that constant, counts characters (not bytes) over the
    bodies a user or agent invokes, and reports each one over it."""

    def _fixture(self, td):
        root = Path(td) / ".claude"
        home = make_complete_root(root)
        head = "---\ndescription: fixture body.\n---\n\n"
        cap = lc.SKILL_REATTACH_CHARS
        write(root / "commands" / "over.md", head + "x" * (cap - len(head) + 1))
        write(root / "commands" / "exact.md", head + "x" * (cap - len(head)))
        write(root / "commands" / "wide.md", head + "\u00e9" * cap)
        write(root / "skills" / "gamma" / "SKILL.md", head + "y" * cap)
        write(root / "commands" / "checklists" / "long.md", head + "w" * cap)
        write(root / "skills" / "gamma" / "reference" / "detail.md", "z" * (cap * 2))
        write(root / "commands" / "agents" / "brief.md", "z" * (cap * 2))
        return root, home

    def test_constant_is_the_hook_states_own(self):
        hs_spec = importlib.util.spec_from_file_location(
            "hook_state_for_census", TOOLS_DIR.parent / "hooks" / "_hook_state.py")
        hs = importlib.util.module_from_spec(hs_spec)
        hs_spec.loader.exec_module(hs)
        self.assertEqual(lc.SKILL_REATTACH_CHARS, hs.SKILL_REATTACH_CHARS)

    def test_reports_characters_over_the_cap_in_invocable_bodies_only(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._fixture(td)
            report = lc.build_report(root, home=home)
            rows = {r["path"]: r["chars"] for r in report["reattach_over_cap"]}
            cap = lc.SKILL_REATTACH_CHARS
            head = len("---\ndescription: fixture body.\n---\n\n")
            self.assertEqual(rows, {
                ".claude/commands/over.md": cap + 1,
                ".claude/commands/checklists/long.md": head + cap,
                ".claude/commands/wide.md": head + cap,
                ".claude/skills/gamma/SKILL.md": head + cap,
            })

    def test_a_multibyte_body_under_the_cap_in_characters_is_not_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._fixture(td)
            write(root / "commands" / "narrow.md", "---\ndescription: d.\n---\n\n" + "\u00e9" * 15_000)
            report = lc.build_report(root, home=home)
            self.assertGreater((root / "commands" / "narrow.md").stat().st_size, lc.SKILL_REATTACH_CHARS)
            self.assertNotIn(".claude/commands/narrow.md", {r["path"] for r in report["reattach_over_cap"]})

    def test_console_report_lists_each_row_and_states_the_byte_trigger_relation_once(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._fixture(td)
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
            result = run_tool(["--root", str(root)], env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(".claude/commands/over.md", result.stdout)
            self.assertEqual(result.stdout.count("independent of the byte-based oversized review trigger"), 1)

    def test_report_that_carries_no_row_prints_none(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            home = make_complete_root(root)
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
            result = run_tool(["--root", str(root)], env=env)
            self.assertEqual(lc.build_report(root, home=home)["reattach_over_cap"], [])
            self.assertIn("none", result.stdout)


class SessionStartEmissionTests(unittest.TestCase):
    """A registered SessionStart command's stdout is hook text: past HOOK_TEXT_CAP the client shows a 2,000-character
    preview. The census runs each measured SessionStart command isolated and --budgets fails one past the cap."""

    def _tree(self, td, stdout_bytes):
        root = Path(td) / ".claude"
        home = make_complete_root(root)
        write(root / "hooks" / "stub_start.py", "import sys\nsys.stdout.write('x' * %d)\n" % stdout_bytes)
        settings = {"hooks": {"SessionStart": [{"hooks": [{
            "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/stub_start.py"', "timeout": 30, "type": "command"}]}]}}
        write(root / "settings.json", json.dumps(settings))
        return root, home

    def test_a_planted_session_start_stdout_past_the_hook_text_cap_fails_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._tree(td, 10_001)
            fails = lc.check_budgets(lc.build_report(root, home=home, need_session_start_hooks=True))
            self.assertTrue(any("stub_start.py" in f for f in fails), fails)

    def test_a_tripwire_only_report_skips_the_session_start_matrix(self):
        """The close's hot-set review reads only null-cap budgets and no SessionStart budget is one, so
        `need_budget_hooks` alone must not pay for the SessionStart matrix; `--budgets` still runs it."""
        with tempfile.TemporaryDirectory() as td:
            root, home = self._tree(td, 10)
            tripwire_only = lc.build_report(root, home=home, need_budget_hooks=True)
            self.assertNotIn("session_start_rows", tripwire_only)
            budgets_run = lc.build_report(root, home=home, need_session_start_hooks=True)
            self.assertIn("session_start_rows", budgets_run)

    def test_a_planted_session_start_stdout_at_the_hook_text_cap_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._tree(td, 10_000)
            fails = lc.check_budgets(lc.build_report(root, home=home, need_session_start_hooks=True))
            self.assertFalse(any("stub_start.py" in f for f in fails), fails)


class SessionStartInjectionTests(unittest.TestCase):
    """SessionStart text is part of what a session loads. A group with no matcher fires at both the startup and
    the compact source, and a hook that varies its text by reader gets one counted row per reader."""

    def _tree(self, td):
        root = Path(td) / ".claude"
        make_complete_root(root)
        write(root / "hooks" / "stub_start.py",
              "import json, sys\n"
              "sys.stdout.write('x' * (100 if json.load(sys.stdin).get('source') == 'startup' else 250))\n")
        write(root / "hooks" / "stub_reader.py",
              "import json, sys\n"
              "sys.stdout.write('y' * (7 if json.load(sys.stdin).get('model') == 'alpha' else 40))\n")
        settings = {"hooks": {"SessionStart": [{"hooks": [
            {"command": f'{sys.executable} "{root / "hooks" / "stub_start.py"}"', "timeout": 30,
             "type": "command"},
            {"command": f'{sys.executable} "{root / "hooks" / "stub_reader.py"}"', "timeout": 30,
             "type": "command"},
        ]}]}}
        write(root / "settings.json", json.dumps(settings))
        return root

    def test_a_group_with_no_matcher_runs_at_both_the_startup_and_compact_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._tree(td)
            rows, _, _ = lc.profile_session_start_hooks(root, lc.load_settings(root))
            cases = sorted(r["case"] for r in rows if r["hook"] == "stub_start.py")
            self.assertEqual(cases, ["compact", "startup"])

    def test_each_reader_counts_its_own_text_plus_the_commands_that_do_not_vary(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._tree(td)
            saved = dict(lc.SESSION_START_CASES)
            lc.SESSION_START_CASES["stub_reader.py"] = lambda _root: [
                ("model=alpha", {}, lc._session_start_payload("alpha", "startup")),
                ("model=beta", {}, lc._session_start_payload("beta", "startup")),
            ]
            try:
                rows, _, _ = lc.profile_session_start_hooks(root, lc.load_settings(root))
            finally:
                lc.SESSION_START_CASES.clear()
                lc.SESSION_START_CASES.update(saved)
            injection = lc.session_start_injection(rows, standing_bytes=1000)
            by_reader = {r["reader"]: r for r in injection["rows"]}
            self.assertEqual(by_reader["model=alpha"]["bytes"], 107, injection)  # 7 own + 100 startup
            self.assertEqual(by_reader["model=beta"]["bytes"], 140, injection)   # 40 own + 100 startup
            self.assertEqual(by_reader["model=alpha"]["load_bytes"], 1107, injection)
            self.assertEqual(by_reader["compact"]["bytes"], 250, injection)


class BudgetsHelpTests(unittest.TestCase):
    def test_budgets_help_names_the_tripwire_exception(self):
        result = subprocess.run([sys.executable, str(MODULE_PATH), "--help"], capture_output=True, text=True,
                                timeout=60, env=dict(os.environ, COLUMNS="400"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tripwire", result.stdout)

    def test_caps_without_budgets_is_refused(self):
        """`--caps` only acts inside the `--budgets` branch; alone it must refuse, not exit 0 as a pass."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            result = run_tool(["--root", str(root), "--caps"])
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("--caps needs --budgets", result.stderr)


if __name__ == "__main__":
    unittest.main()
