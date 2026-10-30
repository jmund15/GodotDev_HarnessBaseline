#!/usr/bin/env python3
"""Proof of consumer invariants on the checkout it runs in (a project or a fresh `bootstrap.sh` target).

    python3 .claude/tests/test_fresh_consumer.py
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CLAUDE = ROOT / ".claude"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CLAUDE / "tools"))
import _lock_rows  # noqa: E402

FAILURES = []
SKIPS = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail) if detail else ""))


def skip(label, why):
    print("SKIP %s (%s)" % (label, why))
    SKIPS.append(label)


def run(args, env_root=None, cwd=None):
    env = dict(os.environ)
    if env_root is not None:
        env["CLAUDE_PROJECT_DIR"] = str(env_root)
    done = subprocess.run([sys.executable, *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=str(cwd or ROOT), env=env)
    return done.returncode, done.stdout + done.stderr


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8")


def case1():
    label = "1 hot_set_review carries no exception-form basis.error"
    try:
        import close_predicates
        out = close_predicates.evaluate(["hot_set_review"], close_predicates.Ctx(ROOT))
        error = str((out["hot_set_review"].get("basis") or {}).get("error") or "")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    check(label, not re.match(r"^\w+(Error|Exception): ", error), error)


def case2():
    label = "2 every declared mandates key is in the catalog"
    import review_ledger
    catalog = review_ledger.load_catalog(CLAUDE)
    declared = {}
    for path in sorted((CLAUDE / "commands").rglob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^mandates:\s*(.+?)\s*$", line)
            if match:
                for key in match.group(1).split(","):
                    declared.setdefault(key.strip(), path.name)
    missing = sorted(k for k in declared if k not in catalog)
    check(label, not missing, ", ".join("%s (%s)" % (k, declared[k]) for k in missing))
    unhashed = []
    for key in sorted(catalog):
        try:
            if review_ledger.rule_hash(key, CLAUDE, catalog) is None:
                unhashed.append(key)
        except Exception as exc:  # noqa: BLE001
            unhashed.append("%s (%s)" % (key, exc))
    check("2 every composed catalog key's rule_hash computes", not unhashed, ", ".join(unhashed))


def case3(rows):
    label = "3 no delivered row is untracked and ignored"
    delivered = [p for p, r in rows.items() if r.get("status") in ("tracked", "composed")]
    tracked = set(git("ls-files").stdout.split("\n"))
    candidates = [p for p in delivered if p not in tracked and (ROOT / p).exists()]
    ignored = []
    if candidates:
        done = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-z", "--stdin"],
                              input="\0".join(candidates).encode("utf-8"), capture_output=True)
        ignored = [p for p in done.stdout.decode("utf-8").split("\0") if p]
    check(label, not ignored, ", ".join(ignored[:10]))


def case4(lock):
    label = "4 every 100755 baseline entry is executable"
    if os.name != "posix":
        return skip(label, "not POSIX")
    clone = CLAUDE / ".cache" / "baseline-repo"
    sha = lock.get("synced_commit")
    if not sha or not clone.is_dir():
        return skip(label, "no pinned baseline clone")
    tree = subprocess.run(["git", "-C", str(clone), "ls-tree", "-r", sha], capture_output=True, text=True,
                          encoding="utf-8")
    if tree.returncode != 0:
        return skip(label, "pinned tree unreadable")
    bad = []
    for line in tree.stdout.splitlines():
        meta, _, rel = line.partition("\t")
        if meta.startswith("100755") and (ROOT / rel).is_file() and not os.access(ROOT / rel, os.X_OK):
            bad.append(rel)
    check(label, not bad, ", ".join(bad[:10]))


def case5(lock_path):
    label = "5 layer_closure --lock reports 0 findings"
    if not (CLAUDE / ".cache" / "baseline-repo").is_dir():
        return skip(label, "no pinned baseline clone")
    code, out = run([str(CLAUDE / "tools" / "layer_closure.py"), "--lock", str(lock_path), "--root", str(ROOT)])
    check(label, code == 0, out.strip()[-400:])


def case6():
    label = "6 index_composition without a baseline file prints no baseline set"
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / ".search-index"
        db.mkdir()
        con = sqlite3.connect(str(db / "search.db"))
        con.execute("CREATE TABLE chunks(file_path TEXT)")
        con.execute("INSERT INTO chunks VALUES ('.claude/a.md')")
        con.commit()
        con.close()
        code, out = run([str(CLAUDE / "tools" / "index_composition.py")], env_root=tmp)
    step4 = (CLAUDE / "commands" / "reindex_search.md").read_text(encoding="utf-8")
    marker_ok = ("no baseline set" in step4 and "index_composition.py\" --set-baseline" in step4
                 and "11.5k" not in step4)
    ok = code == 0 and "no baseline set" in out and not re.search(r"vs .*baseline:", out) and marker_ok
    check(label, ok, "exit %s, step4 names marker+command %s: %s" % (code, marker_ok, out.strip()[-300:]))


def case7():
    label = "7 --lookup with no snapshot exits 1"
    with tempfile.TemporaryDirectory() as tmp:
        code, out = run([str(CLAUDE / "tools" / "self_eval_archive_store.py"), "--archive",
                         str(Path(tmp) / "absent.json"), "--lookup", "no-such-session"], cwd=tmp)
    check(label, code == 1, "exit %s: %s" % (code, out.strip()[-300:]))


def case8():
    label = "8 shipped external_models.json holds no account fields (credit, credentialFile)"
    data = json.loads((CLAUDE / "reference" / "external_models.json").read_text(encoding="utf-8"))
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("credit", "credentialFile"):
                    found.append(path + "/" + key)
                walk(value, path + "/" + key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, "%s/%d" % (path, i))

    walk(data, "")
    check(label, not found, "%d: %s" % (len(found), ", ".join(found[:6])))


def case9():
    label = "9 adaptation index_prebuild lists the family-manifest command only where the godot layer is adopted"
    import adaptation
    adaptation._CACHE.clear()
    skills = CLAUDE / "skills" / "project_subsystems"
    prebuild = adaptation.get(CLAUDE, "index_prebuild")
    command = "python3 .claude/hooks/generate_family_manifest.py"
    has_godot = (skills / "adaptation.godot.defaults.json").is_file()
    ok = (prebuild == [command]) if has_godot else (prebuild == [])
    with tempfile.TemporaryDirectory() as tmp:
        bare = Path(tmp) / ".claude" / "skills" / "project_subsystems"
        bare.mkdir(parents=True)
        for layer in ("pure", "coding"):
            source = skills / ("adaptation.%s.defaults.json" % layer)
            if source.is_file():
                (bare / source.name).write_bytes(source.read_bytes())
        adaptation._CACHE.clear()
        without_godot = adaptation.get(Path(tmp) / ".claude", "index_prebuild")
    check(label, ok and without_godot == [], "%s / %s" % (prebuild, without_godot))


def main():
    case1()
    case2()
    rows = _lock_rows.rows()
    lock_path = CLAUDE / "baseline.lock.json"
    if rows is None:
        for n in (3, 4, 5):
            skip("%d needs the lock" % n, "no baseline.lock.json")
    else:
        case3(rows)
        case4(json.loads(lock_path.read_text(encoding="utf-8")))
        case5(lock_path)
    case6()
    case7()
    case8()
    case9()
    print("%d failure(s), %d skip(s)" % (len(FAILURES), len(SKIPS)))
    for failure in FAILURES:
        print("  - " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
