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
LAYER_FILES = (
    "CLAUDE.core.md",
    "CLAUDE.coding.md",
    "CLAUDE.godot.md",
    "CLAUDE.interactive.md",
)
SECTION = "§"
SUFFIXES = (".md", ".py", ".js", ".sh", ".ps1")
SKIP_PREFIXES = (".claude/scratch/", ".claude/plans/", ".claude/auto-memory/archive/", ".claude/tests/fixtures/")
SKIP_FILES = {".claude/worklog-titles.md", ".claude/tests/test_claudemd_citations.py"}
CITATION = re.compile(
    r"(CLAUDE(?:\.(?:core|coding|godot|interactive))?\.md)`?[ ,]*"
    r"(?:(Core Principles )?" + SECTION + r" ?|(?=\*[^*\n]+\*))"
)
TOKEN = re.compile(r"[^\s.,;:)(\[\]`\"'’/*]+")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail if detail else ""))


def heading_index(text):
    """(numbers, cores, exact_names) for a CLAUDE.md or layer body."""
    numbers, cores, exact_names = set(), set(), set()
    for line in text.splitlines():
        bold = re.match(r"^- \*\*([^*]+?)[.:]?\*\*(?:\s|$)", line)
        if bold:
            exact_names.add(bold.group(1).strip())
            continue
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
            clean = form.rstrip(".:").strip()
            if clean:
                exact_names.add(clean)
            words = [word.rstrip(":") for word in form.split()]
            if len(words) == 1:
                cores.add(words[0])
            for k in range(2, len(words) + 1):
                cores.add(" ".join(words[:k]))
    return numbers, cores, exact_names


def resolves(rest, numbers, cores, exact_names=None, numbered_principle=False, has_section=True):
    if numbered_principle:
        return False
    if not has_section:
        m_it = re.match(r"\*([^*\n]+)\*", rest)
        return bool(m_it and exact_names and m_it.group(1).strip() in exact_names)
    tokens = [re.sub(r"['’]s$", "", t) for t in TOKEN.findall(rest)[:6]]
    if not tokens:
        return False
    if tokens[0].isdigit():
        sec_ok = int(tokens[0]) in numbers
    else:
        sec_ok = any(" ".join(tokens[:k]) in cores for k in range(1, len(tokens) + 1))
    if not sec_ok:
        return False
    m_it = re.match(r"[^.,;()\n`]*?\*([^*\n]+)\*", rest)
    if m_it:
        return bool(exact_names and m_it.group(1).strip() in exact_names)
    return True


def citations(line):
    """(start, target_file, rest, numbered_principle, has_section) for non-example citations."""
    for match in CITATION.finditer(line):
        target_file = match.group(1)
        has_section = SECTION in line[match.start():match.end()]
        check_at = (
            (match.end() - 1 if line[match.end() - 1] == SECTION else line.rfind(SECTION, 0, match.end()))
            if has_section
            else match.end()
        )
        if line[:check_at].count("`") % 2 == 1:
            continue
        if match.start() > 0 and line[match.start() - 1] == '"':
            continue
        if line[:match.start()].rstrip("`").endswith("~/.claude/"):
            continue  # the global file lives outside the repo; its sections are not this file's
        if line[:match.start()].rstrip("`").lower().endswith(("global ", "user ")):
            continue  # "the global/user CLAUDE.md" names the user-level file outside the repo
        yield match.start(), target_file, line[match.end():match.end() + 120], bool(match.group(2)), has_section


def unresolved(repo_files, indices):
    rows = []
    for rel, text in repo_files:
        for lineno, line in enumerate(text.splitlines(), 1):
            for _, target_file, rest, numbered, has_section in citations(line):
                idx = indices.get(target_file)
                if idx is None or not resolves(rest, *idx, numbered_principle=numbered, has_section=has_section):
                    rows.append("%s:%d: %s" % (rel, lineno, line.strip()[:160]))
    return rows


def tracked_files(repo=REPO):
    """Tracked `.claude/` text files, read from the stamp's private index when harness_tests.py
    passes one as HARNESS_STAMP_INDEX, so a file staged only there is still scanned."""
    env = dict(os.environ)
    if os.environ.get("HARNESS_STAMP_INDEX"):
        env["GIT_INDEX_FILE"] = os.environ["HARNESS_STAMP_INDEX"]
    proc = subprocess.run(["git", "ls-files", ".claude"], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", env=env)
    if proc.returncode != 0:
        raise RuntimeError("git ls-files failed (%d): %s" % (proc.returncode, proc.stderr.strip()))
    out = proc.stdout.splitlines()
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
        subprocess.run(["git"] + list(args), cwd=root, check=True, capture_output=True,
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


def composed_chain(claude_md_path=CLAUDE_MD):
    """Project CLAUDE.md plus every existing `@` import in load order."""
    with open(claude_md_path, encoding="utf-8") as handle:
        text = handle.read()
    parts = [text]
    claude_dir = os.path.dirname(claude_md_path)
    for line in text.splitlines():
        m = re.match(r"^@([^\s]+\.md)\s*$", line.strip())
        if m:
            imp = os.path.join(claude_dir, m.group(1))
            if os.path.isfile(imp):
                with open(imp, encoding="utf-8") as ih:
                    parts.append(ih.read())
    return "\n".join(parts)


def load_indices(claude_md_path=CLAUDE_MD):
    """Heading and bold-lead indices for CLAUDE.md (the composed chain) and each layer file alone."""
    indices = {"CLAUDE.md": heading_index(composed_chain(claude_md_path))}
    claude_dir = os.path.dirname(claude_md_path)
    for lf in LAYER_FILES:
        path = os.path.join(claude_dir, lf)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                indices[lf] = heading_index(fh.read())
    return indices


def main():
    fixture = (
        "# Guide\n\n## Alpha Beta Gamma\n\n### 2. Delta (Epsilon)\n\n"
        "## Zeta\n\n## Philosophy: Eta Theta\n\n- **Bold Lead.** Body.\n- **Colon Lead:** Body.\n"
    )
    numbers, cores, exact_names = heading_index(fixture)
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
        ("the whole heading across its colon resolves", cite + "Philosophy: Eta Theta", True),
        ("a numbered Core Principles item does not resolve", "CLAUDE.md Core Principles " + SECTION + "4", False),
        ("a bold lead italic citation resolves whole", "`CLAUDE.core.md` *Bold Lead*", True),
        ("a bold lead with colon resolves whole", "`CLAUDE.core.md` *Colon Lead*", True),
        ("a prefix-only italic citation does not resolve", "`CLAUDE.core.md` *Alpha*", False),
    ]
    for label, line, expected in samples:
        found = list(citations(line))
        ok = bool(found) and resolves(
            found[0][2], numbers, cores, exact_names,
            numbered_principle=found[0][3], has_section=found[0][4],
        ) is expected
        check(label, ok, repr(line))

    # Planted E41 cases over a synthetic composed chain + layer files.
    planted_indices = {
        "CLAUDE.md": heading_index("## Core Heading\n\n## Coding Heading\n\n- **Goal.** Body.\n"),
        "CLAUDE.core.md": heading_index("## Core Principles\n\n- **Goal.** Body.\n"),
        "CLAUDE.coding.md": heading_index("## Coding Heading\n"),
        "CLAUDE.interactive.md": heading_index("## Answering and asking the owner\n"),
    }
    planted_files = [("p.md", "\n".join([
        "CLAUDE.core.md " + SECTION + "Self-Improvement Loop",
        "`CLAUDE.interactive.md` " + SECTION + "Missing Heading",
        "`CLAUDE.interactive.md` *Answering*",
        "CLAUDE.md " + SECTION + "Coding Heading",
        "`CLAUDE.core.md` §Core Principles *Goal*",
    ]))]
    planted_bad = unresolved(planted_files, planted_indices)
    check("E41 planted scan reports exactly the three broken citations",
          len(planted_bad) == 3
          and all("p.md:%d:" % n in row for n, row in zip((1, 2, 3), planted_bad)),
          "\n    " + "\n    ".join(planted_bad))

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

    indices = load_indices(CLAUDE_MD)
    rows = unresolved(list(tracked_files()), indices)
    check("every live CLAUDE.md and layer-file citation names an existing section or bold lead", not rows,
          "\n    " + "\n    ".join(rows[:40]))

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
