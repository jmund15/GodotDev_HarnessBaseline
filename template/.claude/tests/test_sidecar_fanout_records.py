#!/usr/bin/env python3
"""RED-first proof: `tools/sidecar_fanout.py` must not leave two full copies of one stream-json
run on disk. `-P <label>.stream` and the captured `<label>.out.json` were byte-identical for every
stream-json job (observed 2026-09-13: three pairs at 3.85 MB / 3.29 MB / 1.63 MB under
`.claude/scratch/harness-ad6ec4-finish-records/`).

`.claude/scripts/lib/sidecar_common.sh` shows which file the archival readers actually need:
`sc_resume_loop`'s classifier (`sc_run_watched` -> `$OUTPUT` -> `sidecar_resume_check.py` on stdin)
and `sc_write_record`'s `-R` record are BOTH built from the launcher's own final stdout, never
re-read from the `-P` path on disk. That same stdout is exactly what Python's `subprocess.call`
redirect captures into `<label>.out.json` (`tools/ladder_ingest.py`'s documented deliverable). The
`-P` stream's only job is a LIVE one -- the stall watchdog's mtime check and a human tailing a
running job (`reference/sidecar_dispatch.md` "-P <file> live progress stream (then send stdout to
/dev/null)"). Once the child exits, a second full copy earns nothing.

A fake launcher plays the same -P/stdout tee a real one does: it writes matching content to BOTH
the -P path and its own stdout, so `sf.run_jobs` exercises the real post-exit code path.

Run: python3 .claude/tests/test_sidecar_fanout_records.py
"""
import importlib.util
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "sidecar_fanout.py")
spec = importlib.util.spec_from_file_location("sf_records", MOD)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_rec_")

STREAM_BODY = "\n".join(
    ['{"type":"system","subtype":"init"}']
    + ['{"type":"assistant","message":{"content":[{"type":"text","text":"line %d"}]}}' % i
       for i in range(200)]
    + ['{"type":"result","subtype":"success","result":"final deliverable text","num_turns":3}']
) + "\n"


def _read(path):
    return open(path, encoding="utf-8").read() if os.path.isfile(path) else None


_FAKE_LAUNCHER = os.path.join(TMP, "fake_launcher.py")
with open(_FAKE_LAUNCHER, "w", encoding="utf-8") as _fh:
    _fh.write(
        "import sys\n"
        "body = open(sys.argv[1], encoding='utf-8').read()\n"
        "if '-P' in sys.argv:\n"
        "    open(sys.argv[sys.argv.index('-P') + 1], 'w', encoding='utf-8').write(body)\n"
        "sys.stdout.write(body)\n"
        "sys.exit(int(sys.argv[2]))\n"
    )
_BODY_FILE = os.path.join(TMP, "body.jsonl")
open(_BODY_FILE, "w", encoding="utf-8").write(STREAM_BODY)


def _fake_launcher_argv(progress_path, exit_code=0, with_progress=True):
    """Mimics a real sidecar launcher's tee: writes STREAM_BODY to -P AND to its own stdout.

    Argv[:6] is what `run_jobs` prints per dispatch -- routed through a body FILE, never inline
    code, so a printed argv stays short instead of echoing the whole payload.
    """
    argv = [sys.executable, _FAKE_LAUNCHER, _BODY_FILE, str(exit_code)]
    if with_progress:
        argv += ["-P", progress_path]
    return argv


def _run_one(label, exit_code=0, with_progress=True):
    out_dir = os.path.join(TMP, label)
    os.makedirs(out_dir, exist_ok=True)
    stream_path = os.path.join(out_dir, "%s.stream" % label)
    out_path = os.path.join(out_dir, "%s.out.json" % label)
    argv = _fake_launcher_argv(stream_path, exit_code=exit_code, with_progress=with_progress)
    results = sf.run_jobs([(label, argv, out_path)], 1, out_dir)
    return results[label], stream_path, out_path


CASES = []


def case(name, fn):
    CASES.append((name, fn))


_res, _stream_path, _out_path = _run_one("dupcheck")

case("the run exits clean",
     lambda: _res["exit"] == 0)

case("the archival copy (.out.json) keeps the FULL deliverable -- the same text "
     "sc_resume_loop's classifier and the -R record are built from",
     lambda: _read(_out_path) == STREAM_BODY)

case("the -P stream is NOT a second full copy once the run has completed",
     lambda: _read(_stream_path) != STREAM_BODY)

case("...it is bounded to a small fraction of the archival copy's size",
     lambda: len(_read(_stream_path) or "") < len(STREAM_BODY) / 4)

case("...and it points at the archival copy rather than losing the reference",
     lambda: os.path.basename(_out_path) in (_read(_stream_path) or ""))

# A failed run still leaves the FULL deliverable text in .out.json (nothing here is lost on
# failure), and the stream is bounded exactly the same as a clean run.
_res_fail, _stream_fail, _out_fail = _run_one("failcheck", exit_code=1)

case("a non-zero exit still keeps the full text in .out.json",
     lambda: _res_fail["exit"] == 1 and _read(_out_fail) == STREAM_BODY)

case("...and the stream is bounded the same way on a failed run",
     lambda: _read(_stream_fail) != STREAM_BODY
             and len(_read(_stream_fail) or "") < len(STREAM_BODY) / 4)

# Defensive: a job shape with no -P at all (no progress file ever created) must not crash the
# bounding step -- it has nothing to bound.
_res_nop, _stream_nop, _out_nop = _run_one("nopcheck", with_progress=False)

case("a job with no -P stream does not crash the post-run bounding step",
     lambda: _res_nop["exit"] == 0 and not os.path.exists(_stream_nop)
             and _read(_out_nop) == STREAM_BODY)


def main():
    failed = 0
    total = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        total += 1
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
