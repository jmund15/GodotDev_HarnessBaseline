"""Proof: the harness push guard needs no local close where the project declares a main check (`adaptation.json`
`main_check`: `main` is proved after every push), and `approving_gates`' proved-push contract still approves only the
commit a stored green close proved. Where none is declared, the guard still blocks an unproven harness commit pushed to
main, and a branch under `land_prefix` passes only with a land runner. A push whose source the guard cannot resolve is
denied either way.

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
import approving_gates  # noqa: E402
import git_guardrails as guard  # noqa: E402

MAIN_CHECK = ".github/workflows/main_check.yml"


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
            the checkout's own `land_prefix` or `main_check`."""
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
            declared = {
                "main_check": under("declared", {"land_prefix": "land/", "main_check": MAIN_CHECK},
                                    ["origin", "HEAD:main"], ["origin", "HEAD:feature/x"],
                                    ["origin", "HEAD:land/abcd1234"], ["origin", "$c:refs/heads/main"]),
                "no_land": under("declared_no_land", {"main_check": MAIN_CHECK}, ["origin", "HEAD:main"]),
                "wrong_type": under("wrong_type", {"land_prefix": "land/", "main_check": ["x"]},
                                    ["origin", "HEAD:main"]),
                "empty": under("empty", {"land_prefix": "land/", "main_check": ""}, ["origin", "HEAD:main"]),
                "undeclared": under("undeclared", {"land_prefix": "land/"}, ["origin", "HEAD:main"]),
            }
    check("where no main check is declared, an unproven harness commit pushed to main is blocked",
          bool(to_main) and "BLOCKED harness push" in to_main, to_main)
    check("with a land runner and no main check, the main-push deny names the land push and no local close or --for run",
          bool(to_main) and "HEAD:land/<" in to_main and "--for" not in to_main and "--close" not in to_main,
          to_main)
    check("the land push the deny names carries the submodule check",
          bool(to_main) and "git push --recurse-submodules=check origin HEAD:land/<" in to_main, to_main)
    check("the deny does not say the land runner runs the close",
          bool(to_main) and "runs the close" not in to_main, to_main)
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

    d_main, d_other, d_land, d_unexpanded = declared["main_check"]
    check("main_check declared: a non-land harness push to main with no close is allowed", d_main is None, d_main)
    check("main_check declared: a push to another branch with no close is allowed", d_other is None, d_other)
    check("main_check declared: a land/ branch push is still allowed", d_land is None, d_land)
    check("main_check declared: a push source the guard cannot resolve is still denied, naming it",
          bool(d_unexpanded) and "BLOCKED harness push" in d_unexpanded and "`$c`" in d_unexpanded, d_unexpanded)
    check("main_check declared with no land_prefix: a push to main with no close is allowed",
          declared["no_land"][0] is None, declared["no_land"][0])
    check("main_check of the wrong type is no declaration: the push still needs a close",
          bool(declared["wrong_type"][0]) and "BLOCKED harness push" in declared["wrong_type"][0],
          declared["wrong_type"][0])
    check("main_check an empty string is no declaration: the push still needs a close",
          bool(declared["empty"][0]) and "BLOCKED harness push" in declared["empty"][0], declared["empty"][0])
    check("the same unproven push is denied while main_check is not declared",
          bool(declared["undeclared"][0]) and "BLOCKED harness push" in declared["undeclared"][0],
          declared["undeclared"][0])

    # Where no close runs, the proved-push contract approves nothing, so the push takes the normal permission path,
    # which judges push authority.
    sha = "a" * 40
    with tempfile.TemporaryDirectory(prefix="hpl_stamp_") as stamps:
        no_close, green = os.path.join(stamps, "none.json"), os.path.join(stamps, "green.json")
        with open(green, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"close": {"head": sha, "verdict": "green", "red": []}}, fh)

        def approved(command, stamp_path):
            with patch.dict(os.environ, {"HARNESS_TEST_STAMP": stamp_path}):
                argv, _why = approving_gates.push_argv(command, repo)
                contract = approving_gates.owning_contract(argv, repo) if argv else None
                return bool(contract and contract.name == "proved-push" and contract.judge(argv, repo)[0])

        plain = "git push origin %s:main" % sha
        check("proved-push approves the commit a stored green close proved", approved(plain, green))
        check("proved-push approves nothing where no close ran, so a skipped proof is never authority to publish",
              not approved(plain, no_close))
        check("... nor a push of a commit other than the proved one",
              not approved("git push origin %s:main" % ("b" * 40), green))
        for label, command in (("after &&", "git status && " + plain), ("inside $(...)", "echo $(%s)" % plain),
                               ("inside backticks", "echo `%s`" % plain), ("behind an env prefix", "env " + plain),
                               ("behind a sudo prefix", "sudo " + plain)):
            check("a push hidden %s is not approved, even with a green close of its commit" % label,
                  not approved(command, green))

    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
