"""Proof: the harness push guard admits a push to a branch under the project's `adaptation.json` `land_prefix`,
which the land runner proves before main moves, and still blocks an unproven harness commit pushed to main. With
no `land_prefix` the project has no land runner, so a branch of that name gets no exemption.

    python3 .claude/tests/test_harness_push_land.py
"""
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import git_guardrails as guard  # noqa: E402


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def main():
    failures = []

    def check(label, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + str(detail)[:300] if detail else ""))

    with tempfile.TemporaryDirectory(prefix="hpl_") as tmp:
        origin, repo = os.path.join(tmp, "origin.git"), os.path.join(tmp, "repo")
        git("init", "-q", "--bare", "-b", "main", origin, cwd=tmp)
        git("clone", "-q", origin, repo, cwd=tmp)
        os.makedirs(os.path.join(repo, ".claude", "hooks"))
        with open(os.path.join(repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("base\n")
        git("add", "README.md", cwd=repo)
        git("commit", "-q", "-m", "base", cwd=repo)
        git("push", "-q", "origin", "HEAD:main", cwd=repo)
        with open(os.path.join(repo, ".claude", "hooks", "x.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
        git("add", ".claude/hooks/x.py", cwd=repo)
        git("commit", "-q", "-m", "harness change", cwd=repo)
        stamp = os.path.join(tmp, "stamp.json")  # absent: no close has run
        def under(name, seed, *pushes):
            """The verdicts of `pushes` with the guard reading a planted adaptation.json, so no outcome depends on
            the checkout's own `land_prefix`."""
            claude = os.path.join(tmp, "claude_" + name)
            os.makedirs(os.path.join(claude, "skills", "project_subsystems"))
            with open(os.path.join(claude, "skills", "project_subsystems", "adaptation.json"), "w",
                      encoding="utf-8", newline="\n") as fh:
                json.dump(seed, fh)
            with patch.object(guard, "_ADAPTATION_DIR", claude, create=True):
                return tuple(guard.harness_push_verdict(push, repo) for push in pushes)

        with patch.dict(os.environ, {"HARNESS_TEST_STAMP": stamp}):
            to_main, to_land, to_land_full, to_other, unexpanded = under(
                "land", {"land_prefix": "land/"},
                ["origin", "HEAD:main"], ["origin", "HEAD:land/abcd1234"],
                ["origin", "HEAD:refs/heads/land/abcd1234"], ["origin", "HEAD:feature/x"],
                ["origin", "$c:refs/heads/main"])
            seeded = {
                "none": under("none", {}, ["origin", "HEAD:land/abcd1234"], ["origin", "HEAD:ship/abcd1234"]),
                "ship": under("ship", {"land_prefix": "ship/"},
                              ["origin", "HEAD:land/abcd1234"], ["origin", "HEAD:ship/abcd1234"]),
            }
    check("an unproven harness commit pushed to main is blocked", bool(to_main) and "BLOCKED harness push" in to_main,
          to_main)
    check("with a land runner, the main-push deny names the land push and no local close or --for run",
          bool(to_main) and "HEAD:land/<" in to_main and "--for" not in to_main and "--close" not in to_main,
          to_main)
    check("the land push the deny names carries the submodule check",
          bool(to_main) and "git push --recurse-submodules=check origin HEAD:land/<" in to_main, to_main)
    check("the same commit pushed to a land/ branch passes", to_land is None, to_land)
    check("a land/ branch named by its full ref passes", to_land_full is None, to_land_full)
    check("a non-land branch still needs a close", bool(to_other) and "BLOCKED harness push" in to_other, to_other)
    check("a push source the guard cannot resolve names it and says to push by literal hash",
          bool(unexpanded) and "BLOCKED harness push" in unexpanded and "`$c`" in unexpanded
          and "literal" in unexpanded and "does not expand" in unexpanded, unexpanded)
    land_none, ship_none = seeded.get("none", (None, None))
    land_ship, ship_ship = seeded.get("ship", (None, None))
    check("with no land_prefix, a land/ branch still needs a close",
          bool(land_none) and "BLOCKED harness push" in land_none and "--close" in land_none, land_none)
    check("the exemption follows the configured prefix", ship_ship is None, ship_ship)
    check("... and only that prefix", bool(land_ship) and "BLOCKED harness push" in land_ship, land_ship)

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
