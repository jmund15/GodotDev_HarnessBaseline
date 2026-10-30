#!/usr/bin/env bash
# A direct opencode launch (no SIDECAR_LAUNCH_ID from a fan-out) makes a launch id the shared
# record check accepts, and its Go session header keeps the 32-hex shape. No network call.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
. "$R/scripts/lib/sidecar_common.sh"
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
fail=0

# A synthetic Go row cloned from a live messages-mode row's shape, so a roster change never
# breaks this proof.
python3 - "$R/reference/external_models.json" "$D/registry.json" <<'PY'
import copy, json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
base = next(m for m in data["models"] if m["transport"] == "go" and m.get("apiMode") == "messages")
row = copy.deepcopy(base)
row.update(id="probe-launch-model", alias="probe-launch", upstreamId="probe-launch-model",
           family="probe-launch", order=max(m["order"] for m in data["models"]) + 1)
row.pop("servedIds", None)
data["models"].append(row)
json.dump(data, open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PY
export HARNESS_MODEL_REGISTRY="$D/registry.json"

eval "$(sed -n '/^oc_select_row() {/,/^}/p' "$R/scripts/opencode_sidecar.sh")"
unset SIDECAR_LAUNCH_ID
SC_LAUNCH_ID=""
SC_MODEL="probe-launch"
SC_CREDENTIAL="stub-key"
ENV_FILE="$D/.env.ai-worker.cmd"
if oc_select_row; then
  echo "OK   oc_select_row resolves the Go row"
else
  echo "FAIL oc_select_row rc=$?"; exit 1
fi

SC_RECORD="$D/direct.record.json"
SC_LABEL="launch-id-test"
SC_PARENT_SESSION_ID="parent"
SC_LAUNCH_PREPARED=0
SC_INVENTORY_SWEEP_LIMIT=3
SC_EFFORT="max"
SC_COST_MODEL="plan-quota"
SC_DISCLOSURE="full"
SC_LEDGER=""
SC_RECORD_PARSER="claude"
SC_SCHEMA_FILE=""
prep_rc=0
sc_prepare_record 2>"$D/prepare.err" || prep_rc=$?
if [ "$prep_rc" -eq 0 ] && ! grep -q "invalid launch identity" "$D/prepare.err"; then
  echo "OK   the shared record check accepts a direct launch's id"
else
  echo "FAIL sc_prepare_record rc=$prep_rc: $(tr '\n' ' ' < "$D/prepare.err" | cut -c1-200)"; fail=1
fi

if printf '%s' "$OC_SESSION_ID" | grep -Eq '_[0-9a-f]{32}$' && [ "${OC_SESSION_ID##*_}" = "${SC_LAUNCH_ID//-/}" ]; then
  echo "OK   the Go session header carries the launch id as 32 hex"
else
  echo "FAIL session header shape: $OC_SESSION_ID"; fail=1
fi

STUB_ID="12345678-1234-4234-8234-123456789abc"
sc_ensure_launch_id() { SC_LAUNCH_ID="$STUB_ID"; export SIDECAR_LAUNCH_ID="$SC_LAUNCH_ID"; }
SC_LAUNCH_ID=""
SC_LAUNCH_PREPARED=0
SC_RECORD="$D/stub.record.json"
sc_prepare_record 2>/dev/null
if [ "$SC_LAUNCH_ID" = "$STUB_ID" ]; then
  echo "OK   sc_prepare_record takes an unset launch id from sc_ensure_launch_id"
else
  echo "FAIL sc_prepare_record left SC_LAUNCH_ID='$SC_LAUNCH_ID' (a second generator)"; fail=1
fi
exit "$fail"
