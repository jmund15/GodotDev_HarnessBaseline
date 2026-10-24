#!/usr/bin/env python3
"""Proof for hooks/sidecar_workdir_guard.py, the child-side floor that keeps a worktree job out of the main checkout.

Each case pipes a PreToolUse payload into the guard armed with (workdir, main checkout, spill dir):
  deny    Write/Edit/NotebookEdit whose path is in the main checkout and outside the workdir: absolute, `..`
          climb from the workdir, a relative path on a payload cwd in the main checkout, a sibling worktree
  allow   a path in the workdir; the spill dir's report; a path outside the main checkout; a main-checkout job
          (workdir is the main root); a Read; a prefix-sibling (`<workdir>-x`)
  bash    a Bash command whose redirection, tee, cp/mv/install, sed -i/perl -i, git -C index write or python path
          literal lands in the main checkout is denied; reads, workdir/spill/temp writes, quoted mentions and
          unresolvable targets are allowed
  merge   sc_settings_with_bench_guard registers the guard on the three write tools, expanded with the launcher's values
    python3 .claude/tests/test_sidecar_workdir_guard.py      0 pass, 1 failure
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _sidecar_launch import bash_exe  # noqa: E402

CLAUDE = Path(__file__).resolve().parents[1]
# The merge runs over a root with no adaptation.json, so the guard's registration must be the launcher's own.
MERGE = '. "$CLAUDE_DIR/scripts/lib/sidecar_common.sh" >/dev/null 2>&1; SC_ROOT="$PLANT_ROOT"; sc_settings_with_bench_guard ""'


def bare_root(tmp):
    hooks = Path(tmp, 'bare', 'hooks')
    hooks.mkdir(parents=True)
    (hooks / '_checkout_root.py').write_bytes((CLAUDE / 'hooks' / '_checkout_root.py').read_bytes())
    return hooks.parent.as_posix()
GUARD = CLAUDE / 'hooks' / 'sidecar_workdir_guard.py'
FAILS = []


def ck(name, ok, detail=''):
    print(('  PASS  ' if ok else '  FAIL  ') + name + ('' if ok else '\n        ' + str(detail)[:400]), flush=True)
    if not ok:
        FAILS.append(name)


def run(workdir, main, spill, tool, path, cwd=None, field='file_path'):
    stdin = json.dumps({'cwd': str(cwd or workdir), 'hook_event_name': 'PreToolUse', 'tool_name': tool,
                        'tool_input': {field: str(path), 'content': 'x'}})
    r = subprocess.run([sys.executable, str(GUARD), str(workdir), str(main), str(spill)], input=stdin,
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    return r.returncode, r.stderr


def run_bash(workdir, main, spill, command, cwd=None):
    stdin = json.dumps({'cwd': str(cwd or workdir), 'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                        'tool_input': {'command': command}})
    r = subprocess.run([sys.executable, str(GUARD), str(workdir), str(main), str(spill)], input=stdin,
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    return r.returncode, r.stderr


def main():
    if not GUARD.exists():
        ck('guard exists', False, GUARD)
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        main_root = tmp / 'repo'
        work = main_root / '.claude' / 'worktrees' / 'w1'
        sibling = main_root / '.claude' / 'worktrees' / 'w2'
        spill = main_root / '.claude' / 'scratch' / 'drive' / 'out'
        outside = tmp / 'elsewhere'
        for d in (work, sibling, spill, outside, Path(str(work) + '-x')):
            d.mkdir(parents=True)
        a = (work, main_root, spill)

        def case(name, want, tool, path, **kw):
            rc, err = run(*a, tool, path, **kw)
            ck(name, rc == want and (want == 0 or work.as_posix() in err.replace(chr(92), '/')), (rc, err))

        case('deny Write to the main tests dir', 2, 'Write', main_root / '.claude' / 'tests' / 'x.py')
        case('deny Edit to a main tracked file', 2, 'Edit', main_root / '.claude' / 'tools' / 'eval_findings.py')
        case('deny NotebookEdit in the main checkout', 2, 'NotebookEdit', main_root / 'n.ipynb', field='notebook_path')
        case('deny a `..` climb from the workdir', 2, 'Write', work / '..' / '..' / 'tests' / 'x.py')
        case('deny a relative path on a main-checkout cwd', 2, 'Write', 'a.py', cwd=main_root)
        case('deny a sibling worktree', 2, 'Write', sibling / 'a.py')
        case('deny a prefix-sibling-free main root file', 2, 'Write', main_root / 'README.md')
        case('allow a workdir file', 0, 'Write', work / '.claude' / 'tests' / 'x.py')
        case('allow a relative workdir path', 0, 'Edit', 'a.py')
        case('allow the spill dir report', 0, 'Write', spill / 'lane.md')
        case('allow a path outside the main checkout', 0, 'Write', outside / 'a.txt')
        case('allow `<workdir>-x` outside the main checkout rule', 0, 'Write', outside / 'b.txt')
        rc, err = run(main_root, main_root, spill, 'Write', main_root / 'a.py')
        ck('allow a main-checkout job (workdir is the main root)', rc == 0, (rc, err))
        rc, err = run(work, main_root, '', 'Write', work / 'a.py')
        ck('allow with no spill dir', rc == 0, (rc, err))
        rc, err = run(work, main_root, spill, 'Read', main_root / 'a.py')
        ck('allow a Read of the main checkout', rc == 0, (rc, err))
        r = subprocess.run([sys.executable, str(GUARD), str(work), str(main_root), str(spill)], input='not json',
                           capture_output=True, text=True, encoding='utf-8', timeout=60)
        ck('deny an unparseable payload (floor guard fails closed)', r.returncode == 2, (r.returncode, r.stderr))
        rc, err = run(work, main_root, spill, 'Write', main_root / 'a.py', field='other')
        ck('allow a payload with no path field', rc == 0, (rc, err))

        M, W, S, O = main_root.as_posix(), work.as_posix(), spill.as_posix(), outside.as_posix()

        def bash(name, want, command, cwd=None):
            rc, err = run_bash(*a, command, cwd=cwd)
            ck(name, rc == want and (want == 0 or W in err.replace(chr(92), '/')), (rc, err, command))

        bash('deny a redirect into main', 2, 'echo x > %s/.claude/tests/a.py' % M)
        bash('deny an append into main', 2, 'echo x >> %s/a.py' % M)
        bash('deny a fd redirect into main', 2, 'cat a 1> %s/a.py' % M)
        bash('deny a quoted redirect target in main', 2, 'echo x > "%s/a b.py"' % M)
        bash('deny tee into main', 2, 'echo x | tee %s/a.py' % M)
        bash('deny tee -a into main', 2, 'echo x | tee -a %s/a.py' % M)
        bash('deny cp into main', 2, 'cp a.py %s/.claude/tests/a.py' % M)
        bash('deny cp -r flags into main dir', 2, 'cp -r a %s/b' % M)
        bash('deny mv into main', 2, 'mv a.py %s/a.py' % M)
        bash('deny install into main', 2, 'install -m644 a.py %s/a.py' % M)
        bash('deny cp -t main', 2, 'cp -t %s/d a.py' % M)
        bash('deny sed -i on a main file', 2, "sed -i 's/a/b/' %s/a.py" % M)
        bash('deny perl -pi on a main file', 2, "perl -pi -e 's/a/b/' %s/a.py" % M)
        bash('deny git -C main add', 2, 'git -C %s add a.py' % M)
        bash('deny git -C main checkout', 2, 'git -C %s checkout -- a.py' % M)
        bash('deny git -C main commit', 2, 'git -C %s commit -m x' % M)
        bash('deny python -c writing a main path', 2, "python3 -c \"open('%s/a.py','w').write('x')\"" % M)
        bash('deny a python heredoc writing a main path', 2,
             "python3 - <<'EOF'\nfrom pathlib import Path\nPath('%s/a.py').write_text('x')\nEOF" % M)
        bash('deny a write after cd into main', 2, 'cd %s && echo x > a.py' % M)
        bash('deny a relative redirect on a main cwd', 2, 'echo x > a.py', cwd=main_root)
        bash('deny a write in the second segment', 2, 'cd %s && echo y > a && cp a %s/b' % (W, M))
        bash('allow a read of main', 0, 'cat %s/a.py' % M)
        bash('allow grep of main into the workdir', 0, 'grep x %s/a.py > %s/o.txt' % (M, W))
        bash('allow a redirect in the workdir', 0, 'echo x > %s/a.py' % W)
        bash('allow a relative redirect on the workdir cwd', 0, 'echo x > a.py')
        bash('allow a redirect to the spill dir', 0, 'echo x > %s/lane.md' % S)
        bash('allow a redirect to a temp dir', 0, 'echo x > %s/a.txt' % O)
        bash('allow cp from main into the workdir', 0, 'cp %s/a.py %s/a.py' % (M, W))
        bash('allow cp -t workdir from main', 0, 'cp -t %s %s/a.py' % (W, M))
        bash('allow sed -i in the workdir', 0, "sed -i 's/a/b/' %s/a.py" % W)
        bash('allow git -C main status', 0, 'git -C %s status --short' % M)
        bash('allow git -C main log', 0, 'git -C %s log -1' % M)
        bash('allow git -C workdir add', 0, 'git -C %s add a.py' % W)
        bash('allow cd main then a read', 0, 'cd %s && git status && cat a.py' % M)
        bash('allow cd main then back to workdir write', 0, 'cd %s && cat a && cd %s && echo x > a' % (M, W))
        bash('allow /dev/null and fd dups', 0, 'cat %s/a.py > /dev/null 2>&1' % M)
        bash('allow an unresolvable variable target', 0, 'echo x > $TARGET/a.py')
        bash('allow a command-substitution target', 0, 'echo x > $(mktemp)')
        bash('allow a quoted mention of a main redirect', 0, 'echo "x > %s/a.py"' % M)
        bash('allow a heredoc body mentioning a main redirect', 0, "cat > %s/n.txt <<'EOF'\necho x > %s/a.py\nEOF" % (W, M))
        bash('allow a python read of main', 0, "python3 -c \"print(open('%s/a.py').read())\"" % M)
        bash('allow a python write to the workdir', 0, "python3 -c \"open('%s/a.py','w').write('x')\"" % W)
        rc, err = run_bash(main_root, main_root, spill, 'echo x > %s/a.py' % M)
        ck('allow a Bash write in a main-checkout job', rc == 0, (rc, err))

    with tempfile.TemporaryDirectory() as tmp:
        repo, linked = Path(tmp, 'repo'), Path(tmp, 'linked')
        git = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']
        subprocess.run(git + ['init', '-q', str(repo)], check=True)
        subprocess.run(git + ['-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        subprocess.run(git + ['-C', str(repo), 'worktree', 'add', '-q', '-b', 'w', str(linked)], check=True)
        env = dict(os.environ, CLAUDE_DIR=CLAUDE.as_posix(), SC_WORKDIR=str(linked), SC_RECORD='', SC_PROGRESS='',
                   PLANT_ROOT=bare_root(tmp))
        r = subprocess.run([bash_exe(), '-c', MERGE], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60, cwd=str(linked))
    try:
        rows = [e for e in json.loads(r.stdout)['hooks']['PreToolUse']
                if any('sidecar_workdir_guard.py' in h['command'] for h in e['hooks'])]
    except Exception as ex:
        rows = []
        r.stderr += repr(ex)
    ck('merge registers the guard once', len(rows) == 1, (r.stdout[:300], r.stderr[:200]))
    if rows:
        import re
        m = rows[0]['matcher']
        ck('matcher covers Write, Edit, NotebookEdit, Bash only',
           all(re.fullmatch(m, t) for t in ('Write', 'Edit', 'NotebookEdit', 'Bash')) and not re.fullmatch(m, 'Read'), m)

    with tempfile.TemporaryDirectory() as tmp:
        repo, linked = Path(tmp, 'my repo'), Path(tmp, 'my linked')
        subprocess.run(git + ['init', '-q', str(repo)], check=True)
        subprocess.run(git + ['-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        subprocess.run(git + ['-C', str(repo), 'worktree', 'add', '-q', '-b', 'w', str(linked)], check=True)
        env = dict(os.environ, CLAUDE_DIR=CLAUDE.as_posix(), SC_WORKDIR=str(linked), SC_RECORD='', SC_PROGRESS='',
                   PLANT_ROOT=bare_root(tmp))
        r = subprocess.run([bash_exe(), '-c', MERGE], capture_output=True, text=True, encoding='utf-8', env=env, timeout=60, cwd=str(linked))
    try:
        commands = [h['command'] for e in json.loads(r.stdout)['hooks']['PreToolUse'] for h in e['hooks']
                    if 'sidecar_workdir_guard.py' in h['command']]
    except Exception as ex:
        commands = []
        r.stderr += repr(ex)
    ck('merge arms the guard for a checkout path with a space',
       len(commands) == 1 and repo.as_posix() in commands[0].replace(chr(92), '/'), (commands, r.stderr[:200]))


main()
print('FAIL: %s' % FAILS if FAILS else 'ok')
sys.exit(1 if FAILS else 0)
