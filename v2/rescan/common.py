"""Shared loaders for the S4 inventory scripts: file lists, lock rows and file text from git objects."""
import json
import os
import subprocess

WT = "C:/Users/jmund/Game_Dev/Godot_Projects/PushinPotions/.claude/worktrees/harness-v2-plan"
MM = "C:/Users/jmund/MaddMund/MaddManager"
SCR = WT + "/.claude/scratch/v2_s4"
TARGETS = {
    "pp": {"repo": WT, "ref": "6eb07afa740858e72ea4438010efbf870b3a7329", "branch": "origin/main", "name": "PushinPotions", "local": "pp-local"},
    "mm_main": {"repo": MM, "ref": "6295feed427393d3491ad1d035be62a2f1116cc5", "branch": "main", "name": "MaddManager", "local": "mm-local"},
    "mm_r24": {"repo": MM, "ref": "023f41222b071a6a131047a7ca447be6a932e725", "branch": "r24-int", "name": "MaddManager", "local": "mm-local"},
}
TEXT_EXT = (".py", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".md", ".json", ".jsonl", ".yml", ".yaml", ".txt", ".tmpl", ".cs")
MAX_BYTES = 600_000


def git(repo, *args, text=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace") if text else r.stdout


def file_list(key):
    t = TARGETS[key]
    out = git(t["repo"], "ls-tree", "-r", "--name-only", "-z", t["ref"], "--", ".claude")
    return sorted(p for p in out.split("\0") if p)


def lock(key):
    t = TARGETS[key]
    try:
        return json.loads(git(t["repo"], "show", t["ref"] + ":.claude/baseline.lock.json"))
    except Exception:
        return {"files": {}, "substitutions": {}}


def read_texts(key, paths):
    """Blob text for each path in one `cat-file --batch` pass; skips non-text and oversize."""
    t = TARGETS[key]
    want = [p for p in paths if p.lower().endswith(TEXT_EXT)]
    if not want:
        return {}
    inp = "".join(f"{t['ref']}:{p}\n" for p in want).encode("utf-8")
    r = subprocess.run(["git", "-C", t["repo"], "cat-file", "--batch"], input=inp, capture_output=True)
    buf = r.stdout
    pos = 0
    out = {}
    for p in want:
        nl = buf.index(b"\n", pos)
        head = buf[pos:nl].decode().split()
        pos = nl + 1
        if len(head) == 3 and head[1] == "blob":
            n = int(head[2])
            if n <= MAX_BYTES:
                out[p] = buf[pos:pos + n].decode("utf-8", "replace")
            pos += n + 1
    return out


def cache_path(key):
    return f"{SCR}/texts_{key}.json"


def texts(key):
    cp = cache_path(key)
    if os.path.exists(cp):
        return json.load(open(cp, encoding="utf-8"))
    d = read_texts(key, file_list(key))
    json.dump(d, open(cp, "w", encoding="utf-8"))
    return d
