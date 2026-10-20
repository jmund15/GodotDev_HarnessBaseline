"""Proof that every callable registry row declares its context window or a windowGap.

Fixtures plant on copies of the live registry. Run: python3 .claude/tests/test_model_context_windows.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".claude/tools"
sys.path.insert(0, str(TOOLS))
import model_registry as mr  # noqa: E402

LIVE = json.loads((ROOT / ".claude/reference/external_models.json").read_text(encoding="utf-8"))
PLANTED = "zz-window-row"
GAP = {"reason": "the catalog lists no window", "checked": "https://example.test/catalog", "asOf": "2026-09-30"}


def row_of(data, mid):
    return next(m for m in data["models"] if m["id"] == mid)


def planted(**changes):
    """A live copy whose first Anthropic row is renamed to PLANTED and has no window, plus `changes` applied to it."""
    data = copy.deepcopy(LIVE)
    row = next(m for m in data["models"] if m["id"].startswith("claude-"))
    row.update(id=PLANTED, alias=PLANTED + "-alias", version=PLANTED, family=PLANTED)
    row.pop("servedIds", None)
    data["aliasHistory"] = {}
    row.pop("limits", None)
    row.pop("windowGap", None)
    row.update(changes)
    return data


def cli(*args, env=None):
    done = subprocess.run([sys.executable, str(TOOLS / "model_registry.py"), *args],
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
    return done.returncode, done.stdout.strip()


class ValidatorTests(unittest.TestCase):
    def test_callable_row_with_no_window_and_no_gap_fails(self):
        with self.assertRaises(mr.RegistryError) as ctx:
            mr._validate(planted(), "fixture")
        self.assertIn("windowGap", str(ctx.exception))

    def test_window_gap_passes(self):
        mr._validate(planted(windowGap=dict(GAP)), "fixture")

    def test_malformed_gap_fails(self):
        for broken in ({"reason": "x"}, "no catalog", {**GAP, "checked": " "}):
            with self.assertRaises(mr.RegistryError, msg=str(broken)):
                mr._validate(planted(windowGap=broken), "fixture")

    def test_gap_beside_a_window_fails(self):
        data = planted(windowGap=dict(GAP), limits={"contextTokens": 1000000, "maxOutputTokens": 128000})
        with self.assertRaises(mr.RegistryError):
            mr._validate(data, "fixture")

    def test_retired_unavailable_row_passes_bare(self):
        data = planted(lifecycle="retired", status={"state": "unavailable", "reason": "retired"})
        mr._validate(data, "fixture")


class RegistryTests(unittest.TestCase):
    def test_live_registry_validates(self):
        mr._validate(copy.deepcopy(LIVE), "live")

    def test_context_window_cli_reads_the_registry_it_is_pointed_at(self):
        data = planted(limits={"contextTokens": 500000, "maxContextTokens": 800000, "effectiveContextPercent": 90})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            env = {**os.environ, "HARNESS_MODEL_REGISTRY": str(path)}
            self.assertEqual(cli("context-window", PLANTED, env=env), (0, "450000"))
            self.assertEqual(cli("context-window", PLANTED, "--max", env=env), (0, "720000"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
