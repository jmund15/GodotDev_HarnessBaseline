#!/usr/bin/env python3
"""Proof that CLAUDE.core.md stands alone: the shared core never depends on a project's CLAUDE.md.

The core is published to every project; CLAUDE.md is project-owned and imports it. Each section
lives whole in one of the two files, so:

- no heading appears in both files;
- every `§Name` the core cites without a file path names a core heading;
- the core never names the project file's own sections ("Project Guidelines");
- every `.claude/` file, skill and command the core cites exists in this tree.

Planted cases prove each check fails on a violation.

    python3 .claude/tests/test_claude_core_standalone.py
"""
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
PROJECT_SECTIONS = ("Project Guidelines",)
NOT_FILES = {"offset", "limit", "write_doc"}  # a Read parameter pair and an MCP tool, not skills
BOOTSTRAP_STATE = {"baseline.lock.json"}  # written at bootstrap, absent from the template tree
PATH_TOKEN = re.compile(r"^[\w.-]+(/[\w.<>-]+)*/?$")
FILE_SUFFIXES = (".md", ".py", ".json", ".sh", ".ps1", ".js")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail if detail else ""))


def headings(text):
    names = set()
    for line in text.splitlines():
        if line.startswith("#"):
            names.add(re.sub(r"^\d+(?:[–-]\d+)?\.\s+", "", line.lstrip("#").strip()))
    return names


def section_cites(text):
    """`§Name` citations not anchored to a backticked file path."""
    for match in re.finditer("§ ?([A-Z][^:;,.|)`]*)", text):
        before = text[:match.start()].rstrip()
        if before.endswith("`"):
            continue
        yield match.group(1).strip()


def resolves(cite, names):
    """A heading resolves by prefix in either direction, whole or after a `<prefix>: ` label."""
    forms = set(names) | {h.split(": ", 1)[1] for h in names if ": " in h}
    return any(cite == h or h.startswith(cite) or cite.startswith(h) for h in forms)


def _exists(root, *relpaths):
    return any(os.path.exists(os.path.join(root, rel)) for rel in relpaths)


def cited_paths(text):
    """(token, candidate relpaths) for each backticked file, skill or command the text cites."""
    for token in sorted(set(re.findall(r"`([^`\s]+)`", text))):
        if any(mark in token for mark in "<*~$=(") or token.startswith(("/tmp", ".claude/cache/")) or token in BOOTSTRAP_STATE:
            continue
        if token.startswith("/") and re.fullmatch(r"/[a-z][a-z0-9_]*", token):
            name = token[1:]
            yield token, (f".claude/commands/{name}.md", f".claude/skills/{name}/SKILL.md")
        elif re.fullmatch(r"[a-z][a-z0-9_]*", token):
            if token not in NOT_FILES:
                yield token, (f".claude/skills/{token}/SKILL.md", f".claude/commands/{token}.md")
        elif PATH_TOKEN.match(token) and ("/" in token or token.endswith(FILE_SUFFIXES)) and (
                token.endswith(FILE_SUFFIXES) or token.endswith("/")):
            rel = token[len(".claude/"):] if token.startswith(".claude/") else token
            yield token, (f".claude/{rel}", f".claude/skills/{rel}", f".claude/auto-memory/{rel}")


def problems(core, overlay, root):
    found = []
    core_names = headings(core)
    for name in sorted(core_names & headings(overlay)):
        found.append(f"heading in both files: {name}")
    for cite in section_cites(core):
        if not resolves(cite, core_names):
            found.append(f"core cites a section it does not hold: §{cite}")
    for phrase in PROJECT_SECTIONS:
        if phrase in core:
            found.append(f"core names a project section: {phrase}")
    for token, candidates in cited_paths(core):
        if not _exists(root, *candidates):
            found.append(f"core cites a missing file: {token}")
    return found


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
        return handle.read()


def planted(core, overlay, files=()):
    with tempfile.TemporaryDirectory() as root:
        for rel in files:
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        return problems(core, overlay, root)


def main():
    clean_core = "## Alpha Rules\nSee §Alpha Rules and `testing`.\n"
    clean_files = (".claude/skills/testing/SKILL.md",)
    check("a clean planted pair passes", planted(clean_core, "## Beta\n", clean_files) == [])
    check("a heading in both files fails",
          any("heading in both" in p for p in planted(clean_core, "## Alpha Rules\n", clean_files)))
    check("a core cite of an overlay-only section fails",
          any("does not hold" in p for p in planted(clean_core + "Per §Beta Notes.\n", "## Beta Notes\n", clean_files)))
    check("a cite anchored to a backticked file is not judged",
          planted(clean_core + "See `rules/x.md` §Other.\n", "", clean_files + (".claude/rules/x.md",)) == [])
    check("a core naming Project Guidelines fails",
          any("project section" in p for p in planted(clean_core + "Project Guidelines names it.\n", "", clean_files)))
    check("a missing cited skill fails",
          any("missing file: change_control" in p for p in planted(clean_core + "`change_control` owns it.\n", "", clean_files)))
    check("a missing cited path fails",
          any("missing file" in p for p in planted(clean_core + "`reference/project_stack.md`\n", "", clean_files)))
    check("a missing slash command fails",
          any("missing file: /nowhere" in p for p in planted(clean_core + "Run `/nowhere`.\n", "", clean_files)))

    live = problems(read(".claude/CLAUDE.core.md"), read(".claude/CLAUDE.md"), REPO)
    check("the live core stands alone", live == [], "; ".join(live))

    print()
    print("all ok" if not FAILURES else "%d failure(s)" % len(FAILURES))
    for failure in FAILURES:
        print("  " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
