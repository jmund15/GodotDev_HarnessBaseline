#!/usr/bin/env python3
"""Fetch a YouTube video's captions into a timestamped local transcript for ingestion.

Two `uvx` runs, no MCP and no API key: `youtube-transcript-api` for the timed captions and
`yt-dlp` for the identity header. The transcript lands in a gitignored cache, one file per video
id, as an identity header plus one `[mm:ss] text` line per caption segment; the stamps are the
quote anchors a digest cites. A cached file is reused unless `--refresh` asks for a new fetch.

Missing metadata is not fatal: the header says `unknown` and the slug falls back to the id.

Usage:
    python3 .claude/tools/yt_transcript.py <youtube-url-or-id> [--out DIR] [--refresh]

stdout: one JSON object {id, url, title, channel, published, duration, slug, path, lines, cached}.
Exit 0 written or reused; 2 the argument is not a YouTube video; 3 the video has no retrievable
transcript (the library's reason on stderr; nothing is written); 4 `uvx` is not on PATH.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "cache", "youtube")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")
PATH_KINDS = ("shorts", "live", "embed", "v")
META_FORMAT = "%(title)s\t%(uploader)s\t%(upload_date)s\t%(duration_string)s"


def video_id(arg):
    """The 11-character video id in a YouTube URL or bare id, else None."""
    arg = (arg or "").strip()
    if ID_RE.match(arg):
        return arg
    m = re.match(r"^(?:https?://)?([^/?#]+)(/[^?#]*)?(?:\?([^#]*))?", arg)
    if not m:
        return None
    host, path, query = m.group(1).lower(), m.group(2) or "", m.group(3) or ""
    candidate = None
    if host == "youtu.be":
        candidate = path.strip("/").split("/")[0]
    elif host in HOSTS:
        parts = [p for p in path.split("/") if p]
        if parts[:1] == ["watch"]:
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            candidate = params.get("v")
        elif len(parts) >= 2 and parts[0] in PATH_KINDS:
            candidate = parts[1]
    return candidate if candidate and ID_RE.match(candidate) else None


def stamp(seconds):
    """mm:ss under an hour, h:mm:ss from an hour on."""
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def fetch_meta(vid, run):
    """{title, channel, published, duration}; `unknown` for any field yt-dlp could not give."""
    meta = dict.fromkeys(("title", "channel", "published", "duration"), "unknown")
    got = run(["uvx", "--from", "yt-dlp", "yt-dlp", "--skip-download", "--print", META_FORMAT,
               "https://youtu.be/%s" % vid], capture_output=True, text=True, encoding="utf-8")
    line = (got.stdout or "").strip().splitlines()
    if got.returncode != 0 or not line:
        return meta
    fields = line[-1].split("\t")
    if len(fields) == 4:
        title, channel, date, duration = fields
        meta.update(title=title or "unknown", channel=channel or "unknown", duration=duration or "unknown")
        if re.match(r"^\d{8}$", date):
            meta["published"] = "%s-%s-%s" % (date[:4], date[4:6], date[6:])
    return meta


def fetch_segments(vid, run):
    """(segments, None) on success, else (None, the library's reason)."""
    got = run(["uvx", "--from", "youtube-transcript-api", "youtube_transcript_api", vid,
               "--format", "json"], capture_output=True, text=True, encoding="utf-8")
    out = got.stdout or ""
    try:
        data = json.loads(out)
        segments = data[0] if data and isinstance(data[0], list) else None
    except (ValueError, IndexError, KeyError, TypeError):
        segments = None
    if not segments:
        reason = (out.strip() or (got.stderr or "").strip() or "no transcript returned")
        return None, reason
    return segments, None


def render(vid, meta, segments, fetched):
    head = ["---", "id: %s" % vid, "url: https://youtu.be/%s" % vid, "title: %s" % meta["title"],
            "channel: %s" % meta["channel"], "published: %s" % meta["published"],
            "duration: %s" % meta["duration"], "fetched: %s" % fetched, "---", ""]
    lines = ["[%s] %s" % (stamp(s.get("start", 0)), " ".join(str(s.get("text", "")).split()))
             for s in segments]
    return "\n".join(head + lines) + "\n"


def read_header(path):
    meta = {}
    with open(path, encoding="utf-8") as fh:
        if fh.readline().strip() != "---":
            return meta
        for line in fh:
            if line.strip() == "---":
                break
            key, _, value = line.partition(": ")
            meta[key.strip()] = value.strip()
    return meta


def main(argv=None, run=subprocess.run):
    ap = argparse.ArgumentParser(description="Fetch a YouTube transcript into the local cache.")
    ap.add_argument("video", help="a YouTube URL or an 11-character video id")
    ap.add_argument("--out", default=DEFAULT_OUT, help="directory for <id>.md (default: .claude/cache/youtube)")
    ap.add_argument("--refresh", action="store_true", help="fetch again even when a cached file exists")
    args = ap.parse_args(argv)

    vid = video_id(args.video)
    if vid is None:
        print("not a YouTube video URL or id: %r" % args.video, file=sys.stderr)
        return 2
    path = os.path.join(args.out, "%s.md" % vid)

    if os.path.exists(path) and not args.refresh:
        meta = read_header(path)
        with open(path, encoding="utf-8") as fh:
            count = sum(1 for line in fh if line.startswith("["))
        cached = True
    else:
        try:
            segments, reason = fetch_segments(vid, run)
            if segments is None:
                print("no transcript for %s: %s" % (vid, reason), file=sys.stderr)
                return 3
            meta = fetch_meta(vid, run)
        except FileNotFoundError:
            print("uvx is not on PATH; install uv (https://docs.astral.sh/uv/) to fetch transcripts",
                  file=sys.stderr)
            return 4
        os.makedirs(args.out, exist_ok=True)
        fetched = datetime.date.today().isoformat()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(vid, meta, segments, fetched))
        count = len(segments)
        cached = False

    title = meta.get("title", "unknown")
    print(json.dumps({"id": vid, "url": "https://youtu.be/%s" % vid, "title": title,
                      "channel": meta.get("channel", "unknown"), "published": meta.get("published", "unknown"),
                      "duration": meta.get("duration", "unknown"),
                      "slug": slug(title) if title != "unknown" else vid.lower(),
                      "path": path, "lines": count, "cached": cached}))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
