"""Classify and prune top-level `.claude/scratch/` entries for `/harness_prune` step Scratch.

  inventory [--keep-days N] [--vault ROOT] [--out PATH] [--repo DIR] [--scratch DIR]
      One JSON row per top-level entry: name, kind, bytes, files, newestMtime, referencedBy,
      referrers ({code, prose}), git (worktree/repo/null), gitWork, gitShells, proseFiles (.md,
      largest first, max 20), bucket, unverified.
      Buckets, first match wins: KEEP-WORKTREE, KEEP-GIT, KEEP-REFERENCED, KEEP-RECENT, CANDIDATE.
      KEEP-GIT: the entry holds a real repository (a `.git` directory with a HEAD, or a live
      linked worktree) with work git has nowhere else: commits no ref of the checkout reaches,
      on no remote of that repository, or modified tracked files (line-ending changes ignored).
      `gitWork` names each such repository and its work. An empty
      `.git` shell (no HEAD, counted in `gitShells`) or a clean copy whose commits the checkout
      holds is judged like any other row.
      KEEP-REFERENCED: something that runs reads the entry, i.e. a tracked or untracked file of a
      code or config suffix (CODE_SUFFIXES) names it. A prose, log or vault-note mention is
      recorded under `referrers.prose` and keeps nothing.
      References: one `git grep --untracked` pass over the repo plus every vault
      DevProjects/{{PROJECT_NAME}}/Claude/**/*.md. Any unread vault input (unreadable, dehydrated
      OneDrive placeholder, or an absent vault) marks every CANDIDATE row `unverified`.
      Exit 0 when bucket counts and bytes reconcile to the entry total, 1 otherwise.

  apply --inventory PATH --verdicts PATH [--vault ROOT]
      Verdicts: {name: {"verdict": "DELETE"|"ARCHIVE"|"KEEP", "reason": str,
      "archiveFiles": [path relative to the entry]}}. A verdict may name any inventory row; every
      verified CANDIDATE row needs one. A verdict for a name outside the inventory, a bad verdict
      value, `archiveFiles` on a non-ARCHIVE verdict or a path leaving the entry, or a verified
      CANDIDATE with no verdict aborts before any action (exit 2). A DELETE/ARCHIVE verdict on a
      KEEP-REFERENCED or KEEP-RECENT row stands: the judge saw the row's recorded facts. Each
      DELETE/ARCHIVE row is re-checked against the live tree and skipped when it holds `gitWork` now
      (a verdict never carries the owner's word for work git has nowhere else), its entry vanished,
      its newest mtime or `gitWork` changed, or a new code referrer appeared since the inventory.
      ARCHIVE copies `archiveFiles` (default: every .md in the entry) to
      <vault>/DevProjects/{{PROJECT_NAME}}/Claude/Archived/Scratch/<YYYY-MM>/<entry>/, verifies
      size and read-back, then deletes the entry; every key-shape match (`vault_sync.mask_bytes`) is
      replaced by a marker in the copy and counted per file in the output; a failed copy leaves the entry. Registered
      git worktrees inside an entry go through `git worktree remove` first.
      Exit 0 when nothing failed, 1 when any row failed.

  trim [--keep-days N] [--bulk-bytes N] [--repo DIR] [--scratch DIR]
      Deterministic, no verdicts: inside every top-level scratch folder (not a worktree, not a
      nested git checkout) delete each non-.md file of at least --bulk-bytes (default 100 MB)
      whose mtime is older than --keep-days. A citation pins a folder's text, not its crash dumps,
      captures and build outputs, so the folder stays and only its bulk goes. Top-level files are
      left to the CANDIDATE path. Exit 0 when nothing failed, 1 when any file could not go.
"""
import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import vault_read  # noqa: E402  (the dehydration-aware vault reader)
import vault_sync  # noqa: E402  (owns the key shapes an archive masks)
from _git_run import run as git_run  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_VAULT = vault_read.DEFAULT_VAULT
VAULT_DOCS = Path('DevProjects/{{PROJECT_NAME}}/Claude')
ARCHIVE_DIR = VAULT_DOCS / 'Archived' / 'Scratch'
BUCKETS = ('KEEP-WORKTREE', 'KEEP-GIT', 'KEEP-REFERENCED', 'KEEP-RECENT', 'CANDIDATE')
VERDICTS = ('DELETE', 'ARCHIVE', 'KEEP')
PROSE_CAP = 20
BULK_BYTES = 100 * 1024 * 1024
# ERE for git grep; a bracket expression cannot hold `\t`, so whitespace is a class.
GREP_ERE = r"""(\.claude[/\\])?scratch[/\\][^]"'[:space:]`)>]+"""
MENTION_RE = re.compile(r"""(?:\.claude[/\\])?scratch[/\\]+([^\s"'`)\]>/\\]+)""")
TRAILING_PUNCT = '.,;:*|!?'
# A referrer with one of these suffixes runs or configures something that can read the path;
# every other suffix (.md, .txt, .jsonl, .log, vault notes) is prose or a record of it.
CODE_SUFFIXES = frozenset(('.py', '.ps1', '.psm1', '.sh', '.bash', '.cmd', '.bat', '.cs', '.gd',
                           '.gdshader', '.js', '.mjs', '.ts', '.json', '.yml', '.yaml', '.toml'))
# Top-level scratch folders a tool keeps across sessions; the harness_prune close predicate never counts them.
TOOL_OWNED_SCRATCH = ('session_end', 'boot', 'kfm', 'eval_out', 'linux_preview')


def _entry_for(segment, names):
    if segment in names:
        return segment
    trimmed = segment.rstrip(TRAILING_PUNCT)
    return trimmed if trimmed in names else None


def _mentions(text):
    return [m.group(1) for m in MENTION_RE.finditer(text)]


def repo_references(repo, names):
    run = git_run(['grep', '--untracked', '-z', '-o', '-I', '-E', GREP_ERE], cwd=str(repo))
    if run.returncode not in (0, 1):
        raise RuntimeError('git grep failed: ' + run.err_text.strip())
    refs = {}
    for line in run.text.splitlines():
        path, _, match = line.partition('\0')
        for segment in _mentions(match):
            name = _entry_for(segment, names)
            if name:
                refs.setdefault(name, set()).add(path)
    return refs


def vault_references(vault, names):
    """Return (refs, counts); counts separate read, unreadable, unattempted and absent."""
    texts, counts = vault_read.read_vault(vault, VAULT_DOCS)
    refs = {}
    for path, text in texts:
        for segment in _mentions(text):
            name = _entry_for(segment, names)
            if name:
                refs.setdefault(name, set()).add(path)
    return refs, counts


vault_verified = vault_read.verified


def registered_worktrees(repo):
    run = git_run(['worktree', 'list', '--porcelain'], cwd=str(repo))
    if run.returncode:
        raise RuntimeError('git worktree list failed: ' + run.err_text.strip())
    return [Path(line[len('worktree '):]).resolve() for line in run.text.splitlines()
            if line.startswith('worktree ')]


def referrer_kind(path):
    return 'code' if Path(path).suffix.lower() in CODE_SUFFIXES else 'prose'


def _is_link(path):
    return path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path))


def _owns_git_file(git_file):
    """True when a `.git` file is a live linked worktree of its own checkout: the admin dir it names
    exists and that dir's gitdir back-pointer resolves to this very file. A copy or an orphan is not."""
    try:
        text = git_file.read_text(encoding='utf-8', errors='replace').strip()
        if not text.startswith('gitdir:'):
            return False
        admin = Path(text[len('gitdir:'):].strip())
        if not admin.is_absolute():
            admin = git_file.parent / admin
        pointer = Path((admin / 'gitdir').read_text(encoding='utf-8', errors='replace').strip())
        if not pointer.is_absolute():
            pointer = admin / pointer
        return os.path.normcase(os.path.realpath(pointer)) == os.path.normcase(os.path.realpath(git_file))
    except OSError:
        return False


def walk_entry(path, scratch, bulk_bytes=BULK_BYTES):
    """Size, file count, newest mtime, nested real repositories (`repos`: a `.git` directory holding a HEAD,
    or a worktree whose back-pointer names its own .git file), empty `.git` shells (`shells`), .md files and
    non-.md files of `bulk_bytes` or more as (bytes, path, mtime); links are not followed."""
    info = {'bytes': 0, 'files': 0, 'newest': os.lstat(path).st_mtime, 'git': False, 'repos': [],
            'shells': 0, 'prose': [], 'bulk': []}
    if not path.is_dir() or _is_link(path):
        if path.is_file():
            info['bytes'], info['files'] = os.lstat(path).st_size, 1
            if path.suffix.lower() == '.md':
                info['prose'].append((info['bytes'], path.relative_to(scratch).as_posix()))
        return info
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            children = list(os.scandir(current))
        except OSError:
            continue
        for child in children:
            try:
                st = child.stat(follow_symlinks=False)
            except OSError:
                continue
            info['newest'] = max(info['newest'], st.st_mtime)
            if child.name == '.git':
                if child.is_dir(follow_symlinks=False):
                    if os.path.isfile(os.path.join(child.path, 'HEAD')):
                        info['repos'].append(current)
                    else:
                        info['shells'] += 1
                elif _owns_git_file(Path(child.path)):
                    info['repos'].append(current)
                info['git'] = bool(info['repos'])
            if child.is_dir(follow_symlinks=False) and not _is_link(Path(child.path)):
                stack.append(Path(child.path))
            elif child.is_file(follow_symlinks=False):
                info['bytes'] += st.st_size
                info['files'] += 1
                if child.name.lower().endswith('.md'):
                    rel = Path(child.path).relative_to(scratch).as_posix()
                    info['prose'].append((st.st_size, rel))
                elif st.st_size >= bulk_bytes:
                    info['bulk'].append((st.st_size, Path(child.path), st.st_mtime))
    return info


def _within(inner, outer):
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


class MainCommits:
    """Every commit the checkout's refs reach, read once on first use."""

    def __init__(self, repo):
        self.repo, self._set = Path(repo), None

    def get(self):
        if self._set is None:
            run = git_run(['rev-list', '--all'], cwd=str(self.repo))
            self._set = set(run.text.split()) if run.returncode == 0 else set()
        return self._set


def _git_read(repo_dir, args, env=None):
    return git_run(args, cwd=str(repo_dir), env=env, prefix=('-c', 'safe.directory=*'))


def _read_only_env(repo_dir, scratch_dir):
    """Environment that sends git's index refresh to a copy: a write to the real index would move the
    mtime the sweep judges the entry by."""
    env = {'GIT_OPTIONAL_LOCKS': '0'}
    where = _git_read(repo_dir, ['rev-parse', '--git-path', 'index'])
    index = Path(repo_dir) / where.text.strip() if where.returncode == 0 and where.text.strip() else None
    if index and index.is_file():
        copy = Path(scratch_dir) / 'index'
        shutil.copyfile(index, copy)
        env['GIT_INDEX_FILE'] = str(copy)
    return env


def repo_work(repo_dir, main):
    """Describe the work a nested repository holds that git has nowhere else; [] when none.
    A repository git cannot read is reported as work, so an unreadable one is never judged."""
    work = []
    commits = _git_read(repo_dir, ['rev-list', '--all'])
    if commits.returncode:
        return ['unreadable repository: ' + commits.err_text.strip()[:120]]
    remote = _git_read(repo_dir, ['rev-list', '--remotes'])
    held = main.get() | (set(remote.text.split()) if remote.returncode == 0 else set())
    unique = [c for c in commits.text.split() if c not in held]
    if unique:
        work.append('{} commit{} on no remote and no ref of the checkout'.format(
            len(unique), '' if len(unique) == 1 else 's'))
    with tempfile.TemporaryDirectory() as tmp:
        env = _read_only_env(repo_dir, tmp)
        if commits.text.strip():
            diff = _git_read(repo_dir, ['diff', 'HEAD', '--numstat', '--ignore-cr-at-eol',
                                        '--ignore-space-at-eol'], env)
        else:
            diff = _git_read(repo_dir, ['status', '--porcelain', '--untracked-files=no'], env)
    changed = len(diff.text.splitlines())
    if diff.returncode:
        work.append('unreadable diff: ' + diff.err_text.strip()[:120])
    elif changed:
        work.append('{} modified tracked file(s)'.format(changed))
    return work


def _row(path, scratch, worktrees, references, keep_days, now, verified, main):
    info = walk_entry(path, scratch)
    resolved = path.resolve()
    git, git_work = None, []
    if any(_within(wt, resolved) for wt in worktrees):
        git = 'worktree'
    elif info['repos']:
        git = 'repo'
        for repo_dir in info['repos']:
            rel = Path(repo_dir).relative_to(path).as_posix() if Path(repo_dir) != path else '.'
            git_work += ['{}: {}'.format(rel, item) for item in repo_work(repo_dir, main)]
    cited = sorted(references.get(path.name, ()))
    referrers = {'code': [c for c in cited if referrer_kind(c) == 'code'],
                 'prose': [c for c in cited if referrer_kind(c) == 'prose']}
    if git == 'worktree':
        bucket = 'KEEP-WORKTREE'
    elif git_work:
        bucket = 'KEEP-GIT'
    elif referrers['code']:
        bucket = 'KEEP-REFERENCED'
    elif info['newest'] > now - keep_days * 86400:
        bucket = 'KEEP-RECENT'
    else:
        bucket = 'CANDIDATE'
    prose = [rel for _, rel in sorted(info['prose'], key=lambda item: (-item[0], item[1]))][:PROSE_CAP]
    return {'name': path.name, 'kind': 'dir' if path.is_dir() else 'file', 'bytes': info['bytes'],
            'files': info['files'], 'newestMtime': info['newest'], 'referencedBy': cited,
            'referrers': referrers, 'git': git, 'gitWork': git_work, 'gitShells': info['shells'],
            'proseFiles': prose, 'bucket': bucket, 'unverified': bucket == 'CANDIDATE' and not verified}


def _references(repo, vault, names):
    refs = repo_references(repo, names)
    vault_refs, counts = vault_references(vault, names)
    for name, paths in vault_refs.items():
        refs.setdefault(name, set()).update(paths)
    return refs, counts


def build_inventory(repo=REPO, scratch=None, vault=DEFAULT_VAULT, keep_days=14, now=None):
    repo = Path(repo)
    scratch = Path(scratch) if scratch else repo / '.claude' / 'scratch'
    now = time.time() if now is None else now
    entries = sorted(scratch.iterdir(), key=lambda p: p.name)
    names = {p.name for p in entries}
    refs, counts = _references(repo, vault, names)
    worktrees = registered_worktrees(repo)
    verified = vault_verified(counts)
    main = MainCommits(repo)
    rows = [_row(p, scratch, worktrees, refs, keep_days, now, verified, main) for p in entries]
    buckets = {b: {'count': 0, 'bytes': 0} for b in BUCKETS}
    for row in rows:
        buckets[row['bucket']]['count'] += 1
        buckets[row['bucket']]['bytes'] += row['bytes']
    return {'repo': str(repo), 'scratch': str(scratch), 'keepDays': keep_days, 'generatedAt': now,
            'vault': counts, 'rows': rows,
            'summary': {'entries': len(rows), 'buckets': buckets,
                        'unverified': sum(r['unverified'] for r in rows)}}


def reconciles(inv):
    buckets = inv['summary']['buckets'].values()
    return (sum(b['count'] for b in buckets) == inv['summary']['entries'] == len(inv['rows'])
            and sum(b['bytes'] for b in buckets) == sum(r['bytes'] for r in inv['rows']))


def print_summary(inv):
    print('| Bucket | Count | Bytes |')
    print('|---|---|---|')
    for name, bucket in inv['summary']['buckets'].items():
        print('| {} | {} | {} |'.format(name, bucket['count'], bucket['bytes']))
    total = sum(r['bytes'] for r in inv['rows'])
    print('| TOTAL | {} | {} |'.format(inv['summary']['entries'], total))
    vault = inv['vault']
    print('vault inputs: read={} unread={} (unreadable={} unattempted={}) absent={}'.format(
        vault['read'], vault['unreadable'] + vault['unattempted'], vault['unreadable'],
        vault['unattempted'], vault['absent']))
    print('unverified CANDIDATE rows: {}'.format(inv['summary']['unverified']))
    print('reconciles: {}'.format('yes' if reconciles(inv) else 'NO'))


# --- apply -------------------------------------------------------------------

def _clear_readonly_and_retry(failures):
    def handler(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError as exc:
            failures.append('{}: {}'.format(path, exc))
    return handler


def remove_entry(path):
    """Delete a file or tree, clearing read-only bits; return the paths that could not go."""
    failures = []
    if path.is_dir() and not _is_link(path):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry(failures))
    else:
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
        except OSError as exc:
            failures.append('{}: {}'.format(path, exc))
    if path.exists() and not failures:
        failures.append('{}: still present after removal'.format(path))
    return failures


def remove_entry_with_worktrees(path, repo):
    """Remove an entry; a registered git worktree in or at it goes through `git worktree remove`
    first so the checkout's registry stays true. Returns the paths that could not go."""
    failures = []
    resolved = path.resolve()
    main = Path(repo).resolve()
    inside = sorted((wt for wt in registered_worktrees(repo) if wt != main and _within(wt, resolved)),
                    key=lambda wt: -len(wt.parts))
    for wt in inside:
        # No fixed cap: removing a worktree deletes every file in it, so its cost grows with the tree.
        run = git_run(['worktree', 'remove', '--force', '--force', str(wt)], cwd=str(repo), timeout=None)
        if run.returncode and wt.exists():
            failures.append('{}: git worktree remove failed: {}'.format(
                wt, run.err_text.strip()))
    if inside:
        git_run(['worktree', 'prune'], cwd=str(repo), timeout=None)
    if failures:
        return failures
    return remove_entry(path) if path.exists() else []


def trim_bulk(repo=REPO, scratch=None, keep_days=14, bulk_bytes=BULK_BYTES, now=None):
    """Delete old bulk files inside scratch folders; return ([(path, bytes)], [failure])."""
    repo = Path(repo)
    scratch = Path(scratch) if scratch else repo / '.claude' / 'scratch'
    cutoff = (time.time() if now is None else now) - keep_days * 86400
    worktrees = registered_worktrees(repo)
    removed, failed = [], []
    for entry in sorted(scratch.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or _is_link(entry) or any(_within(wt, entry.resolve()) for wt in worktrees):
            continue
        info = walk_entry(entry, scratch, bulk_bytes)
        if info['git']:
            continue
        for size, path, mtime in sorted(info['bulk'], key=lambda item: str(item[1])):
            if mtime >= cutoff:
                continue
            problems = remove_entry(path)
            if problems:
                failed.extend(problems)
            else:
                removed.append((path, size))
    return removed, failed


def archive_entry(path, scratch, vault, files=None):
    """Copy the named files (default: every .md) into the vault archive with key shapes masked and verify each copy.

    Returns (archive folder, [(path relative to the entry, matches masked)] for each file that held any)."""
    dest_root = Path(vault) / ARCHIVE_DIR / time.strftime('%Y-%m') / path.name
    if path.is_file():
        sources = [path]
    elif files is not None:
        sources = [path / rel for rel in files]
        missing = [str(src) for src in sources if not src.is_file()]
        if missing:
            raise OSError('named file(s) not found: ' + ', '.join(missing))
    else:
        sources = sorted(p for p in path.rglob('*.md') if p.is_file())
    if not sources:
        raise OSError('no files to archive')
    masked = []
    for source in sources:
        rel = Path(source.name) if source == path else source.relative_to(path)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data, count = vault_sync.mask_bytes(source.read_bytes())
        dest.write_bytes(data)
        if dest.stat().st_size != len(data) or dest.read_bytes() != data:
            raise OSError('copy verification failed for ' + str(dest))
        if count:
            masked.append((rel.as_posix(), count))
    return dest_root, masked


def _load_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def _archive_file_problems(name, verdict, entry):
    files = verdict['archiveFiles']
    if verdict['verdict'] != 'ARCHIVE':
        return ['{}: archiveFiles belongs to an ARCHIVE verdict only'.format(name)]
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
        return ['{}: archiveFiles must be a non-empty list of paths'.format(name)]
    root = os.path.normcase(os.path.realpath(entry))
    bad = [f for f in files if Path(f).is_absolute()
           or not os.path.normcase(os.path.realpath(entry / f)).startswith(root + os.sep)]
    return ['{}: archiveFiles path leaves the entry: {}'.format(name, f) for f in bad]


def validate_verdicts(inv, verdicts):
    rows = {r['name']: r for r in inv['rows']}
    problems = []
    if not isinstance(verdicts, dict):
        return ['verdicts file is not a JSON object']
    for name, verdict in verdicts.items():
        row = rows.get(name)
        if row is None:
            problems.append('{}: not in the inventory'.format(name))
        elif row['unverified']:
            problems.append('{}: unverified (vault inputs unread)'.format(name))
        elif not isinstance(verdict, dict) or verdict.get('verdict') not in VERDICTS \
                or not isinstance(verdict.get('reason'), str) or not verdict['reason'].strip():
            problems.append('{}: verdict must be DELETE/ARCHIVE/KEEP with a reason'.format(name))
        elif 'archiveFiles' in verdict:
            problems += _archive_file_problems(name, verdict, Path(inv['scratch']) / name)
    for name, row in rows.items():
        if row['bucket'] == 'CANDIDATE' and not row['unverified'] and name not in verdicts:
            problems.append('{}: CANDIDATE row has no verdict'.format(name))
    return problems


def apply(inv, verdicts, vault):
    problems = validate_verdicts(inv, verdicts)
    if problems:
        print('REFUSED before any action:')
        for problem in problems:
            print('  ' + problem)
        return 2
    repo, scratch = Path(inv['repo']), Path(inv['scratch'])
    rows = {r['name']: r for r in inv['rows']}
    names = {p.name for p in scratch.iterdir()}
    refs, counts = _references(repo, vault, names)
    if not vault_verified(counts):
        print('REFUSED: vault inputs unread now (unreadable={} unattempted={} absent={})'.format(
            counts['unreadable'], counts['unattempted'], counts['absent']))
        return 2
    worktrees = registered_worktrees(repo)
    main = MainCommits(repo)
    live_now = time.time()
    totals = {'archived': 0, 'deleted': 0, 'kept': 0, 'skipped': 0, 'failed': 0}
    failures = []
    for name in sorted(verdicts):
        verdict, row, path = verdicts[name]['verdict'], rows[name], scratch / name
        if verdict == 'KEEP':
            totals['kept'] += 1
            print('KEPT     {} — {}'.format(name, verdicts[name]['reason']))
            continue
        drift = None
        if not path.exists():
            drift = 'entry no longer exists'
        else:
            live = _row(path, scratch, worktrees, refs, inv['keepDays'], live_now, True, main)
            if live['gitWork']:
                drift = 'holds git work git has nowhere else, so only the owner removes it: ' + '; '.join(live['gitWork'])
            elif live['newestMtime'] != row['newestMtime']:
                drift = 'newest mtime changed since the inventory'
            elif live['gitWork'] != row.get('gitWork', []):
                drift = 'git work changed since the inventory: ' + '; '.join(live['gitWork'])
            elif set(live['referrers']['code']) - set(row.get('referrers', {}).get('code', [])):
                drift = 'now read by ' + ', '.join(sorted(set(live['referrers']['code'])
                                                           - set(row.get('referrers', {}).get('code', []))))
        if drift:
            totals['skipped'] += 1
            print('SKIPPED  {} — {}'.format(name, drift))
            continue
        try:
            if verdict == 'ARCHIVE':
                dest, masked = archive_entry(path, scratch, vault, verdicts[name].get('archiveFiles'))
            unremoved = remove_entry_with_worktrees(path, repo)
        except OSError as exc:
            totals['failed'] += 1
            failures.append('{}: {} failed, entry left in place: {}'.format(name, verdict, exc))
            print('FAILED   {} — {}'.format(name, exc))
            continue
        if unremoved:
            totals['failed'] += 1
            failures.append('{}: could not remove {}'.format(name, '; '.join(unremoved)))
            print('FAILED   {} — {} path(s) unremovable'.format(name, len(unremoved)))
            continue
        key = 'archived' if verdict == 'ARCHIVE' else 'deleted'
        totals[key] += 1
        where = ' -> ' + str(dest) if verdict == 'ARCHIVE' else ''
        print('{:<8} {}{}'.format(key.upper(), name, where))
        if verdict == 'ARCHIVE':
            for rel, count in masked:
                print('  MASKED {} key match(es) in {}'.format(count, rel))
    if failures:
        print('Failures:')
        for failure in failures:
            print('  ' + failure)
    print(' '.join('{}={}'.format(k, v) for k, v in totals.items()))
    return 1 if totals['failed'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('--keep-days', type=int, default=14)
    inv.add_argument('--vault', default=str(DEFAULT_VAULT))
    inv.add_argument('--out')
    inv.add_argument('--repo', default=str(REPO))
    inv.add_argument('--scratch')
    app = sub.add_parser('apply')
    app.add_argument('--inventory', required=True)
    app.add_argument('--verdicts', required=True)
    app.add_argument('--vault', default=str(DEFAULT_VAULT))
    trm = sub.add_parser('trim')
    trm.add_argument('--keep-days', type=int, default=14)
    trm.add_argument('--bulk-bytes', type=int, default=BULK_BYTES)
    trm.add_argument('--repo', default=str(REPO))
    trm.add_argument('--scratch')
    args = parser.parse_args(argv)
    if args.cmd == 'trim':
        removed, failed = trim_bulk(repo=args.repo, scratch=args.scratch, keep_days=args.keep_days,
                                    bulk_bytes=args.bulk_bytes)
        for path, size in removed:
            print('TRIMMED  {} ({:.1f} MB)'.format(path, size / 1e6))
        for problem in failed:
            print('FAILED   ' + problem)
        print('trimmed={} bytes={} failed={}'.format(len(removed), sum(size for _, size in removed), len(failed)))
        return 1 if failed else 0
    if args.cmd == 'inventory':
        result = build_inventory(repo=args.repo, scratch=args.scratch, vault=args.vault,
                                 keep_days=args.keep_days)
        if args.out:
            Path(args.out).write_bytes(json.dumps(result, indent=1).encode('utf-8'))
            print('inventory: ' + args.out)
        print_summary(result)
        return 0 if reconciles(result) else 1
    return apply(_load_json(args.inventory), _load_json(args.verdicts), Path(args.vault))


if __name__ == '__main__':
    sys.exit(main())
