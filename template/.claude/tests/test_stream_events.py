#!/usr/bin/env python3
"""Proof: `tools/stream_events.py` is the one `claude -p` stream-event iterator, and the two per-event
classifiers (sidecar_resume_check, extract_deliverable) read events through it.

Run: python3 .claude/tests/test_stream_events.py
"""
import importlib.util
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
import stream_events as se  # one module object, shared with every importer below


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_yields_only_json_object_lines():
    lines = [
        'noise before the stream\n',
        '{"type": "assistant", "n": 1}\n',
        '{"type": "assistant", "n": 2\n',
        '\n',
        '  {"type": "result", "n": 3}  \n',
        '[1, 2]\n',
    ]
    got = [d["n"] for d in se.iter_stream_events(lines)]
    assert got == [1, 3], got


def test_reads_a_file_object_lazily():
    fh = io.StringIO('{"type": "result", "n": 7}\n{"type": "user", "n": 8}\n')
    assert [d["n"] for d in se.iter_stream_events(fh)] == [7, 8]


def test_resume_check_reads_through_the_shared_iterator():
    rc = load("sidecar_resume_check_wire", os.path.join(TOOLS, "sidecar_resume_check.py"))
    assert rc.iter_stream_events is se.iter_stream_events
    assert not hasattr(rc, "_events"), "the hand parser must be gone"


def test_extract_deliverable_reads_through_the_shared_iterator():
    try:  # the benchmark suite that ships this classifier is project-local
        extract = os.path.join(os.path.dirname(HERE), "scripts", "benchmark_campaign", "scoring",
                               "extract_deliverable.py")
        if not os.path.isfile(extract):
            raise ImportError(extract)
        ed = load("extract_deliverable_wire", extract)
    except ImportError:
        return
    assert ed.iter_stream_events is se.iter_stream_events


def run():
    cases = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for case in cases:
        try:
            case()
            print("ok %s" % case.__name__)
        except Exception as exc:  # the proof reports every failing case, not the first
            failed += 1
            print("FAIL %s: %s: %s" % (case.__name__, type(exc).__name__, exc))
    print("\n%d/%d passed" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
