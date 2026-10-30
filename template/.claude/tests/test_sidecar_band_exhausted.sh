#!/usr/bin/env bash
# Migration proof: provider quota and balance now have one normalized capacity gate.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
FAIL=0; PASS=0
ok() { echo "OK   $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL $1"; FAIL=$((FAIL+1)); }

legacy='^sc_gate_provider_band\(\)|^sc_gate_balance\(\)|(^|[^A-Z_])SC_BALANCE_URL([^A-Z_]|$)'
if grep -E "$legacy" "$R/scripts/lib/sidecar_common.sh" >/dev/null; then
  bad "legacy provider/balance gates are absent from sidecar_common"
else
  ok "legacy provider/balance gates are absent from sidecar_common"
fi
if grep -E '^sc_gate_(provider_band|balance)' "$R/scripts/"*_sidecar.sh >/dev/null; then
  bad "every launcher uses the unified capacity gate"
else
  ok "every launcher uses the unified capacity gate"
fi
if grep -E '"(quotaProbe|balanceUrl)"' "$R/reference/external_models.json" >/dev/null; then
  bad "registry retired quotaProbe and balanceUrl"
else
  ok "registry retired quotaProbe and balanceUrl"
fi
if grep -E 'def quota_probe_for' "$R/tools/model_registry.py" >/dev/null; then
  bad "registry API retired quota_probe_for"
else
  ok "registry API retired quota_probe_for"
fi

LIB="$R/scripts/lib/sidecar_common.sh"
SC_TRANSPORT=go
. "$LIB"
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
export SIDECAR_EXHAUSTED_DIR="$TMPD/markers"
SC_TRANSPORT_STATE=available
SC_ALIAS=go-kimi
SC_MODEL=go-alpha
SC_USAGE_LIMIT_RESET="$(( $(date +%s) + 900 ))"
SC_USAGE_LIMIT_SID=""
SC_USAGE_LIMITED=1
SC_LAUNCH_ID=""
OUTPUT='{"type":"result","is_error":true,"error":{"type":"UsageLimitError","scope":"model","model":"go-alpha"}}'
rc=1
sc_finish_usage_limit 2>/dev/null
[ "$rc" = 10 ] && [ -f "$SIDECAR_EXHAUSTED_DIR/go.json" ] \
  && [ -z "$(ls "$SIDECAR_EXHAUSTED_DIR" | grep -v '^go\.json$')" ] \
  && ok "a Go limit naming one model still marks the whole transport" \
  || bad "a Go limit naming one model still marks the whole transport"
_out="$( (SC_MODEL=go-beta; sc_gate_exhausted; printf 'allowed') 2>&1 )"; _rc=$?
[ "$_rc" = 10 ] && ok "a Go limit naming one model refuses every Go model" \
  || bad "a Go limit naming one model refuses every Go model"
rm -f "$SIDECAR_EXHAUSTED_DIR/go.json"

OUTPUT='{"type":"result","is_error":true,"error":{"type":"UsageLimitError","scope":"account","model":"go-alpha"}}'
rc=1
sc_finish_usage_limit 2>/dev/null
[ -f "$SIDECAR_EXHAUSTED_DIR/go.json" ] && ok "account-wide error marks the whole Go transport" \
  || bad "account-wide error marks the whole Go transport"
_out="$( (SC_MODEL=go-beta; sc_gate_exhausted; printf 'allowed') 2>&1 )"; _rc=$?
[ "$_rc" = 10 ] && ok "account-wide marker refuses another Go model" \
  || bad "account-wide marker refuses another Go model"
rm -f "$SIDECAR_EXHAUSTED_DIR/go.json"
OUTPUT='{"type":"result","is_error":true,"result":"limit with no scope"}'
rc=1
sc_finish_usage_limit 2>/dev/null
[ -f "$SIDECAR_EXHAUSTED_DIR/go.json" ] && ok "unclassifiable limit fails closed for the transport" \
  || bad "unclassifiable limit fails closed for the transport"
python3 - "$SIDECAR_EXHAUSTED_DIR/go.json" <<'PY'
import json, sys, time
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
data["expiresAt"] = int(time.time()) - 1
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh)
PY
_out="$( (SC_MODEL=go-beta; sc_gate_exhausted; printf 'allowed') 2>&1 )"; _rc=$?
[ "$_rc" = 0 ] && [ "$_out" = allowed ] && ok "transport marker expires" \
  || bad "transport marker expires"

mkdir -p "$TMPD/tools"
cat > "$TMPD/tools/provider_capacity.py" <<'PY'
import json
print(json.dumps({"status": "available", "quota": {
    "routingBand": "Hot", "routingWindow": "thirty_day", "bindingWindow": "thirty_day",
    "windows": [{"name": "seven_day", "pressure": 0.1},
                {"name": "thirty_day", "pressure": 1.9}],
}}))
PY
_out="$( (SC_ROOT="$TMPD"; SC_REGISTRY_CLI="$R/tools/model_registry.py";
          SC_MIN_BAND=Hot; SC_AUTHORIZED=0; CLAUDE_CODE_TRANSPORT=go;
          sc_gate_band; printf '%s|%s' "$SC_BAND" "$SC_BAND_P") 2>&1 )"; _rc=$?
[ "$_rc" = 0 ] && [ "$_out" = 'Hot|1.9' ] && ok "host band shows its routing window's pressure" \
  || bad "host band shows its routing window's pressure"

echo
echo "capacity migration: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
