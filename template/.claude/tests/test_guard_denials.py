"""Plain-script proof for guard_denials, reading `fixtures/guard_denials/denials.jsonl`.

The fixture holds the tool_use and tool_result lines cut from real project transcripts (sessions of
this project, trimmed to the fields the scanner reads, with project names neutral): one error tool result per denial signature,
one transient classifier outage, and two non-error results that quote deny text. The wait-loop and
heredoc-guard rows are carried from an earlier capture of the same shape, because no project transcript
holds those two denials now. Each result follows its own tool_use, so the denominator is the file's own.
"""
import json
import os
import re
import subprocess
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

FIXTURE = os.path.join(HERE, 'fixtures', 'guard_denials', 'denials.jsonl')


def fixture_lines():
    with open(FIXTURE, encoding='utf-8') as handle:
        return [line for line in handle if line.strip()]


def fixture_results():
    """(row, tool_result item) for every tool result in the fixture."""
    out = []
    for line in fixture_lines():
        row = json.loads(line)
        content = row.get('message', {}).get('content')
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict) and item.get('type') == 'tool_result':
                out.append((row, item))
    return out


def _text(item):
    return gd.flatten(item.get('content'))


def outage_row():
    """The transient outage: an error result the scanner does not classify as a denial."""
    for row, item in fixture_results():
        if item.get('is_error') is True and 'gave no verdict' in _text(item) and gd.classify(_text(item)) is None:
            return row
    return None


def prose_sessions():
    """Sessions of the non-error results that quote a deny text."""
    return {row.get('sessionId') for row, item in fixture_results()
            if item.get('is_error') is not True and gd.classify(_text(item))}


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
    row = outage_row()
    key = '%s|%s' % (gd.utc_day(row.get('timestamp')), row.get('sessionId'))
    outage = [d for d in found['denials'] if d['session'] == row.get('sessionId')]
    S.check(outage == [] and found['calls'].get(key) == 1,
            'test_transient_classifier_outage_is_not_a_denial', repr(outage))


def test_a_prose_quote_of_a_deny_text_is_not_a_denial():
    """The non-error results that quote a deny text (fixture rows) are not denials."""
    with tempfile.TemporaryDirectory() as tmp:
        make_tree(tmp)
        found = gd.scan([os.path.join(tmp, 'projects')])
    quoted = [row for row in found['denials'] if row['session'] in prose_sessions()]
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


def _cli(*args):
    return subprocess.run([sys.executable, os.path.join(TOOLS, 'guard_denials.py'), *args], capture_output=True,
                          text=True, encoding='utf-8', errors='replace')


def test_cli_unknown_flag_exits_nonzero():
    S.check(_cli('--no-such-flag').returncode != 0, 'test_cli_unknown_flag_exits_nonzero')


def test_cli_missing_root_is_reported_as_a_gap():
    with tempfile.TemporaryDirectory() as tmp:
        done = _cli('--roots', os.path.join(tmp, 'absent'))
        coverage = json.loads(done.stdout.splitlines()[-1])
    S.check(coverage['gaps'] and coverage['files'] == 0, 'test_cli_missing_root_is_reported_as_a_gap', done.stdout)


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            S.guard(name, fn)
    for failure in S.failures:
        print('FAIL detail: ' + failure)
    print('%d checks, %d failures' % (S.count, len(S.failures)))
    sys.exit(bool(S.failures))
