#!/usr/bin/env python3
"""S8 proof: `compose --check` against the LIVE worktree tree's own settings split.

The baseline repo itself has no `baseline.lock.json` (that is a consumer-only artifact), so this
proof materializes a minimal consumer tree from the worktree's actual, current
`template/.claude/settings.base.json` and a fresh empty `settings.project.json`, with a synthetic
lock declaring only the `.claude/settings.json` composed row. That is "the live tree" this proof
checks: the CURRENT committed `settings.base.json` composes cleanly against a project file, and
`compose --check` round-trips (first run finds drift and writes nothing; a `compose` then a repeat
`--check` are clean).

    python3 .claude/tests/test_settings_composed.py
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
_TEMPLATE_CLAUDE = Path(__file__).resolve().parents[1]
_REPO_ROOT = _TEMPLATE_CLAUDE.parents[1]  # .../template/.claude -> .../template -> repo root


def case(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    return None if ok else f"{label}: {detail}"


def _rmtree_writable(path: Path) -> None:
    def onerror(func, p, exc_info):
        try:
            Path(p).chmod(stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror)


def main() -> int:
    base_path = _TEMPLATE_CLAUDE / "settings.base.json"
    if not base_path.is_file():
        print(f"CANNOT-RUN: {base_path} is missing", file=sys.stderr)
        return 2

    failures = []
    root = Path(tempfile.mkdtemp(prefix="settings_composed_"))
    claude = root / ".claude"
    (claude / "hooks").mkdir(parents=True)
    (claude / "tools").mkdir(parents=True)
    for name in ("baseline_sync.py", "baseline_identity.py", "baseline_compose.py", "adaptation.py"):
        shutil.copy(_TOOLS_DIR / name, claude / "tools" / name)
    shutil.copy(base_path, claude / "settings.base.json")
    (claude / "settings.project.json").write_text("{}\n", encoding="utf-8")
    (claude / "skills" / "project_subsystems").mkdir(parents=True)
    (claude / "skills" / "project_subsystems" / "adaptation.json").write_text("{}\n", encoding="utf-8")
    (claude / "skills" / "project_subsystems" / "SKILL.md").write_text(
        "# project_subsystems\n\n```yaml\nsubsystems:\n  - id: fixture-subsystem\n    paths: [fixture]\n```\n",
        encoding="utf-8")

    lock = {
        "schema": 2, "profile": "pure,coding,godot", "substitutions": {},
        "baseline_repo": "https://example.invalid/x.git", "baseline_ref": "main",
        "synced_commit": "0" * 40, "identity": {"abbreviations": []},
        "files": {
            ".claude/settings.base.json": {"status": "tracked", "layer": "pure", "hash": None,
                                            "judged": {"sha": None, "verdict": "push", "at": "x", "borderline": False, "confirmed_at": None}},
            ".claude/settings.project.json": {"status": "local", "layer": "pure", "judged": None},
            ".claude/settings.json": {"status": "composed", "layer": "pure", "hash": None,
                                       "inputs": [".claude/settings.base.json", ".claude/settings.project.json"], "judged": None},
        },
    }
    (claude / "baseline.lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")

    def run_compose(args):
        return subprocess.run(
            [sys.executable, str(claude / "tools" / "baseline_sync.py"), "compose"] + args,
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )

    r_before = run_compose(["--check"])
    failures.append(case("compose --check on the live settings.base.json finds drift first",
                          r_before.returncode == 1, r_before.stdout + r_before.stderr))
    r_write = run_compose([])
    failures.append(case("compose writes settings.json from the live settings.base.json",
                          r_write.returncode == 0 and (claude / "settings.json").is_file(),
                          r_write.stdout + r_write.stderr))
    r_after = run_compose(["--check"])
    failures.append(case("compose --check is clean once composed",
                          r_after.returncode == 0, r_after.stdout + r_after.stderr))

    composed = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    base = json.loads(base_path.read_text(encoding="utf-8"))
    composed_allow = set(composed.get("permissions", {}).get("allow", []))
    base_allow = set(base.get("permissions", {}).get("allow", []))
    non_skill_base = {e for e in base_allow if not e.startswith("Skill(")}
    # This fixture's `.claude/commands` and `.claude/skills` are empty, so every `Skill(<name>)`
    # base entry legitimately prunes (R10); every other base entry must survive unpruned, since
    # the profile here is the full `pure,coding,godot`.
    failures.append(case("composed allow keeps every non-Skill() base entry (full profile)",
                          non_skill_base <= composed_allow,
                          repr(non_skill_base - composed_allow)))
    failures.append(case("composed allow adds nothing beyond base ∪ project",
                          composed_allow <= base_allow, repr(composed_allow - base_allow)))

    _rmtree_writable(root)

    total = len(failures)
    failures = [f for f in failures if f]
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
