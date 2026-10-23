"""Turn a video, GIF or YouTube URL into timestamped contact sheets (plus a transcript when one exists).

Read each printed sheet path with the Read tool: one image shows many moments for the price of one frame.
An image costs ceil(width / 28) * ceil(height / 28) tokens. Older models downsize an image past a 1568 px
long edge or 1568 tokens; newer high-resolution models allow 2576 px or 4784 tokens. Every sheet is fitted
under this script's own cap of 1568 tokens and 1568 px (printed as `cap:`), so no model resizes it and the
cost per sheet stays predictable. --width sets the largest cell (the resolution lever; --crop selects a
source region instead); cells shrink further when many moments share a sheet.

  python .claude/scripts/video_frames.py clip.gif                       # 12 moments picked by motion
  python .claude/scripts/video_frames.py talk.mp4 --mode even           # evenly spaced (narration, slow UI)
  python .claude/scripts/video_frames.py talk.mp4 --every 10            # one moment per ~10 s, several sheets
  python .claude/scripts/video_frames.py rec.mp4 --crop 0,600,400,120   # x,y,w,h in source pixels
  python .claude/scripts/video_frames.py URL --start 30 --duration 20   # YouTube: downloads <=720p to scratch
  python .claude/scripts/video_frames.py rec.mp4 --start 3.2 --duration 0.2 --frames 1 --width 1200   # zoom in on one moment

--frames sets the moments on a sheet; the grid that fits keeps all its cells, so a sheet may show a few more
(printed). --every SEC spaces moments evenly at most SEC apart across the window and starts a new sheet every
--frames moments, at most 8 sheets (96 moments, about 12k tokens); narrow --start/--duration or raise --every
past that.

Scene mode scans the window at 6000 / window-seconds candidates per second (1 to 30), so a 1 h window is
scanned at about 1.7 per second and a change shorter than that can be missed: narrow the window to look
closer. Sheet cells carry their timestamp when a monospace font is found (otherwise `labels: off` is printed
and the `times:` line lists the cells row by row). Transcript sources, cheapest first: YouTube subtitles, a
sidecar .srt/.vtt next to the file, an embedded subtitle stream, then `--whisper` (faster-whisper, opt-in,
installed into the interpreter that runs this script).
"""
import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / ".claude" / "scratch"
MAX_EDGE = 1568
MAX_TOKENS = 1568
PATCH = 28
PAD = 4
MIN_CELL = 8
MAX_FRAMES = 24
DEFAULT_FRAMES = 12
MAX_SHEETS = 8
FONTS = (Path("C:/Windows/Fonts/consola.ttf"), Path("/System/Library/Fonts/Menlo.ttc"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"))
WHISPER_MODEL = "base"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def probe(path: Path) -> dict:
    res = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height:format=duration", "-of", "json", str(path)])
    if res.returncode:
        sys.exit(f"ffprobe failed on {path}: {res.stderr.strip()}")
    data = json.loads(res.stdout)
    kinds = [s["codec_type"] for s in data["streams"]]
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        sys.exit(f"no video stream in {path}")
    return {"width": video["width"], "height": video["height"],
            "duration": float(data["format"].get("duration") or 0),
            "audio": "audio" in kinds, "subtitle": "subtitle" in kinds}


def fetch_url(url: str) -> tuple[Path, list[Path]]:
    """Download <=720p into scratch/downloads (reused on a repeat run), then try English subtitles."""
    dl = SCRATCH / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    base = [sys.executable, "-m", "yt_dlp", "--no-playlist", "-o", str(dl / "%(id)s.%(ext)s")]
    res = run(base + ["--no-simulate", "-f", "bv*[height<=720]+ba/b[height<=720]/b", "--merge-output-format", "mp4",
                      "--print", "after_move:filepath", url])
    video = next((p for p in map(Path, res.stdout.splitlines()) if p.suffix == ".mp4" and p.exists()), None)
    if video is None:
        sys.exit(f"yt-dlp failed:\n{res.stderr.strip()[-600:]}")
    subs = sorted(dl.glob(f"{video.stem}.en*.srt"))
    if not subs:
        run(base + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en,en-orig",
                    "--convert-subs", "srt", url])
        subs = sorted(dl.glob(f"{video.stem}.en*.srt"))
    return video, subs


def image_tokens(width: int, height: int) -> int:
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


def sheet_size(cols: int, rows: int, cell: int, aspect: float) -> tuple[int, int]:
    """Pixel size of a cols x rows sheet: ffmpeg `tile` pads between cells only, and `scale=w:-2` makes
    each cell an even number of rows tall."""
    height = 2 * math.ceil(cell * aspect / 2)
    return cols * cell + PAD * (cols - 1), rows * height + PAD * (rows - 1)


def widest_cell(cols: int, rows: int, aspect: float, want: int) -> int | None:
    """Largest cell width, at most `want`, whose sheet stays inside MAX_EDGE and MAX_TOKENS."""
    cell = min(want, (MAX_EDGE - PAD * (cols - 1)) // cols)
    while cell >= min(MIN_CELL, want):
        width, height = sheet_size(cols, rows, cell, aspect)
        if max(width, height) <= MAX_EDGE and image_tokens(width, height) <= MAX_TOKENS:
            return cell
        cell -= 1
    return None


def fit_grid(n: int, aspect: float, want: int) -> tuple[int, int, int]:
    """Return (columns, rows, cell width) for the layout that keeps the sheet inside both caps. Layouts whose
    cells are within 5% of the widest tie; among them the sheet closest to 3:2 landscape wins, then the one
    with the fewest empty cells. `aspect` is height / width."""
    options = []
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell = widest_cell(cols, rows, aspect, want)
        if cell is not None:
            width, height = sheet_size(cols, rows, cell, aspect)
            options.append((cell, abs(math.log(width / height / 1.5)), cols * rows - n, cols, rows))
    if not options:
        raise ValueError(f"no layout fits {n} moments of aspect {aspect:.2f} under the sheet cap")
    floor = 0.95 * max(option[0] for option in options)
    cell, _, _, cols, rows = min((o for o in options if o[0] >= floor), key=lambda o: (o[1], o[2]))
    return cols, rows, cell


def even_picks(candidates: int, n: int) -> list[int]:
    """Candidate indices at the midpoints of n equal steps."""
    n = min(n, candidates)
    return sorted({min(candidates - 1, int((i + 0.5) * candidates / n)) for i in range(n)})


def plan_sheets(length: float, every: float, per_sheet: int) -> list[tuple[float, float, int]]:
    """Split a window into (offset, length, moments) sheets of equal steps at most `every` seconds apart."""
    n = max(1, math.ceil(length / every - 1e-9))
    sheets = math.ceil(n / per_sheet)
    if sheets > MAX_SHEETS:
        raise ValueError(f"{n} moments need {sheets} sheets of {per_sheet}; the limit is {MAX_SHEETS}. "
                         f"Raise --every or narrow --start/--duration")
    step = length / n
    base, extra = divmod(n, sheets)
    plan, offset = [], 0.0
    for i in range(sheets):
        count = base + (i < extra)
        span = length - offset if i == sheets - 1 else count * step
        plan.append((offset, span, count))
        offset += span
    return plan


def scene_scores(src: Path, start: float, length: float, rate: float, vf: str) -> list[float]:
    """Per-candidate frame-difference score (0..1) at `rate` candidates per second."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(src), "-vf",
           f"{vf}fps={rate},scale='min(160,iw)':-2,select='gte(scene,0)',metadata=print:file=-",
           "-an", "-f", "null", "-"]
    res = run(cmd)
    if res.returncode:
        sys.exit(f"ffmpeg scene scan failed: {res.stderr.strip()[-400:]}")
    return [float(m) for m in re.findall(r"lavfi\.scene_score=([0-9.eE+-]+)", res.stdout)]


def pick_moments(scores: list[float], n: int) -> list[int]:
    """The highest-scoring candidates at least half an even step apart. A sheet of six or more also keeps
    the first and last candidate as the opening and closing frames."""
    total = len(scores)
    n = min(n, total)
    chosen = [0, total - 1] if n >= 6 else []
    gap = max(1, total // (2 * n))
    for i in sorted(range(total), key=lambda i: -scores[i]):
        if len(chosen) >= n:
            break
        if all(abs(i - c) >= gap for c in chosen):
            chosen.append(i)
    return sorted(chosen)


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[16:24]
    return int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")


def parse_cues(text: str) -> list[tuple[float, str]]:
    """Parse SRT or VTT into (start seconds, line) pairs, dropping the repeated lines of rolling captions."""
    cues, last = [], ""
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = block.strip().split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        stamp = re.match(r"\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)", lines[idx])
        if not stamp:
            continue
        h, m, s, ms = stamp.groups()
        start = int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)
        for ln in lines[idx + 1:]:
            ln = re.sub(r"<[^>]+>", "", ln).strip()
            if ln and ln != last:
                cues.append((start, ln))
                last = ln
    return cues


def whisper_cues(src: Path, start: float, length: float) -> list[tuple[float, str]]:
    """Decode the window to 16 kHz mono PCM with ffmpeg (faster-whisper's own decoder breaks on new PyAV)."""
    import numpy
    from faster_whisper import WhisperModel
    res = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(src),
                          "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True)
    if res.returncode:
        sys.exit(f"audio extract failed: {res.stderr.decode(errors='replace').strip()[-300:]}")
    segments, _ = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8").transcribe(
        numpy.frombuffer(res.stdout, dtype=numpy.float32))
    return [(start + seg.start, seg.text.strip()) for seg in segments if seg.text.strip()]


def find_transcript(src: Path, info: dict, subs: list[Path], start: float, end: float,
                    use_whisper: bool) -> tuple[list[tuple[float, str]], str]:
    sidecars = subs or sorted(p for p in src.parent.glob(f"{src.stem}*") if p.suffix in (".srt", ".vtt"))
    if sidecars:
        pick = next((p for p in sidecars if p.name.endswith(".en.srt")), sidecars[0])
        label = "youtube subtitles" if subs else f"sidecar {pick.name}"
        cues = parse_cues(pick.read_text(encoding="utf-8", errors="replace"))
    elif info["subtitle"]:
        res = run(["ffmpeg", "-v", "error", "-i", str(src), "-map", "0:s:0", "-f", "srt", "-"])
        cues, label = parse_cues(res.stdout), "embedded subtitle stream"
    elif use_whisper and info["audio"]:
        if importlib.util.find_spec("faster_whisper") is None:
            return [], f"--whisper needs faster-whisper in {sys.executable}: {sys.executable} -m pip install faster-whisper"
        cues, label = whisper_cues(src, start, end - start), f"whisper {WHISPER_MODEL}"
    else:
        return [], (f"audio present, no transcript source (add --whisper after "
                    f"`{sys.executable} -m pip install faster-whisper`)" if info["audio"] else "")
    return [c for c in cues if start <= c[0] <= end], label


def find_font() -> Path | None:
    return next((f for f in FONTS if f.exists()), None)


def label_filter(font: Path | None, start: float, cell: int) -> str:
    if font is None:
        return ""
    face = font.as_posix().replace(":", "\\:")
    return (f",setpts=PTS+{start}/TB,drawtext=fontfile='{face}':text='%{{pts\\:hms}}':"
            f"fontsize={max(10, cell // 22)}:fontcolor=white:box=1:boxcolor=black@0.6:x=3:y=3")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("input", help="video or GIF path, or a YouTube URL")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                        help=f"moments on a sheet, max {MAX_FRAMES} (default {DEFAULT_FRAMES}); with --every, "
                             f"the most one sheet holds")
    parser.add_argument("--mode", choices=("scene", "even"),
                        help="scene: biggest frame-to-frame changes (default); even: equal time steps")
    parser.add_argument("--every", type=float, metavar="SEC",
                        help=f"even moments at most SEC apart over the window, one sheet per --frames moments "
                             f"(max {MAX_SHEETS} sheets)")
    parser.add_argument("--start", type=float, default=0.0, help="window start in seconds")
    parser.add_argument("--duration", type=float, help="window length in seconds (default: to end)")
    parser.add_argument("--crop", help="x,y,w,h in source pixels: a source region, applied before scoring and scaling")
    parser.add_argument("--width", type=int, default=640,
                        help=f"largest cell width in pixels, the resolution lever; the sheet still fits "
                             f"{MAX_TOKENS} tokens and {MAX_EDGE} px, so cells shrink as moments are added")
    parser.add_argument("--whisper", action="store_true", help="transcribe audio with faster-whisper when no subtitles exist")
    parser.add_argument("--out", type=Path, help="output folder (default: .claude/scratch/frames/<name>)")
    args = parser.parse_args()
    if not 1 <= args.frames <= MAX_FRAMES:
        sys.exit(f"--frames must be 1..{MAX_FRAMES}; use --every for longer videos")
    if args.every is not None and args.every <= 0:
        sys.exit("--every must be a positive number of seconds")
    if args.every and args.mode == "scene":
        sys.exit("--every spaces moments evenly: drop --mode scene")
    mode = args.mode or ("even" if args.every else "scene")

    subs: list[Path] = []
    if re.match(r"https?://", args.input):
        src, subs = fetch_url(args.input)
    else:
        src = Path(args.input)
        if not src.exists():
            sys.exit(f"not found: {src}")
    info = probe(src)
    end = min(info["duration"], args.start + args.duration) if args.duration else info["duration"]
    length = end - args.start
    if length <= 0:
        sys.exit(f"empty window: start {args.start}s, video is {info['duration']:.1f}s")

    vf, src_w, src_h = "", info["width"], info["height"]
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > src_w or y + h > src_h:
            sys.exit(f"crop {args.crop} outside the {src_w}x{src_h} source")
        vf, src_w, src_h = f"crop={w}:{h}:{x}:{y},", w, h

    try:
        plan = plan_sheets(length, args.every, args.frames) if args.every else [(0.0, length, args.frames)]
    except ValueError as err:
        sys.exit(str(err))
    font = find_font()
    tag = hashlib.sha1(json.dumps([str(src), args.start, end, args.frames, mode, args.every, args.crop,
                                   args.width]).encode()).hexdigest()[:6]
    out = (args.out or SCRATCH / "frames" / src.stem).resolve()
    out.mkdir(parents=True, exist_ok=True)

    print(f"source: {info['width']}x{info['height']}, {info['duration']:.1f}s; window {args.start:.1f}-{end:.1f}s; "
          f"mode {mode}{'; every ' + format(length / sum(c for _, _, c in plan), '.2f') + 's' if args.every else ''}"
          f"{'; crop ' + args.crop if args.crop else ''}")
    print(f"cap: {MAX_TOKENS} tokens and {MAX_EDGE} px per sheet; cell width at most {min(args.width, src_w)} px")
    if font is None:
        print("labels: off, no monospace font found; match cells to the times line, row by row")

    total_tokens = 0
    for number, (offset, span, count) in enumerate(plan, 1):
        win_start = args.start + offset
        rate = min(30.0, max(1.0, 6000 / span))
        candidates = max(1, int(span * rate))
        scores = scene_scores(src, win_start, span, rate, vf) if mode == "scene" else []
        if mode == "scene":
            candidates = len(scores) or 1
        want = min(count, candidates)
        try:
            cols, rows, cell = fit_grid(want, src_h / src_w, min(args.width, src_w))
        except ValueError as err:
            sys.exit(str(err))
        if not args.every:
            want = min(cols * rows, MAX_FRAMES, candidates)
        picks = (pick_moments(scores, want) if scores else [0]) if mode == "scene" else even_picks(candidates, want)
        times = [win_start + i / rate for i in picks]

        keep = "+".join(f"eq(n,{i})" for i in picks)
        multi = len(plan) > 1
        sheet = out / f"sheet_{tag}{'_' + str(number) if multi else ''}.png"
        res = run(["ffmpeg", "-v", "error", "-y", "-ss", str(win_start), "-t", str(span), "-i", str(src), "-vf",
                   f"{vf}fps={rate},select='{keep}',scale={cell}:-2{label_filter(font, win_start, cell)},"
                   f"tile={cols}x{rows}:padding={PAD}:color=black", "-an", "-frames:v", "1", "-update", "1", str(sheet)])
        if res.returncode or not sheet.exists():
            sys.exit(f"ffmpeg sheet failed: {res.stderr.strip()[-400:]}")

        width, height = png_size(sheet)
        tokens = image_tokens(width, height)
        total_tokens += tokens
        print(f"sheet{f' {number}/{len(plan)}' if multi else ''}: {sheet}")
        print(f"       {width}x{height}, {len(picks)} moments in {cols}x{rows} (cell {cell} px), ~{tokens} tokens")
        print("times: " + " ".join(f"{t:.2f}" for t in times))
    if len(plan) > 1:
        print(f"total: {len(plan)} sheets, ~{total_tokens} tokens")

    cues, source = find_transcript(src, info, subs, args.start, end, args.whisper)
    if cues:
        merged: dict[float, list[str]] = {}
        for t, line in cues:
            merged.setdefault(t, []).append(line)
        text = "\n".join(f"[{int(t // 60)}:{t % 60:04.1f}] {' '.join(lines)}" for t, lines in merged.items())
        transcript = out / f"transcript_{tag}.txt"
        transcript.write_text(text + "\n", encoding="utf-8", newline="\n")
        print(f"transcript: {transcript} ({len(merged)} lines, ~{len(text) // 4} tokens, {source})")
    elif source:
        print(f"transcript: none; {source}")


if __name__ == "__main__":
    main()
