"""B15 re-scan: referrers of each helper in the lane tree. Matches rel path, basename, python/js import stem, and backslash-written paths."""
import os, re, sys, json
BS = chr(92)
ROOT = 'template/.claude'
TEXT = ('.py','.js','.mjs','.cjs','.sh','.ps1','.md','.json','.jsonl','.yml','.yaml','.txt','.tmpl','.cs')
files = []
for d, _, fs in os.walk(ROOT):
    if '__pycache__' in d or 'node_modules' in d: continue
    for f in fs:
        files.append(os.path.relpath(os.path.join(d, f), ROOT).replace(BS, '/'))
txt = {}
for p in files:
    if p.endswith(TEXT):
        try: txt[p] = open(os.path.join(ROOT, p), encoding='utf-8', errors='replace').read().replace(BS, '/')
        except Exception: pass
# repo-root files that may name tree files
for extra in ('baseline.manifest.json','README.md','bootstrap.sh','publish.sh','tools/layer_entries.json'):
    pass
def is_test(p): return p.startswith('tests/')
def cands():
    out = []
    for p in files:
        if p.startswith(('hooks/','tools/','workflows/','scripts/','schemas/','tests/')) and p.endswith(('.py','.js','.mjs','.sh','.ps1','.json')):
            out.append(p)
    return out
TOK = re.compile(r"[A-Za-z0-9_./-]+")
WORDS = re.compile(r"[A-Za-z0-9_]+")
ftok = {q: set(TOK.findall(t)) for q, t in txt.items()}
fword = {q: set(WORDS.findall(t)) for q, t in txt.items()}
def refs(p, alive):
    base = p.rsplit('/',1)[-1]; stem = base.rsplit('.',1)[0]
    out = []
    for q in alive:
        if q == p: continue
        T = ftok[q]
        hit = base in T or any(t.endswith('/' + p) or t == p for t in T if base in t)
        if not hit and p.endswith(('.py','.js','.mjs')) and (len(stem) >= 7 or stem.startswith('_')):
            hit = stem in fword[q]
        if hit: out.append(q)
    return out
if __name__ == '__main__':
    alive = set(txt)
    res = {p: refs(p, alive) for p in cands()}
    json.dump(res, open('v2/rescan/refs.json','w'), indent=1)
    print(len(res))
