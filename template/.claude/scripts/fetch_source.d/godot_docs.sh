#!/usr/bin/env bash
# fetch_source.sh resolver: Godot CLASS reference always resolves from the version-pinned cache,
# never the network — /en/stable/ is a moving alias, so the rendered page cannot be pinned to the
# engine, and the cache holds the very XML that page is generated from at the exact pin. Non-class
# Godot URLs (tutorials, guides) DO fetch: the host is only intermittently Cloudflare-gated, so
# refusing them outright throws away a source that usually works.
#
# Contract (fetch_source.sh header): exit 2 = not this resolver's URL; otherwise one manifest row,
# exit 0 = OK, exit 1 = refused.
set -uo pipefail

url="$1"
[[ "$url" == *docs.godotengine.org* ]] || exit 2

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CACHE="$REPO_ROOT/.claude/cache/godot-docs/doc/classes"
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

cls=""
[[ "$url" =~ class_([a-z0-9_]+)\.html ]] && cls="${BASH_REMATCH[1]}"
if [[ -z "$cls" ]]; then
  echo "fetch_source: note — docs.godotengine.org is intermittently Cloudflare-gated; on a" >&2
  echo "  CHALLENGE or non-2xx below, fall back to context7 /websites/godotengine_en_4_7." >&2
  exit 2
fi

hit=""
[[ -d "$CACHE" ]] && hit="$(find "$CACHE" -iname "${cls}.xml" | head -1)"
if [[ -n "$hit" ]]; then
  printf 'OK\tcache\t%s\t%s\t%s\n' "$(wc -c < "$hit" | tr -d ' ')" "$(winpath "$hit")" "$url"
  exit 0
fi
printf 'BLOCKED\t-\t0\t-\t%s\n' "$url"
echo "fetch_source: class '$cls' is not in the version-pinned cache, and the rendered page" >&2
echo "  cannot be pinned to the engine. Build the cache: .claude/scripts/godot_docs_cache.sh" >&2
exit 1
