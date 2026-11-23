"""Print the code, test and prose totals of the harness tree at HEAD and their change since a base commit.

    python3 .claude/tools/ci_totals.py [BASE] [PREFIX]

Both sides are counted the same way, from the files git tracks at that commit, so the previous run's
totals need no stored state: the workflow passes the commit the previous run built (the push's `before`
or the PR's base). With no BASE, or one git cannot resolve, only the current totals print.
PREFIX is the tracked directory counted, default `.claude/` (a consumer repo's harness); the shared repo passes
`template/.claude/`. Nothing outside it is counted.
Code is source files, test is code under a `tests` directory or named `test_*`, prose is `.md`.
"""
import io
import subprocess
import sys
import tarfile
from pathlib import PurePosixPath

CODE = {".py", ".js", ".mjs", ".sh", ".ps1", ".cs", ".gd"}
KINDS = ("code", "test", "prose")
DEFAULT_PREFIX = ".claude/"


def kind(path):
    p = PurePosixPath(path)
    if p.suffix == ".md":
        return "prose"
    if p.suffix in CODE:
        return "test" if "tests" in p.parts or p.name.startswith("test_") else "code"
    return None


def totals(rev, prefix=DEFAULT_PREFIX):
    """{kind: (files, lines, bytes)} over the files tracked at `rev` under `prefix`."""
    archive = subprocess.run(["git", "archive", rev], capture_output=True, check=True).stdout
    out = {k: [0, 0, 0] for k in KINDS}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            k = member.isfile() and member.name.startswith(prefix) and kind(member.name)
            if k:
                data = tar.extractfile(member).read()
                row = out[k]
                row[0] += 1
                row[1] += data.count(b"\n")
                row[2] += len(data)
    return out


def main(argv):
    prefix = argv[1] if len(argv) > 1 else DEFAULT_PREFIX
    now = totals("HEAD", prefix)
    base = None
    if argv and not set(argv[0]) <= {"0"}:
        try:
            base = totals(argv[0], prefix)
        except subprocess.CalledProcessError:
            print(f"base {argv[0][:12]} not found; no change shown")
    print(f"{'':6}{'files':>7}{'lines':>9}{'bytes':>10}" + ("   change since " + argv[0][:12] if base else ""))
    for k in KINDS:
        line = f"{k:6}{now[k][0]:>7,}{now[k][1]:>9,}{now[k][2]:>10,}"
        if base:
            line += "   " + " ".join(f"{a - b:+,}" for a, b in zip(now[k], base[k]))
            line += "  (files lines bytes)"
        print(line)


if __name__ == "__main__":
    main(sys.argv[1:])
