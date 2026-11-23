"""land_watch re-runs a land job the runner lost and never one land_run.ps1 judged.

Job records are cut from live `gh api .../attempts/<n>/jobs` output: run 40906965306 attempt 1 (the runner killed
its worker before any step ran), run 40872523078 (land_run.ps1 judged the commit red) and run 41090216622 (the run
read `completed` while its job still read `in_progress` with no steps, between two HTTP 502s). Those runs predate the
hosted land: their land step carried the name the old workflow gave it, and the records here name it `Land`, which is
what `land.yml` calls it now.

The watch cases plant `gh`, the clock and the catch-up, so no process runs. The catch-up cases run real git on copies of
two module-scoped fixtures (a bare origin, the shared checkout `s`, a peer clone `p`; the same plus a Jmodot repo).
"""
import contextlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import land_watch  # noqa: E402

LOST = [{"conclusion": "failure", "name": "land", "steps": []}]
CHECKOUT_FAILED = [{"conclusion": "failure", "name": "land", "steps": [
    {"conclusion": "success", "name": "Set up job"},
    {"conclusion": "failure", "name": "Check out the pushed commit"},
    {"conclusion": "skipped", "name": "Land"}]}]
JUDGED_RED = [{"conclusion": "failure", "steps": [
    {"conclusion": "success", "name": "Set up job"},
    {"conclusion": "success", "name": "Check out the pushed commit"},
    {"conclusion": "failure", "name": "Land"},
    {"conclusion": "success", "name": "Complete job"}]}]
STILL_RUNNING = [{"conclusion": None, "name": "land", "status": "in_progress", "steps": []}]
JUDGED_STEP_UNRECORDED = [{"conclusion": "failure", "name": "land", "status": "completed", "steps": [
    {"conclusion": "success", "name": "Check out the pushed commit"},
    {"conclusion": None, "name": "Land"}]}]
RED_LOG = 'land\tUNKNOWN STEP\t2026-10-30T18:10:21Z LAND_RESULT=red stage=close sha=%s branch=land/t1 :: no green close\n'
PUSHED = 'a' * 40
REBASED = 'b' * 40
BAD_GATEWAY = 'gh: Server Error (HTTP 502)'
OTHER_RUN = {'databaseId': 9, 'headSha': REBASED, 'status': 'completed', 'conclusion': 'success', 'attempt': 1}
# cut from run 42795583929
FIXTURE_LOG = open(os.path.join(HERE, 'fixtures', 'land_close_log_excerpt.txt'), encoding='utf-8').read()
TIME_LINE = ('land	Land	2026-11-10T10:58:17.5555200Z   harness_tests: close time: pool 2156.0s; '
             'alone retries 6, 1172.4s; load x3.7 (18118.0s over 4910.0s fastest, 529 of 529 proofs with history)\n')
GROWTH_LINE = ('land	Land	2026-11-11T18:40:01.0000000Z   harness_tests: close growth: '
               'test_a.py 187.3s vs at most 93.6s before (3 closes); test_b.py 46.0s vs at most 6.6s before '
               '(49 closes) (load-normalized)\n')


def run_entry(runs):
    """A `gh run list` reply for one run."""
    return 0, json.dumps(runs), ''


class Clock:
    """A fake `time` module: each sleep advances it, so no real minutes pass."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    @property
    def minutes(self):
        return (self.now - 1000.0) / 60


@pytest.fixture
def catch_calls(monkeypatch):
    """watch runs the shared-checkout catch-up on LANDED; planted, so the watch cases never touch this checkout."""
    calls = []
    monkeypatch.setattr(land_watch, 'catch_up', lambda *a: (calls.append(a), 'CATCH-UP planted')[1])
    return calls


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(land_watch, 'time', fake)
    return fake


@pytest.fixture
def watch_with(monkeypatch, clock, catch_calls):
    """`watch_with(fake_gh, sha=PUSHED, timeout_min=1, ...)` runs the watch on a fake gh and clock; (rc, stdout)."""
    def run(fake_gh, sha=PUSHED, timeout_min=1, *extra):
        monkeypatch.setattr(land_watch, 'gh', fake_gh)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = land_watch.watch('land/t1', sha, timeout_min, *extra)
        return rc, buf.getvalue()
    return run


def completed(conclusion='success'):
    return run_entry([{'databaseId': 7, 'headSha': PUSHED, 'status': 'completed', 'conclusion': conclusion,
                       'attempt': 1}])


def gh_for(conclusion='success', jobs=LOST, result='', log=None, calls=None):
    """A fake gh over one completed run: its job list, and `LAND_RESULT=<result>` (or `log`) from the run log."""
    def fake_gh(*a):
        if calls is not None:
            calls.append(a)
        if a[:2] == ('run', 'list'):
            return completed(conclusion)
        if a[:1] == ('api',):
            return 0, json.dumps({'jobs': jobs}), ''
        return 0, log if log is not None else 'LAND_RESULT=%s\n' % result, ''
    return fake_gh


def reruns(calls):
    return [c for c in calls if c[:2] == ('run', 'rerun')]


# classify and retry

@pytest.mark.parametrize('jobs,verdict', [
    pytest.param(LOST, 'lost', id='a job with no step run is lost'),
    pytest.param(CHECKOUT_FAILED, 'lost', id='a failed checkout is lost'),
    pytest.param(JUDGED_RED, 'judged', id='a red from land_run.ps1 is judged'),
    pytest.param(STILL_RUNNING, 'pending', id='a job still running is pending, not lost'),
])
def test_classify(jobs, verdict):
    assert land_watch.classify(jobs) == verdict


def retry_reruns(monkeypatch, jobs, attempt, log=(0, '', '')):
    """retry over `jobs`, with `log` as the `gh run view --log` reply; the `run rerun` calls it made."""
    calls = []
    monkeypatch.setattr(land_watch, 'attempt_jobs', lambda run, n: jobs)
    monkeypatch.setattr(land_watch, 'gh', lambda *a: (calls.append(a), log if a[:2] == ('run', 'view') else (0, '', ''))[1])
    land_watch.retry('1', attempt)
    return reruns(calls)


@pytest.mark.parametrize('jobs,attempt,log,rerun', [
    pytest.param(LOST, 1, (0, '', ''), True, id='a lost attempt is re-run'),
    pytest.param(JUDGED_RED, 1, (0, '', ''), False, id='a judged red is never re-run'),
    pytest.param(LOST, land_watch.MAX_ATTEMPTS, (0, '', ''), False, id='re-runs stop at MAX_ATTEMPTS'),
    pytest.param(STILL_RUNNING, 1, (0, '', ''), False, id='a job still running is never re-run'),
    pytest.param(JUDGED_STEP_UNRECORDED, 1, (0, RED_LOG % PUSHED, ''), False,
                 id='a run whose log carries LAND_RESULT is never re-run, though its land step is unrecorded'),
    pytest.param(JUDGED_STEP_UNRECORDED, 1, (1, '', BAD_GATEWAY), False, id='a run whose log cannot be read is never re-run'),
])
def test_retry(monkeypatch, jobs, attempt, log, rerun):
    assert retry_reruns(monkeypatch, jobs, attempt, log) == ([('run', 'rerun', '1')] if rerun else [])


def test_landed_of_names_the_rebased_sha_or_the_pushed_one():
    assert land_watch.landed_of('green sha=%s branch=land/t1 landed=%s' % (PUSHED, REBASED), PUSHED) == REBASED
    assert land_watch.landed_of('green sha=%s branch=land/t1' % PUSHED, PUSHED) == PUSHED


# watch

def test_watch_prints_the_landed_sha_runs_the_catch_up_after_it(watch_with, catch_calls):
    rc, out = watch_with(gh_for(jobs=LOST, result='green sha=%s branch=land/t1 landed=%s' % (PUSHED, REBASED)))
    assert rc == 0 and out.splitlines()[-2:] == ['LANDED ' + REBASED, 'CATCH-UP planted']
    assert catch_calls


@pytest.mark.parametrize('log,conclusion,rc,lines', [
    pytest.param(FIXTURE_LOG, 'success', 0, ['LANDED ' + PUSHED],
                 id='a land log that parses: LANDED names the pushed sha when the result has no landed= field'),
    pytest.param(FIXTURE_LOG.replace('LAND_RESULT', TIME_LINE + GROWTH_LINE + 'LAND_RESULT', 1), 'success', 0,
                 ['LANDED ' + PUSHED], id='a close wall, time and growth line adds none of them to LANDED'),
    pytest.param('LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, 'success', 0, ['LANDED ' + PUSHED],
                 id='a log with only the result line prints LANDED'),
    pytest.param('LAND_RESULT=red stage=rebase sha=%s branch=land/t1 :: conflicts in notes.md\n' % PUSHED, 'failure', 1,
                 ['RED red stage=rebase sha=%s branch=land/t1 :: conflicts in notes.md' % PUSHED],
                 id='a red names its stage line and nothing else'),
])
def test_watch_terminal_line_from_the_run_log(watch_with, log, conclusion, rc, lines):
    got, out = watch_with(gh_for(conclusion, jobs=JUDGED_RED, log=log))
    assert got == rc and [l for l in out.splitlines() if l.startswith(('LANDED', 'RED'))] == lines


def test_a_cancelled_run_is_terminal_and_never_re_run_or_caught_up(watch_with, catch_calls):
    calls = []
    rc, out = watch_with(gh_for('cancelled', calls=calls))
    assert rc == 1 and out.splitlines()[-1] == 'CANCELLED run 7' and not reruns(calls)
    assert not catch_calls, 'the commits are not upstream yet'


def test_an_abbreviated_sha_finds_its_run_and_prints_the_full_sha(watch_with):
    # A watch that never matches the run is stopped after a few polls, never run to the timeout.
    lists = []

    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            lists.append(a)
            assert len(lists) <= 3, 'polled instead of matching the abbreviated sha'
            return completed()
        return 0, 'LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, ''
    rc, out = watch_with(fake_gh, PUSHED[:9])
    assert rc == 0 and 'LANDED ' + PUSHED in out.splitlines()


@pytest.mark.parametrize('replies,id_', [
    pytest.param([BAD_GATEWAY, STILL_RUNNING, BAD_GATEWAY, JUDGED_RED], '502s and a running job', id='502-and-running'),
    pytest.param([JUDGED_STEP_UNRECORDED], 'the land step unrecorded', id='step-unrecorded'),
])
def test_a_red_reads_from_the_log_whatever_the_job_reads_say(watch_with, clock, monkeypatch, replies, id_):
    """job reads come back as `replies` in order, the last repeating: a jobs list or an HTTP 502 error text."""
    monkeypatch.setattr(clock, 'sleep', lambda seconds: None)  # the retry backoff costs no time here
    calls, queue = [], list(replies)

    def fake_gh(*a):
        calls.append(a)
        if a[:2] == ('run', 'list'):
            return completed('failure')
        if a[:1] == ('api',):
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            return (1, '', reply) if isinstance(reply, str) else (0, json.dumps({'jobs': reply}), '')
        return 0, 'LAND_RESULT=red stage=close sha=%s branch=land/t1 :: no green close\n' % PUSHED, ''
    rc, out = watch_with(fake_gh)
    assert rc == 1 and out.splitlines()[-1].startswith('RED red stage=close') and not reruns(calls)


@pytest.mark.parametrize('conclusion,jobs,max_minutes', [
    pytest.param('success', [], None, id='green: UNREAD, never LANDED on the pushed sha'),
    pytest.param('failure', LOST, 1, id='red: UNREAD at once, never polled to the timeout'),
])
def test_a_run_whose_log_cannot_be_read_is_unread_with_exit_2(watch_with, clock, conclusion, jobs, max_minutes):
    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            return completed(conclusion)
        if a[:1] == ('api',):
            return 0, json.dumps({'jobs': jobs}), ''
        return 1, '', BAD_GATEWAY
    rc, out = watch_with(fake_gh, PUSHED, 30)
    assert rc == 2 and out.splitlines()[-1].startswith('UNREAD') and 'LANDED' not in out
    if max_minutes:
        assert clock.minutes < max_minutes


def watch_runs(watch_with, runs, sha, timeout_min=30, list_rc=0):
    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            return (list_rc, json.dumps(runs), '') if list_rc == 0 else (list_rc, '', 'boom')
        return 0, 'LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, ''
    return watch_with(fake_gh, sha, timeout_min, 5)


def test_runs_present_and_none_match_the_sha_is_no_run_after_the_window(watch_with, clock):
    rc, out = watch_runs(watch_with, [OTHER_RUN], 'c' * 40)
    lines = out.splitlines()
    assert len([l for l in lines if l.startswith('NO RUN')]) == 2, 'once while polling, once at the exit'
    assert ('NO RUN matches ' + 'c' * 40) in out and REBASED in out
    assert rc == 2 and lines[-1].startswith('NO RUN') and 5 <= clock.minutes < 7


def test_no_runs_at_all_waits_the_full_timeout(watch_with, clock):
    rc, out = watch_runs(watch_with, [], 'c' * 40)
    assert rc == 2 and out.splitlines()[-1].startswith('TIMEOUT') and 'NO RUN' not in out and clock.minutes >= 30


def test_a_matching_run_among_others_still_lands(watch_with):
    rc, out = watch_runs(watch_with, [OTHER_RUN, dict(OTHER_RUN, databaseId=10, headSha=PUSHED)], PUSHED)
    assert rc == 0 and 'NO RUN' not in out


def test_a_failed_list_read_is_not_a_no_run(watch_with):
    rc, out = watch_runs(watch_with, [], 'c' * 40, timeout_min=2, list_rc=1)
    assert 'NO RUN' not in out and rc == 2


@pytest.fixture(scope='module')
def live_repo(tmp_path_factory):
    """A primary checkout holding a close log a land runner left behind: the watch must forward nothing from it."""
    repo = tmp_path_factory.mktemp('land_watch_live') / 'primary'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    log_at = repo / '.claude' / 'worktrees' / 'land-runner' / '.claude' / 'scratch' / 'land_close.log'
    log_at.parent.mkdir(parents=True)
    failed = 'FAIL 84.2s C:/r/.claude/tests/test_layer_closure.py'
    first = ('FIRST RED test_layer_closure.py at f3b2d7bd3 fix(close): retry order; '
             'next: fix this commit, or hand it to its owner\'s fix job')
    log_at.write_text('LAND_CLOSE sha=%s branch=land/t1\n%s\n%s\n' % (PUSHED, failed, first), encoding='utf-8', newline='')
    return repo


def test_no_live_close_lines_are_forwarded_while_the_run_runs(watch_with, live_repo, monkeypatch):
    polls, lists = 3, [0]

    def fake_gh(*a):
        if a[:2] == ('run', 'list'):
            lists[0] += 1
            status, conclusion = ('in_progress', None) if lists[0] <= polls else ('completed', 'success')
            return run_entry([{'databaseId': 7, 'headSha': PUSHED, 'status': status, 'conclusion': conclusion,
                               'attempt': 1}])
        return 0, 'LAND_RESULT=green sha=%s branch=land/t1\n' % PUSHED, ''
    monkeypatch.chdir(live_repo)
    rc, out = watch_with(fake_gh, PUSHED, 60)
    assert 'LIVE' not in out
    assert rc == 0 and out.splitlines()[-2:] == ['LANDED ' + PUSHED, 'CATCH-UP planted']
    assert not any(hasattr(land_watch, name) for name in ('LiveClose', 'live_close', 'close_log_path', 'close_facts_of',
                                                          'pre_existing_of', 'LIVE_POLL_S'))


# catch-up: real git, a bare origin, a clone as the shared checkout `s`, a peer clone `p` that pushes

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


@pytest.fixture(scope='module', autouse=True)
def _no_sequence_editor():
    saved = os.environ.get('GIT_SEQUENCE_EDITOR')
    os.environ['GIT_SEQUENCE_EDITOR'] = 'true'
    yield
    if saved is None:
        os.environ.pop('GIT_SEQUENCE_EDITOR')
    else:
        os.environ['GIT_SEQUENCE_EDITOR'] = saved


@pytest.fixture(scope='module')
def templates(tmp_path_factory):
    """`plain`: origin.git at `base` (f.txt, g.txt), the shared checkout `s` cloned from it, the peer clone `p`.
    `jmodot`: `plain` plus a Jmodot repo: the peer has pushed a gitlink at its commit `second`; `s` holds Jmodot at `first`."""
    tmp = tmp_path_factory.mktemp('land_watch_tpl')
    plain = str(tmp / 'tpl')
    origin, s, p = (os.path.join(plain, x) for x in ('origin.git', 's', 'p'))
    os.makedirs(plain)
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', origin], check=True)
    subprocess.run(['git', 'clone', '-q', origin, p], check=True, capture_output=True)
    g(p, 'checkout', '-q', '-b', 'main')
    commit(p, 'f.txt', 'base\n')
    commit(p, 'g.txt', 'base\n')
    g(p, 'push', '-q', 'origin', 'main')
    subprocess.run(['git', 'clone', '-q', origin, s], check=True, capture_output=True)
    g(s, 'remote', 'set-head', 'origin', 'main')

    jroot = str(tmp / 'tplj')
    shutil.copytree(plain, jroot)
    for x in ('s', 'p'):
        g(os.path.join(jroot, x), 'remote', 'set-url', 'origin', os.path.join(jroot, 'origin.git'))
    jm = os.path.join(jroot, 'jm')
    subprocess.run(['git', 'init', '-q', '-b', 'master', jm], check=True)
    commit(jm, 'j.txt', '1\n')
    first = head(jm)
    commit(jm, 'j.txt', '2\n')
    second = head(jm)
    subprocess.run(['git', 'clone', '-q', jm, os.path.join(jroot, 'sj')], check=True, capture_output=True)
    g(os.path.join(jroot, 'sj'), 'reset', '-q', '--hard', first)
    jp = os.path.join(jroot, 'p')
    g(jp, 'update-index', '--add', '--cacheinfo', '160000,%s,Jmodot' % second)
    g(jp, 'commit', '-q', '-m', 'pointer')
    g(jp, 'push', '-q', 'origin', 'main')
    return types.SimpleNamespace(plain=plain, jmodot=jroot, second=second)


@pytest.fixture
def world(templates, tmp_path):
    """`world(jmodot=False)`: a copy of a template with its remotes pointed at the copy; (s, p)."""
    count = itertools.count()

    def make(jmodot=False):
        root = str(tmp_path / ('w%d' % next(count)))
        shutil.copytree(templates.jmodot if jmodot else templates.plain, root)
        origin = os.path.join(root, 'origin.git')
        for x in ('s', 'p'):
            g(os.path.join(root, x), 'remote', 'set-url', 'origin', origin)
        s = os.path.join(root, 's')
        if jmodot:
            shutil.move(os.path.join(root, 'sj'), os.path.join(s, 'Jmodot'))
        return s, os.path.join(root, 'p')
    return make


def test_behind_with_no_local_commits_fast_forwards_and_keeps_an_unrelated_dirty_file(world):
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    put(s, 'notes.txt', 'peer dirty\n')
    line = land_watch.catch_up(s)
    assert head(s) == head(p) and os.path.exists(os.path.join(s, 'notes.txt')) and line.startswith('CATCH-UP moved')


def test_diverged_with_every_local_commit_upstream_by_patch_keep_resets_keeping_edits(world):
    s, p = world()
    commit(s, 'a.txt', 'a\n')
    peer_pushes(p, 'b.txt', 'b\n')
    pick(p, s, 'HEAD')
    put(s, 'notes.txt', 'peer dirty\n')
    put(s, 'g.txt', 'peer edit\n')
    line = land_watch.catch_up(s)
    assert head(s) == head(p) and read(s, 'g.txt') == 'peer edit\n'
    assert os.path.exists(os.path.join(s, 'notes.txt')) and line.startswith('CATCH-UP moved')


def test_a_local_commit_not_upstream_moves_nothing(world):
    s, p = world()
    commit(s, 'mine.txt', 'mine\n')
    peer_pushes(p, 'a.txt', 'a\n')
    before = head(s)
    assert 'not upstream' in land_watch.catch_up(s) and head(s) == before


def test_a_local_merge_commit_which_git_cherry_skips_moves_nothing(world):
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
    assert 'not moved' in land_watch.catch_up(s) and head(s) == before


def test_a_staged_index_moves_nothing(world):
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    put(s, 'staged.txt', 's\n')
    g(s, 'add', 'staged.txt')
    before = head(s)
    assert 'staged' in land_watch.catch_up(s) and head(s) == before


def test_a_branch_other_than_the_default_moves_nothing(world):
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(s, 'checkout', '-q', '-b', 'side')
    before = head(s)
    assert 'side' in land_watch.catch_up(s) and head(s) == before


def test_a_rebase_in_progress_moves_nothing(world):
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(s, 'rebase', '-i', '--exec', 'false', 'HEAD~1')
    before = head(s)
    assert 'in progress' in land_watch.catch_up(s) and head(s) == before


def test_a_merge_in_progress_moves_nothing(world):
    s, p = world()
    peer_pushes(p, 'a.txt', 'a\n')
    g(p, 'checkout', '-q', '-b', 'other', 'HEAD~1')
    commit(p, 'c.txt', 'c\n')
    g(p, 'push', '-q', 'origin', 'other')
    g(s, 'fetch', '-q', 'origin')
    g(s, 'merge', '--no-commit', '--no-ff', 'origin/other')
    before = head(s)
    assert 'in progress' in land_watch.catch_up(s) and head(s) == before


def test_behind_with_a_dirty_file_the_fast_forward_would_overwrite_git_refuses(world):
    s, p = world()
    commit(p, 'f.txt', 'upstream\n')
    g(p, 'push', '-q', 'origin', 'main')
    put(s, 'f.txt', 'my dirty edit\n')
    before = head(s)
    assert 'refused' in land_watch.catch_up(s) and head(s) == before and read(s, 'f.txt') == 'my dirty edit\n'


def test_diverged_with_a_dirty_file_the_keep_reset_would_overwrite_git_refuses(world):
    s, p = world()
    commit(s, 'a.txt', 'a\n')
    commit(p, 'f.txt', 'upstream\n')
    g(p, 'push', '-q', 'origin', 'main')
    pick(p, s, 'HEAD')
    put(s, 'f.txt', 'my dirty edit\n')
    before = head(s)
    assert 'refused' in land_watch.catch_up(s) and head(s) == before and read(s, 'f.txt') == 'my dirty edit\n'


def test_jmodot_clean_on_master_behind_the_new_pointer_is_fast_forwarded(world, templates):
    s, _ = world(jmodot=True)
    land_watch.catch_up(s)
    assert head(os.path.join(s, 'Jmodot')) == templates.second


def test_jmodot_dirty_is_left_and_one_line_says_why(world, templates):
    s, _ = world(jmodot=True)
    put(os.path.join(s, 'Jmodot'), 'j.txt', 'dirty\n')
    line = land_watch.catch_up(s)
    assert head(os.path.join(s, 'Jmodot')) != templates.second
    assert [l for l in line.splitlines() if 'Jmodot' in l] == ['Jmodot left: dirty']


def test_jmodot_on_a_branch_other_than_master_is_left_and_a_line_says_why(world, templates):
    s, _ = world(jmodot=True)
    g(os.path.join(s, 'Jmodot'), 'checkout', '-q', '-b', 'feature')
    assert head(os.path.join(s, 'Jmodot')) != templates.second and 'feature' in land_watch.catch_up(s)
