#!/usr/bin/env bash
# opencode_run_sidecar.sh — the free `opencode run` seat (transport opencode-cli).
#
# The genuine OpenCode CLI runs one job of any launcher kind (scout, lens, reviewer, author, judge, gate, arm)
# against a Zen free model. It runs none of .claude/hooks, so the confinement is the per-dispatch agent
# permission config below, probed before spend and attested after the run from the CLI's own session
# export. The kind's built-ins (guards/tool_profiles.json, through sc_resolve_tools) map to the CLI's
# permission names; a built-in with no mapping refuses (exit 2). The seat serves no MCP, so a kind's
# servers are dropped and the drop is recorded in the run record's toolProfile.seatDropped, as are
# the websearch and webfetch tools a read-only job once had. A job whose resolved set carries edit or
# bash (author, arm, a `-t +Bash` delta) may write, but only inside the `-d` root: edit is allowed, `external_directory`
# admits the root and the staged input files only, and bash is allowed except for commands that name a
# vault, holdout or sibling-cell path or a git remote operation. Those bash rules are patterns over the
# command text, so a relative walk (`cat ../sibling/x`) is outside what they can see; the CLI's own
# path check and `escape_sweep.py` are the other layers. A write shape refuses to run in the checkout
# that holds this launcher. `-p` is accepted and inert (every rule is a total allow or deny); `-Z` sets
# the stall signal (`oc_watch_stall`), and `-T` alone ends a wedged run; `-r` is not served.
#
# A bare `"deny"` removes the tool from the request, and Zen's free tier refused such a request with
# 403 FreeTierError (measured 2026-09-27); a deny written as pattern rules keeps every tool offered and
# refuses each call at execution. Every denial here is therefore a pattern rule. Running the CLI
# through WSL changes nothing: WSL's `opencode` is the same Windows binary, it receives the config only
# through WSLENV, and without it the run silently falls back to the unrestricted `build` agent.
set -uo pipefail

SC_TRANSPORT=opencode-cli
case "${BASH_SOURCE[0]}" in
  */sidecar-snapshots/*) SC_LAUNCHER_DIR="${SC_LAUNCHER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}" ;;
  *) SC_LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" ;;
esac
. "$SC_LAUNCHER_DIR/lib/sidecar_common.sh"
sc_reexec_snapshot "$@"

OC_AGENT=sidecar-readonly
OC_ALLOWED=read,glob,grep   # the --check probe's set; a job's comes from its resolved kind
OC_ROOT=""
OC_EXTRAS=""
# Without these the CLI loads ~/.claude instructions and skills into a free model's context and appends
# the nearest CLAUDE.md to every file it reads under .claude/, so `-D pointer` would not be pointer;
# `--pure` keeps user and project plugins, which run outside the permission config, off.
export OPENCODE_DISABLE_CLAUDE_CODE=1

oc_ensure_output() {
  [ -n "${OUTPUT:-}" ] && return 0
  OUTPUT="$(python3 - "$1" <<'PY'
import json, sys
print(json.dumps({'type': 'result', 'result': f'Launcher stopped before a CLI result (exit {sys.argv[1]}); see stderr.',
                  'structured_output': None, 'is_error': True}))
PY
)"
}

oc_preparse_refusal() {
  echo "$1" >&2
  if [ -n "$SC_RECORD" ] && [ -z "${SIDECAR_DETACHED:-}" ]; then
    sc_pid_line "$$" > "${SC_RECORD}.pid" 2>/dev/null || :
    oc_ensure_output 2
    sc_write_durable_files 2
  fi
  exit 2
}

# oc_validate_flags <allowed-value-flags> <allowed-switches> "$@"
oc_validate_flags() {
  local valued="$1" switches="$2" token previous="" takes_value=0
  shift 2
  for token in "$@"; do
    if [ "$takes_value" -eq 1 ]; then
      [ "$previous" != '-R' ] || SC_RECORD="$token"
      takes_value=0
      continue
    fi
    previous="$token"
    case "$token" in
      -r) oc_preparse_refusal '-r refused: resume is not served by the free CLI seat' ;;
      --) break ;;
      -?)
        case " $valued " in *" $token "*) takes_value=1; continue ;; esac
        case " $switches " in *" $token "*) continue ;; esac
        oc_preparse_refusal "flag $token refused: the free CLI seat does not serve it" ;;
      -*) oc_preparse_refusal "flag $token refused: the free CLI seat does not serve it" ;;
    esac
  done
  [ "$takes_value" -eq 0 ] || oc_preparse_refusal 'missing flag value'
}

oc_bin() {
  if [ -n "${OPENCODE_BIN:-}" ]; then printf '%s' "$OPENCODE_BIN"; return 0; fi
  command -v opencode
}

oc_native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -am "$1"; else realpath -m "$1"; fi
}

oc_upstream() {
  python3 - "$SC_MODEL" "$SC_ROOT/tools" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
import model_registry
row = model_registry.resolve(sys.argv[1])
if row['transport'] != 'opencode-cli':
    raise SystemExit(f"{sys.argv[1]} is on transport {row['transport']}, not opencode-cli")
print(row.get('upstreamId') or row['id'])
PY
}

# The live catalog is the authority for price and variants: a row whose upstream id is absent, priced
# above zero, or asked for a variant it does not serve refuses before any model call.
oc_catalog_gate() {
  local listing rc
  if [ -n "${SC_EFFORT:-}" ] && [ "$SC_EFFORT" != none ] && ! timeout 30 "$SC_BIN" run --help < /dev/null 2>&1 | grep -q -- '--variant'; then
    echo "-e $SC_EFFORT refused: this opencode CLI has no --variant flag" >&2
    return 2
  fi
  listing="$(mktemp)"
  local attempt=1 delay waits
  read -ra waits <<< "${SIDECAR_CATALOG_LOCK_WAITS:-2 5}"
  while :; do
    timeout 30 "$SC_BIN" models opencode --verbose < /dev/null > "$listing" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ] || ! grep -qF 'database is locked' "$listing" || [ "$attempt" -ge 3 ]; then
      break
    fi
    delay="${waits[attempt - 1]:-${waits[0]:-2}}"
    sleep "$delay"
    attempt=$((attempt + 1))
  done
  python3 - "$(sc_to_native "$listing")" "$SC_UPSTREAM" "$([ "${SC_EFFORT:-}" = none ] && printf '' || printf '%s' "${SC_EFFORT:-}")" "$rc" <<'PY'
import json, re, sys
path, upstream, effort, rc = sys.argv[1:5]
text = open(path, encoding='utf-8', errors='replace').read()
if rc != '0':
    raise SystemExit(f'catalog probe failed (exit {rc}): {text[-300:]!r}')
blocks = re.split(r'^opencode/(\S+)\r?\n', text, flags=re.M)
catalog = {}
for i in range(1, len(blocks), 2):
    try:
        catalog[blocks[i]] = json.loads(blocks[i + 1])
    except ValueError:
        pass
info = catalog.get(upstream)
if info is None:
    raise SystemExit(f'opencode/{upstream} is absent from the live CLI catalog')
cost = info.get('cost') or {}
priced = {k: v for k, v in cost.items() if k != 'cache' and v} | {f'cache.{k}': v for k, v in (cost.get('cache') or {}).items() if v}
if priced or not cost:
    raise SystemExit(f'opencode/{upstream} is not free in the live catalog ({cost!r}); the free seat refuses it')
variants = sorted((info.get('variants') or {}).keys())
if effort and effort not in variants:
    raise SystemExit(f"-e {effort} refused: opencode/{upstream} serves variants {variants or 'none'}")
PY
  rc=$?
  rm -f "$listing"
  return "$rc"
}

# oc_permission_config <agent> <allowed-csv> <root> <extra-paths-joined-by-|> -> the CLI config JSON.
# Every denial is a pattern rule over `*` and `**`; a bare "deny" drops the tool from the request.
# Without a root the policy is the read-only one. With a root, edit and bash are admitted only as the
# rules below confine them; bash rules resolve last-match-wins, so the later group overrides the earlier.
oc_permission_config() {
  python3 - "$1" "$2" "${3:-}" "${4:-}" <<'PY'
import json, re, sys
agent, allowed, root, extras = sys.argv[1:5]
allowed = set(filter(None, allowed.split(',')))
extras = [e for e in extras.split('|') if e]
deny_every_call = {'*': 'deny', '**': 'deny'}

def fwd(path):
    return path.replace('\\', '/').rstrip('/')

def back(path):
    return fwd(path).replace('/', '\\')

def named_in_command(path, directory):
    plain = fwd(path)
    spelled = [plain, back(path)]
    if re.match(r'^[A-Za-z]:/', plain):
        spelled.append('/' + plain[0].lower() + plain[2:])
    patterns = []
    for spelling in spelled:
        stops = [' ', '"', "'", ';', '&', '|', ')'] + (['\\' if '\\' in spelling else '/'] if directory else [])
        patterns += ['*' + spelling] + ['*' + spelling + stop + '*' for stop in stops]
    return patterns

permission = {'*': dict(deny_every_call),
              'read': ({'*': 'allow', '*.env': 'deny', '*.env.*': 'deny', '*.env.example': 'allow'}
                       if 'read' in allowed else dict(deny_every_call))}
for name in ('glob', 'grep'):
    permission[name] = 'allow' if name in allowed else dict(deny_every_call)
# The CLI's schema takes only an action string for these two (measured 2026-10-10: a pattern map is
# "Expected PermissionActionConfig"), so a removed one is left to the `*` deny, which keeps it offered.
for name in ('websearch', 'webfetch'):
    if name in allowed:
        permission[name] = 'allow'
if 'edit' in allowed:
    permission['edit'] = {'*': 'allow'}
if 'bash' in allowed:
    rules = {'*': 'allow', '*BenchmarkArms*': 'deny'}
    rules.update({p: 'allow' for p in named_in_command(root, True)})
    rules.update({noun: 'deny' for noun in ('*OneDrive*', '*ObsidianVault*', '*BenchmarkHoldout*')})
    for extra in extras:
        rules.update({p: 'allow' for p in named_in_command(extra, False)})
    rules.update({p: 'deny' for p in ('*git *fetch*', '*git *remote*', '*git *clone*', '*git *push*', '*git *pull*',
                                      '*git *submodule update*', '*ls-remote*')})
    permission['bash'] = rules
if 'todowrite' in allowed:
    permission['todowrite'] = 'allow'
if root:
    outside = {'*': 'deny', fwd(root) + '/**': 'allow', back(root) + '\\**': 'allow'}
    for extra in extras:
        folder = fwd(extra).rsplit('/', 1)[0]
        outside.update({folder + '/*': 'allow', folder.replace('/', '\\') + '\\*': 'allow'})
    permission['external_directory'] = outside
print(json.dumps({'agent': {agent: {'mode': 'primary', 'permission': permission}}}))
PY
}

# oc_allowed_tools -> the CLI permission names this job may use, comma-separated in a fixed order: the
# resolved kind's built-ins (SC_RESOLVED_BUILTINS) through the closed Claude-name table, less `-x`, so a
# name in `-t` and `-x` is denied. A `-x` name with no CLI counterpart (Task, Agent, mcp__*) is a no-op.
oc_allowed_tools() {
  python3 - "${SC_RESOLVED_BUILTINS:-}" "${SC_DISALLOWED:-}" <<'PY'
import sys
resolved, disallowed = sys.argv[1:3]
table = {'read': 'read', 'glob': 'glob', 'grep': 'grep', 'websearch': 'websearch', 'webfetch': 'webfetch',
         'edit': 'edit', 'write': 'edit', 'multiedit': 'edit', 'notebookedit': 'edit', 'bash': 'bash',
         'todowrite': 'todowrite'}
order = ['read', 'glob', 'grep', 'websearch', 'webfetch', 'edit', 'bash', 'todowrite']

def mapped(name):
    return table.get(name.lower())

allowed = set()
for name in filter(None, (n.strip() for n in resolved.split(','))):
    if name.lower().startswith('mcp__'):
        continue
    if mapped(name) is None:
        raise SystemExit(f'{name} refused: the free CLI seat has no permission for it (it serves Read, Glob, '
                         'Grep, Edit, Write, MultiEdit, NotebookEdit, Bash, TodoWrite, WebSearch and WebFetch)')
    allowed.add(mapped(name))
allowed -= {mapped(name) for name in disallowed.split(',') if name}
print(','.join(t for t in order if t in allowed))
PY
}

# The seat passes no MCP config, so the kind's servers never load. The run record says so: the profile
# keeps the built-ins it launched with, lists no servers, and names what the seat dropped.
oc_record_seat_drops() {
  SC_TOOL_PROFILE="$(printf '%s' "$SC_TOOL_PROFILE" | python3 -c 'import json, sys
p = json.load(sys.stdin)
reads_only = sys.argv[1] not in ("arm", "author")
p["seatDropped"] = {"mcpServers": p["mcpServers"], "tools": ["websearch", "webfetch"] if reads_only else []}
p["mcpServers"] = []
p["mcpRegistrations"] = {}
print(json.dumps(p, indent=2))' "$SC_SHAPE")" || sc_die 2 "[sidecar] could not record the seat's tool drops"
}

# A write shape is confined by permission rules alone, so it never runs in the checkout that holds this
# launcher, where the hooks and the owner's uncommitted work live.
oc_refuse_own_checkout() {
  case ",$OC_ALLOWED," in *,edit,*|*,bash,*) ;; *) return 0 ;; esac
  local mine theirs
  mine="$(git -C "$SC_ROOT" rev-parse --show-toplevel 2>/dev/null)" || return 0
  theirs="$(git -C "$SC_WORKDIR" rev-parse --show-toplevel 2>/dev/null)" || return 0
  [ "$(oc_native "$mine" | tr '[:upper:]' '[:lower:]')" != "$(oc_native "$theirs" | tr '[:upper:]' '[:lower:]')" ] ||
    sc_die 2 "-G $SC_SHAPE refused: -d is this launcher's own checkout ($theirs); a write shape needs a cell root or a linked worktree because no .claude/hooks run in the CLI"
}

# Reads the CLI's own resolved agent and fails closed unless every tool it offers resolves to the
# intended action: the approved set allows, everything else denies, and no tool is dropped from the
# request (a dropped tool is what draws the free tier's 403).
oc_permission_gate() {
  local resolved rc
  resolved="$(mktemp)"
  OPENCODE_CONFIG_CONTENT="$OC_CONFIG" timeout 30 "$SC_BIN" debug agent "$OC_AGENT" --pure < /dev/null > "$resolved" 2>&1
  rc=$?
  python3 - "$(sc_to_native "$resolved")" "$rc" "$OC_ALLOWED" "$OC_ROOT" "$OC_EXTRAS" <<'PY'
import json, re, sys
path, rc, allowed_csv, root, extras_joined = sys.argv[1:6]
text = open(path, encoding='utf-8', errors='replace').read()
if rc != '0':
    raise SystemExit(f'permission probe failed (exit {rc}): {text[-300:]!r}')
agent = json.loads(text)
rules = agent.get('permission') or []
allowed = set(filter(None, allowed_csv.split(',')))
extras = [e for e in extras_joined.split('|') if e]
edit_tools = {'edit', 'write', 'patch', 'multiedit', 'apply_patch'}
path_tools = {'read', 'glob', 'grep', 'edit'}

def match(pattern, value):
    return re.fullmatch('.*'.join(re.escape(p) for p in pattern.split('*')).replace(r'\?', '.'), value, re.S) is not None

def action(permission, target):
    hit = [r for r in rules if match(r['permission'], permission) and match(r.get('pattern', '*'), target)]
    return hit[-1]['action'] if hit else 'ask'

def fold(value):
    return value.replace('\\', '/').rstrip('/').lower()

def outside(target):
    if not re.match(r'^([A-Za-z]:)?[\\/]', target):
        return False
    here = fold(target)
    return not (here == fold(root) or here.startswith(fold(root) + '/') or here in {fold(e) for e in extras})

def resolve(tool, target):
    permission = 'edit' if tool in edit_tools else tool
    if root and permission in path_tools and outside(target):
        return 'external_directory', action('external_directory', target.replace('\\', '/').rsplit('/', 1)[0] + '/*')
    return permission, action(permission, target)

offered = agent.get('tools') or {}
dropped = sorted(t for t, on in offered.items() if not on)
if dropped:
    raise SystemExit(f'permission config drops tools {dropped} from the request; the free tier refuses that')

checks = []
for tool in sorted(set(offered) | {'bash', 'edit', 'task', 'external_directory'}):
    permission = 'edit' if tool in edit_tools else tool
    on = permission in allowed
    verdict = 'allow' if on else 'deny'
    if not root:
        checks += [(tool, target, verdict) for target in ('src/x.cs', 'rm -rf .', 'general')]
    elif permission in path_tools:
        checks += [(tool, root + '/src/x.cs', verdict), (tool, 'src/x.cs', verdict),
                   (tool, 'C:/Users/gate-outside/x.txt', 'deny')]
        if permission == 'read':
            checks += [(tool, extra, verdict) for extra in extras]
    elif permission == 'bash':
        if on:
            checks += [(tool, 'ls src', 'allow'), (tool, f'ls {root}/src', 'allow'), (tool, f'cd "{root}" && ls', 'allow')]
            checks += [(tool, f'sed -n 1,5p {extra}', 'allow') for extra in extras]
            checks += [(tool, command, 'deny') for command in (
                'cat C:/Users/gate-outside/OneDrive/Documents/x.md', 'cat /c/Users/gate-outside/ObsidianVault/x.md',
                'cat C:/Users/gate-outside/Game_Dev/BenchmarkHoldout/keys.json',
                'cat C:/Users/gate-outside/Game_Dev/BenchmarkArms/zz-sibling-cell/raw.json',
                f'cp {root}/a C:/Users/gate-outside/ObsidianVault/a',
                'git fetch origin', 'git -C . fetch origin', 'git -c k=v fetch', "bash -c 'git fetch origin'",
                'env git fetch', 'git pull', 'git ls-remote', 'git push origin main', 'git clone https://x/y')]
            if extras:
                checks.append((tool, f'git fetch origin # {extras[0]}', 'deny'))
            if 'benchmarkarms' in fold(root):
                checks.append((tool, f'cat {root}0/raw.json', 'deny'))
        else:
            checks += [(tool, target, 'deny') for target in ('ls src', 'git fetch origin')]
    elif permission == 'external_directory':
        checks += [(tool, root + '/x/*', 'allow'), (tool, 'C:/Users/gate-outside/*', 'deny')]
    elif permission in ('todowrite', 'websearch', 'webfetch'):
        checks.append((tool, 'x', verdict))
    else:
        checks.append((tool, 'general', verdict))
wrong = []
for tool, target, want in checks:
    consulted, got = resolve(tool, target)
    if got != want:
        wrong.append(f'{tool}:{target}={got} via {consulted} (want {want})')
if wrong:
    raise SystemExit('effective permission differs from the ' + ('scoped' if root else 'read-only') + ' policy: ' + '; '.join(wrong[:8]))
PY
  rc=$?
  rm -f "$resolved"
  return "$rc"
}

# oc_attest <raw-events> <message-file> <path> <snapshot> [<path> <snapshot>...] -> prints the attested usage,
# or fails naming the gap. The export proves what the event stream cannot: the model that served every
# assistant turn, the agent that ran (a missing agent silently becomes `build`), that the stdin message
# arrived verbatim, and that each attached file reached the model whole. A file is matched by its path
# and compared with its snapshot taken before the run.
oc_attest() {
  local raw="$1" message="$2" sid exported rc
  shift 2
  sid="$(python3 - "$(sc_to_native "$raw")" <<'PY'
import json, sys
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if isinstance(event, dict) and event.get('sessionID'):
        print(event['sessionID'])
        break
PY
)"
  [ -n "$sid" ] || { echo 'CLI event log carries no sessionID to attest' >&2; return 1; }
  exported="$(mktemp)"
  if ! timeout 30 "$SC_BIN" export "$sid" --pure < /dev/null > "$exported" 2>/dev/null; then
    rm -f "$exported"; echo "CLI export failed for $sid" >&2; return 1
  fi
  local natives=() file
  for file in "$@"; do natives+=("$(oc_native "$file")"); done
  python3 - "$(sc_to_native "$exported")" "$SC_UPSTREAM" "$OC_AGENT" "$(sc_to_native "$message")" ${natives[@]+"${natives[@]}"} <<'PY'
import json, pathlib, re, sys
exported, upstream, agent, message, *pairs = sys.argv[1:]
text = pathlib.Path(exported).read_text(encoding='utf-8', errors='replace')
data = json.loads(text[text.index('{'):])
messages = data.get('messages') or []
served = [m.get('info') or {} for m in messages if (m.get('info') or {}).get('role') == 'assistant']
if not served:
    raise SystemExit('export has no assistant turn to attest')
for info in served:
    got = (info.get('providerID'), info.get('modelID'), info.get('agent') or info.get('mode'))
    if got[2] == 'compaction':
        got = got[:2] + (agent,)
    if got != ('opencode', upstream, agent):
        raise SystemExit(f'served identity {got!r} differs from requested (opencode, {upstream}, {agent})')
    if info.get('cost'):
        raise SystemExit(f'export reports cost {info.get("cost")!r} on a free run')
user = next((m for m in messages if (m.get('info') or {}).get('role') == 'user'), {})
def lf(value):
    return value.replace('\r\n', '\n')

sent = lf(open(message, encoding='utf-8', newline='').read())
if not any(p.get('type') == 'text' and lf(p.get('text') or '') == sent for p in user.get('parts') or []):
    raise SystemExit('the stdin message did not reach the model verbatim')
reads = {}
for part in user.get('parts') or []:
    body = part.get('text') or ''
    head = re.match(r'<path>(.*?)</path>', body)
    if part.get('type') == 'text' and head:
        reads[head.group(1).replace('\\', '/').lower()] = body
for path, snapshot in zip(pairs[::2], pairs[1::2]):
    body = reads.get(path.replace('\\', '/').lower())
    if body is None:
        raise SystemExit(f'attached file never reached the model: {path}')
    lines = pathlib.Path(snapshot).read_text(encoding='utf-8').splitlines()
    whole = ('<content>\n' + ''.join(f'{n}: {line}\n' for n, line in enumerate(lines, 1))
             + f'\n(End of file - total {len(lines)} lines)\n</content>')
    if whole not in lf(body):
        raise SystemExit(f'attached file reached the model incomplete: {path}')
tokens = [info.get('tokens') or {} for info in served]
print(json.dumps({'model': upstream,
                  'inputTokens': sum(t.get('input') or 0 for t in tokens),
                  'outputTokens': sum(t.get('output') or 0 for t in tokens),
                  'cacheReadInputTokens': sum((t.get('cache') or {}).get('read') or 0 for t in tokens)}))
PY
  rc=$?
  rm -f "$exported"
  return "$rc"
}

oc_parse() {
  OC_MODE="$1" OC_ROOT="$OC_ROOT" OC_EXTRAS="$OC_EXTRAS" OC_RAW="$(sc_to_native "$SC_RAW_TMP")" OC_SCHEMA="$(sc_to_native "$SC_SCHEMA_FILE")" \
  OC_PROMPT="$(sc_to_native "$SC_PROMPT_TMP")" OC_ATTESTED="$OC_ATTESTED" OC_ATTEST_ERROR="$OC_ATTEST_ERROR" \
  OC_RC="$rc" OC_RATE_LIMIT="$(sc_to_native "$SC_RAW_TMP.ratelimit")" OC_RESCUE_LOG="$(sc_to_native "$OC_RESCUE_LOG")" OC_RESCUE_MSG="$(sc_to_native "$OC_RESCUE_MSG")" python3 - <<'PY'
import json, os, pathlib, re, sys

mode = os.environ['OC_MODE']
raw = pathlib.Path(os.environ['OC_RAW']).read_text(encoding='utf-8', errors='replace')
events = []
for line in raw.splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if isinstance(event, dict):
        events.append(event)
parts = [e.get('part') for e in events if isinstance(e.get('part'), dict)]
texts = [p for p in parts if p.get('type') == 'text' and isinstance(p.get('text'), str)]
last = texts[-1].get('messageID') if texts else None
answer = '\n'.join(p['text'] for p in texts if p.get('messageID') == last).strip()
errors = [e for e in events if e.get('type') == 'error']
failed = int(os.environ['OC_RC']) != 0 or bool(errors) or not answer
structured = None
schema_error = None

def validate(value, rule, where='$'):
    if 'oneOf' in rule:
        matches = 0
        for branch in rule['oneOf']:
            try:
                validate(value, branch, where)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(f'{where}: oneOf requires exactly one matching schema')
    kinds = rule.get('type')
    if kinds:
        test = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list),
                'string': lambda v: isinstance(v, str), 'null': lambda v: v is None,
                'boolean': lambda v: isinstance(v, bool),
                'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
                'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}
        if not any(test[k](value) for k in ([kinds] if isinstance(kinds, str) else kinds)):
            raise ValueError(f'{where}: wrong type')
    if 'enum' in rule and value not in rule['enum']:
        raise ValueError(f'{where}: value outside enum')
    if isinstance(value, dict):
        for name in rule.get('required', []):
            if name not in value:
                raise ValueError(f'{where}: missing {name}')
        for name, item in value.items():
            if name in (rule.get('properties') or {}):
                validate(item, rule['properties'][name], f'{where}.{name}')
            elif rule.get('additionalProperties') is False:
                raise ValueError(f'{where}: unexpected key {name}')
    if isinstance(value, list):
        if len(value) < rule.get('minItems', 0):
            raise ValueError(f'{where}: fewer than {rule["minItems"]} items')
        for index, item in enumerate(value):
            validate(item, rule.get('items') or {}, f'{where}[{index}]')
    if isinstance(value, str) and 'pattern' in rule and not re.search(rule['pattern'], value):
        raise ValueError(f'{where}: pattern mismatch')

if os.environ['OC_SCHEMA'] and not failed:
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*)\n```', answer, re.S)
    try:
        structured = json.loads(fenced.group(1) if fenced else answer)
        answer = json.dumps(structured, ensure_ascii=False)
        validate(structured, json.loads(pathlib.Path(os.environ['OC_SCHEMA']).read_text(encoding='utf-8')))
    except (ValueError, OSError) as exc:
        failed, structured = True, None
        schema_error = str(exc)
        answer = f'Schema validation failed: {exc}\n{answer}'

if mode == 'judge':
    used = {line.split('\t', 1)[0] for line in pathlib.Path(os.environ['OC_RESCUE_LOG']).read_text(encoding='utf-8').splitlines()}
    steps = [p for p in parts if p.get('type') == 'step-finish']
    session = next((e['sessionID'] for e in events if e.get('sessionID')), '')
    kind = detail = message = ''
    if int(os.environ['OC_RC']) == 0 and session:
        if not answer and steps and (steps[-1].get('tokens') or {}).get('output') == 0 and 'empty-turn' not in used:
            kind = 'empty-turn'
            detail = f"last step_finish reason {steps[-1].get('reason')}, 0 output tokens, no text"
            message = 'Continue the task from where you stopped; finish it and give the final answer.\n'
        elif schema_error is not None and 'schema-repair' not in used:
            kind, detail = 'schema-repair', schema_error
            message = ('Your final answer failed the JSON schema check: ' + schema_error + '\n'
                       'Reply with only the corrected JSON object that matches the attached schema: '
                       'no markdown fence, no prose, no second object.\n')
    if kind:
        pathlib.Path(os.environ['OC_RESCUE_MSG']).write_text(message, encoding='utf-8', newline='\n')
    sys.stdout.buffer.write(('\x1f'.join((kind, session, detail.replace('\n', ' '))) + '\n').encode('utf-8'))
    raise SystemExit(0)

read_first = set()
collecting = False
for line in pathlib.Path(os.environ['OC_PROMPT']).read_text(encoding='utf-8').splitlines():
    if re.match(r'^\s*(?:#+\s*)?(?:files to )?read first\s*:', line, flags=re.I):
        collecting = True
    elif collecting and not re.match(r'^\s*(?:[-*]|\d+[.)])\s+', line):
        collecting = False
    if collecting:
        read_first.update(p.rstrip('.,;:') for p in re.findall(r'(?:[.\w-]+/)+[.\w-]+', line))
reads = set()
for part in parts:
    if part.get('type') == 'tool' and str(part.get('tool', '')).lower() == 'read':
        args = (part.get('state') or {}).get('input') or {}
        path = args.get('filePath') or args.get('path') or ''
        if path:
            reads.add(path.replace('\\', '/'))
proven = read_first and all(any(r == p or r.endswith('/' + p) for r in reads) for p in read_first)
attested = json.loads(os.environ['OC_ATTESTED']) if os.environ['OC_ATTESTED'] else None
usage = {}
if attested:
    usage = {attested['model']: {'canonicalModel': attested['model'], 'inputTokens': attested['inputTokens'],
                                 'outputTokens': attested['outputTokens'],
                                 'cacheReadInputTokens': attested['cacheReadInputTokens']}}
rescues = []
for line in pathlib.Path(os.environ['OC_RESCUE_LOG']).read_text(encoding='utf-8').splitlines():
    kind, _, detail = line.partition('\t')
    rescues.append({'kind': kind, 'detail': detail})
result = {'type': 'result', 'subtype': 'error' if failed else 'success', 'is_error': failed,
          'result': answer or raw[-1000:], 'structured_output': structured, 'modelUsage': usage,
          'engagement': 'proven' if proven else 'unproven', 'rescues': rescues}
# The CLI's last error event is why the run failed: it leads the result, and its HTTP status rides as
# `error_status`, the key the bench grid's rate-limit classifier reads (lib.sh bench_void_reason).
if errors:
    error = errors[-1].get('error') if isinstance(errors[-1].get('error'), dict) else {}
    data = error.get('data') if isinstance(error.get('data'), dict) else {}
    status = data.get('statusCode')
    head = ' '.join(str(x) for x in (error.get('name') or 'error', status) if x is not None)
    result['result'] = f"{head}: {data.get('message') or error.get('message') or ''}\n{result['result']}"
    if isinstance(status, int):
        result['error_status'] = status
# A rate-limit log line (oc_stream) is the same provider verdict as a 429 error event.
limit_file = pathlib.Path(os.environ['OC_RATE_LIMIT'])
limited = limit_file.read_text(encoding='utf-8', errors='replace').strip() if limit_file.is_file() else ''
if limited:
    said = (re.search(r'error\.error="([^"]*)"', limited) or [None, limited])[1]
    kind = 'RateLimitError' if 'Rate limit exceeded' in said else 'RetryError'
    result['result'] = f"{kind} 429: {said}\n{result['result']}"
    result['error_status'] = 429
if os.environ['OC_ROOT']:
    import posixpath
    def folded(value):
        value = value.replace('\\', '/')
        drive = re.match(r'^/([A-Za-z])/(.*)', value)
        return (f'{drive.group(1)}:/{drive.group(2)}' if drive else value).lower()
    root = folded(os.environ['OC_ROOT']).rstrip('/')
    staged = {folded(p) for p in os.environ['OC_EXTRAS'].split('|') if p}
    def escapes(token):
        absolute = re.match(r'^([A-Za-z]:)?[\\/]', token) is not None
        if not absolute and '/' not in token and '\\' not in token and '..' not in token:
            return False
        here = posixpath.normpath(folded(token) if absolute else posixpath.join(root, folded(token)))
        return not (here == root or here.startswith(root + '/') or here in staged or here == '/dev/null')
    attempts = []
    for part in parts:
        if part.get('type') != 'tool':
            continue
        state = part.get('state') or {}
        given = state.get('input') or {}
        tool = str(part.get('tool', ''))
        targets = [given[k] for k in ('filePath', 'file_path', 'path', 'workdir', 'cwd') if isinstance(given.get(k), str)]
        if tool.lower() == 'bash':
            targets += re.findall(r'[^\s"\'=;|&()<>]+', str(given.get('command', '')))
        attempts += [{'tool': tool, 'target': t, 'status': state.get('status')} for t in targets if escapes(t)]
    result['outsideRootAttempts'] = attempts
if os.environ['OC_ATTEST_ERROR']:
    result['result'] = os.environ['OC_ATTEST_ERROR'] + '\n' + result['result']
print(json.dumps(result, ensure_ascii=False))
PY
}

# oc_stream <raw-out> <stream> <pid-file> <hit-file>: the tail of oc_run's pipeline. It copies the CLI's
# output to <raw-out> and <stream>, except the CLI's own log lines (`--print-logs`), which only matter here.
# The free tier answers a rate-limited model with HTTP 429 and the CLI then retries 3 times and hangs with no
# stdout and no exit, so an ERROR log line carrying `Rate limit exceeded` or `AI_RetryError` is written to
# <hit-file> and the CLI process in <pid-file> is stopped at once.
oc_stream() {
  local raw="$1" stream="$2" pidfile="$3" hit="$4" line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      'timestamp='*' level=ERROR '*'Rate limit exceeded'*|'timestamp='*' level=ERROR '*AI_RetryError*)
        printf '%s\n' "$line" > "$hit"
        kill "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null
        break ;;
      'timestamp='*' level='*) ;;
      *) printf '%s\n' "$line" | tee -a "$raw" >> "$stream" ;;
    esac
  done
}

# oc_run <message-file> <raw-out> <cwd> <timeout> <progress-stream-or-empty> <session-or-empty> <attachment...>
# The message rides stdin: a model that treats attached files as untrusted data still takes the brief
# as the user's own words, and stdin has no command-line length limit. A session resumes the CLI session
# with that id; its events append to <raw-out>, and attachments ride only the first call. A rate-limited
# model ends the run (oc_stream), cools its free-pool member, and returns 1.
oc_run() {
  local message="$1" raw="$2" cwd="$3" limit="$4" stream="${5:-/dev/null}" session="${6:-}" file
  shift 6
  local attach=() resume=()
  for file in "$@"; do attach+=(--file "$(oc_native "$file")"); done
  [ -z "$session" ] || resume=(--session "$session")
  local variant=()
  [ -z "${SC_EFFORT:-}" ] || [ "$SC_EFFORT" = none ] || variant=(--variant "$SC_EFFORT")
  local pidfile="$raw.clipid" hit="$raw.ratelimit" status
  rm -f "$hit"
  # The run hosts no Claude hooks; these markers let the test entry points (WideRunGuard.ps1) tell a delegate's run.
  local brief_abs=""
  [ -z "${SC_PROMPT_FILE:-}" ] || brief_abs="$(sc_prompt_file_abs)"
  # opencode has no per-command shell prefix, so a bench arm runs its whole tree below normal: its API time
  # then carries machine load, and the record says so (workPriority: tree). Like lib/below_normal_shell.sh, the
  # tree moves to nice 10 (below normal), never a relative +10 that takes a below-normal caller to idle.
  local nice=() n
  [ -z "${BENCH_ARM:-}" ] || { n=$(nice); nice=(nice -n $(( n < 10 ? 10 - n : 0 ))); SC_WORK_PRIORITY=tree; }
  ( cd "$cwd" && printf '%s' "$BASHPID" > "$pidfile" && export CLAUDE_CODE_SIDECAR="$SC_TRANSPORT" \
      ${brief_abs:+CLAUDE_CODE_SIDECAR_PROMPT_FILE="$brief_abs"} && OPENCODE_CONFIG_CONTENT="$OC_CONFIG" exec ${limit:+timeout "$limit"} ${nice[@]+"${nice[@]}"} "$SC_BIN" run --pure \
      --print-logs --log-level DEBUG --agent "$OC_AGENT" --model "opencode/$SC_UPSTREAM" --format json \
      ${variant[@]+"${variant[@]}"} ${resume[@]+"${resume[@]}"} ${attach[@]+"${attach[@]}"} < "$message" 2>&1 ) | oc_stream "$raw" "$stream" "$pidfile" "$hit"
  status=$?
  if [ -s "$hit" ]; then
    python3 "$SC_ROOT/tools/free_pool.py" cool "$SC_UPSTREAM" > /dev/null 2>&1 || echo "[sidecar] could not cool the free-pool member $SC_UPSTREAM" >&2
    echo "[sidecar] $SC_UPSTREAM answered a rate limit; the run was ended and the member cooled" >&2
    return 1
  fi
  return "$status"
}

# oc_watch_stall <progress-file>: the shared watchdog's signal (lib/sidecar_common.sh §stall watchdog) for a
# CLI whose events all land in the progress file: silence past SC_STALL_SEC appends one `stalled` journal row,
# the next event appends `resumed`, and the run itself is never ended. Runs until killed.
oc_watch_stall() {
  local progress="$1" last idle stalled=0
  while sleep "$SC_WATCH_POLL_SEC"; do
    last="$(stat -c %Y "$progress" 2>/dev/null)" || continue
    idle=$(( $(date +%s) - last ))
    if [ "$idle" -ge "$SC_STALL_SEC" ]; then
      if [ "$stalled" = 0 ]; then sc_journal stalled "idle=$idle"; stalled=1; fi
    elif [ "$stalled" = 1 ]; then
      sc_journal resumed "idle=$idle"
      stalled=0
    fi
  done
}

if [ "${1:-}" = '--check' ]; then
  oc_validate_flags '-m' '-A -U -W' "${@:2}"
  sc_check_model_override "$@"
  sc_resolve_model
  sc_gate_availability
  SC_BIN="$(oc_bin)" || { echo 'UNAVAILABLE (opencode CLI missing)'; exit 4; }
  SC_UPSTREAM="$(oc_upstream)" || exit 2
  OC_CONFIG="$(oc_permission_config "$OC_AGENT" "$OC_ALLOWED" "" "")"
  oc_catalog_gate || { echo "UNAVAILABLE (catalog gate refused $SC_UPSTREAM)"; exit 3; }
  oc_permission_gate || { echo 'UNAVAILABLE (permission probe failed)'; exit 3; }
  sc_gate_band
  sc_gate_capacity
  SC_CHECK_MODE=1 sc_gate_price_window
  # Admission is request-free; the actual launch attests the served session after its call.
  printf 'OK (model=opencode/%s agent=%s cli=opencode run; no model request)\n' "$SC_UPSTREAM" "$OC_AGENT"
  exit 0
fi

oc_validate_flags '-m -e -t -l -d -f -R -P -G -S -D -C -x -p -T -Z -B' '-X -A' "$@"
sc_parse_flags "$@"
sc_on_exit 'oc_ensure_output "$rc"'
shift "$SC_SHIFT"
[ "${1:-}" != '--' ] || shift
sc_read_prompt "$@"
sc_validate_enums
case "${SC_STALL_SEC:-}" in ''|*[!0-9]*) sc_die 2 "-Z needs a whole number of seconds" ;; esac

case "${SC_SHAPE:-}" in scout|lens|reviewer|author|judge|gate) ;; arm) ;; *) sc_die 2 "-G ${SC_SHAPE:-(missing)} refused: the free CLI seat serves scout, lens, reviewer, author, judge, gate and arm only" ;; esac
[ -z "$SC_BASE_BUILTINS" ] || [ "$SC_SHAPE" = arm ] || sc_die 2 "-B refused: it is the arm kind's task base list, not a flag of -G $SC_SHAPE"
[ "$SC_SHAPE" != reviewer ] || [ -n "$SC_SCHEMA_FILE" ] || sc_die 2 '-G reviewer requires -S <schema>'
[ -d "$SC_WORKDIR" ] || sc_die 2 "-d directory missing: $SC_WORKDIR"
[ -z "$SC_SCHEMA_FILE" ] || [ -f "$SC_SCHEMA_FILE" ] || sc_die 2 "-S schema missing: $SC_SCHEMA_FILE"
for file in ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
  [ -f "$file" ] || sc_die 2 "-C file missing: $file"
done
sc_resolve_tools
OC_ALLOWED="$(oc_allowed_tools 2>&1)" || sc_die 2 "$OC_ALLOWED"
oc_record_seat_drops
# A job whose resolved set carries edit or bash is confined by permission rules rooted at `-d`.
OC_SCOPED=0
case "$SC_SHAPE" in arm|author) OC_SCOPED=1 ;; esac
case ",$OC_ALLOWED," in *,edit,*|*,bash,*) OC_SCOPED=1 ;; esac
if [ "$OC_SCOPED" = 1 ]; then
  OC_AGENT=sidecar-scoped
  OC_ROOT="$(oc_native "$SC_WORKDIR")"
  oc_refuse_own_checkout
  OC_EXTRAS=""
  for file in "$SC_PROMPT_FILE" "$SC_SCHEMA_FILE" ${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"}; do
    [ -z "$file" ] || OC_EXTRAS="${OC_EXTRAS:+$OC_EXTRAS|}$(oc_native "$file")"
  done
  IFS='|' read -ra staged <<< "${BENCH_ARM_INPUT_FILES:-}"
  for file in ${staged[@]+"${staged[@]}"}; do
    [ -z "$file" ] || OC_EXTRAS="${OC_EXTRAS:+$OC_EXTRAS|}$(oc_native "$file")"
  done
fi
if [ -n "$SC_SCHEMA_FILE" ]; then
  python3 - "$(sc_to_native "$SC_SCHEMA_FILE")" <<'PY' || exit 2
import json, sys
supported = {'type', 'description', 'additionalProperties', 'required', 'properties', 'items', 'enum', 'pattern', 'oneOf', 'minItems'}
def visit(rule):
    if not isinstance(rule, dict):
        raise ValueError('schema node must be an object')
    extra = set(rule) - supported
    if extra:
        raise ValueError('unsupported schema keyword: ' + ', '.join(sorted(extra)))
    if 'minItems' in rule and (not isinstance(rule['minItems'], int) or isinstance(rule['minItems'], bool) or rule['minItems'] < 0):
        raise ValueError('minItems must be a non-negative integer')
    if not isinstance(rule.get('additionalProperties', False), bool):
        raise ValueError('additionalProperties must be a boolean')
    for child in (rule.get('properties') or {}).values():
        visit(child)
    if 'items' in rule:
        visit(rule['items'])
    if 'oneOf' in rule:
        branches = rule['oneOf']
        if not isinstance(branches, list) or not branches:
            raise ValueError('oneOf must be a non-empty schema array')
        for child in branches:
            visit(child)
try:
    visit(json.load(open(sys.argv[1], encoding='utf-8')))
except (ValueError, OSError) as exc:
    print(f'-S schema refused: {exc}', file=sys.stderr)
    sys.exit(2)
PY
fi
sc_resolve_model
sc_gate_availability
SC_BIN="$(oc_bin)" || sc_die 4 'opencode CLI missing'
sc_gate_band
sc_gate_capacity
sc_gate_price_window
sc_admit_launch
sc_validate_effort
SC_UPSTREAM="$(oc_upstream)" || exit 2
OC_CONFIG="$(oc_permission_config "$OC_AGENT" "$OC_ALLOWED" "$OC_ROOT" "$OC_EXTRAS")"
oc_catalog_gate || exit 2
oc_permission_gate || exit 3

SC_PROMPT_TMP="$(mktemp)"
SC_RAW_TMP="$(mktemp)"
sc_on_exit 'rm -f "$SC_PROMPT_TMP" "$SC_RAW_TMP" "$SC_RAW_TMP.clipid" "$SC_RAW_TMP.ratelimit" 2>/dev/null || :'
{
  printf 'Repository root: %s\n\n' "$(oc_native "$SC_WORKDIR")"
  if [ "$OC_SCOPED" = 1 ]; then cat "$SC_ROOT/reference/opencode_arm_preamble.md"
  else cat "$SC_ROOT/reference/opencode_run_preamble.md"; fi
  printf '\n'
  if [ -n "$SC_SCHEMA_FILE" ]; then
    printf 'Return ONLY one JSON object that matches the attached schema: no markdown fence, no prose.\n\n'
  fi
  printf '%s\n' "$SC_PROMPT"
} > "$SC_PROMPT_TMP"

OC_FILES=()
[ -z "$SC_SCHEMA_FILE" ] || OC_FILES+=("$SC_SCHEMA_FILE")
# `full` attaches what Claude Code loads: CLAUDE.md, then each file its `@` lines import, in order.
if [ "$SC_DISCLOSURE" = full ]; then
  OC_FILES+=("$SC_ROOT/CLAUDE.md")
  while IFS= read -r oc_import; do
    oc_import="${oc_import%$'\r'}"
    [ -f "$SC_ROOT/${oc_import#@}" ] && OC_FILES+=("$SC_ROOT/${oc_import#@}")
  done < <(grep '^@' "$SC_ROOT/CLAUDE.md")
fi
OC_FILES+=(${SC_CONTEXT_FILES[@]+"${SC_CONTEXT_FILES[@]}"})

# Attestation checks the bytes each attachment held at launch, so a file a peer rewrites mid-run
# (the live CLAUDE.md) cannot fail it. Each pair is the path the CLI was given, then its snapshot.
OC_SNAP_DIR="$(mktemp -d)"
sc_on_exit 'rm -rf "$OC_SNAP_DIR" 2>/dev/null || :'
OC_PAIRS=()
for file in ${OC_FILES[@]+"${OC_FILES[@]}"}; do
  snap="$OC_SNAP_DIR/${#OC_PAIRS[@]}"
  cp "$file" "$snap" || sc_die 2 "attached file unreadable: $file"
  # The CLI's read tool cuts a line past 2000 chars, a file past 2000 lines or 51200 bytes (its binary's
  # read caps), so such a file can never attest: refuse it before any model call.
  over="$(python3 - "$(sc_to_native "$snap")" <<'PY'
import sys
data = open(sys.argv[1], 'rb').read()
lines = data.decode('utf-8', 'replace').splitlines()
long = next((n for n, line in enumerate(lines, 1) if len(line) > 2000), None)
if long:
    print(f'line {long} is {len(lines[long - 1])} chars (cap 2000)')
elif len(lines) > 2000:
    print(f'{len(lines)} lines (cap 2000)')
elif len(data) > 51200:
    print(f'{len(data)} bytes (cap 51200)')
PY
)"
  [ -z "$over" ] || sc_die 2 "attached file exceeds the opencode read caps, so it would reach the model cut: $file: $over"
  OC_PAIRS+=("$file" "$snap")
done

[ -z "$SC_PROGRESS" ] || : > "$SC_PROGRESS"
SC_RUN_STARTED=1
if [ -n "$SC_RECORD" ] && [ -n "$SC_PROGRESS" ] && [ "${SC_STALL_SEC:-0}" -gt 0 ]; then
  oc_watch_stall "$SC_PROGRESS" &
  OC_STALL_PID=$!
  sc_on_exit 'kill "$OC_STALL_PID" 2>/dev/null'
fi
oc_run "$SC_PROMPT_TMP" "$SC_RAW_TMP" "$SC_WORKDIR" "$SC_TIMEOUT" "$SC_PROGRESS" "" ${OC_FILES[@]+"${OC_FILES[@]}"}
rc=$?
OC_ATTESTED=""
OC_ATTEST_ERROR=""
# A schema failure or an empty final turn resumes the same CLI session, once per kind. `oc_parse judge`
# names the rescue, or prints an empty kind when none applies.
OC_RESCUE_LOG="$(mktemp)"
OC_RESCUE_MSG="$(mktemp)"
sc_on_exit 'rm -f "$OC_RESCUE_LOG" "$OC_RESCUE_MSG" 2>/dev/null || :'
while [ "$rc" -eq 0 ]; do
  IFS=$'\x1f' read -r kind session detail <<< "$(oc_parse judge)"
  [ -n "$kind" ] || break
  printf '%s\t%s\n' "$kind" "$detail" >> "$OC_RESCUE_LOG"
  oc_run "$OC_RESCUE_MSG" "$SC_RAW_TMP" "$SC_WORKDIR" "$SC_TIMEOUT" "$SC_PROGRESS" "$session"
  rc=$?
done
[ "$rc" -eq 124 ] && echo "opencode CLI timed out after ${SC_TIMEOUT}s" >&2
if [ "$rc" -eq 0 ]; then
  OC_ATTEST_ERR_TMP="$(mktemp)"
  OC_ATTESTED="$(oc_attest "$SC_RAW_TMP" "$SC_PROMPT_TMP" ${OC_PAIRS[@]+"${OC_PAIRS[@]}"} 2>"$OC_ATTEST_ERR_TMP")" \
    || { OC_ATTEST_ERROR="attestation failed: $(tail -c 400 "$OC_ATTEST_ERR_TMP")"; rc=1; }
  rm -f "$OC_ATTEST_ERR_TMP"
fi
OUTPUT="$(oc_parse result)"
[ -n "$OUTPUT" ] || OUTPUT='{"type":"result","result":"CLI produced no parseable result","structured_output":null,"is_error":true}'
SC_RUN_FINISHED=1
# One Python reads the three fields the tail needs (the attested model, the envelope's is_error and its
# engagement), each empty when its own JSON does not parse: a Python start costs 0.1 s, and three ran here.
# The envelope goes in on stdin, which an environment variable cannot carry past the Windows size limit.
OC_TAIL="$(printf '%s' "$OUTPUT" | OC_ATT="$OC_ATTESTED" python3 -c 'import json, os, sys
def load(text):
    try:
        return json.loads(text)
    except ValueError:
        return None
def field(doc, key, default, kind=str):
    try:
        return kind(doc.get(key, default))
    except (AttributeError, TypeError, ValueError):
        return ""
att = os.environ.get("OC_ATT", "")
model = field(load(att), "model", "") if att.strip() else ""
envelope = load(sys.stdin.read())
print("%s|%s|%s" % (model, field(envelope, "is_error", True, int), field(envelope, "engagement", "unproven")))')"
IFS='|' read -r SC_ATTESTED_MODEL OC_IS_ERROR OC_ENGAGEMENT <<< "$OC_TAIL"
if [ "$rc" -eq 0 ] && [ "$OC_IS_ERROR" = 1 ]; then rc=1; fi
printf '%s\n' "$OUTPUT"
[ -z "$SC_PROGRESS" ] || printf '%s\n' "$OUTPUT" >> "$SC_PROGRESS"
sc_write_record "$OUTPUT" "$rc"
if [ -n "$SC_RECORD" ] && [ -f "$SC_RECORD" ]; then
  OC_ENGAGEMENT="$OC_ENGAGEMENT" \
    OC_RECORD="$(sc_to_native "$SC_RECORD")" python3 - <<'PY'
import json, os, pathlib
path = pathlib.Path(os.environ['OC_RECORD'])
data = json.loads(path.read_text(encoding='utf-8'))
data['engagement'] = os.environ['OC_ENGAGEMENT']
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
PY
fi
exit "$rc"
