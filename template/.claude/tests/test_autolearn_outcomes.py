"""Proof for tools/autolearn_outcomes.py: `record` validates the row shape and caps the file, `lookup`
returns the prior declines for a target.

    python3 .claude/tests/test_autolearn_outcomes.py
"""
import contextlib
import importlib.util
import io
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
    """One tool call in this process, through its own entry point: exit code, stdout, stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = TOOL_MODULE.main([*args, "--file", file])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def spawn(file, *args):
    p = subprocess.run([sys.executable, TOOL, *args, "--file", file], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def rows(file):
    if not os.path.exists(file):
        return []
    with open(file, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def load_tool():
    spec = importlib.util.spec_from_file_location("autolearn_outcomes", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL_MODULE = load_tool()


def record_in_process(module, file, date, target, signal, outcome):
    """One real `record` call in this process: its exit code and what it printed."""
    argv = ["record", "--file", file, "--date", date, "--session", "s1", "--target", target, "--signal", signal,
            "--recommended", "apply", "--reason", "x", "--outcome", outcome]
    if outcome == "applied":
        argv += ["--fix", "hook"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = module.main(argv)
    return rc, out.getvalue()


def shared_writer_cases(tmp, cases):
    module = load_tool()
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
                                                       "removal.jsonl", "repeat.jsonl", "best.jsonl"])))
    with patch.object(module, "locked", side_effect=TimeoutError("planted lock timeout"), create=True):
        rc = module.main(["record", *ROW, "--file", file])
    cases.append(("lock timeout exits nonzero without an unlocked write",
                  rc != 0 and real_open(file, "rb").read() == before))


def ledger_id_cases(tmp, cases):
    """A row's id is a digest of the row, so it survives rotation and reordering, and a row differing in any field
    has another id. Every listing prints it and `show` resolves it."""
    module = load_tool()
    base = {"date": "2026-10-04", "session": "s1", "target": "rules/x.md#Top", "signal": "sig",
            "recommended": "apply", "outcome": "declined", "reason": "too costly", "fix": "text",
            "metric": "friction.a.b", "row": "R1"}
    ids = {module.row_id(base)}
    varied = {"date": "2026-10-05", "session": "s2", "target": "rules/y.md", "signal": "other", "recommended": "choose",
              "outcome": "applied", "reason": "cheap", "fix": "tool", "metric": "friction.c.d", "row": "R2"}
    distinct = all(module.row_id(dict(base, **{k: v})) not in ids and not ids.add(module.row_id(dict(base, **{k: v})))
                   for k, v in varied.items())
    cases.append(("rows differing in any one field have different ids", distinct))
    cases.append(("a row without the optional fields has an id too, and the field order is not part of it",
                  module.row_id({k: base[k] for k in ("date", "session")})
                  == module.row_id({k: base[k] for k in ("session", "date")})))

    f = os.path.join(tmp, "ids.jsonl")
    sig = lambda n: ["--date", "2026-10-%02d" % n, "--session", "s", "--target", "t%d.md" % n, "--signal", "g%d" % n,
                     "--recommended", "apply", "--outcome", "declined", "--reason", "x", "--cap", "5"]
    for n in (1, 2, 3, 4):
        run(f, "record", *sig(n))
    _, out, _ = run(f, "lookup", "--target", "t3.md")
    before = json.loads(out.splitlines()[0])
    line_before = [r["target"] for r in rows(f)].index("t3.md") + 1
    for n in (5, 6, 7):
        run(f, "record", *sig(n))
    line_after = [r["target"] for r in rows(f)].index("t3.md") + 1
    _, out, _ = run(f, "lookup", "--target", "t3.md")
    after = json.loads(out.splitlines()[0])
    cases.append(("a listing prints the row's id", bool(before.get("id"))))
    cases.append(("an id printed before a rotation names the same row after it",
                  line_before != line_after and before["id"] == after["id"]))
    rc, out, _ = run(f, "show", before["id"])
    cases.append(("show resolves an id back to its row",
                  rc == 0 and json.loads(out.splitlines()[0])["target"] == "t3.md"))
    rc, _, err = run(f, "show", "00000000")
    cases.append(("show of an id no row has exits 1 and says so", rc == 1 and "00000000" in err))
    rc, out, _ = run(f, "record", *sig(8))
    cases.append(("record prints the new row's id", rc == 0 and module.row_id(rows(f)[-1]) in out))
    _, out, _ = run(f, "history", "--target", "t8.md")
    cases.append(("history lists the id", json.loads(out.splitlines()[0])["id"] == module.row_id(rows(f)[-1])))

    u = os.path.join(tmp, "unswept_ids.jsonl")
    for n in (1, 2):
        run(u, "record", *sig(n))
    for n in (3,):
        run(u, "record", *[("recorded" if a == "declined" else a) for a in sig(n)])
    rc, out, _ = run(u, "unswept", "--rows")
    listed = [json.loads(l) for l in out.splitlines() if l.strip()]
    cases.append(("unswept --rows lists each unread recorded row with its id",
                  rc == 0 and [r["target"] for r in listed] == ["t3.md"] and listed[0]["id"] == module.row_id(rows(u)[2])))

    r = os.path.join(tmp, "repeat_ids.jsonl")
    record_in_process(module, r, "2026-10-18", "hooks/a.py", "idled beside a gate", "recorded")
    _, out = record_in_process(module, r, "2026-11-10", "hooks/a.py", "idled again", "recorded")
    cases.append(("a repeat line names the earlier row by its id", module.row_id(rows(r)[0]) in out))


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="autolearn_outcomes_")
    extra = tempfile.mkdtemp(prefix="autolearn_metric_")
    try:
        cli = os.path.join(extra, "cli.jsonl")
        rc, out, err = spawn(cli, "record", *ROW)
        cases.append(("the CLI runs from its command line, records the row and exits 0",
                      rc == 0 and not err and len(rows(cli)) == 1 and TOOL_MODULE.row_id(rows(cli)[0]) in out))
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
        cases.append(("a rejected row states the shape it accepts and one valid call",
                      "accepts:" in err and "example:" in err))
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

        # The record call lists the earlier rows nearest the new slip: the recorded or applied rows sharing the most
        # signal words or paths, of any date, target or outcome. The agent judging a repeat knows the choice by its
        # words, never by the file a fix happened to land in.
        r = os.path.join(tmp, "repeat.jsonl")
        tool = load_tool()
        slip = lambda date, target, signal, outcome="recorded": record_in_process(tool, r, date, target, signal, outcome)
        _, out = slip("2026-10-18", "hooks/a.py", "idled beside a gate")
        cases.append(("a first recorded row names no repeat", "repeat" not in out))
        _, out = slip("2026-10-28", "hooks/b.py", "other")
        cases.append(("a recorded row sharing no word or path names no repeat", "repeat" not in out))
        _, out = slip("2026-10-29", "hooks/a.py;rules/z.md#Top", "something unrelated entirely")
        cases.append(("a shared path alone names no repeat: a path is not a choice", "repeat" not in out))
        rc, out = slip("2026-11-10", "hooks/a.py;rules/z.md#Top", "idled again")
        cases.append(("a second slip on the path with a shared word names the earlier row",
                      rc == 0 and "repeat" in out and "2026-10-18" in out and "idled beside a gate" in out
                      and "2026-10-28" not in out))
        slip("2026-09-01", "hooks/old.py", "edited a hook without loading instruction_quality first")
        _, out = slip("2026-11-25", "rules/other.md", "began harness edits without loading instruction_quality first")
        cases.append(("the same choice under another target and months earlier is named",
                      "2026-09-01" in out and "edited a hook" in out))
        slip("2026-10-20", "tools/x.py", "armed the watcher in a foreground call", "applied")
        _, out = slip("2026-11-26", "docs/y.md", "armed a watcher in the foreground call")
        cases.append(("an earlier applied row alone is named: a fix that landed and was slipped again",
                      "2026-10-20" in out and "applied" in out))
        _, out = slip("2026-11-27", "docs/y.md", "armed a watcher in the foreground call", "declined")
        cases.append(("a declined row prints no repeat", "repeat" not in out))
        _, out = slip("2026-11-28", "docs/z.md", "armed a watcher in a foreground call again")
        cases.append(("an earlier declined row of the same choice is named: the owner already ruled on its fix",
                      "2026-11-27" in out and "declined" in out))
        best = os.path.join(tmp, "best.jsonl")
        record_in_process(tool, best, "2026-01-01", "p/best.md", "alpha beta zeta omega", "recorded")
        for i in range(10):
            record_in_process(tool, best, "2026-02-%02d" % (i + 1), "p/f%d.md" % i, "alpha beta filler%d" % i, "recorded")
        _, out = record_in_process(tool, best, "2026-03-01", "p/new.md", "alpha beta zeta omega", "recorded")
        named = [l for l in out.splitlines() if l.startswith("  ")]
        cases.append(("the nearest rows are kept before the bound, so the best match is named whatever its age",
                      0 < len(named) < 11 and "2026-01-01" in out))
        # A target's path is the first word of each `;` part, the `.claude/` prefix optional: the label words after
        # it name no path, and a bare `CLAUDE.core.md` and `.claude/CLAUDE.core.md` are one file.
        slip("2026-11-14", "hooks/c.py deny text", "c slip")
        _, out = slip("2026-11-15", "hooks/d.py deny text", "d slip")
        cases.append(("rows sharing only label words after the path name no repeat", "repeat" not in out))
        slip("2026-11-14", "CLAUDE.core.md", "core slip idle")
        _, out = slip("2026-11-15", ".claude/CLAUDE.core.md#Git", "core again idle")
        cases.append(("a bare and a .claude/-prefixed path are one shared path", "core slip" in out))
        _, out, _ = run(r, "history", "--target", "CLAUDE.core.md")
        cases.append(("history matches the prefixed spelling of a bare target", "core again" in out))

        s = os.path.join(extra, "sweep.jsonl")
        ex = lambda *a: run(s, *a)
        cases.append(("unswept on an absent ledger prints nothing", ex("unswept")[:2] == (0, "")))
        for d, o in (("2026-11-01", "recorded"), ("2026-11-05", "recorded"), ("2026-11-06", "applied")):
            ex("record", "--date", d, "--session", "s", "--target", "t%s.md" % d, "--signal", "g",
               "--recommended", "r", "--outcome", o, "--reason", "x")
        _, out, _ = ex("unswept")
        cases.append(("unswept prints count and oldest date of unread recorded rows only",
                      out.strip() == "autolearn: 2 correction row(s) no sweep has read, oldest 2026-11-01"))
        cases.append(("swept writes the newest recorded date beside the ledger",
                      ex("swept")[0] == 0 and open(s + ".swept", encoding="utf-8").read().strip() == "2026-11-05"))
        cases.append(("unswept prints nothing once every row is swept", ex("unswept")[:2] == (0, "")))
        ex("record", "--date", "2026-11-09", "--session", "s", "--target", "n.md", "--signal", "g",
           "--recommended", "r", "--outcome", "recorded", "--reason", "x")
        _, out, _ = ex("unswept")
        cases.append(("a later recorded row is unswept again", "1 correction row(s)" in out and "oldest 2026-11-09" in out))
        cases.append(("swept --through bounds the marker to what the sweep read",
                      ex("swept", "--through", "2026-11-07")[0] == 0 and ex("unswept")[1].startswith("autolearn: 1")))

        h = os.path.join(extra, "handed.jsonl")
        hx = lambda *a: run(h, *a)
        rec = lambda d, sess, tgt, o, *more: hx("record", "--date", d, "--session", sess, "--target", tgt, "--signal",
                                               "g", "--recommended", "r", "--outcome", o, "--reason", "x", *more)
        targets = lambda out: [json.loads(l)["target"] for l in out.splitlines() if l.strip()]
        rec("2026-11-10", "A", "handed.md", "recorded")
        rec("2026-11-10", "B", "other.md", "recorded")
        cases.append(("unswept --rows --session lists only that session's unread rows",
                      targets(hx("unswept", "--rows", "--session", "A")[1]) == ["handed.md"]))
        rec("2026-11-10", "A", "codify.md", "applied")
        cases.append(("a same-session /codify row, which carries no packet row label, leaves a handed row unswept",
                      "handed.md" in targets(hx("unswept", "--rows")[1])))
        rec("2026-11-10", "A", "packet.md", "declined", "--row", "R1")
        rec("2026-11-11", "A", "later.md", "recorded")
        cases.append(("a packet row marks its session's earlier recorded rows read, never another session's or a later one",
                      targets(hx("unswept", "--rows")[1]) == ["other.md", "later.md"]))
        cases.append(("the session-start count leaves out a row its session's packet answered",
                      hx("unswept")[1].startswith("autolearn: 2 ")))

        g = os.path.join(tmp, "cap.jsonl")
        for i in range(7):
            run(g, "record", "--date", "2026-10-0%d" % (i + 1), *ROW[2:], "--cap", "5")
        got = rows(g)
        cases.append(("record keeps only the newest --cap rows",
                      len(got) == 5 and got[0]["date"] == "2026-10-03" and got[-1]["date"] == "2026-10-07"))
        ledger_id_cases(extra, cases)
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
