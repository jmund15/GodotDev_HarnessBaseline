"""Init and sync on planted vaults and bare origins: isolation, history, refusals, locks and errors.

The vault, its git dir and the bare origin are built once per module (`init`, `pushed`, `pushed_cap`) and copied per
test; a copy only has its absolute paths (core.worktree, the origin url) rewritten, with no git spawned.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_STAGE = tempfile.TemporaryDirectory()
for _name in ('vault_sync.py', 'vault_read.py'):
    shutil.copy(ROOT / '.claude/tools' / _name, Path(_STAGE.name) / _name)
TOOL = Path(_STAGE.name) / 'vault_sync.py'
(TOOL.parent / 'vault_sync.project.json').write_text(
    json.dumps({'folders': ['Proj'], 'gitDirName': 'vault-git-test', 'taskName': 'VaultSyncTest'}), encoding='utf-8')
spec = importlib.util.spec_from_file_location('vault_sync', TOOL)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)
vs.configure(['Proj'], 'vault-git-test', 'VaultSyncTest')
TOKEN = 'ghp_' + 'a1B2c3' * 6

FIRST = {'Proj/a.md': 'one', 'Proj/b.md': 'two', 'Proj/c.md': 'three',
         'AI/API Keys/k.md': 'private', 'Proj/.obsidian/app.json': '{}',
         'Proj/x.db': 'bin', '.obsidian/w.json': '{}', 'PeerA/m.md': 'other',
         'PeerB/b.md': 'other', 'PeerC/c.md': 'other'}


def git(*args, git_dir=None):
    base = ['git'] + (['--git-dir', str(git_dir)] if git_dir else [])
    return subprocess.run(base + list(args), check=True, capture_output=True, text=True).stdout.strip()


def plant(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')


@pytest.fixture(scope='module', autouse=True)
def _empty_cwd(tmp_path_factory):
    # Every git here names its repository (`--git-dir`, a path argument); each starts in an empty folder, so no git
    # command runs with this checkout as its directory.
    here = Path.cwd()
    os.chdir(tmp_path_factory.mktemp('vault_sync_cwd'))
    yield
    os.chdir(here)


class Scene:
    """Vault machine `a` (worktree, git dir) beside its bare origin, under `base`."""

    def __init__(self, base):
        self.base = Path(base)
        self.remote = self.base / 'remote.git'
        self.a = self.base / 'a' / 'DevProjects', self.base / 'a' / 'gitdir'

    def machine(self, name):
        worktree, git_dir = self.base / name / 'DevProjects', self.base / name / 'gitdir'
        worktree.mkdir(parents=True)
        vs.init(worktree, git_dir, str(self.remote))
        return worktree, git_dir

    def tip(self):
        try:
            return git('rev-parse', '--verify', '-q', 'refs/heads/vault', git_dir=self.remote)
        except subprocess.CalledProcessError:
            return None

    def remote_files(self):
        return git('ls-tree', '-r', '--name-only', 'refs/heads/vault', git_dir=self.remote).splitlines()


def _build(base, kind):
    scene = Scene(base)
    git('init', '-q', '--bare', str(scene.remote))
    worktree, git_dir = scene.machine('a')
    if kind == 'pushed':
        plant(worktree, FIRST)
        vs.sync(worktree, git_dir, first_push=True)
    elif kind == 'pushed_cap':
        plant(worktree, {f'Proj/d/{n}.md': f'note {n}' for n in range(vs.DELETION_CAP + 1)})
        vs.sync(worktree, git_dir, first_push=True)
    return scene


@pytest.fixture(scope='module')
def templates(tmp_path_factory):
    root = tmp_path_factory.mktemp('vault_templates')
    return {kind: _build(root / kind, kind) for kind in ('init', 'pushed', 'pushed_cap')}


@pytest.fixture
def scene(templates, tmp_path):
    """`scene(kind)`: a copy of the named template under tmp_path, its absolute paths rewritten to the copy."""
    def make(kind='init'):
        src = templates[kind].base
        dst = tmp_path / 'scene'
        shutil.copytree(src, dst)
        config = dst / 'a' / 'gitdir' / 'config'
        text = config.read_text(encoding='utf-8')
        # git config escapes a Windows path's backslashes, so the url holds them doubled
        for old, new in ((src.as_posix(), dst.as_posix()), (str(src).replace('\\', '\\\\'), str(dst).replace('\\', '\\\\'))):
            text = text.replace(old, new)
        config.write_text(text, encoding='utf-8', newline='\n')
        return Scene(dst)
    return make


def first(worktree):
    plant(worktree, FIRST)


# Init

def test_vault_gets_no_git_file_and_no_gitignore(scene):
    worktree, git_dir = scene().a
    assert sorted(p.name for p in worktree.iterdir()) == []
    assert (git_dir / 'hooks' / 'pre-commit').is_file()
    assert git('config', 'core.worktree', git_dir=git_dir) == worktree.as_posix()


def test_init_twice_is_a_no_op_and_pushes_nothing(scene):
    v = scene()
    worktree, git_dir = v.a
    vs.init(worktree, git_dir, str(v.remote))
    assert v.tip() is None


# Sync

def test_first_push_needs_the_flag(scene):
    v = scene()
    worktree, git_dir = v.a
    first(worktree)
    code, report = vs.sync(worktree, git_dir)
    assert (code, [r['kind'] for r in report['refusals']], v.tip()) == (1, ['first-push'], None)


def test_first_push_publishes_the_allowlist_only(scene):
    v = scene()
    worktree, git_dir = v.a
    first(worktree)
    code, report = vs.sync(worktree, git_dir, first_push=True)
    assert (code, report['pushed']) == (0, True), report
    assert sorted(v.remote_files()) == ['Proj/a.md', 'Proj/b.md', 'Proj/c.md']
    assert [p.name for p in worktree.iterdir() if p.name.startswith('.git')] == []


def test_a_later_change_fast_forwards(scene):
    v = scene('pushed')
    worktree, git_dir = v.a
    before = v.tip()
    plant(worktree, {'Proj/new.md': 'fresh'})
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed']) == (0, True), report
    assert git('rev-parse', 'refs/heads/vault~1', git_dir=v.remote) == before
    assert 'Proj/new.md' in v.remote_files()


def test_a_conflict_copy_name_reaches_the_status_file(scene):
    worktree, git_dir = scene('pushed').a
    plant(worktree, {'Proj/note-DESKTOP-ABC123.md': 'copy'})
    code, report = vs.sync(worktree, git_dir)
    assert code == 0, report
    status = json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))
    assert status['conflictCopies'] == ['Proj/note-DESKTOP-ABC123.md']


def test_every_git_child_opens_no_console_window(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vs.subprocess, 'run', lambda *args, **kwargs: calls.append(kwargs))
    vs._git_run(tmp_path, None, 'status')
    assert calls[0]['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def test_no_change_makes_no_commit(scene):
    v = scene('pushed')
    worktree, git_dir = v.a
    before = v.tip()
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed'], v.tip()) == (0, False, before)


def test_a_second_machine_continues_the_history_without_a_merge(scene):
    v = scene('pushed')
    a_tip = v.tip()
    b_tree, b_git = v.machine('b')
    first(b_tree)
    plant(b_tree, {'Proj/from_b.md': 'b'})
    code, report = vs.sync(b_tree, b_git)
    assert (code, report['pushed']) == (0, True), report
    assert git('rev-list', '--merges', '--count', 'refs/heads/vault', git_dir=v.remote) == '0'
    assert git('rev-parse', 'refs/heads/vault~1', git_dir=v.remote) == a_tip


def test_refusal_leaves_origin_alone_and_never_records_the_secret(scene):
    v = scene('pushed')
    worktree, git_dir = v.a
    before = v.tip()
    plant(worktree, {'Proj/leak.md': TOKEN})
    code, report = vs.sync(worktree, git_dir)
    assert (code, v.tip()) == (1, before)
    assert [r['kind'] for r in report['refusals']] == ['secret']
    status = (git_dir / 'sync_status.json').read_text(encoding='utf-8')
    assert not json.loads(status)['ok']
    assert TOKEN not in status + (git_dir / 'sync_log.jsonl').read_text(encoding='utf-8')
    (worktree / 'Proj/leak.md').unlink()
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed']) == (0, False), report


def test_a_secret_file_is_held_back_and_the_clean_change_still_pushes(scene):
    v = scene('pushed')
    worktree, git_dir = v.a
    plant(worktree, {'Proj/leak.md': TOKEN, 'Proj/clean.md': 'fine'})
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed']) == (0, True), report
    assert 'Proj/clean.md' in v.remote_files()
    assert 'Proj/leak.md' not in v.remote_files()
    assert git('diff', '--cached', '--name-only', git_dir=git_dir).strip() == ''
    assert [(r['kind'], r['path']) for r in report['refusals']] == [('secret', 'Proj/leak.md')]
    status = (git_dir / 'sync_status.json').read_text(encoding='utf-8')
    assert [r['path'] for r in json.loads(status)['refusals']] == ['Proj/leak.md']
    assert TOKEN not in status
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed']) == (1, False), report


def test_a_forbidden_path_is_held_back_and_the_rest_pushes(scene):
    v = scene('pushed')
    worktree, git_dir = v.a
    plant(worktree, {'AI/k.md': 'private', 'Proj/clean.md': 'fine'})
    git('--work-tree', str(worktree), 'add', '-f', 'AI/k.md', git_dir=git_dir)
    code, report = vs.sync(worktree, git_dir)
    assert (code, report['pushed']) == (0, True), report
    assert 'AI/k.md' not in v.remote_files()
    assert [r['kind'] for r in report['refusals']] == ['forbidden']


def test_a_held_back_file_with_a_deletion_over_the_cap_pushes_nothing(scene):
    v = scene('pushed_cap')
    worktree, git_dir = v.a
    before = v.tip()
    for path in (worktree / 'Proj/d').iterdir():
        path.unlink()
    plant(worktree, {'Proj/leak.md': TOKEN, 'Proj/clean.md': 'fine'})
    code, report = vs.sync(worktree, git_dir)
    assert (code, v.tip(), report['pushed']) == (1, before, False)
    assert 'deletion-cap' in [r['kind'] for r in report['refusals']]


def test_a_vanished_folder_is_not_pushed(scene):
    v = scene('pushed_cap')
    worktree, git_dir = v.a
    before = v.tip()
    for path in (worktree / 'Proj/d').iterdir():
        path.unlink()
    code, report = vs.sync(worktree, git_dir)
    assert (code, v.tip(), [r['kind'] for r in report['refusals']]) == (1, before, ['deletion-cap'])


def test_a_fresh_lock_means_busy_and_nothing_runs(scene):
    v = scene()
    worktree, git_dir = v.a
    first(worktree)
    (git_dir / 'sync.lock').write_text('12345', encoding='utf-8')
    code, report = vs.sync(worktree, git_dir, first_push=True)
    assert (code, report['busy'], v.tip()) == (0, True, None)


def test_a_stale_lock_is_taken_over(scene):
    worktree, git_dir = scene().a
    first(worktree)
    lock = git_dir / 'sync.lock'
    lock.write_text('12345', encoding='utf-8')
    old = time.time() - vs.LOCK_STALE_S - 60
    os.utime(lock, (old, old))
    code, _ = vs.sync(worktree, git_dir, first_push=True)
    assert code == 0
    assert not lock.exists()


def test_an_unreachable_remote_is_an_error_row_not_a_crash(scene):
    v = scene()
    worktree, git_dir = v.a
    first(worktree)
    git('remote', 'set-url', 'origin', str(v.base / 'no_such_remote.git'), git_dir=git_dir)
    code, report = vs.sync(worktree, git_dir, first_push=True)
    assert code == 3
    assert report['error']
    assert not json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))['ok']


def test_an_oserror_in_a_run_is_an_error_row_not_a_crash(scene, monkeypatch):
    worktree, git_dir = scene().a
    first(worktree)
    monkeypatch.setattr(vs, '_sync', lambda *args: (_ for _ in ()).throw(FileNotFoundError('git')))
    code, report = vs.sync(worktree, git_dir, first_push=True)
    assert code == 3
    assert 'FileNotFoundError' in report['error']
    assert not json.loads((git_dir / 'sync_status.json').read_text(encoding='utf-8'))['ok']


def test_a_stalled_git_call_ends_inside_the_lock_takeover_window(scene, monkeypatch):
    worktree, git_dir = scene().a
    first(worktree)
    seen = []

    def stalled(cmd, **kwargs):
        seen.append(kwargs.get('timeout'))
        raise subprocess.TimeoutExpired(cmd, kwargs.get('timeout') or 0)
    monkeypatch.setattr(vs.subprocess, 'run', stalled)
    code, report = vs.sync(worktree, git_dir, first_push=True)
    assert code == 3
    assert 'timed out' in report['error']
    assert set(seen) == {vs.LOCK_STALE_S}
    assert not (git_dir / 'sync.lock').exists()


def test_the_lock_is_held_until_the_status_row_is_written(scene, monkeypatch):
    worktree, git_dir = scene().a
    first(worktree)
    held = []
    original = vs._record
    monkeypatch.setattr(vs, '_record', lambda gd, code, report: (held.append((gd / 'sync.lock').exists()),
                                                                 original(gd, code, report)))
    vs.sync(worktree, git_dir, first_push=True)
    assert held == [True]


def test_log_keeps_the_newest_rows_only(scene):
    worktree, git_dir = scene().a
    first(worktree)
    (git_dir / 'sync_log.jsonl').write_text('{"old": 1}\n' * (vs.LOG_ROWS + 20), encoding='utf-8')
    vs.sync(worktree, git_dir, first_push=True)
    rows = (git_dir / 'sync_log.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(rows) == vs.LOG_ROWS
    assert '"ok"' in rows[-1]
