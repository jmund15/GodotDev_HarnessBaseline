#!/usr/bin/env python3
"""Proof for tools/spend_ledger.py and for every reader of the spend ledger that goes through it.

The ledger is append-only and was never bounded (34.7 MB over 16k rows on 2026-11-10), and the Stop hook and the
session watcher each parsed it whole. The writer now renames a ledger past `ROTATE_BYTES` to
`<ledger>.seg-<UTC stamp>`; a segment's stamp is an upper bound on its rows' timestamps. A reader that needs a
window opens only the segments whose stamp reaches it; a reader that needs history reads them all.

Cases run on planted ledgers under this proof's own temp root. Exit 0 pass, 1 fail.
"""
import json
import os
import sys
import tempfile
import time
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "hooks"), str(ROOT / "scripts")]
BASE = Path(tempfile.mkdtemp(prefix="spend_ledger_proof_", dir=os.environ.get("TMP")))
os.environ["CLAUDE_CONFIG_DIR"] = str(BASE / "login")
(BASE / "login" / "projects").mkdir(parents=True)
FAILS = []


def require(ok, message):
    if not ok:
        FAILS.append(message)


def stamp(epoch):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(epoch))


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def write_rows(path, rows):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def fresh(name):
    path = BASE / name / "ledger.jsonl"
    path.parent.mkdir(parents=True)
    return path


def segment(path, epoch, rows):
    seg = Path("%s.seg-%s" % (path, stamp(epoch)))
    write_rows(seg, rows)
    return seg


def spend(session, label, epoch, **extra):
    return dict({"timestamp": iso(epoch), "parentSessionId": session, "label": label, "transport": "t",
                 "exitCode": 0, "costUSD": 1.0}, **extra)


NOW = time.time()
HOUR = 3600


def helper_cases():
    import spend_ledger as sl
    led = fresh("helper")
    write_rows(led, [spend("s", "a", NOW)])
    require(sl.rotate_if_large(str(led), limit=10 ** 6) is None and led.exists(), "a ledger under the limit stays")
    seg = sl.rotate_if_large(str(led), limit=10, now=NOW)
    require(seg == "%s.seg-%s" % (led, stamp(NOW)) and Path(seg).exists() and not led.exists(),
            "a ledger past the limit is renamed to <ledger>.seg-<UTC stamp> and the live path is left absent: %r" % seg)
    write_rows(led, [spend("s", "b", NOW)])
    require(sl.rotate_if_large(str(led), limit=10, now=NOW) is None and led.exists(),
            "a rotation into a segment name already taken leaves both files alone")
    with mock.patch.object(sl.os, "rename", side_effect=PermissionError("held open")):
        require(sl.rotate_if_large(str(led), limit=10, now=NOW + 5) is None and led.exists(),
                "a rename the OS refuses (a reader holds the file) is retried by a later write, never raised")
    (led.parent / "ledger.jsonl.anthropic-index.json").write_text("{}", encoding="utf-8")
    (led.parent / "ledger.jsonl.seg-notastamp").write_text("x", encoding="utf-8")
    require([os.path.basename(p) for p in sl.paths(str(led))]
            == ["ledger.jsonl.seg-%s" % stamp(NOW), "ledger.jsonl"],
            "paths lists segments oldest first then the live ledger, and names no sibling file that is not a segment: %r"
            % sl.paths(str(led)))

    led = fresh("window")
    old, mid, recent = NOW - 30 * HOUR, NOW - 10 * HOUR, NOW - HOUR
    for epoch in (old, mid, recent):
        segment(led, epoch, [spend("s", "x%d" % epoch, epoch)])
    write_rows(led, [spend("s", "live", NOW)])
    names = lambda since: [os.path.basename(p) for p in sl.paths(str(led), since=since)]
    require(len(names(None)) == 4, "no window reads every segment and the live ledger: %r" % names(None))
    require(names(NOW - 24 * HOUR) == ["ledger.jsonl.seg-%s" % stamp(mid), "ledger.jsonl.seg-%s" % stamp(recent),
                                       "ledger.jsonl"],
            "a 24 h window skips the segment rotated 30 h ago: %r" % names(NOW - 24 * HOUR))
    require(names(NOW - 5 * 60) == ["ledger.jsonl"], "a 5 min window reads the live ledger alone: %r" % names(NOW - 5 * 60))

    got = [json.loads(line)["label"] for line in sl.iter_lines(str(led), needle=json.dumps("s"))]
    require(got == ["x%d" % old, "x%d" % mid, "x%d" % recent, "live"], "iter_lines walks segments then live: %r" % got)
    require(list(sl.iter_lines(str(led), needle=json.dumps("nobody"))) == [], "the needle drops every other row's line")
    require(list(sl.iter_lines(str(BASE / "nowhere" / "x.jsonl"))) == [], "a missing ledger holds nothing")

    # A rotation between two reads of one pass must neither drop nor repeat a row.
    led = fresh("race")
    write_rows(led, [spend("s", "r%d" % i, NOW) for i in range(5)])
    reader = sl.iter_lines(str(led))
    first = json.loads(next(reader))["label"]
    sl.rotate_if_large(str(led), limit=10, now=NOW)
    write_rows(led, [spend("s", "after", NOW)])
    rest = [json.loads(line)["label"] for line in reader]
    seen = [first] + rest
    require(seen[:5] == ["r%d" % i for i in range(5)] and seen[5:] in ([], ["after"]),
            "a rotation mid-pass loses and repeats nothing (Windows refuses it while the file is open): %r" % seen)
    # The file the reader holds open, named again as a segment (a rename on a platform that allows one while it is open).
    led = fresh("same-file")
    write_rows(led, [spend("s", "once%d" % i, NOW) for i in range(3)])
    os.link(led, "%s.seg-%s" % (led, stamp(NOW)))
    got = [json.loads(line)["label"] for line in sl.iter_lines(str(led))]
    require(got == ["once0", "once1", "once2"], "a segment that is the file already open is read once: %r" % got)


def reader_cases():
    import dispatch_table as dt
    import watch_records as w
    import work_key
    import transcript_usage

    # Every reader of a window reads the segments that reach it and never the older ones.
    led = fresh("readers")
    os.environ["SIDECAR_LEDGER_PATH"] = str(led)
    sid = "sid-readers"
    old, recent = NOW - 30 * HOUR, NOW - 2 * HOUR
    segment(led, old, [spend(sid, "old-job", old, outcome="ended-before-result", exitCode=1)])
    segment(led, recent, [spend(sid, "seg-job", recent, outcome="ended-before-result", exitCode=1)])
    write_rows(led, [spend(sid, "live-job", NOW - 60)])
    opened = []
    real_open = open

    def spy(path, *args, **kwargs):
        opened.append(os.path.basename(str(path)))
        return real_open(path, *args, **kwargs)

    with mock.patch("builtins.open", spy):
        row = dt._ledger_row(sid, "seg-job", NOW - 3 * HOUR)
    require(row is not None and row["label"] == "seg-job", "the Stop hook finds a row that sits in a segment: %r" % row)
    require(not any(name.endswith("seg-" + stamp(old)) for name in opened),
            "the Stop hook opens no segment rotated before its window: %r" % opened)
    require(dt._ledger_row(sid, "old-job", None) is not None, "with no window the Stop hook still reaches the oldest segment")
    w._LEDGER_READS.clear()
    opened.clear()
    with mock.patch("builtins.open", spy):
        finish = w.finish_rows(sid)
    require(sorted(r["label"] for r in finish) == ["seg-job"] and not any(n.endswith("seg-" + stamp(old)) for n in opened),
            "the watcher reads the 24 h of finish rows across segments and live, not the older segment: %r %r" % (finish, opened))

    # History readers read every segment.
    key = "k" * 64
    segment(led, old, [{"timestamp": iso(old), "label": "paid", "workKey": key, "exitCode": 0, "costUSD": 2.0, "launchId": "L1"}])
    require(work_key.find_finished(key, str(led)) is not None, "the re-pay check sees finished work that sits in a segment")
    segment(led, old + 1, [{"timestamp": iso(old), "sessionId": "hist", "transport": "deepseek"}])
    require(transcript_usage.ledger_transports(str(led)).get("hist") == "deepseek",
            "a session's transport is read from a segment")
    import orchestration_metrics as om
    labels = [r.get("label") for r in om.collect_sidecar(str(led), include_unlabeled=True)]
    require("paid" in labels, "the metrics collector reads segment rows: %r" % labels)
    import console_credit_probe as probe
    cut = NOW - 100 * HOUR
    segment(led, NOW - 90 * HOUR, [spend("x", "credit", NOW - 90 * HOUR, transport="c", costUSD=3.0)])
    total, err = probe.ledger_spend(str(led), "c", cut)
    require(err is None and total == 3.0, "the credit probe sums a segment's rows after its anchor: %r %r" % (total, err))
    import console_credit_audit as audit
    day = time.strftime("%Y-%m-%d", time.gmtime(NOW - 90 * HOUR))
    totals, err = audit.ledger_by_day(str(led), "c", {day})
    require(err is None and totals == {day: 3.0}, "the audit sums a segment's rows for a day in its window: %r %r" % (totals, err))
    seg_old = segment(led, NOW - 200 * HOUR, [spend("x", "ancient", NOW - 200 * HOUR, transport="c", costUSD=9.0)])
    with mock.patch("builtins.open", spy):
        opened.clear()
        probe.ledger_spend(str(led), "c", cut)
    require(seg_old.name not in opened, "the credit probe opens no segment rotated before its anchor: %r" % opened)


def writer_case():
    """The launcher's own function, cut out of lib/sidecar_common.sh and run: it rotates a ledger past the limit
    (the default ledger when its argument is __default__) and leaves a small one."""
    import re
    import subprocess
    import spend_ledger as sl
    text = (ROOT / "scripts" / "lib" / "sidecar_common.sh").read_text(encoding="utf-8")
    found = re.search(r"^sc_rotate_ledger\(\) \{\n.*?^\}\n", text, re.S | re.M)
    require(found is not None, "lib/sidecar_common.sh defines sc_rotate_ledger")
    if not found:
        return
    led = fresh("writer")
    led.write_text("x" * (sl.ROTATE_BYTES + 10) + "\n", encoding="utf-8")
    small = fresh("writer-small")
    small.write_text("x\n", encoding="utf-8")
    script = "sc_to_native() { printf '%s' \"$1\"; }\nSC_ROOT=\"$1\"\nSC_LEDGER_DEFAULT=\"$2\"\n" + found.group(0) \
        + "sc_rotate_ledger __default__\nsc_rotate_ledger \"$3\"\nsc_rotate_ledger ''\n"
    import _msys  # System32's bash.exe is the WSL shim, which sees neither these paths nor python3
    done = subprocess.run([_msys.tool("bash"), "-c", script, "bash", str(ROOT).replace("\\", "/"), str(led).replace("\\", "/"),
                           str(small).replace("\\", "/")], capture_output=True, text=True, timeout=60)
    require(done.returncode == 0, "sc_rotate_ledger never fails the launch: %r" % done.stderr)
    require(not led.exists() and len(sl.segments(str(led))) == 1, "the launcher rotates the default ledger past the limit")
    require(small.exists() and sl.segments(str(small)) == [], "the launcher leaves a small ledger alone")


def main():
    for case in (helper_cases, reader_cases, writer_case):
        try:
            case()
        except Exception as exc:  # a missing module or a crash is a red, never a pass
            FAILS.append("%s crashed: %s: %s" % (case.__name__, type(exc).__name__, exc))
    if FAILS:
        print("\n".join("FAIL " + f for f in FAILS))
        sys.exit(1)
    print("PASS spend ledger: segments, windows, and every reader")


if __name__ == "__main__":
    main()
