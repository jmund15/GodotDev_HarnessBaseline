#!/usr/bin/env bash
# Proof for the shared provider-capacity gate in scripts/lib/sidecar_common.sh.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "CANNOT RUN: $LIB missing"; exit 2; }
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/tools"
cat > "$ROOT/tools/provider_capacity.py" <<'PY'
import os,sys
mode=os.environ.get("CAP_MODE", "available")
if mode == "invalid": print("not-json"); sys.exit(0)
if mode == "runner-fail": sys.exit(2)
print(os.environ["CAP_JSON"])
PY

PASS=0; FAIL=0
ok() { echo "OK   $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL $1: $2"; FAIL=$((FAIL+1)); }

result() { # <status> <dispatchBand> <sourceKind>
  python3 - "$1" "$2" "$3" <<'PY'
import json,sys
status,band,source=sys.argv[1:]
quota=None
balance=None
if status in ("available","exhausted"):
 quota={"windows":[{"name":"seven_day","usedPercent":100 if status=="exhausted" else 50,"resetsAt":1800000000,"windowDurationMins":10080,"pressure":1.0,"band":band}],"dispatchBand":band,"routingBand":band,"bindingWindow":"seven_day","credits":{"enabled":False,"available":False,"balance":None}}
if status == "insufficient": balance={"amount":0.0,"currency":"USD"}
error=None
if status in ("auth-error","network-error","malformed"):
 error={"kind":{"auth-error":"auth","network-error":"network","malformed":"malformed"}[status],"message":status}
print(json.dumps({"schemaVersion":1,"transport":"fake","costModel":"plan-quota","sourceKind":source,"observedAt":"2027-01-15T08:00:00Z","status":status,"error":error,"liveFailure":None,"quota":quota,"balance":balance}))
PY
}

gate() { # <status> <band> <source> <cost> <floorBalance> [authorized] [mode]
  local status="$1" band="$2" source="$3" cost="$4" floor="$5" authorized="${6:-0}" mode="${7:-available}"
  local payload dir err rc
  payload="$(result "$status" "$band" "$source")"
  dir="$(mktemp -d)"; err="$dir/err"
  ( set +u
    . "$LIB" >/dev/null 2>&1
    set -u
    SC_ROOT="$ROOT"; SC_TRANSPORT=fake; SC_ALIAS=fake; SC_MODEL=fake-model
    SC_COST_MODEL="$cost"; SC_MIN_BAND=Surplus
    SC_MIN_BALANCE="$floor"; SC_AUTHORIZED="$authorized"; SC_CREDENTIAL=""
    CAP_JSON="$payload" CAP_MODE="$mode" sc_gate_capacity
  ) >"$dir/out" 2>"$err"
  rc=$?
  printf '%s|%s' "$rc" "$(cat "$err")"
}

expect_rc() { local label="$1" want="$2"; shift 2; local got; got="$(gate "$@")"; [ "${got%%|*}" = "$want" ] && ok "$label" || bad "$label" "$got"; }

host_gate() { # <status> <band> <floor> [authorized] [mode]
  local status="$1" band="$2" floor="$3" authorized="${4:-0}" mode="${5:-available}"
  local payload dir err rc
  payload="$(result "$status" "$band" live)"
  dir="$(mktemp -d)"; err="$dir/err"
  ( set +u
    . "$LIB" >/dev/null 2>&1
    set -u
    SC_ROOT="$ROOT"; SC_TRANSPORT=deepseek; SC_ALIAS=fake; SC_MODEL=fake-model
    SC_COST_MODEL=marginal-usd; SC_MIN_BAND="$floor"
    SC_MIN_BALANCE=0; SC_AUTHORIZED="$authorized"; SC_CREDENTIAL=""
    CLAUDE_CODE_TRANSPORT=fake CAP_JSON="$payload" CAP_MODE="$mode" sc_gate_band
  ) >"$dir/out" 2>"$err"
  rc=$?
  printf '%s|%s' "$rc" "$(cat "$err")"
}

expect_host_rc() { local label="$1" want="$2"; shift 2; local got; got="$(host_gate "$@")"; [ "${got%%|*}" = "$want" ] && ok "$label" || bad "$label" "$got"; }

expect_rc "available quota passes" 0 available "On pace" live plan-quota 0
expect_rc "exhausted quota refuses with 10" 10 exhausted Exhausted live plan-quota 0
expect_rc "-A cannot override exhaustion" 10 exhausted Exhausted live plan-quota 0 1
expect_rc "insufficient balance refuses with 6" 6 insufficient "On pace" live marginal-usd 1
expect_rc "auth failure refuses with 3" 3 auth-error "On pace" live plan-quota 0
expect_rc "network failure refuses with 8" 8 network-error "On pace" live plan-quota 0
expect_rc "malformed provider result refuses with 8" 8 malformed "On pace" live plan-quota 0
expect_rc "unsupported amount passes at zero floor" 0 unsupported "On pace" unsupported marginal-usd 0
expect_rc "unsupported amount refuses at positive floor" 8 unsupported "On pace" unsupported marginal-usd 1
# One plan-quota rule for every window: exhausted (10) or an unreadable band (8), never a per-row
# band ceiling. A Hot codex window is the case the retired ceiling refused while the native route
# kept taking work at the same pressure.
expect_rc "a Hot plan-quota band passes: no per-row ceiling" 0 available Hot live plan-quota 0
expect_rc "...with or without -A: the ceiling it overrode is gone" 0 available Hot live plan-quota 0 1
expect_rc "a plan-quota window with no readable band refuses with 8" 8 available "" live plan-quota 0
expect_rc "runner failure refuses with 8" 8 available "On pace" live plan-quota 0 0 runner-fail
expect_rc "invalid JSON refuses with 8" 8 available "On pace" live plan-quota 0 0 invalid

# A launcher `--check` itself: sc_check_gates runs the same refusal gates a dispatch runs, with the
# REAL registry resolving a codex row and the planted runner answering capacity. The exhausted-marker
# dir is pointed at an empty one so a live marker on this machine cannot decide the case.
check_gates() { # <status> <band> [authorized]
  local status="$1" band="$2" authorized="${3:-0}"
  local payload dir err rc
  payload="$(result "$status" "$band" live)"
  dir="$(mktemp -d)"; err="$dir/err"
  ( set +u
    . "$LIB" >/dev/null 2>&1
    set -u
    # SC_TRANSPORT is what the codex launcher sets for itself; everything else comes from the
    # REAL registry row through sc_resolve_model.
    SC_ROOT="$ROOT"; SC_TRANSPORT=codex; SC_MODEL=sol
    SC_AUTHORIZED="$authorized"; SC_CREDENTIAL=""
    SC_UNSUSPEND=0; SIDECAR_EXHAUSTED_DIR="$dir/no-marker"; CLAUDE_CODE_TRANSPORT=anthropic
    CAP_JSON="$payload" CAP_MODE=available sc_check_gates
  ) >"$dir/out" 2>"$err"
  rc=$?
  printf '%s|%s' "$rc" "$(cat "$err")"
}
expect_check_rc() { local label="$1" want="$2"; shift 2; local got; got="$(check_gates "$@")"; [ "${got%%|*}" = "$want" ] && ok "$label" || bad "$label" "$got"; }

expect_check_rc "codex --check with a Hot planted band passes" 0 available Hot
expect_check_rc "codex --check with a planted exhausted window refuses with 10" 10 exhausted Exhausted
expect_check_rc "...and -A does not lift exhaustion either" 10 exhausted Exhausted 1

expect_host_rc "live host Hot band satisfies a paid floor" 0 available Hot "On pace"
expect_host_rc "live host Surplus band refuses a paid floor" 5 available Surplus "On pace"
expect_host_rc "an exhausted host plan authorizes off-quota work" 0 exhausted Exhausted "On pace"
expect_host_rc "unsupported host amount refuses a paid floor" 5 unsupported Surplus "On pace"
expect_host_rc "-A overrides an unsupported host amount deliberately" 0 unsupported Surplus "On pace" 1
expect_host_rc "host capacity runner failure refuses" 5 available Hot "On pace" 0 runner-fail

mv "$ROOT/tools/provider_capacity.py" "$ROOT/tools/provider_capacity.py.off"
expect_rc "missing capacity runner refuses with 8" 8 available "On pace" live plan-quota 0

echo
echo "sidecar capacity gate: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
