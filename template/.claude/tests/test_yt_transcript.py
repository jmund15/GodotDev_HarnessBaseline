#!/usr/bin/env python3
"""Proof for `tools/yt_transcript.py`: a YouTube URL becomes a timestamped local transcript.

Every case plants the process runner, so no case touches the network or needs `uvx`. The runner
answers the two commands the tool issues: the transcript fetch (`youtube_transcript_api ... --format
json`) and the metadata print (`yt-dlp --skip-download --print`). The transcript library exits 0
and prints prose when a video has no transcript, so that shape is planted as the no-captions case.

    python3 .claude/tests/test_yt_transcript.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "yt_transcript.py")
VID = "D8PikZ1KhUo"
SEGMENTS = [[{"text": "Before we get started", "start": 0.4, "duration": 2.0},
             {"text": "one question\nfor you", "start": 65.2, "duration": 1.5},
             {"text": "the end", "start": 3725.0, "duration": 1.0}]]
META = "If you have a Claude sub, watch this\tTheo - t3.gg\t20261002\t1:06:36\n"
NO_CAPTIONS = ("Could not retrieve a transcript for the video https://www.youtube.com/watch?v=%s! "
               "This is most likely caused by:\n\nSubtitles are disabled for this video\n" % VID)


def load():
    spec = importlib.util.spec_from_file_location("yt_transcript", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runner(transcript_out=None, meta_out=META, meta_rc=0, missing=False):
    """A planted `subprocess.run`; `calls` records each argv it saw."""
    calls = []

    def run(argv, **_kw):
        calls.append(argv)
        if missing:
            raise FileNotFoundError("uvx")
        if "youtube_transcript_api" in argv:
            out = json.dumps(SEGMENTS) if transcript_out is None else transcript_out
            return subprocess.CompletedProcess(argv, 0, out, "")
        return subprocess.CompletedProcess(argv, meta_rc, meta_out, "" if meta_rc == 0 else "ERROR: x")
    run.calls = calls
    return run


def invoke(mod, argv, run):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv, run=run)
    return code, out.getvalue(), err.getvalue()


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    try:
        yt = load()
    except Exception as exc:
        case("tools/yt_transcript.py imports", False, repr(exc))
        return report(cases)

    for url in ("https://youtu.be/%s" % VID, "https://youtu.be/%s?si=abc&t=5" % VID,
                "https://www.youtube.com/watch?v=%s&t=12s" % VID, "https://m.youtube.com/watch?v=%s" % VID,
                "https://www.youtube.com/shorts/%s" % VID, "https://youtube.com/live/%s" % VID,
                "https://www.youtube.com/embed/%s" % VID, VID):
        case("video_id reads %s" % url, yt.video_id(url) == VID, repr(yt.video_id(url)))
    for bad in ("https://vimeo.com/123", "https://www.youtube.com/watch?v=short", "not a url", ""):
        case("video_id refuses %r" % bad, yt.video_id(bad) is None, repr(yt.video_id(bad)))

    case("timestamps under an hour read mm:ss", yt.stamp(65.2) == "01:05", yt.stamp(65.2))
    case("timestamps past an hour read h:mm:ss", yt.stamp(3725.0) == "1:02:05", yt.stamp(3725.0))
    case("slug is lowercase kebab of the title",
         yt.slug("If you have a Claude sub, watch this") == "if-you-have-a-claude-sub-watch-this")

    # Success: the file carries the identity header and one stamped line per caption segment.
    out_dir = tempfile.mkdtemp(prefix="yt_ok_")
    run = runner()
    code, out, err = invoke(yt, ["https://youtu.be/%s" % VID, "--out", out_dir], run)
    path = os.path.join(out_dir, "%s.md" % VID)
    body = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    try:
        summary = json.loads(out)
    except ValueError:
        summary = {}
    case("a fetch exits 0 and writes <id>.md", code == 0 and body, "code=%r err=%r" % (code, err))
    case("the header names title, channel, published date, duration, url and fetch date",
         all(s in body for s in ("If you have a Claude sub, watch this", "Theo - t3.gg", "2026-10-02",
                                 "1:06:36", "https://youtu.be/%s" % VID, "fetched:")), body[:400])
    case("each segment is one stamped line, inner newlines folded",
         "[00:00] Before we get started" in body and "[01:05] one question for you" in body
         and "[1:02:05] the end" in body, body[-300:])
    case("stdout is a JSON summary with id, slug, path and line count",
         summary.get("id") == VID and summary.get("slug") == "if-you-have-a-claude-sub-watch-this"
         and summary.get("lines") == 3 and os.path.samefile(summary.get("path", path), path), out)

    # Reuse: a cached transcript is not fetched again unless --refresh asks.
    run2 = runner()
    code2, out2, _ = invoke(yt, [VID, "--out", out_dir], run2)
    case("a cached transcript is reused without a fetch", code2 == 0 and run2.calls == []
         and json.loads(out2).get("cached") is True, repr(run2.calls))
    run3 = runner()
    code3, _, _ = invoke(yt, [VID, "--out", out_dir, "--refresh"], run3)
    case("--refresh fetches again", code3 == 0 and len(run3.calls) == 2, repr(run3.calls))

    # Metadata failure is not fatal: the transcript still lands, slugged by id.
    md_dir = tempfile.mkdtemp(prefix="yt_nometa_")
    code4, out4, _ = invoke(yt, [VID, "--out", md_dir], runner(meta_out="", meta_rc=1))
    case("unreadable metadata still writes the transcript, slug falls back to the id",
         code4 == 0 and json.loads(out4).get("slug") == VID.lower()
         and "title: unknown" in open(os.path.join(md_dir, "%s.md" % VID), encoding="utf-8").read(), out4)

    # Failure exits: each writes nothing and says why on stderr.
    nc_dir = tempfile.mkdtemp(prefix="yt_nocap_")
    code5, _, err5 = invoke(yt, [VID, "--out", nc_dir], runner(transcript_out=NO_CAPTIONS))
    case("no transcript exits 3 with the library's reason and writes no file",
         code5 == 3 and "Subtitles are disabled" in err5 and os.listdir(nc_dir) == [], err5)
    code6, _, err6 = invoke(yt, ["https://vimeo.com/1", "--out", nc_dir], runner())
    case("a non-YouTube argument exits 2", code6 == 2 and "YouTube" in err6, err6)
    code7, _, err7 = invoke(yt, [VID, "--out", nc_dir], runner(missing=True))
    case("a missing uvx exits 4 naming uvx", code7 == 4 and "uvx" in err7 and os.listdir(nc_dir) == [], err7)

    return report(cases)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
