"""A job's brief never sits at the path its own deliverable lands on: `<out dir>/<label>.md`.

The run writes the job's report there on both routes (`tools/dispatch_result.py`), so a brief named that way
is overwritten by the digest and lost to every later read of the brief.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import sidecar_fanout  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f" :: {detail}"))
    if not ok:
        FAILED.append(name)


def main():
    out = Path(tempfile.mkdtemp(prefix="fanout_brief_"))
    other = Path(tempfile.mkdtemp(prefix="fanout_brief_other_"))
    jobs = [{"label": "vc-s4", "promptFile": str(out / "vc-s4.md")},
            {"label": "vc-s5", "promptFile": str(out / "vc-s5-brief.md")},
            {"label": "gf", "promptFile": str(other / "gf.md")}]
    message = sidecar_fanout.brief_collision(jobs, out)
    check("a brief at <out dir>/<label>.md is refused, naming the job and the path",
          bool(message) and "vc-s4" in message and "vc-s4.md" in message, message)
    check("a brief beside it under another name, and one in another folder, pass",
          message is not None and "vc-s5" not in message and "gf" not in message, message)
    check("no collision returns None", sidecar_fanout.brief_collision(jobs[1:], out) is None)
    os.rmdir(out)
    os.rmdir(other)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
