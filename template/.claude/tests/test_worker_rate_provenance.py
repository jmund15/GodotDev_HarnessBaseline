"""`worker_rate show` flags a source the worker read with uncommitted edits.

An artifact's `git_sha` names a commit, but a dirty file's bytes at call time are not in
it: auditing at that sha checks a file the worker never saw. The server records each
source's blob id; `dirty_sources` compares it with the blob the commit holds. The
nested-worktree case pins the longest-root match: a worktree lives INSIDE the main
checkout, so the shorter root also matches the path and would check the wrong commit.

    python .claude/tests/test_worker_rate_provenance.py
"""

import pathlib
import subprocess
import sys
import tempfile

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import worker_rate as wr  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        FAILURES.append(msg)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


tmp = pathlib.Path(tempfile.mkdtemp(prefix="worker-rate-provenance-"))
main = tmp / "main"
main.mkdir()
git(main, "init", "-q", "-b", "main")
git(main, "config", "user.email", "t@t")
git(main, "config", "user.name", "t")
(main / "a.py").write_bytes(b"x = 40\n")
git(main, "add", "a.py")
git(main, "commit", "-q", "-m", "init")
wt = main / "worktrees" / "wt1"
git(main, "worktree", "add", "-q", "-b", "wt1", str(wt))
(wt / "a.py").write_bytes(b"x = 60\n")
git(wt, "commit", "-q", "-am", "wt")

wt_file = str(wt / "a.py")
shas = {str(main): git(main, "rev-parse", "HEAD"), str(wt): git(wt, "rev-parse", "HEAD")}

clean = {"git_shas": shas, "source_blobs": {wt_file: git(wt, "hash-object", "a.py")}}
check(wr.dirty_sources(clean) == [],
      "a clean worktree read matches its own commit (longest root wins over the main checkout)")

(wt / "a.py").write_bytes(b"x = 61\n")
dirty = {"git_shas": shas, "source_blobs": {wt_file: git(wt, "hash-object", "a.py")}}
out = wr.dirty_sources(dirty)
check(len(out) == 1 and wt_file in out[0], f"an uncommitted edit is flagged: {out}")

check(wr.dirty_sources({"git_shas": {}, "source_blobs": {wt_file: "0" * 40}}) == [],
      "a source outside every recorded repo is skipped, not flagged")

check(wr.dirty_sources({"git_sha": "abc"}) == [], "a pre-provenance artifact shows nothing")

# A ts printed to the second collides (16 ledger seconds hold two calls), so a prefix naming
# two rows is refused rather than resolved to whichever comes first.
import io, json, types  # noqa: E401,E402
wr.LEDGER = tmp / "calls.jsonl"
wr.RATINGS = tmp / "ratings.jsonl"
rows = [{"ts": "2026-01-01T00:00:01.100000+00:00", "tool": "read_files", "model": "ollama_chat/m",
         "paths_requested": 1, "prompt_tokens": 100, "prompt_chars": 400},
        {"ts": "2026-01-01T00:00:01.900000+00:00", "tool": "read_files", "model": "ollama_chat/m",
         "paths_requested": 1, "prompt_tokens": None, "prompt_chars": 48_000}]
rows += [{"ts": f"2026-01-01T00:01:0{i}.000000+00:00", "tool": "read_files", "model": "ollama_chat/m",
          "paths_requested": 1, "prompt_tokens": 1000, "prompt_chars": 4000} for i in range(5)]
wr.LEDGER.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
err, _real = io.StringIO(), sys.stderr
sys.stderr = err
try:
    rc = wr.cmd_rate(types.SimpleNamespace(outcome="unusable", anchor=None, derivation=None,
                                           note=None, ts="2026-01-01T00:00:01", tool=None))
finally:
    sys.stderr = _real
check(rc == 2 and "00:00:01.100000" in err.getvalue() and "00:00:01.900000" in err.getvalue(),
      f"an ambiguous ts prefix is refused and both matches listed: rc={rc} {err.getvalue()[:160]!r}")
check(not wr.RATINGS.exists(), "the refused rating writes nothing")
rc = wr.cmd_rate(types.SimpleNamespace(outcome="unusable", anchor=None, derivation=None,
                                       note=None, ts="2026-01-01T00:00:01.9", tool=None))
check(rc == 0, "a unique prefix still rates")

# A looped row has no prompt_tokens; breadth reads the server's window estimate instead.
check(wr.breadth_of({"paths_requested": 1, "prompt_tokens": None, "prompt_tokens_est": 12_000}) == "wide",
      "a looped row's 12,000-token estimate lands wide")
check(wr.breadth_of({"paths_requested": 1, "prompt_tokens": 4_000, "prompt_tokens_est": 12_000}) == "narrow",
      "a measured prompt_tokens outranks the estimate")
check(wr.breadth_of({"paths_requested": 1, "prompt_chars": 48_000}) == "narrow",
      "raw chars alone are never converted by a guessed ratio")

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED")
    sys.exit(1)
print("\nALL PASS")
