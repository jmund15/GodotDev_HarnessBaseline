#!/usr/bin/env bash
# opencode_sidecar.sh --check: the LiteLLM requirement follows the row's apiMode, and an
# unresolvable -m is a resolution failure (exit 2), and a probe that times out says so instead of
# reporting a refusal. Every network tool is a stub: no live call.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$HERE/.."
LAUNCHER="$R/scripts/opencode_sidecar.sh"
FAIL=0; PASS=0
ok() { echo "OK   $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL $1 -- rc=$2 out=$3"; FAIL=$((FAIL+1)); }

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
mkdir -p "$TMPD/home/.local/bin" "$TMPD/stubs" "$TMPD/markers" "$TMPD/tmp"
printf 'set OPENCODE_API_KEY=stub-key\r\n' > "$TMPD/home/.env.ai-worker.cmd"
cat > "$TMPD/stubs/curl" <<SH
#!/usr/bin/env bash
echo "\$*" >> "$TMPD/curl.calls"
[ -n "\${CURL_HTTP:-}" ] && { printf 'upstream body\n%s' "\$CURL_HTTP"; exit 0; }
exit "\${CURL_RC:-7}"
SH
cat > "$TMPD/stubs/claude" <<'SH'
#!/usr/bin/env bash
exit 0
SH
cat > "$TMPD/stubs/litellm-old" <<'SH'
#!/usr/bin/env bash
echo "LiteLLM: Current Version = 1.50.0"
SH
cat > "$TMPD/stubs/litellm-new" <<'SH'
#!/usr/bin/env bash
echo "LiteLLM: Current Version = 1.99.0"
SH
chmod +x "$TMPD/stubs/"*

# Synthetic Go rows, one per path, cloned from the live Go rows' shapes into a temp registry.
python3 - "$R/reference/external_models.json" "$TMPD/registry.json" <<'PY'
import copy, json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
go = [m for m in data["models"] if m["transport"] == "go"]
top = max(m["order"] for m in data["models"])
for n, mode in enumerate(("messages", "chat"), 1):
    base = next(m for m in go if m.get("apiMode") == mode)
    row = copy.deepcopy(base)
    row.update(id="probe-%s-model" % mode, alias="probe-%s" % mode, upstreamId="probe-%s-model" % mode,
               family="probe-%s" % mode, order=top + n)
    row.pop("servedIds", None)
    data["models"].append(row)
json.dump(data, open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PY

# PATH without any real litellm: drop every directory that holds one.
CLEAN_PATH="$TMPD/stubs"
IFS=':' read -r -a _dirs <<< "$PATH"
for d in "${_dirs[@]}"; do
  [ -e "$d/litellm" ] || [ -e "$d/litellm.exe" ] || CLEAN_PATH="$CLEAN_PATH:$d"
done

run_check() { # <model> [litellm-bin]
  rm -f "$TMPD/curl.calls"
  env -u OC_LITELLM_BIN HOME="$TMPD/home" USERPROFILE="$TMPD/home" PATH="$CLEAN_PATH" \
      TEMP="$TMPD/tmp" TMPDIR="$TMPD/tmp" SIDECAR_EXHAUSTED_DIR="$TMPD/markers" \
      HARNESS_MODEL_REGISTRY="$TMPD/registry.json" \
      CLAUDE_BIN="$TMPD/stubs/claude" ${2:+OC_LITELLM_BIN="$2"} \
      bash "$LAUNCHER" --check -m "$1" 2>/dev/null
}

out="$(run_check probe-chat)"; rc=$?
[ "$rc" = 4 ] && case "$out" in *litellm*) true ;; *) false ;; esac && [ ! -f "$TMPD/curl.calls" ] \
  && ok "a chat-mode Go row without LiteLLM fails --check before any live call" \
  || bad "a chat-mode Go row without LiteLLM fails --check before any live call" "$rc" "$out"

out="$(run_check probe-chat "$TMPD/stubs/litellm-old")"; rc=$?
[ "$rc" = 4 ] && case "$out" in *"too old"*) true ;; *) false ;; esac && [ ! -f "$TMPD/curl.calls" ] \
  && ok "a chat-mode Go row with LiteLLM below the floor fails --check" \
  || bad "a chat-mode Go row with LiteLLM below the floor fails --check" "$rc" "$out"

out="$(run_check probe-chat "$TMPD/stubs/litellm-new")"; rc=$?
[ "$rc" = 16 ] && case "$out" in *"live probe"*) true ;; *) false ;; esac && [ -f "$TMPD/curl.calls" ] \
  && ok "a chat-mode Go row with a current LiteLLM reaches the (stubbed) live probe" \
  || bad "a chat-mode Go row with a current LiteLLM reaches the (stubbed) live probe" "$rc" "$out"

out="$(run_check probe-messages)"; rc=$?
[ "$rc" = 16 ] && case "$out" in *"live probe"*) true ;; *) false ;; esac \
  && ok "a messages-mode Go row needs no LiteLLM and reaches the (stubbed) live probe" \
  || bad "a messages-mode Go row needs no LiteLLM and reaches the (stubbed) live probe" "$rc" "$out"

out="$(CURL_RC=28 OC_PROBE_TIMEOUT_SEC=5 run_check probe-messages)"; rc=$?
[ "$rc" = 16 ] && case "$out" in *"no answer within 5s"*) true ;; *) false ;; esac \
  && case "$out" in *refuses*) false ;; *) true ;; esac && grep -q -- "--max-time 5 " "$TMPD/curl.calls" \
  && ok "a probe that times out reports no answer within its timeout, not a refusal" \
  || bad "a probe that times out reports no answer within its timeout, not a refusal" "$rc" "$out"

out="$(CURL_HTTP=429 run_check probe-messages)"; rc=$?
[ "$rc" = 16 ] && case "$out" in *"answered HTTP 429"*) true ;; *) false ;; esac \
  && ok "an overloaded probe (HTTP 429) exits 16, endpoint unavailable, so a fan-out moves the job" \
  || bad "an overloaded probe (HTTP 429) exits 16, endpoint unavailable, so a fan-out moves the job" "$rc" "$out"

out="$(CURL_HTTP=401 run_check probe-messages)"; rc=$?
[ "$rc" = 3 ] && case "$out" in *"HTTP 401"*) true ;; *) false ;; esac \
  && ok "a refused probe (HTTP 401) keeps exit 3, credential, and does not move" \
  || bad "a refused probe (HTTP 401) keeps exit 3, credential, and does not move" "$rc" "$out"

out="$(run_check no-such-model-xyz)"; rc=$?
[ "$rc" = 2 ] && [ ! -f "$TMPD/curl.calls" ] \
  && ok "an unknown -m exits 2 (resolution failure)" \
  || bad "an unknown -m exits 2 (resolution failure)" "$rc" "$out"

echo
echo "opencode --check: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
