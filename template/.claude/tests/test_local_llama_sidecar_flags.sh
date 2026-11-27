#!/usr/bin/env bash
# local_llama_sidecar.sh: --check hands backend_hold.py the registry row's port, window and upstream id; a dispatch
# hands it the -e effort (any getopts form) and passes every flag through to opencode_sidecar.sh with
# SC_UPSTREAM_KEY=local; a dispatch without -e exits 2 before uv runs; -X detaches (and -X without -R is refused
# by the lib before uv runs). `uv` is a stub that records argv.
set -uo pipefail
# The local llama seat is a Windows workstation facility: backend_hold.py spawns the child from Windows
# Python through an absolute Git Bash path, and the launch memory floor reads Windows headroom. A host
# without those facilities has no seat to prove.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) echo "SKIP: the local llama seat spawns through Windows Python and Git Bash and reads Windows memory headroom; $(uname -s) has none of the three"; exit 0 ;;
esac
unset CLAUDE_CODE_SESSION_ID  # the proof is no session: an invoking session's dispatch-doctrine state must not deny its launches
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
L="$R/scripts/local_llama_sidecar.sh"
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
fail=0
cat > "$D/uv" <<'SH'
#!/usr/bin/env bash
{ printf 'KEY=%s\n' "${SC_UPSTREAM_KEY:-}"; printf 'MEM=%s\n' "${SC_MEMORY_CHECKED:-}"; printf 'ARG=%s\n' "$@"; } > "$UV_LOG"
echo OK
SH
chmod +x "$D/uv"
export UV_BIN="$D/uv" UV_LOG="$D/uv.log" AI_WORKER_DIR="$D/aiw"
# Floors of 0 admit every launch, so the cases below do not depend on this machine's free memory.
export LAUNCH_MIN_COMMIT_GIB=0 LAUNCH_MIN_PHYSICAL_GIB=0
row="$(python3 - "$R/tools" <<'PY'
import sys, urllib.parse
sys.path.insert(0, sys.argv[1])
import model_registry
d = model_registry.load()
r = next(r for r in d["models"] if r.get("transport") == "local")
print(r["alias"], r["upstreamId"], urllib.parse.urlparse(d["transports"]["local"]["baseUrl"]).port, r["limits"]["contextTokens"])
PY
)"
read -r ALIAS UP PORT CTX <<<"$row"
args() { sed -n 's/^ARG=//p' "$UV_LOG" | tr '\n' ' '; }

rm -f "$UV_LOG"; out="$(bash "$L" --check -m "$ALIAS" 2>&1)"; rc=$?
a="$(args)"
case "$a" in *"backend_hold.py check --backend $UP --port $PORT --ctx $CTX "*) ;; *) echo "FAIL --check args: $a ($out)"; fail=1 ;; esac
[ "$rc" = 0 ] || { echo "FAIL --check rc=$rc: $out"; fail=1; }

rm -f "$UV_LOG"; out="$(bash "$L" -m "$ALIAS" -e low -f p.md -l lab 2>&1)"
a="$(args)"
case "$a" in *"run --backend $UP --port $PORT --ctx $CTX --effort low -- "*"/opencode_sidecar.sh -m $ALIAS -e low -f p.md -l lab "*) ;; *) echo "FAIL dispatch args: $a ($out)"; fail=1 ;; esac
# backend_hold.py spawns the child from Windows Python, where a bare `bash` resolves to WSL's bash, which cannot
# open a C:/ path: the child's interpreter must be this Git Bash, by absolute path.
child_sh="$(sed -n 's/^ARG=//p' "$UV_LOG" | sed -n '/^--$/{n;p;q}')"
case "$child_sh" in [A-Za-z]:/*bash*) ;; *) echo "FAIL child interpreter must be an absolute Windows path to bash, got '$child_sh'"; fail=1 ;; esac
grep -qx 'KEY=local' "$UV_LOG" 2>/dev/null || { echo "FAIL dispatch must export SC_UPSTREAM_KEY=local"; fail=1; }
# The child must not re-check memory: by then the server this launch started holds its share and refuses the launch.
grep -qx 'MEM=1' "$UV_LOG" 2>/dev/null || { echo "FAIL dispatch must hand the child SC_MEMORY_CHECKED=1"; fail=1; }

# Memory admission runs before backend_hold.py starts the server: a refused launch exits 12 and runs no uv.
rm -f "$UV_LOG"; out="$(LAUNCH_MIN_COMMIT_GIB=100000 bash "$L" -m "$ALIAS" -e low -f p.md -l lab 2>&1)"; rc=$?
{ [ "$rc" = 12 ] && [ ! -e "$UV_LOG" ]; } || { echo "FAIL memory refusal: rc=$rc, uv ran=$([ -e "$UV_LOG" ] && echo yes || echo no) ($out)"; fail=1; }

rm -f "$UV_LOG"; bash "$L" -m "$ALIAS" -fp.md -elow >/dev/null 2>&1
case "$(args)" in *"--effort low -- "*) ;; *) echo "FAIL clustered -elow not read as effort: $(args)"; fail=1 ;; esac

rm -f "$UV_LOG"; bash "$L" -m "$ALIAS" -f p.md >/dev/null 2>&1; rc=$?
{ [ "$rc" = 2 ] && [ ! -e "$UV_LOG" ]; } || { echo "FAIL no -e: rc=$rc, uv ran=$([ -e "$UV_LOG" ] && echo yes || echo no)"; fail=1; }

# The parent resolves the job's tool profile itself, before memory admission, so an invalid one is
# refused before the server starts and the class sees the job's built-ins rather than the brief alone.
rm -f "$UV_LOG"; bash "$L" -m "$ALIAS" -e low -G no-such-kind -f p.md -l lab >/dev/null 2>&1; rc=$?
{ [ "$rc" = 2 ] && [ ! -e "$UV_LOG" ]; } || { echo "FAIL an invalid tool profile must be refused in the parent: rc=$rc, uv ran=$([ -e "$UV_LOG" ] && echo yes || echo no)"; fail=1; }

# -X is a detach, like every launcher's: the foreground call returns at once, and the job it spawned is the
# one that runs uv (the stub), so the GPU hold lives in the job, not in the caller.
export SIDECAR_LEDGER_PATH="$D/ledger.jsonl"
rm -f "$UV_LOG"; bash "$L" -m "$ALIAS" -e low -X -f p.md >/dev/null 2>&1; rc=$?
{ [ "$rc" = 2 ] && [ ! -e "$UV_LOG" ]; } || { echo "FAIL -X without -R: rc=$rc, uv ran=$([ -e "$UV_LOG" ] && echo yes || echo no)"; fail=1; }

REC="$D/rec"; rm -f "$UV_LOG"
out="$(bash "$L" -X -R "$REC" -m "$ALIAS" -e low -f p.md -l lab 2>&1)"; rc=$?
case "$out" in *"DETACHED pid="*) ;; *) echo "FAIL -X did not detach: rc=$rc $out"; fail=1 ;; esac
[ "$rc" = 0 ] || { echo "FAIL -X rc=$rc: $out"; fail=1; }
waited=0
while [ ! -f "$REC.exit" ] && [ "$waited" -lt 400 ]; do sleep 0.1; waited=$((waited + 1)); done
[ -f "$REC.exit" ] || { echo "FAIL the detached job wrote no .exit"; fail=1; }
[ "$(cat "$REC.exit" 2>/dev/null)" = 0 ] || { echo "FAIL detached exit=$(cat "$REC.exit" 2>/dev/null): $(cat "$REC.err" 2>/dev/null)"; fail=1; }
a="$(args)"
case "$a" in *"run --backend $UP --port $PORT --ctx $CTX --effort low -- "*"/opencode_sidecar.sh "*) ;; *) echo "FAIL detached args: $a"; fail=1 ;; esac
grep -qx 'KEY=local' "$UV_LOG" 2>/dev/null || { echo "FAIL the detached job must export SC_UPSTREAM_KEY=local"; fail=1; }

# A detached job's memory-admission wait is bounded by -T: the parent must carry the bound into the lib,
# which wraps the wait in `timeout`. A shim stands in for that wrapper and answers 124 at once, so the
# case decides the wiring (was the wait wrapped, with this bound) rather than waiting on real memory.
mkdir -p "$D/tbin" "$D/sidecar-snapshots"
cat > "$D/tbin/timeout" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$TIMEOUT_LOG"
exit 124
SH
chmod +x "$D/tbin/timeout"
cp "$L" "$D/sidecar-snapshots/local_llama_sidecar.sh"
export TIMEOUT_LOG="$D/timeout.log" SC_LAUNCHER_DIR="$R/scripts" SC_SNAPSHOT_OF="$L"
rm -f "$TIMEOUT_LOG" "$UV_LOG"
out="$(PATH="$D/tbin:$PATH" SIDECAR_DETACHED=1 bash "$D/sidecar-snapshots/local_llama_sidecar.sh" \
  -T 3 -m "$ALIAS" -e low -f p.md -l lab 2>&1)"; rc=$?
{ [ "$rc" = 12 ] && [ -f "$TIMEOUT_LOG" ] && [ "$(sed -n '1p' "$TIMEOUT_LOG")" = 3 ] && [ ! -e "$UV_LOG" ]; } \
  || { echo "FAIL -T must bound the detached memory wait: rc=$rc timeout_log=$(cat "$TIMEOUT_LOG" 2>/dev/null | head -2 | tr '\n' ' ') uv_ran=$([ -e "$UV_LOG" ] && echo yes || echo no) ($out)"; fail=1; }
unset SC_LAUNCHER_DIR SC_SNAPSHOT_OF

[ "$fail" = 0 ] && echo "PASS: local_llama_sidecar.sh hands backend_hold.py the row and the effort"
exit "$fail"
