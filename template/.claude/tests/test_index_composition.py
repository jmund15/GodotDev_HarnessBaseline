"""Re-runnable proof for tools/index_composition.py.

    python3 .claude/tests/test_index_composition.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "index_composition.py")
MARKER = "no baseline set"


def plant_db(root, memory, other):
    os.makedirs(os.path.join(root, ".search-index"), exist_ok=True)
    con = sqlite3.connect(os.path.join(root, ".search-index", "search.db"))
    con.execute("CREATE TABLE chunks (file_path TEXT)")
    rows = [(".claude/auto-memory/m%d.md" % i,) for i in range(memory)]
    for bucket, count in other.items():
        rows += [("%s/f%d.cs" % (bucket, i),) for i in range(count)]
    con.executemany("INSERT INTO chunks VALUES (?)", rows)
    con.commit()
    con.close()


def baseline_path(root):
    return os.path.join(root, ".claude", "index_composition_baseline.json")


def write_baseline(root, text):
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    with open(baseline_path(root), "w", encoding="utf-8") as handle:
        handle.write(text)


def run(root, *args):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                          env=env, timeout=60, cwd=root)


def consumer(memory, other):
    root = tempfile.mkdtemp(prefix="indexcomp_")
    plant_db(root, memory, other)
    return root


def main():
    cases = []

    root = consumer(10, {"Scripts": 40, "Scenes": 50})
    out = run(root)
    cases.append(("no baseline file: exit 0 and the marker", out.returncode == 0 and MARKER in out.stdout))
    cases.append(("no baseline file: no comparison against a baseline",
                  "vs " not in out.stdout and "halved" not in out.stdout))

    root = consumer(5, {"Scripts": 95})
    out = run(root)
    cases.append(("no baseline file still prints the absolute top-bucket flag",
                  out.returncode == 0 and MARKER in out.stdout and "FLAG: Scripts is 95%" in out.stdout))

    root = consumer(2, {"Scripts": 49, "Scenes": 49})
    write_baseline(root, json.dumps({"chunks": 100, "memory_share": 5.7, "date": "2026-08-16"}))
    out = run(root)
    cases.append(("share under half the baseline's flags the displacement",
                  out.returncode == 0 and "memory share has more than halved" in out.stdout
                  and MARKER not in out.stdout))
    cases.append(("a baseline prints growth against its chunks and date",
                  "vs 2026-08-16 baseline" in out.stdout and "(+0%)" in out.stdout))

    root = consumer(5, {"Scripts": 47, "Scenes": 48})
    write_baseline(root, json.dumps({"chunks": 100, "memory_share": 5.7, "date": "2026-08-16"}))
    out = run(root)
    cases.append(("share over half the baseline's raises no drift flag",
                  out.returncode == 0 and "halved" not in out.stdout and MARKER not in out.stdout))

    root = consumer(6, {"Scripts": 44, "Scenes": 50})
    out = run(root, "--set-baseline")
    written = {}
    if os.path.isfile(baseline_path(root)):
        with open(baseline_path(root), encoding="utf-8") as handle:
            written = json.load(handle)
    cases.append(("--set-baseline writes exactly chunks, memory_share and date",
                  out.returncode == 0 and set(written) == {"chunks", "memory_share", "date"}
                  and written["chunks"] == 100 and written["memory_share"] == 6.0
                  and len(written["date"]) == 10))
    again = run(root)
    cases.append(("a rerun prints growth against the written baseline",
                  again.returncode == 0 and "vs %s baseline: 100 chunks (+0%%)" % written.get("date") in again.stdout
                  and MARKER not in again.stdout))

    root = tempfile.mkdtemp(prefix="indexcomp_")
    out = run(root, "--set-baseline")
    cases.append(("--set-baseline with no db exits 1 and writes no file",
                  out.returncode == 1 and not os.path.exists(baseline_path(root))))
    root = tempfile.mkdtemp(prefix="indexcomp_")
    os.makedirs(os.path.join(root, ".search-index"))
    with open(os.path.join(root, ".search-index", "search.db"), "wb") as handle:
        handle.write(b"not a database")
    out = run(root, "--set-baseline")
    cases.append(("--set-baseline over an unreadable db exits 1 and writes no file",
                  out.returncode == 1 and not os.path.exists(baseline_path(root))))

    for label, text in (("invalid JSON", "{not json"), ("a non-object", "[1, 2]"),
                        ("missing keys", json.dumps({"chunks": 100})),
                        ("wrong types", json.dumps({"chunks": "x", "memory_share": None, "date": 5})),
                        ("a zero chunk count", json.dumps({"chunks": 0, "memory_share": 5.7, "date": "2026-08-16"}))):
        root = consumer(10, {"Scripts": 90})
        write_baseline(root, text)
        out = run(root)
        cases.append(("a malformed baseline (%s) prints the marker, no traceback" % label,
                      out.returncode == 0 and MARKER in out.stdout and "Traceback" not in out.stderr))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
