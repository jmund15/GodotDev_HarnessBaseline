#!/usr/bin/env python3
"""Proof for hooks/tool_cascade_audit.py iter_cs_files(): every .cs file except `*.generated.cs`,
walked through hooks/_cs_files.py, the one pruned .cs walker, with skipped directories never
entered."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(os.environ.get("TCA_HOOKS_DIR") or Path(__file__).resolve().parents[1] / "hooks")
sys.path.insert(0, str(HOOKS))
import _cs_files  # noqa: E402
import tool_cascade_audit as tca  # noqa: E402


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in ("Combat/Hit.cs", "Combat/Hit.generated.cs", "Tests/HitTests.cs",
                    ".claude/worktrees/wt/Combat/Hit.cs", "Combat/obj/Gen.cs"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("", encoding="utf-8")
        calls = []
        real = _cs_files.cs_files

        def recording(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with patch.object(tca, "cs_files", recording, create=True):
            got = sorted(Path(p).relative_to(root).as_posix() for p in tca.iter_cs_files(str(root)))
    ok = got == ["Combat/Hit.cs", "Tests/HitTests.cs"]
    print("%-4s yields .cs outside skipped dirs, no .generated.cs: %s" % ("ok" if ok else "FAIL", got))
    if not ok:
        failures.append("yield: %s" % got)
    ok = bool(calls)
    print("%-4s walks through the shared _cs_files walker" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("iter_cs_files keeps its own walker")
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
