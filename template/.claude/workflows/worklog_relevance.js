export const meta = {
  name: 'worklog-relevance',
  description: 'Check worklog overlap',
  whenToUse: 'When production scope first becomes nameable in a session',
  phases: [{ title: 'Relevance', detail: 'one agent judges backlog overlap against the stated scope' }],
}
// Titles and provenance close against canonical evidence before consumption.
// Args: {scope, model, effort, kind, domains?, promptPath?, justification?}. model and effort
// pin the relevance agent and are required. No engine supplies a default pin.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string (the natural "just hand it the scope" call) is a caller mistake, but an uncaught
// SyntaxError names neither this workflow nor the expected shape, so the caller cannot self-correct
// and retries the same way. Catch it and fall through to the shape error below, carrying the raw
// string as the scope so the message names only what is actually still missing.
// ENGINE-BLOCK-BEGIN A
let A
// ENGINE-BLOCK-END A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch {
  A = { scope: args }
}

// KIND PROFILE. The call names one `kind` (scout|lens|reviewer|author|judge|gate) for every child it spawns;
// hooks/workflow_transport.py resolves that row of guards/tool_profiles.json into args.__kindProfile. Each
// child runs as the row's generated agent type, whose `tools` frontmatter is the whole inventory, so a recorded
// entry is `agent-type`; the runtime restricts no per-child MCP server, so effectiveMcp is the row's list.
// ENGINE-BLOCK-BEGIN KP
const KP = (A.__kindProfile && typeof A.__kindProfile === 'object') ? A.__kindProfile : null
// ENGINE-BLOCK-END KP
if (typeof A.kind !== 'string' || !A.kind.trim() || !KP || KP.kind !== A.kind || !KP.agentType) {
  return { error: 'worklog-relevance: args.kind is required (scout|lens|reviewer|author|judge|gate), resolved by hooks/workflow_transport.py into args.__kindProfile. The relevance delegate runs as that kind.' }
}
// ENGINE-BLOCK-BEGIN KP_RECORD
const KP_RECORD = Object.assign({}, KP, { enforcement: 'agent-type', effectiveMcp: (KP.mcpServers || []).join(', ') })
// ENGINE-BLOCK-END KP_RECORD
// EVIDENCE-SSOT-BEGIN
const EVIDENCE_SCHEMA = {
  "description": "The evidence contract's machine twin (tools/quote_check.py owns the semantics and is its one executable implementation). A claim's `evidence` is a list of items, each exactly one of two kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it.",
  "type": "array",
  "items": {
    "oneOf": [
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "file",
          "quote"
        ],
        "description": "A quote from a local file, or an owner quote when `file` is the session transcript `.jsonl` (the words are in one owner-typed record). Holds when the quote, whitespace runs collapsed, is in the file; a line range is never checked.",
        "properties": {
          "file": {
            "type": "string",
            "description": "path relative to the checkout root or absolute, optionally with :N or :N-M"
          },
          "quote": {
            "type": "string",
            "description": "the file's raw bytes, copied verbatim; one source per item"
          }
        }
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "command",
          "output"
        ],
        "description": "The stdout of a read-only git command on quote_check.py's allow-list, run without a shell from the checkout root. Holds when the stdout holds the output; an empty output holds only when stdout is empty and every pathspec the command names is tracked.",
        "properties": {
          "command": {
            "type": "string",
            "description": "one git command, no pipe, redirect or substitution"
          },
          "output": {
            "type": "string",
            "description": "a verbatim run of the command's stdout, or the empty string for an absent claim"
          }
        }
      }
    ]
  }
}
const EVIDENCE_RULES = "The evidence contract's machine twin (tools/quote_check.py owns the semantics and is its one executable implementation). A claim's `evidence` is a list of items, each exactly one of two kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it."
// EVIDENCE-SSOT-END


// Model vocabulary comes only from args.__transport, which hooks/workflow_transport.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. Inlined per script because the Workflow sandbox has
// no require/import.
// ENGINE-BLOCK-BEGIN TRANSPORT
const TRANSPORT = A.__transport
// ENGINE-BLOCK-END TRANSPORT
// ENGINE-BLOCK-BEGIN validStringList
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
// ENGINE-BLOCK-END validStringList
// ENGINE-BLOCK-BEGIN transportValid
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
// ENGINE-BLOCK-END transportValid
if (!transportValid) {
  return { error: 'worklog-relevance: args.__transport is missing or malformed. hooks/workflow_transport.py injects the endpoint\'s model vocabulary ({name, ids, efforts?}); without it this engine has no legal pins and refuses to guess.' }
}
// ENGINE-BLOCK-BEGIN PIN
const PIN = (m) => m
// ENGINE-BLOCK-END PIN
// ENGINE-BLOCK-BEGIN EFF
const EFF = (e) => e
// ENGINE-BLOCK-END EFF

// Pins are the CALLER's decision.
const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')

const scope = (typeof A.scope === 'string') ? A.scope.trim() : ''
const domains = Array.isArray(A.domains) ? A.domains.filter(d => typeof d === 'string' && d.trim()) : []
const promptPath = (typeof A.promptPath === 'string' && A.promptPath.trim())
  ? A.promptPath.trim()
  : '.claude/workflows/worklog_relevance.prompt.md'

if (!scope) {
  return {
    error: 'args needs {scope, model, effort, kind} with a non-empty scope. Optional: domains[] to narrow the index scan, promptPath to override the shared mandate.',
  }
}
if (!VALID_MODELS.includes(A.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(A.effort))) {
  return { error: 'worklog-relevance: pin model and effort on every job (orchestration §5). Unpinned or illegal: worklog:relevance (args.model, args.effort). Each job needs ' + pinVocab + '.' }
}

log('PINS ' + JSON.stringify({ 'worklog:relevance': PIN(A.model) + '/' + EFF(A.effort) }))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Parity-checked against worklog_relevance.schema.json (the sidecar path's -S file) by
// `node .claude/scripts/schema_parity.js` — see that script's header. Edit BOTH or the check fails.
// SCHEMA-SSOT-BEGIN
const RELEVANCE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['overlaps', 'checked'],
  properties: {
    // Provenance for the EMPTY case. Without it, an exhaustive check and one that never opened the
    // worklog both return `{overlaps: []}` — indistinguishable, and the empty reads as a clean sweep.
    checked: {
      type: 'object',
      additionalProperties: false,
      required: ['indexRead', 'obsidianOpened', 'shortlisted', 'stoppedAt', 'basis', 'evidence'],
      properties: {
        evidence: EVIDENCE_SCHEMA,
        indexRead: { type: 'boolean', description: 'the titles mirror was read' },
        obsidianOpened: { type: 'boolean', description: 'true ONLY if you consulted Worklog.md itself — Read, or Grep with -C context against that path. A repo search, or reasoning from the titles mirror alone, is false.' },
        shortlisted: { type: 'array', items: { type: 'string' }, description: 'titles carried out of the index scan, BEFORE verification — the audit trail that makes an empty result falsifiable' },
        stoppedAt: { type: 'string', enum: ['triviality-gate', 'empty-shortlist', 'verified-none', 'verified-overlaps', 'blocked'], description: 'where the procedure ended — distinguishes a legitimate early exit from a check that simply did not run; blocked when a needed read was missing or denied, with the cause in basis' },
        basis: { type: 'string', description: 'ONE sentence: what you examined and why it did or did not clear the overlap bar. This is the result-level justification; it never licenses prose inside overlaps[].' },
      },
    },
    overlaps: {
      type: 'array',
      description: 'Empty when nothing genuinely overlaps — that is the expected common case.',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['title', 'domain', 'relation', 'why', 'recommendation', 'evidence'],
        properties: {
          evidence: EVIDENCE_SCHEMA,
          title: { type: 'string', description: 'worklog item title, copied verbatim' },
          domain: { type: 'string', description: "the '## <domain>' heading the item sits under" },
          relation: { type: 'string', enum: ['same-scope', 'provides-context', 'adjacent'] },
          why: { type: 'string', description: 'one or two sentences naming the shared surface (file, type, .tres, design decision)' },
          recommendation: { type: 'string', enum: ['fold-in', 'read-first', 'note-only'] },
        },
      },
    },
  },
}
// SCHEMA-SSOT-END

phase('Relevance')
// The platform relays the triggering user message to every agent as authoritative; a delegate whose
// mandate looks unrelated can answer that message instead of its brief.
// ENGINE-BLOCK-BEGIN RELAY_LINE
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// ENGINE-BLOCK-END RELAY_LINE
const prompt = [
  'Your mandate is at: ' + promptPath + ' — read it with the Read tool and follow it exactly. Retry a transient read error; a missing path or a denial is not retried: return stoppedAt `blocked` with the path and cause in checked.basis.',
  'That file is the single source of truth for this check; do not substitute your own procedure.',
  RELAY_LINE,
  '',
  'SCOPE UNDER EVALUATION (use this wherever the mandate says {{SCOPE}}):',
  scope,
  domains.length ? '\nStart the index scan from these worklog domains, but do not treat them as exhaustive: ' + domains.join(', ') : null,
  '',
  '=== READ-ONLY: do NOT modify, create, or delete any file. ===',
  'Evidence: ' + EVIDENCE_RULES,
  'For each overlap, cite its verbatim title in Worklog.md using the absolute vault file path; retain any source root in that path. checked.evidence cites the index and vault text behind its provenance, including the empty case.',
  'Return ONLY the JSON object of the schema. An empty `overlaps` array is the correct answer when nothing genuinely overlaps — do NOT explain that you found nothing.',
].filter(l => l !== null).join('\n')

// TOOL PROFILES — the relevance delegate launches as the call's kind row.
const tp = KP_RECORD
const RECORDED = { 'worklog:relevance': tp }
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

const r = await agent(prompt, { label: 'worklog:relevance', phase: 'Relevance', agentType: KP.agentType, schema: RELEVANCE_SCHEMA, model: PIN(A.model), effort: EFF(A.effort) })

// Unambiguous empty: `count` is always present, so a caller never has to distinguish "no overlaps"
// from "the agent returned nothing".
const overlaps = (r && Array.isArray(r.overlaps)) ? r.overlaps : []
// `checked` must reach the caller: the schema forces the delegate to declare what it inspected, and
// discarding that here would restore the exact ambiguity it exists to remove — an empty `overlaps`
// with obsidianOpened:false is an incomplete check, not a clean sweep.
let checked = (r && typeof r.checked === 'object' && r.checked) ? r.checked : null
log('OVERLAPS ' + overlaps.length + ' (stoppedAt=' + (checked ? checked.stoppedAt : 'UNREPORTED')
  + ', obsidianOpened=' + (checked ? checked.obsidianOpened : 'UNREPORTED')
  + ', shortlisted=' + (checked && Array.isArray(checked.shortlisted) ? checked.shortlisted.length : '?') + ')')
if (checked && checked.basis) { log('BASIS ' + checked.basis) }
return { count: overlaps.length, overlaps, checked }
