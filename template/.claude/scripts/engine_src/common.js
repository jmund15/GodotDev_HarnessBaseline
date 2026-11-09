// Shared engine helpers. schema_parity.js --write copies each block into every engine that declares it.
// A block is the byte text of a top-level declaration, identical across those engines. Edit it here, then --write.
// ENGINE-BLOCK-BEGIN A
let A
// ENGINE-BLOCK-END A
// ENGINE-BLOCK-BEGIN PIN
const PIN = (m) => m
// ENGINE-BLOCK-END PIN
// ENGINE-BLOCK-BEGIN EFF
const EFF = (e) => e
// ENGINE-BLOCK-END EFF
// ENGINE-BLOCK-BEGIN VALID_PICKS
const VALID_PICKS = ['cell', 'room', 'owner', 'default']
// ENGINE-BLOCK-END VALID_PICKS
// ENGINE-BLOCK-BEGIN VALID_SPECS
const VALID_SPECS = ['tight', 'judged']
// ENGINE-BLOCK-END VALID_SPECS
// ENGINE-BLOCK-BEGIN basisFaults
const basisFaults = []
// ENGINE-BLOCK-END basisFaults
// ENGINE-BLOCK-BEGIN pinBasis
const pinBasis = (j) => (j.pick !== undefined ? ' pick:' + j.pick : '') + (j.spec !== undefined ? ' spec:' + j.spec : '')
// ENGINE-BLOCK-END pinBasis
// ENGINE-BLOCK-BEGIN RAILS
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
// ENGINE-BLOCK-END RAILS
// ENGINE-BLOCK-BEGIN RAILS_TEXT
const RAILS_TEXT = (A.__railsText && typeof A.__railsText === 'object') ? A.__railsText : {}
// ENGINE-BLOCK-END RAILS_TEXT
// ENGINE-BLOCK-BEGIN RAILS_HEADER
const RAILS_HEADER = '=== DELEGATE RAILS (standing rules for how you work; your task is the brief above) ==='
// ENGINE-BLOCK-END RAILS_HEADER
// ENGINE-BLOCK-BEGIN inlineRails
const inlineRails = (label, guard, tier) => {
  const text = RAILS_TEXT[guard + '/' + tier]
  if (typeof text === 'string' && text.trim()) return ['', RAILS_HEADER, text].join('\n')
  if (Object.keys(RAILS_TEXT).length > 0) log('RAILS-FALLBACK ' + label + ' ' + guard + '/' + tier)
  return null
}
// ENGINE-BLOCK-END inlineRails
// ENGINE-BLOCK-BEGIN DRIVER_NOTES
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
// ENGINE-BLOCK-END DRIVER_NOTES
// ENGINE-BLOCK-BEGIN driverNotes
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
// ENGINE-BLOCK-END driverNotes
// ENGINE-BLOCK-BEGIN TOOLP
const TOOLP = (A.__toolProfiles && typeof A.__toolProfiles === 'object') ? A.__toolProfiles : {}
// ENGINE-BLOCK-END TOOLP
// ENGINE-BLOCK-BEGIN SPILL_REL
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
// ENGINE-BLOCK-END SPILL_REL
// ENGINE-BLOCK-BEGIN NORMAL_SPILL_DIR
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
// ENGINE-BLOCK-END NORMAL_SPILL_DIR
// ENGINE-BLOCK-BEGIN DIGEST_WORDS
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
// ENGINE-BLOCK-END DIGEST_WORDS
// ENGINE-BLOCK-BEGIN projectRootOf
const projectRootOf = (a) => repoRootOf(a).replace(/\/\.claude\/worktrees\/[^/]+$/, '')
// ENGINE-BLOCK-END projectRootOf
// ENGINE-BLOCK-BEGIN RELAY_LINE
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// ENGINE-BLOCK-END RELAY_LINE
// The key hooks/amend_delivery.py reads to deliver a parent's amendment to a running author job.
// ENGINE-BLOCK-BEGIN AMEND_LINE
const AMEND_LINE = (promptPath) => 'Amendments: the harness delivers ' + promptPath + '.amend.md to you when your parent writes or changes it; a delivered amendment overrides the brief, and your report names what you applied.'
// ENGINE-BLOCK-END AMEND_LINE
// ENGINE-BLOCK-BEGIN repoRootOf
function repoRootOf(a) {
  const root = a && typeof a.__repoRoot === 'string' ? a.__repoRoot.trim().replace(/\\/g, '/').replace(/\/+$/, '') : ''
  return /^([A-Za-z]:)?\//.test(root) ? root : ''
}
// ENGINE-BLOCK-END repoRootOf
// ENGINE-BLOCK-BEGIN rootedPath
function rootedPath(a, p) {
  const root = repoRootOf(a)
  return root && typeof p === 'string' && !/^([A-Za-z]:)?[\\/]/.test(p) ? root + '/' + p.replace(/^\.\//, '') : p
}
// ENGINE-BLOCK-END rootedPath
// ENGINE-BLOCK-BEGIN cdRootPrefix
function cdRootPrefix(a) {
  const root = repoRootOf(a)
  return root ? "cd '" + root.replace(/'/g, "'\"'\"'") + "' && " : ''
}
// ENGINE-BLOCK-END cdRootPrefix
// ENGINE-BLOCK-BEGIN VERDICTS
const VERDICTS = ['VERIFIED', 'UNVERIFIED', 'UNCHECKED']
// ENGINE-BLOCK-END VERDICTS
// ENGINE-BLOCK-BEGIN applyVerdicts
const applyVerdicts = (items, verdicts, keyOf) => items.map(item => {
  const supplied = verdicts[keyOf(item)]
  const verdict = VERDICTS.includes(supplied) ? supplied : 'UNCHECKED'
  return Object.assign({}, item, { verdict, confidence: verdict === 'VERIFIED' ? 'verified' : 'unverified' })
})
// ENGINE-BLOCK-END applyVerdicts
// ENGINE-BLOCK-BEGIN verifierAgent
const verifierAgent = (prompt, opts) => agent(prompt, opts).catch(e => {
  const msg = String((e && e.message) || e)
  if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
  log('FALLBACK ' + opts.label + ' ' + opts.agentType + '→general-purpose')
  return agent(prompt, Object.assign({}, opts, { agentType: 'general-purpose' }))
})
// ENGINE-BLOCK-END verifierAgent
// ENGINE-BLOCK-BEGIN closeCheck
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
// ENGINE-BLOCK-END closeCheck
// ENGINE-BLOCK-BEGIN CLAIM_FENCE
const CLAIM_FENCE = /```(?:claims|findings)[ \t]*\r?\n([\s\S]*?)```/g
// ENGINE-BLOCK-END CLAIM_FENCE
// ENGINE-BLOCK-BEGIN inlineClaims
const inlineClaims = []
// ENGINE-BLOCK-END inlineClaims
// ENGINE-BLOCK-BEGIN declared
const declared = A.__returns && Object.keys(A.__returns).length > 0
// ENGINE-BLOCK-END declared
// ENGINE-BLOCK-BEGIN hasFence
const hasFence = Object.values(out).some(value => typeof value === 'string' && /```(?:claims|findings)[ \t]*\r?\n/.test(value))
// ENGINE-BLOCK-END hasFence
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
// ENGINE-BLOCK-BEGIN RESULT_MODE
const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
// ENGINE-BLOCK-END RESULT_MODE
// ENGINE-BLOCK-BEGIN spillEscapesRoot
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
// ENGINE-BLOCK-END spillEscapesRoot
// ENGINE-BLOCK-BEGIN spillRootAllowed
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
// ENGINE-BLOCK-END spillRootAllowed
// ENGINE-BLOCK-BEGIN READ_FAILURE
const READ_FAILURE = '(retry a transient read error; a missing path or a denial is not retried: return checked.stoppedAt `blocked` with the path and cause in checked.basis)'
// ENGINE-BLOCK-END READ_FAILURE
// ENGINE-BLOCK-BEGIN flags
const flags = []
// ENGINE-BLOCK-END flags
