#!/usr/bin/env python3
"""land_watch re-runs a land job the runner lost and never one land_run.ps1 judged.

Job records are cut from live `gh api .../attempts/<n>/jobs` output: run 40906965306 attempt 1 (the runner killed
its worker before any step ran), run 40872523078 (land_run.ps1 judged the commit red) and run 41090216622 (the run
read `completed` while its job still read `in_progress` with no steps, between two HTTP 502s).
"""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import land_watch

# watch runs the shared-checkout catch-up on LANDED; the watch cases below plant it so they never touch this checkout.
real_catch_up = land_watch.catch_up
catch_calls = []
land_watch.catch_up = lambda *a: (catch_calls.append(a), 'CATCH-UP planted')[1]

LOST =[{"conclusion": "failure", "name": "land", "steps": []}]
CHECKOUT_FAILED = [{"conclusion": "failure", "name": "land", "steps": [
    {"conclusion": "success", "name": "Set up job"},
    {"conclusion": "failure", "name": "Check out the pushed commit in the land worktree"},
    {"conclusion": "skipped", "name": "Prove and land"}]}]
JUDGED_RED = [{"conclusion": "failure", "steps": [
    {"conclusion": "success", "name": "Set up job"},
    {"conclusion": "success", "name": "Check out the pushed commit in the land worktree"},
    {"conclusion": "failure", "name": "Prove and land"},
    {"conclusion": "success", "name": "Complete job"}]}]
STILL_RUNNING = [{"conclusion": None, "name": "land", "status": "in_progress", "steps": []}]
JUDGED_STEP_UNRECORDED = [{"conclusion": "failure", "name": "land", "status": "completed", "steps": [
    {"conclusion": "success", "name": "Check out the pushed commit in the land worktree"},
    {"conclusion": None, "name": "Prove and land"}]}]
RED_LOG = 'land\tUNKNOWN STEP\t2026-10-30T18:10:21Z LAND_RESULT=red stage=close sha=%s branch=land/t1 :: no green close\n'

failures = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        failures.append(name)


def run_retry(jobs, attempt, log=(0, '', '')):
    """retry over `jobs`, with `log` as the `gh run view --log` reply."""
    calls = []
    saved, land_watch.attempt_jobs = land_watch.attempt_jobs, lambda run, n: jobs
    land_watch.gh = lambda *a: (calls.append(a), log if a[:2] == ('run', 'view') else (0, '', ''))[1]
    try:
        land_watch.retry('1', attempt)
    finally:
        land_watch.attempt_jobs = saved
    return [c for c in calls if c[:2] == ('run', 'rerun')]


check('a job with no step run is lost', land_watch.classify(LOST) == 'lost')
check('a failed checkout is lost', land_watch.classify(CHECKOUT_FAILED) == 'lost')
check('a red from land_run.ps1 is judged', land_watch.classify(JUDGED_RED) == 'judged')
check('a lost attempt is re-run', run_retry(LOST, 1) == [('run', 'rerun', '1')])
check('a judged red is never re-run', run_retry(JUDGED_RED, 1) == [])
check('re-runs stop at MAX_ATTEMPTS', run_retry(LOST, land_watch.MAX_ATTEMPTS) == [])
check('a job still running is pending, not lost', land_watch.classify(STILL_RUNNING) == 'pending')
check('a job still running is never re-run', run_retry(STILL_RUNNING, 1) == [])
check('a run whose log carries LAND_RESULT is never re-run, though GitHub left its land step unrecorded',
      run_retry(JUDGED_STEP_UNRECORDED, 1, log=(0, RED_LOG % ('a' * 40), '')) == [])
check('a run whose log cannot be read is never re-run',
      run_retry(JUDGED_STEP_UNRECORDED, 1, log=(1, '', 'gh: Server Error (HTTP 502)')) == [])

PUSHED = 'a' * 40
REBASED = 'b' * 40
check('a green land reports its landed= sha when the runner rebased',
      land_watch.landed_of('green sha=%s branch=land/t1 landed=%s' % (PUSHED, REBASED), PUSHED) == REBASED)
check('a green land with no landed= field reports the pushed sha',
      land_watch.landed_of('green sha=%s branch=land/t1' % PUSHED, PUSHED) == PUSHED)


def run_watch(result_line, conclusion='success'):
    calls = []

    def fake_gh(*a):
        calls.append(a)
        if a[:2] == ('run', 'list'):
            return 0, json.dumps([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed',
                                   'conclusion': conclusion, 'attempt': 1}]), ''
        if a[:1] == ('api',):
            return 0, json.dumps({'jobs': LOST}), ''
        return 0, 'LAND_RESULT=%s\n' % result_line, ''
    land_watch.gh = fake_gh
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = land_watch.watch('land/t1', PUSHED, 1)
    return rc, buf.getvalue(), calls


rc, out, _ = run_watch('green sha=%s branch=land/t1 landed=%s' % (PUSHED, REBASED))
check('watch prints the landed= sha as its LANDED line', rc == 0 and out.splitlines()[-2] == 'LANDED ' + REBASED)
FIXTURE_LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures',
                                'land_close_log_excerpt.txt'), encoding='utf-8').read()  # cut from run 42795583929
TIME_LINE = ('land	Prove and land	2026-11-10T10:58:17.5555200Z   harness_tests: close time: pool 2156.0s; '
             'alone retries 6, 1172.4s; load x3.7 (18118.0s over 4910.0s fastest, 529 of 529 proofs with history)\n')


def watch_log(log, conclusion='success'):
    """watch over one completed run whose `gh run view --log` reply is `log`; the terminal line."""
    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            return 0, json.dumps([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed',
                                   'conclusion': conclusion, 'attempt': 1}]), ''
        if a[:1] == ('api',):
            return 0, json.dumps({'jobs': JUDGED_RED}), ''
        return 0, log, ''
    land_watch.gh = fake_gh
    land_watch.catch_up = lambda path=None: 'CATCH-UP planted'
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = land_watch.watch('land/t1', PUSHED, 1)
    return rc, [l for l in buf.getvalue().splitlines() if l.startswith(('LANDED', 'RED'))]


rc, lines = watch_log(FIXTURE_LOG)
check('a real land log with only the budget line adds the close seconds and budget to LANDED',
      rc == 0 and lines == ['LANDED %s (close 3371.0s of 300s budget)' % PUSHED])
rc, lines = watch_log(FIXTURE_LOG.replace('LAND_RESULT', TIME_LINE + 'LAND_RESULT', 1))
check('a log with the time line adds pool, alone retries and load to LANDED',
      rc == 0 and lines == ['LANDED %s (close 3371.0s of 300s budget; pool 2156.0s; alone retries 6, 1172.4s; '
                            'load x3.7 (18118.0s over 4910.0s fastest, 529 of 529 proofs with history))' % PUSHED])
rc, lines = watch_log('LAND_RESULT=red stage=close sha=%s branch=land/t1 :: new red\n' % PUSHED
                      + FIXTURE_LOG.split('LAND_RESULT')[0], conclusion='failure')
check('a red whose close finished carries the close facts too',
      rc == 1 and len(lines) == 1 and lines[0].startswith('RED red stage=close')
      and lines[0].endswith('(close 3371.0s of 300s budget)'))
rc, lines = watch_log('LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED)
check('a log with no close facts prints LANDED unchanged', rc == 0 and lines == ['LANDED ' + PUSHED])

rc, out, _ = run_watch('green sha=%s branch=land/t1 pre-existing=2 '
                       'test_instruction_quality_split.py,workflow_amend_line_test.js' % PUSHED)
check('a green land names the close\'s pre-existing reds, how many and which, on its LANDED line',
      rc == 0 and out.splitlines()[-2] == 'LANDED %s (2 pre-existing red: test_instruction_quality_split.py, '
      'workflow_amend_line_test.js)' % PUSHED)
rc, out, _ = run_watch('red stage=close sha=%s branch=land/t1 pre-existing=1 '
                       'test_instruction_quality_split.py :: no green close' % PUSHED, conclusion='failure')
check('a red close names the same pre-existing reds on its RED line',
      rc == 1 and out.splitlines()[-1].startswith('RED red stage=close')
      and 'pre-existing=1 test_instruction_quality_split.py' in out.splitlines()[-1])
rc, out, _ = run_watch('superseded sha=%s branch=land/t1 by=land/t2' % PUSHED)
check('a superseded land prints SUPERSEDED with the branch that carries it, not LANDED',
      rc == 0 and out.splitlines()[-1] == 'SUPERSEDED %s by land/t2' % PUSHED)
rc, out, calls = run_watch('', conclusion='cancelled')
check('a cancelled run is terminal: CANCELLED, never re-run as lost',
      rc == 1 and out.splitlines()[-1] == 'CANCELLED run 7' and not [c for c in calls if c[:2] == ('run', 'rerun')])


class _Spun(Exception):
    pass


def run_watch_short_sha(short):
    """watch given an abbreviated sha, as `git log --oneline` and a push line print it, over a green run whose
    headSha GitHub reports in full; a watch that never matches the run is stopped after a few polls."""
    lists = []

    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            lists.append(a)
            if len(lists) > 3:
                raise _Spun()
            return 0, json.dumps([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed',
                                   'conclusion': 'success', 'attempt': 1}]), ''
        return 0, 'LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, ''
    land_watch.gh = fake_gh
    saved_sleep, land_watch.time.sleep = land_watch.time.sleep, lambda _s: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = land_watch.watch('land/t1', short, 1)
    except _Spun:
        rc = 'spun'
    finally:
        land_watch.time.sleep = saved_sleep
    return rc, buf.getvalue()


rc, out = run_watch_short_sha(PUSHED[:9])
check('an abbreviated --sha finds its run and prints LANDED with the full sha, never polls to the timeout',
      rc == 0 and 'LANDED ' + PUSHED in out.splitlines())


def run_watch_jobs(replies):
    """watch over a completed red run whose job reads come back as `replies`, in order, the last one repeating: a
    reply is a jobs list or an HTTP 502 error text."""
    calls = []
    replies = list(replies)

    def fake_gh(*a):
        calls.append(a)
        if a[:2] == ('run', 'list'):
            return 0, json.dumps([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed',
                                   'conclusion': 'failure', 'attempt': 1}]), ''
        if a[:1] == ('api',):
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
            return (1, '', reply) if isinstance(reply, str) else (0, json.dumps({'jobs': reply}), '')
        return 0, 'LAND_RESULT=red stage=close sha=%s branch=land/t1 :: no green close\n' % PUSHED, ''
    land_watch.gh = fake_gh
    saved_sleep, land_watch.time.sleep = land_watch.time.sleep, lambda _s: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = land_watch.watch('land/t1', PUSHED, 1)
    finally:
        land_watch.time.sleep = saved_sleep
    return rc, buf.getvalue(), calls


BAD_GATEWAY = 'gh: Server Error (HTTP 502)'
rc, out, calls = run_watch_jobs([BAD_GATEWAY, STILL_RUNNING, BAD_GATEWAY, JUDGED_RED])
check('a 502 and a job still running between them never crash the watch or re-run a judged red',
      rc == 1 and out.splitlines()[-1].startswith('RED red stage=close')
      and not [c for c in calls if c[:2] == ('run', 'rerun')])
rc, out, calls = run_watch_jobs([JUDGED_STEP_UNRECORDED])
check('watch prints RED from the log when GitHub left the land step unrecorded, and re-runs nothing',
      rc == 1 and out.splitlines()[-1].startswith('RED red stage=close')
      and not [c for c in calls if c[:2] == ('run', 'rerun')])

def run_watch_unreadable(conclusion, jobs):
    """watch over one completed run whose `gh run view --log` fails (HTTP 502), on a fake clock: no real minutes pass."""
    import types
    now = [1000.0]
    fake_time = types.SimpleNamespace(time=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))

    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            return 0, json.dumps([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed',
                                   'conclusion': conclusion, 'attempt': 1}]), ''
        if a[:1] == ('api',):
            return 0, json.dumps({'jobs': jobs}), ''
        return 1, '', BAD_GATEWAY
    saved = land_watch.time
    land_watch.gh, land_watch.time = fake_gh, fake_time
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = land_watch.watch('land/t1', PUSHED, 30)
    finally:
        land_watch.time = saved
    return rc, buf.getvalue(), (now[0] - 1000.0) / 60


rc, out, _ = run_watch_unreadable('success', [])
check('a green run whose log cannot be read is UNREAD and exit 2, never LANDED on the pushed sha',
      rc == 2 and out.splitlines()[-1].startswith('UNREAD') and 'LANDED' not in out)
rc, out, mins = run_watch_unreadable('failure', LOST)
check('a red run whose log cannot be read is UNREAD and exit 2 at once, never polled to the timeout',
      rc == 2 and out.splitlines()[-1].startswith('UNREAD') and mins < 1)


def run_watch_clock(runs, sha, no_match_min=5, timeout_min=30, list_rc=0):
    """watch over a planted run list on a fake clock: each sleep advances it, so no real minutes pass."""
    import types
    now = [1000.0]
    fake_time = types.SimpleNamespace(time=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))

    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            return (list_rc, json.dumps(runs), '') if list_rc == 0 else (list_rc, '', 'boom')
        return 0, 'LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, ''
    saved = land_watch.time
    land_watch.gh, land_watch.time = fake_gh, fake_time
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = land_watch.watch('land/t1', sha, timeout_min, no_match_min)
    finally:
        land_watch.time = saved
    return rc, buf.getvalue(), (now[0] - 1000.0) / 60


OTHER_RUN = {'databaseId': 9, 'headSha': REBASED, 'status': 'completed', 'conclusion': 'success', 'attempt': 1}
rc, out, mins = run_watch_clock([OTHER_RUN], 'c' * 40)
lines = out.splitlines()
check('runs present and none match the sha: NO RUN prints once while polling and once at the exit, naming the shas seen',
      len([l for l in lines if l.startswith('NO RUN')]) == 2 and ('NO RUN matches ' + 'c' * 40) in out and REBASED in out)
check('runs present and none match: exit 2 after the window, not the full timeout',
      rc == 2 and lines[-1].startswith('NO RUN') and 5 <= mins < 7)
rc, out, mins = run_watch_clock([], 'c' * 40)
check('no runs at all keeps today\'s wait: TIMEOUT at the full timeout, no NO RUN line',
      rc == 2 and out.splitlines()[-1].startswith('TIMEOUT') and 'NO RUN' not in out and mins >= 30)
MINE = dict(OTHER_RUN, databaseId=10, headSha=PUSHED)
rc, out, mins = run_watch_clock([OTHER_RUN, MINE], PUSHED)
check('a sha that matches no run still lands when a matching run exists among others',
      rc == 0 and 'NO RUN' not in out)
rc, out, mins = run_watch_clock([], 'c' * 40, timeout_min=2, list_rc=1)
check('a failed list read is not a NO RUN', 'NO RUN' not in out and rc == 2)

rc, out, _ = run_watch('green sha=%s branch=land/t1 landed=%s' % (PUSHED, REBASED))
check('LANDED runs the catch-up and prints its line after LANDED',
      catch_calls and out.splitlines()[-2:] == ['LANDED ' + REBASED, 'CATCH-UP planted'])
del catch_calls[:]
run_watch('superseded sha=%s branch=land/t1 by=land/t2' % PUSHED)
run_watch('', conclusion='cancelled')
check('SUPERSEDED and CANCELLED never run the catch-up: the commits are not upstream yet', not catch_calls)

# --- catch-up: real git, a bare origin, a clone as the shared checkout `s`, a peer clone `p` that pushes ----------
# The fixtures are built once and copied per case; the cases run on threads, since each is git spawns.
import itertools
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from _case_pool import _jobs as _case_jobs

TMP = tempfile.mkdtemp(prefix='land_watch_')
os.environ['GIT_SEQUENCE_EDITOR'] = 'true'


def g(cwd, *args):
    r = subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t',
                        '-c', 'commit.gpgsign=false', *args], capture_output=True, text=True)
    return r.stdout.strip()


def put(cwd, name, text):
    with open(os.path.join(cwd, name), 'w', newline='\n') as f:
        f.write(text)


def commit(cwd, name, text):
    put(cwd, name, text)
    g(cwd, 'add', name)
    g(cwd, 'commit', '-q', '-m', 'add ' + name)


def head(cwd):
    return g(cwd, 'rev-parse', 'HEAD')


def read(*parts):
    with open(os.path.join(*parts)) as f:
        return f.read()


def pick(p, s, rev):
    """cherry-pick `rev` from the shared checkout onto the peer's branch and push it: the rebased land."""
    g(p, 'fetch', '-q', s, rev)
    g(p, 'cherry-pick', 'FETCH_HEAD')
    g(p, 'push', '-q', 'origin', 'main')


def peer_pushes(p, name, text):
    commit(p, name, text)
    g(p, 'push', '-q', 'origin', 'main')


def template():
    """origin.git at `base` (f.txt, g.txt), the shared checkout `s` cloned from it, and the peer clone `p`."""
    root = os.path.join(TMP, 'tpl')
    origin, s, p = (os.path.join(root, x) for x in ('origin.git', 's', 'p'))
    os.makedirs(root)
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', origin], check=True)
    subprocess.run(['git', 'clone', '-q', origin, p], check=True, capture_output=True)
    g(p, 'checkout', '-q', '-b', 'main')
    commit(p, 'f.txt', 'base\n')
    commit(p, 'g.txt', 'base\n')
    g(p, 'push', '-q', 'origin', 'main')
    subprocess.run(['git', 'clone', '-q', origin, s], check=True, capture_output=True)
    g(s, 'remote', 'set-head', 'origin', 'main')
    return root


def jmodot_template(plain):
    """`plain` plus a Jmodot repo: the peer has pushed a gitlink at its commit `second`; `s` holds Jmodot at `first`."""
    root = os.path.join(TMP, 'tplj')
    shutil.copytree(plain, root)
    for x in ('s', 'p'):
        g(os.path.join(root, x), 'remote', 'set-url', 'origin', os.path.join(root, 'origin.git'))
    jm = os.path.join(root, 'jm')
    subprocess.run(['git', 'init', '-q', '-b', 'master', jm], check=True)
    commit(jm, 'j.txt', '1\n')
    first = head(jm)
    commit(jm, 'j.txt', '2\n')
    second = head(jm)
    subprocess.run(['git', 'clone', '-q', jm, os.path.join(root, 'sj')], check=True, capture_output=True)
    g(os.path.join(root, 'sj'), 'reset', '-q', '--hard', first)
    p = os.path.join(root, 'p')
    g(p, 'update-index', '--add', '--cacheinfo', '160000,%s,Jmodot' % second)
    g(p, 'commit', '-q', '-m', 'pointer')
    g(p, 'push', '-q', 'origin', 'main')
    return root, second


_n = itertools.count()
PLAIN = template()
JMODOT, SECOND = jmodot_template(PLAIN)


def world(tpl=PLAIN):
    root = os.path.join(TMP, 'w%d' % next(_n))
    shutil.copytree(tpl, root)
    origin = os.path.join(root, 'origin.git')
    for x in ('s', 'p'):
        g(os.path.join(root, x), 'remote', 'set-url', 'origin', origin)
    s = os.path.join(root, 's')
    if tpl is JMODOT:
        shutil.move(os.path.join(root, 'sj'), os.path.join(s, 'Jmodot'))
    return s, os.path.join(root, 'p')


def moves_behind_keeping_dirty():
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    put(s, 'notes.txt', 'peer dirty\n')
    line = real_catch_up(s)
    return head(s) == head(p) and os.path.exists(os.path.join(s, 'notes.txt')) and line.startswith('CATCH-UP moved')


def keep_resets_patch_equivalent():
    s, p = world()
    commit(s, 'a.txt', 'a\n')
    peer_pushes(p, 'b.txt', 'b\n')
    pick(p, s, 'HEAD')
    put(s, 'notes.txt', 'peer dirty\n')
    put(s, 'g.txt', 'peer edit\n')
    line = real_catch_up(s)
    return (head(s) == head(p) and read(s, 'g.txt') == 'peer edit\n' and os.path.exists(os.path.join(s, 'notes.txt'))
            and line.startswith('CATCH-UP moved'))


def stays_unique_local_commit():
    s, p = world()
    commit(s, 'mine.txt', 'mine\n')
    peer_pushes(p, 'a.txt', 'a\n')
    before = head(s)
    return 'not upstream' in real_catch_up(s) and head(s) == before


def stays_local_merge_commit():
    s, p = world()
    g(s, 'checkout', '-q', '-b', 'side')
    commit(s, 'x.txt', 'x\n')
    g(s, 'checkout', '-q', 'main')
    commit(s, 'a.txt', 'a\n')
    g(s, 'merge', '-q', '--no-ff', '-m', 'merge side', 'side')
    for n in ('x.txt', 'a.txt'):
        pick(p, s, g(s, 'log', '-1', '--format=%H', '--grep=add ' + n))
    peer_pushes(p, 'b.txt', 'b\n')
    before = head(s)
    return 'not moved' in real_catch_up(s) and head(s) == before


def stays_staged_index():
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    put(s, 'staged.txt', 's\n')
    g(s, 'add', 'staged.txt')
    before = head(s)
    return 'staged' in real_catch_up(s) and head(s) == before


def stays_off_default_branch():
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(s, 'checkout', '-q', '-b', 'side')
    before = head(s)
    return 'side' in real_catch_up(s) and head(s) == before


def stays_mid_rebase():
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(s, 'rebase', '-i', '--exec', 'false', 'HEAD~1')
    before = head(s)
    return 'in progress' in real_catch_up(s) and head(s) == before


def stays_mid_merge():
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(p, 'checkout', '-q', '-b', 'other', 'HEAD~1')
    commit(p, 'c.txt', 'c\n')
    g(p, 'push', '-q', 'origin', 'other')
    g(s, 'fetch', '-q', 'origin')
    g(s, 'merge', '--no-commit', '--no-ff', 'origin/other')
    before = head(s)
    return 'in progress' in real_catch_up(s) and head(s) == before


def refuses_to_overwrite_on_ff():
    s, p = world()
    commit(p, 'f.txt', 'upstream\n')
    g(p, 'push', '-q', 'origin', 'main')
    put(s, 'f.txt', 'my dirty edit\n')
    before = head(s)
    return 'refused' in real_catch_up(s) and head(s) == before and read(s, 'f.txt') == 'my dirty edit\n'


def refuses_to_overwrite_on_keep_reset():
    s, p = world()
    commit(s, 'a.txt', 'a\n')
    commit(p, 'f.txt', 'upstream\n')
    g(p, 'push', '-q', 'origin', 'main')
    pick(p, s, 'HEAD')
    put(s, 'f.txt', 'my dirty edit\n')
    before = head(s)
    return 'refused' in real_catch_up(s) and head(s) == before and read(s, 'f.txt') == 'my dirty edit\n'


def jmodot_fast_forwards():
    s, _ = world(JMODOT)
    real_catch_up(s)
    return head(os.path.join(s, 'Jmodot')) == SECOND


def jmodot_dirty_left():
    s, _ = world(JMODOT)
    put(os.path.join(s, 'Jmodot'), 'j.txt', 'dirty\n')
    line = real_catch_up(s)
    return head(os.path.join(s, 'Jmodot')) != SECOND and [l for l in line.splitlines() if 'Jmodot' in l] == [
        'Jmodot left: dirty']


def jmodot_off_master_left():
    s, _ = world(JMODOT)
    g(os.path.join(s, 'Jmodot'), 'checkout', '-q', '-b', 'feature')
    return head(os.path.join(s, 'Jmodot')) != SECOND and 'feature' in real_catch_up(s)


CASES = [
    ('behind with no local commits: fast-forwards and keeps an unrelated dirty file', moves_behind_keeping_dirty),
    ('diverged, every local commit upstream by patch: keep-resets onto origin, keeping uncommitted edits',
     keep_resets_patch_equivalent),
    ('a local commit not upstream: nothing moves', stays_unique_local_commit),
    ('a local merge commit, which git cherry skips: nothing moves', stays_local_merge_commit),
    ('a staged index (a peer may be mid-commit): nothing moves', stays_staged_index),
    ('a branch other than the default: nothing moves', stays_off_default_branch),
    ('a rebase in progress: nothing moves', stays_mid_rebase),
    ('a merge in progress: nothing moves', stays_mid_merge),
    ('behind, a dirty file the fast-forward would overwrite: git refuses, edit kept, nothing forced',
     refuses_to_overwrite_on_ff),
    ('diverged, a dirty file the keep-reset would overwrite: git refuses, edit kept, nothing forced',
     refuses_to_overwrite_on_keep_reset),
    ('Jmodot clean, on master, behind the new pointer: fast-forwarded', jmodot_fast_forwards),
    ('Jmodot dirty: left, one line says why', jmodot_dirty_left),
    ('Jmodot on a branch other than master: left, one line says why', jmodot_off_master_left),
]


def run_case(case):
    try:
        return bool(case[1]())
    except Exception as err:
        print('  %s: %s' % (case[1].__name__, err))
        return False


with ThreadPoolExecutor(max_workers=_case_jobs(None)) as pool:  # _case_pool owns the width (HARNESS_CASE_JOBS)
    for case, ok in zip(CASES, pool.map(run_case, CASES)):
        check(case[0], ok)

shutil.rmtree(TMP, ignore_errors=True)

if failures:
    print('\n%d FAILED' % len(failures))
    sys.exit(1)
print('\nall passed')
