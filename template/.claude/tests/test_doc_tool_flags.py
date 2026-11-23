#!/usr/bin/env python3
"""Proof that every `python3 .claude/tools/<name>.py ... --flag` example in the command, skill and reference
docs names only `--long` flags its tool can parse.

A flag counts as parsed when it occurs as a literal string in the tool's source or in a module the tool imports
from `.claude/tools/` or `.claude/hooks/` (transitively). Sources are read as text: no `--help` spawn, so the
proof stays one fast process. Placeholders (`<...>`) are dropped, and an example ends at a pipe, `;`, `&&`, a
newline or its closing backtick. A tool file absent on disk is reported as its own failure.

    python -m pytest .claude/tests/test_doc_tool_flags.py -q
"""
import re
from pathlib import Path

import pytest

CLAUDE = Path(__file__).resolve().parents[1]
DOC_TREES = ("commands", "skills", "reference")
CALL = re.compile(r"python3?\s+(?:-\w+\s+)*\.claude/tools/(\w+)\.py([^\n`|;]*)")
FLAG = re.compile(r"(?<![\w-])--([A-Za-z][\w-]*)")
IMPORT = re.compile(r"^\s*(?:import|from)\s+(\w+)", re.M)
IMPLICIT = {"help"}  # argparse adds it to every parser


def examples(text):
    """(tool, flags) for each tool call in `text`."""
    for m in CALL.finditer(text):
        args = re.sub(r"<[^>\n]*>", "", m.group(2).split("&&", 1)[0])
        yield m.group(1), sorted(set(FLAG.findall(args)))


class Sources:
    def __init__(self, claude):
        self.claude = claude
        self.cache = {}

    def reach(self, tool):
        """The text of the tool and of every tools/ or hooks/ module it imports; None when the tool is absent."""
        if tool in self.cache:
            return self.cache[tool]
        start = self.claude / "tools" / (tool + ".py")
        if not start.is_file():
            self.cache[tool] = None
            return None
        seen, todo, texts = {start}, [start], []
        while todo:
            text = todo.pop().read_text(encoding="utf-8", errors="replace")
            texts.append(text)
            for name in IMPORT.findall(text):
                for folder in ("tools", "hooks"):
                    path = self.claude / folder / (name + ".py")
                    if path.is_file() and path not in seen:
                        seen.add(path)
                        todo.append(path)
        self.cache[tool] = "\n".join(texts)
        return self.cache[tool]


def literal(flag, source):
    return re.search(r"--%s(?![\w-])" % re.escape(flag), source) is not None


def flag_errors(rel, text, sources):
    errors = []
    for tool, flags in examples(text):
        source = sources.reach(tool)
        if source is None:
            errors.append("%s: tools/%s.py is absent" % (rel, tool))
            continue
        errors += ["%s: tools/%s.py has no --%s" % (rel, tool, f) for f in flags
                   if f not in IMPLICIT and not literal(f, source)]
    return sorted(set(errors))


def doc_errors(claude):
    sources = Sources(claude)
    errors = []
    for tree in DOC_TREES:
        for path in sorted((claude / tree).rglob("*.md")):
            errors += flag_errors(path.relative_to(claude).as_posix(), path.read_text(encoding="utf-8", errors="replace"), sources)
    return errors


@pytest.fixture
def sources():
    return Sources(CLAUDE)


def test_every_doc_example_names_only_flags_its_tool_parses():
    real = doc_errors(CLAUDE)
    assert real == [], real


def test_a_planted_doc_flag_the_tool_lacks_is_reported(sources):
    planted = "Run `python3 .claude/tools/quote_check.py --nosuchflag-zq <x> --also-bad` now."
    assert flag_errors("p.md", planted, sources) == [
        "p.md: tools/quote_check.py has no --also-bad", "p.md: tools/quote_check.py has no --nosuchflag-zq"]


def test_a_tool_absent_on_disk_is_its_own_failure(sources):
    assert flag_errors("p.md", "`python3 .claude/tools/zq_absent_tool.py --x`", sources) == [
        "p.md: tools/zq_absent_tool.py is absent"]


def test_a_flag_after_a_pipe_semicolon_or_and_or_backtick_is_ignored(sources):
    text = ("`python3 .claude/tools/quote_check.py --help | grep --zq1`; python3 .claude/tools/quote_check.py -x ; echo --zq2\n"
            "python3 .claude/tools/quote_check.py && cmd --zq3 and `x` --zq4")
    assert flag_errors("p.md", text, sources) == []


def test_a_placeholder_is_dropped(sources):
    assert flag_errors("p.md", "`python3 .claude/tools/quote_check.py <--zq5> <a --zq6>`", sources) == []


def test_a_tool_source_reach_includes_a_module_it_imports(sources):
    imported = next((t for t in sorted((CLAUDE / "tools").glob("*.py"))
                     if any((CLAUDE / f / (n + ".py")).is_file() for f in ("tools", "hooks")
                            for n in IMPORT.findall(t.read_text(encoding="utf-8", errors="replace")))), None)
    assert imported is not None, "no tool imports another tool or hook"
    assert len(sources.reach(imported.stem)) > len(imported.read_text(encoding="utf-8", errors="replace"))
