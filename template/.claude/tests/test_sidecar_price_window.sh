#!/usr/bin/env bash
# Proof for the time-of-day price gate and declared effort vocabulary in lib/sidecar_common.sh:
#   1. -W parses as a bare flag and swallows no neighbour (-A -W -R -l, and the -AW cluster);
#   2. sc_gate_price_window refuses a peak dispatch on a peakPolicy:refuse row with exit 11,
#      unless -W; -A alone does not bypass it; off-peak and unscheduled transports pass;
#   3. check mode prints ONE stdout line naming the window and exits 11;
#   4. sc_validate_effort refuses a rung the transport does not serve, and only there;
#   5. the run record carries priceWindow, priceMultiplier, modelVersion and a window-priced costUSD;
#   6. every launcher calls the gate right after sc_gate_balance.
# Times are pinned with SC_PRICE_AT: 2026-09-16 02:00Z is a Wednesday peak, 05:00Z off-peak.
# Every case must produce output; a zero-match run is a failure, never a pass.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
LIB="$SCRIPTS/lib/sidecar_common.sh"
[ -f "$LIB" ] || { echo "FAIL: lib missing at $LIB"; exit 1; }
PEAK="2026-09-16T02:00:00Z"
OFF="2026-09-16T05:00:00Z"
fail=0
ran=0

ok()   { ran=$((ran + 1)); echo "OK   $1"; }
bad()  { ran=$((ran + 1)); echo "FAIL $1"; fail=1; }
expect() { if [ "$2" = "$3" ]; then ok "$1 -> $3"; else bad "$1: expected '$2', got '$3'"; fi; }

# ---- 1. flag parsing ---------------------------------------------------------------------------
# A parsed -R arms the durable exit-file trap, which writes <record>.exit on subshell exit. The
# record here is relative, so the probe runs in its own scratch dir and never writes the caller's cwd.
parse_dir="$(mktemp -d)"
cwd_exit_before="$(ls -la rec.exit 2>/dev/null)"
parsed() {  # flags... -> "A W R l"
  ( cd "$parse_dir" || exit 99
    SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
    sc_parse_flags "$@" >/dev/null 2>&1
    printf '%s %s %s %s' "$SC_AUTHORIZED" "${SC_PEAK_AUTHORIZED:-unset}" "${SC_RECORD:-none}" "${SC_LABEL:-none}" )
}
expect "-A -W -R rec -l lab parse independently" "1 1 rec lab" "$(parsed -A -W -R rec -l lab)"
expect "-AW cluster sets both"                   "1 1 none none" "$(parsed -AW)"
expect "no -W leaves peak authorization off"     "1 0 rec none" "$(parsed -A -R rec)"
expect "parse probes leave the caller's cwd untouched" "$cwd_exit_before" "$(ls -la rec.exit 2>/dev/null)"
rm -rf "$parse_dir"

# ---- 2/3. the gate -----------------------------------------------------------------------------
gate() {  # $1 transport, $2 model, $3 at, flags... -> "rc|window|mult" line, then the gate's stderr
  local transport="$1" model="$2" at="$3"; shift 3
  local errf line rc
  errf="$(mktemp)"
  # The gate runs in the subshell's own shell (not a command substitution) so its SC_PRICE_*
  # globals are readable after it returns; a refusal exits that subshell, which is the rc we read.
  line="$( ( SC_TRANSPORT="$transport"; . "$LIB" >/dev/null 2>&1
             sc_parse_flags -m "$model" "$@" >/dev/null 2>&1
             sc_resolve_model >/dev/null 2>&1
             SC_PRICE_AT="$at" sc_gate_price_window >/dev/null 2>"$errf"
             printf '0|%s|%s' "${SC_PRICE_WINDOW:-}" "${SC_PRICE_MULT:-}" ) )"
  rc=$?
  [ "$rc" -ne 0 ] && line="$rc|refused|"
  printf '%s\n%s' "$line" "$(cat "$errf")"
  rm -f "$errf"
}
first() { printf '%s' "$1" | head -1; }

out="$(gate deepseek flash "$PEAK" 2>&1)"
case "$out" in *"11|refused|"*) ok "peak, no -W -> exit 11" ;; *) bad "peak, no -W: $out" ;; esac
case "$out" in *"-W"*) ok "refusal names the -W override" ;; *) bad "refusal does not name -W: $out" ;; esac

out="$(gate deepseek flash "$PEAK" -A 2>&1)"
case "$out" in *"11|refused|"*) ok "peak, -A only -> still exit 11" ;; *) bad "peak, -A only: $out" ;; esac

expect "peak, -W -> passes at full price" "0|peak|1.0" "$(first "$(gate deepseek flash "$PEAK" -W 2>&1)")"
expect "off-peak -> passes at the multiplier" "0|off-peak|0.5" "$(first "$(gate deepseek flash "$OFF" 2>&1)")"
expect "unscheduled transport at a peak hour -> flat no-op" "0|flat|1.0" "$(first "$(gate anthropic sonnet "$PEAK" 2>&1)")"

chk="$( ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
         sc_parse_flags -m flash >/dev/null 2>&1; sc_resolve_model >/dev/null 2>&1
         SC_CHECK_MODE=1 SC_PRICE_AT="$PEAK" sc_gate_price_window 2>/dev/null ); echo "rc=$?" )"
case "$chk" in
  "UNAVAILABLE (peak pricing window"*"rc=11") ok "check mode: one UNAVAILABLE line, exit 11" ;;
  *) bad "check mode: $chk" ;;
esac

# ---- 4. declared effort vocabulary -------------------------------------------------------------
effort() {  # $1 transport, $2 model, $3 effort -> "rc message"
  ( SC_TRANSPORT="$1"; . "$LIB" >/dev/null 2>&1
    sc_parse_flags -m "$2" -e "$3" >/dev/null 2>&1; sc_resolve_model >/dev/null 2>&1
    msg="$(sc_validate_effort 2>&1)"; echo "0 $msg" ) 2>&1 | tail -1
}
out="$( ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
         sc_parse_flags -m flash -e medium >/dev/null 2>&1; sc_resolve_model >/dev/null 2>&1
         sc_validate_effort ) 2>&1; echo "rc=$?")"
case "$out" in *"low|high|max"*"rc=2") ok "deepseek -e medium refused naming low|high|max" ;; *) bad "deepseek -e medium: $out" ;; esac
expect "deepseek -e max accepted" "0 " "$(effort deepseek flash max)"
expect "opencode -e medium still accepted (no declared vocabulary on the row's transport is narrower)" "0 " "$(effort opencode muse medium)"

# ---- 5. record fields --------------------------------------------------------------------------
tmpd="$(mktemp -d)"
rec="$tmpd/probe.record.json"
payload='{"type":"result","session_id":"s","num_turns":1,"result":"ok","modelUsage":{"deepseek-flash":{"inputTokens":1000000,"cacheReadInputTokens":0,"outputTokens":1000000,"canonicalModel":"deepseek-flash"}}}'
( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
  sc_parse_flags -m flash -R "$rec" -L "" -l "probe:price-window" >/dev/null 2>&1
  sc_resolve_model >/dev/null 2>&1
  SC_WORKDIR="$tmpd"
  SC_PRICE_AT="$OFF" sc_gate_price_window >/dev/null 2>&1
  unset SC_PRICE_AT
  sc_write_record "$payload" 0 >/dev/null 2>&1 )
fields="$(python3 -c 'import json,sys
r=json.load(open(sys.argv[1],encoding="utf-8"))
print(r.get("priceWindow"), r.get("priceMultiplier"), r.get("modelVersion"), r.get("costUSD"))' "$(cygpath -w "$rec" 2>/dev/null || echo "$rec")" 2>&1)"
expect "record: window, multiplier, version, off-peak cost" "off-peak 0.5 DeepSeek-V4.1-Flash 0.75" "$fields"
rm -rf "$tmpd"

# ---- 6. call sites -----------------------------------------------------------------------------
# anthropic_sidecar.sh is consumer-local, so the baseline template ships no such file. Assert the
# call-site order for every launcher THIS tree ships and count those cases, rather than fixing a
# launcher list that is only correct in a consumer.
_launchers=0
for launcher in anthropic_sidecar.sh codex_proxy_sidecar.sh deepseek_sidecar.sh opencode_sidecar.sh; do
  [ -f "$SCRIPTS/$launcher" ] || continue
  _launchers=$((_launchers + 1))
  order="$(grep -n -E '^sc_gate_(capacity|price_window)' "$SCRIPTS/$launcher" | cut -d: -f2 | awk '{print $1}' | tr '\n' ' ')"
  expect "$launcher calls price-window after the unified capacity gate" "sc_gate_capacity sc_gate_price_window " "$order"
done

# ---- 7. --check runs the unified gates in dispatch order --------------------------------------------
check_order="$(awk '/^sc_check_gates\(\)/{f=1;next} f&&/^}/{exit} f' "$LIB" | grep -v -E '^[[:space:]]*#' \
  | grep -o -E 'sc_gate_(band|capacity|price_window)' | tr '\n' ' ')"
expect "sc_check_gates runs host band, target capacity, then price window" \
  "sc_gate_band sc_gate_capacity sc_gate_price_window " "$check_order"

# ---- 8. --check sees the dispatch's own -m / -W (review F1) --------------------------------------
# hooks/sidecar_dispatch_context.py runs `<launcher> --check <dispatch args>` before every launch; a
# --check that ignores -W denies a user-authorized peak dispatch before the launcher ever runs.
expect "sc_check_model_override -W sets peak authorization" "1" \
  "$( ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1; sc_check_model_override --check -m flash -W; printf '%s' "$SC_PEAK_AUTHORIZED" ) )"
chk_w="$( ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
           sc_check_model_override --check -m flash -W; sc_resolve_model >/dev/null 2>&1
           SC_CHECK_MODE=1 SC_PRICE_AT="$PEAK" sc_gate_price_window >/dev/null 2>&1 ); echo "rc=$?" )"
expect "check mode at peak WITH -W passes" "rc=0" "$chk_w"
ds_check="$(awk '/^if \[ "\$\{1:-\}" = "--check" \]; then/{f=1} f&&/^fi/{exit} f' "$SCRIPTS/deepseek_sidecar.sh" \
  | grep -v -E '^[[:space:]]*#' | grep -o -E 'sc_check_model_override "\$@"|sc_check_gates' | tr '\n' ' ')"
expect "deepseek --check applies the dispatch's flags before its gates" 'sc_check_model_override "$@" sc_check_gates ' "$ds_check"

# ---- 9. an unreadable effort vocabulary fails CLOSED (review F2) -----------------------------------
stubcli="$(mktemp --suffix=.py)"
printf 'import sys\nsys.exit(3)\n' > "$stubcli"
out="$( ( SC_TRANSPORT=deepseek; . "$LIB" >/dev/null 2>&1
         sc_parse_flags -m flash -e max >/dev/null 2>&1; SC_REGISTRY_CLI="$stubcli"
         sc_validate_effort ) 2>&1; echo "rc=$?")"
rm -f "$stubcli"
case "$out" in *"rc=2") ok "effort-values lookup failure refuses (exit 2), never reads as unrestricted" ;; *) bad "effort lookup failure: $out" ;; esac

# 20 fixed cases plus one per launcher this tree ships (4 in a consumer, 3 upstream).
_expected=$((20 + _launchers))
[ "$ran" -eq "$_expected" ] || { echo "FAIL expected $_expected cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_price_window" || echo "FAIL test_sidecar_price_window"
exit "$fail"
