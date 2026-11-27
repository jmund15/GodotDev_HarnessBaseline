#!/usr/bin/env python3
"""No harness spawn site makes Windows hand a child to the default terminal.

A console-less process (DETACHED_PROCESS) makes every console child allocate a fresh console, and Windows hands it
to the default terminal: a visible window per child that outlives the run when the child dies mid-handoff
(2026-10-05: 16-131 s per child, and a pile of Windows Terminal error windows). A worker that outlives its
launcher takes `detach_options()` (hidden console its children share); a PowerShell `Start-Process` is
`-WindowStyle Hidden` or `-NoNewWindow`; Node `detached: true` is console-less.

    python3 .claude/tests/test_spawn_console_shape.py   (0 pass, 1 fail, 2 cannot run)
"""
import ast
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'scratch', 'cache', '.cache', 'plans', 'luna_scores', 'node_modules'}
# Readers of the flags, not spawners: the proof that asserts a spawn lacks DETACHED_PROCESS, and this proof.
READERS = {'test_hook_detach_console.py', 'test_spawn_console_shape.py'}
BANNED_NAMES = {'DETACHED_PROCESS', 'CREATE_NEW_CONSOLE'}
FAILURES = []


GENERATED_DIRS = {'worktrees', 'logs', 'tmp', '__pycache__', '.git'}


def files(suffix):
    """Harness files under `.claude/`, by a walk that prunes the output, nested-checkout and generated folders
    before it descends: the proof reads the files themselves, so its key holds no git state."""
    for folder, dirs, names in os.walk(CLAUDE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS | GENERATED_DIRS)
        for name in sorted(names):
            path = Path(folder) / name
            if name.endswith(suffix) and not path.relative_to(CLAUDE.parent).as_posix().startswith(
                    '.claude/tests/fixtures/evidence/'):
                yield path


def check_source_scope():
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix='spawn_scope_') as tmp:
        root = Path(tmp)
        snapshot = '.claude/tests/fixtures/evidence/r1/tree/.claude/tools/excerpt.py'
        source = '.claude/tools/live.py'
        for rel in (snapshot, source):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('    source excerpt\n', encoding='utf-8')
        with patch(__name__ + '.CLAUDE', root / '.claude'):
            selected = list(files('.py'))
        if selected != [root / source]:
            FAILURES.append('source scan must keep live Python and exclude frozen evidence excerpts')


def check_python():
    for path in files('.py'):
        if path.name in READERS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError as exc:
            FAILURES.append('%s:%d cannot parse Python source: %s' % (path.relative_to(CLAUDE), exc.lineno or 1, exc.msg))
            continue
        for node in ast.walk(tree):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name in BANNED_NAMES:
                FAILURES.append('%s:%d uses %s (console-less or new-console child)' % (path.relative_to(CLAUDE), node.lineno, name))


def check_powershell():
    for path in files('.ps1'):
        if path.parent.name == 'tests':      # these stub Start-Process rather than call it
            continue
        text = re.sub(r'<#.*?#>', '', path.read_text(encoding='utf-8', errors='replace'), flags=re.S)
        text = re.sub(r'`\s*\r?\n', ' ', text)
        for number, line in enumerate(text.splitlines(), 1):
            code = line.split(' #')[0]
            if re.match(r'\s*#', line) or not re.search(r'\bStart-Process\b', code):
                continue
            if not re.search(r'-WindowStyle\s+Hidden|-NoNewWindow', code):
                FAILURES.append('%s: Start-Process without -WindowStyle Hidden or -NoNewWindow: %s' % (path.relative_to(CLAUDE), code.strip()[:90]))


def check_node():
    for path in list(files('.js')) + list(files('.cjs')) + list(files('.mjs')):
        if path.parent.name == 'tests':
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if re.search(r'\bdetached\s*:\s*true', line):
                FAILURES.append('%s:%d spawns with detached: true (console-less on Windows)' % (path.relative_to(CLAUDE), number))


def load(path, name):
    sys.path[:0] = [str(path.parent)]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_one_detach_shape():
    """The one `detach_options` carries, on Windows, a hidden shared console."""
    hooks = load(CLAUDE / 'hooks' / '_bounded_run.py', '_bounded_run')
    flags = hooks.detach_options().get('creationflags', 0)
    if os.name == 'nt' and (flags & subprocess.DETACHED_PROCESS or not flags & subprocess.CREATE_NO_WINDOW):
        FAILURES.append('detach_options on Windows must be CREATE_NO_WINDOW without DETACHED_PROCESS, got %#x' % flags)


def check_detached_jobs_carry_id():
    """Every detached worker carries a job id (`detach_options(env)`), so reap.py's `queued` sees the work it detaches; a
    worker started inside a job keeps that job's id, so nested detached work counts toward its root."""
    hooks = load(CLAUDE / 'hooks' / '_bounded_run.py', '_bounded_run')
    fresh = hooks.detach_options({'PATH': 'x'}).get('env') or {}
    kept = hooks.detach_options({'PATH': 'x', 'REAP_JOB_ID': 'root-1'}).get('env') or {}
    if not fresh.get('REAP_JOB_ID') or fresh.get('PATH') != 'x' or kept.get('REAP_JOB_ID') != 'root-1':
        FAILURES.append('detach_options must carry env with a fresh REAP_JOB_ID and keep an inherited one: %r / %r' % (fresh, kept))
    listed = [p.relative_to(CLAUDE).as_posix() for p in files('.py')]
    owners = ('hooks/_bounded_run.py',)
    for rel in (f for f in listed if not f.startswith('tests/') and f not in owners):
        text = (CLAUDE / rel).read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'job_env', text):
            FAILURES.append('%s:%d names job_env; a detached worker takes its env from detach_options(env)'
                            % (rel, text.count(chr(10), 0, m.start()) + 1))


def main():
    for check in (check_source_scope, check_python, check_powershell, check_node, check_one_detach_shape,
                  check_detached_jobs_carry_id):
        before = len(FAILURES)
        try:
            check()
        except Exception as exc:
            FAILURES.append('%s raised %s: %s' % (check.__name__, type(exc).__name__, exc))
        print(('FAIL ' if len(FAILURES) > before else 'ok   ') + check.__name__)
    for failure in FAILURES:
        print('  ' + failure)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
