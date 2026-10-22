#!/usr/bin/env python3
"""uid_cache_audit.py: apply insertion point, LF writes, checkout targeting, dirty-skip report, header backfill."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

HOOK = Path(__file__).resolve().parents[1] / 'hooks' / 'uid_cache_audit.py'
GIT = ['git', '-c', 'user.name=proof', '-c', 'user.email=proof@example.invalid', '-c', 'commit.gpgsign=false']
TARGET_REF = '[ext_resource type="Script" path="res://Items/target.tres" id="1_t"]'
HEADERLESS = '[gd_resource type="Resource" format=3]\n\n[resource]\nvalue = 1\n'


def git(root, *args):
    return subprocess.run([*GIT, '-C', str(root), *args], capture_output=True, text=True, check=True).stdout


def cache_bytes(entries):
    out = struct.pack('<I', len(entries))
    for uid, path in entries:
        raw = path.encode('utf-8')
        out += struct.pack('<QI', uid, len(raw)) + raw
    return out


def run_hook(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    return subprocess.run([sys.executable, str(HOOK), *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


def make_repo(root):
    git(root, 'init', '-q')
    (root / 'Items').mkdir()
    (root / 'Items/target.tres').write_bytes(b'[gd_resource type="Resource" format=3 uid="uid://bb"]\n\n[resource]\n')
    (root / 'Items/holder.tres').write_bytes(
        ('[gd_resource type="Resource" format=3]\n\n' + TARGET_REF + '\n\n[resource]\nvalue = 1\n').encode('utf-8'))
    (root / 'Items/old_headerless.tres').write_bytes(HEADERLESS.encode('utf-8'))
    (root / '.godot').mkdir()
    (root / '.godot/uid_cache.bin').write_bytes(cache_bytes([(35, 'res://Items/target.tres'),
                                                              (36, 'res://Items/new_cached.tres')]))
    git(root, 'add', 'Items')
    git(root, 'commit', '-q', '-m', 'seed')


def main():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'repo'
        root.mkdir()
        make_repo(root)

        report = run_hook(root)
        cases.append(('report mode lists the path-only ref and changes nothing',
                      report.returncode == 0 and '[MISSING-UID]' in report.stdout and 'uid://bb' in report.stdout
                      and (root / 'Items/holder.tres').read_bytes().count(b'uid="uid://bb"') == 0))

        applied = run_hook(root, '--apply')
        holder = (root / 'Items/holder.tres').read_bytes()
        cases.append(('apply inserts the uid after the type attribute closing quote',
                      applied.returncode == 0
                      and b'[ext_resource type="Script" uid="uid://bb" path="res://Items/target.tres" id="1_t"]' in holder
                      and b'type="Script uid=' not in holder))
        cases.append(('apply writes LF, never CRLF', holder.count(b'\r\n') == 0 and holder.count(b'\n') > 0))
        git(root, 'checkout', '--', 'Items/holder.tres')

        (root / 'Items/holder.tres').write_bytes(holder + b'# edited\n')
        dirty = run_hook(root)
        cases.append(('a file with uncommitted edits is skipped and named',
                      'skipped (uncommitted edits): Items\\holder.tres' in dirty.stdout
                      or 'skipped (uncommitted edits): Items/holder.tres' in dirty.stdout))
        git(root, 'checkout', '--', 'Items/holder.tres')

        (root / 'Items/new_cached.tres').write_bytes(HEADERLESS.encode('utf-8'))
        (root / 'Items/new_minted.tres').write_bytes(HEADERLESS.encode('utf-8'))
        (root / 'addons/vendor').mkdir(parents=True)
        (root / 'addons/vendor/skip.tres').write_bytes(HEADERLESS.encode('utf-8'))
        git(root, 'add', 'Items/new_cached.tres', 'Items/new_minted.tres', 'addons')
        preview = run_hook(root, '--backfill-headers')
        cases.append(('backfill without --apply reports and writes nothing',
                      preview.returncode == 0 and '2 header(s) missing a uid' in preview.stdout
                      and (root / 'Items/new_cached.tres').read_bytes() == HEADERLESS.encode('utf-8')))
        stamped = run_hook(root, '--backfill-headers', '--apply')
        cached = (root / 'Items/new_cached.tres').read_text(encoding='utf-8')
        minted = (root / 'Items/new_minted.tres').read_text(encoding='utf-8')
        cases.append(('backfill stamps a staged new file with its cache uid',
                      stamped.returncode == 0 and cached.startswith('[gd_resource type="Resource" format=3 uid="uid://bc"]\n')))
        cases.append(('backfill mints a fresh uid for an unregistered staged new file',
                      minted.startswith('[gd_resource type="Resource" format=3 uid="uid://')
                      and cached.split('uid="')[1] != minted.split('uid="')[1]))
        cases.append(('backfill never edits a committed file or addons/',
                      (root / 'Items/old_headerless.tres').read_bytes() == HEADERLESS.encode('utf-8')
                      and (root / 'addons/vendor/skip.tres').read_bytes() == HEADERLESS.encode('utf-8')))

        git(root, 'commit', '-q', '-m', 'add')
        git(root, 'worktree', 'add', '-q', str(Path(tmp) / 'linked'), '-b', 'linked')
        outside = Path(tmp) / 'elsewhere'
        outside.mkdir()
        linked = run_hook(outside, '--repo', str(Path(tmp) / 'linked'))
        cases.append(('--repo targets a linked worktree and reads the main checkout cache',
                      linked.returncode == 0 and 'cache entries=2' in linked.stdout))
        nogit = run_hook(outside)
        cases.append(('outside a checkout it exits with a message naming --repo',
                      nogit.returncode != 0 and '--repo' in (nogit.stderr + nogit.stdout)))
    for label, ok in cases:
        print(('PASS: ' if ok else 'FAIL: ') + label)
    failures = sum(not ok for _, ok in cases)
    print('%d/%d passed' % (len(cases) - failures, len(cases)))
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
