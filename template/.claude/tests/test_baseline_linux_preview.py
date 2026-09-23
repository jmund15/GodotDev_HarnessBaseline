#!/usr/bin/env python3
"""Proof for tools/baseline_linux_preview.py: the CI workflow parser, the WSL script it builds and
the worktree resolver. WSL itself is not started; the script text is asserted instead.

    python3 .claude/tests/test_baseline_linux_preview.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import baseline_linux_preview as blp  # noqa: E402

WORKFLOW = """name: baseline

on:
  pull_request:

jobs:
  baseline:
    name: baseline
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      # a comment between steps
      - name: verify pwsh is on PATH
        run: pwsh -NoProfile -Command 'exit 0'

      - name: baseline-repo-root proofs
        run: |
          set -e
          for f in tests/test_*.py; do
            python3 "$f"
          done

      - name: template harness battery
        run: python3 template/.claude/scripts/harness_tests.py --allow-cannot-run tests/platform_only.json
"""


def main():
    cases = []

    steps = blp.ci_steps(WORKFLOW)
    cases.append(("uses: steps are skipped; every run: step is kept, in order",
                  [n for n, _ in steps] == ["verify pwsh is on PATH", "baseline-repo-root proofs",
                                            "template harness battery"]))
    cases.append(("a one-line run keeps its command",
                  steps[0][1] == "pwsh -NoProfile -Command 'exit 0'"))
    cases.append(("a block run is dedented and keeps its lines",
                  steps[1][1] == 'set -e\nfor f in tests/test_*.py; do\n  python3 "$f"\ndone'))
    cases.append(("a workflow with no run: step parses to []", blp.ci_steps("jobs: {}\n") == []))

    cases.append(("a drive path maps under /mnt",
                  blp.wsl_path(r"C:\Users\x\a b") == "/mnt/c/Users/x/a b"
                  and blp.wsl_path("D:/w/t") == "/mnt/d/w/t"))

    script = blp.preview_script("/mnt/c/w/tree", steps)
    cases.append(("the snapshot lives under $HOME, never /tmp (delete guards admit temp paths)",
                  '"$HOME/baseline-linux-preview"' in script and "/tmp" not in script))
    cases.append(("ci-tools node and pwsh lead PATH",
                  'PATH="$HOME/.local/ci-tools/node/bin:$HOME/.local/ci-tools/pwsh:$PATH"' in script))
    cases.append(("the snapshot excludes .git and is committed to a fresh repo, as checkout gives",
                  "--exclude=.git" in script and "git init -q -b main" in script
                  and "commit -qm snapshot" in script))
    cases.append(("each step runs under the Actions default shell flags",
                  script.count("bash --noprofile --norc -eo pipefail") == len(steps)))
    cases.append(("every step reports pass or fail by name",
                  all("::step %s" % n in script for n, _ in steps)))
    cases.append(("a step body with quotes survives as a heredoc, not an argument",
                  "<<'PREVIEW_STEP_EOF'" in script and "pwsh -NoProfile -Command 'exit 0'" in script))
    with_origin = blp.preview_script("/mnt/c/w/tree", steps, "https://example.invalid/b.git")
    cases.append(("the snapshot carries the worktree's origin, as checkout does (test_bootstrap reads it)",
                  "git remote add origin 'https://example.invalid/b.git'" in with_origin
                  and with_origin.index("git remote add origin") < with_origin.index("::step")))
    cases.append(("no origin adds no remote", "git remote add" not in script))

    cache = Path(tempfile.mkdtemp(prefix="blp_cache_"))
    older = cache / "20260101T000000Z-aaaaaaaa-worktree"
    newer = cache / "20260202T000000Z-bbbbbbbb-worktree"
    for d in (older, newer):
        (d / ".github" / "workflows").mkdir(parents=True)
    cases.append(("no journal id picks the newest worktree", blp.resolve_worktree(cache, None) == newer))
    cases.append(("a journal id picks its worktree",
                  blp.resolve_worktree(cache, "20260101T000000Z-aaaaaaaa") == older))
    cases.append(("an unknown journal id resolves to None",
                  blp.resolve_worktree(cache, "20990101T000000Z-cccccccc") is None))
    cases.append(("an empty cache resolves to None",
                  blp.resolve_worktree(Path(tempfile.mkdtemp(prefix="blp_empty_")), None) is None))

    quoted = blp.preview_script("/mnt/c/w/tree", [("say \"hi\" 'now'", "true")])
    cases.append(("one sanitized label names a step on its ::step, ::pass and ::fail lines",
                  "::step say hi now'" in quoted and "::pass say hi now'" in quoted
                  and '::fail say hi now rc=' in quoted))

    real_cache = blp.CACHE
    stepless = Path(tempfile.mkdtemp(prefix="blp_nosteps_"))
    wt = stepless / "20260303T000000Z-dddddddd-worktree"
    (wt / ".github" / "workflows").mkdir(parents=True)
    (wt / ".github" / "workflows" / "baseline.yml").write_text("jobs: {}\n", encoding="utf-8")
    blp.CACHE = stepless
    try:
        rc = blp.main(["20260303T000000Z-dddddddd"])
    finally:
        blp.CACHE = real_cache
    cases.append(("a workflow with no run: step exits 3, never a green preview of nothing", rc == 3))

    cases.append(("a pwsh hash missing from hashes.sha256 stops setup instead of skipping the check",
                  '[ -n "$want" ] &&' not in blp.SETUP_SCRIPT
                  and '[ -n "$want" ] || { echo "no sha256 for $p" >&2; exit 1; }' in blp.SETUP_SCRIPT))
    cases.append(("setup verifies both downloads before unpacking",
                  "sha256sum -c" in blp.SETUP_SCRIPT and blp.SETUP_SCRIPT.count("sha256sum") >= 2
                  and "iconv -f UTF-16" in blp.SETUP_SCRIPT))

    failed = 0
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        failed += not ok
    print("\n%d/%d cases pass" % (len(cases) - failed, len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
