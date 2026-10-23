---
name: Video Frames
description: >-
  Use to see or read a video, GIF, screen recording or YouTube URL. A plain Read of a GIF shows only its first frame and a Read of a video shows no motion. SKIP still images; Read them directly.
---

# Video Frames

`python3 .claude/scripts/video_frames.py <file or URL>` writes contact sheets under `.claude/scratch/frames/<name>/` and prints each sheet path, its token cost and the `times:` of its cells. Read each printed path with the Read tool. Flags and defaults: `python3 .claude/scripts/video_frames.py --help`.

## Procedure

1. **Overview first.** Run it with no flags and Read the sheet. Cells carry timestamps; the `times:` line lists them row by row.
2. **Pick the mode by what you need.**
   - Something changes on screen (a recording, an animation, a GIF, a bug that appears): the default scene mode picks the biggest changes.
   - Narration, a talk, a slow UI or a steady scene: `--mode even`.
   - A long video where coverage matters: `--every SEC` (even moments at most SEC apart). Pick SEC so the sheet count fits the question; the script names its sheet limit when SEC is too small.
3. **Narrow the window.** After the overview, rerun with `--start` and `--duration` on the span that matters. Scene mode scans a longer window at fewer candidates per second, so a brief flash can slip through a long one.
4. **Zoom for detail.**
   - One small region (a HUD, a health bar, a text line): `--crop x,y,w,h` in source pixels; it selects a source region.
   - One moment at full size: a short window with `--frames 1 --width 1200`.
   - `--width` is the largest cell width. Every sheet stays under its printed `cap:`, so cells shrink as moments are added; fewer moments give larger cells.
5. **Sound.** A printed `transcript:` path is a text file to Read. With audio and no subtitles, `--whisper` transcribes with faster-whisper, which installs into the interpreter that runs the script (the message names it).

## Reading the output

- The sheet shows the moments the script chose, not every frame. Say which timestamps you saw; an action between two cells is unseen until you narrow the window around it.
- A `labels: off` line means no monospace font was found: map cells to `times:` row by row, left to right.
- A YouTube URL is downloaded at 720p or lower into `.claude/scratch/downloads/` and reused on a repeat run.
