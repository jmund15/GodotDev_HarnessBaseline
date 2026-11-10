import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('lens_subject', ROOT / '.claude/tools/lens.py')
lens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lens)

# The key prefix each registry's headings carry, from the registries' one owner (adaptation.json `lens_prefixes`).
REVIEW = lens.REGISTRIES['review'][1]
EXPLORE = lens.REGISTRIES['explore'][1]


class LensCatalogContracts(unittest.TestCase):
    def test_index_does_not_promote_historical_catalog_pin_to_runtime_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = REVIEW + 'example'
            (root / 'review_agents.md').write_text(f'Shared.\n### {example} (Fixture scope) — `model: "FORBIDDENMODEL"`\nExact mandate.\n', encoding='utf-8')
            out = io.StringIO()
            with patch.object(lens, 'AGENTS', root), contextlib.redirect_stdout(out):
                lens.cmd_index(['review'])
            self.assertNotIn('FORBIDDENMODEL', out.getvalue())
            self.assertIn(example, out.getvalue())
            self.assertIn('Fixture scope', out.getvalue())

    def test_guard_advice_matches_selector_fields(self):
        guard_spec = importlib.util.spec_from_file_location('indexed_guard_subject', ROOT / '.claude/hooks/indexed_reference_guard.py')
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        result = guard.process({'tool_name':'Read', 'tool_input':{'file_path':str(ROOT / '.claude/commands/agents/plan_check_agents.md')}})
        self.assertIsNotNone(result)
        message = result['advisory']
        self.assertIn('key, gate and size', message)
        self.assertNotIn('model, gate', message)

    def test_index_preserves_code_and_mixed_plan_gates(self):
        self.assertEqual(lens.Lens('plc-example', '(Purpose) — code', 'plan_check').gate, 'code')
        self.assertEqual(lens.Lens('plc-example', '(Purpose) — code; CONDITIONAL', 'plan_check').gate, 'code,conditional')
        self.assertNotIn('omittable', [label for _, label in lens.GATE_MARKERS])
        self.assertEqual(lens.Lens('plc-example', '(Purpose) — meta or inseparable mixed code/meta', 'plan_check').gate, 'meta-or-mixed')
        self.assertEqual(lens.Lens('plc-example', '(Purpose) — every plan', 'plan_check').gate, 'always')

    def test_index_preserves_explore_trigger_gates(self):
        self.assertEqual(lens.Lens('exp-example', '(Purpose) — floor lens', 'explore').gate, 'floor')
        self.assertEqual(lens.Lens('exp-example', '(Purpose) — triggered', 'explore').gate, 'triggered')
        self.assertEqual(lens.Lens('exp-example', '(Purpose) — `--make-this-easy` mode only', 'explore').gate, 'mode-gated')

    def test_unknown_key_fails_before_emitting_partial_evidence(self):
        out, err = io.StringIO(), io.StringIO()
        missing = REVIEW + 'definitely-not-a-real-lens'
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stopped:
                lens.cmd_get([REVIEW + 'code-reviewer', missing], True)
        self.assertNotEqual(stopped.exception.code, 0)
        self.assertEqual(out.getvalue(), '')
        self.assertIn(missing, err.getvalue())

    def test_selected_bodies_and_shared_context_remain_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for registry, (filename, prefix) in lens.REGISTRIES.items():
                (root / filename).write_text(f'Shared {registry}.\n### {prefix}sample (Purpose) — floor lens\nExact {registry} mandate.\n', encoding='utf-8')
            out = io.StringIO()
            with patch.object(lens, 'AGENTS', root), contextlib.redirect_stdout(out):
                lens.cmd_get([REVIEW + 'sample', EXPLORE + 'sample'], True)
            text = out.getvalue()
            self.assertIn('Shared review.\n', text)
            self.assertIn('Shared explore.\n', text)
            self.assertIn('Exact review mandate.\n', text)
            self.assertIn('Exact explore mandate.\n', text)
            self.assertNotIn('Exact plan_check mandate.', text)


class LensMandateKeyContracts(unittest.TestCase):
    """PR-A01: every lens of the three review registries declares catalog keys, and `keys --check` judges them."""

    @staticmethod
    def ledger():
        sp = importlib.util.spec_from_file_location('review_ledger_subject', ROOT / '.claude/tools/review_ledger.py')
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        return mod

    def problems(self, lenses, catalog_keys=('cq.C', 'cq.D')):
        ledger = self.ledger()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = 'Shared.\n## Agent Templates\n'
            for key, keys in lenses:
                declared = f'mandates: {", ".join(keys)}\n' if keys else ''
                body += f'### {key} (Fixture scope)\n\n{declared}```\nprompt\n```\n'
            (root / 'review_agents.md').write_text(body, encoding='utf-8')
            checklist = root / 'checklists'
            checklist.mkdir()
            (checklist / 'code_quality.md').write_text('# CQ\n\n## Compliance (C)\n- c\n\n## Design (D)\n- d\n', encoding='utf-8')
            heading = {'cq.C': 'Compliance (C)', 'cq.D': 'Design (D)'}
            catalog = {k: {'home': f'checklists/code_quality.md#{heading[k]}', 'interaction': False}
                       for k in catalog_keys}
            with patch.object(lens, 'AGENTS', root), patch.object(lens, 'REGISTRIES', {'review': ('review_agents.md', REVIEW)}):
                sets = {'review': [(item.key, list(getattr(item, 'mandates', None) or [])) for item in lens.parse('review')[1]]}
            return ledger.check_keys(catalog, root, sets)

    def test_a_sound_registry_passes(self):
        self.assertEqual(self.problems([(REVIEW + 'a', ['cq.C']), (REVIEW + 'b', ['cq.D'])]), [])

    def test_a_lens_without_a_mandates_line_fails(self):
        found = self.problems([(REVIEW + 'a', ['cq.C']), (REVIEW + 'b', [])])
        self.assertTrue(any(REVIEW + 'b' in line and 'no mandates' in line for line in found), found)

    def test_two_lenses_of_one_registry_declaring_one_key_fail(self):
        found = self.problems([(REVIEW + 'a', ['cq.C']), (REVIEW + 'b', ['cq.C'])])
        self.assertTrue(any(REVIEW + 'a' in line and REVIEW + 'b' in line and 'cq.C' in line for line in found), found)

    def test_an_unknown_key_fails(self):
        found = self.problems([(REVIEW + 'a', ['cq.Z'])])
        self.assertTrue(any('cq.Z' in line for line in found), found)

    def test_the_live_admission_labels_match_execution_depth(self):
        """agents/session_audit_agents.md's `admission:` labels agree with execution_depth.md §Mandate admission."""
        ledger = self.ledger()
        self.assertEqual(ledger.check_admission(ledger._audit_lenses(), ledger._claude_dir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
