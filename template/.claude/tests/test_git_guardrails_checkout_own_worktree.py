"""Floor proof: discards in a live session-created worktree, never a namesake or rider."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_call  # noqa: E402
import test_pattern_enforcer_own_worktree as own_proof  # noqa: E402
git = own_proof.git
from _must_allow_cases import must_allow_cases  # noqa: E402

HOOK = Path(__file__).resolve().parents[1] / 'hooks/git_guardrails.py'


class OwnDiscard(unittest.TestCase):
    setUp = own_proof.OwnWorktree.setUp
    tearDown = own_proof.OwnWorktree.tearDown
    created_row = own_proof.OwnWorktree.created_row
    result_row = own_proof.OwnWorktree.result_row

    def payload(self, command, rows=None, cwd=None):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in (
            rows if rows is not None else [self.created_row(), self.result_row()])), encoding='utf-8')
        return {'tool_name': 'Bash', 'cwd': str(cwd or self.root), 'session_id': 'own-discard',
                'transcript_path': str(transcript), 'tool_input': {'command': command}}

    @staticmethod
    def env():
        return {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

    def run_hook(self, command, rows=None, cwd=None):
        """The hook's verdict on the payload, `main()` in this process (`_hook_call`)."""
        code, out, err, _ = _hook_call.call(str(HOOK), self.payload(command, rows, cwd), env=self.env())
        self.assertIn(code, (0, 2), err)
        self.assertNotIn('Traceback', err)
        return code

    def test_spawned_hook_imports_and_runs_from_its_command(self):
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(self.payload('git status')),
                           capture_output=True, text=True, env=self.env(), cwd=str(self.root), timeout=90)
        self.assertEqual(0, p.returncode, p.stderr)

    def dirty(self, root):
        (root / '.gitignore').write_text('dirty edit\n', encoding='utf-8')

    def test_own_checkout_and_restore_allow(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        for verb in ('checkout --', 'restore'):
            self.assertEqual(0, self.run_hook(f'git -C "{wt.as_posix()}" {verb} .gitignore'))

    def test_bare_checkout_from_inside_own_worktree_allows(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        for command in ('git checkout -- .gitignore', 'git checkout HEAD -- .gitignore', 'git restore .gitignore'):
            self.assertEqual(0, self.run_hook(command, cwd=wt), command)
        self.assertEqual(0, self.run_hook(f'cd "{wt.as_posix()}" && git checkout -- .gitignore && git status --short'))
        self.dirty(self.root)
        self.assertEqual(2, self.run_hook('git checkout HEAD -- .gitignore'))

    def test_subdirectory_and_preceding_cd_allow(self):
        wt = self.root / '.claude/worktrees/mine'
        (wt / 'nested').mkdir()
        self.dirty(wt)
        self.assertEqual(0, self.run_hook(f'cd "{wt.as_posix()}/nested"; git restore ../.gitignore'))

    def test_once_assigned_literal_is_checked_at_its_real_path(self):
        command = 'P=.gitignore; git restore "$P"'
        self.assertEqual(0, self.run_hook(command))
        self.dirty(self.root)
        self.assertEqual(2, self.run_hook(command))

    def test_repeated_chdir_uses_each_previous_directory(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        self.dirty(self.root)
        peer = self.root.as_posix()
        self.assertEqual(2, self.run_hook(f'git -C "{peer}" -C . restore .gitignore', cwd=wt))
        self.assertEqual(0, self.run_hook(f'git -C "{peer}" -C .claude/worktrees/mine restore .gitignore', cwd=wt))
        self.assertEqual(0, self.run_hook(f'P="{peer}"; git -C "$P" -C "$PWD" restore .gitignore', cwd=wt))

    def add_submodule(self, parent):
        sub_src = Path(self.tmp.name) / 'subsrc'
        if not sub_src.exists():
            sub_src.mkdir()
            git(sub_src, 'init', '-q', '-b', 'main')
            (sub_src / 'lib.txt').write_text('lib\n', encoding='utf-8')
            git(sub_src, 'add', 'lib.txt')
            git(sub_src, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'init')
        git(parent, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', sub_src.as_posix(), 'Sub')
        sub = parent / 'Sub'
        (sub / 'lib.txt').write_text('dirty edit\n', encoding='utf-8')
        return sub

    def test_submodule_of_own_worktree_allows(self):
        sub = self.add_submodule(self.root / '.claude/worktrees/mine')
        self.assertEqual(0, self.run_hook(f'git -C "{sub.as_posix()}" checkout -- lib.txt'))
        self.assertEqual(0, self.run_hook('git -C Sub restore lib.txt', cwd=self.root / '.claude/worktrees/mine'))

    def test_submodule_of_shared_checkout_denies(self):
        sub = self.add_submodule(self.root)
        self.assertEqual(2, self.run_hook(f'git -C "{sub.as_posix()}" checkout -- lib.txt'))

    def tuple_loop_rows(self, command=None):
        # Cut from 60e59a75 toolu_01EE7KTWPeQFBdaMDgLBY38h (2026-10-16).
        command = command or (
            'cd "C:/work/App"; '
            'for spec in "wt1 feat/alpha" "wt2 feat/beta"; '
            'do set -- $spec; git worktree add .claude/worktrees/$1 -b $2 main 2>&1 | tail -1; '
            'git -C .claude/worktrees/$1 submodule update --init Lib 2>&1 | tail -2; done')
        command = command.replace('C:/work/App', self.root.as_posix())
        command = command.replace('wt1', 'mine').replace('wt2', 'mine2')
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = command
        result = self.result_row(
            'HEAD is now at cf1ee7e32 merge: fix/alpha — a new batch segment runs in a batch of its own\n'
            "Cloning into 'C:/work/App/.claude/worktrees/wt1/Lib'...\n"
            "Submodule path 'Lib': checked out 'd2b1b7acdcfb98e213fffffe023c2e4bb684598a'\n"
            'HEAD is now at cf1ee7e32 merge: fix/alpha — a new batch segment runs in a batch of its own\n'
            "Cloning into 'C:/work/App/.claude/worktrees/wt2/Lib'...\n"
            "Submodule path 'Lib': checked out 'd2b1b7acdcfb98e213fffffe023c2e4bb684598a'")
        return [row, result]

    def test_literal_tuple_loop_creation_allows_discard(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        rows = self.tuple_loop_rows()
        for verb in ('checkout --', 'restore'):
            self.assertEqual(0, self.run_hook(f'W=.claude/worktrees/mine; git -C $W {verb} .gitignore', rows))
        # Every iteration counts, including after the preceding submodule update.
        from _regenerable_clone import _unrolled_loops, _first_visible_add
        command = rows[0]['message']['content'][0]['input']['command']
        paths = _first_visible_add(_unrolled_loops(command) or '', str(self.root))
        self.assertEqual({wt, self.root / '.claude/worktrees/mine2'}, set(paths))
        self.assertEqual(2, self.run_hook('git branch -D peer', rows))

    def test_loop_with_echo_between_adds_counts_every_iteration(self):
        # In an unrolled loop an `&& echo` after an add still proves every iteration; in a straight chain only the first add is proven.
        from _regenerable_clone import _unrolled_loops, _first_visible_add
        wt = self.root / '.claude/worktrees'
        command = (f'cd "{self.root.as_posix()}"; for n in mine mine2; do '
                   'git worktree add -q -b fx-$n .claude/worktrees/$n HEAD && echo ok-$n; done; git worktree list')
        self.assertEqual({wt / 'mine', wt / 'mine2'},
                         set(_first_visible_add(_unrolled_loops(command), str(self.root))))
        for between in ('exit 1', 'cd ..', 'echo $X'):
            command = (f'git worktree add .claude/worktrees/mine HEAD; {between}; '
                       'git worktree add .claude/worktrees/mine2 HEAD')
            self.assertEqual([wt / 'mine'], _first_visible_add(command, str(self.root)), between)
        command = 'git worktree add .claude/worktrees/mine HEAD && echo ok && git worktree add .claude/worktrees/mine2 HEAD'
        self.assertEqual([wt / 'mine'], _first_visible_add(command, str(self.root)))

    def test_tuple_loop_ambiguous_creation_stays_denied(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        base = self.tuple_loop_rows()[0]['message']['content'][0]['input']['command']
        for command in (base.replace('set -- $spec', 'set -- $OTHER'),
                        base.replace('feat/alpha', 'feat/*'),
                        base.replace('set -- $spec;', 'set -- $spec; continue;'),
                        base.replace('set -- $spec;', 'set -- $spec; shift;'),
                        base.replace('$2 main', '$3 main'),
                        base.replace('for spec', 'IFS=/; for spec'),
                        base.replace('done', 'done || true')):
            with self.subTest(command=command):
                self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" restore .gitignore',
                                                 self.tuple_loop_rows(command)))
        rows = self.tuple_loop_rows()
        rows[1]['message']['content'][0]['content'] = 'fatal: add failed'
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" restore .gitignore', rows))

    def test_shared_checkout_denies(self):
        self.dirty(self.root)
        self.assertEqual(2, self.run_hook('git checkout -- .gitignore'))

    def test_reset_hard_of_clean_own_worktree_allows_in_every_form(self):
        # 2026-11-04: `git -C .claude/worktrees/m1 reset --hard origin/main` on a worktree this session created,
        # clean tree, was denied as a discard in the shared worktree.
        wt = self.root / '.claude/worktrees/mine'
        for command, cwd in ((f'git -C "{wt.as_posix()}" reset --hard HEAD', None),
                             ('git -C .claude/worktrees/mine reset --hard HEAD', None),
                             (f'cd "{wt.as_posix()}" && git reset --hard HEAD', None),
                             ('git reset --hard HEAD', wt)):
            with self.subTest(command=command):
                self.assertEqual(0, self.run_hook(command, cwd=cwd))

    def test_reset_hard_of_own_worktree_with_an_edit_denies(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" reset --hard HEAD'))
        self.assertEqual(2, self.run_hook('git reset --hard HEAD', cwd=wt))

    def test_reset_hard_in_shared_checkout_or_a_peer_worktree_denies(self):
        wt = self.root / '.claude/worktrees/mine'
        self.assertEqual(2, self.run_hook('git reset --hard'))
        self.assertEqual(2, self.run_hook(f'git -C "{self.root.as_posix()}" reset --hard HEAD', cwd=wt))
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" reset --hard HEAD', rows=[]))
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = (
            f'git worktree add "{Path(self.tmp.name).as_posix()}/other/mine" HEAD')
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" reset --hard HEAD', [row, self.result_row()]))

    def test_same_basename_peer_denies(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = (
            f'git worktree add "{Path(self.tmp.name).as_posix()}/other/mine" HEAD')
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" restore .gitignore', [row, self.result_row()]))

    def test_peer_recreation_denies(self):
        rows = [self.created_row(), self.result_row()]
        git(self.root, 'worktree', 'remove', '--force', '.claude/worktrees/mine')
        git(self.root, 'worktree', 'add', '-q', '-b', 'peer', '.claude/worktrees/mine', 'HEAD')
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" checkout -- .gitignore', rows))

    def test_missing_failed_or_timestamp_free_creation_denies(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        failed = self.result_row()
        failed['message']['content'][0]['is_error'] = True
        no_time = self.created_row()
        no_time.pop('timestamp')
        for rows in ([], [self.created_row()], [self.created_row(), failed], [no_time, self.result_row()]):
            self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" restore .gitignore', rows))

    def test_skipped_or_masked_add_cannot_claim_peer_creation(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        for command in ('false && {add}; sleep 2', '{add} || true',
                        'true || {add}', 'if false; then {add}; fi; true',
                        'false && W=.claude/worktrees/mine; git worktree add "$W" HEAD'):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                add='git worktree add .claude/worktrees/mine HEAD')
            with self.subTest(command=command):
                self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" restore .gitignore',
                                                 [row, self.result_row()]))

    def test_all_must_deny_riders(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        self.dirty(self.root)
        allow = f'git -C "{wt.as_posix()}" restore .gitignore'
        peer = f'git -C "{self.root.as_posix()}" restore .gitignore'
        riders = [f'echo "$({peer})"', f'echo "`{peer}`"', f'sudo {peer}',
                  f'env {peer}', f'printf .gitignore | xargs {peer}',
                  f'W="{wt.as_posix()}"; W="{self.root.as_posix()}"; git -C "$W" restore .gitignore',
                  'P=other; P=.gitignore; git restore "$P"']
        for rider in riders:
            with self.subTest(rider=rider):
                self.assertEqual(2, self.run_hook(allow + '; ' + rider))

    def test_unresolved_mutations_cannot_lend_ownership_to_aliases(self):
        from unittest.mock import patch
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        self.dirty(self.root)
        mutations = ['A=$PEER', 'A+=$PEER', 'unset A', 'read A']
        with patch.dict(os.environ, PEER=self.root.as_posix()):
            for mutation in mutations:
                command = f'A="{wt.as_posix()}"; {mutation}; B=$A; FINAL=$B; git -C "$FINAL" restore .gitignore'
                with self.subTest(mutation=mutation):
                    self.assertEqual(2, self.run_hook(command))

    def test_read_only_mentions_allow(self):
        commands = ['printf "%s" "git restore .gitignore"',
                    'sudo printf "%s" git restore .gitignore',
                    'printf input | xargs printf "%s" git restore .gitignore']
        for command in commands:
            self.assertEqual(0, self.run_hook(command))

    def test_shell_launcher_discards_cannot_borrow_ownership(self):
        import shlex
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(wt)
        body = f'git -C "{wt.as_posix()}" checkout -- .gitignore'
        launchers = ('bash -c', 'bash -lc', 'bash -o errexit -c', 'bash -O extglob -c',
                     'sh -c', 'eval', 'env X=1 bash -c', 'sudo bash -c')
        for launcher in launchers:
            with self.subTest(launcher=launcher):
                self.assertEqual(2, self.run_hook(launcher + ' ' + shlex.quote(body)))
        self.assertEqual(2, self.run_hook('bash -c ' + shlex.quote('sh -c ' + shlex.quote(body))))

    def test_all_floor_git_predicates_inside_launchers(self):
        import shlex
        for body in ('git reset --hard', 'git clean -fd', 'git push --force',
                     'git branch -D peer', 'git stash clear', 'git stash drop',
                     'git reflog expire --all', 'git gc --prune=now',
                     'git filter-branch', 'git filter-repo'):
            with self.subTest(body=body):
                self.assertEqual(2, self.run_hook('bash -c ' + shlex.quote(body)))

    def test_read_only_launcher_bodies_and_noexec_allow(self):
        import shlex
        body = 'git status --short; git show HEAD:.gitignore'
        for launcher in ('bash -c', 'bash -lc', 'bash -o errexit -c', 'bash -O extglob -c',
                         'sh -c', 'eval', 'env X=1 bash -c', 'sudo bash -c'):
            self.assertEqual(0, self.run_hook(launcher + ' ' + shlex.quote(body)))
        self.assertEqual(0, self.run_hook("bash -nc 'git checkout -- .'"))
        self.assertEqual(0, self.run_hook("sh -n -c 'git checkout -- .'"))
        self.assertEqual(0, self.run_hook("bash -c 'git clean -nd; git restore --staged file'"))
        self.assertEqual(0, self.run_hook("printf '%s' \"bash -c 'git checkout -- .'\""))

    def test_redirected_repository_does_not_inherit_ownership(self):
        wt = self.root / '.claude/worktrees/mine'
        self.dirty(self.root)
        self.assertEqual(2, self.run_hook(f'git -C "{wt.as_posix()}" --work-tree="{self.root.as_posix()}" restore .gitignore'))


def test_parser_import_fault_denies_nested_discard():
    script = (
        'import sys, runpy\n'
        'class Broken:\n'
        '    def find_spec(self, name, path=None, target=None):\n'
        "        if name == 'sidecar_argv':\n"
        "            raise ImportError('planted parser import fault')\n"
        'sys.meta_path.insert(0, Broken())\n'
        f'runpy.run_path({str(HOOK)!r}, run_name="__main__")\n')
    data = {'tool_name': 'Bash', 'tool_input': {'command': "bash -c 'git checkout -- .'"}}
    proc = subprocess.run([sys.executable, '-c', script], input=json.dumps(data),
                          capture_output=True, text=True, timeout=90)
    assert proc.returncode == 2 and 'parser' in proc.stderr, proc.stderr
    assert 'Traceback' not in proc.stderr


def test_shared_must_allow_cases():
    cases, cleanup = must_allow_cases('git_guardrails')
    try:
        for label, payload, cwd, _ in cases:
            p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                               text=True, timeout=90)
            assert p.returncode == 0, (label, p.stderr)
            if 'closing a merge' in label:
                payload['tool_input']['command'] = 'git merge --continue'
                p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                                   text=True, timeout=90)
                assert p.returncode == 0, p.stderr
    finally:
        cleanup()


PARTS = 3


def main(part=1):
    """Part `part` of PARTS: every case, split round-robin so the parts run beside each other. Each case calls the
    guard in process and still builds its fixture with git, so the cases only wait on git. A plain function case joins
    the list: a bare `unittest.main()` never ran them."""
    # A profile lane installs this part alone, so no part file at all is its shipped set; a tree that ships
    # the split and lost one part would silently drop its cases, so a partial set still fails.
    siblings = {n: Path(__file__).parent / ('test_git_guardrails_checkout_own_worktree_part%d.py' % n)
                for n in range(2, PARTS + 1)}
    present = {n for n, path in siblings.items() if path.is_file()}
    if present:
        missing = sorted(set(siblings) - present)
        assert not missing, 'missing part %s' % ', '.join(map(str, missing))
    cases = [OwnDiscard(name) for name in unittest.TestLoader().getTestCaseNames(OwnDiscard)]
    cases += [unittest.FunctionTestCase(fn) for fn in (test_parser_import_fault_denies_nested_discard, test_shared_must_allow_cases)]
    suite = unittest.TestSuite(cases[part - 1::PARTS] if present else cases)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
