#!/usr/bin/env bash
# Deterministic web transport: land raw bytes on disk with no model in the loop.
#
# WHY: every other fetch path puts a model between you and the page — WebFetch "answers `prompt`
# against it using a small fast model"; read_web summarizes and silently truncates (measured:
# 101,558 bytes -> 71,478 extracted). Neither can be audited, because the quote you would check is
# produced by the same layer that could have mangled it. This lands bytes, so a claimed quote
# becomes checkable (`.claude/tools/verify_claims.py`).
#
# Costs zero plan quota and zero sidecar dollars. Prefer it whenever a claim will quote the source.
#
#   fetch_source.sh <url> [<url> ...]
#   fetch_source.sh --dir <outdir> <url> ...
#
# Emits a TSV manifest to stdout: status<TAB>http<TAB>bytes<TAB>artifact<TAB>url
# `artifact` is `-` for any non-OK row. The URL->artifact mapping is what lets verify_claims.py
# join a claim's cited URL to the local bytes; without it the verifier cannot resolve its own input.
#
# Exit 0 only if EVERY url landed OK. Any failure exits 1 — a partially-fetched set must never read
# as complete, which is the silent-degradation class this whole effort removes.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK="$REPO_ROOT/.claude/reference/project_stack.md"
CACHE="$REPO_ROOT/.claude/cache/godot-docs/doc/classes"

OUTDIR="${TMPDIR:-/tmp}/claude-sources"
if [[ "${1:-}" == "--dir" ]]; then OUTDIR="$2"; shift 2; fi
[[ $# -gt 0 ]] || { echo "usage: fetch_source.sh [--dir <outdir>] <url> ..." >&2; exit 64; }
mkdir -p "$OUTDIR"

# A flat truncation loses the trailing extension on deeply-nested paths, so an extension-based
# glob skips an artifact that landed fine — silent invisibility, not a fetch failure. It also lets
# two URLs sharing a long prefix collide onto one path, where the second silently overwrites the
# first. Over-length slugs therefore keep the extension and carry a hash of the FULL url.
slugify() {
  local s ext hash
  s="$(printf '%s' "$1" | sed -E 's#^https?://##; s#[^A-Za-z0-9._-]#_#g')"
  if [[ ${#s} -le 120 ]]; then printf '%s' "$s"; return; fi
  ext=""; [[ "$s" =~ (\.[A-Za-z0-9]{1,8})$ ]] && ext="${BASH_REMATCH[1]}"
  hash="$(printf '%s' "$1" | md5sum 2>/dev/null | cut -c1-8)"
  [[ -n "$hash" ]] || hash="$(printf '%s' "$1" | cksum | tr -d ' \n' | cut -c1-8)"
  printf '%s_%s%s' "${s:0:100}" "$hash" "$ext"
}

# Git Bash resolves paths to /c/Users/... which Python and Windows tooling cannot open. The manifest
# is consumed by verify_claims.py, so every artifact path must round-trip outside the shell.
winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

rc=0
for url in "$@"; do
  slug="$(slugify "$url")"; art="$OUTDIR/$slug"

  # Godot CLASS reference always resolves from the cache, never the network — /en/stable/ is a
  # moving alias, so the rendered page cannot be pinned to the engine, and the cache holds the very
  # XML that page is generated from at the exact pin. Non-class Godot URLs (tutorials, guides) DO
  # fetch: the host is only intermittently Cloudflare-gated, so refusing them outright throws away
  # a source that usually works.
  if [[ "$url" == *docs.godotengine.org* ]]; then
    cls=""
    [[ "$url" =~ class_([a-z0-9_]+)\.html ]] && cls="${BASH_REMATCH[1]}"
    if [[ -n "$cls" ]]; then
      hit=""
      [[ -d "$CACHE" ]] && hit="$(find "$CACHE" -iname "${cls}.xml" | head -1)"
      if [[ -n "$hit" ]]; then
        printf 'OK\tcache\t%s\t%s\t%s\n' "$(wc -c < "$hit" | tr -d ' ')" "$(winpath "$hit")" "$url"
        continue
      fi
      printf 'BLOCKED\t-\t0\t-\t%s\n' "$url"
      echo "fetch_source: class '$cls' is not in the version-pinned cache, and the rendered page" >&2
      echo "  cannot be pinned to the engine. Build the cache: .claude/scripts/godot_docs_cache.sh" >&2
      rc=1; continue
    fi
    echo "fetch_source: note — docs.godotengine.org is intermittently Cloudflare-gated; on a" >&2
    echo "  CHALLENGE or non-2xx below, fall back to context7 /websites/godotengine_en_4_7." >&2
  fi

  code="$(curl -sSL --max-time 45 -A 'Mozilla/5.0 (compatible; {{PROJECT_NAME}}-harness)' \
          -o "$art.part" -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
  bytes=0; [[ -f "$art.part" ]] && bytes="$(wc -c < "$art.part" | tr -d ' ')"

  # A non-2xx still has a body — a Cloudflare interstitial, a 404 page, a login wall. Writing it to
  # the artifact path would hand a challenge page to a grep as though it were documentation.
  if [[ "$code" != 2?? ]]; then
    rm -f "$art.part"
    printf 'HTTP_%s\t%s\t0\t-\t%s\n' "$code" "$code" "$url"
    rc=1; continue
  fi

  # 2xx interstitials exist too — bot-check pages return 200 with a challenge body.
  if head -c 4000 "$art.part" | grep -qiE 'Just a moment|Checking your browser|cf-browser-verification|Enable JavaScript and cookies to continue'; then
    rm -f "$art.part"
    printf 'CHALLENGE\t%s\t0\t-\t%s\n' "$code" "$url"
    echo "fetch_source: bot-challenge body at $url — treat as unfetched, not as content." >&2
    rc=1; continue
  fi

  mv "$art.part" "$art"
  printf 'OK\t%s\t%s\t%s\t%s\n' "$code" "$bytes" "$(winpath "$art")" "$url"
done

exit $rc
