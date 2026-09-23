"""Cases for tools/load_census.py: listing totals sum correctly over a fixture
tree, a planted oversized body without a selector fails --budgets, a planted
oversized description crosses the listing budget, the hook profile skips
shared-state hooks by name and reports one row per remaining registered hook,
--json mirrors the console numbers, the transitive-load heuristic counts
an unconditional markdown reference while ignoring a phase-gated one, and
`tools/load_census.*.json` profiles supply rule bundles, budgets, entrypoints,
hook sets and the code-edit probe."""
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

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
MODULE_PATH = TOOLS_DIR / "load_census.py"

spec = importlib.util.spec_from_file_location("load_census", MODULE_PATH)
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), "--root", str(root), "--budgets", *extra_args],
            capture_output=True, text=True, timeout=60, env=env)

    def test_planted_oversized_body_without_selector_fails_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            write(root / "skills" / "beta" / "huge.md", "x" * (lc.OVERSIZED_THRESHOLD + 500))
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("no selector", result.stdout)
            self.assertIn("huge.md", result.stdout)

    def test_planted_oversized_description_crosses_listing_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            big_desc = "d" * (lc.BUDGETS["listing_bytes"][0] + 200)
            write(root / "commands" / "huge_desc.md", f"---\ndescription: {big_desc}\n---\n")
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("listing_bytes", result.stdout)

    def test_clean_fixture_tree_passes_budgets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            result = self.run_cli(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("all budgets within cap.", result.stdout)


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

    def test_known_entry_that_grew_past_five_percent_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_complete_root(root)
            size = self._big(root)
            recorded = int(size / 1.06)
            report = self._report_with_known(root, {".claude/skills/big/SKILL.md": recorded})
            fails = lc.check_budgets(report)
            self.assertTrue(any("KNOWN_OVERSIZED_NO_SELECTOR" in f and "skills/big/SKILL.md" in f
                                and "grew" in f for f in fails), fails)
            within = self._report_with_known(root, {".claude/skills/big/SKILL.md": int(size / 1.04)})
            self.assertEqual([f for f in lc.check_budgets(within) if "KNOWN_OVERSIZED_NO_SELECTOR" in f], [])

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
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--root", str(root), "--json", str(json_path)],
                capture_output=True, text=True, timeout=60)
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

    def test_first_prompt_only_hook_has_zero_steady_and_passes_budget(self):
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
            cap, _ = lc.BUDGETS["prompt_emission_bytes"]
            self.assertLessEqual(steady_total, cap)

    def test_every_prompt_hook_over_cap_fails_steady_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".claude"
            make_minimal_root(root)
            cap, _ = lc.BUDGETS["prompt_emission_bytes"]
            hook = root / "hooks" / "always_emits_hook.py"
            write(hook, textwrap.dedent(f"""
                import sys
                sys.stdin.read()
                sys.stdout.write("x" * {cap + 100})
            """))
            settings = self._settings_for(root, "always_emits_hook.py")
            rows = lc.profile_user_prompt_hooks_two_pass(root, settings)
            first_total, steady_total, excluded = lc.compute_prompt_emission(rows)
            self.assertEqual(excluded, [])
            self.assertGreater(steady_total, cap)
            report = {"prompt_emission_first_bytes": first_total,
                      "prompt_emission_steady_bytes": steady_total,
                      "listing": {"total_bytes": 0}, "standing": {"total_bytes": 0},
                      "rules": {"cs_bundle_bytes": 0, "hsm_bt_bundle_bytes": 0,
                                "scene_authoring_bundle_bytes": 0},
                      "hook_chain": {"edit_subprocesses": 0},
                      "oversized_bodies": []}
            fails = lc.check_budgets(report)
            self.assertTrue(any("prompt_emission_bytes" in f for f in fails))

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
        """Runs the REAL shell_census.py / activity_registry.py from this repo
        against the real settings.json, and proves the real shared paths they
        would otherwise touch are untouched: ~/.claude/.routing_state (the
        _hook_state.py default HARNESS_HOOK_STATE_DIR) and this project's real
        .claude/.cache (shell_census.py's cache file when CLAUDE_PROJECT_DIR
        is not overridden)."""
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
        rows = lc.profile_user_prompt_hooks_two_pass(lc.DEFAULT_ROOT, settings)

        after_state = snapshot(real_state_file)
        after_cache = snapshot(real_cache_file)

        self.assertIsNone(after_state, "isolation failed: real ~/.claude/.routing_state/census00.json was written")
        self.assertEqual(before_cache, after_cache,
                          "isolation failed: real .claude/.cache/shell-census.json changed")

        # Assert over what THIS tree registers. A consumer registers activity_registry.py in its
        # project layer, so a tree composing only the base layer runs shell_census.py alone. The
        # invariant is that every registered shared-state hook is isolated -- not that some
        # particular consumer registers both.
        registered = {r["hook"] for r in rows}
        expected = set(lc.ISOLATED_SHARED_STATE_HOOKS) & registered
        self.assertTrue(expected, "no ISOLATED_SHARED_STATE_HOOKS hook is registered: %s" % sorted(registered))
        isolated_names = {r["hook"] for r in rows if r.get("isolated")}
        self.assertTrue(expected <= isolated_names, sorted(expected - isolated_names))
        for name in sorted(expected):
            row = next(r for r in rows if r["hook"] == name)
            self.assertIn(row["status_first"], (0, "0")) if row["status_first"] != "-" else None


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


if __name__ == "__main__":
    unittest.main()
