"""The standing-context bounds (T2). A push over any of them fails here.

Core: `template/.claude/CLAUDE.core.md` (the shared core every project's `CLAUDE.md` imports) plus every
file it `@`-imports, at most 8 KB.
Per repo: the core plus that repo's `CLAUDE.md` draft in `v2/drafts/` (and what it imports), at most 15 KB.
Model-visible frontmatter: the `description` of each skill and command that does not set
`disable-model-invocation: true`, at most 6 KB. Both are the owner's sanity bounds for today's
scope, not the aim; a bound its content justifies rises with the owner's say.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "template" / ".claude"
DRAFTS = sorted((REPO / "v2" / "drafts").glob("*.CLAUDE.md"))
CORE_MAX = 8 * 1024
REPO_MAX = 15 * 1024
FRONTMATTER_MAX = 6 * 1024

REMEDY = (
    "Run `/claudemd_compact`. Fix the push at its cause (a restatement, a rule for deleted machinery, "
    "a description longer than its trigger needs), never by cutting a rule's reason or hiding a command a "
    "session needs. A bound the content justifies rises with the owner's say, not in this push."
)
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
DESCRIPTION = re.compile(r"^description:[ \t]*(?:>-?|\|-?)?[ \t]*(.*(?:\n[ \t]+.*)*)", re.M)


def always_loaded_bytes(root, repo_claude=None):
    """Bytes of the core and its `@`-imports, plus `repo_claude` and its imports when given."""
    seen, todo = {}, [root / "CLAUDE.core.md", *([repo_claude] if repo_claude else [])]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        data = path.read_bytes()
        seen[path] = len(data)
        for line in data.decode("utf-8").splitlines():
            if line.startswith("@"):
                target = path.parent / line[1:].strip()
                # A repo draft's `@CLAUDE.core.md` names the core it will sit beside once installed.
                todo.append(target if target.exists() else root / target.name)
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


def over_budget(root, repo_claude=None):
    """Problems for the core alone, or (with `repo_claude`) for the core plus that repo's `CLAUDE.md`."""
    measures = [("always-loaded text", always_loaded_bytes(root, repo_claude), REPO_MAX if repo_claude else CORE_MAX)]
    if not repo_claude:
        measures.append(("model-visible frontmatter", frontmatter_bytes(root), FRONTMATTER_MAX))
    return [f"{label} is {measured:,} B, over {cap:,} B. {REMEDY}" for label, measured, cap in measures if measured > cap]


def test_shared_core_is_inside_both_bounds():
    assert not over_budget(ROOT)


def test_each_repo_draft_with_the_core_is_inside_its_bound():
    assert DRAFTS and not [p for d in DRAFTS for p in over_budget(ROOT, d)]


def test_a_9_kb_core_fails_naming_the_remedy(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("x" * 9 * 1024, encoding="utf-8")
    problems = over_budget(tmp_path)
    assert len(problems) == 1 and "/claudemd_compact" in problems[0] and "at its cause" in problems[0]


def test_a_repo_draft_that_takes_the_pair_past_15_kb_fails(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("x" * 8 * 1024, encoding="utf-8")
    draft = tmp_path / "Repo.CLAUDE.md"
    draft.write_text("y" * 8 * 1024, encoding="utf-8")
    assert over_budget(tmp_path) == [] and len(over_budget(tmp_path, draft)) == 1


def test_a_core_import_counts_toward_the_core(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("@more.md\n", encoding="utf-8")
    (tmp_path / "more.md").write_text("x" * 9 * 1024, encoding="utf-8")
    assert len(over_budget(tmp_path)) == 1


def test_hidden_commands_do_not_count(tmp_path):
    (tmp_path / "CLAUDE.core.md").write_text("", encoding="utf-8")
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "a.md").write_text("---\ndescription: shown\n---\n", encoding="utf-8")
    (tmp_path / "commands" / "b.md").write_text(
        "---\ndescription: hidden\ndisable-model-invocation: true\n---\n", encoding="utf-8")
    assert frontmatter_bytes(tmp_path) == len("shown")
