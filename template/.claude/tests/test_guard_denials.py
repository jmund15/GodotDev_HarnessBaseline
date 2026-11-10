"""Plain-script proof for guard_denials, using a transcript cut from real sessions and trimmed.

`.claude/tests/fixtures/` never ships, so the transcript is planted here: one error tool result per
denial signature, one transient classifier outage, and two prose quotes of deny text that are not
denials. Each result follows its own tool_use, so the denominator is the file's own.
"""
import json
import os
import re
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
sys.path.insert(0, TOOLS)
import guard_denials as gd

DENIAL_CATEGORIES = ['index-write', 'commit-stamp', 'recursive-grep', 'sleep-wait', 'wait-loop', 'heredoc-guard',
                     'kill-denied', 'shape-guard', 'dispatch-denied', 'dispatch-doctrine', 'harness-edit',
                     'classifier-denied', 'classifier-no-verdict']

# One deny text per signature, at the head of its tool result.
DENIALS = [
    'an index write runs in its own Bash call',
    'BLOCKED commit (CLAUDE.core.md §Shell Discipline)',
    'RECURSIVE GREP DENIED: `grep -r` can run away without bound',
    'Blocked: sleep is not a wait',
    'BLOCKED foreground wait loop',
    'BLOCKED heredoc with nested quotes',
    'BLOCKED TaskStop on a dispatched agent',
    'Blocked by bash_shape_guard',
    'Dispatch denied: the write set is outside the job',
    'the dispatch doctrine is not in context',
    'Harness edit to .claude/hooks/x DENIED',
    'denied by the Claude Code auto mode classifier',
    'auto mode classifier gave no verdict for Bash: timed out',
]
OUTAGE = ('2fd3246a-138e-425c-8911-21cf89a135a2',
          'auto mode classifier gave no verdict for Bash (timed out)')
PROSE = [('0c6e5ef0-7e88-44c3-a09e-44eaa73f4e08', 'diff: -BLOCKED commit (the hook\'s own text, quoted)'),
         ('14d4c412-691b-474d-9753-bcfbe35e3f24', 'friction report: RECURSIVE GREP DENIED is quoted here')]
STAMP = '2026-09-23T00:00:00Z'


def _pair(call_id, session, text, is_error):
    tool_use = {'type': 'assistant', 'sessionId': session, 'timestamp': STAMP,
                'message': {'content': [{'type': 'tool_use', 'id': call_id, 'name': 'Bash'}]}}
    tool_result = {'type': 'user', 'sessionId': session, 'timestamp': STAMP,
                   'message': {'content': [{'type': 'tool_result', 'tool_use_id': call_id,
                                            'content': text, 'is_error': is_error}]}}
    return [tool_use, tool_result]


def _planted_rows():
    rows = []
    for number, text in enumerate(DENIALS, 1):
        rows += _pair('d%d' % number, 'denials', 'PreToolUse:Bash hook error: ' + text, True)
    rows += _pair('o1', OUTAGE[0], OUTAGE[1], True)
    for number, (session, text) in enumerate(PROSE, 1):
        rows += _pair('p%d' % number, session, text, False)
    return rows


class Suite:
    def __init__(self):
        self.failures, self.count = [], 0

    def check(self, ok, label, detail=''):
        self.count += 1
        print('%-4s %s' % ('ok' if ok else 'FAIL', label))
        if not ok:
            self.failures.append(label + (': ' + detail if detail else ''))

    def guard(self, name, fn):
        try:
            fn()
        except Exception:
            self.check(False, name + ' raised', traceback.format_exc(limit=2).strip().splitlines()[-1])


S = Suite()


def fixture_lines():
    return [json.dumps(row) + '\n' for row in _planted_rows()]


def make_tree(root):
    """Copy the fixture into root/projects/proj/session.jsonl and return that file path."""
    folder = os.path.join(root, 'projects', 'proj')
    os.makedirs(folder)
    path = os.path.join(folder, 'session.jsonl')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.writelines(fixture_lines())
    return path


def tool_result_count():
    bash_ids, results = set(), 0
    for line in fixture_lines():
        content = json.loads(line).get('message', {}).get('content')
        for item in content if isinstance(content, list) else []:
            if item.get('type') == 'tool_use' and item.get('id'):
                bash_ids.add(item.get('id'))
    for line in fixture_lines():
        content = json.loads(line).get('message', {}).get('content')
        for item in content if isinstance(content, list) else []:
            if item.get('type') == 'tool_result' and item.get('tool_use_id') in bash_ids:
                results += 1
    return results


def test_scan_counts_each_signature_once_and_the_tool_result_denominator():
    with tempfile.TemporaryDirectory() as tmp:
        make_tree(tmp)
        found = gd.scan([os.path.join(tmp, 'projects')])
    counts = {}
    for row in found['denials']:
        counts[row['cat']] = counts.get(row['cat'], 0) + 1
    S.check(counts == {cat: 1 for cat in DENIAL_CATEGORIES}, 'test_scan_counts_each_signature_once_and_the_tool_result_denominator: categories',
            repr(counts))
    S.check(sum(found['calls'].values()) == tool_result_count(),
            'test_scan_counts_each_signature_once_and_the_tool_result_denominator: calls equal tool results',
            '%d vs %d' % (sum(found['calls'].values()), tool_result_count()))


def test_transient_classifier_outage_is_not_a_denial():
    with tempfile.TemporaryDirectory() as tmp:
        make_tree(tmp)
        found = gd.scan([os.path.join(tmp, 'projects')])
    outage = [row for row in found['denials'] if row['session'] == '2fd3246a-138e-425c-8911-21cf89a135a2']
    S.check(outage == [] and found['calls'].get('2026-09-23|2fd3246a-138e-425c-8911-21cf89a135a2') == 1,
            'test_transient_classifier_outage_is_not_a_denial', repr(outage))


def test_a_prose_quote_of_a_deny_text_is_not_a_denial():
    """Two real non-error tool results quote each new deny phrase (a diff and a friction report) without being one."""
    with tempfile.TemporaryDirectory() as tmp:
        make_tree(tmp)
        found = gd.scan([os.path.join(tmp, 'projects')])
    quoted = [row for row in found['denials']
              if row['session'] in ('0c6e5ef0-7e88-44c3-a09e-44eaa73f4e08', '14d4c412-691b-474d-9753-bcfbe35e3f24')]
    S.check(quoted == [], 'test_a_prose_quote_of_a_deny_text_is_not_a_denial', repr(quoted))


def test_rerun_reads_only_changed_files():
    with tempfile.TemporaryDirectory() as tmp:
        path = make_tree(tmp)
        roots = [os.path.join(tmp, 'projects')]
        cache = os.path.join(tmp, 'cache.json')
        first = gd.scan(roots, cache_path=cache)
        second = gd.scan(roots, cache_path=cache)
        S.check((first['coverage']['read'], first['coverage']['cached']) == (1, 0),
                'test_rerun_reads_only_changed_files: first scan reads the file', repr(first['coverage']))
        S.check((second['coverage']['read'], second['coverage']['cached']) == (0, 1),
                'test_rerun_reads_only_changed_files: second scan is cached', repr(second['coverage']))
        with open(path, 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps({'type': 'user', 'sessionId': 'extra', 'timestamp': '2026-10-09T00:00:00Z',
                                     'message': {'content': 'appended'}}) + '\n')
        third = gd.scan(roots, cache_path=cache)
        S.check(third['coverage']['read'] == 1, 'test_rerun_reads_only_changed_files: changed file is read again',
                repr(third['coverage']))


def test_unparseable_file_is_counted_not_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, 'projects')
        os.makedirs(folder)
        with open(os.path.join(folder, 'junk.jsonl'), 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('not json at all\n{also not json\n')
        found = gd.scan([folder])
    S.check(found['coverage']['unparseable'] == 1 and found['coverage']['files'] == 1 and found['denials'] == [],
            'test_unparseable_file_is_counted_not_dropped', repr(found['coverage']))


def test_absent_root_is_counted():
    with tempfile.TemporaryDirectory() as tmp:
        found = gd.scan([os.path.join(tmp, 'missing')])
        missing = os.path.join(tmp, 'missing')
    S.check(found['coverage']['gaps'] == [missing] and found['coverage']['roots'] == 0 and found['coverage']['files'] == 0,
            'test_absent_root_is_counted', repr(found['coverage']))


def test_project_roots_names_a_missing_transcript_root():
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, 'projects')
        saved = gd.default_roots
        gd.default_roots = lambda: [base]
        try:
            roots = gd.project_roots(r'C:\x\proj')
            found = gd.scan(roots)
        finally:
            gd.default_roots = saved
    S.check(found['coverage']['gaps'] == [base] and found['coverage']['files'] == 0,
            'test_project_roots_names_a_missing_transcript_root', repr((roots, found['coverage'])))


def test_cache_entry_for_a_removed_file_is_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        path = make_tree(tmp)
        roots = [os.path.join(tmp, 'projects')]
        cache = os.path.join(tmp, 'cache.json')
        gd.scan(roots, cache_path=cache)
        os.remove(path)
        gd.scan(roots, cache_path=cache)
        with open(cache, encoding='utf-8') as handle:
            entries = json.load(handle)
    S.check(entries == {}, 'test_cache_entry_for_a_removed_file_is_dropped', repr(entries))


def test_project_roots_keep_this_projects_directories():
    # A root real on this platform: a drive-letter path is relative on POSIX, so its slug would never match.
    root = os.path.abspath(os.path.join(os.sep, 'x', 'proj'))
    slug = re.sub(r'[^A-Za-z0-9]', '-', root)
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, 'projects')
        for name in [slug, slug + '--claude-worktrees-w1', slug + '2', slug + '-other']:
            os.makedirs(os.path.join(base, name))
        saved = gd.default_roots
        gd.default_roots = lambda: [base]
        try:
            found = gd.project_roots(root)
        finally:
            gd.default_roots = saved
    expected = [os.path.join(base, slug), os.path.join(base, slug + '--claude-worktrees-w1')]
    S.check(found == expected, 'test_project_roots_keep_this_projects_directories', repr(found))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            S.guard(name, fn)
    for failure in S.failures:
        print('FAIL detail: ' + failure)
    print('%d checks, %d failures' % (S.count, len(S.failures)))
    sys.exit(bool(S.failures))
