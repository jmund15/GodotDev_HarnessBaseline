#!/usr/bin/env python3
"""Finish a land: re-run a land job the runner lost before it ran, and report the land's outcome.

  python3 .claude/tools/land_watch.py retry --run <run id> --attempt <n>
  python3 .claude/tools/land_watch.py watch --branch land/<name> --sha <commit> [--timeout-min 240]

`retry` runs from `.github/workflows/land-retry.yml` after every failed land run, so a lost job re-runs with no
session awake. A job is lost when it ends with no step run (the runner could not start its worker, or dropped it)
or its checkout step failed, and its log carries no LAND_RESULT line: the land script never judged the commit. A
red from `land_run.ps1` is never re-run, and neither is a job still running or a run whose log cannot be read.
Re-runs stop after MAX_ATTEMPTS, so a runner that keeps dropping jobs ends red instead of looping.

`watch` waits for the land run of one pushed commit and prints one terminal line: `LANDED <sha>` (the `landed=` sha when the runner rebased, else the pushed one), `SUPERSEDED <sha> by <branch>` (a later land branch carries it),
`RED <stage line>`, `CANCELLED run <id>` (cancelled on purpose, so never re-run), `LOST <run> after <n> attempts` or `UNREAD run <id>` (its log cannot be read or has no LAND_RESULT line: exit 2, never LANDED from the pushed sha); at `--timeout-min` it prints `TIMEOUT` and exits 2. When the branch has runs but none for `--sha`, it prints `NO RUN matches <sha> among <runs seen>` once and, if that holds for NO_MATCH_MIN minutes, prints it again and exits 2; a branch with no runs at all keeps waiting. It re-runs a lost job itself too, which covers a land-retry workflow that never
started; a second re-run of a run already in progress is refused by GitHub and ignored here.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

MAX_ATTEMPTS = 3
WORKFLOW = 'land.yml'
LAND_STEP = 'Prove and land'
NO_MATCH_MIN = 5


def gh(*args):
    out = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8')
    return out.returncode, out.stdout, out.stderr


def classify(jobs):
    """'judged' when the land script judged the commit, 'pending' while a job still runs, else 'lost'.

    GitHub can report a run completed while its job still reads in_progress with no steps; that job is not lost."""
    for job in jobs:
        steps = {s.get('name'): s.get('conclusion') for s in job.get('steps') or []}
        if steps.get(LAND_STEP) in ('success', 'failure'):
            return 'judged'
    if any(job.get('status', 'completed') != 'completed' for job in jobs):
        return 'pending'
    return 'lost'


def attempt_jobs(run, attempt, tries=3):
    """The attempt's job records; a failed read (GitHub answers 5xx under load) is retried before it raises."""
    for n in range(1, tries + 1):
        rc, out, err = gh('api', 'repos/{owner}/{repo}/actions/runs/%s/attempts/%s/jobs' % (run, attempt))
        if not rc:
            return json.loads(out).get('jobs') or []
        if n < tries:
            time.sleep(10)
    raise RuntimeError('cannot read jobs of run %s attempt %s: %s' % (run, attempt, err.strip()))


def settle(run, attempt, state):
    """A `lost` read confirmed against that attempt's log before anything re-runs: GitHub can leave the land step's
    conclusion unrecorded on a run land_run.ps1 judged, but the judgement always prints a LAND_RESULT line.
    An unreadable log is `unread`: nothing re-runs on a guess, and the watch ends rather than poll a lost run."""
    if state != 'lost':
        return state
    rc, out, _ = gh('run', 'view', str(run), '--attempt', str(attempt), '--log')
    if rc:
        return 'unread'
    return 'judged' if 'LAND_RESULT=' in out else 'lost'


def retry(run, attempt):
    """Re-run a lost attempt; True when a re-run was requested."""
    state = settle(run, attempt, classify(attempt_jobs(run, attempt)))
    if state != 'lost':
        print('%s run %s attempt %s: not re-run' % (state.upper(), run, attempt))
        return False
    if attempt >= MAX_ATTEMPTS:
        print('LOST run %s after %s attempts' % (run, attempt))
        return False
    rc, _, err = gh('run', 'rerun', str(run))
    print('RERUN run %s after lost attempt %s%s' % (run, attempt, '' if rc == 0 else ' refused: ' + err.strip()))
    return rc == 0


def land_line(run):
    """The last LAND_RESULT payload of a run's log; None when the log cannot be read or has no verdict line."""
    rc, out, _ = gh('run', 'view', str(run), '--log')
    lines = [l for l in out.splitlines() if 'LAND_RESULT=' in l] if rc == 0 else []
    return lines[-1].split('LAND_RESULT=', 1)[1] if lines else None


def unread_line(run):
    return 'UNREAD run %s: its log has no readable LAND_RESULT line' % run


def landed_of(result, sha):
    """The sha a green land put on main: its `landed=` field when the runner rebased, else the pushed commit."""
    m = re.search(r'\blanded=([0-9a-f]{40})', result)
    return m.group(1) if m else sha


def no_run_line(sha, seen):
    return 'NO RUN matches %s among %s' % (sha, ', '.join(sorted({r['headSha'] + ' (run %s)' % r['databaseId'] for r in seen})))


def git(path, *args):
    out = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=120, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    return out.returncode, out.stdout.strip(), ' '.join(out.stderr.split())


def stay_reason(path, default):
    """Why the checkout is not moved, or '' when nothing in it can be lost by moving it."""
    for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
        rc, p, _ = git(path, 'rev-parse', '--git-path', name)
        if rc == 0 and os.path.exists(os.path.join(path, p)):
            return 'a %s is in progress' % name
    branch = git(path, 'symbolic-ref', '-q', '--short', 'HEAD')[1]
    if branch != default:
        return 'on %s, not %s' % (branch or 'a detached HEAD', default)
    if git(path, 'diff', '--cached', '--quiet')[0]:
        return 'the index has staged changes (a peer may be mid-commit)'
    return ''


def catch_up(path=None):
    """Move the shared checkout onto origin/<default> after a land, when that loses nothing; return a status line.

    Behind with no local commits: `merge --ff-only`. Diverged with every local commit already upstream by patch
    (`git cherry` prints only `-`, and no local merge commit): `reset --keep`. Git refuses either move when an
    uncommitted file would be overwritten; the refusal is reported, never forced. A checkout with a local commit not
    upstream, off the default branch, mid-merge/rebase/cherry-pick/revert or holding a staged index stays put."""
    path = path or git('.', 'worktree', 'list', '--porcelain')[1].splitlines()[0][len('worktree '):]
    ref = git(path, 'symbolic-ref', '-q', '--short', 'refs/remotes/origin/HEAD')[1]
    default = ref.split('/', 1)[1] if ref else 'main'
    up = 'origin/' + default
    why = stay_reason(path, default)
    if not why:
        rc, _, err = git(path, 'fetch', '-q', 'origin', default)
        why = 'fetch failed: ' + err if rc else ''
    if why:
        return 'CATCH-UP not moved: ' + why
    behind, ahead = (int(n) for n in git(path, 'rev-list', '--left-right', '--count', up + '...HEAD')[1].split())
    if not behind:
        return 'CATCH-UP none: %s is current' % default
    if ahead:
        if git(path, 'rev-list', '--merges', '--count', up + '..HEAD')[1] != '0' or \
                any(l.startswith('+') for l in git(path, 'cherry', up, 'HEAD')[1].splitlines()):
            return 'CATCH-UP not moved: %s local commit(s) not upstream' % ahead
        move = git(path, 'reset', '--keep', up)
    else:
        move = git(path, 'merge', '--ff-only', up)
    if move[0]:
        return 'CATCH-UP not moved: git refused: ' + move[2]
    line = 'CATCH-UP moved %s to %s (%s behind)' % (default, git(path, 'rev-parse', '--short', 'HEAD')[1], behind)
    subs = sync_submodules(path)
    return line + '\n' + subs if subs else line


def sync_submodules(path):
    """Fast-forward each clean submodule checkout on master to the pointer the checkout now records; one line per submodule left, '' when there is nothing to move."""
    lines = []
    for entry in git(path, 'ls-tree', '-r', 'HEAD')[1].splitlines():
        meta, _, name = entry.partition('\t')
        mode, _, rest = meta.partition(' ')
        if mode != '160000':
            continue
        pin = rest.split()[1]
        line = sync_submodule(path, name, pin)
        if line:
            lines.append(line)
    return '\n'.join(lines)


def sync_submodule(path, name, pin):
    sub = os.path.join(path, name)
    if not os.path.exists(os.path.join(sub, '.git')):
        return ''
    here = git(sub, 'rev-parse', 'HEAD')[1]
    branch = git(sub, 'symbolic-ref', '-q', '--short', 'HEAD')[1]
    if here == pin:
        return ''
    if git(sub, 'status', '--porcelain')[1]:
        return '%s left: dirty' % name
    if branch != 'master':
        return '%s left: on %s, not master' % (name, branch or 'a detached HEAD')
    if git(sub, 'merge-base', '--is-ancestor', here, pin)[0]:
        return '%s left: its HEAD is not an ancestor of the pointer %s (or the commit is not fetched)' % (name, pin[:8])
    move = git(sub, 'merge', '--ff-only', pin)
    return '%s moved to %s' % (name, pin[:8]) if not move[0] else '%s left: git refused: %s' % (name, move[2])


def watch(branch, sha, timeout_min, no_match_min=NO_MATCH_MIN):
    deadline = time.time() + timeout_min * 60
    last = None
    unmatched_since = None
    while time.time() < deadline:
        rc, out, _ = gh('run', 'list', '--workflow', WORKFLOW, '--branch', branch, '-L', '20',
                        '--json', 'databaseId,headSha,status,conclusion,attempt')
        # GitHub reports the full sha; --sha may be the abbreviated one a push line or `git log --oneline` prints.
        seen = json.loads(out) if rc == 0 else []
        runs = [r for r in seen if r['headSha'].startswith(sha.lower())]
        if seen and not runs:
            if unmatched_since is None:
                unmatched_since = time.time()
                print(no_run_line(sha, seen), flush=True)
            elif time.time() - unmatched_since >= no_match_min * 60:
                print(no_run_line(sha, seen))
                return 2
        else:
            unmatched_since = None
        if runs:
            r = max(runs, key=lambda r: r['databaseId'])
            sha = r['headSha']
            state = (r['databaseId'], r['attempt'], r['status'])
            if state != last:
                print('RUN %s attempt %s %s' % state, flush=True)
                last = state
            if r['status'] == 'completed':
                if r['conclusion'] == 'success':
                    result = land_line(r['databaseId'])
                    if result is None:
                        print(unread_line(r['databaseId']))
                        return 2
                    by = re.search(r'^superseded\b.*\bby=(\S+)', result)
                    print('SUPERSEDED %s by %s' % (sha, by.group(1)) if by else 'LANDED %s' % landed_of(result, sha))
                    if not by:
                        try:
                            print(catch_up())
                        except Exception as err:
                            print('CATCH-UP not moved: %s' % err)
                    return 0
                if r['conclusion'] == 'cancelled':
                    print('CANCELLED run %s' % r['databaseId'])
                    return 1
                try:
                    state = settle(r['databaseId'], r['attempt'],
                                   classify(attempt_jobs(r['databaseId'], r['attempt'])))
                except RuntimeError as err:
                    print('UNREAD %s; polling again' % err, flush=True)
                    state = 'pending'
                if state == 'judged':
                    result = land_line(r['databaseId'])
                    print('RED %s' % result if result is not None else unread_line(r['databaseId']))
                    return 1 if result is not None else 2
                if state == 'unread':
                    print(unread_line(r['databaseId']))
                    return 2
                if state == 'pending':
                    time.sleep(60)
                    continue
                if r['attempt'] >= MAX_ATTEMPTS:
                    print('LOST run %s after %s attempts' % (r['databaseId'], r['attempt']))
                    return 1
                try:
                    retry(r['databaseId'], r['attempt'])
                except RuntimeError as err:
                    print('UNREAD %s; polling again' % err, flush=True)
        time.sleep(60)
    print('TIMEOUT no terminal land run for %s after %s min' % (sha, timeout_min))
    return 2


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('retry')
    r.add_argument('--run', required=True)
    r.add_argument('--attempt', required=True, type=int)
    w = sub.add_parser('watch')
    w.add_argument('--branch', required=True)
    w.add_argument('--sha', required=True)
    w.add_argument('--timeout-min', type=int, default=240)
    a = p.parse_args(argv)
    if a.cmd == 'retry':
        retry(a.run, a.attempt)
        return 0
    return watch(a.branch, a.sha, a.timeout_min)


if __name__ == '__main__':
    sys.exit(main())
