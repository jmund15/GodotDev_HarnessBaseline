#!/usr/bin/env python3
"""Proof for hooks/generate_family_manifest.py production_files(): every production .cs file,
walked with skipped directories pruned (never entered), so the manifest scan does not visit every
checkout under .claude/worktrees/."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _cs_files  # noqa: E402
import generate_family_manifest as gfm  # noqa: E402


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in ("Combat/Hit.cs", "Top.cs", "Tests/HitTests.cs", "addons/Plugin.cs",
                    ".claude/worktrees/wt/Combat/Hit.cs", "Combat/obj/Gen.cs"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("", encoding="utf-8")
        walked = []
        real_walk = os.walk

        def recording_walk(top, *args, **kwargs):
            for entry in real_walk(top, *args, **kwargs):
                walked.append(Path(entry[0]).relative_to(root).as_posix())
                yield entry

        with patch.object(gfm, "REPO", root), patch.object(_cs_files.os, "walk", recording_walk):
            got = [rel for _, rel in gfm.production_files()]
    ok = got == ["Combat/Hit.cs", "Top.cs"]
    print("%-4s yields production .cs only, sorted: %s" % ("ok" if ok else "FAIL", got))
    if not ok:
        failures.append("yield: %s" % got)
    entered = [w for w in walked if w.split("/")[0] in (".claude", "Tests", "addons") or w.endswith("/obj")]
    ok = bool(walked) and not entered
    print("%-4s walks with skipped directories pruned (walked %d dirs)" % ("ok" if ok else "FAIL", len(walked)))
    if not ok:
        failures.append("walk: walked=%s entered=%s" % (walked, entered))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Combat").mkdir()
        (root / "Combat/IHit.cs").write_text("public interface IHit\n{\n}\n", encoding="utf-8")
        (root / "Combat/Hit.cs").write_text("public class Hit : IHit\n{\n}\n", encoding="utf-8")
        out = root / ".claude/generated/abstraction_families.md"
        with patch.object(gfm, "REPO", root), patch.object(gfm, "OUT_PATH", out), \
                patch.object(gfm, "DOUBLES_ROOT", root / "Tests" / "Framework"), \
                patch.object(sys, "argv", ["generate_family_manifest.py"]):
            gfm.main()
        data = out.read_bytes() if out.exists() else b""
    ok = b"\n" in data and b"\r\n" not in data
    print("%-4s writes the manifest with LF line endings" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("line endings: %r" % data[:120])
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
