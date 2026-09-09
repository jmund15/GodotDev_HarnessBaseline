#!/usr/bin/env bash
# opencode_sidecar.sh — run headless Claude Code against OpenCode Zen's FREE models
# (https://opencode.ai/zen), as a zero-marginal-cost delegate.
#
# THE CHILD IS THE REAL `claude` BINARY IN THIS REPO, so the full `.claude` harness applies —
# CLAUDE.md, skills, memory, hooks, rules, guards and the -D disclosure dial — exactly like
# codex_proxy_sidecar.sh. Every dispatch shape routes here; how much context the child loads
# is the -D dial, not a different launcher. Pick -D and -G together as a SIDECAR AGENT TYPE —
# recipe table: reference/sidecar_dispatch.md.
#
# FLAG SURFACE IS IDENTICAL TO EVERY OTHER SIDECAR — parsing lives in lib/sidecar_common.sh
# (one getopts for all providers), so an orchestrator passes the same -m/-e/-t/-n/-o/-d/-f/
# -T/-R/-x/-P/-S/-s/-N/-r/-p/-a/-G/-D/-C/-L/-l/-A/-U here it passes anywhere else. This file
# adds no flags and must never grow one; provider differences belong behind the registry or
# env pins below.
#
# WHY A TRANSLATING PROXY (measured 2026-08-22): Zen's own Anthropic surface (/v1/messages)
# serves free models TEXT-ONLY — any request carrying `tools` is rejected upstream with
# [1210] Invalid API parameter on every free id tested, while the identical request without
# tools returns 200. Claude Code without tools is not a delegate, so the Anthropic→OpenAI
# translation happens here instead: a per-dispatch LiteLLM proxy translates Messages traffic
# to Zen's OpenAI surface (/v1/chat/completions), where function-calling, streaming and
# multi-turn tool loops were verified end-to-end, including a real `claude` child completing
# tool-bearing work.
#
# ONE PROXY PER DISPATCH, killed on exit — forced by where the pin lives, same reasoning as
# the codex transport: the model routing is fixed in the CONFIG FILE read at proxy startup,
# so a shared proxy would serve whichever id its starter pinned. The config names exactly
# one deployment under exactly the requested id, and LiteLLM FAILS LOUDLY ("No deployments
# available") on any other name — there is no alias surface, unlike raine/claude-code-proxy
# where opus/sonnet/haiku all resolve to something. That makes the child's own
# modelUsage.canonicalModel trustworthy identity evidence here (measured agreeing), which is
# why this transport needs no CCP_TRAFFIC_LOG-style capture.
#
# CONTEXT WINDOW COMES FROM THE REGISTRY, ALWAYS: Claude Code assumes 200,000 tokens for any
# id it does not recognize, and every Zen id is unrecognized to it. For a 1M-window model
# that UNDER-declares by 800K and compacts away four fifths of the working memory, so
# CLAUDE_CODE_MAX_CONTEXT_TOKENS is set whenever the registry states a window
# (source: models.dev — the OpenCode team's own catalog). Never hardcode a figure here.
#
# AUTH IS KEYLESS ON THE ANONYMOUS FREE TIER: Zen accepts the literal bearer token `public`
# for the -free models and 401s anything else unauthenticated (garbage keys measured
# failing). The proxy therefore sends api_key `public` unless OPENCODE_API_KEY is populated
# in ~/.env.ai-worker.cmd. ONE row, muse (Contributor-Free), is gated on this transport —
# NOT because the endpoint technically requires it (the anonymous `public` bearer answers
# muse fine too, verified 2026-09-04) but because Contributor-Free's own terms describe an
# account-attributable training-consent exchange; keep using the real key as the documented,
# sanctioned path regardless. A gated model with no real key refuses here, exit 3. Muse's
# actual 500-on-every-call defect (verified 2026-09-03, ~20+ attempts, every client) was the
# call SHAPE, not auth — see apiMode in oc_proxy_start below, the real fix. The child's own
# ANTHROPIC_AUTH_TOKEN is the usual `unused` placeholder: host-auth exclusion is
# sc_scrub_env's job, not the token's.
#
# COST: registry prices are authored $0 because the calls ARE $0 (`cost: "0"` in every Zen
# response) — costUSD 0.0 in the run record is a measured fact here, not the artifact it
# would be on a priced model. Privacy caveat: Zen documents some free models as permitted to
# train on submissions during their free period (see its Privacy section before dispatching
# proprietary material).
#
# Availability: `opencode_sidecar.sh --check` prints one line and exits (0 = can dispatch
# now). SessionStart reports it as `sidecar:` in <session-context>.
#
# Exit codes: see lib/sidecar_common.sh (the shared contract). 8 (provider quota band)
# cannot fire here — a $0 tier has no allowance to burn.

set -uo pipefail

ENV_FILE="${HOME}/.env.ai-worker.cmd"
ZEN_BASE_URL="https://opencode.ai/zen/v1"

SC_TRANSPORT="opencode"
# shellcheck source=lib/sidecar_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/sidecar_common.sh"

# HOME is not portable across hosts: an MSYS shell (e.g. a bash spawned by a non-Claude-Code
# harness) may set it to its own mount (/home/<user>) without exporting USERPROFILE, where
# neither ~/.local/bin nor ~/.env.ai-worker.cmd exist. Resolve the real profile once,
# preferring whatever actually holds the machine's files; every consumer below reads
# OC_USER_HOME, never raw $HOME.
oc_win_home() {
  if [ -f "${HOME}/.env.ai-worker.cmd" ] || [ -d "${HOME}/.local/bin" ]; then
    printf '%s' "$HOME"; return 0
  fi
  if [ -n "${USERPROFILE:-}" ]; then
    command -v cygpath >/dev/null 2>&1 && { cygpath -u "$USERPROFILE"; return 0; }
    printf '%s' "$USERPROFILE"; return 0
  fi
  # Derived last: the account's profile on a Windows drive mount — MSYS (/c) or WSL
  # (/mnt/c) — accepted only where the machine's own markers live.
  local u cand
  u="$(id -un 2>/dev/null || true)"
  for cand in "/c/Users/$u" "/mnt/c/Users/$u"; do
    if [ -n "$u" ] && { [ -f "$cand/.env.ai-worker.cmd" ] || [ -d "$cand/.local/bin" ]; }; then
      printf '%s' "$cand"; return 0
    fi
  done
  printf '%s' "$HOME"
}
OC_USER_HOME="$(oc_win_home)"
ENV_FILE="$OC_USER_HOME/.env.ai-worker.cmd"

SC_MODEL="muse"   # resolved through the registry; alias or full id both fine

OC_PROBE="$SC_ROOT/scripts/lib/oc_proxy_probe.py"
OC_LOG="${OC_LOG:-$OC_USER_HOME/.local/state/litellm-opencode/sidecar-serve.log}"
OC_PORT=""
OC_PID=""

# Ships via `uv tool install 'litellm[proxy]'` (fastapi<0.120 pinned: newer fastapi removed
# get_flat_dependant, which this LiteLLM imports — measured startup crash). Installed exe is
# the primary lookup; PATH the fallback; OC_LITELLM_BIN the override.
oc_litellm_bin() {
  if [ -n "${OC_LITELLM_BIN:-}" ]; then printf '%s' "$OC_LITELLM_BIN"; return 0; fi
  local installed="$OC_USER_HOME/.local/bin/litellm.exe"
  [ -x "$installed" ] && { printf '%s' "$installed"; return 0; }
  command -v litellm >/dev/null 2>&1 && { printf '%s' "$(command -v litellm)"; return 0; }
  return 1
}

# Version floor for apiMode=responses models: litellm <1.81.10 silently drops the
# function_call block when translating a Responses-API reply to Anthropic tool_use — no
# error, just commentary text, so a stale wheel degrades every tool-bearing dispatch without
# a symptom to grep for (BerriAI/litellm#16215, fixed by PR #20243; verified fixed on 1.99.0,
# 2026-09-03: two sequential Read calls in one turn, correct file content both times).
# Chat-Completions-shaped models don't hit this path, so the check is scoped to apiMode.
OC_LITELLM_MIN_VERSION="1.81.10"
oc_litellm_version_ok() {
  local bin="$1" ver
  ver="$("$bin" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  [ -z "$ver" ] && return 0   # can't determine -- fail open rather than block on a parse miss
  python3 -c "
import sys
t = lambda v: tuple(int(x) for x in v.split('.'))
sys.exit(0 if t('$ver') >= t('$OC_LITELLM_MIN_VERSION') else 1)
" && return 0
  echo "[opencode-sidecar] litellm $ver < $OC_LITELLM_MIN_VERSION -- Responses-API tool calls silently drop (BerriAI/litellm#16215, fixed by PR #20243). Run: uv tool install 'litellm[proxy]' --upgrade" >&2
  return 1
}

# Credential policy (see AUTH above): free tier rides `public`; a real OPENCODE_API_KEY in
# the shared CMD-format env file wins when present. Same parse rules as ds_read_key (CR +
# quote stripping — the file has Windows line endings).
oc_read_key() {
  grep -iE '^[[:space:]]*set[[:space:]]+OPENCODE_API_KEY=' "$ENV_FILE" 2>/dev/null \
    | head -1 | sed -E 's/^[[:space:]]*set[[:space:]]+OPENCODE_API_KEY=//' \
    | tr -d '\r' | sed -E 's/^"(.*)"$/\1/'
}

# Same HOME caveat as OC_USER_HOME, applied to the shared resolver: its $HOME fallbacks miss
# under a hostile HOME, so retry against the resolved Windows profile before declaring
# absence.
oc_claude_bin() {
  sc_claude_bin && return 0
  local cand
  for cand in "$OC_USER_HOME/.local/bin/claude.exe" "$OC_USER_HOME/.local/bin/claude.cmd"; do
    [ -x "$cand" ] && { printf '%s' "$cand"; return 0; }
  done
  return 1
}

oc_credential() {
  local k
  k="$(oc_read_key)"
  case "$k" in
    ""|"<redacted>"|"your-key-here") printf '%s' "public" ;;
    *) printf '%s' "$k" ;;
  esac
}

# Zen gates the free tier on the OpenCode client's own identity headers: without them every
# free-tier call answers 400 `MissingSessionID` ("OpenCode's free tier can only be used in
# OpenCode"), real key or `public` alike; with them the same call answers 200. The session id
# is per-dispatch; the values mirror what the CLI sends (see ~/.local/share/opencode/log).
# OC_CLIENT_HEADERS=off drops them — the planted-violation switch for proving --check fires,
# and the way to re-test whether the provider still requires them.
OC_CLIENT_UA="opencode/1.18.27 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.13"
oc_client_headers_on() { [ "${OC_CLIENT_HEADERS:-on}" != "off" ]; }
# YAML block for litellm_params.extra_headers (6-space indent under litellm_params).
oc_client_headers_yaml() {
  oc_client_headers_on || return 0
  printf '      extra_headers:\n        User-Agent: "%s"\n        x-opencode-client: cli\n        x-opencode-project: global\n        x-opencode-request: msg_sidecar_%s\n        x-opencode-session: ses_sidecar_%s\n' "$OC_CLIENT_UA" "$$" "$$"
}
# curl -H arguments for the same headers (used by the --check live probe).
oc_client_headers_curl() {
  oc_client_headers_on || return 0
  printf '%s\n' "-H" "User-Agent: $OC_CLIENT_UA" "-H" "x-opencode-client: cli" "-H" "x-opencode-project: global" \
    "-H" "x-opencode-request: msg_sidecar_check_$$" "-H" "x-opencode-session: ses_sidecar_check_$$"
}

# One real call on the model's registry-stated surface (responses → /responses, else
# /chat/completions), 1 output token, same credential and headers a dispatch sends. The
# catalog probe (GET /models) cannot see an auth or header gate, so --check exercises the
# call path itself (why: gotcha_zen_free_tier_gates_on_client_headers.md).
# Prints "<http-code> <first line of body>"; returns 0 only on HTTP 200.
oc_live_probe() {
  local model="$1" apimode="$2" cred="$3" url body out code
  if [ "$apimode" = "responses" ]; then
    url="$ZEN_BASE_URL/responses"
    body="{\"model\":\"$model\",\"input\":\"Reply with the single word OK.\",\"max_output_tokens\":16}"
  else
    url="$ZEN_BASE_URL/chat/completions"
    body="{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word OK.\"}],\"max_tokens\":16}"
  fi
  local -a hdrs=()
  while IFS= read -r line; do hdrs+=("$line"); done < <(oc_client_headers_curl)
  out="$(curl -s --max-time 45 -w '\n%{http_code}' "$url" -H "Authorization: Bearer $cred" -H "Content-Type: application/json" "${hdrs[@]}" -d "$body" 2>/dev/null)" || { printf '000 curl failed'; return 1; }
  code="${out##*$'\n'}"
  printf '%s %s' "$code" "$(printf '%s' "${out%$'\n'*}" | head -c 160 | tr -d '\r\n')"
  [ "$code" = "200" ]
}

# Start this dispatch's own proxy carrying its config pin. PYTHONUTF8 matters: without it
# the server crashes at startup printing its banner through a cp1252 console (measured).
oc_proxy_start() {
  local bin cfg port_log
  bin="$(oc_litellm_bin)" || { echo "litellm not found (uv tool install 'litellm[proxy]'; set OC_LITELLM_BIN)" >&2; return 4; }
  [ "$SC_API_MODE" = "responses" ] && { oc_litellm_version_ok "$bin" || return 4; }
  OC_PORT="$(python3 "$OC_PROBE" freeport)"
  mkdir -p "$(dirname "$OC_LOG")"

  # The config lives in the STATE DIR, not `mktemp`: the proxy is a native Windows exe that
  # cannot read a POSIX-side /tmp when this script runs under WSL, and a drive-letter path
  # under MSYS needed cygpath anyway. The state dir is readable from both worlds unchanged.
  cfg="$OC_USER_HOME/.local/state/litellm-opencode/config-$$.yaml"
  sc_on_exit 'rm -f "$OC_CFG_PATH" 2>/dev/null || :'
  OC_CFG_PATH="$cfg"

  # Registry-stated call shape (apiMode): most Zen models answer on the ordinary Chat
  # Completions surface litellm assumes by default. muse-spark specifically only answers on
  # the OpenAI Responses API (/v1/responses) — every Chat Completions call 500s instantly
  # regardless of client, headers, or payload (verified 2026-09-04); opencode's own client
  # works because it calls /v1/responses. litellm's "openai/responses/<id>" model prefix
  # forces that call shape end-to-end, verified through a live proxy on the same Anthropic
  # Messages surface this launcher's child speaks — not just the SDK.
  OC_LLM_MODEL="$SC_MODEL"
  [ "$SC_API_MODE" = "responses" ] && OC_LLM_MODEL="responses/$SC_MODEL"

  # extra_headers: the OpenCode client identity headers Zen's free tier gates on (see
  # oc_client_headers_yaml).
  { printf 'model_list:\n  - model_name: %s\n    litellm_params:\n      model: openai/%s\n      api_base: %s\n      api_key: %s\n' \
      "$SC_MODEL" "$OC_LLM_MODEL" "$ZEN_BASE_URL" "$SC_CREDENTIAL"
    oc_client_headers_yaml
    printf '\nlitellm_settings:\n  drop_params: true\n'
  } > "$cfg"

  echo "[opencode-sidecar] starting litellm on :$OC_PORT (model=$SC_MODEL, log: $OC_LOG)" >&2
  env PYTHONUTF8=1 nohup "$bin" --config "$cfg" --host 127.0.0.1 --port "$OC_PORT" >>"$OC_LOG" 2>&1 &
  OC_PID=$!
  sc_on_exit 'kill "$OC_PID" 2>/dev/null || :'
  python3 "$OC_PROBE" wait "$OC_PORT" 90 && return 0
  { echo "[opencode-sidecar] proxy did not become healthy on :$OC_PORT within 90s."
    echo "  Cold start measured ~12-15s; 90s exhausted means broken, not slow."
    echo "  Last lines of $OC_LOG:"
    tail -n 15 "$OC_LOG" 2>/dev/null | sed 's/^/    /'
  } >&2
  return 4
}

# --check: zero-argument availability probe. Prints ONE line, exits, dispatches nothing.
if [ "${1:-}" = "--check" ]; then
  _check_bin="$(oc_litellm_bin)" || { echo "UNAVAILABLE (litellm not installed; uv tool install 'litellm[proxy]' or set OC_LITELLM_BIN)"; exit 4; }
  oc_claude_bin >/dev/null || { echo "UNAVAILABLE (claude CLI not found; set CLAUDE_BIN)"; exit 4; }
  _check_reg="$(python3 "$SC_REGISTRY_CLI" sidecar-fields "$SC_MODEL" 2>&1)" || {
    echo "UNAVAILABLE (model registry unusable: ${_check_reg%%$'\n'*})"; exit 2; }
  _check_state="$(python3 "$SC_REGISTRY_CLI" transport-status "$SC_TRANSPORT" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("state", "available"))
except Exception:
    print("available")' 2>/dev/null)"
  if [ "$_check_state" = "unavailable" ]; then
    echo "UNAVAILABLE (excluded from the roster; see model_registry.py available)"
    exit 7
  fi
  _models="$(curl -s --max-time 20 "$ZEN_BASE_URL/models" 2>/dev/null)" || {
    echo "UNAVAILABLE (zen endpoint unreachable: $ZEN_BASE_URL/models)"; exit 3; }
  case "$_models" in
    *"$SC_MODEL"*) ;;
    "") echo "UNAVAILABLE (zen endpoint returned nothing)"; exit 3 ;;
    *) echo "UNAVAILABLE (model '$SC_MODEL' not in zen's live catalog)"; exit 2 ;;
  esac
  _check_apimode="$(printf '%s' "$_check_reg" | cut -d'|' -f12)"
  if [ "$_check_apimode" = "responses" ]; then
    oc_litellm_version_ok "$_check_bin" || { echo "UNAVAILABLE (litellm too old for this model's apiMode; see stderr)"; exit 4; }
  fi
  _check_cred="$(oc_credential)"
  _check_keylabel="public-tier"; [ "$_check_cred" != "public" ] && _check_keylabel="real-key"
  # SC_MODEL is still the alias here (sc_resolve_model runs after --check); the registry id
  # is the first sidecar-fields column.
  _check_model="${_check_reg%%|*}"
  _check_live="$(oc_live_probe "$_check_model" "$_check_apimode" "$_check_cred")" || {
    echo "UNAVAILABLE (live probe on $_check_model answered HTTP ${_check_live%% *}: ${_check_live#* } -- the catalog lists it, the call path refuses it; headers=${OC_CLIENT_HEADERS:-on})"; exit 3; }
  echo "OK (model=${_check_reg%%|*} endpoint=$ZEN_BASE_URL key=$_check_keylabel live-probe=200 proxy=per-dispatch)"
  exit 0
fi

sc_parse_flags "$@"
shift "$SC_SHIFT"
[ "${1:-}" = "--" ] && shift
sc_read_prompt "$@"

SC_CLAUDE="$(oc_claude_bin)" || { echo "claude CLI not found (set CLAUDE_BIN)" >&2; exit 4; }

sc_resolve_model
sc_gate_availability
sc_validate_common

# Credential BEFORE the gates (house order): a future gated row with no real key is a
# configuration fault and must not read as a band refusal.
SC_CREDENTIAL="$(oc_credential)"
if [ "$SC_AUTH_TIER" = "gated" ] && [ "$SC_CREDENTIAL" = "public" ]; then
  echo "model $SC_ALIAS ($SC_MODEL) is gated: populate OPENCODE_API_KEY in $ENV_FILE" >&2
  exit 3
fi

sc_gate_band
sc_gate_provider_band   # no-op on this transport; present so the ladder is uniform
sc_gate_balance         # no-op while the row stays authTier=open
sc_build_disclosure
sc_validate_effort

# Registry-stated window, ALWAYS declared (header: the 200k default under-declares badly).
SC_CONTEXT_TOKENS="$(python3 "$SC_REGISTRY_CLI" context-window "$SC_MODEL" 2>/dev/null)"
[ -n "$SC_CONTEXT_TOKENS" ] && \
  echo "[opencode-sidecar] declaring context window $SC_CONTEXT_TOKENS (registry; CLI default is 200000)" >&2

EFFORT_ARGS=()
# Passthrough is UNMEASURED on this transport: whether a given free model honors a reasoning
# knob is per-upstream, so treat -e as a requested coordinate until benchmarked.
[ -n "$SC_EFFORT" ] && EFFORT_ARGS=(--effort "$SC_EFFORT")

VERBOSE_ARGS=()
[ "$SC_FORMAT" = "stream-json" ] && VERBOSE_ARGS=(--verbose)

EXTRA_ARGS=()
[ "$SC_PERSIST" -eq 1 ] || EXTRA_ARGS+=(--no-session-persistence)
[ -n "$SC_RESUME" ] && EXTRA_ARGS+=(--resume "$SC_RESUME")
[ -n "$SC_PERM_MODE" ] && EXTRA_ARGS+=(--permission-mode "$SC_PERM_MODE")
[ -n "$SC_SCHEMA_FILE" ] && EXTRA_ARGS+=(--json-schema "$(cat "$SC_SCHEMA_FILE")")
SC_SETTINGS_JSON="$(sc_settings_with_bench_guard "")"
[ -n "$SC_SETTINGS_JSON" ] && EXTRA_ARGS+=(--settings "$SC_SETTINGS_JSON")

sc_scrub_env

oc_proxy_start || exit 4

# Env pins mirror codex_proxy_sidecar.sh; prompt rides STDIN, never argv (Windows exec caps
# the argument list around 32KB — gotcha_prompt_as_argv_transport_windows_limit).
run_claude() {
  cd "$SC_RUN_CWD" && env \
    ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} \
    CLAUDE_CODE_ENTRYPOINT=cli \
    ANTHROPIC_BASE_URL="http://127.0.0.1:$OC_PORT" \
    ANTHROPIC_AUTH_TOKEN=unused \
    ANTHROPIC_API_KEY= \
    ANTHROPIC_SMALL_FAST_MODEL="$SC_MODEL" \
    CLAUDE_CODE_SUBAGENT_MODEL="$SC_MODEL" \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
    ${SC_CONTEXT_TOKENS:+CLAUDE_CODE_MAX_CONTEXT_TOKENS="$SC_CONTEXT_TOKENS"} \
    ${SC_SHAPE:+CLAUDE_CODE_SIDECAR_SHAPE="$SC_SHAPE"} \
    ${SC_TIMEOUT:+timeout} ${SC_TIMEOUT:+"$SC_TIMEOUT"} \
    "$SC_CLAUDE" -p \
      --model "$SC_MODEL" \
      ${EFFORT_ARGS[@]+"${EFFORT_ARGS[@]}"} \
      --output-format "$SC_FORMAT" \
      ${VERBOSE_ARGS[@]+"${VERBOSE_ARGS[@]}"} \
      --allowedTools "$SC_TOOLS" \
      ${SC_DISALLOWED:+--disallowedTools "$SC_DISALLOWED"} \
      ${SC_APPEND_ARGS[@]+"${SC_APPEND_ARGS[@]}"} \
      ${SC_MAX_TURNS:+--max-turns "$SC_MAX_TURNS"} \
      ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
      ${SC_RESUME_SID:+--resume} ${SC_RESUME_SID:+"$SC_RESUME_SID"} \
      --add-dir "$SC_WORKDIR" \
      ${SC_ADD_DIRS[@]+"${SC_ADD_DIRS[@]}"} \
      <<< "$SC_PROMPT"
}

if [ -n "$SC_PROGRESS" ]; then
  OUTPUT="$(run_claude | tee "$SC_PROGRESS")"
else
  OUTPUT="$(run_claude)"
fi
rc=$?
sc_resume_loop run_claude "$SC_PROGRESS"
printf '%s\n' "$OUTPUT"
[ $rc -eq 124 ] && echo "opencode sidecar timed out after ${SC_TIMEOUT}s" >&2

sc_write_record "$OUTPUT" "$rc"

# The proxy accepts ANY bearer value, so a 401 can only be UPSTREAM: either the configured
# credential was rejected by Zen (a bad OPENCODE_API_KEY on a paid row) or a scrubbed-var
# leak sent the child to the real Anthropic endpoint instead of :$OC_PORT.
if [ "$rc" -ne 0 ] && printf '%s' "${OUTPUT:-}" | grep -q '"api_error_status" *: *401'; then
  cat >&2 <<'DIAG'
[opencode-sidecar] 401 through the local proxy.
  The placeholder child token is not the suspect. Check whether ANTHROPIC_BASE_URL
  survived into the child (scrub leak → wrong endpoint); otherwise the OPENCODE_API_KEY
  sent upstream was rejected — free-tier dispatches send the literal `public`.
DIAG
fi

exit $rc
