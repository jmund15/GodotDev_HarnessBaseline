---
name: gotcha_gitignore_build_glob_swallows_production_dirs
description: "Generic build-output gitignore globs ([Dd]ebug/, [Rr]elease/) match same-named production folders ANYWHERE in the tree — new deliverables become silently uncommittable. check-ignore new folders; negate on its own line (no inline comments)."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 51c3a7b4-e34c-4bfd-bcdf-b1d8710a9fe5
  modified: 2026-08-14T07:17:28.646Z
retire_when:
  - review-by: 2027-02-16
---

Generic build-output globs in `.gitignore` (`[Dd]ebug/`, `[Rr]elease/`, `x64/`) match ANY same-named folder in the tree, not just build roots — a design-mandated production folder named `Debug/` becomes silently uncommittable: no error, it just never appears in `git status`. **How to apply:** when a brand-new folder is missing from the untracked list, run `git check-ignore -v <path>` BEFORE assuming git sees it; fix with a `!path/` negation placed after the glob, **on its own line** — gitignore has NO inline comments (a trailing `# ...` makes the pattern literal and dead). **Verified:** 2026-06-11 — `Dungeon/ProcGen/Debug/` (the whole P3b.7 visualizer) absent from status; `check-ignore -v` named `.gitignore:66:[Dd]ebug/`; an inline-commented negation failed, own-line negation fixed it.

**Second victim, harder consequence (2026-08-14):** the same glob swallowed `addons/TileMapLayer3D/core/debug/debug_info_generator.gd` — a PARSE-TIME dependency (the plugin's scripts reference `DebugInfoGenerator` at load). A fresh worktree/clone fails to BOOT with `Identifier "DebugInfoGenerator" not declared` (GDScript parser error, debugger break); the main checkout only works because the file exists on disk from an editor session. When a fresh checkout boots to a parse error naming a class you cannot find in tracked files: (a) `git check-ignore -v` the addon's directories, (b) diff the checkout's `.godot/global_script_class_cache.cfg` against a working checkout — the class present in one and absent in the other confirms a gitignored declaration file.

**Verified (resolved):** 2026-09-04 memory-claim audit — `addons/TileMapLayer3D/core/debug/debug_info_generator.gd` is tracked and `git check-ignore` returns 1; the class rule stands, the instance is fixed.
