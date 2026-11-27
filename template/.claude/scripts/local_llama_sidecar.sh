#!/usr/bin/env bash
# local_llama_sidecar.sh — launcher for registry transport `local`: a llama-server on this machine.
#
# It holds nothing itself. ai-worker's backend_hold.py takes the GPU lock and the queue claim, starts the
# llama-server with the dispatch's effort as the chat template's launch default (the LiteLLM proxy forwards
# no per-request effort to an openai/ backend), restarts it if it dies, and runs opencode_sidecar.sh as its
# child. opencode_sidecar.sh then runs the Claude Code child through its LiteLLM proxy and writes the record.
#
#   local_llama_sidecar.sh --check [-m <alias>]   OK or one UNAVAILABLE line; starts no server
#   local_llama_sidecar.sh -m <alias> -e <effort> [any opencode_sidecar.sh flag]...
#
# Without -m, the first registry row on transport `local` serves. Every flag passes through to
# opencode_sidecar.sh unchanged. This launcher reads -m and -e, plus the tool-profile and -T flags its
# own memory admission needs (-G, -t, -B, -d, -Q, -T), with the shared option string (SC_OPTSTRING),
# and refuses one shape with exit 2:
#   no -e   the server would start at the template default (0.9) while the record names no effort
# -X detaches like every other launcher; the GPU hold lives in backend_hold.py inside the detached
# job, which waits for the child, so it outlives the foreground caller's exit.
# AI_WORKER_DIR overrides the ai-worker checkout (default ~/.config/ai-worker); UV_BIN overrides `uv`.

SC_TRANSPORT="local"
_sc_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$_sc_here" in */sidecar-snapshots) ;; *) if [ "${SC_LAUNCHER_DIR:-}" = "${SC_LAUNCHER_DIR_RUN:-}" ]; then unset SC_LAUNCHER_DIR; fi ;; esac
SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$_sc_here}"
# shellcheck source=lib/sidecar_common.sh
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"

sc_reexec_snapshot "$@"

LL_MODEL=""
LL_EFFORT=""
ll_read_flags() {
  local opt OPTIND=1 OPTARG
  while getopts "$SC_OPTSTRING" opt; do
    case "$opt" in
      m) LL_MODEL="$OPTARG" ;;
      e) LL_EFFORT="$OPTARG" ;;
      f) SC_PROMPT_FILE="$OPTARG" ;;
      G) SC_SHAPE="$OPTARG" ;;
      l) SC_LABEL="$OPTARG" ;;
      R) SC_RECORD="$OPTARG" ;;
      T) SC_TIMEOUT="$OPTARG" ;;
      t) SC_TOOLS="$OPTARG" ;;
      B) SC_BASE_BUILTINS="$OPTARG" ;;
      d) SC_WORKDIR="$OPTARG" ;;
      Q) SC_TIER="$OPTARG" ;;
    esac
  done
}

ll_win_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

AIW="${AI_WORKER_DIR:-$(ll_win_path "$HOME")/.config/ai-worker}"
UV="${UV_BIN:-uv}"

# Prints "<upstreamId> <port> <contextTokens>" for the row, else one UNAVAILABLE line and exit 2.
ll_row_fields() {
  python3 - "$SC_ROOT/tools" "$LL_MODEL" <<'PY'
import sys, urllib.parse
sys.path.insert(0, sys.argv[1])
import model_registry
data = model_registry.load()
want = sys.argv[2]
rows = [r for r in data.get("models", []) if r.get("transport") == "local"]
row = next((r for r in rows if want in (r.get("alias"), r.get("id"), r.get("upstreamId"))), None) if want else (rows[0] if rows else None)
if row is None:
    print("UNAVAILABLE (no transport-local registry row %s)" % (want or "")); sys.exit(2)
port = urllib.parse.urlparse((data.get("transports", {}).get("local") or {}).get("baseUrl") or "").port
ctx = (row.get("limits") or {}).get("contextTokens")
if not port or not ctx:
    print("UNAVAILABLE (transport local baseUrl or row contextTokens missing)"); sys.exit(2)
print(row.get("upstreamId") or row["id"].split("/", 1)[1], port, ctx)
PY
}

if [ "${1:-}" = "--check" ]; then
  shift
  ll_read_flags "$@"
  fields="$(ll_row_fields)" || { echo "$fields"; exit 2; }
  read -r LL_UPSTREAM LL_PORT LL_CTX <<<"$fields"
  command -v claude >/dev/null 2>&1 || [ -n "${CLAUDE_BIN:-}" ] || { echo "UNAVAILABLE (claude CLI not found)"; exit 4; }
  command -v litellm >/dev/null 2>&1 || [ -x "$(ll_win_path "$HOME")/.local/bin/litellm.exe" ] || [ -n "${OC_LITELLM_BIN:-}" ] || {
    echo "UNAVAILABLE (litellm not found)"; exit 4; }
  exec "$UV" run --project "$AIW" python -u "$AIW/backend_hold.py" check \
    --backend "$LL_UPSTREAM" --port "$LL_PORT" --ctx "$LL_CTX"
fi

ll_read_flags "$@"
[ -n "$LL_EFFORT" ] || { echo "[local] -e is required: the effort is fixed when the server starts" >&2; exit 2; }
fields="$(ll_row_fields)" || { echo "$fields" >&2; exit 2; }
read -r LL_UPSTREAM LL_PORT LL_CTX <<<"$fields"

# Memory admission runs here, before backend_hold.py starts the server: checked in the child, the server this
# launch just started holds its share of the floor and the launch refuses itself on every retry. The job's
# tool profile is resolved first, exactly as the child would, so the class sees its built-ins: a job with
# no shell takes the light floor instead of the brief-and-shape heavy one.
[ -z "${SC_SHAPE:-}" ] || sc_resolve_tools
sc_check_launch_memory
export SC_MEMORY_CHECKED=1

# The credential opencode_sidecar.sh writes as the upstream api_key: never the env file's Zen key.
export SC_UPSTREAM_KEY="local"
# backend_hold.py spawns the child from Windows Python, where a bare `bash` resolves to WSL's bash (System32), which
# cannot open a C:/ path; this shell's own interpreter, by absolute path, runs the child.
LL_BASH="$(ll_win_path "${BASH:-/usr/bin/bash}")"
"$UV" run --project "$AIW" python -u "$AIW/backend_hold.py" run \
  --backend "$LL_UPSTREAM" --port "$LL_PORT" --ctx "$LL_CTX" --effort "$LL_EFFORT" \
  -- "$LL_BASH" "$SC_LAUNCHER_DIR/opencode_sidecar.sh" "$@"
