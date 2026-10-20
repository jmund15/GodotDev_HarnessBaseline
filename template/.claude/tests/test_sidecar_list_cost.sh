#!/usr/bin/env bash
# Proof that a plan-quota sidecar run records a LIST-price costUSD marked as not spend, that the
# cache-write tokens are recorded and priced, and that a marginal-usd run is unchanged.
# The registry is planted (HARNESS_MODEL_REGISTRY) so no rate here rots with the live roster.
# SIDECAR_LIB overrides the library under test, for the RED run against the pre-change copy.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
LIB="${SIDECAR_LIB:-$SCRIPTS/lib/sidecar_common.sh}"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
fail=0; ran=0
ok()  { ran=$((ran + 1)); echo "OK   $1"; }
bad() { ran=$((ran + 1)); echo "FAIL $1"; fail=1; }
expect() { if [ "$2" = "$3" ]; then ok "$1 -> $3"; else bad "$1: expected '$2', got '$3'"; fi; }

tmpd="$(mktemp -d)"
winpath() { cygpath -w "$1" 2>/dev/null || echo "$1"; }
python3 - "$(winpath "$tmpd/registry.json")" "$(winpath "$HERE/../reference/external_models.json")" <<'PY'
import json, sys
root = json.load(open(sys.argv[2], encoding="utf-8"))
for row in root["models"]:
    if row["id"] == "gpt-6.1-sol":
        row.update(id="zz-plan-a", alias="zz-plan-a-alias", version="zz-plan-a", family="zz-plan-a")
        row["price"] = dict(cacheMissPer1M=2, cacheHitPer1M=0.1, cacheWritePer1M=2.5, outputPer1M=10,
                            currency="USD", asOf="2026-09-30", source="https://example.test/pricing")
    if row["id"] == "gpt-6-astra":
        row.pop("price", None)
        row["priceGap"] = {"reason": "planted gap", "checked": "https://example.test", "asOf": "2026-09-30"}
root["aliasHistory"] = {}
json.dump(root, open(sys.argv[1], "w", encoding="utf-8"))
PY
export HARNESS_MODEL_REGISTRY="$(winpath "$tmpd/registry.json")"

record() {  # transport model label payload -> record path
  local transport="$1" model="$2" label="$3" payload="$4" rec="$tmpd/$3.record.json"
  ( SC_TRANSPORT="$transport"; . "$LIB" >/dev/null 2>&1
    sc_parse_flags -m "$model" -R "$rec" -L "" -l "$label" >/dev/null 2>&1
    sc_resolve_model >/dev/null 2>&1
    SC_WORKDIR="$tmpd"
    sc_write_record "$payload" 0 >/dev/null 2>&1 )
  echo "$rec"
}
field() {  # record json-expr
  python3 -c 'import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8")); print(eval(sys.argv[2]))' "$(winpath "$1")" "$2" 2>&1
}

sol='{"type":"result","session_id":"s","num_turns":1,"result":"ok","usage":{"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0}},"modelUsage":{"zz-plan-a":{"inputTokens":1000000,"cacheReadInputTokens":1000000,"cacheCreationInputTokens":1000000,"outputTokens":1000000,"canonicalModel":"zz-plan-a"}}}'
rec="$(record codex zz-plan-a-alias quota "$sol")"
expect "plan-quota run: list-price costUSD (2 + 0.1 + 2.5 + 10)" "14.6" "$(field "$rec" 'r["costUSD"]')"
expect "plan-quota run: costBasis says list-price, not spend" "list-price (plan-quota; not spend)" "$(field "$rec" 'r["costBasis"]')"
expect "plan-quota run: costModel still plan-quota" "plan-quota" "$(field "$rec" 'r["costModel"]')"
expect "record carries cacheWriteTokens" "1000000" "$(field "$rec" 'r["cacheWriteTokens"]')"

flash='{"type":"result","session_id":"s","num_turns":1,"result":"ok","modelUsage":{"deepseek-flash":{"inputTokens":1000000,"cacheReadInputTokens":0,"outputTokens":1000000,"canonicalModel":"deepseek-flash"}}}'
rec="$(record deepseek flash paid "$flash")"
expect "marginal-usd run keeps a registry-id costBasis" "deepseek-flash" "$(field "$rec" 'r["costBasis"]')"
expect "marginal-usd run still records its cost" "True" "$(field "$rec" 'r["costUSD"] > 0')"

astra='{"type":"result","session_id":"s","num_turns":1,"result":"ok","modelUsage":{"gpt-6-astra":{"inputTokens":10,"outputTokens":10,"canonicalModel":"gpt-6-astra"}}}'
rec="$(record codex gpt-6-astra gap "$astra")"
expect "plan-quota run on a priceGap row: null costUSD" "None" "$(field "$rec" 'r["costUSD"]')"
expect "plan-quota run on a priceGap row: basis names the gap" "True" "$(field "$rec" '"planted gap" in r["costBasis"]')"

rm -rf "$tmpd"
[ "$ran" -ge 8 ] || { echo "FAIL: only $ran cases ran"; exit 1; }
[ "$fail" -eq 0 ] && echo "PASS $ran cases" || echo "FAILED"
exit "$fail"
