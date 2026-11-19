#!/usr/bin/env bash
# opencode_sidecar.sh oc_proxy_start: wait while the proxy process lives, refuse as soon as it exits, and show only
# this launch's lines of the shared serve log. A cold litellm import took 83 s on a loaded machine (2026-10-05), so a
# fixed 90 s budget refused slow-but-healthy proxies and printed a shared log's stale tail as the cause.
# The litellm binary is a stub; no network call.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
. "$R/scripts/lib/sidecar_common.sh"
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
fail=0
eval "$(sed -n '/^OC_LITELLM_MIN_VERSION=/p;/^oc_litellm_bin() {/,/^}/p;/^oc_litellm_version() {/,/^}/p;/^oc_litellm_version_ok() {/,/^}/p;/^oc_proxy_start() {/,/^}/p' "$R/scripts/opencode_sidecar.sh")"
OC_PROBE="$R/scripts/lib/oc_proxy_probe.py"
OC_USER_HOME="$D"
SC_MODEL="probe"; OC_UPSTREAM_MODEL="probe-model"; SC_API_MODE="chat"; ZEN_BASE_URL="http://127.0.0.1:9"
SC_CREDENTIAL="stub"; SC_TRANSPORT="go"; OC_SESSION_ID="0123"

cat > "$D/litellm-dies" <<'SH'
#!/usr/bin/env bash
echo "boom: this launch's own failure"
exit 1
SH
cat > "$D/litellm-slow" <<'SH'
#!/usr/bin/env bash
port=""; cfg=""; while [ $# -gt 0 ]; do [ "$1" = "--port" ] && port="$2"; [ "$1" = "--config" ] && cfg="$2"; shift; done
[ -r "$cfg" ] || { echo "no readable --config: $cfg"; exit 3; }
sleep 12
exec python3 -c "
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()" "$port"
SH
chmod +x "$D"/litellm-*

mkdir -p "$OC_USER_HOME/.local/state/litellm-opencode"
OC_LOG="$D/serve.log"
echo "OLD-LINE from an earlier launch" > "$OC_LOG"
OC_LITELLM_BIN="$D/litellm-dies"
start=$(date +%s)
out="$( (oc_proxy_start) 2>&1 )"; rc=$?
secs=$(( $(date +%s) - start ))
if [ "$rc" = 4 ] && [ "$secs" -lt 30 ] && [[ "$out" == *boom* ]] && [[ "$out" != *OLD-LINE* ]]; then
  echo "OK   a proxy that exits is refused at once, naming this launch's own log lines (${secs}s)"
else
  echo "FAIL dead proxy: rc=$rc ${secs}s out=${out: -300}"; fail=1
fi
if [ "$secs" -lt 8 ]; then
  echo "OK   a dead proxy is refused within one 2 s probe chunk, not a 10 s one (${secs}s)"
else
  echo "FAIL dead proxy refusal took ${secs}s; one probe chunk is 2 s plus the probe's two refused connects (4.5 s), a 10 s chunk costs 15 s"; fail=1
fi

OC_LITELLM_BIN="$D/litellm-slow"
OC_PROXY_WAIT_MAX=120
out="$( (oc_proxy_start && echo STARTED; kill "$OC_PID" 2>/dev/null) 2>&1 )"; rc=$?
if [[ "$out" == *STARTED* ]]; then
  echo "OK   a proxy still starting past one wait chunk is waited for while it lives"
else
  echo "FAIL slow proxy: rc=$rc out=${out: -300}"; fail=1
fi

cat > "$D/litellm-hang" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = "--version" ] && { echo "LiteLLM: Current Version = 1.99.0"; exit 0; }
exec sleep 15
SH
chmod +x "$D/litellm-hang"
OC_LITELLM_BIN="$D/litellm-hang"
OC_PROXY_WAIT_MAX=5
start=$(date +%s)
# Time the call where it returns: on Git Bash the killed stub's sleep keeps the substitution open until it ends.
out="$( (oc_proxy_start; r=$?; echo "RETURNED_AT=$(date +%s)"; kill "$OC_PID" 2>/dev/null; exit "$r") 2>&1 )"; rc=$?
returned="$(printf '%s' "$out" | sed -n 's/^RETURNED_AT=//p' | head -n 1)"
secs=$(( ${returned:-$(date +%s)} - start ))
reported="$(printf '%s' "$out" | sed -n 's/.*not healthy on :[0-9]* after \([0-9]*\)s.*/\1/p' | head -n 1)"
if [ "$rc" = 4 ] && [ -n "$reported" ] && [ "$reported" -ge $((secs - 1)) ]; then
  echo "OK   the wait cap and its message count wall time, not a nominal 2 s per probe chunk (${reported}s of ${secs}s)"
else
  echo "FAIL hung proxy: rc=$rc reported=${reported:-none} real=${secs}s out=${out: -200}"; fail=1
fi
OC_PROXY_WAIT_MAX=120

cat > "$D/litellm-cfg" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = "--version" ] && { echo "LiteLLM: Current Version = 1.99.0"; exit 0; }
while [ $# -gt 0 ]; do [ "$1" = "--config" ] && cp "$2" "$CFG_COPY"; shift; done
exit 1
SH
chmod +x "$D/litellm-cfg"
OC_LITELLM_BIN="$D/litellm-cfg"
flag="use_chat_completions_url_for_anthropic_messages: true"
for mode in chat responses; do
  export CFG_COPY="$D/cfg-$mode"
  (SC_API_MODE="$mode"; oc_proxy_start) >/dev/null 2>&1
  if [ ! -f "$CFG_COPY" ]; then
    echo "FAIL $mode: the stub never received a --config"; fail=1
  elif [ "$mode" = chat ] && ! grep -qF "$flag" "$CFG_COPY"; then
    echo "FAIL chat: config lets litellm bridge /v1/messages to /responses, which a chat-only Zen model rejects"; fail=1
  elif [ "$mode" = responses ] && grep -qF "$flag" "$CFG_COPY"; then
    echo "FAIL responses: config forces chat/completions on a responses-only model"; fail=1
  else
    echo "OK   $mode: the proxy config routes /v1/messages to the registry's surface"
  fi
done

# LiteLLM 1.110+ refuses to start without a master key, and older versions drop Go's chat usage: each launch writes a
# fresh key into its config and keeps it for the child, so the proxy is never keyless.
keys=""
for n in 1 2; do
  export CFG_COPY="$D/cfg-key-$n"
  key="$( (SC_API_MODE=chat; oc_proxy_start >/dev/null 2>&1; printf '%s' "${OC_PROXY_KEY:-}") )"
  in_cfg="$(sed -n 's/^  master_key: *//p' "$CFG_COPY" 2>/dev/null)"
  if [ -z "$key" ] || [ "$key" != "$in_cfg" ] || [[ "$key" != sk-* ]]; then
    echo "FAIL launch $n: the proxy key the child gets ('$key') is not the config's master_key ('$in_cfg')"; fail=1
  fi
  keys="$keys $key"
done
set -- $keys
if [ -n "${1:-}" ] && [ "${1:-}" != "${2:-}" ]; then
  echo "OK   each launch writes its own master key into the proxy config and keeps it for the child"
else
  echo "FAIL two launches did not get two distinct keys:$keys"; fail=1
fi

echo; [ "$fail" -eq 0 ] && echo "opencode proxy start: all passed" || echo "opencode proxy start: FAILED"
exit "$fail"
