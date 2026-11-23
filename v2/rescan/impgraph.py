"""Python import closure over hooks/ and tools/ and scripts/: who imports whom (ast, plus deferred imports and sys.path hooks dirs)."""
import ast, os, json, sys
ROOT='.'
mods={}
for d in ('hooks','tools','scripts','scripts/lib'):
    for f in os.listdir(d):
        if f.endswith('.py'): mods.setdefault(f[:-3],[]).append(f'{d}/{f}')
def imports(path):
    try: t=ast.parse(open(path,encoding='utf-8').read())
    except Exception: return set()
    out=set()
    for n in ast.walk(t):
        if isinstance(n,ast.Import):
            for a in n.names: out.add(a.name.split('.')[0])
        elif isinstance(n,ast.ImportFrom):
            if n.module: out.add(n.module.split('.')[0])
            for a in n.names: out.add(a.name)
        elif isinstance(n,ast.Call) and getattr(n.func,'attr',getattr(n.func,'id',''))=='import_module' and n.args and isinstance(n.args[0],ast.Constant): out.add(str(n.args[0].value).split('.')[0])
    return {m for m in out if m in mods}
graph={}
for m,ps in mods.items():
    for p in ps: graph[p]={q for i in imports(p) for q in mods[i] if q!=p}
json.dump({k:sorted(v) for k,v in graph.items()},open('../../v2/rescan/imports.json','w'),indent=0)
print(len(graph))
