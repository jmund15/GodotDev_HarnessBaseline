#!/usr/bin/env python3
"""
gen_manifest.py — regenerate baseline.manifest.json from the template/ tree.

Each manifest entry: {"path": <relpath under template/>, "layer": ..., "sync": ...}

  layer "universal" : any Claude Code project
  layer "godot"     : Godot 4.x + C# projects
  layer "jmodot"    : projects built on the Jmodot framework submodule
  sync  "auto"      : hash-tracked by baseline_sync.py in consumer projects
  sync  "seed"      : copied at bootstrap, thereafter project-owned (watch-only)

Run from the baseline repo root after adding/removing template files:
  python3 tools/gen_manifest.py
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template"

SEED_PATTERNS = [
    ".claude/CLAUDE.md",
    ".claude/settings.json",
    ".claude/worklog-titles.md",
    ".claude/skills/game_vision/*",
    ".claude/skills/project_subsystems/*",
    ".claude/commands/checklists/known_failure_modes.md",
]

JMODOT_PATTERNS = [
    ".claude/skills/jmodot/*",
    ".claude/skills/status_effect_authoring/*",
    ".claude/skills/vfx_patterns/*",
    ".claude/skills/logging_methodology/*",
    ".claude/rules/hsm_bt_patterns.md",
    ".claude/rules/jmodot_*.md",
    ".claude/rules/visual_layers.md",
    ".claude/hooks/check_logger_tag_prefix.py",
    ".claude/commands/agents/jmodot_submodule_procedure.md",
    ".claude/auto-memory/jmodot_*.md",
    ".claude/auto-memory/arch_rule_*.md",
    ".claude/auto-memory/Blackboard_NullStorage_Asymmetry.md",
    ".claude/auto-memory/gotcha_blackboard_*.md",
    ".claude/auto-memory/gotcha_component_caches_*.md",
    ".claude/auto-memory/feedback_prefer_typed_shapes_over_empty_markers.md",
    ".claude/auto-memory/feedback_prefer_data_params_over_injected_delegates.md",
    ".claude/auto-memory/feedback_typed_state_over_bb_flag_soup.md",
    ".claude/auto-memory/feedback_dont_defer_existing_framework_abstractions.md",
]

GODOT_PATTERNS = [
    ".claude/skills/testing/*",
    ".claude/skills/refactor_procedure/*",
    ".claude/skills/sprite_authoring/*",
    ".claude/skills/shader_authoring/*",
    ".claude/rules/csharp_*.md",
    ".claude/rules/godot_files.md",
    ".claude/rules/scene_authoring.md",
    ".claude/rules/physics_patterns.md",
    ".claude/rules/cloud_dev.md",
    ".claude/hooks/session_context_loader.py",
    ".claude/hooks/cloud_test_enforcer.py",
    ".claude/hooks/analyze_godot_logs.py",
    ".claude/hooks/tres_nullstrip_guard.py",
    ".claude/hooks/apply_blanket_tool.py",
    ".claude/hooks/tool_cascade_audit.py",
    ".claude/hooks/pattern_enforcer.py",
    ".claude/commands/regression_gate.md",
    ".claude/commands/analyze_godot_logs.md",
    ".claude/commands/audit_test_accessors.md",
    ".claude/commands/clean_pull.md",
    ".claude/commands/pr_test_checklist.md",
    ".claude/commands/agents/pr_test_checklist_conventions.md",
    ".claude/cloud-install.sh",
    ".claude/scripts/run_test_suite.ps1",
    ".claude/tools/csharp-ls-adapter.js",
    ".claude/tools/setup-csharp-ls.sh",
    ".claude/auto-memory/gotcha_godot*.md",
    ".claude/auto-memory/gotcha_gdunit4_*.md",
    ".claude/auto-memory/gotcha_editor_reserialize_*.md",
    ".claude/auto-memory/gotcha_export_enum_*.md",
    ".claude/auto-memory/gotcha_inherited_scene_*.md",
    ".claude/auto-memory/feedback_godot_*.md",
    ".claude/auto-memory/feedback_lsp_default_for_csharp.md",
]


# Runtime/session-state directories and build artifacts that exist on disk but are
# never part of the shipped template (the filesystem walk doesn't respect gitignore).
# Mirrors baseline_sync.py's consumer-side exclude set.
EXCLUDE_DIR_PARTS = {"__pycache__", "logs", ".cache", "sessions",
                     "worktrees", "plans", "scratch"}


def is_artifact(p: Path) -> bool:
    return p.suffix == ".pyc" or bool(EXCLUDE_DIR_PARTS.intersection(p.parts))


def match(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, p) for p in patterns)


def main() -> None:
    files = []
    # Sort by the posix relpath STRING, not the Path object: Path comparison uses
    # os.path.normcase, so it's case-insensitive on Windows and case-sensitive on
    # Linux — same inputs, different manifest order per OS. A string key is stable
    # cross-platform.
    for p in sorted(TEMPLATE.rglob("*"), key=lambda x: x.relative_to(TEMPLATE).as_posix()):
        if not p.is_file() or p.name == ".gitkeep":
            continue
        if is_artifact(p):
            continue  # runtime/session-state artifacts — not part of the template
        rel = p.relative_to(TEMPLATE).as_posix()
        if match(rel, JMODOT_PATTERNS):
            layer = "jmodot"
        elif match(rel, GODOT_PATTERNS):
            layer = "godot"
        else:
            layer = "universal"
        sync = "seed" if match(rel, SEED_PATTERNS) else "auto"
        files.append({"path": rel, "layer": layer, "sync": sync})
    manifest = {"version": 1, "files": files}
    (ROOT / "baseline.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for f in files:
        counts[f["layer"]] = counts.get(f["layer"], 0) + 1
    print(f"{len(files)} files — " +
          ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    # Layer assignment defaults to "universal" on no pattern match, so a newly added
    # Godot/Jmodot file silently lands universal (and escapes --no-jmodot stripping).
    # The audit catches that — nudge toward it rather than trust the fallthrough.
    print("verify separation: python3 tools/audit_baseline.py")


if __name__ == "__main__":
    main()
