#!/usr/bin/env python3
"""Proof for `tools/yt_transcript.py`: a YouTube URL becomes a timestamped local transcript.

Every case plants the process runner, so no case touches the network or needs `uvx`. The runner
answers the two commands the tool issues: the transcript fetch (`youtube_transcript_api ... --format
json`) and the metadata print (`yt-dlp --skip-download --print`). The transcript library exits 0
and prints prose when a video has no transcript, so that shape is planted as the no-captions case.

    python -m pytest .claude/tests/test_yt_transcript.py -q
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "yt_transcript.py")
VID = "D8PikZ1KhUo"
SEGMENTS = [[{"text": "Before we get started", "start": 0.4, "duration": 2.0},
             {"text": "one question\nfor you", "start": 65.2, "duration": 1.5},
             {"text": "the end", "start": 3725.0, "duration": 1.0}]]
META = "If you have a Claude sub, watch this\tTheo - t3.gg\t20261002\t1:06:36\n"
NO_CAPTIONS = ("Could not retrieve a transcript for the video https://www.youtube.com/watch?v=%s! "
               "This is most likely caused by:\n\nSubtitles are disabled for this video\n" % VID)
URL_FORMS = ["https://youtu.be/%s" % VID, "https://youtu.be/%s?si=abc&t=5" % VID,
             "https://www.youtube.com/watch?v=%s&t=12s" % VID, "https://m.youtube.com/watch?v=%s" % VID,
             "https://www.youtube.com/shorts/%s" % VID, "https://youtube.com/live/%s" % VID,
             "https://www.youtube.com/embed/%s" % VID, VID]


@pytest.fixture
def yt():
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


@pytest.fixture
def fetched(yt, tmp_path):
    """One successful fetch into a fresh out dir: the exit code, stdout, stderr, file body and the file path."""
    out_dir = str(tmp_path / "ok")
    code, out, err = invoke(yt, [URL_FORMS[0], "--out", out_dir], runner())
    path = os.path.join(out_dir, "%s.md" % VID)
    body = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    return {"code": code, "out": out, "err": err, "body": body, "path": path, "out_dir": out_dir}


@pytest.mark.parametrize("url", URL_FORMS, ids=[u.replace("https://", "")[:40] for u in URL_FORMS])
def test_video_id_reads_each_url_form(yt, url):
    assert yt.video_id(url) == VID, repr(yt.video_id(url))


@pytest.mark.parametrize("bad", ["https://vimeo.com/123", "https://www.youtube.com/watch?v=short", "not a url", ""])
def test_video_id_refuses_a_non_video_url(yt, bad):
    assert yt.video_id(bad) is None, repr(yt.video_id(bad))


def test_a_timestamp_under_an_hour_reads_mm_ss(yt):
    assert yt.stamp(65.2) == "01:05", yt.stamp(65.2)


def test_a_timestamp_past_an_hour_reads_h_mm_ss(yt):
    assert yt.stamp(3725.0) == "1:02:05", yt.stamp(3725.0)


def test_the_slug_is_the_lowercase_kebab_of_the_title(yt):
    assert yt.slug("If you have a Claude sub, watch this") == "if-you-have-a-claude-sub-watch-this"


def test_a_fetch_exits_zero_and_writes_the_transcript_file(fetched):
    assert fetched["code"] == 0 and fetched["body"], "code=%r err=%r" % (fetched["code"], fetched["err"])


def test_the_header_names_title_channel_date_duration_url_and_fetch_date(fetched):
    body = fetched["body"]
    assert all(s in body for s in ("If you have a Claude sub, watch this", "Theo - t3.gg", "2026-10-02",
                                   "1:06:36", "https://youtu.be/%s" % VID, "fetched:")), body[:400]


def test_each_segment_is_one_stamped_line_with_inner_newlines_folded(fetched):
    body = fetched["body"]
    assert ("[00:00] Before we get started" in body and "[01:05] one question for you" in body
            and "[1:02:05] the end" in body), body[-300:]


def test_stdout_is_a_json_summary_with_id_slug_path_and_line_count(fetched):
    try:
        summary = json.loads(fetched["out"])
    except ValueError:
        summary = {}
    assert (summary.get("id") == VID and summary.get("slug") == "if-you-have-a-claude-sub-watch-this"
            and summary.get("lines") == 3 and os.path.samefile(summary.get("path", fetched["path"]), fetched["path"])), fetched["out"]


def test_a_cached_transcript_is_reused_without_a_fetch(yt, fetched):
    run = runner()
    code, out, _ = invoke(yt, [VID, "--out", fetched["out_dir"]], run)
    assert code == 0 and run.calls == [] and json.loads(out).get("cached") is True, repr(run.calls)


def test_refresh_fetches_again(yt, fetched):
    run = runner()
    code, _, _ = invoke(yt, [VID, "--out", fetched["out_dir"], "--refresh"], run)
    assert code == 0 and len(run.calls) == 2, repr(run.calls)


def test_unreadable_metadata_still_writes_the_transcript_with_the_slug_falling_back_to_the_id(yt, tmp_path):
    md_dir = str(tmp_path / "nometa")
    code, out, _ = invoke(yt, [VID, "--out", md_dir], runner(meta_out="", meta_rc=1))
    assert (code == 0 and json.loads(out).get("slug") == VID.lower()
            and "title: unknown" in open(os.path.join(md_dir, "%s.md" % VID), encoding="utf-8").read()), out


def test_no_transcript_exits_three_with_the_library_reason_and_writes_no_file(yt, tmp_path):
    nc_dir = tmp_path / "nocap"
    nc_dir.mkdir()
    code, _, err = invoke(yt, [VID, "--out", str(nc_dir)], runner(transcript_out=NO_CAPTIONS))
    assert code == 3 and "Subtitles are disabled" in err and os.listdir(nc_dir) == [], err


def test_a_non_youtube_argument_exits_two(yt, tmp_path):
    code, _, err = invoke(yt, ["https://vimeo.com/1", "--out", str(tmp_path)], runner())
    assert code == 2 and "YouTube" in err, err


def test_a_missing_uvx_exits_four_naming_uvx_and_writes_no_file(yt, tmp_path):
    nc_dir = tmp_path / "nouvx"
    nc_dir.mkdir()
    code, _, err = invoke(yt, [VID, "--out", str(nc_dir)], runner(missing=True))
    assert code == 4 and "uvx" in err and os.listdir(nc_dir) == [], err
