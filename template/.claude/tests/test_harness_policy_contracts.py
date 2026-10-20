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
    """`_lock_rows.project_owned` for a path relative to `.claude/`."""
    return project_owned('.claude/' + relpath)


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
    'actionable coordination': r'Message a dispatched agent only when it changes its next action',
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
                         for part in ('SKILL.md', 'reference/checkpointing.md', 'reference/model_evidence.md'))

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
        text = self.orchestration_doctrine()
        self.assertNotIn('Read-only lanes integrate for free', text)
        self.assertNotIn('Compaction is survivable, not degrading', text)
        self.assertEqual(missing_orchestration_contracts(text), [])
        self.assertRegex(text.lower(), 'frozen')

    def test_handoff_contract_checks_detect_each_removed_obligation(self):
        text = self.orchestration_doctrine()
        for name, pattern in ORCHESTRATION_CONTRACTS.items():
            with self.subTest(contract=name):
                mutant = re.sub(pattern, '', text)
                self.assertIn(name, missing_orchestration_contracts(mutant))

    def test_instruction_loading_has_no_free_context_claim(self):
        text = '\n'.join(self.read('skills/instruction_quality/' + part) for part in (
            'SKILL.md', 'reference/skills_and_commands.md', 'reference/orchestration.md',
            'reference/hooks.md', 'reference/claims_and_wording.md')
            if (ROOT / 'skills/instruction_quality' / part).is_file())
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
        audit = self.read('commands/session_audit.md')
        self.assertIn('--validate-close', overnight)
        self.assertIn('Resume: /session_digest', overnight)
        self.assertNotIn('push to main', overnight.split('## Step 2', 1)[1].split('## Step 3', 1)[0].lower())
        self.assertIn('Batch the complete audit when full context exceeds the bound', audit)

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
        if _project_owned('commands/merge_pr.md'):
            merge = self.read('commands/merge_pr.md')
            # The ledger conflict rules moved to their owner when /pr_sync took over branch
            # sync (84fcdf26b); this fork defers to that file rather than restating them.
            self.assertIn('agents/pr_branch_sync.md', merge)
            syncs = self.read('commands/agents/pr_branch_sync.md')
            self.assertIn('self_evaluate_archive.jsonl', syncs)
            self.assertIn('read-only legacy snapshot', syncs)


if __name__ == '__main__':
    unittest.main()
