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
#   4. Writes .claude/baseline.lock.json via baseline_sync.py init (v2, with --layers
#      recorded as the lock profile) so /sync_baseline works from day one.
#   5. Runs baseline_sync.py compose, which writes .claude/settings.json from the copied
#      settings.base.json + settings.project.json seed, pruning per the adopted layers.
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
# CLAUDE.md imports the core and each adopted layer's doctrine file, in layer order.
claude_md = target / ".claude" / "CLAUDE.md"
if claude_md.exists():
    overlays = [f"@CLAUDE.{layer}.md" for layer in ("coding", "godot")
                if (target / ".claude" / f"CLAUDE.{layer}.md").exists()]
    lines = claude_md.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [line for line in lines if line.strip() not in {"@CLAUDE.coding.md", "@CLAUDE.godot.md"}]
    at = next((n + 1 for n, line in enumerate(kept) if line.strip() == "@CLAUDE.core.md"), 0)
    kept[at:at] = [line + "\n" for line in overlays]
    claude_md.write_text("".join(kept), encoding="utf-8", newline="\n")
# The seed domain list grows by each adopted layer's starter domains, once, at adoption.
registry = target / ".claude" / "skills" / "project_subsystems"
if (registry / "adaptation.json").exists():
    adaptation = json.loads((registry / "adaptation.json").read_text(encoding="utf-8"))
    for layer in ("coding", "godot"):
        starter = registry / f"adaptation.{layer}.json"
        if starter.exists():
            adaptation.setdefault("memory_domains", []).extend(
                json.loads(starter.read_text(encoding="utf-8")).get("memory_domains", []))
    (registry / "adaptation.json").write_text(json.dumps(adaptation, indent=2) + "\n", encoding="utf-8",
                                              newline="\n")
# Prune directories emptied by the strip so the target tree matches its layers.
for d in sorted((target / ".claude").rglob("*"), reverse=True):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()

# The copied engine owns substitution, so install and `check` agree: a PLACEHOLDER_OK file names
# the tokens rather than uses them, and stays byte-for-byte (baseline_sync.forward_for).
import sys
sys.path.insert(0, str(target / ".claude" / "tools"))
import baseline_sync

changed = 0
for p in (target / ".claude").rglob("*"):
    if not p.is_file():
        continue
    try:
        s = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    out = baseline_sync.forward_for(p.relative_to(target).as_posix(), s, subs)
    if out != s:
        p.write_text(out, encoding="utf-8")
        changed += 1
print(f"substitutions applied in {changed} files")
EOF

# Layer-aware settings.json pruning (a missing hook script, an absent Skill(<name>)
# command/skill, or a godot-stack permission when `godot` was not adopted) now lives in
# `baseline_compose.compose_settings` (R3/R10), run by `compose` below, after `init` has
# written the lock `profile` those filters read.
( cd "$TARGET" && python3 .claude/tools/baseline_sync.py init \
    --baseline-dir "$HERE" --repo "$REPO" --ref "$REF" --layers "$LAYERS" \
    --sub "{{PROJECT_NAME}}=$PROJECT_NAME" \
    --sub "{{PROJECT_NAMESPACE}}=${PROJECT_NAMESPACE:-$PROJECT_NAME}" \
    ${VAULT_ROOT:+--sub "{{VAULT_ROOT}}=$VAULT_ROOT"} \
    ${PROJECT_ROOT:+--sub "{{PROJECT_ROOT}}=$PROJECT_ROOT"} \
  && python3 .claude/tools/baseline_sync.py compose )

cat <<NEXT

Bootstrapped. Next steps in $TARGET:
  1. Fill in the PROJECT section of .claude/CLAUDE.md (domain split, project domains).
  2. Seed skills/project_subsystems/SKILL.md, and skills/game_vision/SKILL.md when godot is adopted.
  3. Create the Obsidian dirs: <vault>/DevProjects/$PROJECT_NAME/Claude/TODO/.
  4. Review permissions in .claude/settings.project.json (project) or .claude/settings.local.json
     (this machine); run /sync_permissions later.
  5. In the first Claude session: /system_check, then /reindex_search. On a new machine with
     the godot layer, /workstation_setup provisions the toolchain and runs /reindex_search.
  6. Commit .claude/ including baseline.lock.json.
NEXT
