"""Structural doctrine checks; these do not establish behavioral equivalence."""
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _forked_locally(relpath):
    """True only when this checkout's own baseline.lock.json marks `relpath` a local fork.

    A published/shared proof file must not assume the checkout it runs in is {{PROJECT_NAME}}'s
    own repo: `baseline.lock.json` itself is never published (it is the publisher's own
    bookkeeping), so a template checkout has none, and a `forked` row's content (verdict
    "fork", never "push" -- baseline_publish.py `_collect_commit`) intentionally never syncs
    upstream. A test asserting a fork's exact prose belongs only to the checkout that owns
    that fork.
    """
    lock_path = ROOT / 'baseline.lock.json'
    if not lock_path.is_file():
        return False
    try:
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    entry = (lock.get('files') or {}).get('.claude/' + relpath)
    return bool(entry) and entry.get('status') == 'forked'


ORCHESTRATION_CONTRACTS = {
    'independent coverage': r'(?:Keep|Retain) required (?:coverage|lenses) and independent review',
    'evidence retention': r'Preserve (?:full evidence|original findings and provenance)',
    'source verification': r'verify decisive claims and original-source coverage',
    'conflict handling': r'Reconcile conflicting evidence directly',
    'grounded handoffs': r'Check inherited claims against source and local inputs',
    'known values retained': r'preserve known values even when an inference fails',
    'votes are not proof': r'not (?:a guarantee from vote count|vote counts)',
    'resume requirements': r'task state: requirements/exclusions, decisions, active jobs, accepted artifacts, verification',
    'resume freshness': r'After resume, recheck required inputs and decisions before trusting receipts',
    'actionable coordination': r'Message another session or agent only when it changes their next action',
    'no routine resumptions': r'A message may resume a finished agent; do not reopen a completed audit',
}


def missing_orchestration_contracts(text):
    return [name for name, pattern in ORCHESTRATION_CONTRACTS.items()
            if not re.search(pattern, text)]


class PolicyContracts(unittest.TestCase):
    def read(self, path):
        text = (ROOT / path).read_text(encoding='utf-8')
        # CLAUDE.md opens with `@CLAUDE.core.md`; the import composes one document at load time.
        imports = [line[1:].strip() for line in text.splitlines() if line.startswith('@')]
        return text + ''.join('\n' + ((ROOT / path).parent / name).read_text(encoding='utf-8')
                              for name in imports if ((ROOT / path).parent / name).is_file())

    def test_metrics_candidates_are_advisory_in_all_consumers(self):
        for path in ('skills/orchestration/SKILL.md', 'commands/orchestration_metrics.md', 'commands/eval_dashboard.md'):
            with self.subTest(path=path):
                text = self.read(path)
                self.assertNotIn('a listed shape runs one rung below default', text)
                self.assertNotIn('the action is a substituted downgrade', text)
                self.assertRegex(text.lower(), 'advisory|no automatic')
                self.assertRegex(text.lower(), 'quality')
                self.assertRegex(text.lower(), 'complete.task|parent')

    def test_read_only_integration_and_compaction_are_not_free(self):
        text = self.read('skills/orchestration/SKILL.md')
        self.assertNotIn('Read-only lanes integrate for free', text)
        self.assertNotIn('Compaction is survivable, not degrading', text)
        self.assertEqual(missing_orchestration_contracts(text), [])
        self.assertRegex(text.lower(), 'frozen')

    def test_handoff_contract_checks_detect_each_removed_obligation(self):
        text = self.read('skills/orchestration/SKILL.md')
        for name, pattern in ORCHESTRATION_CONTRACTS.items():
            with self.subTest(contract=name):
                mutant = re.sub(pattern, '', text)
                self.assertIn(name, missing_orchestration_contracts(mutant))

    def test_instruction_loading_has_no_free_context_claim(self):
        text = self.read('skills/instruction_quality/SKILL.md')
        self.assertNotIn('Conditionally-loaded files: size is free', text)
        self.assertNotIn('the count may not drop', text)
        self.assertRegex(text.lower(), 'trigger')
        self.assertRegex(text.lower(), 'coverage')

    def test_cache_claims_do_not_treat_disk_edits_as_proved_invalidation(self):
        for path in ('CLAUDE.md', 'commands/codify.md', 'commands/apply_harness_edits.md'):
            with self.subTest(path=path):
                text = self.read(path)
                self.assertNotIn('each `ToolSearch` rebuilds the prompt cache', text)
                self.assertNotIn('a mid-session edit invalidates the prompt cache at full-context cost', text)
                self.assertNotIn('editing them mid-session invalidates the prompt cache at full-context cost', text)
                self.assertRegex(text.lower(), 'loaded|reload|invalidate|cache')

    def test_overnight_matches_current_background_tool_contract(self):
        text = self.read('commands/overnight.md')
        self.assertNotIn('a backgrounded Bash task dies when the session idles', text)
        self.assertIn('run_in_background', text)
        self.assertRegex(text.lower(), 'fail|terminal')

    def test_self_evaluation_uses_the_bounded_ledger(self):
        command = self.read('commands/self_evaluate.md')
        self.assertIn('self_eval_archive_store.py --upsert', command)
        self.assertIn('self_eval_archive_store.py --lookup', command)
        self.assertIn('self_evaluate-selected-<session_id>.json', command)
        self.assertIn('Phase 3 receipt evidence', command)
        self.assertIn('Exit 2', command)
        self.assertNotIn('Read `self_evaluate_archive.json`', command)
        dashboard = self.read('commands/eval_dashboard.md')
        self.assertIn('frozen legacy snapshot', dashboard)
        self.assertIn('bounded JSONL ledger', dashboard)
        self.assertNotIn('| tail', dashboard)
        if _forked_locally('commands/merge_pr.md'):
            merge = self.read('commands/merge_pr.md')
            self.assertIn('self_evaluate_archive.jsonl', merge)
            self.assertIn('read-only legacy snapshot', merge)

    def test_closeout_keeps_full_phase_set_but_defers_loading(self):
        if not _forked_locally('commands/session_end.md'):
            self.skipTest('commands/session_end.md is not tracked as a local fork here; '
                           'this checkout has no {{PROJECT_NAME}}-specific phase set to police')
        text = self.read('commands/session_end.md')
        for phase in ('0', '1', '2', '3', '3.5', '3.6', '4', '5', '5.4', '5.5', '6', '7', '8'):
            self.assertIn('## Phase ' + phase + ':', text)
        self.assertIn('--stage precommit', text)
        self.assertIn('--stage final', text)
        self.assertRegex(text, 'Workflows or.*sidecar|Workflows or has attributable sidecar')
        self.assertNotIn('every user prompt verbatim, every friction row', text)
        self.assertIn('every user requirement and friction row', text)
        self.assertNotIn('For each plan file in `.claude/plans/`', text)
        self.assertIn('session_digest.py --prompt-tail session_end', text)
        self.assertIn('session_digest.py --list 20', text)
        self.assertNotIn('session_digest.py --session <exact-session-id>', text)


if __name__ == '__main__':
    unittest.main()
