export const meta = {
  name: 'worklog-relevance',
  description: 'Check worklog overlap',
  whenToUse: 'When production scope first becomes nameable in a session',
  phases: [{ title: 'Relevance', detail: 'one agent judges backlog overlap against the stated scope' }],
}
// Titles and provenance close against canonical evidence before consumption.
// Args: {scope, model, effort, tier, checker: {model, effort}, domains?, promptPath?, justification?}. model and effort
// pin the relevance agent and are required; checker pins the closeCheck verifier this engine always starts. Both are
// validated before any agent starts. No engine supplies a default pin.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string (the natural "just hand it the scope" call) is a caller mistake, but an uncaught
// SyntaxError names neither this workflow nor the expected shape, so the caller cannot self-correct
// and retries the same way. Catch it and fall through to the shape error below, carrying the raw
// string as the scope so the message names only what is actually still missing.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch {
  A = { scope: args }
}

// EVIDENCE-SSOT-BEGIN
const EVIDENCE_SCHEMA = {
  "description": "The evidence contract's machine twin (reference/evidence_contract.md owns the semantics; tools/verify_claims.py is the one executable implementation). A claim's `evidence` is a list of items, each exactly one of four kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it.",
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
        "description": "A quote from a local file. VERIFIED when the quote (case-folded, in-order `...` gaps allowed, a leading `path:N:` or `N:` grep prefix stripped) occurs in the file; UNVERIFIED when the file is readable and lacks it or does not exist; UNCHECKED when the file cannot be read.",
        "properties": {
          "file": {
            "type": "string",
            "description": "repository-relative or absolute path, optionally with :N or :N-M; the line range is reported, never decisive"
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
        "description": "The stdout of a read-only git command from the allow-list (git grep, ls-files, ls-tree, log, show, branch, check-ignore, cat-file, rev-parse, diff --stat or --name-only, status --short), run without a shell from the repository root. VERIFIED when stdout holds the output, or when output is empty, stdout is empty and every pathspec the command names matches a tracked path. Any other command, a shell metacharacter, an unlisted flag or a timeout is UNCHECKED.",
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
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "owner"
        ],
        "description": "The owner's verbatim words. VERIFIED when an owner turn of the session transcript holds them (an AskUserQuestion answer is an owner turn when the item also carries its `question`; a runtime envelope is not), or when a row of the named task record holds them and still resolves. A delegate-recorded, missing or unresolved row, or an unreadable transcript, is UNCHECKED.",
        "properties": {
          "owner": {
            "type": "string",
            "description": "the owner's words, verbatim"
          },
          "question": {
            "type": "string",
            "description": "the AskUserQuestion question the owner answered; required when the quote is a picked option label"
          },
          "task": {
            "type": "string",
            "description": "a task record id whose condition rows may hold the words when this session's transcript does not"
          }
        }
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "url",
          "quote"
        ],
        "description": "A quote from an external page. A fetched artifact (fetch_source.sh manifest) is checked like a file; without one the item is UNCHECKED.",
        "properties": {
          "url": {
            "type": "string",
            "description": "the page the quote was read from"
          },
          "quote": {
            "type": "string",
            "description": "verbatim from the fetched bytes"
          },
          "artifact": {
            "type": "string",
            "description": "local path of the fetched bytes"
          }
        }
      }
    ]
  }
}
const EVIDENCE_RULES = "The evidence contract's machine twin (reference/evidence_contract.md owns the semantics; tools/verify_claims.py is the one executable implementation). A claim's `evidence` is a list of items, each exactly one of four kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it."
// EVIDENCE-SSOT-END

// CLOSE-CHECK-BEGIN
function repoRootOf(a) {
  const root = a && typeof a.__repoRoot === 'string' ? a.__repoRoot.trim().replace(/\\/g, '/').replace(/\/+$/, '') : ''
  return /^([A-Za-z]:)?\//.test(root) ? root : ''
}
function rootedPath(a, p) {
  const root = repoRootOf(a)
  return root && typeof p === 'string' && !/^([A-Za-z]:)?[\\/]/.test(p) ? root + '/' + p.replace(/^\.\//, '') : p
}
function cdRootPrefix(a) {
  const root = repoRootOf(a)
  return root ? "cd '" + root.replace(/'/g, "'\"'\"'") + "' && " : ''
}
const VERDICTS = ['VERIFIED', 'UNVERIFIED', 'UNCHECKED']
const applyVerdicts = (items, verdicts, keyOf) => items.map(item => {
  const supplied = verdicts[keyOf(item)]
  const verdict = VERDICTS.includes(supplied) ? supplied : 'UNCHECKED'
  return Object.assign({}, item, { verdict, confidence: verdict === 'VERIFIED' ? 'verified' : 'unverified' })
})
const boundedVerification = verification => ({
  status: verification.status, counts: verification.counts, report: verification.report,
  ...(verification.note ? { note: verification.note } : {}),
  nonVerified: Object.keys(verification.verdicts).filter(key => verification.verdicts[key] !== 'VERIFIED'),
})
// A session that predates the generated verifier type rejects it; the verifier retries once as general-purpose.
const verifierAgent = (prompt, opts) => agent(prompt, opts).catch(e => {
  const msg = String((e && e.message) || e)
  if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
  log('FALLBACK ' + opts.label + ' ' + opts.agentType + '→general-purpose')
  return agent(prompt, Object.assign({}, opts, { agentType: 'general-purpose' }))
})
const closeCheck = async (mode, { items, keyOf, out, root = A.root, spillDir, expectTotal, expectSources, ledger, fold, seats = [] }) => {
  const keys = items.map(keyOf)
  if (keys.some(key => typeof key !== 'string' || !key.trim() || /(?:undefined|null)#|#(?:undefined|null)$/.test(key))
    || new Set(keys).size !== keys.length) throw new Error('closeCheck: duplicate or missing source key')
  const quoteArg = value => "'" + String(value).replace(/'/g, "'\"'\"'") + "'"
  const identity = JSON.stringify([mode, items.map(keyOf)])
  let hash = 2166136261
  for (let i = 0; i < identity.length; i++) hash = Math.imul(hash ^ identity.charCodeAt(i), 16777619)
  const label = 'verify-' + (hash >>> 0).toString(36)
  out = rootedPath(A, out.replace(/\.json$/, '-' + label + '.json'))
  // The verifier may report --out resolved (absolute, either separator) and may trail prose; the report must LEAD with the file.
  const reportsOut = report => {
    const lead = String(report).replace(/\\/g, '/').trim().match(/^(.*?\.json)(?![\w-])/), o = out.replace(/\\/g, '/').replace(/^\.\//, '')
    return !!lead && (lead[1] === o || lead[1].endsWith('/' + o))
  }
  const profile = A.__toolProfiles && A.__toolProfiles.verify
  const transport = A.__transport
  const checker = A.checker || {}
  const model = checker.model
  const effort = checker.effort
  if (typeof model !== 'string' || !Array.isArray(transport.ids) || !transport.ids.includes(model)) {
    throw new Error('closeCheck: args.checker.model is missing or outside args.__transport.ids; refusing the checked route')
  }
  if (Array.isArray(transport.efforts) && !transport.efforts.includes(effort)) {
    throw new Error('closeCheck: args.checker.effort is missing or outside args.__transport.efforts; refusing the checked route')
  }
  const schema = {
    type: 'object', required: ['status', 'counts', 'verdicts', 'report'], additionalProperties: false,
    properties: {
      status: { type: 'string', enum: ['pass', 'fail', 'unavailable'] },
      counts: { type: 'object', required: VERDICTS, additionalProperties: false,
        properties: Object.fromEntries(VERDICTS.map(v => [v, { type: 'integer', minimum: 0 }])) },
      verdicts: { type: 'object', additionalProperties: { type: 'string', enum: VERDICTS } },
      report: { type: 'string' },
      note: { type: 'string' },
    },
  }
  let verification
  try {
    if (!profile || !profile.agentType || profile.tier !== 'fanout') throw new Error('missing verifier fanout profile')
    let command = 'python3 .claude/tools/post_check.py ' + mode
      + (fold && fold.findings ? ' --findings ' + quoteArg(fold.findings) : ' --run-label ' + label)
      + ' --out ' + (/^[\w./:-]+$/.test(out) ? out : quoteArg(out))
    if (fold && fold.findings) {
      if (fold.digest) command += ' --digest ' + quoteArg(fold.digest)
      if (fold.snapshot) command += ' --snapshot ' + quoteArg(fold.snapshot)
    }
    if (root) command += ' --root ' + quoteArg(root)
    if (spillDir) command += ' --spill-dir ' + quoteArg(rootedPath(A, spillDir))
    if (expectTotal !== undefined) command += ' --expect-total ' + expectTotal
    if (expectSources !== undefined) command += ' --expect-sources ' + expectSources
    if (ledger) {
      for (const [field, flag] of Object.entries({stage: 'stage', subject: 'subject', roster: 'roster', scopeJson: 'scope-json', task: 'task', premiseCommits: 'premise-commits', head: 'head', blobs: 'blobs'})) {
        if (ledger[field]) command += ' --' + flag + ' ' + quoteArg(ledger[field])
      }
      if (ledger.noNewDecision) command += ' --no-new-decision'
    }
    const seatsFile = out.replace(/\.json$/, '.seats.json')
    const launched = Object.fromEntries(seats.map(seat => [seat.label, {
      model: seat.model, effort: seat.effort, tier: seat.tier || null,
      mandates: seat.mandates || [], mustRead: seat.mustRead || [],
    }]))
    if (new Set(seats.map(seat => seat.label)).size !== seats.length) throw new Error('duplicate launched seat label')
    const writer = 'import sys; from pathlib import Path; p=Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[2] + "\\n", encoding="utf-8", newline="\\n")'
    if (mode === 'review') command += ' --seats-file ' + quoteArg(seatsFile)
    command = cdRootPrefix(A) + 'python3 -c ' + quoteArg(writer) + ' ' + quoteArg(seatsFile) + ' ' + quoteArg(JSON.stringify(launched)) + ' && ' + command
    log('PINS ' + JSON.stringify({[label]: model + '/' + effort + '/' + profile.agentType + ' tier:fanout'}))
    verification = await verifierAgent('Run exactly this command with Bash: ' + command
      + '\nThen Read its --out file, even if the command exits 1. Return ONLY {status, counts, verdicts, report, note}.'
      + ' Copy status and counts. Map each verdict key to its verdict string, without changing values. Set report to the --out path and nothing else; put any observation in note (optional).'
      + ' Do not check evidence yourself, change permissions, or run another command.',
    { label, phase: 'Verify', agentType: profile.agentType, model, effort, schema })
    if (!verification || !['pass', 'fail'].includes(verification.status) || !reportsOut(verification.report)
      || !verification.verdicts || !verification.counts
      || items.some(item => !VERDICTS.includes(verification.verdicts[keyOf(item)]))) throw new Error('incomplete verifier report')
    const reported = Object.values(verification.verdicts)
    if (reported.some(v => !VERDICTS.includes(v))) throw new Error('illegal verifier verdict')
    const observed = Object.fromEntries(VERDICTS.map(v => [v, reported.filter(verdict => verdict === v).length]))
    if (VERDICTS.some(v => verification.counts[v] !== observed[v])
      || new Set(items.map(keyOf)).size !== items.length
      || (verification.status === 'pass' && (observed.UNVERIFIED > 0 || observed.UNCHECKED > 0))) throw new Error('inconsistent verifier report')
    verification.report = out  // the checker's text only proves it read this file; readers open the engine's own path
  } catch (e) {
    verification = { status: 'unavailable', counts: { VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: items.length },
      verdicts: Object.fromEntries(items.map(item => [keyOf(item), 'UNCHECKED'])), report: out,
      note: String((e && e.message) || e) }
  }
  return { items: applyVerdicts(items, verification.verdicts, keyOf), verification }
}
// CLOSE-CHECK-END

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. Inlined per script because the Workflow sandbox has
// no require/import.
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
if (!TRANSPORT || typeof TRANSPORT !== 'object' || !validStringList(TRANSPORT.ids)
  || (TRANSPORT.efforts !== undefined && !validStringList(TRANSPORT.efforts))) {
  return { error: 'worklog-relevance: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary ({name, ids, efforts?}); without it this engine has no legal pins and refuses to guess.' }
}
const PIN = (m) => m
const EFF = (e) => e

// Pins are the CALLER's decision (provider choice is budget-posture, and the ladder in
// reference/model_ladder_evidence.md is the only role→model surface).
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
    error: 'args needs {scope, model, effort, tier} with a non-empty scope, tier the one model serves (the provider guard checks it). Optional: domains[] to narrow the index scan, promptPath to override the shared mandate.',
  }
}
if (!VALID_MODELS.includes(A.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(A.effort))) {
  return { error: 'worklog-relevance: pin model and effort on every job (orchestration §5). Unpinned or illegal: worklog:relevance (args.model, args.effort). Each job needs ' + pinVocab + '.' }
}
// closeCheck runs on every relevance pass, so its verifier pin is always required; refuse before the agent starts.
const checkerOk = !!A.checker && typeof A.checker === 'object' && VALID_MODELS.includes(A.checker.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.checker.effort))
if (!checkerOk) {
  return { error: 'worklog-relevance: args.checker = {model, effort} is required because every run starts the closeCheck verifier; args.checker must pin ' + pinVocab + '. Refusing before any agent starts.' }
}

log('PINS ' + JSON.stringify({ 'worklog:relevance': PIN(A.model) + '/' + EFF(A.effort) + ' tier:' + A.tier }))
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
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
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

// TOOL PROFILES — the inventory this delegate launches with. A native child INHERITS the session's
// MCP set, so the entry is `inherited` with effectiveMcp unknown unless the delegate runs as its
// generated agent type (`agent-type`); never a strict claim of enforcement.
const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
// LEAN AGENT TYPE (plan lean-delegate-agent-types). Copied per engine: a Workflow script has no imports.
// A job runs as its profile's generated `<prefix>-*` type only when it asked for `general-purpose` or no
// type, its profile names an `agentType` with no add/remove delta, and hooks/workflow_provider_guard.py
// proved its brief covered: `args.__coverage[key].covered` holds the profile's shape. No entry, no swap.
// A swapped prompt ends with `args.__skillIndex`, because a `<prefix>-*` type has no Skill tool. A session
// that predates the generated type rejects it; the job retries once, as asked (the known-good type), and is re-recorded.
const LEAN = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { c: (a && a.__coverage) || {}, s: (a && typeof a.__skillIndex === 'string') ? a.__skillIndex : '' } } catch (e) { return { c: {}, s: '' } } })()
const leanTypeOf = (keys, asked, tp) => {
  const cov = keys.map(k => LEAN.c[k]).find(Boolean)
  return ((asked == null || asked === 'general-purpose') && tp && tp.agentType
    && !(tp.add || []).length && !(tp.remove || []).length
    && cov && Array.isArray(cov.covered) && cov.covered.includes(tp.shape)) ? tp.agentType : null
}
const leanRecord = (tp, asked, swapped) => tp ? Object.assign({}, tp, swapped
  ? { agentType: swapped, enforcement: 'agent-type', effectiveMcp: (tp.mcpServers || []).join(', ') }
  : { agentType: asked || 'general-purpose' }) : null
const leanAgent = (prompt, opts, swapped, rec) => {
  if (!swapped) return agent(prompt, opts)
  const asked = opts.agentType || 'general-purpose'
  return agent(LEAN.s ? prompt + '\n\n' + LEAN.s : prompt, Object.assign({}, opts, { agentType: swapped })).catch(e => {
    const msg = String((e && e.message) || e)
    if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
    log('FALLBACK ' + opts.label + ' ' + swapped + '→' + asked)
    rec[opts.label] = Object.assign({}, rec[opts.label], { agentType: 'general-purpose', enforcement: 'inherited', effectiveMcp: 'unknown/inherited' })
    log('TOOL-PROFILES-REVISED ' + JSON.stringify(rec))
    return agent(prompt, Object.assign({}, opts, { agentType: asked }))
  })
}
const tp = TOOLP.d['worklog:relevance'] || TOOLP.f['survey'] || null
const swapped = leanTypeOf(['worklog:relevance'], undefined, tp)
const RECORDED = { 'worklog:relevance': leanRecord(tp, undefined, swapped) }
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

const r = await leanAgent(prompt, { label: 'worklog:relevance', phase: 'Relevance', schema: RELEVANCE_SCHEMA, model: PIN(A.model), effort: EFF(A.effort) }, swapped, RECORDED)

// Unambiguous empty: `count` is always present, so a caller never has to distinguish "no overlaps"
// from "the agent returned nothing".
let overlaps = (r && Array.isArray(r.overlaps)) ? r.overlaps : []
// `checked` must reach the caller: the schema forces the delegate to declare what it inspected, and
// discarding that here would restore the exact ambiguity it exists to remove — an empty `overlaps`
// with obsidianOpened:false is an incomplete check, not a clean sweep.
let checked = (r && typeof r.checked === 'object' && r.checked) ? r.checked : null
log('OVERLAPS ' + overlaps.length + ' (stoppedAt=' + (checked ? checked.stoppedAt : 'UNREPORTED')
  + ', obsidianOpened=' + (checked ? checked.obsidianOpened : 'UNREPORTED')
  + ', shortlisted=' + (checked && Array.isArray(checked.shortlisted) ? checked.shortlisted.length : '?') + ')')
if (checked && checked.basis) { log('BASIS ' + checked.basis) }
const sourceItems = overlaps.map((item, index) => Object.assign({}, item, {lens: 'worklog:relevance', index: index + 1}))
sourceItems.push(Object.assign({}, checked || {evidence: []}, {lens: 'worklog:relevance', index: 'checked'}))
const closed = await closeCheck('claims', {items: sourceItems, keyOf: item => item.lens + '#' + item.index, out: '.claude/scratch/worklog_relevance_claims.json',
  seats: [{ label: 'worklog:relevance', model: PIN(A.model), effort: EFF(A.effort), tier: A.tier }] })
overlaps = closed.items.slice(0, -1)
checked = closed.items[closed.items.length - 1]
const verification = closed.verification
return { count: overlaps.length, overlaps, checked, verification }
