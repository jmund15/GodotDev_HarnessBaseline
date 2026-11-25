"""Proof that each `pr_pipeline` entry of the project's adaptation.json names something that exists.

An entry runs only when a batch of PRs is open, so a typo'd `/command` or script path would fail on that day and not
before. A `/command` entry needs `commands/<name>.md`; any other entry's `.claude/...` path (with or without a leading
`{worktree}/`) needs the file. An entry that runs a tool outside `.claude/` (`npm run ...`) is the project's to prove.
"""
import json
import re
import sys
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "tools"))
import adaptation  # noqa: E402


def unresolved(claude_dir):
    """`(point, entry, why)` for each entry of `claude_dir`'s `pr_pipeline` that names a missing command or path."""
    claude = Path(claude_dir)
    gaps = []
    for point, entries in adaptation.get(claude, "pr_pipeline").items():
        for entry in entries:
            if entry.startswith("/"):
                name = entry[1:].split()[0]
                if not (claude / "commands" / (name + ".md")).is_file():
                    gaps.append((point, entry, "commands/%s.md is missing" % name))
                continue
            for token in entry.split():
                path = re.sub(r"^\{worktree\}/", "", token)
                if path.startswith(".claude/") and not (claude.parent / path).exists():
                    gaps.append((point, entry, "%s is missing" % path))
    return gaps


def planted(tmp_path, block):
    claude = tmp_path / ".claude"
    (claude / "skills" / "project_subsystems").mkdir(parents=True)
    (claude / "skills" / "project_subsystems" / "adaptation.json").write_text(json.dumps({"pr_pipeline": block}), encoding="utf-8")
    (claude / "commands").mkdir()
    (claude / "commands" / "pr_sync.md").write_text("sync\n", encoding="utf-8")
    (claude / "scripts").mkdir()
    (claude / "scripts" / "gate.ps1").write_text("exit 0\n", encoding="utf-8")
    adaptation._CACHE.clear()
    return claude


def test_every_pr_pipeline_entry_of_this_project_resolves():
    assert unresolved(CLAUDE) == []


def test_entries_that_exist_resolve(tmp_path):
    claude = planted(tmp_path, {"sync": ["/pr_sync {pr}"], "gate": ["pwsh -File {worktree}/.claude/scripts/gate.ps1 -Detach"],
                                "post_merge": ["npm --prefix {worktree} run verify:merge"]})
    assert unresolved(claude) == []


def test_a_missing_command_and_a_missing_script_are_each_named(tmp_path):
    claude = planted(tmp_path, {"checklist": ["/pr_nope {pr}"], "discover": ["python3 .claude/tools/nope.py sweep"],
                                "gate": ["pwsh -File {worktree}/.claude/scripts/nope.ps1"]})
    assert unresolved(claude) == [
        ("discover", "python3 .claude/tools/nope.py sweep", ".claude/tools/nope.py is missing"),
        ("gate", "pwsh -File {worktree}/.claude/scripts/nope.ps1", ".claude/scripts/nope.ps1 is missing"),
        ("checklist", "/pr_nope {pr}", "commands/pr_nope.md is missing"),
    ]
