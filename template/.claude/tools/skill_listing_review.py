"""Skill-listing entries nobody has ruled on: the session-start check for startup context we did not author.

Claude Code lists every model-invocable skill to the model at session start, and each entry is paid every
turn. Entries arrive from outside the repo with no review: a client update adds bundled skills, claude.ai
account sync adds document skills, a plugin adds its own. This check reads the listing the newest session
transcript recorded (its `skill_listing` attachment's `names`), drops every entry a file under `.claude/`
authors (a skill dir, a command path, a workflow's `meta.name`), drops every entry
`reference/skill_listing_kept.json` keeps, and reports the rest.

Rule each reported entry: keep it (add it to `kept` with the reason it applies here), or hide it. Hide a
skill with a `skillOverrides` entry in `.claude/settings.project.json`, which every checkout shares, then run `python3 .claude/tools/baseline_sync.py compose`
(`user-invocable-only` keeps it typable, `off` removes it); a plugin's skills through `/plugin`; claude.ai synced skills with
`syncClaudeAiSkills: false` in user settings. A hidden entry leaves the listing, so it never reports again.

Usage: python3 .claude/tools/skill_listing_review.py [--transcripts <dir>]   (prints the banner, or nothing)
"""
import argparse
import json
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent.parent
LEDGER = Path("reference") / "skill_listing_kept.json"
NEWEST_FILES = 8      # transcripts to try, newest first
ROWS_PER_FILE = 400   # the initial listing sits near a transcript's top
WORKFLOW_NAME = re.compile(r"""^\s*name:\s*['"]([^'"]+)['"]""", re.M)


def authored(claude: Path) -> set:
    """Listing names that files under this .claude dir author."""
    names = {p.parent.name for p in claude.glob("skills/*/SKILL.md")}
    commands = claude / "commands"
    names |= {":".join(p.relative_to(commands).with_suffix("").parts) for p in commands.rglob("*.md")}
    for script in claude.glob("workflows/*.js"):
        match = WORKFLOW_NAME.search(script.read_text(encoding="utf-8", errors="replace")[:2000])
        if match:
            names.add(match.group(1))
    return names


def listed_names(transcripts: Path):
    """The newest recorded skill listing's names, or None when no transcript holds one."""
    if not transcripts.is_dir():
        return None
    files = sorted(transcripts.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:NEWEST_FILES]:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= ROWS_PER_FILE:
                    break
                if '"skill_listing"' not in line:
                    continue
                try:
                    attachment = json.loads(line).get("attachment") or {}
                except ValueError:
                    continue
                if attachment.get("type") == "skill_listing" and isinstance(attachment.get("names"), list):
                    return attachment["names"]
    return None


def unruled(transcripts: Path, claude: Path = CLAUDE_DIR) -> list:
    """Sorted listing entries neither authored here nor kept by the ledger. Raises on an unreadable ledger."""
    names = listed_names(transcripts)
    if not names:
        return []
    kept = json.loads((claude / LEDGER).read_text(encoding="utf-8"))["kept"]
    ours = authored(claude)
    return sorted({n for n in names if n not in ours and n not in kept})


def banner(transcripts: Path, claude: Path = CLAUDE_DIR) -> str:
    """One session-start line naming unruled entries; empty when there are none."""
    try:
        new = unruled(transcripts, claude)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"skill-listing: UNKNOWN ({type(error).__name__}: {error})"
    if not new:
        return ""
    return (f"[skill-listing] {len(new)} listing entr{'y' if len(new) == 1 else 'ies'} no one has ruled on, "
            f"each paid every turn: {', '.join(new)}. Bring them to the owner as one packet: keep (add to "
            f"{LEDGER.as_posix()} with why it applies) or hide (skillOverrides user-invocable-only|off in "
            f"settings.project.json; /plugin; syncClaudeAiSkills). Recipe: tools/skill_listing_review.py docstring.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcripts", type=Path, required=True,
                        help="the project's transcript folder (~/.claude/projects/<slug>)")
    args = parser.parse_args(argv)
    line = banner(args.transcripts)
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
