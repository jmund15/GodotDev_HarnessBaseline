"""Count guard denials from Claude Code session transcripts: per category, and per hook with repeats.

Usage: python3 .claude/tools/guard_denials.py --roots DIR [DIR ...] [--cache FILE] [--days N] [--hooks DIR]

A hook line is `hook <name> <tool> denials=N sessions=N repeat=N identical=N` for the denials of the last N days
(every day without `--days`). The hook is the module that owns the deny text: `emitted_signatures` reads the
deny strings out of the hooks' own source (`--hooks DIR`, this checkout's by default), so a reworded deny is
followed with no table to keep. A denial in an older wording than that checkout's counts as `unattributed`.
`repeat` counts a hook's denials after its first in the same session; `identical` counts those whose call
(tool and input) was already denied earlier in that session.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _hook_state import replace_path, stat_present  # noqa: E402

import model_registry

# Longest text before a signature in the captured fixture is 146 chars; 700 keeps quoted text further in from matching.
SIGNATURE_WINDOW = 700

# The denial category table; the per-hook rows below do not use it.
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
    ('dispatch-doctrine', re.compile(r'the dispatch doctrine is not in context')),
    ('harness-edit', re.compile(r'Harness edit to .* DENIED')),
    ('classifier-denied', re.compile(r'denied by the Claude Code auto mode classifier')),
    # 'gave no verdict (timed out)' / '(the response ended ...)' is a transient check failure, not a denial.
    ('classifier-no-verdict', re.compile(r'auto mode classifier gave no verdict for \w+: ')),
]
CLIENT_CATEGORIES = ('classifier-denied', 'classifier-no-verdict')

# What a hook's denial looks like in a transcript: `PreToolUse:Bash hook error: [<command>]: <deny text>`.
HOOK_HEADER = re.compile(r'^\w+:\w+ hook error: (?:\[([^\]]*)\]: )?')
_DENY_WORD = re.compile(r'\b(?:BLOCKED|DENIED|Blocked|denied)\b')
_HOLE = re.compile(r'%[-#0-9.]*[sdrfx]|\{[^{}]*\}')
# A dispatcher runs many guards, so its name in a header attributes nothing.
DISPATCHERS = frozenset(('pre_bash', 'pre_bash_dispatch', 'pre_edit_dispatch', 'post_edit_dispatch',
                         'pre_read_dispatch', 'post_read_dispatch', 'pre_workflow_dispatch', 'prompt_dispatch'))
CACHE_VERSION = 2
MIN_LITERAL = 14  # literal characters an opening needs before it can name a hook
OPENING_LITERAL = 30  # literal characters an opening keeps at most, past the deny word


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


def _pattern(text):
    """(literal length, regex source) for a deny string's opening: its literal text escaped, each `%s` or `{x}`
    hole a short wildcard. The opening ends at the deny word, or at its OPENING_LITERAL-th literal character when
    that comes later, so a reworded tail still matches. None when the opening holds too little literal text."""
    head = text.lstrip().split('\n', 1)[0][:120]
    word = _DENY_WORD.search(head)
    cut, literal, last, parts = word.end() if word else len(head), 0, 0, []
    for hole in _HOLE.finditer(head):
        if hole.start() >= cut and literal + (hole.start() - last) >= OPENING_LITERAL:
            break
        parts.append(re.escape(head[last:hole.start()]))
        parts.append('.{0,2000}?')
        literal += hole.start() - last
        last = hole.end()
        cut = max(cut, last)
    room = max(cut - last, OPENING_LITERAL - literal)
    parts.append(re.escape(head[last:last + room]))
    literal += len(head[last:last + room])
    if literal < MIN_LITERAL or not re.match(r'[A-Za-z\[`]', head):
        return None
    return literal, ''.join(parts)


_SIGNATURES = {}


def emitted_signatures(hooks_dir=None):
    """[(literal length, compiled opening, owner modules)], longest first: every deny string in the hooks' source
    (a string constant, or an f-string with each field a hole, outside a docstring, whose first 70 characters name a block
    or a denial), keyed by the module that holds it."""
    folder = hooks_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks')
    present = stat_present(os.path.join(folder, name) for name in sorted(os.listdir(folder)) if name.endswith('.py'))
    names = [os.path.basename(path) for path, _ in present]
    stamp = tuple((os.path.basename(path), st.st_mtime_ns) for path, st in present)
    key = (os.path.abspath(folder), stamp)
    if key not in _SIGNATURES:  # parsing the hooks takes seconds: once per process and unchanged folder
        _SIGNATURES[key] = _read_signatures(folder, names)
    return _SIGNATURES[key]


def _read_signatures(folder, names):
    owners = {}
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding='utf-8') as handle:
                tree = ast.parse(handle.read())
        except (OSError, SyntaxError, ValueError):
            continue
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                text = node.value
            elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
                text = ''.join(part.value if isinstance(part, ast.Constant) else '{}' for part in node.values)
            if text and _DENY_WORD.search(text.lstrip()[:70]):
                built = _pattern(text)
                if built:
                    owners.setdefault(built, set()).add(name[:-3])
    return sorted(((literal, re.compile(pattern, re.DOTALL), sorted(who))
                   for (literal, pattern), who in owners.items()), key=lambda row: -row[0])


def attribute(text, signatures):
    """The hook a hook-error result belongs to, or None when `text` is no hook error: the owner of the longest
    deny opening it starts with (owners joined by `+` when modules share one), else the script its own header
    names when that is no dispatcher, else `unattributed`."""
    header = HOOK_HEADER.match(text)
    if not header:
        return None
    body = text[header.end():]
    for _literal, pattern, who in signatures:
        if pattern.match(body):
            return '+'.join(who)
    scripts = re.findall(r'(\w+)\.py', header.group(1) or '')
    return scripts[-1] if scripts and scripts[-1] not in DISPATCHERS else 'unattributed'


def _fingerprint(name, tool_input):
    if isinstance(tool_input, dict) and isinstance(tool_input.get('command'), str):
        body = tool_input['command']
    else:
        body = json.dumps(tool_input, sort_keys=True, default=str)
    return hashlib.sha1(('%s\0%s' % (name, body)).encode('utf-8', 'replace')).hexdigest()[:12]


def parse_file(path):
    """Return the denial candidates, tool-result calls and parse flag of one transcript. A candidate is an error
    result that opens with a hook-error header or matches a category signature; it keeps the text's opening, so
    a reworded deny reclassifies it on the next scan with no re-read."""
    uses, candidates, calls, parsed_lines = {}, [], {}, 0
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
                    uses[item['id']] = (item.get('name') or '?', _fingerprint(item.get('name'), item.get('input')))
                elif item.get('type') == 'tool_result' and item.get('tool_use_id') in uses:
                    day = utc_day(line.get('timestamp'))
                    session = line.get('sessionId') or stem
                    key = '%s|%s' % (day, session)
                    calls[key] = calls.get(key, 0) + 1
                    if item.get('is_error') is True:
                        text = flatten(item.get('content'))
                        if HOOK_HEADER.match(text) or classify(text):
                            tool, fp = uses[item['tool_use_id']]
                            candidates.append({'day': day, 'session': session, 'ts': line.get('timestamp') or '',
                                               'tool': tool, 'fp': fp, 'head': text[:SIGNATURE_WINDOW]})
    return {'candidates': candidates, 'calls': calls, 'unparseable': parsed_lines == 0, 'v': CACHE_VERSION}


def _classified(candidates, signatures):
    """Each candidate with its category (the legacy table) and its hook (the emitting module), either maybe None."""
    rows = []
    for item in candidates:
        cat = classify(item['head'])
        hook = attribute(item['head'], signatures) or ('client' if cat in CLIENT_CATEGORIES else None)
        if cat or hook:
            rows.append({**{k: item[k] for k in ('day', 'session', 'ts', 'tool', 'fp')}, 'cat': cat, 'hook': hook})
    return rows


def window_start(days, now=None):
    """The first UTC day (YYYY-MM-DD) of a `days`-day window ending today, or None for no window."""
    if not days:
        return None
    return ((now or datetime.now(timezone.utc)) - timedelta(days=days - 1)).strftime('%Y-%m-%d')


def hook_report(rows, since=None):
    """Per (hook, tool), over the rows with a hook on or after `since`: denials, sessions, repeat, identical."""
    seen_hook, seen_call, table = set(), set(), {}
    for row in sorted((r for r in rows if r.get('hook') and (since is None or r['day'] >= since)),
                      key=lambda r: (r['ts'], r['session'])):
        entry = table.setdefault((row['hook'], row['tool']), {'denials': 0, 'sessions': set(), 'repeat': 0, 'identical': 0})
        entry['denials'] += 1
        entry['sessions'].add(row['session'])
        first = (row['session'], row['hook'], row['tool'])
        entry['repeat'] += first in seen_hook
        seen_hook.add(first)
        call = (row['session'], row['hook'], row['fp'])
        entry['identical'] += call in seen_call
        seen_call.add(call)
    return sorted(({'hook': hook, 'tool': tool, 'denials': e['denials'], 'sessions': len(e['sessions']),
                    'repeat': e['repeat'], 'identical': e['identical']} for (hook, tool), e in table.items()),
                  key=lambda r: (-r['denials'], r['hook'], r['tool']))


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
        replace_path(tmp, cache_path)
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


def scan(roots, cache_path=None, hooks_dir=None):
    """{'denials': rows with a category, 'guards': rows with a hook, 'calls', 'coverage'}."""
    signatures = emitted_signatures(hooks_dir)
    coverage = {'files': 0, 'read': 0, 'cached': 0, 'unparseable': 0, 'unreadable': 0, 'roots': 0, 'gaps': []}
    cache = load_cache(cache_path)
    entries, rows, calls = {}, [], {}
    for path in transcript_paths(roots, coverage):
        try:
            stat = os.stat(path)
            known = cache.get(path)
            if (isinstance(known, dict) and known.get('size') == stat.st_size and known.get('mtime') == stat.st_mtime
                    and known.get('v') == CACHE_VERSION):
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
        rows.extend(_classified(entry.get('candidates', []), signatures))
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
    return {'denials': [row for row in rows if row['cat']], 'guards': [row for row in rows if row['hook']],
            'calls': calls, 'coverage': coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--roots', nargs='+', default=None)
    parser.add_argument('--cache', default=None)
    parser.add_argument('--days', type=int, default=None, help='count only the last N UTC days, today included')
    parser.add_argument('--hooks', default=None, help="hooks folder whose deny strings name each hook (default: this checkout's)")
    args = parser.parse_args()
    since = window_start(args.days)
    found = scan(args.roots or default_roots(), cache_path=args.cache, hooks_dir=args.hooks)
    counts = {cat: 0 for cat, _ in SIGNATURES}
    for row in found['denials']:
        if since is None or row['day'] >= since:
            counts[row['cat']] += 1
    for cat, _ in SIGNATURES:
        print('%s %d' % (cat, counts[cat]))
    for row in hook_report(found['guards'], since):
        print('hook %(hook)s %(tool)s denials=%(denials)d sessions=%(sessions)d repeat=%(repeat)d identical=%(identical)d' % row)
    print('calls %d' % sum(n for key, n in found['calls'].items() if since is None or key.split('|', 1)[0] >= since))
    print(json.dumps(found['coverage']))


if __name__ == '__main__':
    main()
