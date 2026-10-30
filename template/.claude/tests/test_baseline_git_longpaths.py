#!/usr/bin/env python3
"""Proof: the baseline tools' git calls check out paths past Windows' 260-character limit.

`publish` and `author start` add a worktree of the baseline under the consumer's `.claude/.cache/`. From a
consumer that is itself a worktree, a deep baseline fixture path passes 260 characters, and git for Windows
refuses the checkout unless `core.longpaths` is set. Global and system git config are blanked here, so only
the tools' own git arguments decide. Off Windows the limit does not exist and the proof skips.

    python3 .claude/tests/test_baseline_git_longpaths.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import baseline_publish  # noqa: E402
import baseline_sync  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def make_source(root):
    src = root / "src"
    deep = src.joinpath(*(["a" * 40] * 4)) / ("f" * 60 + ".json")
    deep.parent.mkdir(parents=True)
    deep.write_text("{}\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(src), "-c", "core.longpaths=true", *args], check=True, capture_output=True)
    return src


def main():
    if os.name != "nt":
        print("skip: the 260-character path limit exists only on Windows")
        return 0
    root = Path(tempfile.mkdtemp(prefix="bl_longpaths_"))
    empty = root / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    os.environ["GIT_CONFIG_GLOBAL"] = str(empty)
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    src = make_source(root)
    deep_parent = root.joinpath(*(["w" * 20] * 3))
    for name, git in (("baseline_sync", baseline_sync._git), ("baseline_publish", baseline_publish._git)):
        target = deep_parent / name
        try:
            git(src, ["worktree", "add", "-q", "-b", "t-" + name, str(target), "HEAD"])
            ok, detail = True, ""
        except Exception as exc:  # noqa: BLE001 - the error text is the evidence
            ok, detail = False, str(exc)[:300]
        longest = max((len(str(p)) for p in target.rglob("*")), default=0)
        check(f"{name}._git adds a worktree whose paths pass 260 characters ({longest})", ok and longest > 260, detail)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
