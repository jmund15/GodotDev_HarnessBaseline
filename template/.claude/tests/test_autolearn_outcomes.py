"""Proof for tools/autolearn_outcomes.py: `record` validates the row shape and caps the file, `lookup`
returns the prior declines for a target.

    python3 .claude/tests/test_autolearn_outcomes.py
"""
import importlib.util
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "autolearn_outcomes.py")

ROW = ["--date", "2026-10-04", "--session", "s1", "--target", "rules/x.md#Top", "--signal", "sig",
       "--recommended", "apply", "--outcome", "declined", "--reason", "too costly"]


def run(file, *args):
    p = subprocess.run([sys.executable, TOOL, *args, "--file", file], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def rows(file):
    if not os.path.exists(file):
        return []
    with open(file, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def shared_writer_cases(tmp, cases):
    spec = importlib.util.spec_from_file_location("autolearn_outcomes", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_read = module.read_rows

    def slow_read(path):
        result = real_read(path)
        time.sleep(0.04)
        return result

    for cap in (20, 5):
        file = os.path.join(tmp, "concurrent_%d.jsonl" % cap)
        start = threading.Barrier(8)

        def record(i):
            start.wait(timeout=10)
            return module.main(["record", *ROW, "--row", str(i), "--file", file, "--cap", str(cap)])

        with patch.object(module, "read_rows", slow_read), ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(record, range(8)))
        got = rows(file)
        cases.append(("concurrent real record calls keep %d rows with cap %d" % (min(8, cap), cap),
                      results == [0] * 8 and len(got) == min(8, cap)
                      and len({r["row"] for r in got}) == min(8, cap)))

    file = os.path.join(tmp, "atomic.jsonl")
    run(file, "record", *ROW, "--row", "seed")
    observations = []
    real_open = open

    def observe():
        rc, out, err = run(file, "history", "--target", "rules/x.md")
        got = [json.loads(line) for line in out.splitlines()]
        observations.append(rc == 0 and not err and bool(got) and got[0]["row"] == "seed")

    class InterruptedWrite:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.fh.close()

        def write(self, text):
            half = len(text) // 2
            self.fh.write(text[:half])
            self.fh.flush()
            observe()
            return half + self.fh.write(text[half:])

    def interrupted_open(path, mode="r", **kwargs):
        fh = real_open(path, mode, **kwargs)
        return InterruptedWrite(fh) if mode == "w" else fh

    real_replace = os.replace

    def observed_replace(src, dst):
        observe()
        return real_replace(src, dst)

    with patch.object(module, "open", interrupted_open, create=True), patch.object(module.os, "replace", observed_replace):
        rc = module.main(["record", *ROW, "--row", "new", "--file", file])
    cases.append(("history sees complete rows during ledger publication",
                  rc == 0 and bool(observations) and all(observations) and len(rows(file)) == 2))

    before = real_open(file, "rb").read()
    with patch.object(module.os, "replace", side_effect=OSError("planted write failure")):
        rc = module.main(["record", *ROW, "--file", file])
    cases.append(("publication failure exits nonzero and keeps the old ledger and no temp file",
                  rc != 0 and real_open(file, "rb").read() == before
                  and sorted(os.listdir(tmp)) == sorted(["o.jsonl", "history.jsonl", "cap.jsonl",
                                                       "concurrent_20.jsonl", "concurrent_5.jsonl", "atomic.jsonl",
                                                       "removal.jsonl"])))
    with patch.object(module, "locked", side_effect=TimeoutError("planted lock timeout"), create=True):
        rc = module.main(["record", *ROW, "--file", file])
    cases.append(("lock timeout exits nonzero without an unlocked write",
                  rc != 0 and real_open(file, "rb").read() == before))


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="autolearn_outcomes_")
    extra = tempfile.mkdtemp(prefix="autolearn_metric_")
    try:
        f = os.path.join(tmp, "o.jsonl")
        rc, _, _ = run(f, "record", *ROW)
        cases.append(("record appends one validated row", rc == 0 and len(rows(f)) == 1
                      and rows(f)[0]["outcome"] == "declined" and rows(f)[0]["recommended"] == "apply"))
        metric = "friction.recursive-grep.sessions"
        metric_file = os.path.join(extra, "metric.jsonl")
        rc, _, _ = run(metric_file, "record", *ROW, "--metric", metric)
        cases.append(("record stores metric", rc == 0 and rows(metric_file)[0].get("metric") == metric))
        with open(metric_file, "rb") as fh:
            before = fh.read()
        rc, _, err = run(metric_file, "record", *ROW, "--metric", "bad/metric")
        with open(metric_file, "rb") as fh:
            unchanged = fh.read() == before
        cases.append(("bad metric token is rejected and file unchanged",
                      rc != 0 and "metric" in err and "bad/metric" in err and unchanged))
        plain_file = os.path.join(extra, "plain.jsonl")
        rc, _, _ = run(plain_file, "record", *ROW)
        cases.append(("record without metric keeps the prior row keys",
                      rc == 0 and "metric" not in rows(plain_file)[0]
                      and set(rows(plain_file)[0]) == set(rows(f)[0])))
        rc, _, err = run(f, "record", *[("approved" if a == "declined" else a) for a in ROW])
        cases.append(("an outcome outside applied|declined is rejected, file unchanged",
                      rc != 0 and "outcome" in err and len(rows(f)) == 1))
        rc, _, err = run(f, "record", *[a for a in ROW if a not in ("--reason", "too costly")])
        cases.append(("a missing reason is rejected", rc != 0 and len(rows(f)) == 1))
        rc, _, err = run(f, "record", *(ROW[:-1] + [" "]))
        cases.append(("a blank reason is rejected", rc != 0 and len(rows(f)) == 1))
        rc, _, _ = run(f, "record", *ROW, "--bogus", "x")
        cases.append(("an unknown flag is rejected", rc != 0 and len(rows(f)) == 1))
        rc, _, err = run(f, "record", *(["--date", "yesterday"] + ROW[2:]))
        cases.append(("a non-ISO date is rejected", rc != 0 and "date" in err and len(rows(f)) == 1))

        applied = [("applied" if a == "declined" else a) for a in ROW]
        applied[ROW.index("rules/x.md#Top")] = "rules/y.md#Other"
        run(f, "record", *applied)
        rc, out, _ = run(f, "lookup", "--target", "rules/x.md#Top")
        got = [json.loads(l) for l in out.splitlines() if l.strip()]
        cases.append(("lookup returns only the declines for the target",
                      rc == 0 and len(got) == 1 and got[0]["target"] == "rules/x.md#Top"
                      and got[0]["outcome"] == "declined"))
        rc, out, _ = run(f, "lookup", "--target", "rules/none.md")
        cases.append(("lookup of an unseen target prints nothing and exits 0", rc == 0 and out.strip() == ""))

        with open(f, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("not json\n")
        rc, out, err = run(f, "lookup", "--target", "t")
        cases.append(("a corrupt row is named on stderr, not silently skipped", "unreadable" in err))

        h = os.path.join(tmp, "history.jsonl")
        base = [a for a in ROW]
        rc, _, _ = run(h, "record", *base, "--fix", "text")
        cases.append(("record stores a fix type", rc == 0 and rows(h)[0].get("fix") == "text"))
        removal = os.path.join(tmp, "removal.jsonl")
        rc, _, _ = run(removal, "record", *base, "--fix", "remove")
        cases.append(("record stores a removal fix", rc == 0 and rows(removal)[0].get("fix") == "remove"))
        rc, _, err = run(h, "record", *base, "--fix", "louder")
        cases.append(("a fix type outside the ladder is rejected, file unchanged",
                      rc != 0 and "fix" in err and len(rows(h)) == 1))
        slip = [("recorded" if a == "declined" else a) for a in base]
        slip[slip.index("rules/x.md#Top")] = "rules/x.md#Other"
        rc, _, _ = run(h, "record", *slip, "--fix", "none")
        cases.append(("outcome recorded counts a slip with no landing", rc == 0 and rows(h)[-1]["outcome"] == "recorded"))
        elsewhere = list(base)
        elsewhere[elsewhere.index("rules/x.md#Top")] = "skills/z/SKILL.md"
        run(h, "record", *elsewhere, "--fix", "tool")
        unrelated = list(base)
        unrelated[unrelated.index("rules/x.md#Top")] = "rules/x.md.bak"
        run(h, "record", *unrelated)
        rc, out, _ = run(h, "history", "--target", "rules/x.md", "--target", "skills/z/SKILL.md")
        got = [json.loads(l) for l in out.splitlines() if l.strip()]
        cases.append(("history returns every outcome for each target path, any anchor, and nothing else",
                      rc == 0 and sorted(r["target"] for r in got)
                      == ["rules/x.md#Other", "rules/x.md#Top", "skills/z/SKILL.md"]
                      and {r["outcome"] for r in got} == {"declined", "recorded"}))
        for target in ("rules/a.md; rules/x.md", "rules/x.md §Preferences pricing"):
            loose = list(base)
            loose[loose.index("rules/x.md#Top")] = target
            run(h, "record", *loose)
        rc, out, _ = run(h, "history", "--target", "rules/x.md")
        got = [json.loads(l)["target"] for l in out.splitlines() if l.strip()]
        cases.append(("history matches a path inside a several-path or section-named target, as the ledger writes them",
                      rc == 0 and "rules/a.md; rules/x.md" in got and "rules/x.md §Preferences pricing" in got
                      and "rules/x.md.bak" not in got))
        rc, out, _ = run(h, "history", "--target", "rules/none.md")
        cases.append(("history of an unseen target prints nothing and exits 0", rc == 0 and out.strip() == ""))

        g = os.path.join(tmp, "cap.jsonl")
        for i in range(7):
            run(g, "record", "--date", "2026-10-0%d" % (i + 1), *ROW[2:], "--cap", "5")
        got = rows(g)
        cases.append(("record keeps only the newest --cap rows",
                      len(got) == 5 and got[0]["date"] == "2026-10-03" and got[-1]["date"] == "2026-10-07"))
        shared_writer_cases(tmp, cases)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(extra, ignore_errors=True)

    failures = [l for l, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
