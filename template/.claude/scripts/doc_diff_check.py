#!/usr/bin/env python3
"""Assert that a change touched ONLY comments in .cs files.

Comment-only remediation is delegated work, and the one thing a delegate must not do
while editing comments is edit code. A line-shape heuristic ("every changed line starts
with ///") gets this wrong in both directions: it rejects a trailing-comment edit on a
code line, and it accepts a `//`-looking line inside a string literal.

So this compares the CODE, not the lines. Both revisions are stripped of comments and
whitespace-normalized; if the results differ, production code changed. That is the claim
worth asserting, stated directly.

Usage:
    doc_diff_check.py [<base-rev>] [--paths <glob> ...]

    <base-rev>  defaults to HEAD. Compares the working tree against it.

Exit 0 = comment-only. Exit 1 = production code changed (offending files listed).
Exit 2 = the check could not run (bad rev, git failure).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def git(*args: str, cwd: Path = REPO) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments, respecting string and char literals.

    Handles the four C# literal forms that can contain comment-looking text:
    "..." (with backslash escapes), @"..." (verbatim, "" escapes), '...', and the
    interpolated $"..." / $@"..." variants, which for this purpose behave like their
    non-interpolated bases.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                i += 1
            i += 2
            continue

        # Verbatim string: @"..." or $@"..." or @$"..."
        if c in "@$" and _verbatim_at(src, i):
            start = i
            while src[i] != '"':
                i += 1
            i += 1
            while i < n:
                if src[i] == '"':
                    if i + 1 < n and src[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(src[start:i])
            continue

        if c in "\"'":
            start = i
            quote = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                if src[i] == "\n":  # unterminated; bail rather than run away
                    break
                i += 1
            out.append(src[start:i])
            continue

        out.append(c)
        i += 1

    return "".join(out)


def _verbatim_at(src: str, i: int) -> bool:
    """True when src[i] begins a verbatim string prefix (@" / $@" / @$")."""
    j = i
    seen = set()
    while j < len(src) and src[j] in "@$" and src[j] not in seen:
        seen.add(src[j])
        j += 1
    return j < len(src) and src[j] == '"' and "@" in seen


def code_of(src: str) -> str:
    return " ".join(strip_comments(src).split())


def check_tree(root: Path, base: str, label: str) -> tuple[int, list[tuple[str, str, str]]]:
    """Check one git working tree. Returns (files_checked, offenders)."""
    changed = [
        p for p in git("diff", "--name-only", base, "--", "*.cs", cwd=root).splitlines() if p.strip()
    ]
    offenders: list[tuple[str, str, str]] = []
    for rel in changed:
        name = f"{label}{rel}"
        f = root / rel
        if not f.exists():
            offenders.append((name, "<deleted>", "file deleted — not a comment-only change"))
            continue
        try:
            old = git("show", f"{base}:{rel}", cwd=root)
        except RuntimeError:
            offenders.append((name, "<new file>", "file added — not a comment-only change"))
            continue

        new = f.read_text(encoding="utf-8", errors="replace")
        a, b = code_of(old), code_of(new)
        if a != b:
            offenders.append((name, *_first_divergence(a, b)))
    return len(changed), offenders


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = args[0] if args else "HEAD"

    # The Jmodot submodule is its own git tree: a `git diff` in the parent never reports
    # changes to files inside it. 41 of the 91 files this check exists for live there, so
    # missing this would make the check silently pass on half its subject.
    trees = [(REPO, "")]
    sub = REPO / "Jmodot"
    if (sub / ".git").exists():
        trees.append((sub, "Jmodot/"))

    total = 0
    offenders: list[tuple[str, str, str]] = []
    for root, label in trees:
        try:
            n, offs = check_tree(root, base, label)
        except RuntimeError as e:
            print(f"doc_diff_check: {e}", file=sys.stderr)
            return 2
        total += n
        offenders.extend(offs)

    if not total:
        print(f"doc_diff_check: no .cs changes against {base}.")
        return 0

    if not offenders:
        print(f"doc_diff_check: {total} .cs file(s) changed, comments only. OK.")
        return 0

    print(f"doc_diff_check: PRODUCTION CODE CHANGED in {len(offenders)} of {total} file(s).")
    for rel, was, now in offenders:
        print(f"\n  {rel}\n    was: {was}\n    now: {now}")
    return 1


def _first_divergence(a: str, b: str, window: int = 90) -> tuple[str, str]:
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    start = max(0, k - 20)
    return a[start : start + window], b[start : start + window]


if __name__ == "__main__":
    sys.exit(main())
