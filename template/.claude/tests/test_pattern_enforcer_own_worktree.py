#!/usr/bin/env python3
"""Proof: a worktree this session created goes without an owner pick, ignored files included.

The creating `git worktree add` must be a Bash call in the session transcript (the row is cut from a
real transcript); the same command in prose, or no creation at all, is denied as not retired. The guard
reads no reply, so a transcript answer grants nothing.

    python3 .claude/tests/test_pattern_enforcer_own_worktree.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hooks"))
import dangerous_shell_guard as pe  # noqa: E402

ROW = json.loads((HERE / "fixtures/transcript_worktree_add_row.json").read_text(encoding="utf-8"))


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


class OwnWorktree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="own_worktree_")
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        (self.root / ".gitignore").write_text(".claude/scratch/\n", encoding="utf-8")
        git(self.root, "add", ".gitignore")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        remote = Path(self.tmp.name) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
        git(self.root, "remote", "add", "origin", str(remote))
        git(self.root, "push", "-q", "-u", "origin", "main")
        self.started = datetime.now(timezone.utc).isoformat()
        git(self.root, "worktree", "add", "-q", "-b", "scratch/mine", ".claude/worktrees/mine", "HEAD")
        self.finished = datetime.now(timezone.utc).isoformat()
        evidence = self.root / ".claude/worktrees/mine/.claude/scratch/evidence.txt"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("a job's own scratch\n", encoding="utf-8")

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.root), "worktree", "remove", "--force", ".claude/worktrees/mine"],
                       capture_output=True)
        self.tmp.cleanup()

    def verdict(self, rows, command="rm -rf .claude/worktrees/mine"):
        transcript = Path(self.tmp.name) / "transcript.jsonl"
        transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        # The planted repo sits in the system temp dir, which the guard admits outright; move that root away.
        elsewhere = str(Path(self.tmp.name).parent / "elsewhere")
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)), \
                patch("tempfile.gettempdir", return_value=elsewhere):
            return pe.check_bash_command(command, cwd=str(self.root),
                                         transcript_path=str(transcript))

    def blocked(self, rows):
        return self.verdict(rows)[0]

    def created_row(self):
        row = json.loads(json.dumps(ROW))
        row['cwd'] = str(self.root)
        row['timestamp'] = self.started
        for block in row["message"]["content"]:
            block["input"]["command"] = block["input"]["command"].replace("cut-runner", "mine").replace(
                'C:/work/project', self.root.as_posix())
        return row

    def result_row(self, output='c85bc63ba fix(overnight): the no-regret audit call carries the drive\'s task and plan flags',
                   is_error=False):
        # Shape cut from the same live call as ROW (2026-10-07T14:37:11.753Z); a failed call stores its error
        # text as toolUseResult (session ffd4976e, 2026-10-09).
        return {'type': 'user', 'timestamp': self.finished, 'cwd': str(self.root),
                'message': {'role': 'user', 'content': [{'type': 'tool_result',
                    'tool_use_id': ROW['message']['content'][0]['id'], 'is_error': is_error,
                    'content': output}]},
                'toolUseResult': 'Error: ' + output if is_error else {'stdout': '', 'stderr': '', 'interrupted': False}}

    def test_created_by_this_session_is_removed_without_a_pick(self):
        self.assertFalse(self.blocked([self.created_row(), self.result_row()]))

    def test_chained_delete_of_a_removable_worktree_passes(self):
        # Live 2026-10-12: `cd <root> && rm -rf <approved worktrees>; git worktree prune` was denied with no
        # reason: every target was removable, and only the chaining failed the lone-delete check.
        for command in ('cd "%s" && rm -rf .claude/worktrees/mine' % self.root.as_posix(),
                        'rm -rf .claude/worktrees/mine; git worktree prune'):
            self.assertFalse(self.verdict([self.created_row(), self.result_row()], command)[0], command)
        self.assertTrue(self.verdict([self.created_row(), self.result_row()],
                                     'cd "%s" && rm -rf .claude/worktrees/mine peer' % self.root.as_posix())[0])

    def test_owned_unpushed_worktree_names_failed_retirement(self):
        wt = self.root / '.claude/worktrees/mine'
        (wt / 'local.txt').write_text('local only', encoding='utf-8')
        git(wt, 'add', 'local.txt')
        git(wt, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-qm', 'local')
        head = git(wt, 'rev-parse', '--short', 'HEAD').strip()
        blocked, message = self.verdict([self.created_row(), self.result_row()])
        self.assertTrue(blocked)
        self.assertIn('not retired: HEAD ' + head + ' is on no remote branch', message)

    def test_unowned_worktree_names_the_retirement_failure(self):
        # The guard reads no reply, so it names why the worktree is not removable (not retired).
        blocked, message = self.verdict([])
        self.assertTrue(blocked)
        self.assertIn('is not retired', message)
        self.assertIn('worktree has local files or changes', message)
        self.assertNotIn('AskUserQuestion', message)

    def test_skipped_or_masked_add_cannot_claim_peer_creation(self):
        for command in ('false && {add}; sleep 2', '{add} || true',
                        'true || {add}', 'if false; then {add}; fi; true',
                        'false && W=.claude/worktrees/mine; git worktree add "$W" HEAD'):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                add='git worktree add .claude/worktrees/mine HEAD')
            with self.subTest(command=command):
                self.assertTrue(self.blocked([row, self.result_row()]))

    def test_first_add_with_visible_errors_owns_whatever_follows(self):
        # 2026-10-08, session b48600e4: nine of its ten worktrees were made this way and could not be removed.
        # A failed add always prints `fatal:` to the call's output, so a clean output proves it ran and succeeded.
        for command, output in (('{add} 2>&1 | tail -1; git -C .claude/worktrees/mine log --oneline -1',
                                 'HEAD is now at 0d317e543 init\n0d317e543 init'),
                                ('git worktree add -q -b x .claude/worktrees/mine HEAD 2>&1 | tail -2; '
                                 'H=$(git rev-parse --short HEAD); echo "$H"', '0d317e5'),
                                ('{add}; true', ''),
                                ('cd {root}; {add} 2>&1 | tail -2; true', ''),
                                ('cd {root}; H=$(git rev-parse --short HEAD); git grep -n x; {add} 2>&1 | tail -1', '')):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                add='git worktree add .claude/worktrees/mine HEAD', root=self.root.as_posix())
            with self.subTest(command=command):
                self.assertFalse(self.blocked([row, self.result_row(output)]))

    def test_failed_or_hidden_add_output_is_not_ours(self):
        for command, output in (('{add} 2>&1 | tail -1; true', "fatal: '.claude/worktrees/mine' already exists"),
                                ('{add}; true', "error: could not lock config file"),
                                ('{add} 2>/dev/null; true', ''),
                                ('{add} 2>&1 | grep -v fatal; true', ''),
                                ('{add} 2>&1 | head -1; true', ''),
                                ('cd elsewhere; {add} 2>&1 | tail -1; true', ''),
                                ('git grep -q x && {add} 2>&1 | tail -1', ''),
                                ('set -e; {add} 2>&1 | tail -1', ''),
                                ('exit 0; {add}', '')):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                add='git worktree add .claude/worktrees/mine HEAD')
            with self.subTest(command=command):
                self.assertTrue(self.blocked([row, self.result_row(output)]))

    def test_each_leading_add_with_visible_errors_counts(self):
        # 2026-10-09, session ffd4976e: `add A && add B; list` made B, and B could not be removed.
        # An add that directly follows a visible add runs after it, and either failure prints `fatal:`.
        other = 'git worktree add -q -b other .claude/worktrees/other HEAD'
        for command in ('{other} && {add}; git worktree list',
                        '{other}; {add}',
                        'cd {root} && {other} && {add} 2>&1 | tail -1'):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                other=other, add='git worktree add .claude/worktrees/mine HEAD', root=self.root.as_posix())
            with self.subTest(command=command):
                self.assertFalse(self.blocked([row, self.result_row('')]))
        for command, output in (('{other} && {add}; git worktree list', "fatal: '.claude/worktrees/other' already exists"),
                                ('{other} && {add} 2>/dev/null; true', ''),
                                ('{other} && {add} || true', '')):
            row = self.created_row()
            row['message']['content'][0]['input']['command'] = command.format(
                other=other, add='git worktree add .claude/worktrees/mine HEAD')
            with self.subTest(command=command):
                self.assertTrue(self.blocked([row, self.result_row(output)]))

    def test_visible_add_survives_a_later_commands_exit_status(self):
        # 2026-10-09, session ffd4976e: a script after `;` exited 1, so the call's result was an error,
        # though the add before it had succeeded. Error texts cut from that session's transcript.
        add = 'git worktree add -q -b x .claude/worktrees/mine main 2>&1 | tail -2; python3 - <<\'EOF\'\nraise SystemExit(1)\nEOF'
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = add
        self.assertFalse(self.blocked([row, self.result_row(
            'Exit code 1\nTraceback (most recent call last):\r\n  File "<stdin>", line 7, in <module>\r\n'
            "FileNotFoundError: [Errno 2] No such file or directory: '.claude/worktree", is_error=True)]))
        for output in ("Exit code 128\nfatal: '.claude/worktrees/mine' already exists",
                       'PreToolUse:Bash hook error: [python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/pre_bash_dispatch.py"]: '
                       'BLOCKED commit (CLAUDE.core.md §Shell Discipline: an index write runs in its own Bash call)',
                       'Permission for this action was denied by the Claude Code auto mode classifier. '
                       'Reason: [Interfere With Workloads].'):
            with self.subTest(output=output[:30]):
                self.assertTrue(self.blocked([row, self.result_row(output, is_error=True)]))

    def loop_row(self, command, shift=0.0):
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = command
        if shift:
            row['timestamp'] = datetime.fromtimestamp(
                datetime.fromisoformat(row['timestamp']).timestamp() + shift, timezone.utc).isoformat()
        return row

    def test_literal_loop_adds_prove_every_listed_worktree(self):
        # Session 69255593: 19 worktrees made by `for j in ...; do git worktree add ... $j; done` were refused removal
        # because their paths never occur literally in the call.
        git(self.root, 'worktree', 'add', '-q', '-b', 'scratch/mine2', '.claude/worktrees/mine2', 'HEAD')
        self.finished = datetime.now(timezone.utc).isoformat()
        both = 'rm -rf .claude/worktrees/mine .claude/worktrees/mine2'
        loops = ('for j in mine mine2; do git worktree add -q -b claude/finish-$j .claude/worktrees/$j 88bb2dea0 2>&1 | tail -1; done; '
                 'git worktree list | grep -c finish-',
                 'for k in mine mine2; do git worktree add -q -b claude/finish-$k .claude/worktrees/$k main; done; git worktree list',
                 'for k in mine mine2\ndo\n  git worktree add -q -b x-${k} ".claude/worktrees/${k}" HEAD\ndone',
                 'cd "%s" && for k in mine mine2; do git worktree add -q -b x-$k .claude/worktrees/$k HEAD; done' % self.root.as_posix())
        for command in loops:
            with self.subTest(command=command):
                self.assertFalse(self.verdict([self.loop_row(command), self.result_row('')], both)[0])
        self.assertTrue(self.verdict([self.loop_row('for j in mine2; do git worktree add -q -b x-$j .claude/worktrees/$j HEAD; done'),
                                      self.result_row('')], both)[0])

    def test_loop_that_cannot_prove_each_add_stays_unproven(self):
        add = 'git worktree add -q -b x-$j .claude/worktrees/$j HEAD'
        for command in ('for j in $X; do %s; done' % add,
                        'for j in $(echo mine); do %s; done' % add,
                        'for j in mine $(echo a); do %s; done' % add,
                        'for j in min*; do %s; done' % add,
                        'for j in {mine,a}; do %s; done' % add,
                        'for j in mine; do for i in a b; do %s; done; done' % add,
                        'for j in mine; do %s 2>/dev/null; done' % add,
                        'for j in mine; do %s || true; done' % add,
                        'for j in mine; do %s; done || true' % add,
                        'for j in mine; do %s; done | tail -1' % add,
                        'for j in mine; do %s; done 2>/dev/null' % add,
                        'for j in mine; do %s & done' % add,
                        'for j in mine; do break; %s; done' % add,
                        'for j in mine; do [ -z "$X" ] && continue; %s; done' % add,
                        'for j in mine; do %s-$OTHER; done' % add,
                        'for j in other another; do %s; done' % add,
                        'while true; do %s; done' % add):
            with self.subTest(command=command):
                self.assertTrue(self.blocked([self.loop_row(command), self.result_row('')]))

    def test_loop_with_a_failure_line_or_foreign_timestamp_stays_unproven(self):
        command = 'for j in mine; do git worktree add -q -b x-$j .claude/worktrees/$j HEAD; done'
        for text in ("fatal: '.claude/worktrees/mine' already exists", 'error: could not lock config'):
            with self.subTest(text=text):
                self.assertTrue(self.blocked([self.loop_row(command), self.result_row(text)]))
        early = self.loop_row(command, shift=-7200)
        self.finished = datetime.fromtimestamp(
            datetime.fromisoformat(early['timestamp']).timestamp() + 60, timezone.utc).isoformat()
        self.assertTrue(self.blocked([early, self.result_row('')]))

    def test_creation_in_the_sessions_earlier_transcript_counts(self):
        # A continued conversation gets a new transcript; the creating call sits in the session's own file.
        (Path(self.tmp.name) / "sid-earlier.jsonl").write_text(
            json.dumps(self.created_row()) + "\n" + json.dumps(self.result_row()) + "\n", encoding="utf-8")
        with patch.dict(os.environ, CLAUDE_CODE_SESSION_ID="sid-earlier"):
            self.assertFalse(self.blocked([]))

    def test_other_full_path_with_same_basename_is_not_ours(self):
        row = self.created_row()
        row['message']['content'][0]['input']['command'] = (
            'git -C "' + self.root.as_posix() + '" worktree add -b peer "' +
            (Path(self.tmp.name) / 'other/mine').as_posix() + '" HEAD')
        self.assertTrue(self.blocked([row, self.result_row()]))

    def test_recreated_path_does_not_inherit_ownership(self):
        rows = [self.created_row(), self.result_row()]
        git(self.root, 'worktree', 'remove', '--force', '.claude/worktrees/mine')
        git(self.root, 'worktree', 'add', '-q', '-b', 'peer-recreated', '.claude/worktrees/mine', 'HEAD')
        evidence = self.root / '.claude/worktrees/mine/.claude/scratch/peer.txt'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('peer evidence', encoding='utf-8')
        self.assertTrue(self.blocked(rows))

    def test_delete_riders_remain_denied(self):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in [self.created_row(), self.result_row()]), encoding='utf-8')
        allowed = 'rm -rf .claude/worktrees/mine'
        riders = ['echo "$(rm -rf peer)"', 'echo "`rm -rf peer`"',
                  'sudo rm -rf peer', 'env rm -rf peer', 'printf peer | xargs rm -rf',
                  'W=.claude/worktrees/mine; W=peer; rm -rf "$W"']
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)), \
                patch('tempfile.gettempdir', return_value=str(Path(self.tmp.name).parent / 'elsewhere')):
            for rider in riders:
                with self.subTest(rider=rider):
                    self.assertTrue(pe.check_bash_command(allowed + '; ' + rider, str(self.root), str(transcript))[0])

    def test_unresolved_mutations_cannot_lend_ownership_to_aliases(self):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text('\n'.join(json.dumps(r) for r in [self.created_row(), self.result_row()]), encoding='utf-8')
        wt = self.root / '.claude/worktrees/mine'
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root), PEER=self.root.as_posix()), \
                patch('tempfile.gettempdir', return_value=str(Path(self.tmp.name).parent / 'elsewhere')):
            for mutation in ['A=$PEER', 'A+=$PEER', 'unset A', 'read A']:
                for original in [wt, self.root / '.claude/.cache/proof']:
                    command = f'A="{original.as_posix()}"; {mutation}; B=$A; FINAL=$B; rm -rf "$FINAL"'
                    with self.subTest(mutation=mutation, original=original):
                        self.assertTrue(pe.check_bash_command(command, str(self.root), str(transcript))[0])

    def test_untracked_nonworktree_directory_is_removable(self):
        # A scratch entry holding no git repository and no worktree loses nothing
        # git holds, so it needs no owner pick. A folder that holds a repo stays denied (see
        # test_clone_with_ignored_evidence_is_not_recoverable).
        target = self.root / '.claude/scratch/made'
        target.mkdir(parents=True)
        (target / 'evidence').write_text('irreplaceable', encoding='utf-8')
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)), \
                patch('tempfile.gettempdir', return_value=str(Path(self.tmp.name).parent / 'elsewhere')):
            self.assertFalse(pe.check_bash_command('rm -rf .claude/scratch/made', str(self.root))[0])

    def test_peer_worktree_in_temp_is_not_plain_temp_cleanup(self):
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)):
            self.assertTrue(pe.check_bash_command('rm -rf .claude/worktrees/mine', str(self.root))[0])

    def test_clone_with_ignored_evidence_is_not_recoverable(self):
        clone = self.root / '.claude/scratch/probe'
        git(self.root, 'clone', '-q', str(self.root), str(clone))
        evidence = clone / '.claude/scratch/evidence'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('ignored evidence', encoding='utf-8')
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)), \
                patch('tempfile.gettempdir', return_value=str(Path(self.tmp.name).parent / 'elsewhere')):
            self.assertTrue(pe.check_bash_command('rm -rf .claude/scratch/probe', str(self.root))[0])

    def scratch_made(self):
        target = self.root / '.claude/scratch/made'
        target.mkdir(parents=True)
        (target / 'evidence').write_text('a study output', encoding='utf-8')

    def scratch_verdict(self, rows):
        transcript = Path(self.tmp.name) / 'transcript.jsonl'
        transcript.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        with patch.dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)), \
                patch('tempfile.gettempdir', return_value=str(Path(self.tmp.name).parent / 'elsewhere')):
            return pe.check_bash_command('rm -rf .claude/scratch/made', str(self.root), str(transcript))[0]

    @staticmethod
    def said(text):
        return {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}

    @staticmethod
    def typed(text):
        return {'type': 'user', 'uuid': 'owner-' + str(abs(hash(text))), 'message': {'role': 'user', 'content': text}}

    def test_owner_answer_does_not_decide_a_scratch_delete(self):
        # The scratch rule alone decides, so the same
        # command passes with no transcript and with a typed "yes" alike.
        self.scratch_made()
        self.assertFalse(self.scratch_verdict([]))
        self.assertFalse(self.scratch_verdict([self.said('Should I delete .claude/scratch/made? My pick: yes.'),
                                               self.typed('Yes')]))

    def test_creation_named_only_in_prose_stays_denied(self):
        prose = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "git worktree add -q -b scratch/mine .claude/worktrees/mine HEAD"}]}}
        self.assertTrue(self.blocked([prose]))

    def test_another_sessions_worktree_stays_denied(self):
        self.assertTrue(self.blocked([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
