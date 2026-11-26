"""Shared fixture for harness_tests.py proofs: a throwaway git repo with an origin/main upstream and
the runner's module state patched to it, so a proof can plant harness paths and run `close`."""
import atexit
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/scripts'))
import harness_tests as runner
from _hook_call import _writable, copy_repo


_TEMPLATES = {}  # one built repository per `seed_repo` implementation, shared by every class that inherits it


class RepoCase(unittest.TestCase):
    """Each `seed_repo` implementation builds its repository once (on a prototype case) and each case resets to it by
    copying the built tree, `.git` included (`_hook_call.copy_repo`). A subclass that plants more files or commits
    extends `seed_repo` and calls super first; its cases then start from that state too."""

    @classmethod
    def _template(cls):
        # Built on first use, not in setUpClass: some proofs construct a case and call setUp directly.
        if cls.seed_repo not in _TEMPLATES:
            root = tempfile.mkdtemp(prefix='harness_close_template_')
            atexit.register(shutil.rmtree, root, onerror=_writable)
            proto = cls('setUp')
            proto.repo = Path(root) / 'repo'
            proto.repo.mkdir()
            proto.seed_repo()
            _TEMPLATES[cls.seed_repo] = (proto.repo, proto.base)
        return _TEMPLATES[cls.seed_repo]

    def seed_repo(self):
        for args in [('init', '-q'), ('config', 'user.email', 't@t'), ('config', 'user.name', 't')]:
            self.git(*args)
        self.write('README.md', 'seed\n')
        self.commit('seed')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        branch = self.git('branch', '--show-current')
        self.git('config', 'branch.' + branch + '.remote', 'origin')
        self.git('config', 'branch.' + branch + '.merge', 'refs/heads/main')
        self.git('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='harness_close_')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        template, self.base = type(self)._template()
        copy_repo(template, self.repo)
        self.git('config', 'remote.origin.url', str(self.repo / 'unused.git'))  # a path, so it is set per case
        self.stamp = self.repo / 'stamp.json'
        self.env = {'HARNESS_TEST_STAMP': str(self.stamp), 'HARNESS_NO_PASS_CACHE': '1',
                    'HARNESS_SLOT_DIR': str(self.repo / 'slots'),
                    'HARNESS_MACHINE_ROOT': str(self.repo / 'slots'), 'HARNESS_SLOT_BUDGET': ''}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env).start()
        os.environ.pop('CLAUDE_CODE_SESSION_ID', None)  # a proof's close never waits on the live session's dispatches
        for key, value in [('STAMP_PATH', str(self.stamp)), ('PROOF_BINDINGS', {}),
                           ('EXCLUDED', {}), ('HEAVY', {}), ('SERIAL', {}),
                           ('DURATIONS_PATH', None), ('KNOWN_RED_LOG', None), ('PASSES_PATH', None)]:
            patch.object(runner, key, value).start()
        # The machine's CPU is an ambient input: a loaded machine would read a planted red as contended.
        patch.object(runner._slot_pool, 'cpu_read', lambda: None).start()

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def commit(self, message):
        self.git('add', '-f', '--all')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def close(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = runner.run_close(str(self.repo))
        return rc, out.getvalue()

    def plant(self, value='good'):
        self.write('.claude/hooks/unit.py', 'VALUE = ' + repr(value) + '\n')
        self.write('.claude/tests/test_unit.py',
                   "from pathlib import Path\nimport sys\n"
                   "text = (Path(__file__).resolve().parents[1] / 'hooks/unit.py').read_text()\n"
                   "if 'bad' in text:\n    print('FAIL: unit regressed')\n    sys.exit(1)\n")
