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

Usage:  python .claude/tools/verify_doc_citations.py <doc.md> [--repo <root>]
Exit:   0 = all citations resolve, 1 = unresolved citations found, 2 = bad invocation.
"""

import argparse
import os
import re
import subprocess
import sys

# Backticked identifiers only — prose PascalCase is too noisy to gate on.
IDENT = re.compile(r'`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`')
# path/to/File.ext:123
FILE_LINE = re.compile(r'`?([\w./\\-]+\.(?:cs|tscn|tres|md|py|js|json)):(\d+)`?')

# Symbol existence is proven ONLY by code. Markdown, notes, and docs are the distrusted source —
# a symbol whose sole occurrences are in `.md` is a claim about the codebase, not the codebase.
SOURCE_EXT = ('.cs', '.gd', '.tscn', '.tres', '.godot')

# Words that look like symbols but are vocabulary, not code.
STOPWORDS = {
    'PascalCase', 'README', 'TODO', 'FIXME', 'NOTE', 'WARNING', 'CRITICAL',
    'ACCEPT', 'MAJOR_REWORK', 'MINOR_REVISION', 'APPROVE', 'REVISE',
    'Godot', 'Jmodot', '{{PROJECT_NAME}}', 'Inspector', 'Resource', 'Node',
    'Claude', 'Obsidian', 'MEMORY', 'CLAUDE',
}


def is_symbol_shaped(name: str) -> bool:
    """PascalCase-ish, mixed case, long enough to not be an abbreviation."""
    leaf = name.split('.')[-1]
    if len(leaf) < 4 or leaf in STOPWORDS:
        return False
    if not leaf[0].isupper():
        return False
    return any(c.islower() for c in leaf) and any(c.isupper() for c in leaf[1:])


LINE_COMMENT = re.compile(r'//.*$', re.MULTILINE)
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


def have_ripgrep() -> bool:
    try:
        subprocess.run(['rg', '--version'], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def strip_comments(src: str) -> str:
    """Remove `//`, `///` and `/* */` — the sources this check exists to distrust.

    A symbol that survives ONLY inside a comment is a documentation claim, not code. This is the
    mechanism of the whole tool: `NavIntentResolver` and `ActiveLeafState` both appear in the repo
    and both are absent from it.
    """
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', src))


def build_index(repo: str):
    """One walk → (basename -> [paths], [source paths]). Everything else reuses it."""
    by_base, sources = {}, []
    skip = {'.git', 'obj', 'bin', '.search-index', 'logs', '.godot', 'node_modules', '.import'}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            full = os.path.join(root, f)
            by_base.setdefault(f, []).append(full)
            if f.endswith(SOURCE_EXT):
                sources.append(full)
    return by_base, sources


def symbol_exists(name: str, repo: str, sources, use_rg: bool) -> bool:
    """True only if the leaf identifier appears in NON-COMMENT source."""
    leaf = name.split('.')[-1]
    candidates = sources
    if use_rg:
        r = subprocess.run(
            ['rg', '-l', '--fixed-strings', leaf, repo],
            capture_output=True, text=True,
        )
        hits = [p for p in r.stdout.splitlines() if p.strip()]
        if not hits:
            return False
        candidates = [p for p in hits if p.endswith(SOURCE_EXT)]
    for path in candidates:
        try:
            with open(path, encoding='utf-8', errors='ignore') as fh:
                src = fh.read()
        except OSError:
            continue
        if leaf not in src:
            continue
        if leaf in strip_comments(src):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('doc')
    ap.add_argument('--repo', default='.')
    args = ap.parse_args()

    if not os.path.isfile(args.doc):
        print(f'ERROR: no such doc: {args.doc}', file=sys.stderr)
        return 2

    with open(args.doc, encoding='utf-8', errors='ignore') as fh:
        text = fh.read()

    repo = os.path.abspath(args.repo)
    use_rg = have_ripgrep()
    by_base, sources = build_index(repo)

    bad_paths, bad_symbols = [], []

    def resolve(path: str):
        """Cited paths are often bare basenames — resolve them against the index."""
        norm = path.replace('\\', '/')
        if os.path.isabs(norm) and os.path.isfile(norm):
            return norm
        direct = os.path.join(repo, norm)
        if os.path.isfile(direct):
            return direct
        hits = by_base.get(norm.split('/')[-1], [])
        return hits[0] if len(hits) == 1 else (hits[0] if hits else None)

    seen_paths = set()
    for path, line in FILE_LINE.findall(text):
        key = (path, line)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        full = resolve(path)
        if full is None:
            bad_paths.append(f'{path}:{line}  — no such file in repo')
            continue
        try:
            with open(full, encoding='utf-8', errors='ignore') as fh:
                total = sum(1 for _ in fh)
            if int(line) > total:
                bad_paths.append(f'{path}:{line}  — file has only {total} lines')
        except OSError:
            bad_paths.append(f'{path}:{line}  — unreadable')

    cited_files = {p.replace('\\', '/').split('/')[-1] for p, _ in FILE_LINE.findall(text)}
    for name in sorted(set(IDENT.findall(text))):
        if not is_symbol_shaped(name) or name in cited_files:
            continue
        if not symbol_exists(name, repo, sources, use_rg):
            bad_symbols.append(name)

    print(f'Citation check — {args.doc}')
    print(f'  repo: {repo}   ripgrep: {"yes" if use_rg else "no (slow path)"}')
    print(f'  file:line citations checked: {len(seen_paths)}')

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

    if not bad_paths and not bad_symbols:
        print('\nAll citations resolve.')
        return 0

    print(f'\nFAILED: {len(bad_paths)} path(s), {len(bad_symbols)} symbol(s) unresolved.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
