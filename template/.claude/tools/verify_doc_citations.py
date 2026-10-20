#!/usr/bin/env python3
"""Resolve the citations in a design/plan doc against the repo before an agent panel reads it.

Catches the cheap half of the false-premise class: a named symbol that does not exist, or a
`file:line` pointing past EOF. Three consecutive design drafts died on exactly this
(`TryGetFirstCompanionOfType`, `ActiveLeafState`, `NavIntentResolver`) — each one grep-detectable
in milliseconds, each one found instead by an adversarial panel costing ~600k subagent tokens.

Does NOT catch a claim that is wrong ABOUT a symbol that exists ("X has no callers", "these two
methods are identical"). That still needs reading. This is the mechanical floor, not the ceiling.

Expected false positives: a doc that deliberately names absent symbols — a retraction/disproved
table, a "do not re-derive" list. Those hits are the doc working correctly; confirm the symbol is
named AS absent, then ignore. Never "fix" a retraction table to satisfy this check.

Also serves /memory_audit lens 7: `--kinds` adds `path`, `command` and `flag` citations, harness
`module.attr` symbols resolve against `.claude/**/module.py`, and `--json` emits one object with a
found/missing/unparseable status per citation. A citation whose form cannot be resolved
deterministically (a `<x>`/`{x}`/`*`/`...` placeholder, a path outside the repo, a flag with no tool in
its span) is `unparseable`, reported apart from `missing` and never a failure. `[[wikilinks]]` are
not citations. Inline Markdown link destinations and explicit `./` and `../` paths resolve
from the citing file's directory; bare repo paths in code spans retain repo-root resolution.

Usage:  python .claude/tools/verify_doc_citations.py <doc.md>|@<listfile>... [--repo <root>] [--json]
            [--kinds symbol,fileline,path,command,flag | all]   (default: symbol,fileline)
            [--only found,missing,unparseable]   (JSON rows to print; counts stay complete)
Exit:   0 = nothing missing (unparseable alone passes), 1 = missing citations found,
        2 = bad invocation (a missing doc, an unknown kind or status).
`@listfile` holds one doc path per line, for a file set too long for the command line. With
`--only`, a file left with no rows drops from `files`; `files_checked` still counts every doc.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adaptation  # noqa: E402

# Backticked identifiers only — prose PascalCase is too noisy to gate on.
IDENT = re.compile(r'`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`')

# The repo's adaptation file adds its code extensions (`source_extensions`) to these. Symbol
# existence is proven ONLY by code: markdown, notes, and docs are the distrusted source — a symbol
# whose sole occurrences are in `.md` is a claim about the codebase, not the codebase.
DOC_EXT = ('.md', '.py', '.js', '.json')

# Words that look like symbols but are vocabulary, not code; the repo adds its own (`symbol_stopwords`).
STOPWORDS = {
    'PascalCase', 'README', 'TODO', 'FIXME', 'NOTE', 'WARNING', 'CRITICAL',
    'ACCEPT', 'MAJOR_REWORK', 'MINOR_REVISION', 'APPROVE', 'REVISE',
    'Claude', 'Obsidian', 'MEMORY', 'CLAUDE',
}


def file_line_pattern(source_ext):
    """path/to/File.ext:123 for a doc extension or a configured code extension."""
    exts = sorted({e.lstrip('.') for e in DOC_EXT + tuple(source_ext)}, key=len, reverse=True)
    return re.compile(r'`?([\w./\\-]+\.(?:' + '|'.join(re.escape(e) for e in exts) + r')):(\d+)`?')


def is_symbol_shaped(name: str, stopwords=STOPWORDS) -> bool:
    """PascalCase-ish, mixed case, long enough to not be an abbreviation."""
    leaf = name.split('.')[-1]
    if len(leaf) < 4 or leaf in stopwords:
        return False
    if not leaf[0].isupper():
        return False
    return any(c.islower() for c in leaf) and any(c.isupper() for c in leaf[1:])


KINDS = ('symbol', 'fileline', 'path', 'command', 'flag')
DEFAULT_KINDS = ('symbol', 'fileline')
STATUSES = ('found', 'missing', 'unparseable')
PATH_EXT = DOC_EXT + ('.jsonl', '.sh', '.ps1', '.yaml', '.yml', '.txt')
TOOL_EXT = ('.py', '.js', '.sh', '.ps1')

# Claude Code built-in slash commands the memory store cites; everything else must be a project
# command or skill. A name belongs here only when it is a documented Claude Code built-in.
BUILTIN_COMMANDS = frozenset({'compact', 'mcp', 'model'})

SPAN = re.compile(r'`([^`\n]+)`')
FENCE = re.compile(r'^```.*?^```[ \t]*$', re.MULTILINE | re.DOTALL)
WIKILINK = re.compile(r'\[\[[^\]\n]*\]\]')
MARKDOWN_LINK = re.compile(r'\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+"[^"\n]*"|\s+\'[^\'\n]*\')?\s*\)')
PLACEHOLDER = re.compile(r'<[^<>\s]+>|\{[^{}]*\}|\*|\.\.\.')
COMMAND = re.compile(r'(?<![\w/.~-])/([a-z][a-z0-9_-]*)(?![\w/-])(?!\.\w)')
FLAG = re.compile(r'^--[A-Za-z][\w-]*(?:=.*)?$')
HARNESS_SYMBOL = re.compile(r'^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$')

LINE_COMMENT = re.compile(r'//.*$', re.MULTILINE)
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


def strip_comments(src: str) -> str:
    """Remove `//`, `///` and `/* */` — the sources this check exists to distrust.

    A symbol that survives ONLY inside a comment is a documentation claim, not code. This is the
    mechanism of the whole tool: `NavIntentResolver` and `ActiveLeafState` both appear in the repo
    and both are absent from it.
    """
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', src))


def ignored_dirs(repo: str) -> set:
    """Repo-relative `dir/` entries git ignores (one `git ls-files` call); empty outside a git repo.

    Build and engine output folders are pruned because the repo's ignore rules exclude them. A nested
    repository's own ignore rules are not read. Ignored folders under `.claude/` stay indexed: the
    harness tree holds files docs cite by path.
    """
    try:
        p = subprocess.run(['git', '-C', repo, 'ls-files', '--others', '--ignored', '--exclude-standard',
                            '--directory'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    if p.returncode != 0:
        return set()
    return {ln.replace('\\', '/') for ln in p.stdout.splitlines()
            if ln.endswith('/') and not ln.startswith('.claude/')}


def build_index(repo: str, source_ext=()):
    """One walk → (basename -> [paths], [source paths]). Everything else reuses it."""
    by_base, sources = {}, []
    skip = {'.git', '.search-index', 'logs', 'node_modules', 'worktrees', 'obj', 'bin', '.godot', '.import'}
    # Scratch holds stale tree snapshots: a basename or symbol match there would vouch for a
    # file line or a symbol the live tree no longer has.
    ignored = ignored_dirs(repo) | {'.claude/scratch/'}
    for root, dirs, files in os.walk(repo):
        rel = os.path.relpath(root, repo).replace('\\', '/')
        prefix = '' if rel == '.' else rel + '/'
        dirs[:] = [d for d in dirs if d not in skip and prefix + d + '/' not in ignored]
        for f in files:
            full = os.path.join(root, f)
            by_base.setdefault(f, []).append(full)
            if f.endswith(source_ext):
                sources.append(full)
    return by_base, sources


class Resolver:
    """One repo walk, shared by every file and every citation kind."""

    def __init__(self, repo: str):
        self.repo = repo
        claude = os.path.join(repo, '.claude')
        self.source_ext = tuple(e for e in adaptation.get(claude, 'source_extensions') if isinstance(e, str) and e)
        self.stopwords = STOPWORDS | {w for w in adaptation.get(claude, 'symbol_stopwords') if isinstance(w, str)}
        self.path_ext = PATH_EXT + tuple(e for e in self.source_ext if e not in PATH_EXT)
        self.file_line = file_line_pattern(self.source_ext)
        self.by_base, self.sources = build_index(repo, self.source_ext)
        self._module_text = {}
        self._symbol = {}
        self._code = None
        self._stripped = {}

    def relative_target(self, token: str, doc: str | None):
        """Explicit ./ and ../ citations resolve from the citing doc, never by basename."""
        if doc is None or not token.startswith(('./', '../')):
            return None
        citing = doc if os.path.isabs(doc) else os.path.join(self.repo, doc)
        target = os.path.abspath(os.path.join(os.path.dirname(citing), token))
        root = os.path.abspath(self.repo)
        if os.path.commonpath((root, target)) != root:
            return 'unparseable', target
        return ('found' if os.path.exists(target) else 'missing'), target

    def resolve_file(self, path: str, doc: str | None = None):
        """Explicit relative paths use the citing doc; bare names keep index resolution."""
        norm = path.replace('\\', '/')
        relative = self.relative_target(norm, doc)
        if relative:
            return relative[1] if relative[0] == 'found' and os.path.isfile(relative[1]) else None
        if os.path.isabs(norm) and os.path.isfile(norm):
            return norm
        direct = os.path.join(self.repo, norm)
        if os.path.isfile(direct):
            return direct
        hits = self.by_base.get(norm.split('/')[-1], [])
        return hits[0] if hits else None

    def resolve_path(self, token: str, doc: str | None = None):
        """Explicit relative paths use the citing doc; bare repo paths keep root resolution."""
        norm = token.replace('\\', '/')
        relative = self.relative_target(norm, doc)
        if relative:
            status, _target = relative
            return status, 'path outside the repo' if status == 'unparseable' else ''
        if norm.startswith('~'):
            return 'unparseable', 'path outside the repo (~)'
        if os.path.isabs(norm) or re.match(r'^[A-Za-z]:/', norm):
            root = self.repo.replace('\\', '/').rstrip('/') + '/'
            if not norm.lower().startswith(root.lower()):
                return 'unparseable', 'absolute path outside the repo'
            norm = norm[len(root):]
        for base in (self.repo, os.path.join(self.repo, '.claude')):
            if os.path.exists(os.path.join(base, norm)):
                return 'found', ''
        hits = self.by_base.get(norm.rstrip('/').split('/')[-1], []) if '/' not in norm.rstrip('/') else []
        if len(hits) == 1:
            return 'found', 'unique basename match'
        if hits:
            return 'found', f'basename matches {len(hits)} files'
        return 'missing', 'no such file or directory in repo'

    def symbol_in_code(self, name: str) -> bool:
        """True only if the leaf appears in NON-COMMENT source; each source is read and stripped once."""
        leaf = name.split('.')[-1]
        if leaf not in self._symbol:
            if self._code is None:
                self._code = []
                for path in self.sources:
                    try:
                        with open(path, encoding='utf-8', errors='ignore') as fh:
                            self._code.append(fh.read())
                    except OSError:
                        continue
                self._stripped = {}
            hit = False
            for i, src in enumerate(self._code):
                if leaf not in src:
                    continue
                if i not in self._stripped:
                    self._stripped[i] = strip_comments(src)
                if leaf in self._stripped[i]:
                    hit = True
                    break
            self._symbol[leaf] = hit
        return self._symbol[leaf]

    def command_exists(self, name: str) -> bool:
        claude = os.path.join(self.repo, '.claude')
        return (name in BUILTIN_COMMANDS
                or os.path.isfile(os.path.join(claude, 'commands', name + '.md'))
                or os.path.isfile(os.path.join(claude, 'skills', name, 'SKILL.md')))

    def harness_module(self, module: str):
        """Path of `<repo>/.claude/**/<module>.py` when exactly one exists, else None."""
        prefix = os.path.join(self.repo, '.claude') + os.sep
        hits = [p for p in self.by_base.get(module + '.py', []) if p.startswith(prefix)]
        return hits[0] if len(hits) == 1 else None

    def module_text(self, path: str) -> str:
        if path not in self._module_text:
            try:
                with open(path, encoding='utf-8', errors='ignore') as fh:
                    self._module_text[path] = fh.read()
            except OSError:
                self._module_text[path] = ''
        return self._module_text[path]

    def module_defines(self, path: str, attr: str) -> bool:
        """A top-level `def`, `class` or assignment — a comment naming the attr does not count."""
        a = re.escape(attr)
        pat = rf'^(?:(?:async\s+)?def|class)\s+{a}\b|^{a}\s*(?::[^=\n]+)?=(?!=)'
        return re.search(pat, self.module_text(path), re.MULTILINE) is not None

    def tool_has_flag(self, path: str, flag: str) -> bool:
        return re.search(re.escape(flag) + r'(?![\w-])', self.module_text(path)) is not None


def clean_token(tok: str) -> str:
    tok = tok.strip('"\'()[],;')
    return tok if tok.endswith('/') else tok.rstrip(':.')


def is_path_token(tok: str, path_ext=PATH_EXT) -> bool:
    if not tok or tok.startswith('--') or '://' in tok or re.search(r':\d+', tok):
        return False
    if re.fullmatch(r'/[a-z][a-z0-9_-]*', tok) or re.fullmatch(r'/[^/.]*', tok) or re.fullmatch(r'\.\w+', tok):
        return False
    return '/' in tok or tok.lower().endswith(tuple(path_ext))


def spans_of(text: str):
    """Every backtick span plus each line of a fenced block; also the text with fences removed."""
    out = []
    for m in FENCE.finditer(text):
        out.extend(line for line in m.group(0).splitlines()[1:-1] if line.strip())
    rest = FENCE.sub('', text)
    out.extend(m.group(1) for m in SPAN.finditer(rest))
    return out, rest


def collect(text: str, kinds, rs: Resolver, doc: str | None = None):
    """Citation rows {kind,text,status,detail}, one per distinct (kind, text), in first-seen order."""
    rows, seen = [], set()

    def add(kind, cite, status, detail=''):
        if kind in kinds and (kind, cite) not in seen:
            seen.add((kind, cite))
            rows.append({'kind': kind, 'text': cite, 'status': status, 'detail': detail})

    text = WIKILINK.sub('', text)

    cited_files = {p.replace('\\', '/').split('/')[-1] for p, _ in rs.file_line.findall(text)}
    for path, line in rs.file_line.findall(text):
        cite = f'{path}:{line}'
        if ('fileline', cite) in seen:
            continue
        relative = rs.relative_target(path.replace('\\', '/'), doc)
        if relative and relative[0] == 'unparseable':
            add('fileline', cite, 'unparseable', 'path outside the repo')
            continue
        full = rs.resolve_file(path, doc)
        if full is None:
            add('fileline', cite, 'missing', 'no such file in repo')
            continue
        try:
            with open(full, encoding='utf-8', errors='ignore') as fh:
                total = sum(1 for _ in fh)
            if int(line) > total:
                add('fileline', cite, 'missing', f'file has only {total} lines')
            else:
                add('fileline', cite, 'found')
        except OSError:
            add('fileline', cite, 'missing', 'unreadable')

    if 'symbol' in kinds:
        for name in sorted(set(IDENT.findall(text))):
            hm = HARNESS_SYMBOL.match(name)
            module = rs.harness_module(hm.group(1)) if hm else None
            if module and ('.' + hm.group(2)) not in rs.path_ext:
                attr = hm.group(2)
                ok = rs.module_defines(module, attr)
                add('symbol', name, 'found' if ok else 'missing',
                    '' if ok else f'{os.path.basename(module)} defines no `{attr}`')
            elif is_symbol_shaped(name, rs.stopwords) and name not in cited_files:
                if not rs.source_ext:
                    add('symbol', name, 'unparseable', 'no source_extensions configured')
                    continue
                ok = rs.symbol_in_code(name)
                add('symbol', name, 'found' if ok else 'missing',
                    '' if ok else 'zero occurrences in non-comment code')

    spans, rest = spans_of(text)
    if 'path' in kinds:
        for match in MARKDOWN_LINK.finditer(rest):
            target = match.group(1) or match.group(2)
            if target.startswith(('#', '//')) or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target):
                continue
            target = target.split('#', 1)[0]
            if PLACEHOLDER.search(target):
                add('path', target, 'unparseable', 'placeholder')
            else:
                relative = target if target.startswith(('./', '../', '/', '~')) else './' + target
                status, detail = rs.resolve_path(relative, doc)
                add('path', target, status, detail)
    if 'path' in kinds or 'flag' in kinds:
        for span in spans:
            tokens = [t for t in (clean_token(x) for x in span.split()) if t]
            tool = None  # (status, token) of the first tool-shaped path in this span
            for tok in tokens:
                if not is_path_token(tok, rs.path_ext):
                    continue
                if PLACEHOLDER.search(tok):
                    add('path', tok, 'unparseable', 'placeholder')
                    status = 'unparseable'
                else:
                    status, detail = rs.resolve_path(tok, doc)
                    add('path', tok, status, detail)
                if tool is None and tok.lower().endswith(TOOL_EXT):
                    tool = (status, tok)
            for tok in tokens:
                if not FLAG.match(tok):
                    continue
                flag = tok.split('=')[0]
                if tool is None:
                    add('flag', flag, 'unparseable', 'no tool path in the same span')
                    continue
                full = rs.resolve_file(tool[1], doc) if tool[0] == 'found' else None
                if full is None:
                    add('flag', flag, 'unparseable', f'tool {tool[1]} did not resolve')
                elif rs.tool_has_flag(full, flag):
                    add('flag', flag, 'found', f'in {tool[1]}')
                else:
                    add('flag', flag, 'missing', f'{tool[1]} does not contain {flag}')

    if 'command' in kinds:
        for m in COMMAND.finditer(rest):
            name = m.group(1)
            found = rs.command_exists(name)
            add('command', '/' + name, 'found' if found else 'missing',
                '' if found else 'no command, skill or built-in of that name')
    return rows


def report_text(doc: str, rows, kinds, rs: Resolver) -> bool:
    """The historical report shape; True when the doc has no missing citation."""
    def bad(kind):
        return [r for r in rows if r['kind'] == kind and r['status'] == 'missing']

    bad_paths = [f"{r['text']}  — {r['detail']}" for r in bad('fileline') + bad('path')]
    bad_symbols = [r['text'] for r in bad('symbol')]
    bad_commands = [f"{r['text']}  — {r['detail']}" for r in bad('command')]
    bad_flags = [f"{r['text']}  — {r['detail']}" for r in bad('flag')]
    unparseable = [r for r in rows if r['status'] == 'unparseable']

    print(f'Citation check — {doc}')
    print(f'  repo: {rs.repo}')
    print(f'  file:line citations checked: {sum(1 for r in rows if r["kind"] == "fileline")}')
    for kind in ('path', 'command', 'flag'):
        if kind in kinds:
            print(f'  {kind} citations checked: {sum(1 for r in rows if r["kind"] == kind)}')

    if bad_paths:
        print(f'\nUNRESOLVED PATHS ({len(bad_paths)}):')
        for b in bad_paths:
            print(f'  - {b}')

    if bad_symbols:
        print(f'\nUNRESOLVED SYMBOLS ({len(bad_symbols)}) — zero occurrences in the repo:')
        for b in bad_symbols:
            print(f'  - {b}')
        print('\n  Each is either a typo, a deleted type, or a claim sourced from documentation')
        print('  rather than code. Resolve every one BEFORE dispatching a review panel.')

    for label, items in (('COMMANDS', bad_commands), ('FLAGS', bad_flags)):
        if items:
            print(f'\nUNRESOLVED {label} ({len(items)}):')
            for b in items:
                print(f'  - {b}')

    if unparseable:
        print(f'\nUNPARSEABLE ({len(unparseable)}) — no deterministic resolution, not a failure:')
        for r in unparseable:
            print(f'  - [{r["kind"]}] {r["text"]}  — {r["detail"]}')

    if not (bad_paths or bad_symbols or bad_commands or bad_flags):
        print('\nAll citations resolve.')
        return True

    parts = [f'{len(bad_paths)} path(s)', f'{len(bad_symbols)} symbol(s)']
    if 'command' in kinds:
        parts.append(f'{len(bad_commands)} command(s)')
    if 'flag' in kinds:
        parts.append(f'{len(bad_flags)} flag(s)')
    print(f'\nFAILED: {", ".join(parts)} unresolved.')
    return False


def parse_list(raw: str, allowed, noun: str):
    if raw == 'all':
        return allowed
    items = tuple(k.strip() for k in raw.split(',') if k.strip())
    unknown = [k for k in items if k not in allowed]
    if unknown or not items:
        raise ValueError(f'unknown {noun}(s): {", ".join(unknown) or repr(raw)}; '
                         f'choose from {",".join(allowed)} or all')
    return items


def main() -> int:
    ap = argparse.ArgumentParser(fromfile_prefix_chars='@')
    ap.add_argument('docs', nargs='+', metavar='doc')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--kinds', default=','.join(DEFAULT_KINDS))
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--only', default='all')
    args = ap.parse_args()

    try:
        kinds = parse_list(args.kinds, KINDS, 'kind')
        only = parse_list(args.only, STATUSES, 'status')
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2
    for doc in args.docs:
        if not os.path.isfile(doc):
            print(f'ERROR: no such doc: {doc}', file=sys.stderr)
            return 2

    repo = os.path.abspath(args.repo)
    rs = Resolver(repo)
    per_file = []
    for doc in args.docs:
        with open(doc, encoding='utf-8', errors='ignore') as fh:
            per_file.append((doc, collect(fh.read(), kinds, rs, os.path.abspath(doc))))

    counts = {'found': 0, 'missing': 0, 'unparseable': 0}
    for _, rows in per_file:
        for r in rows:
            counts[r['status']] += 1

    if args.json:
        files = [{'path': d, 'citations': [r for r in rows if r['status'] in only]}
                 for d, rows in per_file]
        if only != STATUSES:
            files = [f for f in files if f['citations']]
        print(json.dumps({'repo': repo, 'files_checked': len(per_file), 'files': files,
                          'counts': counts}, ensure_ascii=False, indent=1))
    else:
        for i, (doc, rows) in enumerate(per_file):
            if i:
                print()
            report_text(doc, rows, kinds, rs)
    return 1 if counts['missing'] else 0


if __name__ == '__main__':
    sys.exit(main())
