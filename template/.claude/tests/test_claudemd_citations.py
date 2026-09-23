#!/usr/bin/env python3
"""Proof that every harness citation of a CLAUDE.md section names a section that exists.

A CLAUDE.md cut that renames or removes a section leaves citations elsewhere pointing at nothing, and a
reader who follows one finds no rule. This scans tracked `.claude/` text files for a CLAUDE.md section
citation (the section sign after the file name, or a numbered Core Principles item). Scratch, plans,
archived memory, the worklog title mirror and JSON ledgers are history and are skipped. A citation whose
section sign sits inside a backtick span, or whose file name follows a double quote, is a documented
example and is skipped.

A citation resolves when it starts with a heading's section number, or with at least the first two words
of a heading (a one-word heading must match whole). Heading forms that also resolve: the text before
" (", before " — ", and after "<prefix>: ".

    python3 .claude/tests/test_claudemd_citations.py
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
CLAUDE_MD = os.path.join(REPO, ".claude", "CLAUDE.md")
CLAUDE_CORE_MD = os.path.join(REPO, ".claude", "CLAUDE.core.md")
SECTION = "§"
SUFFIXES = (".md", ".py", ".js", ".sh", ".ps1")
SKIP_PREFIXES = (".claude/scratch/", ".claude/plans/", ".claude/auto-memory/archive/")
SKIP_FILES = {".claude/worklog-titles.md", ".claude/CLAUDE.md", ".claude/CLAUDE.core.md"}
CITATION = re.compile(r"CLAUDE\.md`?[ ,]*(Core Principles )?" + SECTION + r" ?")
TOKEN = re.compile(r"[^\s.,;:)(\[\]`\"’/*]+")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail if detail else ""))


def heading_index(text):
    """(numbers, cores) for a CLAUDE.md body."""
    numbers, cores = set(), set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        name = line.lstrip("#").strip()
        numbered = re.match(r"(\d+)(?:[–-](\d+))?\.\s+(.*)", name)
        if numbered:
            low, high = int(numbered.group(1)), int(numbered.group(2) or numbered.group(1))
            numbers.update(range(low, high + 1))
            name = numbered.group(3)
        forms = {name, name.split(" (")[0], name.split(" — ")[0]}
        if ": " in name:
            forms.add(name.split(": ", 1)[1])
        for form in forms:
            words = form.split()
            if len(words) == 1:
                cores.add(words[0])
            for k in range(2, len(words) + 1):
                cores.add(" ".join(words[:k]))
    return numbers, cores


def resolves(rest, numbers, cores, numbered_principle=False):
    tokens = [re.sub(r"['’]s$", "", t) for t in TOKEN.findall(rest)[:6]]
    if numbered_principle or not tokens:
        return False
    if tokens[0].isdigit():
        return int(tokens[0]) in numbers
    return any(" ".join(tokens[:k]) in cores for k in range(1, len(tokens) + 1))


def citations(line):
    """(column, rest-of-line, numbered_principle) for each citation that is not a documented example."""
    for match in CITATION.finditer(line):
        section_at = match.end() - 1 if line[match.end() - 1] == SECTION else line.rfind(SECTION, 0, match.end())
        if line[:section_at].count("`") % 2 == 1:
            continue
        if match.start() > 0 and line[match.start() - 1] == '"':
            continue
        if line[:match.start()].rstrip("`").endswith("~/.claude/"):
            continue  # the global file lives outside the repo; its sections are not this file's
        if line[:match.start()].rstrip("`").lower().endswith("global "):
            continue  # "the global CLAUDE.md" names the same user-level file
        yield match.start(), line[match.end():match.end() + 80], bool(match.group(1))


def unresolved(repo_files, numbers, cores):
    rows = []
    for rel, text in repo_files:
        for lineno, line in enumerate(text.splitlines(), 1):
            for _, rest, numbered in citations(line):
                if not resolves(rest, numbers, cores, numbered):
                    rows.append("%s:%d: %s" % (rel, lineno, line.strip()[:160]))
    return rows


def tracked_files(repo=REPO):
    """Tracked `.claude/` text files, read from the stamp's private index when harness_tests.py
    passes one as HARNESS_STAMP_INDEX, so a file staged only there is still scanned."""
    env = dict(os.environ)
    if os.environ.get("HARNESS_STAMP_INDEX"):
        env["GIT_INDEX_FILE"] = os.environ["HARNESS_STAMP_INDEX"]
    out = subprocess.run(["git", "-C", repo, "ls-files", ".claude"], capture_output=True, text=True,
                         encoding="utf-8", env=env).stdout.splitlines()
    for rel in out:
        if not rel.endswith(SUFFIXES) or rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            yield rel, handle.read()


def private_index_fixture():
    """(seen-with-index, seen-without) for `.claude/new.md` staged only in a private index of a
    temp repo, as a private-index stamp would leave it."""
    import tempfile
    root = tempfile.mkdtemp(prefix="claudemd_cit_")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "HARNESS_STAMP_INDEX"}
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(root)

    def git(*args, extra=None):
        subprocess.run(["git", "-C", root] + list(args), check=True, capture_output=True,
                       env=dict(env, **(extra or {})))

    git("init", "-q")
    os.makedirs(os.path.join(root, ".claude"))
    for name in ("old.md", "new.md"):
        with open(os.path.join(root, ".claude", name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("x\n")
    git("add", ".claude/old.md")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    private = os.path.join(root, "private.index")
    git("read-tree", "HEAD", extra={"GIT_INDEX_FILE": private})
    git("add", ".claude/new.md", extra={"GIT_INDEX_FILE": private})
    saved = os.environ.get("HARNESS_STAMP_INDEX")
    try:
        os.environ["HARNESS_STAMP_INDEX"] = private
        seen = any(rel == ".claude/new.md" for rel, _ in tracked_files(root))
        os.environ.pop("HARNESS_STAMP_INDEX")
        blind = any(rel == ".claude/new.md" for rel, _ in tracked_files(root))
    finally:
        if saved is not None:
            os.environ["HARNESS_STAMP_INDEX"] = saved
    return seen, blind


def main():
    fixture ="# Guide\n\n## Alpha Beta Gamma\n\n### 2. Delta (Epsilon)\n\n## Zeta\n\n## Philosophy: Eta Theta\n"
    numbers, cores = heading_index(fixture)
    cite = "CLAUDE.md " + SECTION
    samples = [
        ("a two-word heading prefix resolves", cite + "Alpha Beta rules", True),
        ("a section number resolves", cite + "2 owns it", True),
        ("the text before a parenthesis resolves", cite + "Delta", True),
        ("a one-word heading resolves whole", cite + "Zeta.", True),
        ("the text after a prefix colon resolves", cite + "Eta Theta", True),
        ("a missing heading does not resolve", cite + "Omega Rules", False),
        ("a missing number does not resolve", cite + "7 carve-out", False),
        ("one word of a longer heading does not resolve", cite + "Alpha", False),
        ("a numbered Core Principles item does not resolve", "CLAUDE.md Core Principles " + SECTION + "4", False),
    ]
    for label, line, expected in samples:
        found = list(citations(line))
        ok = bool(found) and resolves(found[0][1], numbers, cores, found[0][2]) is expected
        check(label, ok, repr(line))
    check("a citation inside backticks is a documented example", not list(citations("`" + cite + "7` is stale")))
    check("a quoted citation is a documented example", not list(citations('cites "' + cite + '7" there')))
    check("a citation of the global ~/.claude/CLAUDE.md is not judged against the project file",
          not list(citations("as the global ~/.claude/" + cite + "Worker Model Delegation directs")))
    check("a citation of the global CLAUDE.md by name is not judged against the project file",
          not list(citations("the worker route in the global " + cite + "Tool routing")))
    check("a backticked file name with a bare section sign is still checked",
          len(list(citations("`CLAUDE.md` " + SECTION + "9"))) == 1)
    seen, blind = private_index_fixture()
    check("a .claude file staged only in the stamp's private index is scanned", seen and not blind,
          "seen=%s without-HARNESS_STAMP_INDEX=%s" % (seen, blind))

    with open(CLAUDE_MD, encoding="utf-8") as handle:
        combined = handle.read()
    if os.path.isfile(CLAUDE_CORE_MD):
        # The `@CLAUDE.core.md` import composes into one document at load time (memory import
        # resolution, code.claude.com/docs/en/memory) -- a citation of a core-owned heading is
        # correct once the split lands, so the index has to cover both files, not just the
        # project-owned one this test's own citer scan skips.
        with open(CLAUDE_CORE_MD, encoding="utf-8") as handle:
            combined += "\n" + handle.read()
    numbers, cores = heading_index(combined)
    rows = unresolved(list(tracked_files()), numbers, cores)
    check("every live CLAUDE.md section citation names an existing section", not rows,
          "\n    " + "\n    ".join(rows[:40]))

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
