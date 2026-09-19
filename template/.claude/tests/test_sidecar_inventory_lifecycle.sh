#!/usr/bin/env bash
# Proves direct sidecar inventories are bounded and live only until record publication.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/../scripts/lib/sidecar_common.sh"
. "$LIB"
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
fail=0
say() { echo "$*"; }

for i in 1 2 3 4 5; do
  printf '{}\n' > "$D/fanout-00000000-0000-0000-0000-00000000000$i.inventory.json"
done
printf '{}\n' > "$D/.sidecar-inventory-abandoned"
python3 - "$D" <<'PY'
import os, pathlib, sys, time
old = time.time() - 8 * 24 * 3600
for path in pathlib.Path(sys.argv[1]).glob('fanout-*.inventory.json'):
    os.utime(path, (old, old))
staging = pathlib.Path(sys.argv[1]) / '.sidecar-inventory-abandoned'
os.utime(staging, (old - 1, old - 1))
PY
printf '{}\n' > "$D/fanout-10000000-0000-0000-0000-000000000000.inventory.json"

mkdir "$D/blocked.record.json"
SC_RECORD="$D/blocked.record.json"
SC_LABEL="inventory-test"
SC_LAUNCH_ID="20000000-0000-0000-0000-000000000000"
SC_PARENT_SESSION_ID="parent"
SC_LAUNCH_PREPARED=0
SC_INVENTORY_SWEEP_LIMIT=3
SC_MODEL="gpt-5.6-sol"
SC_EFFORT="medium"
SC_TRANSPORT="codex"
SC_COST_MODEL="plan-quota"
SC_DISCLOSURE="full"
SC_LEDGER=""
SC_RECORD_PARSER="codex"
SC_SCHEMA_FILE=""
sc_prepare_record
stale_count=$(python3 - "$D" <<'PY'
import pathlib, sys
print(sum(1 for p in pathlib.Path(sys.argv[1]).glob('fanout-0*.inventory.json')))
PY
)
if [ "$stale_count" = 3 ] && [ ! -e "$D/.sidecar-inventory-abandoned" ] \
    && [ -f "$SC_INVENTORY" ]; then
  say "OK   stale sweep bounds final inventories and staging debris"
else
  say "FAIL stale sweep: stale=$stale_count staging=$([ -e "$D/.sidecar-inventory-abandoned" ] && echo present || echo absent) inventory=${SC_INVENTORY:-missing}"; fail=1
fi

failed_rc=0
sc_write_record '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}' 1 2>"$D/publish-error.log" || failed_rc=$?
if [ "$failed_rc" -ne 0 ] && [ -f "$SC_INVENTORY" ]; then
  say "OK   failed record publication preserves discovery inventory"
else
  say "FAIL failed publication: rc=$failed_rc inventory=${SC_INVENTORY:-missing}"; fail=1
fi

sc_cleanup_published_inventory 0
if [ ! -e "$SC_INVENTORY" ]; then
  say "OK   successful cleanup removes discovery inventory"
else
  say "FAIL successful cleanup left inventory"; fail=1
fi

SC_RECORD="$D/published.record.json"
SC_LAUNCH_ID="30000000-0000-0000-0000-000000000000"
SC_LAUNCH_PREPARED=0
sc_prepare_record
published_inventory="$SC_INVENTORY"
sc_write_record '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}' 0
if [ -s "$SC_RECORD" ] && [ ! -e "$published_inventory" ]; then
  say "OK   record writer wires cleanup after atomic publication"
else
  say "FAIL record writer did not publish then clean inventory"; fail=1
fi

slow_dir="$D/slow-json"
mkdir "$slow_dir"
cat > "$slow_dir/sitecustomize.py" <<'PY'
import json
import os
import time

_original_dumps = json.dumps


def slow_dump(value, handle, *args, **kwargs):
    encoded = _original_dumps(value, *args, **kwargs)
    split = max(1, len(encoded) // 2)
    handle.write(encoded[:split])
    handle.flush()
    os.fsync(handle.fileno())
    time.sleep(1)
    handle.write(encoded[split:])


json.dump = slow_dump
PY
SC_RECORD="$D/atomic.record.json"
SC_LAUNCH_ID="40000000-0000-0000-0000-000000000000"
SC_LAUNCH_PREPARED=0
atomic_inventory="$D/fanout-$SC_LAUNCH_ID.inventory.json"
observed_partial=0
(PYTHONPATH="$slow_dir" sc_prepare_record) &
prepare_pid=$!
while kill -0 "$prepare_pid" 2>/dev/null; do
  if [ -e "$atomic_inventory" ] && ! python3 -m json.tool "$atomic_inventory" >/dev/null 2>&1; then
    observed_partial=1
  fi
done
prepare_rc=0
wait "$prepare_pid" || prepare_rc=$?
if [ "$prepare_rc" -eq 0 ] && [ "$observed_partial" -eq 0 ] \
    && python3 -m json.tool "$atomic_inventory" >/dev/null 2>&1; then
  say "OK   inventory publication never exposes partial JSON"
else
  say "FAIL inventory publication exposed partial JSON or failed: rc=$prepare_rc partial=$observed_partial"; fail=1
fi

exit $fail
