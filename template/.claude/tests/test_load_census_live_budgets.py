"""Live-tree budget proof for tools/load_census.py (D2): no other proof ever
runs `--budgets`, so a commit could exceed a cap silently. This file runs the
cheapest invocation that still evaluates every budget -- `--budgets` alone,
with no `--profile-hooks` (which would additionally run the PreToolUse /
PostToolUse hook profiler that `--budgets` does not need) and no `--root`
override (the default root is this real `.claude/` tree) -- against the real
repository tree, and separately proves the check actually binds by
monkeypatching one live-measured cap below its live value through the
module's own `BUDGETS` dict (never by editing load_census.py)."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
MODULE_PATH = TOOLS_DIR / "load_census.py"
ROOT = Path(__file__).resolve().parents[1]  # the real .claude/ tree

spec = importlib.util.spec_from_file_location("load_census_live", MODULE_PATH)
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)

# The census counts the user's global CLAUDE.md as a standing file and reports a missing one as a source error, so a
# live verdict read from the caller's home differs per machine. The proof plants one empty global file and measures
# the repository's own bytes.
HOME = Path(tempfile.mkdtemp(prefix="census_home_"))
(HOME / ".claude").mkdir()
(HOME / ".claude" / "CLAUDE.md").write_text("", encoding="utf-8")
HOME_ENV = dict(os.environ, HOME=str(HOME), USERPROFILE=str(HOME))
_SHARED = {}


def budgets_cli():
    """One `--budgets` run, shared by every case that reads its output: each run profiles every budget hook."""
    if "cli" not in _SHARED:
        _SHARED["cli"] = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--budgets"],
            capture_output=True, text=True, timeout=120, env=HOME_ENV)
    return _SHARED["cli"]


def hooked_report():
    """One in-process report with the budget hooks measured, shared by the cases that read it."""
    if "report" not in _SHARED:
        _SHARED["report"] = lc.build_report(ROOT, profile_hooks_flag=False, need_budget_hooks=True, home=HOME)
    return _SHARED["report"]


class LiveBudgetsPassTests(unittest.TestCase):
    def test_cheapest_budgets_invocation_passes_on_the_real_tree(self):
        """`python3 .claude/tools/load_census.py --budgets` -- the cheapest
        invocation that still evaluates every budget, since `--budgets` alone
        already forces `profile_user_prompt_hooks_two_pass` (the
        prompt-emission budget needs it) without paying for the separate
        `--profile-hooks` PreToolUse/PostToolUse profiler pass."""
        result = budgets_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all budgets within cap.", result.stdout)

    def test_live_report_has_no_source_errors_or_exceeded_cap(self):
        fails = lc.check_budgets(hooked_report())
        self.assertEqual(fails, [], fails)


class LiveTripwireTests(unittest.TestCase):
    def test_every_over_baseline_budget_prints_one_tripwire_line(self):
        rows = lc.over_baseline(hooked_report())
        result = budgets_cli()
        lines = [l for l in result.stdout.splitlines() if l.lstrip().startswith("tripwire ")]
        self.assertEqual(len(lines), len(rows), (lines, rows))


class PlantedLowCapFailsTests(unittest.TestCase):
    def test_cap_planted_below_the_live_cs_bundle_value_fails(self):
        """A cap set below the live `cs_rule_bundle_bytes` value must fail --
        injected through the report's merged `budgets` (the tool's own interface: base `BUDGETS` plus
        each `tools/load_census.*.json` profile), never by editing load_census.py."""
        report = lc.build_report(ROOT, profile_hooks_flag=False, need_budget_hooks=False)
        live_bytes = report["rules"]["cs_bundle_bytes"]
        baseline = report["budgets"]["cs_rule_bundle_bytes"][1]
        report["budgets"]["cs_rule_bundle_bytes"] = (live_bytes - 1, baseline)
        fails = lc.check_budgets(report)
        self.assertTrue(
            any(f.startswith("cs_rule_bundle_bytes ") and " > " in f for f in fails), fails)


if __name__ == "__main__":
    unittest.main()
