"""Proof: the push-range reader (`hooks/_push_range.py`) names git's empty tree without writing it, passes its git
environment to every query and finds a bare push's destination in git's own order; and `approving_gates`' proved-push
contract approves only the commit a stored green close proved, never a push hidden in a compound or behind a prefix.

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
import _git_run  # noqa: E402
import _push_range as push_range  # noqa: E402
import approving_gates  # noqa: E402

SHA1_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SHA256_EMPTY_TREE = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def git_out(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def loose_objects(root):
    out = subprocess.run(["git", "-C", root, "count-objects", "-v"], capture_output=True, text=True,
                         check=True).stdout
    return int(next(line.split(":")[1] for line in out.splitlines() if line.startswith("count:")))


def hash_empty_tree(root):
    return subprocess.run(["git", "-C", root, "hash-object", "-t", "tree", "--stdin"], input=b"",
                          capture_output=True, check=True).stdout.decode().strip()


def main():
    failures = []

    def check(label, ok, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + str(detail)[:300] if detail else ""))

    # A no-base preflight must not write an object into the repository: the empty tree it falls back to is git's
    # built-in one, so the preflight only has to name it.
    with tempfile.TemporaryDirectory(prefix="hpl_empty_") as empty_tmp:
        empty_repo = os.path.join(empty_tmp, "repo")
        git("init", "-q", "-b", "main", empty_repo, cwd=empty_tmp)
        with open(os.path.join(empty_repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("base\n")
        git("add", "README.md", cwd=empty_repo)
        git("commit", "-q", "-m", "base", cwd=empty_repo)
        before = loose_objects(empty_repo)
        empty_base = push_range.close_base(empty_repo)
        after = loose_objects(empty_repo)
    check("a no-base close preflight returns git's empty tree without writing an object",
          empty_base == SHA1_EMPTY_TREE and before == after, (empty_base, before, after))

    # The constant names git's own empty tree in each object format, and the unwritten SHA-256 id resolves.
    with tempfile.TemporaryDirectory(prefix="hpl_tree_") as tree_tmp:
        sha1_repo = os.path.join(tree_tmp, "sha1")
        git("init", "-q", "-b", "main", sha1_repo, cwd=tree_tmp)
        sha1_value = hash_empty_tree(sha1_repo)
        sha256_repo = os.path.join(tree_tmp, "sha256")
        made = subprocess.run(["git", "init", "-q", "--object-format=sha256", "-b", "main", sha256_repo],
                              cwd=tree_tmp, capture_output=True)
        sha256_value, sha256_kind, sha256_paths = None, None, None
        if made.returncode == 0:
            sha256_value = hash_empty_tree(sha256_repo)
            with open(os.path.join(sha256_repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("base\n")
            git("add", "README.md", cwd=sha256_repo)
            git("commit", "-q", "-m", "base", cwd=sha256_repo)
            sha256_kind = subprocess.run(["git", "-C", sha256_repo, "cat-file", "-t", SHA256_EMPTY_TREE],
                                         capture_output=True, text=True).stdout.strip()
            sha256_paths = subprocess.run(["git", "-C", sha256_repo, "diff-tree", "--no-commit-id", "--name-only",
                                           "-r", SHA256_EMPTY_TREE, "HEAD"], capture_output=True,
                                          text=True).stdout.splitlines()
    check("EMPTY_TREE names git's empty tree in a sha1 repository",
          push_range.EMPTY_TREE["sha1"] == sha1_value, sha1_value)
    if sha256_value is None:
        print("SKIP the sha256 empty-tree cases: this git cannot create a --object-format=sha256 repository")
    else:
        check("EMPTY_TREE names git's empty tree in a sha256 repository",
              push_range.EMPTY_TREE["sha256"] == sha256_value, sha256_value)
        check("the unwritten sha256 empty tree answers `tree`", sha256_kind == "tree", sha256_kind)
        check("diff-tree against the unwritten sha256 empty tree lists the committed path",
              sha256_paths == ["README.md"], sha256_paths)

    # A probe that predates SHA-256 support reads as sha1; only a printed name outside the table is an unresolved base.
    def probe(returncode, stdout):
        real_run = _git_run.run

        def fake(args, **kwargs):
            if list(args) == ["rev-parse", "--show-object-format"]:
                return _git_run.GitResult(returncode, stdout, b"")
            return real_run(args, **kwargs)
        with patch.object(_git_run, "run", fake):
            return push_range._object_format(".")
    check("a format probe that exits nonzero reads as sha1", probe(1, b"") == "sha1")
    check("a format probe that prints nothing reads as sha1", probe(0, b"\n") == "sha1")
    check("a format probe that prints a known format keeps it", probe(0, b"sha256\n") == "sha256")
    with tempfile.TemporaryDirectory(prefix="hpl_fmt_") as fmt_tmp:
        fmt_repo = os.path.join(fmt_tmp, "repo")
        git("init", "-q", "-b", "main", fmt_repo, cwd=fmt_tmp)
        with open(os.path.join(fmt_repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("base\n")
        git("add", "README.md", cwd=fmt_repo)
        git("commit", "-q", "-m", "base", cwd=fmt_repo)
        real_run = _git_run.run

        def unknown_format(args, **kwargs):
            if list(args) == ["rev-parse", "--show-object-format"]:
                return _git_run.GitResult(0, b"sha512\n", b"")
            return real_run(args, **kwargs)
        with patch.object(_git_run, "run", unknown_format):
            try:
                push_range.close_base(fmt_repo)
                unresolved = None
            except RuntimeError as exc:
                unresolved = str(exc)
    check("a printed object format outside {sha1, sha256} is an unresolved base",
          bool(unresolved) and "sha512" in unresolved, unresolved)

    # The range readers pass the caller's git_env to every query they make.
    with tempfile.TemporaryDirectory(prefix="hpl_env_") as env_tmp:
        env_repo = os.path.join(env_tmp, "repo")
        git("init", "-q", "-b", "main", env_repo, cwd=env_tmp)
        with open(os.path.join(env_repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("base\n")
        git("add", "README.md", cwd=env_repo)
        git("commit", "-q", "-m", "base", cwd=env_repo)
        env_base = git_out("rev-parse", "HEAD", cwd=env_repo)
        git("update-ref", "refs/remotes/origin/main", env_base, cwd=env_repo)
        os.makedirs(os.path.join(env_repo, ".claude", "hooks"))
        with open(os.path.join(env_repo, ".claude", "hooks", "x.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("X = 1\n")
        git("add", ".claude/hooks/x.py", cwd=env_repo)
        git("commit", "-q", "-m", "harness change", cwd=env_repo)
        overlay = {"GIT_TEST_OVERLAY": "1"}
        seen = []
        real_run = _git_run.run

        def record(args, **kwargs):
            seen.append(kwargs.get("env"))
            return real_run(args, **kwargs)
        with patch.object(_git_run, "run", record):
            env_paths = push_range.close_paths(env_repo, env_base, "HEAD", "origin", overlay)
            path_envs = list(seen)
            seen.clear()
            env_covers = push_range.default_base_covers(env_repo, env_base, "HEAD", "origin", overlay)
            cover_envs = list(seen)
    check("close_paths walks the default branch and passes its git_env to every query",
          env_paths == [".claude/hooks/x.py"] and bool(path_envs) and all(e == overlay for e in path_envs),
          (env_paths, path_envs))
    check("default_base_covers passes its git_env to every query",
          env_covers and bool(cover_envs) and all(e == overlay for e in cover_envs), (env_covers, cover_envs))

    # A bare push's remote comes from git's own order of config lookups.
    with tempfile.TemporaryDirectory(prefix="hpl_dest_") as dest_tmp:
        dest_repo = os.path.join(dest_tmp, "repo")
        git("init", "-q", "-b", "feature", dest_repo, cwd=dest_tmp)
        with open(os.path.join(dest_repo, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("base\n")
        git("add", "README.md", cwd=dest_repo)
        git("commit", "-q", "-m", "base", cwd=dest_repo)
        git("remote", "add", "origin", "https://example.invalid/o.git", cwd=dest_repo)
        by_origin = push_range.push_destination(dest_repo)
        git("config", "remote.pushDefault", "backup", cwd=dest_repo)
        by_default = push_range.push_destination(dest_repo)
        git("config", "branch.feature.pushRemote", "upstream", cwd=dest_repo)
        by_branch = push_range.push_destination(dest_repo)
    check("push_destination defaults to origin and the current branch", by_origin == ("origin", "feature"), by_origin)
    check("remote.pushDefault is read when the branch names no pushRemote", by_default == ("backup", "feature"),
          by_default)
    check("the branch's pushRemote wins over remote.pushDefault", by_branch == ("upstream", "feature"), by_branch)

    # Where no close runs, the proved-push contract approves nothing, so the push takes the normal permission path,
    # which judges push authority.
    sha = "a" * 40
    with tempfile.TemporaryDirectory(prefix="hpl_stamp_") as stamps:
        no_close, green = os.path.join(stamps, "none.json"), os.path.join(stamps, "green.json")
        repo = os.path.join(stamps, "repo")  # the contract reads the stamp; no repository is planted
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
