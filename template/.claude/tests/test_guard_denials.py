"""Proof for tools/guard_denials.py over tests/fixtures/guard_denials/denials.jsonl.

The fixture holds the tool_use and tool_result lines cut from real project transcripts, trimmed to the
fields the scanner reads: one error result per denial signature, one transient classifier outage, and two
non-error results that quote deny text. The wait-loop and heredoc-guard rows are carried from an earlier
capture, because no project transcript holds those two denials now.

    python -m pytest .claude/tests/test_guard_denials.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), 'tools', 'guard_denials.py')
sys.path.insert(0, os.path.dirname(TOOL))
import guard_denials as gd  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, 'fixtures', 'guard_denials')
FIXTURE = os.path.join(FIXTURE_DIR, 'denials.jsonl')
CATEGORIES = [cat for cat, _ in gd.SIGNATURES]


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


def tool_result_count():
    """Tool results whose tool_use is in the fixture: the denominator the scan must reproduce."""
    use_ids, count = set(), 0
    rows = [json.loads(line) for line in fixture_lines()]
    for row in rows:
        content = row.get('message', {}).get('content')
        for item in content if isinstance(content, list) else []:
            if item.get('type') == 'tool_use' and item.get('id'):
                use_ids.add(item['id'])
    for row in rows:
        content = row.get('message', {}).get('content')
        for item in content if isinstance(content, list) else []:
            if item.get('type') == 'tool_result' and item.get('tool_use_id') in use_ids:
                count += 1
    return count


def make_tree(root):
    """Copy the fixture into root/projects/proj/session.jsonl and return that file path."""
    folder = os.path.join(root, 'projects', 'proj')
    os.makedirs(folder)
    path = os.path.join(folder, 'session.jsonl')
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.writelines(fixture_lines())
    return path


def cli(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def test_each_signature_counted_once_and_calls_match_tool_results(tmp_path):
    make_tree(str(tmp_path))
    found = gd.scan([str(tmp_path / 'projects')])
    counts = {cat: 0 for cat in CATEGORIES}
    for row in found['denials']:
        counts[row['cat']] += 1
    assert counts == {cat: 1 for cat in CATEGORIES}
    assert sum(found['calls'].values()) == tool_result_count()


def test_transient_classifier_outage_and_prose_quotes_are_not_denials(tmp_path):
    make_tree(str(tmp_path))
    found = gd.scan([str(tmp_path / 'projects')])
    outage = [(row, item) for row, item in fixture_results()
              if item.get('is_error') is True and 'gave no verdict' in gd.flatten(item.get('content'))
              and gd.classify(gd.flatten(item.get('content'))) is None]
    assert outage, 'fixture must carry the transient outage'
    outage_session = outage[0][0].get('sessionId')
    assert not [d for d in found['denials'] if d['session'] == outage_session]
    prose = {row.get('sessionId') for row, item in fixture_results()
             if item.get('is_error') is not True and gd.classify(gd.flatten(item.get('content')))}
    assert prose, 'fixture must carry the prose quotes'
    assert not [d for d in found['denials'] if d['session'] in prose]


def test_per_day_summary_splits_calls_and_denials_by_day():
    found = gd.scan([FIXTURE_DIR])
    days = gd.summarize(found)
    assert sum(day['calls'] for day in days.values()) == tool_result_count()
    assert sum(sum(day['cats'].values()) for day in days.values()) == len(CATEGORIES)


def test_rerun_reads_only_changed_files(tmp_path):
    path = make_tree(str(tmp_path))
    roots = [str(tmp_path / 'projects')]
    cache = str(tmp_path / 'cache.json')
    first = gd.scan(roots, cache_path=cache)
    second = gd.scan(roots, cache_path=cache)
    assert (first['coverage']['read'], first['coverage']['cached']) == (1, 0)
    assert (second['coverage']['read'], second['coverage']['cached']) == (0, 1)
    with open(path, 'a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps({'type': 'user', 'sessionId': 'extra', 'timestamp': '2026-10-09T00:00:00Z',
                                 'message': {'content': 'appended'}}) + '\n')
    third = gd.scan(roots, cache_path=cache)
    assert third['coverage']['read'] == 1


def test_cache_entry_for_a_removed_file_is_dropped(tmp_path):
    path = make_tree(str(tmp_path))
    roots = [str(tmp_path / 'projects')]
    cache = str(tmp_path / 'cache.json')
    gd.scan(roots, cache_path=cache)
    os.remove(path)
    gd.scan(roots, cache_path=cache)
    with open(cache, encoding='utf-8') as handle:
        assert json.load(handle) == {}


def test_unparseable_file_is_counted_not_dropped(tmp_path):
    folder = tmp_path / 'projects'
    folder.mkdir()
    (folder / 'junk.jsonl').write_text('not json at all\n{also not json\n', encoding='utf-8')
    found = gd.scan([str(folder)])
    assert found['coverage']['unparseable'] == 1
    assert found['coverage']['files'] == 1
    assert found['denials'] == []


def test_absent_root_is_a_named_gap(tmp_path):
    missing = str(tmp_path / 'missing')
    found = gd.scan([missing])
    assert found['coverage']['gaps'] == [missing]
    assert found['coverage']['roots'] == 0 and found['coverage']['files'] == 0


def test_project_roots_keep_this_projects_directories(tmp_path, monkeypatch):
    root = os.path.abspath(os.path.join(os.sep, 'x', 'proj'))
    slug = re.sub(r'[^A-Za-z0-9]', '-', root)
    base = tmp_path / 'projects'
    for name in [slug, slug + '--claude-worktrees-w1', slug + '2', slug + '-other']:
        (base / name).mkdir(parents=True)
    monkeypatch.setattr(gd, 'default_roots', lambda: [str(base)])
    assert gd.project_roots(root) == [str(base / slug), str(base / (slug + '--claude-worktrees-w1'))]


def test_default_roots_come_from_transcript_usage(monkeypatch):
    monkeypatch.setattr(gd.transcript_usage, 'projects_dirs', lambda: ['/logins/a/projects'])
    assert gd.default_roots() == ['/logins/a/projects']


def test_cli_fixture_directory_prints_per_day_and_per_hundred_calls():
    calls = tool_result_count()
    done = cli('--roots', FIXTURE_DIR)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert 'total calls %d denials %d per100 %s' % (calls, len(CATEGORIES), gd.per_hundred(len(CATEGORIES), calls)) in lines
    assert '  wait-loop 1 per100 %s' % gd.per_hundred(1, calls) in lines
    assert any(re.match(r'^(?!total )\S+ calls \d+ denials \d+ per100 \S+$', line) for line in lines), lines
    assert json.loads(lines[-1])['files'] == 1


def test_cli_unknown_flag_exits_nonzero():
    assert cli('--no-such-flag').returncode != 0


def test_cli_missing_root_is_reported_as_a_gap(tmp_path):
    done = cli('--roots', str(tmp_path / 'absent'))
    assert done.returncode == 0
    assert json.loads(done.stdout.splitlines()[-1])['gaps']


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
