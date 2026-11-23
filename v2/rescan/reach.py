import json,collections
r=json.load(open('v2/rescan/refs.json'))
t=lambda p:p.startswith('tests/')
codeDirs=('hooks/','tools/','workflows/','scripts/','schemas/')
fwd=collections.defaultdict(set)
for p,v in r.items():
    for q in v: fwd[q].add(p)
cand=set(r)
roots=set()
for p,v in r.items():
    for q in v:
        if q not in cand: roots.add(q)
# roots: non-candidate referrers (docs, settings, commands, skills); plus candidate files that are entry points are discovered via them
seen=set(); st=[]
for q in roots:
    for p in fwd[q]: 
        if p not in seen: seen.add(p); st.append(p)
while st:
    q=st.pop()
    if t(q): continue
    for p in fwd[q]:
        if p not in seen: seen.add(p); st.append(p)
dead=sorted(p for p in cand if p not in seen and not t(p))
json.dump(dead,open('v2/rescan/dead.json','w'),indent=1)
print(len(dead))
for p in dead:
    print(p,[q for q in r[p] if not t(q)][:5], sum(t(q) for q in r[p]))
