#!/usr/bin/env python3
"""Fetch a YouTube video's spoken and written text into a local cache: timed captions, and with
`--meta` the description and top-level comments.

Two `uvx` runs, no MCP and no API key: `youtube-transcript-api` for the timed captions and
`yt-dlp` for the identity header. The transcript lands in a gitignored cache, one file per video
id, as an identity header plus one `[mm:ss] text` line per caption segment; the stamps are the
quote anchors a reader cites. A cached file is reused unless `--refresh` asks for a new fetch.

`--meta` adds one `yt-dlp` run into `<id>_meta/`: `v.description`, and `comments.md` with up to
`--max-comments` top-level comments, most liked first, one `- [N likes] text` line each.

Missing metadata is not fatal: the header says `unknown` and the slug falls back to the id. A
failed `--meta` fetch is not fatal either: the summary carries `meta_error` and no extras paths.

Usage:
    python3 .claude/tools/yt_transcript.py <youtube-url-or-id> [--out DIR] [--refresh] [--meta [--max-comments N]]

stdout: one JSON object {id, url, title, channel, published, duration, slug, path, lines, cached};
with `--meta` also {description, comments, comment_count} or {meta_error}.
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


def fetch_extras(vid, out, max_comments, refresh, run):
    """{description, comments, comment_count} from `<out>/<id>_meta/`, fetching unless cached; else {meta_error}."""
    meta_dir = os.path.join(out, "%s_meta" % vid)
    stem = os.path.join(meta_dir, "v")
    comments_path = os.path.join(meta_dir, "comments.md")
    if refresh or not os.path.exists(comments_path):
        os.makedirs(meta_dir, exist_ok=True)
        for old in (stem + ".info.json", stem + ".description", comments_path):
            if os.path.exists(old):
                os.remove(old)
        got = run(["uvx", "--from", "yt-dlp", "yt-dlp", "--skip-download", "--write-description",
                   "--write-comments", "--extractor-args",
                   "youtube:max_comments=%d,all,0,0;comment_sort=top" % max_comments, "-o", stem,
                   "https://youtu.be/%s" % vid], capture_output=True, text=True, encoding="utf-8")
        try:
            with open(stem + ".info.json", encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            reason = ((got.stderr or "").strip().splitlines() or ["no info.json written"])[-1]
            return {"meta_error": "yt-dlp exit %s: %s" % (got.returncode, reason)}
        top = [c for c in info.get("comments") or [] if c.get("parent", "root") == "root"]
        top.sort(key=lambda c: c.get("like_count") or 0, reverse=True)
        with open(comments_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines("- [%d likes] %s\n" % (c.get("like_count") or 0, " ".join(str(c.get("text", "")).split()))
                          for c in top)
    with open(comments_path, encoding="utf-8") as fh:
        count = sum(1 for line in fh if line.startswith("- ["))
    desc = stem + ".description"
    return {"description": desc if os.path.exists(desc) else None, "comments": comments_path,
            "comment_count": count}


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
    ap.add_argument("--meta", action="store_true", help="also fetch the description and top-level comments")
    ap.add_argument("--max-comments", type=int, default=200, help="comments to fetch with --meta (default 200)")
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
    summary = {"id": vid, "url": "https://youtu.be/%s" % vid, "title": title,
               "channel": meta.get("channel", "unknown"), "published": meta.get("published", "unknown"),
               "duration": meta.get("duration", "unknown"),
               "slug": slug(title) if title != "unknown" else vid.lower(),
               "path": path, "lines": count, "cached": cached}
    if args.meta:
        try:
            summary.update(fetch_extras(vid, args.out, args.max_comments, args.refresh, run))
        except FileNotFoundError:
            summary["meta_error"] = "uvx is not on PATH"
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
