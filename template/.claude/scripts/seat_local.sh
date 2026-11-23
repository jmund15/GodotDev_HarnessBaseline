#!/usr/bin/env bash
# seat_local.sh <prompt.md> <workdir> | --check | hold -e <effort> -- <child...>: the seat served by a llama-server on this machine (seats.json
# `inkling-local`). seat.sh sets the SEAT_* values from the seat's row and its model's row: SEAT_NAME, SEAT_MODEL (the
# server's backend id in ai-worker's models.yaml), SEAT_EFFORT, SEAT_CONTEXT, SEAT_MAX_OUTPUT, SEAT_BASE_URL,
# SEAT_WRITES, SEAT_SETTINGS, SEAT_RECORD.
#
# The job is scripts/seat_claude.sh's headless Claude child, pointed at a LiteLLM proxy in front of the server, and run
# under ai-worker's backend_hold.py. backend_hold.py takes the GPU lock and the queue claim that `check_local_queue`
# reads, starts llama-server at the dispatch's effort (the proxy forwards no per-request effort to an openai/ backend,
# so the server's launch default is the only channel), restarts a dead server while the child runs, and on the child's
# exit stops the server and frees the lock. It runs the child as its own child, so this script spawns it and never
# execs. The child is this script again with --held (proxy up, the child, proxy down).
#
#   hold -e <effort> [-m <seat>] -- <child argv...>
#             The hold and the proxy as one verb that runs any child (seat defaults to inkling-local; the row is
#             read through tools/launch_check.py). The child gets ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN for the
#             proxy, ANTHROPIC_API_KEY="", CLAUDE_CODE_MAX_CONTEXT_TOKENS / CLAUDE_CODE_MAX_OUTPUT_TOKENS,
#             ANTHROPIC_SMALL_FAST_MODEL and CLAUDE_CODE_SUBAGENT_MODEL (the model), SEAT_PROXY_VERSION and
#             SEAT_MODEL (the route name the proxy serves: pass it as the child's --model); the parent's other
#             CLAUDE*/ANTHROPIC* vars are scrubbed. Nothing else is added. The child's stdout and stderr, and
#             backend_hold's stderr lines (`server argv:`, restarts), pass through untouched. Exit code = the
#             child's; no -e, no `--` or no child: exit 2. The preflight (--check's checks) runs first. The
#             dispatch below runs seat_claude.sh through this verb.
#
#   --check   OK, or one UNAVAILABLE line, from claude and litellm resolving, litellm at its floor, and
#             `backend_hold.py check` (port, window, backend id, exe and gguf against models.yaml). Starts nothing and
#             reads no online catalog.
#   dispatch  exit 2 with no SEAT_EFFORT (the server would start at the template default while the record names none).
#             Writes the run record to $SEAT_RECORD (tools/launch_check.py record), failed runs included.
# AI_WORKER_DIR overrides the ai-worker checkout (default ~/.config/ai-worker); UV_BIN overrides `uv`.
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lc="$here/tools/launch_check.py"
UV="${UV_BIN:-uv}"
# LiteLLM below this reads a chat-mode record's usage as unmeasured.
LITELLM_FLOOR="1.110.0"

win() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}
AIW="$(win "${AI_WORKER_DIR:-$HOME/.config/ai-worker}")"

litellm_bin() {
  local installed="$HOME/.local/bin/litellm.exe"
  if [ -x "$installed" ]; then printf '%s' "$installed"; else command -v litellm; fi
}

# `litellm --version` imports all of LiteLLM (13 s measured), so the installed tool's version is read from its
# dist-info folder name; a binary elsewhere is asked directly.
litellm_version() {
  local bin="$1" tooldir info
  if [ "$bin" = "$HOME/.local/bin/litellm.exe" ] && tooldir="$("$UV" tool dir 2>/dev/null)"; then
    tooldir="$(cygpath -u "$tooldir" 2>/dev/null || printf '%s' "$tooldir")"
    for info in "$tooldir"/litellm/Lib/site-packages/litellm-*.dist-info "$tooldir"/litellm/lib/python*/site-packages/litellm-*.dist-info; do
      [ -d "$info" ] && { basename "$info" .dist-info | sed 's/^litellm-//'; return 0; }
    done
  fi
  "$bin" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1
}

# Sets PORT, LITELLM and PROXY_VERSION, or prints one UNAVAILABLE line and exits (2: the row; 4: a missing tool).
preflight() {
  PORT="${SEAT_BASE_URL#*://}"
  PORT="${PORT%%/*}"
  PORT="${PORT##*:}"
  case "$PORT" in ''|*[!0-9]*) PORT="" ;; esac
  if [ -z "${SEAT_MODEL:-}" ] || [ -z "${SEAT_CONTEXT:-}" ] || [ -z "$PORT" ]; then
    echo "UNAVAILABLE (seats.json: the model row needs contextTokens and a baseUrl with a port)"
    exit 2
  fi
  command -v claude >/dev/null 2>&1 || { echo "UNAVAILABLE (claude CLI not found)"; exit 4; }
  LITELLM="$(litellm_bin)" || { echo "UNAVAILABLE (litellm not found)"; exit 4; }
  PROXY_VERSION="$(litellm_version "$LITELLM")"
  if [ -z "$PROXY_VERSION" ]; then echo "UNAVAILABLE (litellm version unreadable)"; exit 4; fi
  python3 -c 'import sys
def t(v): return tuple(int(x) for x in v.split("."))
sys.exit(0 if t(sys.argv[1]) >= t(sys.argv[2]) else 1)' "$PROXY_VERSION" "$LITELLM_FLOOR" || {
    echo "UNAVAILABLE (litellm $PROXY_VERSION < $LITELLM_FLOOR: chat-mode usage reads unmeasured)"
    exit 4
  }
}

if [ "${1:-}" = --check ]; then
  preflight
  exec "$UV" run --project "$AIW" python -u "$AIW/backend_hold.py" check \
    --backend "$SEAT_MODEL" --port "$PORT" --ctx "$SEAT_CONTEXT"
fi

# `hold`'s argument check, before anything starts. Sets EFFORT, SEAT and CHILD (an array).
hold_refuse() {
  echo "seat_local.sh: $1; usage: seat_local.sh hold -e <effort> [-m <seat>] -- <child argv...>" >&2
  exit 2
}
parse_hold() {
  EFFORT=""; SEAT="inkling-local"; CHILD=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -e) [ "$#" -ge 2 ] || hold_refuse "-e needs a value"; EFFORT="$2"; shift 2 ;;
      -m) [ "$#" -ge 2 ] || hold_refuse "-m needs a seat"; SEAT="$2"; shift 2 ;;
      --) shift; CHILD=("$@"); break ;;
      *) hold_refuse "unexpected argument $1" ;;
    esac
  done
  [ -n "$EFFORT" ] || hold_refuse "-e is required: the effort is fixed when the server starts"
  [ "${#CHILD[@]}" -gt 0 ] || hold_refuse "a child command is required after --"
}

# The inner half of `hold`: runs inside backend_hold.py, with the server up. Starts the proxy, runs the child under
# the proxy env, stops the proxy when the child exits, and exits with the child's code.
if [ "${1:-}" = --held ]; then
  shift
  [ "${1:-}" = -- ] && shift
  scratch="$SEAT_HOLD_SCRATCH"
  LITELLM="$(litellm_bin)"
  pport="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  pkey="sk-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  # The upstream key is the literal `local`: no real credential reaches a local server. LiteLLM 1.110+ refuses to start
  # keyless, so each run keys its own proxy.
  {
    printf 'model_list:\n  - model_name: %s\n    litellm_params:\n      model: openai/%s\n      api_base: %s\n      api_key: local\n' \
      "$SEAT_MODEL" "$SEAT_MODEL" "$SEAT_BASE_URL"
    printf '\ngeneral_settings:\n  master_key: %s\n' "$pkey"
    printf '\nlitellm_settings:\n  drop_params: true\n  use_chat_completions_url_for_anthropic_messages: true\n'
  } > "$scratch/litellm.yaml"
  env PYTHONUTF8=1 "$LITELLM" --config "$(win "$scratch/litellm.yaml")" --host 127.0.0.1 --port "$pport" >> "$scratch/litellm.log" 2>&1 &
  proxy_pid=$!
  trap 'kill "$proxy_pid" 2>/dev/null' EXIT
  # A cold LiteLLM import took 83 s on a loaded machine, so a live proxy is waited for; one that exited is refused at once.
  waited=$SECONDS
  until python3 -c 'import sys, urllib.request
urllib.request.urlopen("http://127.0.0.1:%s/health/liveliness" % sys.argv[1], timeout=2)' "$pport" 2>/dev/null; do
    kill -0 "$proxy_pid" 2>/dev/null || { echo "seat_local.sh: the litellm proxy exited before it became healthy:" >&2; tail -n 15 "$scratch/litellm.log" >&2; exit 4; }
    [ $((SECONDS - waited)) -lt "${SEAT_PROXY_WAIT_MAX:-600}" ] || { echo "seat_local.sh: the litellm proxy is alive but not healthy after ${SEAT_PROXY_WAIT_MAX:-600}s" >&2; exit 4; }
    sleep 0.4
  done
  # Claude Code assumes 200000 tokens for a model it does not know, so the window the server holds is declared, and
  # the output cap is the model's, bounded by a quarter of that window: the cap is also the compaction reserve, and
  # the bound keeps the first compaction at three quarters of the window or later.
  cap="${SEAT_MAX_OUTPUT:-$((SEAT_CONTEXT / 4))}"
  [ "$cap" -le $((SEAT_CONTEXT / 4)) ] || cap=$((SEAT_CONTEXT / 4))
  scrub=()
  while IFS='=' read -r name _; do
    case "$name" in CLAUDE_CODE_SIDECAR) ;; CLAUDE*|ANTHROPIC*) scrub+=(-u "$name") ;; esac
  done < <(env)
  env ${scrub[@]+"${scrub[@]}"} \
    ANTHROPIC_BASE_URL="http://127.0.0.1:$pport" ANTHROPIC_AUTH_TOKEN="$pkey" ANTHROPIC_API_KEY="" \
    ANTHROPIC_SMALL_FAST_MODEL="$SEAT_MODEL" CLAUDE_CODE_SUBAGENT_MODEL="$SEAT_MODEL" \
    CLAUDE_CODE_MAX_CONTEXT_TOKENS="$SEAT_CONTEXT" CLAUDE_CODE_MAX_OUTPUT_TOKENS="$cap" \
    SEAT_MODEL="$SEAT_MODEL" \
    "$@"
  exit $?
fi

# The verb: the child under backend_hold.py and the proxy. stdout and stderr pass through untouched, backend_hold's
# stderr lines included.
if [ "${1:-}" = hold ]; then
  shift
  parse_hold "$@"
  assign="$(python3 "$lc" seat "$SEAT")" || exit 2
  eval "$assign"
  export SEAT_NAME="$SEAT" SEAT_MODEL SEAT_CONTEXT SEAT_MAX_OUTPUT SEAT_BASE_URL
  case " $SEAT_EFFORTS " in
    *" $EFFORT "*) ;;
    *) echo "seat_local.sh: seat '$SEAT' takes -e one of: ${SEAT_EFFORTS:-none}" >&2; exit 2 ;;
  esac
  export SEAT_EFFORT="$EFFORT"
  preflight
  export SEAT_PROXY_VERSION="$PROXY_VERSION"
  if [ -z "${SEAT_HOLD_SCRATCH:-}" ]; then
    SEAT_HOLD_SCRATCH="$(mktemp -d)"
    trap 'rm -rf "$SEAT_HOLD_SCRATCH"' EXIT
  fi
  export SEAT_HOLD_SCRATCH
  # backend_hold.py spawns its child from Windows Python, where a bare `bash` resolves to WSL's bash, which cannot open
  # a C:/ path: this shell's own interpreter, by absolute path, runs the child.
  "$UV" run --project "$AIW" python -u "$AIW/backend_hold.py" run \
    --backend "$SEAT_MODEL" --port "$PORT" --ctx "$SEAT_CONTEXT" --effort "$EFFORT" \
    -- "$(win "${BASH:-/usr/bin/bash}")" "$(win "$here/scripts/seat_local.sh")" --held -- "${CHILD[@]}"
  exit $?
fi

# The dispatch: seat.sh's call. seat_claude.sh is the child of the hold verb.
prompt="$1"
workdir="$2"
if [ -z "${SEAT_EFFORT:-}" ]; then
  echo "seat_local.sh: SEAT_EFFORT is required: the effort is fixed when the server starts" >&2
  exit 2
fi
preflight
scratch="$(dirname "$SEAT_SETTINGS")"
record="${SEAT_RECORD:-$scratch/record.json}"
: > "$scratch/claude.json"
export SEAT_HOLD_SCRATCH="$scratch"
# `mcp__ai-worker` is denied: its calls queue on the GPU lock this run holds and would never return.
bash "$here/scripts/seat_local.sh" hold -e "$SEAT_EFFORT" -m "$SEAT_NAME" -- \
  env CLAUDE_CODE_ENTRYPOINT=cli CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  SEAT_OUTPUT_FORMAT=json SEAT_DISALLOWED="mcp__ai-worker" \
  bash "$here/scripts/seat_claude.sh" "$(win "$prompt")" "$(win "$workdir")" \
  > "$scratch/claude.json" 2> "$scratch/hold.err"
rc=$?
python3 "$lc" record "$SEAT_NAME" "$SEAT_MODEL" "$SEAT_EFFORT" "$rc" "$PROXY_VERSION" \
  "$(win "$scratch/claude.json")" "$(win "$scratch/hold.err")" "$(win "$record")" > "$scratch/answer.txt"
if [ "$rc" -ne 0 ]; then
  cat "$scratch/hold.err" "$scratch/claude.json" >&2
  exit "$rc"
fi
cat "$scratch/answer.txt"
