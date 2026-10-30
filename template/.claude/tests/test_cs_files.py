#!/usr/bin/env python3
"""Proof for hooks/_cs_files.py: every .cs file outside a skipped directory is yielded, sorted,
and a skipped directory is never descended into (a filter-after walk visits every worktree under
.claude/)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _cs_files  # noqa: E402


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in ("Src/B.cs", "Src/Deep/A.cs", "Top.cs", "Src/notes.txt",
                    ".claude/worktrees/wt/Src/B.cs", "Tests/T.cs", "Src/obj/Gen.cs"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("", encoding="utf-8")
        walked = []
        real_walk = os.walk

        def recording_walk(top, *args, **kwargs):
            for entry in real_walk(top, *args, **kwargs):
                walked.append(Path(entry[0]).relative_to(root).as_posix())
                yield entry

        with patch.object(_cs_files.os, "walk", recording_walk):
            got = [(rel.as_posix()) for _, rel in _cs_files.cs_files(root, {".claude", "Tests", "obj"})]
        dot_walked = []

        def dot_walk(top, *args, **kwargs):
            for entry in real_walk(top, *args, **kwargs):
                dot_walked.append(Path(entry[0]).relative_to(root).as_posix())
                yield entry

        with patch.object(_cs_files.os, "walk", dot_walk):
            dot_got = [rel.as_posix() for _, rel in _cs_files.cs_files(root, {"obj"}, skip_dot=True)]
        dot_want = ["Src/B.cs", "Src/Deep/A.cs", "Tests/T.cs", "Top.cs"]
        dot_entered = [w for w in dot_walked if w != "." and w.split("/")[0].startswith(".")]
        ok = dot_got == dot_want and not dot_entered
        print("%-4s skip_dot yields every .cs outside dot and skipped dirs: %s" % ("ok" if ok else "FAIL", dot_got))
        if not ok:
            failures.append("skip_dot: %s descended into %s" % (dot_got, dot_entered))
    want = ["Src/B.cs", "Src/Deep/A.cs", "Top.cs"]
    ok = got == want
    print("%-4s yields every .cs outside skipped dirs, sorted: %s" % ("ok" if ok else "FAIL", got))
    if not ok:
        failures.append("yield: %s" % got)
    entered = [w for w in walked if w.split("/")[0] in (".claude", "Tests") or "/obj" in w]
    ok = not entered
    print("%-4s never descends into a skipped directory" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("descended: %s" % entered)
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
