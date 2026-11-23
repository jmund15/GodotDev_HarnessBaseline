"""The shared core's two standing-context bounds (T2). A push over either fails here.

Always-loaded: `template/.claude/CLAUDE.core.md` (the shared core every project's `CLAUDE.md` imports) plus every
file it `@`-imports, at most 15 KB.
Model-visible frontmatter: the `description` of each skill and command that does not set
`disable-model-invocation: true`, at most 6 KB. Both are the owner's sanity bounds for today's
scope, not the aim; a bound its content justifies rises with the owner's say.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "template" / ".claude"
ALWAYS_LOADED_MAX = 15 * 1024
FRONTMATTER_MAX = 6 * 1024

REMEDY = (
    "Run `/claudemd_compact`. Fix the push at its cause (a restatement, a rule for deleted machinery, "
    "a description longer than its trigger needs), never by cutting a rule's reason or hiding a command a "
    "session needs. A bound the content justifies rises with the owner's say, not in this push."
)
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
DESCRIPTION = re.compile(r"^description:[ \t]*(?:>-?|\|-?)?[ \t]*(.*(?:\n[ \t]+.*)*)", re.M)


def always_loaded_bytes(root):
    seen, todo = {}, [root / "CLAUDE.core.md"]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        data = path.read_bytes()
        seen[path] = len(data)
        for line in data.decode("utf-8").splitlines():
            if line.startswith("@"):
                todo.append(path.parent / line[1:].strip())
    return sum(seen.values())


def frontmatter_bytes(root):
    total = 0
    for path in [*root.glob("skills/*/SKILL.md"), *root.glob("commands/*.md")]:
        match = FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if not match or re.search(r"^disable-model-invocation:\s*true\s*$", match.group(1), re.M):
            continue
        description = DESCRIPTION.search(match.group(1))
        if description:
            total += len(" ".join(description.group(1).split()).encode("utf-8"))
    return total


def over_budget(root):
    problems = []
    for label, measured, cap in (("always-loaded text", always_loaded_bytes(root), ALWAYS_LOADED_MAX),
                                 ("model-visible frontmatter", frontmatter_bytes(root), FRONTMATTER_MAX)):
        if measured > cap:
            problems.append(f"{label} is {measured:,} B, over {cap:,} B. {REMEDY}")
    return problems


def test_shared_core_is_inside_both_bounds():
    assert not over_budget(ROOT)


def test_a_16_kb_core_fails_naming_the_remedy(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("x" * 16 * 1024, encoding="utf-8")
    problems = over_budget(tmp_path)
    assert len(problems) == 1 and "/claudemd_compact" in problems[0] and "at its cause" in problems[0]


def test_hidden_commands_do_not_count(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("", encoding="utf-8")
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "a.md").write_text("---\ndescription: shown\n---\n", encoding="utf-8")
    (tmp_path / "commands" / "b.md").write_text(
        "---\ndescription: hidden\ndisable-model-invocation: true\n---\n", encoding="utf-8")
    assert frontmatter_bytes(tmp_path) == len("shown")
