"""Count guard denials per day from Claude Code session transcripts.

Usage: python3 .claude/tools/guard_denials.py --roots DIR [DIR ...] [--cache FILE]
"""
import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import model_registry

# Longest text before a signature in the captured fixture is 146 chars; 700 keeps quoted text further in from matching.
SIGNATURE_WINDOW = 700

SIGNATURES = [
    ('index-write', re.compile(r'an index write runs in its own Bash call')),
    ('commit-stamp', re.compile(r'BLOCKED (git )?commit')),
    ('recursive-grep', re.compile(r'RECURSIVE GREP DENIED')),
    ('sleep-wait', re.compile(r'Blocked: sleep')),
    ('wait-loop', re.compile(r'BLOCKED foreground wait loop')),
    ('heredoc-guard', re.compile(r'BLOCKED heredoc')),
    ('kill-denied', re.compile(r'BLOCKED (TaskStop|kill)')),
    ('shape-guard', re.compile(r'Blocked by bash_shape_guard')),
    ('dispatch-denied', re.compile(r'Dispatch denied|Sidecar dispatch denied')),
    ('harness-edit', re.compile(r'Harness edit to .* DENIED')),
    ('classifier-denied', re.compile(r'denied by the Claude Code auto mode classifier')),
]


def default_roots():
    try:
        logins = model_registry.login_dirs()
    except model_registry.RegistryError:
        logins = [os.path.expanduser('~/.claude'), os.path.expanduser('~/.claude-secondary')]
    return [model_registry.projects_dir(login) for login in logins]


def project_roots(root):
    """Transcript folders of one project: its own slug and linked worktrees (slug + '--' suffix).
    A configured projects directory that is missing is returned as is, so the scan names it a gap."""
    slug = re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(root))
    found = []
    for base in default_roots():
        if not os.path.isdir(base):
            found.append(base)
            continue
        for name in os.listdir(base):
            if name == slug or name.startswith(slug + '--'):
                path = os.path.join(base, name)
                if os.path.isdir(path):
                    found.append(path)
    return sorted(found)


def flatten(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(part if isinstance(part, str) else part.get('text', '')
                       for part in content if isinstance(part, (str, dict)))
    return ''


def classify(text):
    head = text[:SIGNATURE_WINDOW]
    for cat, pattern in SIGNATURES:
        if pattern.search(head):
            return cat
    return None


def utc_day(timestamp):
    if not isinstance(timestamp, str):
        return 'unknown'
    try:
        moment = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        return 'unknown'
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%d')


def parse_file(path):
    """Return the denials, tool-result calls and parse flag of one transcript."""
    call_ids, denials, calls, parsed_lines = set(), [], {}, 0
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding='utf-8', errors='replace') as handle:
        for raw in handle:
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(line, dict):
                continue
            parsed_lines += 1
            content = line.get('message', {}).get('content') if isinstance(line.get('message'), dict) else None
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'tool_use' and item.get('id'):
                    call_ids.add(item['id'])
                elif item.get('type') == 'tool_result' and item.get('tool_use_id') in call_ids:
                    day = utc_day(line.get('timestamp'))
                    session = line.get('sessionId') or stem
                    key = '%s|%s' % (day, session)
                    calls[key] = calls.get(key, 0) + 1
                    if item.get('is_error') is True:
                        cat = classify(flatten(item.get('content')))
                        if cat:
                            denials.append({'day': day, 'session': session, 'cat': cat})
    return {'denials': denials, 'calls': calls, 'unparseable': parsed_lines == 0}


def load_cache(cache_path):
    if not cache_path or not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(cache_path, entries):
    folder = os.path.dirname(os.path.abspath(cache_path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(entries, handle, sort_keys=True)
        os.replace(tmp, cache_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def transcript_paths(roots, coverage):
    seen, found = set(), []
    for root in roots:
        if not os.path.isdir(root):
            coverage['gaps'].append(os.path.abspath(root))
            continue
        coverage['roots'] += 1
        for folder, _, names in os.walk(root):
            for name in names:
                if name.endswith('.jsonl'):
                    path = os.path.abspath(os.path.join(folder, name))
                    if path not in seen:
                        seen.add(path)
                        found.append(path)
    return sorted(found)


def scan(roots, cache_path=None):
    coverage = {'files': 0, 'read': 0, 'cached': 0, 'unparseable': 0, 'unreadable': 0, 'roots': 0, 'gaps': []}
    cache = load_cache(cache_path)
    entries, denials, calls = {}, [], {}
    for path in transcript_paths(roots, coverage):
        try:
            stat = os.stat(path)
            known = cache.get(path)
            if isinstance(known, dict) and known.get('size') == stat.st_size and known.get('mtime') == stat.st_mtime:
                entry = known
                coverage['cached'] += 1
            else:
                entry = dict(parse_file(path), size=stat.st_size, mtime=stat.st_mtime)
                coverage['read'] += 1
        except OSError:
            coverage['unreadable'] += 1
            continue
        entries[path] = entry
        coverage['files'] += 1
        coverage['unparseable'] += 1 if entry.get('unparseable') else 0
        denials.extend(entry.get('denials', []))
        for key, count in entry.get('calls', {}).items():
            calls[key] = calls.get(key, 0) + count
    if cache_path:
        kept = dict(entries)
        for path in cache:
            if path not in kept and os.path.exists(path):
                kept[path] = cache[path]
        if coverage['read'] or kept.keys() != cache.keys():
            try:
                write_cache(cache_path, kept)
            except OSError:
                pass
    return {'denials': denials, 'calls': calls, 'coverage': coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--roots', nargs='+', default=None)
    parser.add_argument('--cache', default=None)
    args = parser.parse_args()
    found = scan(args.roots or default_roots(), cache_path=args.cache)
    counts = {cat: 0 for cat, _ in SIGNATURES}
    for row in found['denials']:
        counts[row['cat']] += 1
    for cat, _ in SIGNATURES:
        print('%s %d' % (cat, counts[cat]))
    print('calls %d' % sum(found['calls'].values()))
    print(json.dumps(found['coverage']))


if __name__ == '__main__':
    main()
