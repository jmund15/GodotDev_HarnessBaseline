export const meta = {
  name: 'dispatch-chains',
  description: 'Run pinned job chains in parallel, each chain sequential',
  phases: [{ title: 'Chains', detail: 'one concurrent lane per chain; jobs sequential inside a lane' }],
}
// Claims/findings jobs close with independent deterministic verification.

// Platform contract mirrors dispatch.js: args may arrive as a JSON string or an already-parsed
// value, and an uncaught SyntaxError would name neither this workflow nor the expected shape.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'dispatch-chains: args must be JSON-serializable {chains: [{name, jobs: [{label, promptPath, model, effort}, ...]}, ...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
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
        "description": "The stdout of a read-only git command from the allow-list (git grep, ls-files, ls-tree, log, show, branch, check-ignore, cat-file, rev-parse, diff --stat or --name-only, status --short), run without a shell from the repository root. VERIFIED when stdout holds the output, or when output is empty, stdout is empty and every pathspec the command names matches a tracked path. Any other command, a shell metacharacter, an unlisted flag or a timeout is UNCHECKED. One leading `git -C <dir>` (an existing repository-relative directory, where the command then runs) and `check-ignore --no-index` are allowed.",
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
    // A subject the fold skipped writes no digest, so each subject's snapshot also names the files the review read.
    for (const subject of (fold && !fold.findings && Array.isArray(fold.subjects) ? fold.subjects : [])) {
      if (subject && subject.snapshot) command += ' --reviewed-snapshot ' + quoteArg(subject.snapshot)
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
    // The agent Bash route cuts a command near 8K characters mid-quote, so the seats file is written in parts.
    const COMMAND_BUDGET = 6000
    const writer = 'import sys; from pathlib import Path; p=Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); f=p.open(sys.argv[2], encoding="utf-8", newline=""); f.write(sys.argv[3]); f.close()'
    const writes = []
    for (let rest = JSON.stringify(launched); rest.length;) {
      const prefix = cdRootPrefix(A) + 'python3 -c ' + quoteArg(writer) + ' ' + quoteArg(seatsFile) + ' ' + (writes.length ? 'a' : 'w') + ' '
      if ((prefix + "'{}'").length > COMMAND_BUDGET) throw new Error('verifier seats-file path leaves no room within the command budget')
      let size = rest.length
      while ((prefix + quoteArg(rest.slice(0, size))).length > COMMAND_BUDGET) size = Math.max(1, Math.floor(size * 0.9))
      if (size < rest.length && /[\uD800-\uDBFF]/.test(rest[size - 1])) size -= 1
      writes.push(prefix + quoteArg(rest.slice(0, size)))
      rest = rest.slice(size)
    }
    if (mode === 'review') command += ' --seats-file ' + quoteArg(seatsFile)
    const printer = 'import json, sys; r = json.load(open(sys.argv[1], encoding="utf-8")); print(json.dumps({"status": r["status"], "counts": r["counts"], "verdicts": {k: v["verdict"] for k, v in r["verdicts"].items()}}))'
    command = cdRootPrefix(A) + command + ' ; python3 -c ' + quoteArg(printer) + ' ' + quoteArg(out)
    log('PINS ' + JSON.stringify({[label]: model + '/' + effort + '/' + profile.agentType + ' tier:fanout'}))
    verification = await verifierAgent('Run each seats-file write below with Bash first, one call per line, in order:\n' + writes.join('\n')
      + '\nRun exactly this command with Bash: ' + command
      + '\nIts last output line is one JSON object holding status, counts and verdicts, read from the --out file; it prints even when the check exits 1.'
      + ' Return ONLY {status, counts, verdicts, report, note}: copy status, counts and verdicts from that line unchanged. Set report to the --out path and nothing else; put any observation in note (optional).'
      + ' Do not check evidence yourself, change permissions, or run any other command.',
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

const chains = Array.isArray(A.chains) ? A.chains : []

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. `efforts` is absent when the transport declares none.
// args.checker = {model, effort}: the pin for the verifier this engine starts itself (closeCheck). Required
// only when the run reaches that step, and validated before any agent starts. No engine supplies a default pin.
const PIN = (m) => m
const EFF = (e) => e
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
if (!transportValid) {
  return { error: 'dispatch-chains: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary (non-empty string array `ids`, optional non-empty `efforts`); without it this engine has no legal pins and refuses to guess.' }
}

// Every job pins its own model, and its own effort when the transport declares an effort vocabulary.
const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
const tierOfJob = (j) => j.tier || A.tier || '-'
const VALID_AGENT_TYPES = ['Explore', 'Plan', 'general-purpose', 'lane-survey', 'lane-review', 'lane-author']

const allJobs = chains.flatMap(c => (c && Array.isArray(c.jobs)) ? c.jobs.map(j => [c, j]) : [])
const unpinned = allJobs.filter(([, j]) => !!j && (!VALID_MODELS.includes(j.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(j.effort))))
if (unpinned.length > 0) {
  return {
    error: 'dispatch-chains: pin model and effort on every job (orchestration §5). Unpinned or illegal: ' + unpinned.map(([, j]) => j.label || '(unlabeled)').join(', ') + '. Each job needs ' + pinVocab + '.',
    badJobs: unpinned.map(([, j]) => j.label || '(unlabeled)'),
  }
}
const badChain = chains.filter(c => !c || !c.name || !Array.isArray(c.jobs) || c.jobs.length === 0)
const badJob = allJobs.filter(([, j]) => !j || !j.label || !j.promptPath || !VALID_AGENT_TYPES.includes(j.agentType))
if (chains.length === 0 || badChain.length > 0 || badJob.length > 0) {
  return {
    error: 'Every chain needs {name, jobs:[...]} and every job needs {label, promptPath, model, agentType}; agentType must be in ['
      + VALID_AGENT_TYPES.join('|') + ']. Each job needs ' + pinVocab
      + '. Write each prompt to a file and pass its path — never inline large prompts into args.',
    badChains: badChain.map(c => (c && c.name) || '(unnamed)'),
    badJobs: badJob.map(([, j]) => (j && j.label) || '(unlabeled)'),
  }
}

// OPTIONAL launch facts, recorded on the job: `pick` says WHY this pin was chosen
// (`cell|room|owner|default`), `spec` whether the slice's test intent is clearly defined
// (`tight|judged`, orchestration §5). Optional and independent -- an omitted field is a real answer
// the readouts count as unrecorded (`pins.spec-unrecorded.all`) -- but a value outside the enum is
// refused before any agent starts.
const VALID_PICKS = ['cell', 'room', 'owner', 'default']
const VALID_SPECS = ['tight', 'judged']
const basisFaults = []
for (const [, j] of allJobs) {
  if (!j) continue
  if (j.pick !== undefined && !VALID_PICKS.includes(j.pick)) basisFaults.push({ job: j, field: 'pick' })
  if (j.spec !== undefined && !VALID_SPECS.includes(j.spec)) basisFaults.push({ job: j, field: 'spec' })
}
if (basisFaults.length > 0) {
  return {
    error: 'dispatch-chains: pick must be in [' + VALID_PICKS.join('|') + '] and spec must be in [' + VALID_SPECS.join('|') + '] on every job that states them (orchestration §5). Illegal: '
      + basisFaults.map(f => (f.job.label || '(unlabeled)') + ' ' + f.field + '=' + JSON.stringify(f.job[f.field])).join(', ') + '. Omit either field to record none.',
    badJobs: basisFaults.map(f => f.job.label || '(unlabeled)'),
  }
}

// Labels are the progress-tree identity and (with spillDir) the deliverable filename. A collision
// across chains is silent data loss, so reject it up front rather than overwriting.
const seenLabel = {}
const dupLabel = allJobs.filter(([, j]) => { const d = !!seenLabel[j.label]; seenLabel[j.label] = true; return d })
if (dupLabel.length > 0) {
  return { error: 'Job labels must be unique across ALL chains. Duplicates: ' + dupLabel.map(([, j]) => j.label).join(', ') }
}

// The closeCheck verifier starts only when the run declares returns (or a fence lands at runtime, which closeCheck
// guards again). Validate its pin before any agent starts, when this run declares returns.
const declaredReturns = !!(A.__returns && Object.keys(A.__returns).length > 0)
const checkerOk = !!A.checker && typeof A.checker === 'object' && VALID_MODELS.includes(A.checker.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.checker.effort))
if (declaredReturns && !checkerOk) {
  return { error: 'dispatch-chains: args.checker = {model, effort} is required because this run reaches the closeCheck verifier; args.checker must pin ' + pinVocab + '. Refusing before any agent starts.' }
}

log('CHAINS ' + JSON.stringify(Object.fromEntries(chains.map(c => [c.name, c.jobs.length]))))
const pinBasis = (j) => (j.pick !== undefined ? ' pick:' + j.pick : '') + (j.spec !== undefined ? ' spec:' + j.spec : '')
log('PINS ' + JSON.stringify(Object.fromEntries(allJobs.map(([, j]) => [j.label, PIN(j.model) + '/' + EFF(j.effort) + (j.agentType ? '/' + j.agentType : '') + ' tier:' + tierOfJob(j) + pinBasis(j)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// The concurrency contract this engine exists to express. Contention scope is NOT one boolean:
// a machine-wide single-flight resource (the runtime test slots, a language-server wrapper) is unsafe
// whenever ANY other chain is live, while a per-lane resource (a worktree's obj/bin) is safe
// precisely because a chain runs its own jobs one at a time. `chain.exclusiveResource` names what
// this lane owns outright; `A.sharedSingleFlight` lists what no lane may touch.
const SHARED = Array.isArray(A.sharedSingleFlight) ? A.sharedSingleFlight : []
const guardFor = (c) => {
  const lines = ['', '=== ORCHESTRATION GUARD ===']
  if (chains.length > 1) {
    lines.push('Other agents are running CONCURRENTLY in other lanes. You are the ONLY agent in your own lane'
      + (c.exclusiveResource ? ' (' + c.exclusiveResource + '), which you own outright for the duration of your task.' : '.'))
  }
  if (SHARED.length > 0) {
    lines.push('MACHINE-WIDE single-flight — do NOT use these under any circumstances, even though you own your lane: '
      + SHARED.join('; ') + '. If your brief mandates one, STOP and report that it needs a serialized dispatch.')
  }
  return lines.join('\n')
}

// Delegate rails — ONE home (.claude/guards/), assembled and inlined by the Workflow hook (see dispatch.js).
const VALID_SHAPES = ['any', 'survey', 'review', 'author']
// Guard tier per RECEIVING model is registry data (`railTier`), injected as `args.__rails` by
// hooks/workflow_provider_guard.py because a Workflow script cannot read files. A model the map
// does not name, or a call the hook did not rewrite, reads `detailed`: the fail-safe direction.
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
const shapeOf = (j) => VALID_SHAPES.includes(j.shape) ? j.shape : 'any'
const tierOf = (j) => RAILS[j.model] || 'detailed'

// TOOL PROFILES — what each child of this run launches with. A native child INHERITS the session's
// MCP set (the runtime restricts no per-child server), so an entry is `inherited` with effectiveMcp
// unknown unless the job runs as its generated agent type (`agent-type`). hooks/workflow_provider_guard.py resolves args.__toolProfiles for a job that
// DECLARED a shape or a delta, and args.__toolProfileDefaults (one row per shape) for the stages a
// script spawns on its own labels. `log()` output is retained in <session>/workflows/<runId>.json.
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
const profileOf = (j) => TOOLP.d[j.label] || TOOLP.f[shapeOf(j)] || null
const swapOf = (j) => leanTypeOf([j.label], j.agentType, profileOf(j))
const effectiveType = (j) => swapOf(j) || j.agentType
const RECORDED = Object.fromEntries(allJobs.map(([, j]) => [j.label, leanRecord(profileOf(j), j.agentType, swapOf(j))]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))
// Rails text per (shape, tier) is assembled from .claude/guards/ by tools/guard_text.py and injected as
// `args.__railsText` by hooks/workflow_provider_guard.py, because this script cannot read files. The
// inline text reaches every delegate; the Read pointer is only the fallback when the hook supplied
// none, and a present map missing a job's pair logs RAILS-FALLBACK so a key disagreement is visible.
const RAILS_TEXT = (A.__railsText && typeof A.__railsText === 'object') ? A.__railsText : {}
const RAILS_HEADER = '=== DELEGATE RAILS (standing rules for how you work; your task is the brief above) ==='
const inlineRails = (label, shape, tier) => {
  const text = RAILS_TEXT[shape + '/' + tier]
  if (typeof text === 'string' && text.trim()) return ['', RAILS_HEADER, text].join('\n')
  if (Object.keys(RAILS_TEXT).length > 0) log('RAILS-FALLBACK ' + label + ' ' + shape + '/' + tier)
  return null
}
// Registry driverNotes per pinned model; rails key on (shape, tier) and carry no model, so notes travel in args.__driverNotes.
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
const guardRef = (x) => { const r = railsRef(x); return r && r + driverNotes(x.model) }
const railsRef = (j) => {
  const tier = tierOf(j)
  const shape = shapeOf(j)
  const inline = inlineRails(j.label, shape, tier)
  if (inline) return inline
  const files = shape === 'any' ? '.claude/guards/any.md' : '.claude/guards/any.md and .claude/guards/' + shape + '.md'
  return ['', '=== DELEGATE RAILS ===',
    'Read ' + files + ' with the Read tool, plus each layer overlay beside them (.claude/guards/<name>.<layer>.md) that exists, and follow the `## ' + tier
    + '` section of each. Read ONLY that section — the other tiers are for other models.'].join('\n')
}

// Return-path spill (optional), identical contract to dispatch.js: bound what lands in orchestrator
// context, and let the orchestrator Read only the deliverables it acts on.
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
if (NORMAL_SPILL_DIR && (/(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR) || !/^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))) {
  return { error: 'dispatch-chains: spillDir must be repository-relative and contained by .claude/scratch/ (for example .claude/scratch/<task>/spill). Received: ' + SPILL_REL }
}
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan', 'lane-review', 'lane-survey']
// A job dispatched from a linked worktree (<project>/.claude/worktrees/<name>) reports under the project root,
// where the brief's own paths point, not under the worktree it runs in.
const projectRootOf = (a) => repoRootOf(a).replace(/\/\.claude\/worktrees\/[^/]+$/, '')
const SPILL_DIR = SPILL_REL && projectRootOf(A) ? projectRootOf(A) + '/' + NORMAL_SPILL_DIR : SPILL_REL
const spills = (j) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(effectiveType(j))
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'
// A writing job names what it wrote beyond its spec, so review routes those units as design no
// reviewer saw (orchestration §2 *Later rounds*); `dispatch_result.py check --block choices=unit,why` validates it.
const choicesContract = (j) => NO_WRITE_AGENT_TYPES.includes(effectiveType(j)) ? '' : [
  '',
  '=== CHOICES (orchestration/reference/writer_returns.md) ===',
  'End your deliverable with a fenced block opened by ```choices holding one JSON object per line, {"unit": "<what you wrote>", "why": "<its reason>"}, for each unit whose content your brief or plan does not state. Leave the block empty when there is none. With Bash, run `python3 .claude/tools/dispatch_result.py check <your deliverable file> --block choices=unit,why` before returning; the parent runs it otherwise, and a missing block fails it.',
].join('\n')

const spillContract = (j) => spills(j) ? ['',
  '=== RETURN-PATH CONTRACT (overrides any "return the full result" wording in your brief) ===',
  'Write your FULL deliverable to ' + spillPath(j.label) + ' using the Write tool. That file is yours alone.',
  'Then return ONLY: (a) a digest of at most ' + DIGEST_WORDS + ' words covering what the caller must decide or act on, (b) one line `couldNotSatisfy:` naming every unmet item with what stopped it (denial, missing input, out-of-lane file) and the exact intended change, or `couldNotSatisfy: none`, and (c) a final line exactly `FULL: ' + spillPath(j.label) + '`.',
  'Do NOT restate the full deliverable in your final message. If you could not write the file, say so in the digest instead of pasting the content.',
].join('\n') : ''

const INLINE_LABELS = SPILL_DIR ? allJobs.map(([, j]) => j).filter(j => !spills(j)).map(j => j.label) : []
if (SPILL_DIR) {
  const seenPath = {}
  const collided = allJobs.map(([, j]) => j).filter(spills).filter(j => { const p = spillPath(j.label).toLowerCase(); const d = !!seenPath[p]; seenPath[p] = true; return d })
  if (collided.length > 0) {
    return { error: 'args.spillDir is set, but these labels collide after filename sanitization: ' + collided.map(j => j.label).join(', ') }
  }
  log('SPILL-DIR ' + SPILL_DIR + ' (digest cap ' + DIGEST_WORDS + ' words/job)')
  if (INLINE_LABELS.length > 0) {
    log('SPILL-EXEMPT (' + NO_WRITE_AGENT_TYPES.join('/') + ' have no Write tool; these return in full INLINE and cost orchestrator context): ' + INLINE_LABELS.join(', '))
  }
}

// A later job in a chain runs against the tree its predecessors already changed, which is the whole
// point of chaining — so tell it that, and name the predecessors it must not silently contradict.
const handoff = (c, idx) => idx === 0 ? '' : ['',
  '=== CHAIN POSITION ===',
  'You are step ' + (idx + 1) + ' of ' + c.jobs.length + ' in lane `' + c.name + '`. Earlier steps in this lane ('
  + c.jobs.slice(0, idx).map(p => '`' + p.label + '`').join(', ')
  + ') have ALREADY edited this working tree and their changes are present. Read the current file bytes rather than assuming the pre-chain state, and if a change of yours would revert or contradict an earlier step, report the conflict instead of silently overwriting it.',
].join('\n')

// The platform relays the triggering user message to every agent as authoritative; a job whose
// brief looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// How a parent corrects a running author job: Workflow agents have no inbox (orchestration §4).
const AMEND_LINE = (promptPath) => 'Amend: at each item and before returning, if ' + promptPath + '.amend.md exists, read it; it overrides the brief. Name what you applied.'

phase('Chains')
const results = await parallel(chains.map(c => async () => {
  const out = []
  for (let i = 0; i < c.jobs.length; i++) {
    const j = c.jobs[i]
    const ctx = A.contextPath
      ? 'SHARED CONTEXT for this dispatch is at: ' + A.contextPath + ' — read it with the Read tool FIRST (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it).\n\n'
      : ''
    const prompt = ctx
      + 'Evidence: ' + EVIDENCE_RULES + '\n'
      + 'Your full task brief is at: ' + j.promptPath + ' — read it with the Read tool and execute it exactly (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it). Your final message is the deliverable: return the result the brief asks for, self-contained, no meta-commentary.'
      + '\n' + RELAY_LINE
      + (shapeOf(j) === 'author' ? '\n' + AMEND_LINE(j.promptPath) : '')
      + handoff(c, i)
      + guardFor(c)
      + guardRef(j)
      + spillContract(j)
      + choicesContract(j)
    const opts = { label: j.label, phase: 'Chains', model: PIN(j.model), effort: EFF(j.effort), agentType: j.agentType }
    let r
    try {
      r = await leanAgent(prompt, opts, swapOf(j), RECORDED)
    } catch (e) {
      out.push([j.label, {
        error: 'dispatch-chains: agent rejected (' + ((e && e.message) || String(e)) + '); remaining jobs in lane `' + c.name + '` were not run',
      }])
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length
        + ' (rejected result from ' + j.label + ')')
      break
    }
    if (r == null) {
      out.push([j.label, {
        error: 'dispatch-chains: agent returned no result; remaining jobs in lane `' + c.name + '` were not run',
      }])
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length
        + ' (no result from ' + j.label + ')')
      break
    }
    out.push([j.label, r])
    log('lane ' + c.name + ': ' + (i + 1) + '/' + c.jobs.length + ' done (' + j.label + ')')
  }
  return out
}))

const out = Object.fromEntries(results.filter(Boolean).flat().filter(Boolean))
const inlineRecover = INLINE_LABELS.length > 0
  ? 'No Write tool for ' + INLINE_LABELS.join(', ') + ': their deliverables came back inline. Land them with: python3 .claude/tools/dispatch_result.py <this run\'s transcript dir> --out-dir ' + SPILL_DIR
  : null
const CLAIM_FENCE = /```(?:claims|findings)[ \t]*\r?\n([\s\S]*?)```/g
const inlineClaims = []
for (const [label, result] of Object.entries(out)) {
  if (typeof result !== 'string') continue
  let index = 0
  for (const match of result.matchAll(CLAIM_FENCE)) {
    for (const line of match[1].split(/\r?\n/).filter(line => line.trim())) {
      index++
      try {
        const claim = JSON.parse(line)
        if (claim && typeof claim === 'object' && !Array.isArray(claim)) inlineClaims.push({ label, index, claim })
      } catch (e) { /* The deterministic report records malformed rows. */ }
    }
  }
}
const declared = A.__returns && Object.keys(A.__returns).length > 0
const hasFence = Object.values(out).some(value => typeof value === 'string' && /```(?:claims|findings)[ \t]*\r?\n/.test(value))
let verification = null
if (declared || hasFence) {
  const checked = await closeCheck('claims', {
    items: inlineClaims.map(row => Object.assign({}, row.claim, { __key: String(row.claim.id || row.label + '#' + row.index) })),
    keyOf: claim => claim.__key,
    out: (SPILL_DIR || '.claude/scratch') + '/post_check_claims.json',
    spillDir: SPILL_DIR,
    seats: allJobs.filter(([, j]) => j.label in out).map(([, j]) => Object.assign({}, j, { tier: tierOfJob(j) })),
  })
  verification = checked.verification
  let position = 0
  for (const [label, result] of Object.entries(out)) {
    if (typeof result !== 'string') continue
    out[label] = result.replace(CLAIM_FENCE, (fence, body) => fence.replace(body, body.split(/\r?\n/).map(line => {
      if (!line.trim()) return line
      try {
        const claim = JSON.parse(line)
        if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return line
        const applied = Object.assign({}, checked.items[position++])
        delete applied.__key
        return JSON.stringify(applied)
      } catch (e) { return line }
    }).join('\n')))
  }
}
const returned = SPILL_DIR
  ? Object.assign(inlineRecover ? { inlineRecover } : {}, { spillDir: SPILL_DIR, digests: out }, INLINE_LABELS.length > 0 ? { inlineLabels: INLINE_LABELS } : {})
  : out

return verification ? Object.assign(returned, { verification }) : returned
