#!/usr/bin/env python3
"""The workflow templates under `.claude/templates/workflows/` stay project-neutral and in step with each project's copy.

A project copies the templates to `.github/workflows/` once; its copy may differ in its project values only: the
cap literals (`timeout-minutes` values and `--cap-minutes` arguments), which stay literals because
`main_check.parse_job_minutes` reads the job timeout as text, and the repository's secret names (`*_TOKEN`).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.normpath(os.path.join(HERE, '..'))
ROOT = os.path.dirname(CLAUDE)
TEMPLATES = os.path.join(CLAUDE, 'templates', 'workflows')
LIVE = os.path.join(ROOT, '.github', 'workflows')
NAMES = ('land.yml', 'land-retry.yml', 'main_check.yml')
sys.path.insert(0, os.path.join(CLAUDE, 'scripts'))
import main_check

failures = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        failures.append(name)


def read(path):
    with open(path, encoding='utf-8', newline='') as fh:
        return fh.read()


def masked(text):
    text = re.sub(r'(timeout-minutes:\s*)\d+', r'\1N', text)
    text = re.sub(r'\b[A-Z][A-Z0-9_]*_TOKEN\b', 'TOKEN', text)
    return re.sub(r'(--cap-minutes\s+)\d+', r'\1N', text)


check('every template exists', all(os.path.isfile(os.path.join(TEMPLATES, n)) for n in NAMES))
check('a repository secret name is a project value, masked like a cap',
      masked('token: ${{ secrets.MM_LAND_TOKEN }}') == masked('token: ${{ secrets.LAND_TOKEN }}'))

for name in NAMES:
    template = os.path.join(TEMPLATES, name)
    live = os.path.join(LIVE, name)
    if not (os.path.isfile(template) and os.path.isfile(live)):
        continue
    check('%s: the project copy equals the template, caps masked' % name, masked(read(template)) == masked(read(live)))

check('the mask hides only the cap literals',
      masked('timeout-minutes: 5\n--cap-minutes 9\n') == masked('timeout-minutes: 7\n--cap-minutes 1\n')
      and masked('name: a\n') != masked('name: b\n'))

ABSOLUTE_PATH = r'[A-Za-z]:\\|/Users/|/home/'
PINNED_RUNNER = r'runs-on:.*self-hosted'
check('the absolute-path check fires on a planted Windows path',
      bool(re.search(ABSOLUTE_PATH, 'working-directory: C:\\Users\\x\\repo')))
check('the runner check fires on a planted self-hosted label',
      bool(re.search(PINNED_RUNNER, 'runs-on: [self-hosted, some-local]')))

for name in NAMES:
    template = os.path.join(TEMPLATES, name)
    if not os.path.isfile(template):
        continue
    text = read(template)
    check('%s: no absolute path' % name, not re.search(ABSOLUTE_PATH, text))
    check('%s: no self-hosted project runner label' % name, not re.search(PINNED_RUNNER, text))

main_template = os.path.join(TEMPLATES, 'main_check.yml')
if os.path.isfile(main_template):
    text = read(main_template)
    check('main_check: the runner and the worktree come from repository variables',
          'vars.MAIN_CHECK_RUNNER' in text and 'vars.MAIN_CHECK_DIR' in text)
    found = re.search(r'^    timeout-minutes:\s*(\d+)\s*$', text, re.MULTILINE)
    check('main_check: the job timeout is a literal main_check.parse_job_minutes reads',
          bool(found) and main_check.parse_job_minutes(text) == int(found.group(1)))

if failures:
    print('\n%d FAILED' % len(failures))
    sys.exit(1)
print('\nall passed')
