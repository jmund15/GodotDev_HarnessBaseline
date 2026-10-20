"""Classify and prune top-level `.claude/scratch/` entries for `/harness_prune` step Scratch.

  inventory [--keep-days N] [--vault ROOT] [--out PATH] [--repo DIR] [--scratch DIR]
      One JSON row per top-level entry: name, kind, bytes, files, newestMtime, referencedBy,
      git (worktree/repo/null), proseFiles (.md, largest first, max 20), bucket, unverified.
      Buckets, first match wins: KEEP-WORKTREE, KEEP-GIT, KEEP-REFERENCED, KEEP-RECENT, CANDIDATE.
      References: one `git grep --untracked` pass over the repo plus every vault
      DevProjects/{{PROJECT_NAME}}/Claude/**/*.md. Any unread vault input (unreadable, dehydrated
      OneDrive placeholder, or an absent vault) marks every CANDIDATE row `unverified`.
      Exit 0 when bucket counts and bytes reconcile to the entry total, 1 otherwise.

  apply --inventory PATH --verdicts PATH [--vault ROOT]
      Verdicts: {name: {"verdict": "DELETE"|"ARCHIVE"|"KEEP", "reason": str}}, one per
      verified CANDIDATE row. A verdict for any other row, a bad verdict value, or a verified
      CANDIDATE with no verdict aborts before any action (exit 2). Each row is re-checked
      against the live tree first (newest mtime, references, vault reads); drift skips it.
      ARCHIVE copies the entry's .md files to
      <vault>/DevProjects/{{PROJECT_NAME}}/Claude/Archived/Scratch/<YYYY-MM>/<entry>/, verifies
      size and read-back, then deletes the entry; a failed copy leaves the entry.
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
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault_read  # noqa: E402  (the dehydration-aware vault reader)

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
    run = subprocess.run(['git', '-C', str(repo), 'grep', '--untracked', '-z', '-o', '-I', '-E', GREP_ERE],
                         capture_output=True)
    if run.returncode not in (0, 1):
        raise RuntimeError('git grep failed: ' + run.stderr.decode('utf-8', 'replace').strip())
    refs = {}
    for line in run.stdout.decode('utf-8', 'replace').splitlines():
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
    run = subprocess.run(['git', '-C', str(repo), 'worktree', 'list', '--porcelain'],
                         capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError('git worktree list failed: ' + run.stderr.strip())
    return [Path(line[len('worktree '):]).resolve() for line in run.stdout.splitlines()
            if line.startswith('worktree ')]


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
    """Size, file count, newest mtime, nested owned .git (a clone, or a worktree whose back-pointer names its own .git file), .md files and non-.md files of `bulk_bytes` or more
    as (bytes, path, mtime); links are not followed."""
    info = {'bytes': 0, 'files': 0, 'newest': os.lstat(path).st_mtime, 'git': False, 'prose': [], 'bulk': []}
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
            if child.name == '.git' and (child.is_dir(follow_symlinks=False)
                                         or _owns_git_file(Path(child.path))):
                info['git'] = True
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


def _row(path, scratch, worktrees, references, keep_days, now, verified):
    info = walk_entry(path, scratch)
    resolved = path.resolve()
    git = None
    if any(_within(wt, resolved) for wt in worktrees):
        git = 'worktree'
    elif info['git']:
        git = 'repo'
    cited = sorted(references.get(path.name, ()))
    if git == 'worktree':
        bucket = 'KEEP-WORKTREE'
    elif git == 'repo':
        bucket = 'KEEP-GIT'
    elif cited:
        bucket = 'KEEP-REFERENCED'
    elif info['newest'] > now - keep_days * 86400:
        bucket = 'KEEP-RECENT'
    else:
        bucket = 'CANDIDATE'
    prose = [rel for _, rel in sorted(info['prose'], key=lambda item: (-item[0], item[1]))][:PROSE_CAP]
    return {'name': path.name, 'kind': 'dir' if path.is_dir() else 'file', 'bytes': info['bytes'],
            'files': info['files'], 'newestMtime': info['newest'], 'referencedBy': cited, 'git': git,
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
    rows = [_row(p, scratch, worktrees, refs, keep_days, now, verified) for p in entries]
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


def archive_entry(path, scratch, vault):
    """Copy every .md in the entry into the vault archive and verify each copy."""
    dest_root = Path(vault) / ARCHIVE_DIR / time.strftime('%Y-%m') / path.name
    sources = [path] if path.is_file() else sorted(p for p in path.rglob('*.md') if p.is_file())
    if not sources:
        raise OSError('no .md files to archive')
    for source in sources:
        rel = Path(source.name) if source == path else source.relative_to(path)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        data = source.read_bytes()
        if dest.stat().st_size != len(data) or dest.read_bytes() != data:
            raise OSError('copy verification failed for ' + str(dest))
    return dest_root


def _load_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def validate_verdicts(inv, verdicts):
    rows = {r['name']: r for r in inv['rows']}
    problems = []
    if not isinstance(verdicts, dict):
        return ['verdicts file is not a JSON object']
    for name, verdict in verdicts.items():
        row = rows.get(name)
        if row is None:
            problems.append('{}: not in the inventory'.format(name))
        elif row['bucket'] != 'CANDIDATE':
            problems.append('{}: bucket {} is not CANDIDATE'.format(name, row['bucket']))
        elif row['unverified']:
            problems.append('{}: unverified (vault inputs unread)'.format(name))
        elif not isinstance(verdict, dict) or verdict.get('verdict') not in VERDICTS \
                or not isinstance(verdict.get('reason'), str) or not verdict['reason'].strip():
            problems.append('{}: verdict must be DELETE/ARCHIVE/KEEP with a reason'.format(name))
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
    totals = {'archived': 0, 'deleted': 0, 'kept': 0, 'skipped': 0, 'failed': 0}
    failures = []
    for name in sorted(verdicts):
        verdict, row, path = verdicts[name]['verdict'], rows[name], scratch / name
        drift = None
        if not path.exists():
            drift = 'entry no longer exists'
        elif refs.get(name):
            drift = 'now referenced by ' + ', '.join(sorted(refs[name]))
        elif walk_entry(path, scratch)['newest'] != row['newestMtime']:
            drift = 'newest mtime changed since the inventory'
        if verdict == 'KEEP':
            totals['kept'] += 1
            print('KEPT     {} — {}'.format(name, verdicts[name]['reason']))
            continue
        if drift:
            totals['skipped'] += 1
            print('SKIPPED  {} — {}'.format(name, drift))
            continue
        try:
            if verdict == 'ARCHIVE':
                dest = archive_entry(path, scratch, vault)
            unremoved = remove_entry(path)
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
