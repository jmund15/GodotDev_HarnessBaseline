#!/usr/bin/env bash
# Populate/refresh the version-pinned Godot class-reference cache.
#
# WHY THIS EXISTS: docs.godotengine.org sits behind Cloudflare bot verification and returns
# HTTP 429 to curl AND to WebFetch, host-wide. It is also unpinnable — `/en/stable/` is a moving
# alias, while `source_trust.md` rule 3 requires the claim name its version. This cache resolves
# both: `doc/classes/*.xml` is the first-party source the HTML reference is GENERATED from, tagged
# to the exact engine pin.
#
# Fetch is a blobless sparse clone: ~1.5s for 810 files, and atomic — git either produces a
# consistent tree or fails. A per-file curl loop could leave a half-populated cache that reads
# as complete, which is the silent-degradation class this whole effort exists to remove.
#
#   godot_docs_cache.sh            ensure the cache matches the pin (no-op if already correct)
#   godot_docs_cache.sh --force    rebuild from scratch
#   godot_docs_cache.sh --check    exit 0 in sync, 2 stale/absent; never writes
#
# Version SSOT: .claude/reference/project_stack.md (never hardcode the pin here).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK="$REPO_ROOT/.claude/reference/project_stack.md"
CACHE="$REPO_ROOT/.claude/cache/godot-docs"
STAMP="$CACHE/.stamp"
INDEX="$REPO_ROOT/.claude/reference/godot_class_index.md"
GEN="$REPO_ROOT/.claude/tools/gen_godot_class_index.py"
UPSTREAM="https://github.com/godotengine/godot"

die() { echo "godot_docs_cache: $*" >&2; exit 1; }

[[ -f "$STACK" ]] || die "missing version SSOT: $STACK"

# project_stack.md line: `- Godot engine: **4.7.1** stable mono`
PIN="$(grep -oE 'Godot engine:[^0-9]*([0-9]+\.[0-9]+(\.[0-9]+)?)' "$STACK" \
       | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)"
[[ -n "$PIN" ]] || die "could not parse the Godot pin from $STACK"
TAG="${PIN}-stable"

mode="${1:-ensure}"
current=""
[[ -f "$STAMP" ]] && current="$(head -1 "$STAMP" | tr -d '[:space:]')"

# A stamp without a populated tree is a lie — treat it as absent.
count=0
[[ -d "$CACHE/doc/classes" ]] && count="$(find "$CACHE/doc/classes" -name '*.xml' | wc -l | tr -d ' ')"
[[ "$count" -gt 0 ]] || current=""

if [[ "$mode" == "--check" ]]; then
  if [[ "$current" == "$PIN" ]]; then
    echo "in-sync $PIN ($count classes)"; exit 0
  fi
  echo "STALE cache='${current:-absent}' pin='$PIN'"; exit 2
fi

if [[ "$mode" != "--force" && "$current" == "$PIN" ]]; then
  echo "godot_docs_cache: already at $PIN ($count classes) — nothing to do"
  exit 0
fi

echo "godot_docs_cache: building cache for $TAG ..."
mkdir -p "$(dirname "$CACHE")"

# Build into a sibling, swap on success — a failed refresh must never destroy a good cache.
STAGE="${CACHE}.staging.$$"
cleanup() { [[ -d "$STAGE" ]] && rm -rf "$STAGE" || true; }
trap cleanup EXIT

git -c advice.detachedHead=false clone --filter=blob:none --sparse --depth 1 \
    --branch "$TAG" --quiet "$UPSTREAM" "$STAGE" \
  || die "clone failed for tag '$TAG' — does that tag exist upstream? (pin from $STACK)"
git -C "$STAGE" sparse-checkout set doc/classes >/dev/null \
  || die "sparse-checkout of doc/classes failed"

staged="$(find "$STAGE/doc/classes" -name '*.xml' | wc -l | tr -d ' ')"
[[ "$staged" -gt 100 ]] || die "only $staged class files fetched — refusing to install a partial cache"

# Index must generate BEFORE the swap, so a generator failure cannot leave cache and index disagreeing.
python "$GEN" "$STAGE/doc/classes" "$INDEX" "$PIN" || die "index generation failed"

echo "$PIN" > "$STAGE/.stamp"
[[ -d "$CACHE" ]] && rm -rf "$CACHE"
mv "$STAGE" "$CACHE"
trap - EXIT

echo "godot_docs_cache: installed $staged classes at $PIN"
echo "  cache: $CACHE/doc/classes/<Class>.xml   (gitignored)"
echo "  index: $INDEX                            (committed, semantic-searchable)"
