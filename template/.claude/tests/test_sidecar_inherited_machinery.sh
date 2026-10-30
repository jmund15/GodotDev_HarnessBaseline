#!/usr/bin/env bash
# A launcher must not honor the snapshot/detach machinery it did NOT set.
#
# A session that is itself a sidecar child carries SC_SNAPSHOT_OF and SIDECAR_DETACHED in its
# environment (the launcher exports them to the child it spawns, and every process under that child
# inherits them). A launcher invoked from there used to believe it WAS that outer job: it skipped
# the `-X` scan and getopts rejected `-X` as an unknown option, so every nested dispatch died
# `-X must be a standalone argument` (exit 2) and, with no -X, wrote no `-R` durable files at all.
#
# Every case runs a COPY of a real launcher through the real lib, with `-X` and no `-R` so the
# refusal is reachable and NOTHING is spawned. A wrong answer here is a live process or a lost
# record, not a cosmetic difference.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
[ -f "$SCRIPTS/lib/sidecar_common.sh" ] || { echo "FAIL: lib missing"; exit 1; }
fail=0
ran=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp "$SCRIPTS/anthropic_sidecar.sh" "$WORK/launcher.sh" || exit 1

check() {  # $1 label, $2 expected, $3 actual
  ran=$((ran + 1))
  if [ "$3" = "$2" ]; then echo "OK   $1 -> '$3'"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

run() {  # remaining args: env assignments before the launcher
  env SC_LAUNCHER_DIR="$SCRIPTS" "$@" bash "$WORK/launcher.sh" -m haiku -l inherited -X -D pointer -G arm 2>&1 | tail -1
}

# 1. the inherited machinery, exactly as this session's own environment carries it
check "inherited SC_SNAPSHOT_OF + SIDECAR_DETACHED still scans -X" "-X needs -R" \
      "$(SIDECAR_DETACHED=1 SC_SNAPSHOT_OF=.claude/scripts/deepseek_sidecar.sh run)"
# 2. SIDECAR_DETACHED alone (the flag that also suppresses the -R durable-file writer)
check "inherited SIDECAR_DETACHED alone still scans -X" "-X needs -R" \
      "$(SIDECAR_DETACHED=1 run)"
# 3. SC_SNAPSHOT_OF alone
check "inherited SC_SNAPSHOT_OF alone still scans -X" "-X needs -R" \
      "$(SC_SNAPSHOT_OF=.claude/scripts/deepseek_sidecar.sh run)"
# 4. CONTROL: a clean environment is unaffected -- the fix must not change an ordinary launch
check "a clean environment stages -X the same way" "-X needs -R" "$(run)"
# 5. CONTROL: the outer value is cleared, not honored -- the OTHER launcher name proves the launcher
#    file being run is not the one the inherited marker names.
check "a different inherited launcher name is not honored" "-X needs -R" \
      "$(SIDECAR_DETACHED=1 SC_SNAPSHOT_OF=.claude/scripts/codex_proxy_sidecar.sh run)"

[ "$ran" -ge 5 ] || { echo "FAIL expected >= 5 cases, ran $ran"; fail=1; }
[ "$fail" = 0 ] && echo "PASS test_sidecar_inherited_machinery" || echo "FAIL test_sidecar_inherited_machinery"
exit "$fail"
