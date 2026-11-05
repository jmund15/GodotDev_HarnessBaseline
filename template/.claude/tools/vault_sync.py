"""Carry the Obsidian design vault to cloud sessions on the orphan branch `vault` of the repo's `origin`.

  scan --tree [--root DIR]
      Read every file under the allowlisted folders of DIR (default: the local vault) for key shapes.
      Prints JSON: scanned, hits [{path, pattern}], unreadable, forbidden; never matched text.
      Exit 0 clean, 1 hit or forbidden path, 2 unreadable file or absent DIR.

  precommit [--allow-deletions N]
      Judge staged changes, never working content. Prints JSON: refusals [{kind, path, ...}],
      deletions, caseRenames, conflictCopies. Exit 1 for forbidden/unlisted paths, staged secrets,
      deletion of an existing path, or deletions above DELETION_CAP (or N); exit 2 for unreadable
      blobs. Renames are not deletions. Conflict copies are reported, not refused.
      VAULT_SYNC_ALLOW_DELETIONS=N equals the flag.

  init [--worktree DIR] [--git-dir DIR] [--remote URL]
      Create the git dir outside OneDrive (default: %LOCALAPPDATA%/<gitDirName>), set core.worktree
      at the vault, write info/exclude and hooks/pre-commit. No .git or .gitignore in the vault.
      Push nothing; safe to rerun. The remote defaults to this repo's origin.

  sync [--worktree DIR] [--git-dir DIR] [--allow-deletions N] [--first-push]
      Under a lock (taken over after LOCK_STALE_S), fetch origin/vault, point the branch at it,
      add -A (working tree wins), precommit, commit, push fast-forward. Exit 0 pushed, no change,
      or busy; 1 refused (including missing remote branch without --first-push); 3 git/network failure.
      Reading with add and scan hydrates OneDrive placeholders. Each locked run writes
      <git-dir>/sync_status.json; pushes and failures append sync_log.jsonl (newest LOG_ROWS kept).
      Neither holds matched text.

  mirror [--target DIR]
      Fetch origin/vault in the current clone; replace only the configured folders beneath DIR
      (default: the harness readers' home-relative root), read-only after extraction completes.
      Peer folders and parent modes stay untouched. Reject unlisted archive entries or a missing
      allowlisted folder without replacing the old extract. Print
      `vault mirror: <sha>, last vault change <date> (<N> h ago), at <DIR>` or
      `vault mirror UNAVAILABLE: <reason>`; exit 0 either way. Age is the last vault change,
      not the fetch time, so a quiet vault reads old while in sync.

  install-task [--pythonw PATH] [--dry-run]
      Windows: register the configured taskName with schtasks /Create /F, every SYNC_INTERVAL_MIN minutes,
      through pythonw.exe beside this interpreter. --dry-run prints JSON and registers nothing.

AI/ holds credential notes: no subcommand reads it; nothing outside the configured folders is tracked.

Per-project values live in vault_sync.project.json beside this file:
  {"folders": ["Proj"], "gitDirName": "vault-git-x", "taskName": "VaultSyncX"}
folders are the vault folders carried (single path segments, never AI); gitDirName names the git dir and
taskName the scheduled task. With the file absent or invalid, every subcommand except mirror prints
`vault sync NOT-CONFIGURED: <reason>` on stderr and exits 2; mirror prints
`vault mirror UNAVAILABLE: <reason>` on stdout and exits 2.
"""
import argparse
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

EXCLUDED_DIRS = frozenset({'.obsidian', '.search-index'})
EXCLUDED_SUFFIXES = ('.db',)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault_read

DEFAULT_ROOT = vault_read.DEFAULT_VAULT / 'DevProjects'
DELETION_CAP = 10
BRANCH = 'vault'
SYNC_INTERVAL_MIN = 15
LOCK_STALE_S = 600
GIT_TIMEOUT_S = LOCK_STALE_S
LOG_ROWS = 500
CONFIG_PATH = Path(__file__).with_name('vault_sync.project.json')
ALLOWLIST: tuple = ()
GIT_DIR_NAME = ''
TASK_NAME = ''
EXCLUDE = ''
CONFIG_STATE = 'absent'
CONFIG_REASON = '.claude/tools/vault_sync.project.json is absent'
PATTERNS = {
    'sk-ant': rb'sk-ant-[A-Za-z0-9_\-]{20,}',
    'sk': rb'\bsk-[A-Za-z0-9]{20,}',
    'ghp': rb'\bghp_[A-Za-z0-9]{20,}',
    'github_pat': rb'\bgithub_pat_[A-Za-z0-9_]{20,}',
    'AKIA': rb'\bAKIA[0-9A-Z]{16}\b',
    'AIza': rb'\bAIza[0-9A-Za-z_\-]{35}\b',
    'xox': rb'\bxox[abprs]-[A-Za-z0-9\-]{10,}',
    'hf': rb'\bhf_[A-Za-z0-9]{30,}',
    'private-key': rb'-----BEGIN [A-Z ]*PRIVATE KEY-----',
}
_COMPILED = {name: re.compile(rx) for name, rx in PATTERNS.items()}


def _segment_problem(value) -> str | None:
    if not isinstance(value, str) or value in ('', '.', '..') or '/' in value or '\\' in value:
        return f'{value!r} is not a single path segment'
    return None


def configure(folders, git_dir_name, task_name) -> None:
    """Set the per-project values every function reads; raises ValueError on a value that cannot be carried."""
    global ALLOWLIST, GIT_DIR_NAME, TASK_NAME, EXCLUDE, CONFIG_STATE, CONFIG_REASON
    if not isinstance(folders, (list, tuple)) or not folders:
        raise ValueError('folders must be a non-empty list')
    for folder in folders:
        problem = _segment_problem(folder)
        if problem:
            raise ValueError(f'folders: {problem}')
        if folder == 'AI':
            raise ValueError('folders: AI holds credential notes and is never carried')
    for key, value in (('gitDirName', git_dir_name), ('taskName', task_name)):
        if not isinstance(value, str) or not value or '/' in value or '\\' in value:
            raise ValueError(f'{key} must be a non-empty string without a path separator')
    ALLOWLIST, GIT_DIR_NAME, TASK_NAME = tuple(folders), git_dir_name, task_name
    EXCLUDE = '\n'.join(['/*', *(f'!/{d}/' for d in ALLOWLIST), *(f'{d}/' for d in sorted(EXCLUDED_DIRS)),
                         *(f'*{s}' for s in EXCLUDED_SUFFIXES)]) + '\n'
    CONFIG_STATE, CONFIG_REASON = 'configured', ''


def _load_config() -> None:
    global CONFIG_STATE, CONFIG_REASON
    try:
        text = CONFIG_PATH.read_text(encoding='utf-8')
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        CONFIG_STATE, CONFIG_REASON = 'invalid', f'vault_sync.project.json is unreadable: {type(exc).__name__}'
        return
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('top level must be an object')
        missing = [k for k in ('folders', 'gitDirName', 'taskName') if k not in data]
        if missing:
            raise ValueError(f'missing key {missing[0]}')
        configure(data['folders'], data['gitDirName'], data['taskName'])
    except json.JSONDecodeError as exc:
        CONFIG_STATE, CONFIG_REASON = 'invalid', f'vault_sync.project.json is not valid JSON ({exc.msg})'
    except ValueError as exc:
        CONFIG_STATE, CONFIG_REASON = 'invalid', f'vault_sync.project.json: {exc}'


_load_config()


def scan_bytes(data: bytes) -> list[str]:
    """Names of key shapes present in data, in PATTERNS order."""
    return [name for name, rx in _COMPILED.items() if rx.search(data)]


def _excluded(parts) -> bool:
    return any(part in EXCLUDED_DIRS for part in parts) or parts[-1].endswith(EXCLUDED_SUFFIXES)


def forbidden_path(rel: str) -> str | None:
    """Why a vault-relative POSIX path may never be tracked; unlisted folders fail closed."""
    parts = rel.split('/')
    if parts[0] == 'AI':
        return 'AI/ holds credential notes'
    if len(parts) < 2 or parts[0] not in ALLOWLIST:
        return 'outside the allowlisted folders'
    if _excluded(parts):
        return 'excluded name (.obsidian, .search-index, *.db)'
    return None


def scan_paths(root: Path, rels: list[str]) -> dict:
    report = {'scanned': 0, 'hits': [], 'unreadable': [], 'forbidden': []}
    for rel in rels:
        if forbidden_path(rel):
            report['forbidden'].append(rel)
            continue
        try:
            data = (Path(root) / rel).read_bytes()
        except OSError:
            report['unreadable'].append(rel)
            continue
        report['scanned'] += 1
        report['hits'] += [{'path': rel, 'pattern': name} for name in scan_bytes(data)]
    return report


def tree_files(root: Path) -> list[str]:
    rels = []
    for folder in ALLOWLIST:
        for base, dirs, files in os.walk(Path(root) / folder):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for name in files:
                rel = (Path(base) / name).relative_to(root).as_posix()
                if not _excluded(rel.split('/')):
                    rels.append(rel)
    return sorted(rels)


def scan_tree(root: Path) -> dict:
    return scan_paths(root, tree_files(root))


def _conflict_copy(rel: str) -> bool:
    name = rel.rsplit('/', 1)[-1]
    host = re.escape(socket.gethostname().upper())
    return bool(re.search(rf'-(?:(?:DESKTOP|LAPTOP)-[A-Z0-9]{{5,}}|{host})\.[^/.]+$', name))


def check_changes(changes: list[dict], exists, cap: int = DELETION_CAP) -> dict:
    added = {c['path'].casefold(): c['path'] for c in changes if c['status'] in 'ARC'}
    out = {'refusals': [], 'deletions': 0, 'caseRenames': [], 'conflictCopies': []}
    for change in changes:
        status, rel = change['status'], change['path']
        if status == 'D':
            twin = added.get(rel.casefold())
            if twin and twin != rel:
                out['caseRenames'].append({'from': rel, 'to': twin})
                continue
            out['deletions'] += 1
            if exists(rel):
                out['refusals'].append({'kind': 'deletion-exists', 'path': rel})
            continue
        reason = forbidden_path(rel)
        if reason:
            out['refusals'].append({'kind': 'forbidden', 'path': rel, 'reason': reason})
        elif _conflict_copy(rel):
            out['conflictCopies'].append(rel)
    if out['deletions'] > cap:
        out['refusals'].append({'kind': 'deletion-cap', 'path': '*', 'count': out['deletions'], 'cap': cap})
    return out


def _git_run(repo, git_dir, *args, data: bytes | None = None, env=None):
    base = ['git']
    if git_dir:
        base += ['--git-dir', str(git_dir), '--work-tree', str(repo)]
    elif repo:
        base += ['-C', str(repo)]
    return subprocess.run(base + list(args), input=data, capture_output=True, env=env, timeout=GIT_TIMEOUT_S,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _git(repo, git_dir, *args, data: bytes | None = None) -> bytes:
    result = _git_run(repo, git_dir, *args, data=data)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result.stdout


def staged_changes(repo, git_dir) -> list[dict]:
    tokens = _git(repo, git_dir, 'diff', '--cached', '-M', '--name-status', '-z').decode('utf-8').split('\0')
    changes, i = [], 0
    while i + 1 < len(tokens):
        status = tokens[i][:1]
        if status in 'RC':
            changes.append({'status': status, 'old': tokens[i + 1], 'path': tokens[i + 2]})
            i += 3
        else:
            changes.append({'status': status, 'path': tokens[i + 1]})
            i += 2
    return changes


def staged_blobs(repo, git_dir, rels: list[str]) -> dict:
    if not rels:
        return {}
    out = _git(repo, git_dir, 'cat-file', '--batch', data=''.join(f':{rel}\n' for rel in rels).encode('utf-8'))
    blobs, pos = {}, 0
    for rel in rels:
        end = out.index(b'\n', pos)
        header = out[pos:end].split()
        pos = end + 1
        if header[-1] == b'missing':
            blobs[rel] = None
            continue
        size = int(header[2])
        blobs[rel] = out[pos:pos + size]
        pos += size + 1
    return blobs


def precommit(repo=None, git_dir=None, allow_deletions: int | None = None) -> tuple[int, dict]:
    top = Path(_git(repo, git_dir, 'rev-parse', '--show-toplevel').decode('utf-8').strip())
    changes = staged_changes(repo, git_dir)
    report = check_changes(changes, lambda rel: os.path.lexists(top / rel),
                           DELETION_CAP if allow_deletions is None else allow_deletions)
    refused = {r['path'] for r in report['refusals']}
    to_scan = [c['path'] for c in changes if c['status'] != 'D' and c['path'] not in refused]
    unreadable = False
    for rel, data in staged_blobs(repo, git_dir, to_scan).items():
        if data is None:
            report['refusals'].append({'kind': 'unreadable', 'path': rel})
            unreadable = True
        else:
            report['refusals'] += [{'kind': 'secret', 'path': rel, 'pattern': name} for name in scan_bytes(data)]
    kinds = {r['kind'] for r in report['refusals']}
    return (1 if kinds - {'unreadable'} else 2 if unreadable else 0), report


def default_git_dir() -> Path | None:
    """The sync git dir, or None while the tool is unconfigured."""
    if CONFIG_STATE != 'configured':
        return None
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'state')
    return Path(base) / GIT_DIR_NAME


def init(worktree, git_dir, remote_url: str) -> None:
    worktree, git_dir = Path(worktree), Path(git_dir)
    if not (git_dir / 'HEAD').exists():
        git_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'init', '-q', '--bare', str(git_dir)], check=True, capture_output=True)
    for key, value in (('core.bare', 'false'), ('core.worktree', worktree.as_posix()), ('core.autocrlf', 'false'),
                       ('core.longpaths', 'true'), ('user.name', 'vault-sync'), ('user.email', 'vault-sync@localhost')):
        _git(worktree, git_dir, 'config', key, value)
    _git(worktree, git_dir, 'symbolic-ref', 'HEAD', f'refs/heads/{BRANCH}')
    verb = 'set-url' if _git_run(worktree, git_dir, 'remote', 'get-url', 'origin').returncode == 0 else 'add'
    _git(worktree, git_dir, 'remote', verb, 'origin', remote_url)
    (git_dir / 'info').mkdir(exist_ok=True)
    (git_dir / 'info' / 'exclude').write_bytes(EXCLUDE.encode('utf-8'))
    hook = git_dir / 'hooks' / 'pre-commit'
    hook.parent.mkdir(exist_ok=True)
    script = f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "{Path(__file__).resolve().as_posix()}" precommit\n'
    hook.write_bytes(script.encode('utf-8'))
    hook.chmod(0o755)


def _take_lock(lock: Path) -> bool:
    for _ in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime < LOCK_STALE_S:
                    return False
                lock.unlink()
            except FileNotFoundError:
                pass
    return False


def _first_line(result) -> str:
    text = (result.stderr or b'').decode('utf-8', 'replace').strip()
    return text.splitlines()[-1] if text else f'git exited {result.returncode}'


def _sync(worktree, git_dir, allow_deletions, first_push, report) -> int:
    probe = _git_run(worktree, git_dir, 'ls-remote', '--exit-code', '--heads', 'origin', BRANCH)
    if probe.returncode not in (0, 2):
        report['error'] = _first_line(probe)
        return 3
    if probe.returncode == 2:
        if not first_push:
            report['refusals'].append({'kind': 'first-push', 'path': '*'})
            return 1
    else:
        fetch = _git_run(worktree, git_dir, 'fetch', '-q', 'origin', BRANCH)
        if fetch.returncode:
            report['error'] = _first_line(fetch)
            return 3
        tip = _git(worktree, git_dir, 'rev-parse', 'FETCH_HEAD').decode('utf-8').strip()
        _git(worktree, git_dir, 'update-ref', f'refs/heads/{BRANCH}', tip)
    add = _git_run(worktree, git_dir, 'add', '-A')
    if add.returncode:
        report['error'] = _first_line(add)
        return 3
    if _git_run(worktree, git_dir, 'diff', '--cached', '--quiet').returncode == 0:
        return 0
    verdict, judged = precommit(worktree, git_dir, allow_deletions)
    report.update({k: judged[k] for k in ('deletions', 'caseRenames', 'conflictCopies')})
    report['refusals'] += judged['refusals']
    if verdict:
        return 1
    env = dict(os.environ)
    if allow_deletions is not None:
        env['VAULT_SYNC_ALLOW_DELETIONS'] = str(allow_deletions)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    commit = _git_run(worktree, git_dir, 'commit', '-q', '-m', f'vault sync {stamp}', env=env)
    if commit.returncode:
        report['error'] = _first_line(commit)
        return 1 if b'vault_sync: refused' in (commit.stderr or b'') else 3
    report['commit'] = _git(worktree, git_dir, 'rev-parse', 'HEAD').decode('utf-8').strip()
    push = _git_run(worktree, git_dir, 'push', '-q', 'origin', f'refs/heads/{BRANCH}:refs/heads/{BRANCH}')
    if push.returncode:
        report['error'] = _first_line(push)
        return 3
    report['pushed'] = True
    return 0


def _record(git_dir: Path, code: int, report: dict) -> None:
    row = {'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'ok': code == 0, 'code': code,
           'pushed': report['pushed'], 'commit': report['commit'], 'deletions': report['deletions'],
           'caseRenames': report.get('caseRenames', []), 'conflictCopies': report.get('conflictCopies', []),
           'refusals': [{k: r[k] for k in ('kind', 'path', 'pattern') if k in r} for r in report['refusals']],
           'error': report['error']}
    tmp = git_dir / 'sync_status.json.tmp'
    tmp.write_text(json.dumps(row, indent=1), encoding='utf-8')
    os.replace(tmp, git_dir / 'sync_status.json')
    if code or report['pushed']:
        log = git_dir / 'sync_log.jsonl'
        old = log.read_text(encoding='utf-8').splitlines() if log.exists() else []
        log.write_text('\n'.join((old + [json.dumps(row)])[-LOG_ROWS:]) + '\n', encoding='utf-8')


def sync(worktree, git_dir, allow_deletions: int | None = None, first_push: bool = False) -> tuple[int, dict]:
    worktree, git_dir = Path(worktree), Path(git_dir)
    report = {'busy': False, 'pushed': False, 'commit': None, 'deletions': 0, 'refusals': [], 'error': None}
    lock = git_dir / 'sync.lock'
    if not _take_lock(lock):
        report['busy'] = True
        return 0, report
    try:
        try:
            code = _sync(worktree, git_dir, allow_deletions, first_push, report)
        except subprocess.CalledProcessError as exc:
            report['error'] = f'git {exc.cmd[0]} exited {exc.returncode}'
            code = 3
        except subprocess.TimeoutExpired as exc:
            report['error'] = f'git {exc.cmd[-1] if exc.cmd else ""} timed out after {GIT_TIMEOUT_S} s'
            code = 3
        except (OSError, ValueError) as exc:
            report['error'] = f'{type(exc).__name__}: {exc}'
            code = 3
        _record(git_dir, code, report)
    finally:
        lock.unlink(missing_ok=True)
    return code, report


def _chmod_tree(root: Path, file_mode: int, dir_mode: int) -> None:
    for base, dirs, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(base) / name, file_mode)
        for name in dirs:
            os.chmod(Path(base) / name, dir_mode)
    os.chmod(root, dir_mode)


def _writable_file(folder: Path) -> Path | None:
    """A writable file under folder, or None. A mirror extract is wholly read-only; a live vault is not."""
    if not folder.is_dir():
        return None
    for base, _dirs, files in os.walk(folder):
        for name in files:
            path = Path(base) / name
            if os.access(path, os.W_OK):
                return path
    return None


def mirror(target, repo=None, now: datetime | None = None) -> str:
    """Replace only ALLOWLIST folders under target; other projects and parent modes stay untouched."""
    target = Path(target)
    staging = target.with_name(f'.{GIT_DIR_NAME}-mirror.new')
    for folder in ALLOWLIST:
        live = _writable_file(target / folder)
        if live:
            return (f'vault mirror UNAVAILABLE: {target / folder} holds a writable file ({live.name}), so it is a '
                    'live vault, not a mirror extract; the mirror never replaces it')
    try:
        fetch = _git_run(repo, None, 'fetch', '-q', 'origin', BRANCH)
        if fetch.returncode:
            return f'vault mirror UNAVAILABLE: {_first_line(fetch)}'
        sha, date = _git(repo, None, 'log', '-1', '--format=%H%n%cI', 'FETCH_HEAD').decode('utf-8').split()
        archive = _git(repo, None, 'archive', '--format=tar', 'FETCH_HEAD')
        if staging.exists():
            _chmod_tree(staging, 0o644, 0o755)
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                parts = member.name.rstrip('/').split('/')
                if (not parts or parts[0] not in ALLOWLIST or any(p in ('', '.', '..') for p in parts)
                        or '\\' in member.name or not (member.isdir() or member.isfile())
                        or (member.isfile() and forbidden_path(member.name))):
                    raise ValueError('archive contains a forbidden path or entry type')
            tar.extractall(staging, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        for folder in ALLOWLIST:
            if not (staging / folder).is_dir():
                raise ValueError(f'archive is missing {folder}')
        target.mkdir(parents=True, exist_ok=True)
        for folder in ALLOWLIST:
            dest = target / folder
            backup = staging.with_name(f'{staging.name}.{folder}.old')
            if backup.exists():
                _chmod_tree(backup, 0o644, 0o755)
                shutil.rmtree(backup)
            if dest.exists():
                # Linux moves a directory to another parent only while the directory itself is writable.
                _chmod_tree(dest, 0o644, 0o755)
                os.replace(dest, backup)
            try:
                os.replace(staging / folder, dest)
            except OSError:
                if backup.exists():
                    os.replace(backup, dest)
                    _chmod_tree(dest, 0o444, 0o555)
                raise
            _chmod_tree(dest, 0o444, 0o555)
            if backup.exists():
                _chmod_tree(backup, 0o644, 0o755)
                shutil.rmtree(backup, ignore_errors=True)
        staging.chmod(0o755)
        staging.rmdir()
    except (OSError, tarfile.TarError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        return f'vault mirror UNAVAILABLE: {type(exc).__name__}: {exc}'
    hours = int(((now or datetime.now(timezone.utc)) - datetime.fromisoformat(date)).total_seconds() // 3600)
    return f'vault mirror: {sha[:7]}, last vault change {date[:10]} ({hours} h ago), at {target}'


def _mirror_cmd(args) -> int:
    if CONFIG_STATE != 'configured':
        print(f'vault mirror UNAVAILABLE: {CONFIG_REASON}')
        return 2
    print(mirror(args.target))
    return 0


def pythonw_path() -> Path | None:
    candidate = Path(sys.executable).with_name('pythonw.exe')
    return candidate if candidate.is_file() else None


def task_args(pythonw, tool, minutes: int = SYNC_INTERVAL_MIN) -> list[str]:
    run = f'"{pythonw}" "{tool}" sync'
    return ['schtasks', '/Create', '/F', '/SC', 'MINUTE', '/MO', str(minutes), '/TN', TASK_NAME, '/TR', run]


def _install_task(args) -> int:
    pythonw = args.pythonw or pythonw_path()
    if not pythonw:
        print('no pythonw.exe beside the running python; pass --pythonw', file=sys.stderr)
        return 2
    command = task_args(Path(pythonw).as_posix(), Path(__file__).resolve().as_posix())
    if args.dry_run:
        print(json.dumps(command))
        return 0
    done = subprocess.run(command, capture_output=True, text=True)
    print((done.stdout or done.stderr).strip())
    return done.returncode


def _repo_origin() -> str:
    out = subprocess.run(['git', '-C', str(Path(__file__).resolve().parent), 'remote', 'get-url', 'origin'],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _init(args) -> int:
    init(args.worktree, args.git_dir, args.remote or _repo_origin())
    print(json.dumps({'gitDir': str(args.git_dir), 'worktree': str(args.worktree)}))
    return 0


def _sync_cmd(args) -> int:
    code, report = sync(args.worktree, args.git_dir, args.allow_deletions, args.first_push)
    print(json.dumps(report, indent=1))
    return code


def _precommit(args) -> int:
    env = os.environ.get('VAULT_SYNC_ALLOW_DELETIONS')
    allow = args.allow_deletions if args.allow_deletions is not None else int(env) if env else None
    code, report = precommit(allow_deletions=allow)
    print(json.dumps(report, indent=1))
    for refusal in report['refusals']:
        print(f"vault_sync: refused {refusal['kind']} {refusal['path']}", file=sys.stderr)
    return code


def _scan(args) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(json.dumps({'error': f'vault root not found: {root}'}))
        return 2
    report = scan_tree(root)
    print(json.dumps(report, indent=1))
    if report['hits'] or report['forbidden']:
        return 1
    return 2 if report['unreadable'] else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    scan = sub.add_parser('scan')
    scan.add_argument('--tree', action='store_true', required=True)
    scan.add_argument('--root', default=str(DEFAULT_ROOT))
    scan.set_defaults(func=_scan)
    commit = sub.add_parser('precommit')
    commit.add_argument('--allow-deletions', type=int, default=None)
    commit.set_defaults(func=_precommit)
    task_cmd = sub.add_parser('install-task')
    task_cmd.add_argument('--pythonw')
    task_cmd.add_argument('--dry-run', action='store_true')
    task_cmd.set_defaults(func=_install_task)
    mirror_cmd = sub.add_parser('mirror')
    mirror_cmd.add_argument('--target', default=str(DEFAULT_ROOT))
    mirror_cmd.set_defaults(func=_mirror_cmd)
    for name, func in (('init', _init), ('sync', _sync_cmd)):
        cmd = sub.add_parser(name)
        cmd.add_argument('--worktree', default=str(DEFAULT_ROOT))
        cmd.add_argument('--git-dir', default=str(default_git_dir() or ''))
        if name == 'init':
            cmd.add_argument('--remote')
        else:
            cmd.add_argument('--allow-deletions', type=int, default=None)
            cmd.add_argument('--first-push', action='store_true')
        cmd.set_defaults(func=func)
    args = parser.parse_args(argv)
    if CONFIG_STATE != 'configured' and args.func is not _mirror_cmd:
        print(f'vault sync NOT-CONFIGURED: {CONFIG_REASON}', file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
