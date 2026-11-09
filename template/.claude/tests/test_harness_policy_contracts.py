"""Structural doctrine checks; these do not establish behavioral equivalence."""
import json
import re
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lock_rows import project_owned  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _project_owned(relpath):
    """`_lock_rows.project_owned` for a path relative to `.claude/`, and only while the file exists here:
    a file a `local` row names is absent from a template checkout and has no row in its lock, and a
    proof of its prose belongs only to the checkout that owns it."""
    return (ROOT / relpath).is_file() and project_owned('.claude/' + relpath)


ORCHESTRATION_CONTRACTS = {
    'independent coverage': r'(?:Keep|Retain) required (?:coverage|lenses) and independent review',
    'evidence retention': r'Preserve (?:full evidence|original findings and provenance)',
    'source verification': r'verify (?:its )?decisive claims and original-source coverage',
    'conflict handling': r'Reconcile conflicting evidence directly',
    'grounded handoffs': r'Check inherited claims against source and local inputs',
    'known values retained': r'preserve known values even when an inference fails',
    'votes are not proof': r'not (?:a guarantee from vote count|vote counts)',
    'resume requirements': r'task state: requirements/exclusions, decisions, active jobs, accepted artifacts, verification',
    'resume freshness': r'After resume, recheck required inputs and decisions before trusting receipts',
    'actionable coordination': r'Message a peer session or subagent only while its prompt cache is warm, and only for what changes its next action',
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

    def orchestration_doctrine(self):
        return '\n'.join(self.read('skills/orchestration/' + part)
                         for part in ('SKILL.md', 'reference/checkpointing.md', 'reference/model_evidence.md',
                                      'reference/integration.md')) + '\n' + self.read('CLAUDE.md')

    def test_orchestration_doctrine_keeps_its_evidence_and_handoff_contracts(self):
        self.assertEqual(missing_orchestration_contracts(self.orchestration_doctrine()), [])

    def test_handoff_contract_checks_detect_each_removed_obligation(self):
        text = self.orchestration_doctrine()
        for name, pattern in ORCHESTRATION_CONTRACTS.items():
            with self.subTest(contract=name):
                mutant = re.sub(pattern, '', text)
                self.assertIn(name, missing_orchestration_contracts(mutant))

    def test_pr_sync_uses_identity_complete_state_and_gate_contracts(self):
        if not _project_owned('commands/pr_sync.md'):
            self.skipTest('commands/pr_sync.md is not project-owned here')
        command = self.read('commands/pr_sync.md')
        shared = self.read('commands/agents/pr_branch_sync.md')
        merge = self.read('commands/merge_pr.md')
        self.assertIn('gh repo view', command)
        self.assertIn('isCrossRepository', command)
        self.assertRegex(command, r'state.*OPEN|OPEN.*state')
        self.assertIn('STALE_WORKTREE_REGISTRATION', command)
        self.assertIn('pr_sync_<N>_gate.json', command)
        # regression_gate.ps1 gates the checkout it lives in ($PSScriptRoot), so -WorkingDirectory
        # cannot retarget it: the sync worktree's own copy must be the -File target.
        worktree_gate = '-File .claude/worktrees/sync-pr<N>/.claude/scripts/regression_gate.ps1'
        for text in (command, merge):
            self.assertIn(worktree_gate, text)
            self.assertNotIn('-File .claude/scripts/regression_gate.ps1', text)
        self.assertIn('-Detach', command)
        self.assertIn('Monitor', command.split('---', 2)[1])
        self.assertNotIn('godot_bin.sh --headless', command)
        self.assertIn('resource_conflicts.json', command)
        self.assertIn('commit-tree', shared)
        self.assertIn('commit --only Jmodot', shared)
        self.assertNotRegex(shared, r'(?m)^\s*dotnet build\s*$')
        self.assertNotIn('git stash --include-untracked', merge)
        self.assertIn('Monitor', merge.split('---', 2)[1])

    def test_closeout_has_terminal_metrics_prune_and_scope_contracts(self):
        overnight = self.read('commands/overnight.md')
        self.assertIn('--validate-close', overnight)
        self.assertIn('Resume: /session_digest', overnight)
        self.assertNotIn('push to main', overnight.split('## Step 2', 1)[1].split('## Step 3', 1)[0].lower())

    def test_self_evaluation_uses_the_bounded_ledger(self):
        command = self.read('commands/self_evaluate.md')
        self.assertIn('self_eval_archive_store.py --upsert', command)
        self.assertIn('self_eval_archive_store.py --lookup', command)
        self.assertIn('self_evaluate-selected-<session_id>.json', command)
        self.assertNotIn('Read `self_evaluate_archive.json`', command)
        dashboard = self.read('commands/eval_dashboard.md')
        self.assertNotIn('| tail', dashboard)
        if _project_owned('commands/merge_pr.md'):
            merge = self.read('commands/merge_pr.md')
            # The ledger conflict rules moved to their owner when /pr_sync took over branch
            # sync (84fcdf26b); this fork defers to that file rather than restating them.
            self.assertIn('agents/pr_branch_sync.md', merge)
            syncs = self.read('commands/agents/pr_branch_sync.md')
            self.assertIn('self_evaluate_archive.jsonl', syncs)


if __name__ == '__main__':
    unittest.main()
