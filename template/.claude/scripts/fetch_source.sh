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
#
# A pinned source a layer adopts is a resolver, fetch_source.d/<name>.sh, run as `bash <resolver> <url>`
# before the network fetch. It exits 2 when the URL is not its own (notes on stderr are fine), or
# answers the URL: one manifest row on stdout, exit 0 when it landed OK and 1 when it refused.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESOLVERS="$(dirname "${BASH_SOURCE[0]}")/fetch_source.d"

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

  answered=""
  for resolver in "$RESOLVERS"/*.sh; do
    [[ -f "$resolver" ]] || continue
    bash "$resolver" "$url"; r=$?
    [[ $r -eq 2 ]] && continue
    answered=1; [[ $r -eq 0 ]] || rc=1
    break
  done
  [[ -n "$answered" ]] && continue

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
