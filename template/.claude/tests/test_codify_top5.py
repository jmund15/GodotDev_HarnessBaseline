"""The one fix-history recipe: the top-5% query fenced in `commands/codify.md` Step 2, run as written on a planted repo.

`mechanism_litmus` and `/review` point at that query, so it is pinned here: a subject's `fix` counts in any case, and a
`redesign` commit restarts its file's count.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CODIFY = Path(__file__).resolve().parents[1] / "commands" / "codify.md"
BASH = shutil.which("bash")


def fenced_query():
    fences = re.findall(r"```bash\n(.*?)\n```", CODIFY.read_text(encoding="utf-8"), re.S)
    queries = [f for f in fences if "git log" in f and "git ls-files" in f]
    assert len(queries) == 1, "codify.md holds exactly one fix-history query"
    return queries[0]


def plant(root):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def git(*args):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)

    git("init", "-q")
    # oldest first. Case-sensitive counting gives `1 a`; no redesign reset gives `3 a`; the recipe gives `2 b`.
    for n, (subject, name) in enumerate([("fix: a", "a"), ("fix: a", "a"), ("Fix: b", "b"), ("redesign: a", "a"),
                                         ("fix: a", "a"), ("Fix: b", "b")]):
        (root / name).write_text(str(n))
        git("add", name)
        git("commit", "-q", "-m", subject)
    return env


@pytest.mark.skipif(BASH is None, reason="the recipe is a bash line")
def test_the_codify_query_counts_fix_in_any_case_and_restarts_at_redesign(tmp_path):
    env = plant(tmp_path)
    done = subprocess.run([BASH, "-c", fenced_query()], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["2", "b"]
