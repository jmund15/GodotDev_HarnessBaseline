"""List tracked top-level `.claude/` entries that nothing else names, for `/harness_prune`'s Strays step.

  list [--repo DIR] [--min-age-days N]
      One JSON row per candidate: name, files, ageDays. A candidate is a tracked top-level
      entry of `.claude/` (file or directory) that no other tracked file mentions by name
      (`baseline.lock.json` lists every file and is ignored) and whose last commit is at least
      N days old (default 14, scratch_prune's recency floor). Report-only: the owner decides.
      A name another file mentions only by coincidence counts as referenced, so the list
      under-reports rather than over-reports. Exit 0.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK = '.claude/baseline.lock.json'
DEFAULT_MIN_AGE_DAYS = 14
DAY = 86400


def _git(repo, *args):
    run = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8')
    if run.returncode not in (0, 1):
        raise RuntimeError(f'git {args[0]} failed: {run.stderr.strip()}')
    return run.stdout


def _entries(repo):
    entries = {}
    for path in _git(repo, 'ls-files', '-z', '--', '.claude').split('\0'):
        parts = path.split('/')
        if len(parts) >= 2 and path:
            entries.setdefault(parts[1], []).append(path)
    return entries


def _mentioned_elsewhere(repo, name):
    hits = _git(repo, 'grep', '-l', '-F', '-e', name, '--', '.', f':!{LOCK}').splitlines()
    own = f'.claude/{name}'
    return [hit for hit in hits if hit != own and not hit.startswith(own + '/')]


def candidates(repo, min_age_days):
    now = time.time()
    rows = []
    for name, files in sorted(_entries(repo).items()):
        stamp = _git(repo, 'log', '-1', '--format=%ct', '--', f'.claude/{name}').strip()
        age = (now - int(stamp)) / DAY if stamp else 0
        if age < min_age_days or _mentioned_elsewhere(repo, name):
            continue
        rows.append({'name': name, 'files': len(files), 'ageDays': int(age)})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=['list'])
    parser.add_argument('--repo', default=str(REPO))
    parser.add_argument('--min-age-days', type=int, default=DEFAULT_MIN_AGE_DAYS)
    args = parser.parse_args(argv)
    print(json.dumps({'candidates': candidates(args.repo, args.min_age_days)}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
