"""Proof that every Markdown link in the shipped command tree resolves (A11).

The resolution is `tools/verify_doc_citations.py`'s own (`link_rows`), so a link means the same thing here as in
a design doc. Links only: a runtime path in a code span (`.claude/worktrees/`) is not a link and is not checked.
"""
import shutil
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TEMPLATE / ".claude" / "tools"))
import verify_doc_citations as vdc  # noqa: E402


def dead_links(commands_dir, repo):
    rs = vdc.Resolver(str(repo))
    dead = []
    for doc in sorted(Path(commands_dir).rglob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for target, status, _detail in vdc.link_rows(vdc.FENCE.sub("", text), rs, str(doc)):
            if status == "missing":
                dead.append(f"{doc.relative_to(repo).as_posix()} -> {target}")
    return dead


def test_shipped_command_links_resolve():
    assert dead_links(TEMPLATE / ".claude" / "commands", TEMPLATE) == []


def test_a_planted_dead_link_fails(tmp_path):
    shutil.copytree(TEMPLATE / ".claude", tmp_path / ".claude", ignore=shutil.ignore_patterns("tests", "scratch"))
    victim = tmp_path / ".claude" / "commands" / "plan.md"
    victim.write_text(victim.read_text(encoding="utf-8") + "\nSee [gone](no_such_file.md).\n", encoding="utf-8")
    assert dead_links(tmp_path / ".claude" / "commands", tmp_path) == [".claude/commands/plan.md -> no_such_file.md"]
