#!/usr/bin/env python3
"""S36/S38: schema refusal precedes baseline write/publication effects."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import baseline_sync as sync
import baseline_publish as publish


class SchemaBeforeEffects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="conformance_j7_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".claude").mkdir()
        self.write_lock(1)

    def write_lock(self, schema, files=None):
        lock = {"schema": schema, "layers": ["pure"], "files": files or {}}
        (self.root / sync.LOCK_RELPATH).write_text(json.dumps(lock), encoding="utf-8")
        return sync.load_lock(self.root)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_cli_write_routes_refuse_before_source_or_mutex(self):
        routes = [
            ["pull"], ["update-lock"], ["classify", ".claude/x.md", "--status", "local"],
            ["judge", ".claude/x.md", "--verdict", "keep-local"],
            ["forget", ".claude/x.md"], ["gc", "--apply"],
            ["fork", ".claude/x.md"], ["track", ".claude/x.md"],
            ["ignore", ".claude/x.md"], ["sub", "--sub", "{{X}}=value"],
            ["compose"],
            ["init", "--baseline-dir", "unused", "--repo", "unused", "--sub", "{{X}}=value", "--force"],
        ]
        for argv in routes:
            with self.subTest(route=argv[0]), patch.object(sync, "project_root", return_value=self.root), \
                    patch.object(sync, "check_adaptation_contract"), \
                    patch.object(sync, "ensure_baseline") as source, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                before = self.snapshot()
                self.assertEqual(sync.main(argv), 1)
                self.assertIn("v1 lock is read-only", err.getvalue())
                source.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_mutation_refuses_without_creating_mutex(self):
        before = self.snapshot()
        with self.assertRaisesRegex(sync.BaselineError, "v1 lock is read-only"):
            sync.mutate_lock(self.root, lambda lock: self.fail("mutator entered"))
        self.assertEqual(self.snapshot(), before)

    def compose_fixture(self, schema):
        for name, value in [("settings.base.json", {"env": {"BASE": "1"}}),
                            ("settings.project.json", {"env": {"PROJECT": "2"}}),
                            ("settings.json", {"env": {"OLD": "3"}})]:
            (self.root / ".claude" / name).write_text(json.dumps(value), encoding="utf-8")
        return self.write_lock(schema, {".claude/settings.json": {
            "status": "composed", "inputs": [".claude/settings.base.json", ".claude/settings.project.json"],
            "hash": None}})

    def test_compose_refuses_before_output_write(self):
        lock = self.compose_fixture(1)
        before = self.snapshot()
        with patch.object(sync, "check_adaptation_contract"), \
                self.assertRaisesRegex(sync.BaselineError, "v1 lock is read-only"):
            sync.v2_compose(self.root, lock, ["pure"], False)
        self.assertEqual(self.snapshot(), before)

    def test_v2_compose_writes_output_and_hash(self):
        lock = self.compose_fixture(2)
        with patch.object(sync, "check_adaptation_contract"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.v2_compose(self.root, lock, ["pure"], False), 0)
        output = self.root / ".claude/settings.json"
        self.assertEqual(json.loads(output.read_text())["env"], {"BASE": "1", "PROJECT": "2"})
        self.assertEqual(sync.load_lock(self.root)["files"][".claude/settings.json"]["hash"], sync.sha(output.read_bytes()))

    def test_compose_check_remains_read_only_on_v1(self):
        lock = self.compose_fixture(1)
        before = self.snapshot()
        with patch.object(sync, "check_adaptation_contract"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.v2_compose(self.root, lock, ["pure"], True), 1)
        self.assertEqual(self.snapshot(), before)

    def test_fresh_publish_refuses_before_pin_or_journal(self):
        for kind in ("commit", "worktree"):
            for dry in (False, True):
                with self.subTest(kind=kind, dry=dry), patch.object(sync, "check_adaptation_contract"), \
                        patch.object(publish, "_resolve_source", side_effect=lambda root, source: source), \
                        patch.object(publish, "_pin", side_effect=RuntimeError("pin effect reached")) as pin:
                    before = self.snapshot()
                    with self.assertRaisesRegex(publish.PublishError, "v1 lock is read-only"):
                        publish.run(self.root, {"kind": kind, "repo": str(self.root), "commit": "a" * 40},
                                    None, [], dry, False, None)
                    pin.assert_not_called()
                    self.assertEqual(self.snapshot(), before)

    def test_resume_refuses_before_full_battery_journal_rewrite(self):
        journal = publish._new_journal({"kind": "commit", "repo": str(self.root), "commit": "a" * 40},
                                       [], {}, True, False, False, "b" * 40, "fixture", None)
        path = publish._journal_path(self.root, "fixture")
        publish._write_json_atomic(path, journal)
        before = self.snapshot()
        with patch.object(sync, "check_adaptation_contract"), \
                patch.object(publish, "_git_text", return_value="a" * 40), \
                patch.object(publish, "_pin", side_effect=RuntimeError("pin effect reached")), \
                self.assertRaisesRegex(publish.PublishError, "v1 lock is read-only"):
            publish.run(self.root, None, None, [], True, False, "fixture", True)
        self.assertEqual(self.snapshot(), before)

    def test_author_start_refuses_before_cache_or_worktree(self):
        before = self.snapshot()
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "conformance-j7"}), \
                patch.object(sync, "ensure_baseline", side_effect=RuntimeError("cache effect reached")), \
                self.assertRaisesRegex(sync.BaselineError, "v1 lock is read-only"):
            sync.cmd_author_start(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_v2_publish_reaches_transaction_seam(self):
        self.write_lock(2)
        with patch.object(sync, "check_adaptation_contract"), \
                patch.object(publish, "_resolve_source", side_effect=lambda root, source: source), \
                patch.object(publish, "_pin", side_effect=RuntimeError("v2 admitted")):
            with self.assertRaisesRegex(RuntimeError, "v2 admitted"):
                publish.run(self.root, {"kind": "commit", "repo": str(self.root), "commit": "a" * 40},
                            None, [], False, False, None)

    def test_migration_exemption_still_allows_conversion(self):
        def migrate(lock):
            lock["schema"] = 2
        sync.mutate_lock(self.root, migrate, allow_v1=True)
        self.assertEqual(sync.load_lock(self.root)["schema"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
