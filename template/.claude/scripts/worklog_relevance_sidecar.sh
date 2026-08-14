#!/usr/bin/env bash
# worklog_relevance_sidecar.sh — the DeepSeek path of the worklog-relevance check.
#
# Sibling of .claude/workflows/worklog_relevance.js (the Anthropic path). BOTH read the same
# mandate, .claude/workflows/worklog_relevance.prompt.md — neither inlines its own copy. This
# wrapper only substitutes {{SCOPE}} and hands the file to deepseek_sidecar.sh (see that script's
# header for every flag below).
#
# The schema exists twice by necessity (JS object for the Workflow sandbox, .json file for
# --json-schema). Keep them in sync mechanically:
#     node .claude/scripts/schema_parity.js
#
# Usage:
#   worklog_relevance_sidecar.sh [-e EFFORT] [-m MODEL] [-D DOMAINS] [-R RECORD] [-T SECS] -- "SCOPE"
#   worklog_relevance_sidecar.sh -F scope.txt
#
#   -e  effort requested of the sidecar (default: low — the mandate is a converged spec)
#   -m  model                (default: flash, pinned EXPLICITLY rather than inherited —
#       this is a bounded classification task against a converged mandate, so the
#       expensive tier buys nothing and would cost ~3.1x on fresh tokens. Stating
#       it here makes the choice visible at the call site instead of implied.)
#   -D  comma-separated worklog domains to start the index scan from (advisory, not exhaustive)
#   -F  read the scope from a file instead of argv
#   -R  run-record path      (default: a temp file; -R is required for spend-ledger attribution)
#   -T  timeout seconds      (default: unset, per the sidecar)
#   -r  raw: print the sidecar's full JSON envelope instead of just the structured result
#
# Prints the structured `{"overlaps":[...]}` JSON on stdout; served model + cost on stderr.
# Exit codes: 0 ok · 2 bad usage · anything else propagated from deepseek_sidecar.sh.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANDATE="$REPO/.claude/workflows/worklog_relevance.prompt.md"
SCHEMA="$REPO/.claude/workflows/worklog_relevance.schema.json"
SIDECAR="$SCRIPT_DIR/deepseek_sidecar.sh"

EFFORT="low"
MODEL="flash"   # explicit, not inherited — see -m in the header
DOMAINS=""
SCOPE_FILE=""
RECORD=""
TIMEOUT=""
RAW=0

while getopts "e:m:D:F:R:T:r" opt; do
  case "$opt" in
    e) EFFORT="$OPTARG" ;;
    m) MODEL="$OPTARG" ;;
    D) DOMAINS="$OPTARG" ;;
    F) SCOPE_FILE="$OPTARG" ;;
    R) RECORD="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    r) RAW=1 ;;
    *) echo "bad usage; see header" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift

if [ -n "$SCOPE_FILE" ]; then
  [ -f "$SCOPE_FILE" ] || { echo "scope file not found: $SCOPE_FILE" >&2; exit 2; }
  SCOPE="$(cat "$SCOPE_FILE")"
else
  SCOPE="${*:-}"
fi
[ -n "$SCOPE" ] || { echo "empty scope" >&2; exit 2; }
[ -f "$MANDATE" ] || { echo "mandate missing: $MANDATE" >&2; exit 2; }
[ -f "$SCHEMA" ]  || { echo "schema missing: $SCHEMA" >&2; exit 2; }

PROMPT_TMP="$(mktemp)"
RECORD_TMP=""
if [ -z "$RECORD" ]; then RECORD_TMP="$(mktemp)"; RECORD="$RECORD_TMP"; fi
cleanup() { rm -f "$PROMPT_TMP"; [ -n "$RECORD_TMP" ] && rm -f "$RECORD_TMP"; }
trap cleanup EXIT

# Substitute via python3, not sed — a scope containing / & \ or newlines would corrupt a sed script.
MANDATE_V="$MANDATE" SCOPE_V="$SCOPE" DOMAINS_V="$DOMAINS" OUT_V="$PROMPT_TMP" python3 - <<'PYEOF'
import os
text = open(os.environ["MANDATE_V"], encoding="utf-8").read()
scope = os.environ["SCOPE_V"].strip()
domains = os.environ.get("DOMAINS_V", "").strip()
if domains:
    scope += ("\n\nStart the index scan from these worklog domains, but do not treat them as "
              "exhaustive: " + domains)
if "{{SCOPE}}" not in text:
    raise SystemExit("mandate has no {{SCOPE}} placeholder — the two paths would diverge")
open(os.environ["OUT_V"], "w", encoding="utf-8").write(text.replace("{{SCOPE}}", scope))
PYEOF
[ $? -eq 0 ] || exit 2

# The mandate reads the Obsidian worklog, which lives OUTSIDE the repo the sidecar uses as its
# working dir, and headless auto-denies out-of-grant paths. The grant is therefore the ONE vault
# subtree, via --add-dir (sidecar -a) — not `-p bypassPermissions`, which disarms the whole
# permission system to solve a single-path problem.
# The narrower grant is only safe if it actually lands: a silent miss makes the check return an
# empty overlap list, which reads as "nothing relevant" rather than as a failure. The mandate's
# `checked.obsidianOpened` field is the tripwire — treat false as a broken run, not a clean one.
VAULT_DIR="$HOME/OneDrive/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}"
[ -d "$VAULT_DIR" ] || { echo "vault dir missing: $VAULT_DIR — the grant would not land and the check would return a false empty" >&2; exit 2; }
ARGS=(-f "$PROMPT_TMP" -S "$SCHEMA" -t "Read,Glob,Grep" -a "$VAULT_DIR" -G survey
      -l worklog:relevance -R "$RECORD" -d "$REPO" -e "$EFFORT" -o json)
[ -n "$MODEL" ] && ARGS+=(-m "$MODEL")
[ -n "$TIMEOUT" ] && ARGS+=(-T "$TIMEOUT")

OUTPUT="$(bash "$SIDECAR" "${ARGS[@]}")"
rc=$?

if [ "$RAW" -eq 1 ]; then
  printf '%s\n' "$OUTPUT"
else
  OUT_V="$OUTPUT" python3 - <<'PYEOF'
import json, os, sys
try:
    data = json.loads(os.environ["OUT_V"])
except Exception:
    sys.stdout.write(os.environ["OUT_V"]); raise SystemExit(0)
result = data.get("result")
try:
    print(json.dumps(json.loads(result), indent=2))
except Exception:
    # Prose instead of JSON is a mandate failure, not a transport failure — surface it verbatim.
    print(result if result is not None else json.dumps(data, indent=2))
PYEOF
fi

if [ -s "$RECORD" ]; then
  REC_V="$RECORD" python3 - >&2 <<'PYEOF'
import json, os
try:
    r = json.load(open(os.environ["REC_V"], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
print("[worklog:relevance] served=%s effort=%s turns=%s cost=$%s schemaValid=%s" % (
    r.get("servedModel"), r.get("effort"), r.get("numTurns"), r.get("costUSD"), r.get("schemaValid")))
PYEOF
fi

exit $rc
