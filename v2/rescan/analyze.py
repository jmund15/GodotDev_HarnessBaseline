"""S4 analyses on top of classes.json: importers of deleted files, lock substitutions, MaddManager layer rows,
Workflow `args` keys, proof-seconds per class, per-slice file counts. Writes analysis.json."""
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "C:/Users/jmund/Game_Dev/Godot_Projects/PushinPotions/.claude/worktrees/harness-v2-plan/.claude/scratch/v2_s4")
import common

CLS = json.load(open(common.SCR + "/classes.json"))
D, BM = "delete", "bench-moved"
GENERIC = {"README.md", "SKILL.md", "common.md", "manifest.json", "package.json", "index.md", "MEMORY.md"}
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".sh", ".ps1")
SPAWN = re.compile(r"(python3?|bash|node|pwsh|powershell|\bsh\b)\s|subprocess|Popen|spawn|\bexec|import |require\(|\bsource\b|execFile|check_output|\.run\(")
WORD = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|js|mjs|cjs|sh|ps1|md|json|jsonl|yml)")


def rel(p):
    return p[len(".claude/"):]


def slice_for(path, cls):
    r = rel(path)
    if r.startswith(("auto-memory/", "plans/")):
        return "S10"
    if r.startswith("tests/"):
        return "with its subject"
    if r.startswith("hooks/") and re.search(r"(dangerous_shell_guard|git_guardrails|_peer_loss|kill_guard|_regenerable_clone|_git_run|_git_commit|_shell_lex|pre_bash|_floor)", r):
        return "S5"
    if r.startswith(("hooks/", "tools/", "workflows/", "scripts/lib/")) and cls == "shared":
        return "S6b"
    if r.startswith(("commands/", "skills/", "rules/", "reference/", "guards/", "agents/", "output-styles/")) and cls == "shared":
        return "S6a"
    if cls != "shared":
        return "S8 (MaddManager) / S9 (PushinPotions)"
    return "S8 (MaddManager) / S9 (PushinPotions)"


def importers(key):
    t = common.TARGETS[key]
    files = common.file_list(key)
    res = CLS[key]
    txt = common.texts(key)
    deleted = {p for p in files if res[p]["cls"] in (D, BM)}
    base_count = Counter(p.rsplit("/", 1)[-1] for p in files)
    by_rel = {rel(p): p for p in deleted}
    by_base = {}
    for p in deleted:
        b = p.rsplit("/", 1)[-1]
        if base_count[b] == 1 and b not in GENERIC and len(b) >= 8 and b.endswith((".py", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".md", ".json")):
            by_base[b] = p
    stems = defaultdict(list)
    for p in files:
        r = rel(p)
        if r.endswith((".py", ".js", ".mjs", ".cjs")) and not r.startswith(("tests/", "plans/", "auto-memory/")):
            stems[r.rsplit("/", 1)[-1].rsplit(".", 1)[0]].append(p)
    del_stem = {s: ps[0] for s, ps in stems.items() if len(ps) == 1 and ps[0] in deleted}
    ambiguous = sorted(s for s, ps in stems.items() if len(ps) > 1 and any(x in deleted for x in ps))
    hits = []
    for p in files:
        r = rel(p)
        v = res[p]
        if v["cls"] in (D, BM):
            continue
        body = txt.get(p)
        if body is None:
            continue
        seen = set()

        def add(line, target, kind):
            k = (line, target, kind)
            if k not in seen and target != p:
                seen.add(k)
                hits.append({"kept": p, "line": line, "deleted": target, "kind": kind})

        lines = body.split("\n")
        if r.endswith(".py"):
            try:
                tree = ast.parse(body)
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [(a.name.split(".")[0], node.lineno) for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            names = [(node.module.split(".")[-1] if node.level else node.module.split(".")[0], node.lineno)]
                            if node.module.split(".")[0] in ("tools", "hooks", "scripts", "lib", "lib.sidecar"):
                                names += [(a.name, node.lineno) for a in node.names]
                        else:
                            names = [(a.name, node.lineno) for a in node.names]
                    for n, ln in names:
                        if n in del_stem:
                            add(ln, del_stem[n], "import")
            except SyntaxError:
                pass
        if r.endswith((".js", ".mjs", ".cjs")):
            for i, ln in enumerate(lines, 1):
                for m in re.finditer(r"(?:require\(\s*|from\s+|import\(\s*)['\"]([^'\"]+)['\"]", ln):
                    st = m.group(1).rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    if st in del_stem:
                        add(i, del_stem[st], "import")
        for i, ln in enumerate(lines, 1):
            if ".py" not in ln and ".js" not in ln and ".md" not in ln and ".sh" not in ln and ".ps1" not in ln and ".json" not in ln and ".mjs" not in ln and ".cjs" not in ln and ".yml" not in ln:
                continue
            for w in WORD.findall(ln):
                w = w.replace("\\", "/").lstrip("./")
                if w.startswith(".claude/"):
                    w = w[len(".claude/"):]
                tgt = by_rel.get(w)
                if not tgt:
                    parts = w.split("/")
                    for j in range(1, len(parts)):
                        tgt = by_rel.get("/".join(parts[j:]))
                        if tgt:
                            break
                if not tgt and "/" not in w.strip("/"):
                    tgt = by_base.get(w)
                if not tgt:
                    tgt = by_base.get(w.rsplit("/", 1)[-1]) if w.rsplit("/", 1)[-1] in by_base else None
                if not tgt:
                    continue
                if r.endswith(CODE_EXT):
                    kind = "spawn" if SPAWN.search(ln) else "path-string"
                elif r.endswith(".json") and r in ("settings.json",) :
                    kind = "wired"
                elif r.endswith(".json"):
                    kind = "spawn" if '"command"' in ln else "path-string"
                elif r.endswith(".md"):
                    kind = "citation"
                else:
                    kind = "path-string"
                add(i, tgt, kind)
    return deleted, hits, ambiguous


def cut_slice(kept_path, kept_cls):
    r = rel(kept_path)
    if r.startswith(("auto-memory/", "plans/")):
        return "S10 (archive sweep)"
    if r in ("settings.json", "adaptation.json") or r.endswith("project_subsystems/adaptation.json") or r.startswith(("githooks/", "templates/workflows/")) or r in ("scripts/main_check.py",):
        return "S8/S9 (cutover commit)"
    if r.startswith("scripts/") and r.endswith(".ps1"):
        return "S9 (PushinPotions cutover)"
    if r.startswith(("tools/ui_", "tools/round_close")) or "ui_lock" in r or r.startswith("hooks/_wide_runs"):
        return "S8 (MaddManager's UI edits, K8)"
    if r.startswith("tests/"):
        return "with its subject's slice"
    if re.match(r"hooks/(dangerous_shell_guard|git_guardrails|_peer_loss|kill_guard|_regenerable_clone|_git_run|_git_commit|sidecar_workdir_guard|agy_hook_bridge)", r):
        return "S5/S6b (floor and its kept callers)"
    if r.startswith(("hooks/", "tools/", "workflows/", "scripts/")):
        return "S6b"
    if r.startswith(("commands/", "skills/", "rules/", "reference/", "guards/", "agents/", "output-styles/")):
        return "S6a (doctrine rewrite)"
    return "S10 (repoint sweep)"


def substitutions():
    out = {}
    for key in ("pp", "mm_main", "mm_r24"):
        lk = common.lock(key)
        files = common.file_list(key)
        txt = common.texts(key)
        res = CLS[key]
        rows = []
        for tok, val in lk["substitutions"].items():
            if not val:
                rows.append({"token": tok, "value": val, "by_class": {}, "home": "dropped: the value is empty, so nothing is substituted", "examples": []})
                continue
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(val) + r"(?![A-Za-z0-9_])") if re.match(r"^[A-Za-z0-9_ ]+$", val) else re.compile(re.escape(val))
            cnt = Counter()
            ex = defaultdict(list)
            for p, body in txt.items():
                if rel(p).startswith(("auto-memory/", "plans/")):
                    continue
                if pat.search(body):
                    c = res[p]["cls"]
                    cnt[c] += 1
                    if len(ex[c]) < 4:
                        ex[c].append(rel(p))
            sh = cnt.get("shared", 0)
            if tok.startswith("{{"):
                home = "`adaptation.json` key (a placeholder every shared file that carries the value reads at run time)" if sh else "project-local files only: no shared file carries the value"
            elif sh:
                home = f"`adaptation.json` key or the {sh} shared file(s) become project-local; v2 forbids a substitution"
            else:
                home = "project-local file(s) only: no kept shared file carries the value, so no home is needed"
            rows.append({"token": tok, "value": val, "by_class": dict(cnt), "home": home, "examples": ex.get("shared", [])})
        out[key] = rows
    return out


def layer_rows():
    out = {}
    for key in ("mm_main", "mm_r24"):
        lk = common.lock(key)["files"]
        res = CLS[key]
        out[key] = [
            {"path": p, "layer": v.get("layer"), "status": v.get("status"), "cls": res.get(p, {}).get("cls", "not in tree"), "row": res.get(p, {}).get("row", ""), "conf": res.get(p, {}).get("conf", "")}
            for p, v in sorted(lk.items()) if v.get("layer") in ("coding", "godot")
        ]
    return out


def args_keys(key="pp"):
    t = common.TARGETS[key]
    res = {}
    for eng in ("dispatch", "explore_fanout", "dispatch_chains"):
        p = f".claude/workflows/{eng}.js"
        try:
            body = common.git(t["repo"], "show", f"{t['ref']}:{p}")
        except Exception:
            continue
        ks = defaultdict(list)
        for i, ln in enumerate(body.split("\n"), 1):
            for m in re.finditer(r"\bargs\??\.([A-Za-z_][A-Za-z0-9_]*)", ln):
                ks[m.group(1)].append(i)
            for m in re.finditer(r"\bargs\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]", ln):
                ks[m.group(1)].append(i)
            m = re.search(r"(?:const|let|var)\s*\{([^}]+)\}\s*=\s*(?:args|input\.args|ctx\.args)\b", ln)
            if m:
                for part in m.group(1).split(","):
                    nm = part.split(":")[0].split("=")[0].strip()
                    if nm:
                        ks[nm].append(i)
        res[eng] = {k: v[:4] for k, v in sorted(ks.items())}
    return res


def proof_seconds():
    out = {}
    for key, repo in (("pp", common.WT), ("mm_main", common.MM)):
        try:
            g = common.git(repo, "rev-parse", "--git-common-dir").strip()
            if not os.path.isabs(g):
                g = os.path.join(repo, g)
            d = {k: v for k, v in json.load(open(g + "/harness/proof_durations.json")).items() if isinstance(v, (int, float))}
        except Exception as e:
            out[key] = {"error": str(e)}
            continue
        res = CLS[key]
        sec = Counter()
        n = Counter()
        unmatched = 0.0
        un_n = 0
        for name, s in d.items():
            p = ".claude/tests/" + name
            if p in res:
                sec[res[p]["cls"]] += s
                n[res[p]["cls"]] += 1
            else:
                unmatched += s
                un_n += 1
        out[key] = {"seconds": {k: round(v, 1) for k, v in sec.items()}, "proofs": dict(n), "unmatched_seconds": round(unmatched, 1), "unmatched_proofs": un_n, "total": round(sum(d.values()), 1), "recorded": len(d)}
        # by conf for shared (the T6 conversion population)
        byc = Counter()
        for name, s in d.items():
            p = ".claude/tests/" + name
            if p in res and res[p]["cls"] == "shared":
                byc["shared " + res[p]["conf"]] += s
        out[key]["shared_by_conf"] = {k: round(v, 1) for k, v in byc.items()}
    return out


def slice_counts():
    out = {}
    for key in ("pp", "mm_main", "mm_r24"):
        c = Counter()
        for p, v in CLS[key].items():
            c[(slice_for(p, v["cls"]), v["cls"])] += 1
        out[key] = {f"{a}|{b}": n for (a, b), n in sorted(c.items())}
    return out


def main():
    out = {"importers": {}, "substitutions": substitutions(), "layers": layer_rows(), "args": args_keys(), "proofs": proof_seconds(), "slices": slice_counts()}
    for key in ("pp", "mm_main", "mm_r24"):
        deleted, hits, amb = importers(key)
        for h in hits:
            h["cut"] = cut_slice(h["kept"], CLS[key][h["kept"]]["cls"])
        out["importers"][key] = {"deleted": len(deleted), "hits": hits, "ambiguous_stems": amb}
        print(key, "deleted", len(deleted), "hits", len(hits), Counter(h["kind"] for h in hits))
    json.dump(out, open(common.SCR + "/analysis.json", "w"))


if __name__ == "__main__":
    main()
