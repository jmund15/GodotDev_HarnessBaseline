"""Harness close cadence for scripts/harness_tests.py.

Every decision the close makes runs in process on planted facts: a proof's verdict at the snapshot and at a
comparison revision is the read of the world, planted per case, and the close itself (selection, red
classification, bisect, supersession, writer wait, stamp) runs on them over a real local repository. The
real boundaries keep live cases: one whole close over a real private checkout with a real failing proof and
a real bisect, one whole green close, and the real push-topology, pass-store and duration-store reads.
"""
import contextlib
import io
import json
import os
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/scripts'))
sys.path.insert(0, str(ROOT / '.claude/tests'))
sys.path.insert(0, str(ROOT / '.claude/hooks'))
import _push_range as push_range
import harness_tests as runner
from _git_fixture import commit_all
from _hook_call import copy_repo
from _harness_repo_case import RepoCase

runner.sweep_stale_proof_temp = lambda *args, **kwargs: 0  # no case reads or prunes the machine's real TEMP

RED = "FAIL: unit regressed\n"
REAL_REDS_AT = runner._reds_at
REAL_TREE_ENTRIES = runner.tree_entries
REAL_NEVER_COMMITTED = runner._never_committed
REAL_REVISION_CHECKOUT = runner._revision_checkout


class Topology(RepoCase):
    """The push facts the close reads, decided by real local git: no close runs here."""

    def commit(self, message):
        return commit_all(self.repo, message)

    def test_unpublished_branch_uses_default_merge_base(self):
        self.plant('bad')
        base = self.commit('old red')
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.git('config', '--remove-section', 'branch.' + self.git('branch', '--show-current'))
        self.git('branch', '-m', 'unpublished')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad same failure'\n")
        self.commit('old red on unpublished branch')
        self.assertEqual(push_range.close_base(str(self.repo)), base)

    def test_close_base_is_the_push_target_not_the_upstream(self):
        """The upstream is a published commit of this branch, but the push goes to an unpublished target. The
        close must prove against the base `_push_range` names for that target, not the upstream."""
        self.plant()
        published = self.commit('published branch work')
        self.git('update-ref', 'refs/remotes/origin/feat', published)
        branch = self.git('branch', '--show-current')
        self.git('config', 'branch.' + branch + '.merge', 'refs/heads/feat')
        self.git('config', 'branch.' + branch + '.pushRemote', 'backup')
        self.git('update-ref', 'refs/remotes/backup/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'good later'\n")
        self.commit('harness')
        remote, target = push_range.push_destination(str(self.repo))
        self.assertEqual(push_range.close_base(str(self.repo), push_ref="refs/remotes/%s/%s" % (remote, target),
                                               remote=remote), self.base)

    def test_origin_head_takes_precedence_and_uses_merge_base(self):
        self.git('config', '--remove-section', 'branch.' + self.git('branch', '--show-current'))
        self.git('branch', '-m', 'unpublished')
        self.write('README.md', 'local branch\n')
        local = self.commit('local')
        tree = self.git('rev-parse', self.base + '^{tree}')
        remote = self.git('commit-tree', tree, '-p', self.base, '-m', 'remote default advance')
        self.git('update-ref', 'refs/remotes/origin/trunk', remote)
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/trunk')
        self.git('update-ref', 'refs/remotes/origin/main', local)
        self.assertEqual(push_range.close_base(str(self.repo)), self.base)

    def _synced_branch(self):
        """A branch with one own harness edit that merged an advanced default branch."""
        self.plant()
        base = self.commit('green harness')
        self.write('.claude/hooks/main_only.py', "VALUE = 'main'\n")
        main_tip = self.commit('default branch advances')
        self.git('reset', '-q', '--hard', base)
        self.git('update-ref', 'refs/remotes/origin/main', main_tip)
        self.write('.claude/hooks/branch_only.py', "VALUE = 'branch'\n")
        self.commit('branch work')
        return base, main_tip

    def test_sync_merge_paths_are_not_reproved(self):
        base, main_tip = self._synced_branch()
        self.git('merge', '-q', '--no-edit', main_tip)
        self.assertEqual(push_range.close_paths(str(self.repo), base, 'HEAD'), ['.claude/hooks/branch_only.py'])

    def test_merge_resolution_paths_are_proved(self):
        base, main_tip = self._synced_branch()
        self.git('merge', '-q', '--no-commit', main_tip)
        self.write('.claude/hooks/main_only.py', "VALUE = 'resolved'\n")
        self.git('add', '.claude/hooks/main_only.py')
        self.git('-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'merge with a resolution')
        self.assertEqual(push_range.close_paths(str(self.repo), base, 'HEAD'),
                         ['.claude/hooks/branch_only.py', '.claude/hooks/main_only.py'])

    def test_no_default_ref_keeps_every_merged_path(self):
        base, main_tip = self._synced_branch()
        self.git('merge', '-q', '--no-edit', main_tip)
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.assertEqual(push_range.close_paths(str(self.repo), base, 'HEAD'),
                         ['.claude/hooks/branch_only.py', '.claude/hooks/main_only.py'])

    def test_a_close_scopes_by_the_push_remote(self):
        """With a pushRemote apart from the branch remote, a sync's merged-in commits the push remote's default
        branch carries add no path."""
        self.plant()
        base = self.commit('green harness')
        self.write('.claude/hooks/main_only.py', "VALUE = 'main'\n")
        main_tip = self.commit('default branch advances')
        self.git('reset', '-q', '--hard', base)
        self.git('checkout', '-q', '-b', 'feature')
        for key, value in (('remote', 'origin'), ('pushRemote', 'backup'), ('merge', 'refs/heads/feature')):
            self.git('config', 'branch.feature.' + key, value)
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.git('update-ref', 'refs/remotes/backup/main', main_tip)
        self.git('update-ref', 'refs/remotes/backup/feature', base)
        self.write('.claude/hooks/unit.py', "VALUE = 'good branch'\n")
        self.commit('branch work')
        self.git('merge', '-q', '--no-edit', main_tip)
        self.assertEqual(push_range.close_paths(str(self.repo), base, 'HEAD', 'backup'),
                         ['.claude/hooks/unit.py'])

    def test_a_sync_merge_names_the_default_branch_commit_a_red_is_compared_at(self):
        base, main_tip = self._synced_branch()
        self.git('merge', '-q', '--no-edit', main_tip)
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(runner._default_merge_base(str(self.repo), head, base), main_tip)

    def test_every_checkout_of_a_repo_shares_one_pass_cache(self):
        self.git('worktree', 'add', '-q', '--detach', str(self.repo / '.claude/worktrees/twin'), 'HEAD')
        main = runner._shared_path(str(self.repo), 'proof_passes.json')
        linked = runner._shared_path(str(self.repo / '.claude/worktrees/twin'), 'proof_passes.json')
        self.assertEqual(os.path.normcase(main), os.path.normcase(linked))
        self.assertTrue(os.path.normcase(main).startswith(os.path.normcase(str(self.repo / '.git'))), main)
        with patch.object(runner, 'STAMP_PATH', str(self.repo / 'private' / 'stamp.json')):
            self.assertEqual(runner.passes_path(), str(self.repo / 'private' / 'harness_proof_passes.json'))

    def test_every_checkout_of_a_repo_shares_one_duration_record(self):
        """A recorded duration is what admits a failing proof to its alone retry. A checkout that only reused
        another checkout's pass recorded none, so its load flake read as red."""
        self.git('worktree', 'add', '-q', '--detach', str(self.repo / '.claude/worktrees/twin'), 'HEAD')
        seen = []
        for claude_dir in (self.repo / '.claude', self.repo / '.claude/worktrees/twin/.claude'):
            claude_dir.mkdir(parents=True, exist_ok=True)
            stamp = str(claude_dir / 'logs' / 'harness_tests_stamp.json')
            with patch.object(runner, '_CLAUDE_DIR', str(claude_dir)), patch.object(runner, '_DEFAULT_STAMP', stamp), \
                    patch.object(runner, 'STAMP_PATH', stamp), patch.object(runner, 'DURATIONS_PATH', None):
                seen.append(os.path.normcase(runner.durations_path()))
        self.assertEqual(seen[0], seen[1])
        self.assertTrue(seen[0].startswith(os.path.normcase(str(self.repo / '.git'))), seen[0])
        with patch.object(runner, 'STAMP_PATH', str(self.repo / 'private' / 'stamp.json')), \
                patch.object(runner, 'DURATIONS_PATH', None):
            self.assertEqual(runner.durations_path(), str(self.repo / 'private' / 'harness_proof_durations.json'))

    def test_stamp_write_preserves_close(self):
        with patch.object(runner, 'tree_entries', REAL_TREE_ENTRIES):
            self._stamp_preserved()

    def _stamp_preserved(self):
        close = {'head': self.base, 'base': self.base, 'verdict': 'green', 'red': []}
        self.stamp.write_text(json.dumps({'close': close}))
        runner._write_stamp(str(self.repo), 0, 0, [])
        self.assertEqual(json.loads(self.stamp.read_text()).get('close'), close)


class Fixture(RepoCase):
    """A close over planted facts: the proof runs and the private snapshot are facts per case; the
    selection, classification, bisect, stamp and writer wait are the close's own code over real local git."""

    def setUp(self):
        super().setUp()
        self.snapshot = None
        self.frozen = False
        self.verdicts = {}       # proof basename -> (status, detail) at the committed snapshot
        self.working = {}        # proof basename -> status in the shared checkout (the diagnostic rerun)
        self.at = {}             # revision -> {proof basename|"*": "red"|"pass"|"unavailable"}
        self.default_at = 'pass'  # the answer at a revision the case does not name
        self.reach_extra = {'.claude/hooks/unit.py'}  # files the planted proof's reads reach
        self.entries = {}        # the stamp's file map
        self.selected = ['.claude/tests/test_unit.py']  # the proofs the close's paths select
        self.passes_after = None  # callable(root) -> bool: the snapshot proof passes once its input is planted
        self.proof_rule = None    # callable(name, root) -> (status, detail): a proof judged by its checkout
        self.close_base_value = self.base
        self.close_paths_value = ['.claude/hooks/unit.py']
        self.destination = ('origin', 'main')
        self.main_base = None
        patch.object(runner, 'run_proof', self._proof).start()
        patch.object(runner, '_reds_at', self._reds).start()
        patch.object(runner, '_revision_checkout', self._snapshot_checkout).start()
        patch.object(push_range, 'close_base', lambda *a, **k: self.base).start()
        patch.object(push_range, 'close_paths', lambda *a, **k: list(self.close_paths_value)).start()
        patch.object(push_range, 'push_destination', lambda *a, **k: self.destination).start()
        patch.object(runner, '_default_merge_base', lambda *a, **k: self.main_base).start()
        patch.object(runner, '_PassCache', self._planted_cache()).start()
        # The remaining world reads a close makes are facts here too: the stamp's file map, whether a proof
        # is committed, whether the base is git's empty tree. A case that decides one of them restores it.
        patch.object(runner, 'tree_entries', lambda repo_root: dict(self.entries)).start()
        patch.object(runner, '_never_committed', lambda *a, **k: False).start()
        patch.object(runner, '_is_empty_base', lambda *a, **k: False).start()
        patch.object(runner, 'select_for',
                     lambda snapshot, paths: [os.path.join(str(snapshot), rel) for rel in self.selected]).start()

    def commit(self, message):
        return commit_all(self.repo, message)

    def _planted_cache(self):
        """The pass store's facts for a close: no recorded pass, and the proof's reach as the case plants it."""
        case = self

        class PlantedCache:
            def __init__(self, root, **kwargs):
                self.root = root
                self.by_base = {}

            def reach(self, proof):
                return {runner._norm(os.path.relpath(proof, self.root))} | set(case.reach_extra)

            def snapshot_git(self):
                pass
        return PlantedCache

    def freeze(self):
        """The committed checkout the close proves: a copy of the repository now, without edits written after."""
        if not self.frozen:
            self.snapshot = Path(self.tmp.name) / 'snapshot'
            copy_repo(self.repo, self.snapshot)
            self.frozen = True
        return self.snapshot

    def close(self):
        self.freeze()
        return super().close()

    def verdict(self, name):
        return self.verdicts.get(name, ('pass', ''))

    def _proof(self, path, timeout=None, root=None, **kwargs):
        name = os.path.basename(path)
        if self.proof_rule is not None:
            status, detail = self.proof_rule(path, str(root))
            return status, 0.01, detail
        status, detail = self.verdict(name)
        if root is not None and self.frozen and os.path.normcase(str(root)) == os.path.normcase(str(self.snapshot)):
            if self.passes_after and self.passes_after(str(root)):
                return 'pass', 0.01, ''
            return status, 0.01, detail
        if root is not None and os.path.normcase(str(root)) == os.path.normcase(str(self.repo)):
            return self.working.get(name, status), 0.01, detail
        return status, 0.01, detail

    def _reds(self, repo_root, rev, failing, answers=None):
        table = self.at.get(rev) or {}
        reds = {}
        for path in failing:
            answer = table.get(os.path.basename(path), table.get('*', self.default_at))
            if answers is not None:
                answers[path] = answer
            if answer == 'red':
                reds[path] = RED
        return reds

    @contextlib.contextmanager
    def _snapshot_checkout(self, repo_root, rev, proofs=()):
        self.freeze()
        yield str(self.snapshot)


class Bisect(Fixture):
    def test_a_new_red_is_bisected_to_commit(self):
        self.plant()
        self.base = self.commit('base harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('README.md', 'other\n')
        self.commit('unrelated')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad'\n")
        first_red = self.commit('break unit')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad later'\n")
        self.commit('later unit')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.at = {self.base: {'*': 'pass'}}
        self.default_at = 'red'
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertIn('FIRST RED test_unit.py at ' + first_red, out)
        self.assertIn('harness_tests: bisect starts: 1 new red, 2 candidate commits', out)
        self.assertIn('harness_tests: bisect test_unit.py at ' + first_red + ': red', out)
        self.assertLess(out.index('bisect starts:'), out.index('bisect test_unit.py'))
        self.assertLess(out.index('bisect test_unit.py'), out.index('FIRST RED'))
        close = json.loads(self.stamp.read_text())['close']
        self.assertEqual(close['head'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(close['base'], self.base)
        self.assertEqual(close['verdict'], 'red')
        self.assertEqual(close['red'][0]['commit'], first_red)

    def test_red_from_a_merged_side_names_the_side_commit(self):
        self.plant()
        self.base = self.commit('base harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        branch = self.git('branch', '--show-current')
        self.git('checkout', '-q', '-b', 'side')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad side'\n")
        side_red = self.commit('side breaks unit\n\nClaude-Session: https://claude.ai/code/session_SIDE')
        self.git('checkout', '-q', branch)
        self.write('README.md', 'first-parent work\n')
        first_parent = self.commit('first-parent work')
        self.git('merge', '-q', '--no-ff', '-m', 'Merge side', 'side')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.at = {self.base: {'*': 'pass'}, first_parent: {'*': 'pass'}}
        self.default_at = 'red'
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertIn('FIRST RED test_unit.py at ' + side_red, out)
        self.assertIn('Claude-Session: https://claude.ai/code/session_SIDE', out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['red'][0]['commit'], side_red)

    def test_reds_bisect_in_lockstep_one_checkout_per_step(self):
        """Many reds over one commit range bisect together: each step reruns every red at one commit, side by
        side, instead of one red's whole bisect after another."""
        probes = []

        def reds_at(_root, rev, proofs, answers=None):
            probes.append((rev, sorted(proofs)))
            if answers is not None:
                for proof in proofs:
                    answers[proof] = 'red' if rev in ('c3', 'c4') else 'pass'
            return {p: 'FAIL ' + p for p in proofs if rev in ('c3', 'c4')}
        with patch.object(runner, '_reds_at', reds_at):
            first = runner._first_reds(str(self.repo), {p: ('FAIL ' + p, ['c1', 'c2', 'c3', 'c4'])
                                                        for p in ('a.py', 'b.py', 'c.py')})
        self.assertEqual({p: (r.kind, r.commit) for p, r in first.items()},
                         {p: ('found', 'c3') for p in ('a.py', 'b.py', 'c.py')})
        self.assertEqual(probes, [('c2', ['a.py', 'b.py', 'c.py']), ('c3', ['a.py', 'b.py', 'c.py'])])

    def test_bisect_past_the_deadline_names_no_commit(self):
        """A skipped run is not a pass: a bisect that read one as 'not this red' blamed the last candidate."""
        probes = []
        with patch.dict(os.environ, {'HARNESS_DEADLINE': str(time.time() - 1)}), \
                patch.object(runner, '_reds_at', lambda *a: probes.append(a) or {}):
            first = runner._first_red(str(self.repo), self.base, 'HEAD', 'p.py', 'FAIL x', (),
                                      commits=['c1', 'c2', 'c3', 'c4'])
        self.assertEqual(getattr(first, 'kind', None), 'unknown', first)
        self.assertIsNone(getattr(first, 'commit', first), first)
        self.assertEqual(probes, [])

    def test_an_unavailable_historical_run_is_unknown_not_passed(self):
        """A comparison commit the run could not judge is no answer, never a pass: a bisect that read it as
        'not this red' blamed the last candidate commit."""
        def reds_at(_root, rev, proofs, answers=None):
            if rev == 'c2':
                return {}  # the run could not answer here: no pass, no red
            if answers is not None:
                for proof in proofs:
                    answers[proof] = 'red'
            return {p: 'FAIL ' + p for p in proofs}
        with patch.object(runner, '_reds_at', reds_at):
            found = runner._first_reds(str(self.repo), {'p.py': ('FAIL p.py', ['c1', 'c2', 'c3', 'c4'])})['p.py']
        self.assertEqual(getattr(found, 'kind', None), 'unknown', 'an unanswered commit named %r' % (found,))
        self.assertIsNone(getattr(found, 'commit', found), found)

    def test_a_red_green_red_history_reports_the_boundary_it_reproduced(self):
        """Binary search can end on an index it never tested. The answer names a commit the red was seen at,
        or none at all: never an untested index reported as the first failure."""
        history = {'c1': 'red', 'c2': 'pass', 'c3': 'pass', 'c4': 'pass'}

        def reds_at(_root, rev, proofs, answers=None):
            if answers is not None:
                for proof in proofs:
                    answers[proof] = history[rev]
            return {p: 'FAIL ' + p for p in proofs} if history[rev] == 'red' else {}
        with patch.object(runner, '_reds_at', reds_at):
            found = runner._first_reds(str(self.repo), {'p.py': ('FAIL p.py', ['c1', 'c2', 'c3', 'c4'])})['p.py']
        commit = getattr(found, 'commit', found)
        self.assertTrue(getattr(found, 'kind', None) == 'unknown' or history[commit] == 'red',
                        'named %r, which never reproduced the red' % (found,))

    def test_side_bisect_past_the_deadline_keeps_the_merge(self):
        def git(_root, args):
            if '--parents' in args:
                if args[-1] != 'M':
                    raise TypeError('rev-list of %r' % args[-1])
                return 'M P1 P2'
            return 's1\ns2\ns3'
        def reds(_root, rev, _proofs, answers=None):
            if answers is not None:
                for proof in _proofs:
                    answers[proof] = 'red' if rev == 'P2' else 'pass'
            return {'p.py': 'FAIL x'} if rev == 'P2' else {}
        with patch.dict(os.environ, {'HARNESS_DEADLINE': str(time.time() - 1)}), \
                patch.object(runner, '_git', git), patch.object(runner, '_reds_at', reds):
            self.assertEqual(runner._side_commit(str(self.repo), 'M', 'p.py', 'FAIL x', ()), 'M')


class Classification(Fixture):
    def test_base_red_is_pre_existing(self):
        self.plant('bad')
        self.base = self.commit('old red')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'bad same failure'\n")
        self.commit('still old red')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.at = {self.base: {'*': 'red'}}
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('pre-existing red at base', out)

    def test_red_inherited_from_default_branch_is_pre_existing(self):
        self.plant()
        published = self.commit('green harness')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad on main'\n")
        main_red = self.commit('main breaks unit')
        self.git('reset', '-q', '--hard', published)
        branch = self.git('branch', '--show-current')
        self.git('config', 'branch.' + branch + '.merge', 'refs/heads/feature')
        self.git('update-ref', 'refs/remotes/origin/feature', published)
        self.git('update-ref', 'refs/remotes/origin/main', main_red)
        test = self.repo / '.claude/tests/test_unit.py'
        self.write('.claude/tests/test_unit.py', test.read_text(encoding='utf-8') + '# branch touch\n')
        self.commit('branch work selects the proof main broke')
        self.git('merge', '-q', '--no-edit', main_red)
        self.base = published
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.at = {published: {'*': 'pass'}, main_red: {'*': 'red'}}
        self.main_base = main_red
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('pre-existing red on the default branch', out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'green')

    def test_dirty_green_does_not_hide_committed_red(self):
        self.plant()
        self.base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'bad committed'\n")
        self.commit('red HEAD')
        self.freeze()
        self.write('.claude/hooks/unit.py', "VALUE = 'good uncommitted'\n")
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.working = {'test_unit.py': 'pass'}
        self.passes_after = lambda root: 'good uncommitted' in (
            (Path(root) / '.claude/hooks/unit.py').read_text(encoding='utf-8'))
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertNotIn('FIRST RED', out)
        self.assertIn('NEEDS UNCOMMITTED test_unit.py', out)
        self.assertIn('.claude/hooks/unit.py', out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'red')

    def plant_fixture_reader(self, fixture_text):
        self.write('.gitignore', '.claude/fixtures/\n')
        self.write('.claude/hooks/unit.py', "VALUE = 'good'\n")
        self.write('.claude/tests/test_unit.py',
                   "from pathlib import Path\nimport sys\n"
                   "text = (Path(__file__).resolve().parents[1] / 'fixtures/data.json').read_text()\n"
                   "if 'bad' in text:\n    print('FAIL: fixture regressed')\n    sys.exit(1)\n")
        self.commit('proof reads an ignored fixture')
        self.write('.claude/hooks/unit.py', "VALUE = 'good again'\n")
        self.commit('touch unit')
        self.freeze()
        self.write('.claude/fixtures/data.json', fixture_text)

    @staticmethod
    def fixture_text(root):
        try:
            return (Path(root) / '.claude/fixtures/data.json').read_text(encoding='utf-8')
        except OSError:
            return ''

    def test_red_needing_an_ignored_input_is_named_not_bisected(self):
        self.plant_fixture_reader('{"ok": 1}\n')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.working = {'test_unit.py': 'pass'}
        self.passes_after = lambda root: "'bad'" not in self.fixture_text(root) and 'ok' in self.fixture_text(root)
        self.reach_extra = {'.claude/hooks/unit.py', '.claude/fixtures/data.json'}
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertNotIn('bisect', out)
        self.assertNotIn('FIRST RED', out)
        lines = [line for line in out.splitlines() if line.startswith('NEEDS UNCOMMITTED')]
        self.assertEqual(len(lines), 1, out)
        self.assertIn('test_unit.py', lines[0])
        self.assertIn('.claude/fixtures/data.json', lines[0])
        self.assertIn('commit', lines[0])
        self.assertIn('1 need uncommitted inputs', out)
        close = json.loads(self.stamp.read_text())['close']
        self.assertEqual(close['verdict'], 'red')
        self.assertEqual(close['red'][0]['uncommittedInputs'], ['.claude/fixtures/data.json'])

    def test_red_its_uncommitted_inputs_do_not_fix_names_no_input(self):
        # Passes only in the shared checkout's folder: the dirty fixture it reads is not the cause.
        self.write('.claude/fixtures/data.json', '{"ok": 1}\n')
        self.write('.claude/tests/test_unit.py',
                   "from pathlib import Path\nimport sys\n"
                   "fixture = Path(__file__).resolve().parents[1] / 'fixtures/data.json'\n"
                   "text = fixture.read_text() if fixture.exists() else ''\n"
                   "if Path(__file__).resolve().parents[2].name != %r:\n"
                   "    print('FAIL: checkout moved')\n    sys.exit(1)\n" % self.repo.resolve().name)
        self.commit('proof keyed to its checkout folder')
        self.freeze()
        self.write('.claude/fixtures/data.json', '{"ok": 2}\n')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.working = {'test_unit.py': 'pass'}
        self.reach_extra = {'.claude/hooks/unit.py', '.claude/fixtures/data.json'}
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertNotIn('NEEDS UNCOMMITTED', out)
        lines = [line for line in out.splitlines() if line.startswith('PASSES ONLY IN THE WORKING TREE')]
        self.assertEqual(len(lines), 1, out)
        self.assertIn('test_unit.py', lines[0])
        self.assertIn('unknown', lines[0])
        self.assertIn('checkout', lines[0])
        self.assertIn('; next: ', lines[0])
        self.assertIn('0 need uncommitted inputs', out)
        close = json.loads(self.stamp.read_text())['close']
        self.assertEqual(close['verdict'], 'red')
        self.assertNotIn('uncommittedInputs', close['red'][0])
        self.assertTrue(close['red'][0]['passesInWorkingTree'])
        self.assertTrue((self.repo / '.claude/fixtures/data.json').is_file())

    def test_red_that_also_fails_in_the_working_tree_is_still_bisected(self):
        self.plant_fixture_reader('{"bad": 1}\n')
        self.verdicts['test_unit.py'] = ('fail', RED)
        self.at = {self.base: {'*': 'pass'}}
        self.default_at = 'red'
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertIn('FIRST RED test_unit.py at ', out)
        first = [line for line in out.splitlines() if line.startswith('FIRST RED test_unit.py')]
        self.assertEqual(len(first), 1, out)
        self.assertIn('; next: ', first[0])
        self.assertNotIn('NEEDS UNCOMMITTED', out)

    def test_reds_rerun_in_the_working_tree_side_by_side(self):
        import threading
        live, peak, lock = [0], [0], threading.Lock()

        def launch(path, *a, **k):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.3)
            with lock:
                live[0] -= 1
            return ('fail', 0.3, 'FAIL')
        proofs = []
        for name in ('test_a.py', 'test_b.py', 'test_c.py'):
            path = self.repo / '.claude/tests' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('', encoding='utf-8')
            proofs.append(str(path))
        env = {'HARNESS_TEST_JOBS': '3', 'HARNESS_MACHINE_SLOTS': '3', 'HARNESS_SLOT_DIR': str(self.repo / 'slots')}
        real_make = runner._run_context

        def make(*args, **kwargs):
            ctx = real_make(*args, **kwargs)
            ctx.launch = lambda job: launch(job.proof, job.timeout, job.root)
            return ctx

        with patch.dict(os.environ, env), patch.object(runner, '_run_context', make):
            os.environ.pop('HARNESS_SLOT_BUDGET', None)
            status = runner._working_tree_status(str(self.repo), str(self.repo), proofs)
        self.assertEqual(sorted(status.values()), ['fail', 'fail', 'fail'])
        self.assertGreater(peak[0], 1)

    def test_untracked_red_is_compared_against_head_inputs(self):
        self.write('.claude/hooks/unit.py', "VALUE = 'bad'\n")
        self.write('.gitignore', '.claude/tests/\n')
        self.commit('base unit')
        self.plant('bad')
        proof = str(self.repo / '.claude/tests/test_unit.py')

        def judged_by_its_unit(proof_path, root):
            # The proof fails while the unit it reads says 'bad'; a checkout whose unit is good passes it.
            unit = Path(root) / '.claude/hooks/unit.py'
            if not unit.is_file():  # a working-tree run names the proof's own checkout
                unit = Path(proof_path).resolve().parents[2] / '.claude/hooks/unit.py'
            text = unit.read_text(encoding='utf-8') if unit.is_file() else ''
            return ('fail', RED) if 'bad' in text else ('pass', '')
        self.proof_rule = judged_by_its_unit
        with patch.object(runner, '_revision_checkout', REAL_REVISION_CHECKOUT), \
                patch.object(runner, '_never_committed', REAL_NEVER_COMMITTED):
            self.assertIn(proof, REAL_REDS_AT(str(self.repo), self.git('rev-parse', 'HEAD'), [proof]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = runner.run_scoped(str(self.repo), ['.claude/hooks/unit.py'])
        self.assertEqual(rc, 1, out.getvalue())
        self.assertIn('HEAD carries the change', out.getvalue())
        self.assertFalse(self.stamp.exists(), 'an ignored proof alone cannot excuse a committed red')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad uncommitted'\n")
        self.at = {self.git('rev-parse', 'HEAD'): {'*': 'red'}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = runner.run_scoped(str(self.repo), ['.claude/hooks/unit.py'])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn('pre-existing red', out.getvalue())
        self.assertEqual(json.loads(self.stamp.read_text())['files']['.claude/hooks/unit.py'],
                         REAL_TREE_ENTRIES(str(self.repo))['.claude/hooks/unit.py'])
        self.base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'bad still'\n")
        self.git('add', '.claude/hooks/unit.py')
        self.git('commit', '-qm', 'still red')
        with patch.object(runner, '_never_committed', REAL_NEVER_COMMITTED):
            rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('UNCOMMITTED PROOF RED test_unit.py', out)
        self.write('.claude/hooks/unit.py', "VALUE = 'good'\n")
        self.git('add', '.claude/hooks/unit.py')
        self.git('commit', '-qm', 'green unit')
        green = self.git('rev-parse', 'HEAD')
        self.at[green] = {'*': 'pass'}
        self.write('.claude/hooks/unit.py', "VALUE = 'bad'\n")
        with patch.object(runner, '_revision_checkout', REAL_REVISION_CHECKOUT):
            self.assertNotIn(proof, REAL_REDS_AT(str(self.repo), green, [proof]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_scoped(str(self.repo), ['.claude/hooks/unit.py']), 1)

    def test_scoped_doc_paths_are_proved(self):
        self.write('.claude/guide.md', 'authored doc\n')
        self.write('.claude/tests/test_doc.py', "DOC = 'guide.md'\n")
        self.verdicts['test_doc.py'] = ('fail', 'FAIL: doc pin\n')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_scoped(str(self.repo), ['.claude/guide.md']), 1)
        self.verdicts['test_doc.py'] = ('pass', '')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_scoped(str(self.repo), ['.claude/guide.md']), 0)


class CloseRun(Fixture):
    def test_close_states_its_proof_count_and_estimate_before_it_runs(self):
        self.plant()
        self.commit('harness')
        patch.dict(os.environ, {'HARNESS_TEST_JOBS': '1'}).start()
        durations = self.repo / 'durations.json'
        patch.object(runner, 'DURATIONS_PATH', str(durations)).start()
        durations.write_text(json.dumps({}))
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('close: 1 selected proof(s), ~0s at recorded times on 1 job(s), '
                      '1 with no recorded time (unknown)', out)
        self.assertLess(out.index('selected proof(s)'), out.index('close green'), out)
        durations.write_text(json.dumps({'test_unit.py': 40.0}))
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('close: 1 selected proof(s), ~40s at recorded times on 1 job(s), '
                      '0 with no recorded time (unknown)', out)

    def test_a_close_superseded_between_the_check_and_the_write_publishes_nothing(self):
        """A peer close can take the record after this close's last supersession check. The stamp write
        rechecks the claim in the same locked update, or the stale close publishes over the peer's."""
        writes, real = [], runner.update_json_locked
        targets = [runner] + ([runner._authority] if hasattr(runner, '_authority') else [])

        def writing(path, updater, **kwargs):
            if os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(os.path.abspath(runner.STAMP_PATH)):
                writes.append(str(path))
                if len(writes) == 3:  # the verdict write, after the last supersession check
                    real(runner.close_run_path(), lambda doc: doc.update(token='a newer close'), raising=True)
            return real(path, updater, **kwargs)

        for target in targets:
            patch.object(target, 'update_json_locked', writing).start()
        self.close_paths_value = []
        with patch.dict(os.environ, {'HARNESS_TEST_JOBS': '1'}):
            rc, out = self.close()
        self.assertEqual(rc, 2, out)
        self.assertIn('superseded', out)
        self.assertNotEqual(json.loads(self.stamp.read_text())['close'].get('verdict'), 'green')

    def test_push_hint_names_the_upstream_branch(self):
        self.plant()
        published = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/feature', published)
        self.write('README.md', 'more\n')
        head = self.commit('more')
        self.destination = ('origin', 'feature')
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn('git push origin %s:feature' % head, out)

    def test_dirty_worktree_is_not_certified(self):
        self.plant()
        self.base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.write('.claude/hooks/unit.py', "VALUE = 'good again'\n")
        self.commit('still green')
        self.freeze()
        self.write('.claude/hooks/unit.py', "VALUE = 'bad uncommitted'\n")
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'green')

    def test_no_upstream_harness_can_close(self):
        self.git('config', '--remove-section', 'branch.' + self.git('branch', '--show-current'))
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.plant()
        self.commit('first harness')
        rc, out = self.close()
        self.assertEqual(rc, 0, out)


class FollowProof(Fixture):
    def prepare(self):
        self.plant()
        self.commit('harness')

    def test_running_writer_delays_start_and_is_named_once(self):
        import watch_records
        self.prepare()
        writer = {'name': 'refold', 'labels': ['fix'], 'model': 'sonnet', 'elapsed_s': 12}
        polls = iter(([writer], [writer], []))
        events = []

        def writers(sid, repo=None, own_launch=None):
            self.assertEqual(sid, 'session-proof')
            self.assertEqual(os.path.realpath(repo), os.path.realpath(str(self.repo)))
            value = next(polls)
            events.append('wait' if value else 'ready')
            return value

        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'session-proof'}), \
                patch.object(watch_records, 'running_writers', side_effect=writers), \
                patch.object(runner.time, 'sleep'):
            rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count('close waits on refold'), 1, out)
        self.assertIn('fix', out)
        self.assertIn('sonnet', out)
        self.assertEqual(events[:3], ['wait', 'wait', 'ready'])

    def test_no_session_does_not_wait_and_stable_head_runs_once(self):
        import watch_records
        self.prepare()
        env = dict(os.environ)
        env.pop('CLAUDE_CODE_SESSION_ID', None)
        with patch.dict(os.environ, env, clear=True), \
                patch.object(watch_records, 'running_writers', return_value=[]) as writers:
            rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertNotIn('close waits on', out)
        self.assertNotIn('restarting close', out)
        self.assertTrue(not writers.called or writers.call_args.args == (None,))

    def test_head_move_never_restarts_and_stamps_the_proven_commit(self):
        # A peer commit mid-close used to skip every queued proof and restart at the new HEAD; on a
        # shared main that commits every few minutes the close never finished. The private checkout
        # keeps the started commit's results valid, so the close finishes there and names its push.
        self.prepare()
        self.selected = ['.claude/tests/test_unit.py', '.claude/tests/test_unit_other.py']
        self.write('.claude/tests/test_unit_other.py', '# a second proof bound to unit.py\n')
        proven = self.commit('second proof')
        calls = []

        def launch(path, *args, **kwargs):
            calls.append(Path(path).name)
            if len(calls) == 1:
                self.write('README.md', 'a peer commit lands mid-close\n')
                self.commit('move during proof')
            return ('pass', 0.01, '')
        with patch.dict(os.environ, {'HARNESS_TEST_JOBS': '1'}), \
                patch.object(runner, 'run_proof', launch):
            rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertNotIn('restarting', out)
        self.assertEqual(sorted(calls), ['test_unit.py', 'test_unit_other.py'], calls)
        close = json.loads(self.stamp.read_text())['close']
        self.assertEqual(close['head'], proven)
        self.assertEqual(close['verdict'], 'green')
        self.assertIn('git push origin %s:' % proven, out)

    def test_a_newer_close_supersedes_a_running_one(self):
        self.prepare()
        self.write('.claude/tests/test_other.py', '# unit.py\n')
        self.commit('second proof')
        calls = []

        def launch(path, *args, **kwargs):
            calls.append(Path(path).name)
            runner.update_json_locked(runner.close_run_path(), lambda doc: doc.update(token='a newer close'),
                                      raising=True)
            return ('pass', 0.01, '')
        with patch.dict(os.environ, {'HARNESS_TEST_JOBS': '1'}), patch.object(runner, 'run_proof', launch):
            rc, out = self.close()
        self.assertEqual(rc, 2, out)
        self.assertIn('superseded', out)
        self.assertEqual(len(calls), 1, calls)
        self.assertNotEqual(json.loads(self.stamp.read_text())['close'].get('verdict'), 'green')

    def test_a_newer_close_stops_one_still_waiting_on_writers(self):
        # A close parked behind this session's writers ran 70 min after a newer close had proved the
        # same head: it noticed the newer one only once its writers ended.
        import watch_records
        self.prepare()
        writer = {'name': 'author', 'labels': ['fix'], 'model': 'fixture-model', 'elapsed_s': 1}

        def supersede(_seconds):
            runner.update_json_locked(runner.close_run_path(), lambda doc: doc.update(token='a newer close'),
                                      raising=True)
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'session-proof'}), \
                patch.object(watch_records, 'running_writers', return_value=[writer]), \
                patch.object(runner.time, 'sleep', side_effect=supersede), \
                patch.object(runner, 'run_proof') as launch:
            rc, out = self.close()
        self.assertEqual(rc, 2, out)
        self.assertIn('superseded', out)
        self.assertFalse(launch.called)

    def test_now_skips_the_writer_wait_and_still_proves_head(self):
        # Mid-drive, a red sweep needs the close's base-red and FIRST RED classification while an
        # author job runs; waiting for the writers left the session to guess which reds were its own.
        import watch_records
        self.prepare()
        writer = {'name': 'author', 'labels': ['fix'], 'model': 'fixture-model', 'elapsed_s': 1}
        out = io.StringIO()
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'session-proof'}), \
                patch.object(watch_records, 'running_writers', return_value=[writer]), \
                patch.object(runner.time, 'sleep', side_effect=AssertionError('waited')), \
                patch.object(runner, 'run_proof') as launch, \
                contextlib.redirect_stdout(out):
            launch.return_value = ('pass', 0.01, '')
            rc = runner.run_close(str(self.repo), now=True)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertNotIn('close waits on', out.getvalue())
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['head'], self.git('rev-parse', 'HEAD'))

    def test_now_requires_close(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            runner.main(['--now'])
        self.assertIn('--now requires --close', err.getvalue())

    def test_writer_wait_respects_deadline(self):
        import watch_records
        self.prepare()
        writer = {'name': 'author', 'labels': ['fix'], 'model': 'sonnet', 'elapsed_s': 1}
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'session-proof',
                                   'HARNESS_DEADLINE': str(time.time() - 1)}), \
                patch.object(watch_records, 'running_writers', return_value=[writer]), \
                patch.object(runner, 'run_proof') as launch:
            rc, out = self.close()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('HARNESS_DEADLINE', out)
        self.assertFalse(launch.called)

    def test_close_passes_its_own_launch_so_a_delegate_never_waits_on_itself(self):
        import watch_records
        self.prepare()
        with patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': 'session-proof', 'SIDECAR_LAUNCH_ID': 'own-launch'}), \
                patch.object(watch_records, 'running_writers', return_value=[]) as writers:
            rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertEqual(writers.call_args.kwargs.get('own_launch'), 'own-launch', writers.call_args)


class Live(RepoCase):
    """The real boundaries: a private checkout built by git, a proof run as a child process, a real bisect."""

    def test_one_whole_close_proves_a_real_failing_proof_and_bisects_it(self):
        self.plant()
        base = self.commit('green harness')
        self.git('update-ref', 'refs/remotes/origin/main', base)
        self.write('README.md', 'other\n')
        self.commit('unrelated')
        self.write('.claude/hooks/unit.py', "VALUE = 'bad'\n")
        first_red = self.commit('break unit')
        rc, out = self.close()
        self.assertEqual(rc, 1, out)
        self.assertIn('FIRST RED test_unit.py at ' + first_red, out)
        close = json.loads(self.stamp.read_text())['close']
        self.assertEqual(close['verdict'], 'red')
        self.assertEqual(close['red'][0]['commit'], first_red)
        self.assertEqual(close['head'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(close['base'], base)
        self.assertEqual(close['paths'], ['.claude/hooks/unit.py'])

    def test_a_real_green_close_certifies_and_names_its_push(self):
        self.plant()
        self.commit('green harness')
        head = self.git('rev-parse', 'HEAD')
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(self.stamp.read_text())['close']['verdict'], 'green')
        self.assertIn('git push origin %s:' % head, out)


class DoctrineProof(unittest.TestCase):
    def test_doc_bindings_are_declared(self):
        data = json.loads((ROOT / '.claude/skills/project_subsystems/adaptation.json').read_text())
        if 'proof_bindings' not in data:
            self.skipTest("this checkout's adaptation.json is consumer-local and declares no proof_bindings, "
                          "so it binds no doc to a proof")
        required = {'test_claude_core_standalone.py', 'test_claudemd_citations.py',
                    'test_load_census_live_budgets.py'}
        for name in ('CLAUDE.md', 'CLAUDE.core.md', 'CLAUDE.coding.md', 'CLAUDE.godot.md'):
            actual = set(data['proof_bindings'].get('.claude/' + name, []))
            want = required | ({'test_verify_findings.py'} if name == 'CLAUDE.md' else set())
            self.assertTrue(want <= actual, '%s lacks %s' % (name, sorted(want - actual)))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_sharded
    sys.exit(run_sharded(__file__))
