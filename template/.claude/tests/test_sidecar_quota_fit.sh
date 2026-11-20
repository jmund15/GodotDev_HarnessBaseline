#!/usr/bin/env bash
# Proof for sc_check_quota_fit (lib/sidecar_common.sh): a launch whose quota window cannot hold the job waits for
# room when its caller can wait, and warns then runs when it cannot. Runs the real function against a planted
# tools/quota_fit.py that answers from a script file, one line per call (`<exit>|<stdout>`); tests/test_quota_fit.py
# proves the real decision. Each case runs in a subshell, since sc_die exits.
#
#   bash .claude/tests/test_sidecar_quota_fit.sh
set -u

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$R/scripts/lib/sidecar_common.sh"
declare -F sc_check_quota_fit >/dev/null || { echo "CANNOT RUN: sc_check_quota_fit not found"; exit 2; }

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/tools"
cat > "$ROOT/tools/quota_fit.py" <<'PY'
import os, sys
script = os.environ["FIT_SCRIPT"]
lines = open(script, encoding="utf-8").read().splitlines()
with open(os.environ["FIT_CALLS"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
head, rest = (lines[0], lines[1:]) if lines else ("0|fits", [])
open(script, "w", encoding="utf-8").write("\n".join(rest))
code, text = head.split("|", 1)
print(text)
sys.exit(int(code))
PY
export FIT_SCRIPT="$ROOT/script" FIT_CALLS="$ROOT/calls"
PASS=0; FAIL=0
ok() { PASS=$((PASS + 1)); echo "  PASS  $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL  $1"; }

# run <answers> [env...] -> sets RC, ERR, CALLS for one sc_check_quota_fit call
run() {
  local answers="$1"; shift
  printf '%s\n' "$answers" > "$FIT_SCRIPT"; : > "$FIT_CALLS"
  ERR="$(env -u SIDECAR_DETACHED -u SIDECAR_CAN_WAIT "$@" bash -c '
    source "$1/scripts/lib/sidecar_common.sh"
    SC_ROOT="$2"; SC_TRANSPORT=codex; SC_MODEL=probe-alias; SC_EFFORT=max; SC_SHAPE=arm; SC_RECORD=/out/self.record.json
    SC_FIT_RECHECK_S=0; SC_TIMEOUT="${TEST_TIMEOUT:-}"
    sc_journal() { printf "JOURNAL %s %s\n" "$1" "$2" >&2; }
    sleep() { :; }
    sc_check_quota_fit
    echo "STARTED" >&2
  ' _ "$R" "$ROOT" 2>&1)"
  RC=$?
  CALLS="$(wc -l < "$FIT_CALLS" | tr -d ' ')"
}

echo "sc_check_quota_fit:"
run "0|fits"
[ "$RC" = 0 ] && [[ "$ERR" == *STARTED* ]] && [[ "$ERR" != *WARN* ]] && ok "a job that fits starts silently" || bad "fits: rc=$RC $ERR"

run "10|3600 codex: headroom 6 points" SIDECAR_CAN_WAIT=
[ "$RC" = 0 ] && [[ "$ERR" == *"WARN quota window: codex: headroom 6 points"* ]] && [[ "$ERR" == *STARTED* ]] \
  && [ "$CALLS" = 1 ] && ok "an interactive foreground launch warns once and runs" || bad "foreground: rc=$RC calls=$CALLS $ERR"

run "10|3600 codex: headroom 6 points
10|3000 codex: headroom 6 points
0|fits" SIDECAR_CAN_WAIT=1
[ "$RC" = 0 ] && [ "$CALLS" = 3 ] && [[ "$ERR" == *STARTED* ]] && [ "$(grep -c '^JOURNAL waiting-quota' <<<"$ERR")" = 1 ] \
  && ok "a caller that can wait re-asks until the job fits, journaling one waiting-quota row" \
  || bad "can-wait: rc=$RC calls=$CALLS $ERR"

run "10|3600 codex: headroom 6 points
0|fits" SIDECAR_DETACHED=1
[ "$RC" = 0 ] && [ "$CALLS" = 1 ] && [[ "$ERR" == *"WARN quota window"* ]] && [[ "$ERR" == *STARTED* ]] \
  && ok "a detached session job warns and runs: only a caller that says it can wait waits" || bad "detached: rc=$RC calls=$CALLS $ERR"

run "4|no plan holds the job before Nov 22 18:00Z" SIDECAR_CAN_WAIT=1
[ "$RC" = 10 ] && [[ "$ERR" != *STARTED* ]] && ok "a waiting caller no window can hold before a long reset exits 10, nothing started" \
  || bad "long window: rc=$RC $ERR"

run "10|3600 codex: headroom 6 points
10|3600 codex: headroom 6 points" SIDECAR_CAN_WAIT=1 TEST_TIMEOUT=0
[ "$RC" = 10 ] && [[ "$ERR" == *"wait bound"* ]] && [[ "$ERR" != *STARTED* ]] \
  && ok "the -T wait bound ends the wait with exit 10" || bad "timeout: rc=$RC $ERR"

run "2|alias 'probe-alias' does not resolve" SIDECAR_CAN_WAIT=1
[ "$RC" = 0 ] && [[ "$ERR" == *STARTED* ]] && ok "an unreadable fit decides nothing; the launch proceeds" || bad "unreadable: rc=$RC $ERR"

run "0|fits"
[ "$(cat "$FIT_CALLS")" = "probe-alias max arm --plan codex --exclude self.record.json" ] \
  && ok "the check judges the paying plan and excludes the asking launcher by its -R record's file name" \
  || bad "argv: $(cat "$FIT_CALLS")"

echo "test_sidecar_quota_fit: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
