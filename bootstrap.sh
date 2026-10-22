#!/usr/bin/env bash
# bootstrap.sh — adopt the harness baseline into a new project.
#
# Usage (run from the baseline repo root):
#   ./bootstrap.sh --target /path/to/NewProject [--project-name NewProject] \
#       [--vault-root "C:/Users/you/Documents/ObsidianVault"] \
#       [--repo https://github.com/you/harness-baseline.git] [--ref main] \
#       [--layers pure,coding,godot] [--force]
#
#   Path arguments take `C:\x`, `C:/x` or `/c/x`; they are normalized to POSIX absolute paths, and a
#   --vault-root that is not a directory stops the run before anything is written.
#   --project-name defaults to the target folder's name.
#   --layers takes a PREFIX of pure -> coding -> godot (e.g. "pure" for a
#   content-production project, "pure,coding" for a non-Godot code project). Default
#   is all three archetypes.
#   --force merges into an existing .claude: seed files that already exist stay as they are, and
#   the lock is rebuilt (`baseline_sync.py init --force`).
#
# What it does (every file it writes uses LF):
#   1. Copies template/.claude into the target project (refusing to clobber an
#      existing .claude unless --force): `git ls-files` of a git checkout, every file otherwise.
#   2. Substitutes {{PROJECT_NAME}} / {{PROJECT_NAMESPACE}} / {{VAULT_ROOT}} /
#      {{PROJECT_ROOT}} (the target folder) everywhere except baseline.lock.json
#      ({{PROJECT_NAMESPACE}} defaults to the project name).
#   3. Optionally strips layers not in --layers (manifest-driven).
#   4. Writes .claude/baseline.lock.json via baseline_sync.py init (v2, with --layers
#      recorded as the lock profile) so /sync_baseline works from day one; init also renders the
#      lane agents and writes .claude/gitignore.harness into the root .gitignore between its markers.
#   4b. Seeds the gitignored sync cache clone (.claude/.cache/baseline-repo) from this baseline, so the
#      pinned manifest is readable before the first sync.
#   5. Runs baseline_sync.py compose, which writes .claude/settings.json from the copied
#      settings.base.json, the adopted layers' settings.<layer>.json overlays and the
#      settings.project.json seed, pruning a missing hook script or Skill grant.
#   6. Runs `git init` when the target has no repo (staging 100755 template entries executable, and with
#      `git add -f` each delivered file a .gitignore rule matches) and points core.hooksPath at .claude/githooks;
#      creates the vault worklog skeleton
#      <vault>/DevProjects/<project>/Claude/TODO/Worklog.md when absent; merges autoMemoryDirectory
#      into the gitignored .claude/settings.local.json.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="" PROJECT_NAME="" VAULT_ROOT="" REPO="" REF="main"
FORCE=0 LAYERS="pure,coding,godot"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2;;
    --project-name) PROJECT_NAME="$2"; shift 2;;
    --vault-root) VAULT_ROOT="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    --ref) REF="$2"; shift 2;;
    --layers) LAYERS="$2"; shift 2;;
    --force) FORCE=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

# POSIX absolute for bash; the mixed form (C:/x on Windows, the same path elsewhere) for python, JSON and the lock.
posix_abs() {
  local p="${1//\\//}"
  if command -v cygpath >/dev/null 2>&1; then cygpath -u -a -- "$p"; else realpath -m -- "$p"; fi
}
native_abs() {
  local p="${1//\\//}"
  if command -v cygpath >/dev/null 2>&1; then cygpath -m -a -- "$p"; else realpath -m -- "$p"; fi
}

[[ -n "$TARGET" ]] || { echo "required: --target" >&2; exit 1; }
[[ -f "$HERE/baseline.manifest.json" ]] || { echo "baseline.manifest.json missing — run python3 tools/gen_manifest.py first" >&2; exit 1; }
case "$LAYERS" in
  pure|pure,coding|pure,coding,godot) ;;
  *) echo "--layers must be a prefix of pure,coding,godot (got: $LAYERS)" >&2; exit 1;;
esac

TARGET_NATIVE="$(native_abs "$TARGET")"
TARGET="$(posix_abs "$TARGET")"
[[ ! -e "$TARGET" || -d "$TARGET" ]] || { echo "--target $TARGET exists and is not a directory" >&2; exit 1; }
if [[ -n "$VAULT_ROOT" ]]; then
  [[ -d "${VAULT_ROOT//\\//}" ]] || { echo "--vault-root $VAULT_ROOT is not a directory" >&2; exit 1; }
  VAULT_NATIVE="$(native_abs "$VAULT_ROOT")"
  VAULT_ROOT="$(posix_abs "$VAULT_ROOT")"
else
  VAULT_NATIVE=""
fi
[[ -n "$PROJECT_NAME" ]] || PROJECT_NAME="$(basename "$TARGET")"
[[ -n "$PROJECT_NAME" && "$PROJECT_NAME" != "/" ]] || { echo "cannot derive a project name from --target; pass --project-name" >&2; exit 1; }
PROJECT_NAMESPACE="${PROJECT_NAMESPACE:-$PROJECT_NAME}"

if [[ -d "$TARGET/.claude" && $FORCE -ne 1 ]]; then
  echo "$TARGET/.claude already exists — pass --force to merge-overwrite" >&2; exit 1
fi
if [[ -z "$REPO" ]]; then
  REPO="$(git -C "$HERE" remote get-url origin 2>/dev/null || true)"
  [[ -n "$REPO" ]] || { echo "no --repo given and baseline has no origin remote" >&2; exit 1; }
fi

echo "project name:  $PROJECT_NAME"
echo "target folder: $TARGET_NATIVE"
if [[ -n "$VAULT_NATIVE" ]]; then
  echo "vault folder:  $VAULT_NATIVE/DevProjects/$PROJECT_NAME"
else
  echo "vault folder:  (none: no --vault-root, so no worklog skeleton)"
fi

mkdir -p "$TARGET"
# The copy step lists the 100755 template entries here; the git-init step stages them executable.
EXEC_LIST="$(mktemp)"
trap 'rm -f "$EXEC_LIST"' EXIT
EXEC_LIST_NATIVE="$(native_abs "$EXEC_LIST")"

TARGET="$TARGET_NATIVE" PROJECT_NAME="$PROJECT_NAME" PROJECT_NAMESPACE="$PROJECT_NAMESPACE" EXEC_LIST="$EXEC_LIST_NATIVE" \
VAULT_ROOT="$VAULT_NATIVE" PROJECT_ROOT="$TARGET_NATIVE" LAYERS="$LAYERS" HERE="$HERE" FORCE="$FORCE" python3 - <<'EOF'
import json, os, shutil, subprocess
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
seeds = {e["path"] for e in manifest["files"] if e.get("sync") == "seed"}

# Copy with LF line endings; a seed file the project already owns is left as it is.
kept = set()
template = here / "template"
listed = subprocess.run(["git", "-C", str(here), "ls-files", "-s", "-z", "--", "template/.claude"],
                        capture_output=True) if (here / ".git").exists() else None
executable = set()
if listed is not None and listed.returncode == 0 and listed.stdout:
    entries = [e.split("\t", 1) for e in listed.stdout.decode("utf-8").split("\0") if e]
    executable = {name for meta, name in entries if meta.startswith("100755")}
    sources = sorted(here / name for _meta, name in entries)
else:
    sources = sorted((template / ".claude").rglob("*"))
for src in sources:
    if not src.exists():
        continue
    rel = src.relative_to(template).as_posix()
    dest = target / rel
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        continue
    if rel in seeds and dest.exists():
        kept.add(rel)
        continue
    data = src.read_bytes()
    if b"\r\n" in data and b"\0" not in data:
        try:
            data.decode("utf-8")
            data = data.replace(b"\r\n", b"\n")
        except UnicodeDecodeError:
            pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    shutil.copymode(src, dest)
    if src.relative_to(here).as_posix() in executable:
        dest.chmod(dest.stat().st_mode | 0o111)
Path(os.environ["EXEC_LIST"]).write_text("".join(name[len("template/"):] + "\n" for name in sorted(executable)), encoding="utf-8")
if kept:
    print(f"seed files kept ({len(kept)}): " + ", ".join(sorted(kept)))

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
if claude_md.exists() and ".claude/CLAUDE.md" not in kept:
    overlays = [f"@CLAUDE.{layer}.md" for layer in ("coding", "godot")
                if (target / ".claude" / f"CLAUDE.{layer}.md").exists()]
    lines = claude_md.read_text(encoding="utf-8").splitlines(keepends=True)
    kept_lines = [line for line in lines if line.strip() not in {"@CLAUDE.coding.md", "@CLAUDE.godot.md"}]
    at = next((n + 1 for n, line in enumerate(kept_lines) if line.strip() == "@CLAUDE.core.md"), 0)
    kept_lines[at:at] = [line + "\n" for line in overlays]
    claude_md.write_text("".join(kept_lines), encoding="utf-8", newline="\n")
# The seed domain list grows by each adopted layer's starter domains, once, at adoption.
registry = target / ".claude" / "skills" / "project_subsystems"
if (registry / "adaptation.json").exists() and ".claude/skills/project_subsystems/adaptation.json" not in kept:
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
    rel = p.relative_to(target).as_posix()
    if not p.is_file() or rel == ".claude/baseline.lock.json" or rel in kept:
        continue
    try:
        s = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    out = baseline_sync.forward_for(rel, s, subs)
    if out != s:
        p.write_text(out, encoding="utf-8", newline="\n")
        changed += 1
print(f"substitutions applied in {changed} files")
EOF

# Layer-aware settings.json composition (each adopted layer's settings.<layer>.json merged over
# settings.base.json, then a missing hook script or an absent Skill(<name>) pruned) lives in
# `baseline_compose.compose_settings` (R3/R10), run by `compose` below, after `init` has
# written the lock `profile` that picks the overlays.
FORCE_ARG=()
if [[ $FORCE -eq 1 ]]; then FORCE_ARG=(--force); fi
( cd "$TARGET" && python3 .claude/tools/baseline_sync.py init \
    --baseline-dir "$HERE" --repo "$REPO" --ref "$REF" --layers "$LAYERS" \
    ${FORCE_ARG[@]+"${FORCE_ARG[@]}"} \
    --sub "{{PROJECT_NAME}}=$PROJECT_NAME" \
    --sub "{{PROJECT_NAMESPACE}}=$PROJECT_NAMESPACE" \
    ${VAULT_NATIVE:+--sub "{{VAULT_ROOT}}=$VAULT_NATIVE"} \
    --sub "{{PROJECT_ROOT}}=$TARGET_NATIVE" \
  && python3 .claude/tools/baseline_sync.py compose )

# The lock pins this baseline's commit; the proofs and `layer_closure.py` read that commit's manifest from the
# sync engine's cache clone, which the first sync would otherwise be the first to create.
CACHE_CLONE="$TARGET/.claude/.cache/baseline-repo"
if [[ ! -e "$CACHE_CLONE" ]]; then
  if git -c core.longpaths=true clone -q --no-checkout "$HERE" "$CACHE_CLONE" 2>/dev/null \
     && git -C "$CACHE_CLONE" remote set-url origin "$REPO"; then
    echo "baseline cache clone seeded in $TARGET_NATIVE/.claude/.cache/baseline-repo"
  else
    rm -rf "$CACHE_CLONE"
    echo "warning: baseline cache clone not seeded; the first sync creates it" >&2
  fi
fi

if [[ ! -e "$TARGET/.git" ]]; then
  git -C "$TARGET" init -q
  echo "git repository initialized in $TARGET_NATIVE"
  # A delivered file a .gitignore rule matches (the shipped .claude/tests/ ignore) is staged here, so
  # the first commit carries it; it is the install-time twin of /sync_baseline Pull step 4.
  python3 - "$TARGET_NATIVE" "$HERE/baseline.manifest.json" "$EXEC_LIST_NATIVE" <<'EOF'
import json, subprocess, sys
from pathlib import Path

target, manifest, exec_list = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
paths = [e["path"] for e in json.loads(manifest.read_text(encoding="utf-8"))["files"] if (target / e["path"]).is_file()]
checked = subprocess.run(["git", "-C", str(target), "check-ignore", "-z", "--stdin"],
                         input="\0".join(paths).encode("utf-8"), capture_output=True)
ignored = [name for name in checked.stdout.decode("utf-8").split("\0") if name]
for start in range(0, len(ignored), 200):
    subprocess.run(["git", "-C", str(target), "add", "-f", "--", *ignored[start:start + 200]], check=True)
if ignored:
    print(f"staged {len(ignored)} ignored delivered files (git add -f)")
executable = [name for name in exec_list.read_text(encoding="utf-8").splitlines() if (target / name).is_file()]
for start in range(0, len(executable), 200):
    subprocess.run(["git", "-C", str(target), "update-index", "--add", "--chmod=+x", "--", *executable[start:start + 200]], check=True)
if executable:
    print(f"staged {len(executable)} executable delivered files (100755)")
EOF
fi
if [[ -d "$TARGET/.claude/githooks" ]]; then
  git -C "$TARGET" config core.hooksPath .claude/githooks
fi

TARGET="$TARGET_NATIVE" PROJECT_NAME="$PROJECT_NAME" VAULT_ROOT="$VAULT_NATIVE" python3 - <<'EOF'
import datetime, json, os, sys
from pathlib import Path

target = Path(os.environ["TARGET"])
name = os.environ["PROJECT_NAME"]
vault = os.environ.get("VAULT_ROOT", "")


def write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# Claude Code reads autoMemoryDirectory from a settings file and requires an absolute path.
local = target / ".claude" / "settings.local.json"
data = {}
if local.exists():
    try:
        data = json.loads(local.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {local} is not valid JSON ({exc}); fix it, then re-run with --force")
data["autoMemoryDirectory"] = target.as_posix() + "/.claude/auto-memory"
write_lf(local, json.dumps(data, indent=2) + "\n")

if vault:
    worklog = Path(vault) / "DevProjects" / name / "Claude" / "TODO" / "Worklog.md"
    if worklog.exists():
        print(f"worklog kept: {worklog}")
    else:
        today = datetime.date.today().isoformat()
        write_lf(worklog, f"---\nlast_updated: {today}\n---\n\n# Worklog\n\n## Active\n\n## Linked Docs\n")
        print(f"worklog skeleton written: {worklog}")
EOF

echo
echo "Adoption self-check (python3 .claude/tools/baseline_sync.py doctor; re-run it after each step):"
( cd "$TARGET" && python3 .claude/tools/baseline_sync.py doctor ) || true

cat <<NEXT

Bootstrapped. Next steps in $TARGET_NATIVE:
  1. Resolve each pending or fail row above; the seed files it names are this project's to fill in.
  2. Review permissions in .claude/settings.project.json (project) or .claude/settings.local.json
     (this machine); run /sync_permissions later.
  3. In the first Claude session: /reindex_search. On a new machine, /workstation_setup provisions it
     (its toolchain phases when godot is adopted).
  4. Commit .claude/ including baseline.lock.json, and the root .gitignore.
NEXT
