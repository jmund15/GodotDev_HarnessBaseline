#!/usr/bin/env bash
# bootstrap.sh — adopt the harness baseline into a new Godot + C# project.
#
# Usage (run from the baseline repo root):
#   ./bootstrap.sh --target /path/to/NewGame --project-name NewGame \
#       [--vault-root "C:/Users/you/Documents/ObsidianVault"] \
#       [--project-root "C:/path/to/NewGame"] \
#       [--repo https://github.com/you/harness-baseline.git] [--ref main] \
#       [--layers pure,coding,godot] [--force]
#
#   --layers takes a PREFIX of pure -> coding -> godot (e.g. "pure" for a
#   content-production project, "pure,coding" for a non-Godot code project). Default
#   is all three archetypes.
#
# What it does:
#   1. Copies template/.claude into the target project (refusing to clobber an
#      existing .claude unless --force).
#   2. Substitutes {{PROJECT_NAME}} / {{PROJECT_NAMESPACE}} / {{VAULT_ROOT}} /
#      {{PROJECT_ROOT}} everywhere ({{PROJECT_NAMESPACE}} defaults to the project name).
#   3. Optionally strips layers not in --layers (manifest-driven).
#   4. Writes .claude/baseline.lock.json via baseline_sync.py init so /sync_baseline
#      works from day one.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="" PROJECT_NAME="" VAULT_ROOT="" PROJECT_ROOT="" REPO="" REF="main"
FORCE=0 LAYERS="pure,coding,godot"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2;;
    --project-name) PROJECT_NAME="$2"; shift 2;;
    --vault-root) VAULT_ROOT="$2"; shift 2;;
    --project-root) PROJECT_ROOT="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    --ref) REF="$2"; shift 2;;
    --layers) LAYERS="$2"; shift 2;;
    --force) FORCE=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

[[ -n "$TARGET" && -n "$PROJECT_NAME" ]] || { echo "required: --target, --project-name" >&2; exit 1; }
[[ -f "$HERE/baseline.manifest.json" ]] || { echo "baseline.manifest.json missing — run python3 tools/gen_manifest.py first" >&2; exit 1; }
if [[ -d "$TARGET/.claude" && $FORCE -ne 1 ]]; then
  echo "$TARGET/.claude already exists — pass --force to merge-overwrite" >&2; exit 1
fi
if [[ -z "$REPO" ]]; then
  REPO="$(git -C "$HERE" remote get-url origin 2>/dev/null || true)"
  [[ -n "$REPO" ]] || { echo "no --repo given and baseline has no origin remote" >&2; exit 1; }
fi

mkdir -p "$TARGET"
cp -r "$HERE/template/.claude" "$TARGET/"

case "$LAYERS" in
  pure|pure,coding|pure,coding,godot) ;;
  *) echo "--layers must be a prefix of pure,coding,godot (got: $LAYERS)" >&2; exit 1;;
esac

TARGET="$TARGET" PROJECT_NAME="$PROJECT_NAME" PROJECT_NAMESPACE="${PROJECT_NAMESPACE:-$PROJECT_NAME}" \
VAULT_ROOT="$VAULT_ROOT" PROJECT_ROOT="$PROJECT_ROOT" LAYERS="$LAYERS" HERE="$HERE" python3 - <<'EOF'
import json, os
from pathlib import Path

target = Path(os.environ["TARGET"])
here = Path(os.environ["HERE"])
subs = {
    "{{PROJECT_NAME}}": os.environ["PROJECT_NAME"],
    "{{PROJECT_NAMESPACE}}": os.environ["PROJECT_NAMESPACE"],
    "{{VAULT_ROOT}}": os.environ.get("VAULT_ROOT", ""),
    "{{PROJECT_ROOT}}": os.environ.get("PROJECT_ROOT", ""),
}
subs = {k: v for k, v in subs.items() if v}

manifest = json.loads((here / "baseline.manifest.json").read_text(encoding="utf-8"))
adopted = set(os.environ["LAYERS"].split(","))
removed = 0
for entry in manifest["files"]:
    if entry["layer"] not in adopted:
        p = target / entry["path"]
        if p.exists():
            p.unlink(); removed += 1
if removed:
    print(f"layers stripped (kept {os.environ['LAYERS']}): {removed} files removed")
# Prune directories emptied by the strip so the target tree matches its layers.
for d in sorted((target / ".claude").rglob("*"), reverse=True):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()

changed = 0
for p in (target / ".claude").rglob("*"):
    if not p.is_file():
        continue
    try:
        s = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    out = s
    for k, v in subs.items():
        out = out.replace(k, v)
    if out != s:
        p.write_text(out, encoding="utf-8")
        changed += 1
print(f"substitutions applied in {changed} files")

# --- Layer-aware settings.json pruning -------------------------------------
# The seed settings.json ships with wiring for every layer. After the layer
# strip above, drop: (a) hook entries whose script no longer exists, (b)
# Skill(<name>) allow entries whose command/skill file no longer exists, (c)
# gamedev-only permission entries when the godot layer wasn't adopted.
import re as _re
settings_path = target / ".claude" / "settings.json"
if settings_path.exists():
    cfg = json.loads(settings_path.read_text(encoding="utf-8"))
    pruned = 0
    hooks = cfg.get("hooks", {})
    for event, groups in list(hooks.items()):
        for group in list(groups):
            kept = []
            for h in group.get("hooks", []):
                m = _re.search(r"\.claude/hooks/([\w.]+)", h.get("command", ""))
                if m and not (target / ".claude" / "hooks" / m.group(1)).exists():
                    pruned += 1
                    continue
                kept.append(h)
            group["hooks"] = kept
            if not kept:
                groups.remove(group)
        if not groups:
            del hooks[event]
    allow = cfg.get("permissions", {}).get("allow", [])
    def keep_perm(entry: str) -> bool:
        global pruned
        m = _re.fullmatch(r"Skill\(([\w-]+)\)", entry)
        if m:
            name = m.group(1)
            if not ((target / ".claude" / "commands" / f"{name}.md").exists()
                    or (target / ".claude" / "skills" / name).exists()):
                pruned += 1
                return False
        if "godot" not in adopted:
            if _re.search(r"dotnet|gdunit4|GODOT_BIN|mcp__godot__|godotengine|godot-gdunit", entry, _re.I):
                pruned += 1
                return False
        return True
    cfg["permissions"]["allow"] = [e for e in allow if keep_perm(e)]
    settings_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"settings.json pruned to adopted layers: {pruned} entries removed")
EOF

( cd "$TARGET" && python3 .claude/tools/baseline_sync.py init \
    --baseline-dir "$HERE" --repo "$REPO" --ref "$REF" \
    --sub "{{PROJECT_NAME}}=$PROJECT_NAME" \
    --sub "{{PROJECT_NAMESPACE}}=${PROJECT_NAMESPACE:-$PROJECT_NAME}" \
    ${VAULT_ROOT:+--sub "{{VAULT_ROOT}}=$VAULT_ROOT"} \
    ${PROJECT_ROOT:+--sub "{{PROJECT_ROOT}}=$PROJECT_ROOT"} )

cat <<NEXT

Bootstrapped. Next steps in $TARGET:
  1. Fill in the PROJECT section of .claude/CLAUDE.md (domain split, project domains).
  2. Seed skills/game_vision/SKILL.md and skills/project_subsystems/SKILL.md.
  3. Create the Obsidian dirs: <vault>/DevProjects/$PROJECT_NAME/Claude/TODO/.
  4. Review .claude/settings.json permissions for your machine; run /sync_permissions later.
  5. In the first Claude session: /system_check, then /reindex_search.
  6. Commit .claude/ including baseline.lock.json.
NEXT
