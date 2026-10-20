export const meta = {
  name: 'explore-fanout',
  description: 'Run read-only discovery lenses in parallel',
  phases: [
    { title: 'Explore', detail: 'dispatch each supplied lens mandate in parallel (read-only + single-flight guard appended)' },
    { title: 'Consolidate', detail: 'stamp lens, merge corroborations, detect contradictions, downgrade unbacked claims, rank by bearing' },
  ],
}

// Args: {lenses: [{key, promptPath, model, effort, agentType, tier}], contextPrefixPath, spillDir?, resultMode?: 'bounded' | 'full',
//        checker: {model, effort}}. Every lens pins its own model (and effort when the transport declares efforts). `checker` pins
//        the closeCheck verifier this engine always starts; it is required and validated before any agent starts.
// A spill file needs a lens profile with Write; without one the claims return through the journal only.
// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'explore-fanout: args must be JSON-serializable {lenses: [{key, promptPath, model, effort}, ...], contextPrefixPath}. Received a non-JSON string. ' + ((e && e.message) || '') }
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
    verification = await agent('Run exactly this command with Bash: ' + command
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
      verdicts: Object.fromEntries(items.map(item => [keyOf(item), 'UNCHECKED'])), report: out }
  }
  return { items: applyVerdicts(items, verification.verdicts, keyOf), verification }
}
// CLOSE-CHECK-END

const lenses = Array.isArray(A.lenses) ? A.lenses : []
const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
if (!['bounded', 'full'].includes(RESULT_MODE)) {
  return { error: 'explore-fanout: resultMode must be `bounded` or `full`.' }
}

// Optional paid-output spill. A Workflow script cannot write files, so only a receiving profile
// with Write may promise one. Runtime journals remain the native durable result source for all profiles.
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
if (!spillRootAllowed) {
  return { error: 'explore-fanout: spillDir must be repository-relative and contained by .claude/scratch/. Received: ' + SPILL_REL }
}
const SPILL_DIR = SPILL_REL && repoRootOf(A) ? repoRootOf(A) + '/' + NORMAL_SPILL_DIR : SPILL_REL
const spillPath = (key) => SPILL_DIR + '/' + String(key).replace(/[^A-Za-z0-9._-]/g, '_') + '.spill.md'
// TOOL PROFILES — what each child of this run launches with. A native child INHERITS the session's
// MCP set (the runtime restricts no per-child server), so an entry is `inherited` with effectiveMcp
// unknown unless the lens runs as its generated agent type (`agent-type`). hooks/workflow_provider_guard.py
// resolves args.__toolProfiles for a job that DECLARED a shape or a delta, keyed by the lens key, and
// args.__toolProfileDefaults (one row per shape) otherwise. `log()` output is retained in
// <session>/workflows/<runId>.json.
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
const profileOf = (l) => TOOLP.d[l.key] || TOOLP.d[l.label] || TOOLP.f['survey'] || null
const swapOf = (l) => leanTypeOf([l.key, l.label], l.agentType, profileOf(l))
const effectiveType = (l) => swapOf(l) || l.agentType
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan', 'lane-review', 'lane-survey']
const NO_SHELL_AGENT_TYPES = ['lane-review', 'lane-survey']
const spills = (l) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(effectiveType(l))

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. `efforts` is absent when the transport declares none. Inlined because the Workflow sandbox has no require/import.
const PIN = (m) => m
const EFF = (e) => e
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
if (!transportValid) {
  return { error: 'explore-fanout: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary (non-empty string array `ids`, optional non-empty `efforts`); without it this engine has no legal pins and refuses to guess.' }
}

// Every lens pins its own model, and its own effort when the transport declares an effort vocabulary.
const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
const tierOfLens = (l) => l.tier || A.tier || '-'

const badAgentObjects = lenses.filter(l => !l || typeof l !== 'object' || Array.isArray(l))
if (badAgentObjects.length) {
  return { error: 'explore-fanout: every args.lenses entry must be an object.' }
}
const unpinned = lenses.filter(l => !VALID_MODELS.includes(l.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(l.effort)))
if (unpinned.length > 0) {
  return {
    error: 'explore-fanout: pin model and effort on every job (orchestration §5). Unpinned or illegal: ' + unpinned.map(l => l.key || '(unkeyed)').join(', ') + '. Each lens needs ' + pinVocab + '.',
    badLenses: unpinned.map(l => l.key || '(unkeyed)'),
  }
}
const bad = lenses.filter(l => typeof l.key !== 'string' || !l.key.trim()
  || !(l.promptPath || l.prompt)
  || typeof l.agentType !== 'string' || !l.agentType.trim())
if (lenses.length === 0 || bad.length > 0) {
  return {
    error: 'Every lens needs {key, promptPath (or prompt), agentType} with ' + pinVocab + '. Mandates live in .claude/commands/agents/explore_agents.md (or .claude/commands/research.md for res-* lenses) — write the resolved text to a scratchpad file and pass its path; never inline a large mandate into args (gotcha_workflow_args_generation_fidelity).',
    badLenses: bad.map(l => l.key || '(unkeyed)'),
  }
}
const duplicateKeys = lenses.map(l => l.key).filter((key, i, keys) => keys.indexOf(key) !== i)
if (duplicateKeys.length) {
  return { error: 'explore-fanout: duplicate lens key(s): ' + [...new Set(duplicateKeys)].join(', ') }
}
if (SPILL_DIR) {
  const writerPaths = lenses.filter(spills).map(l => spillPath(l.key).toLowerCase())
  const collisions = writerPaths.filter((p, i, paths) => paths.indexOf(p) !== i)
  if (collisions.length) {
    return { error: 'explore-fanout: lens keys collide after spill-path sanitization: ' + [...new Set(collisions)].join(', ') }
  }
  log('SPILL-DIR ' + SPILL_DIR)
}

// Rails appended to EVERY lens, in two layers matching review_fanout.js. DOCTRINE (what a good
// survey looks like — routing, and the three absence gotchas) lives in .claude/guards/{any,survey}.md,
// ONE home shared with dispatch.js and hooks/session_model_rails.py; this engine injects a reference,
// never a copy. MACHINE SAFETY + the output contract stay inline because they are this engine's own
// invariants (read-only by construction, it owns CLAIMS_SCHEMA, it knows the lens count) and hold
// at every guard tier.
// Guard tier per RECEIVING model is registry data (`railTier`), injected as `args.__rails` by
// hooks/workflow_provider_guard.py because a Workflow script cannot read files. A model the map
// does not name, or a call the hook did not rewrite, reads `detailed`: the fail-safe direction.
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
const tierOf = (m) => RAILS[m] || 'detailed'
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
const CONCURRENT = lenses.length > 1
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// The read-only line below is prompt-level. Its advisory backstop is armed OUTSIDE this script by
// .claude/hooks/readonly_marker_arm.py (a Workflow script has no filesystem, require, or clock),
// which arms for any engine carrying the next line.
// READONLY-FANOUT-ENGINE
const BASE_CONTRACT = (l) => [
  '',
  '=== ENGINE CONTRACT ===',
  RELAY_LINE,
  spills(l) ? 'Read-only: do NOT modify, create, or delete any file EXCEPT your own spill file, named in the SPILL-BEFORE-VALIDATE contract below.' : 'Read-only: do NOT modify, create, or delete any file.',
  CONCURRENT ? 'You are one of several lenses running CONCURRENTLY: do NOT run a single-flight tool (a test runner, build, engine or language server that allows one process per machine or per checkout; your delegate rails name this project\'s) — anchor with Grep and Read instead of a language server. Python and Node proofs under .claude/tests/ are not single-flight'
    + (NO_SHELL_AGENT_TYPES.includes(effectiveType(l)) ? '; you have no shell, so cite a proof by path instead of running it.' : '; run the edited unit\'s proof file, not harness_tests.py (its machine slots are shared; rules/harness_tooling.md).')
    + ' If your mandate needs a single-flight test run or call-site enumeration through a language server, report it as a gap; it needs a serialized dispatch.' : null,
  'You report STATE, never advice. A claim says what IS; it never says what should be built, fixed, or preferred. Recommendations are the orchestrator\'s to make from your claims.',
  '',
  '=== CLAIMS CONTRACT (the schema validates shape; these are the rules it cannot express) ===',
  '`subject` — the thing the claim is about (symbol, path, doc heading, rule name). Use the SAME string another lens would use for the same thing: the engine groups on it to detect corroboration and contradiction.',
  '`claim` — one or two sentences of fact, short enough to scan in a list. No rationale, no recommendation.',
  '`polarity` — `exists` (present, does what the claim says) · `absent` (searched for and genuinely not there) · `partial` (present but incomplete or divergent — usually the truest answer, prefer it over forcing exists/absent) · `unclear` (you could not settle it; say why in the claim).',
  'Evidence: ' + EVIDENCE_RULES,
  '`evidence` — REQUIRED for `exists`/`partial`: VERBATIM tool output (a grep line, a signature, a quoted sentence), not your summary. Paraphrase survives fabrication; raw output rarely does. A single quoted line is a strong claim; a long quote is a weaker one.',
  'An absent claim uses evidence: [{command, output: ""}], never prose verification.',
  '`bearing` — `premise-contradiction` (the topic assumes something the code refutes — the highest-value claim you can return; surface it even when it makes the rest of your sweep moot) · `reuse-candidate` (something already owns this concern) · `constraint` (a rule/invariant/gotcha the plan must respect) · `blast-radius` (a consumer a change would break) · `context` (orienting only — keep these few).',
  '`confidence` — `verified` only if you ran the tool and read the output yourself; otherwise `unverified`. The engine downgrades any claim whose evidence does not back it up, so overclaiming gains you nothing.',
  '`checked` — provenance that makes an empty result falsifiable: every search you issued in `toolsUsed` as `tool:target`, where you stopped, and one short sentence of basis. An empty `claims` with an empty `toolsUsed` is reported as a lens that DID NOT RUN, not as a clear result.',
  '`gaps` — anything your mandate could not establish. Silence here reads as full coverage.',
  'OUTPUT: return ONLY the JSON object `{"claims": [...], "checked": {...}}` — no prose around it. Keep every field short: the schema imposes no caps, and a shorter object always beats a longer one.',
].filter(line => line !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3). Shape is fixed `survey`:
// this engine is discovery-shaped by construction, so unlike dispatch.js there is nothing to select.
// Registry driverNotes per pinned model; rails key on (shape, tier) and carry no model, so notes travel in args.__driverNotes.
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
const guardRef = (x) => { const r = railsRef(x); return r && r + driverNotes(x.model) }
const railsRef = (l) => {
  const tier = tierOf(l.model)
  const inline = inlineRails(l.label || l.key, 'survey', tier)
  if (inline) return BASE_CONTRACT(l) + '\n' + inline
  return BASE_CONTRACT(l) + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/survey.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md and of each layer overlay beside them (.claude/guards/<name>.<layer>.md) that exists. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n')
}

// Spill-before-validate: the deliverable is written to disk BEFORE the structured-output attempt, so
// a validation rejection can never destroy completed work (measured 2026-08-08 — three lenses
// rejected 5x each with complete deliverables on disk). The caller recovers the file instead of
// re-dispatching; on success the file is redundant and may be ignored.
const spillContract = (l) => spills(l) ? [
  '',
  '=== SPILL-BEFORE-VALIDATE (your deliverable must survive even if validation rejects it) ===',
  'Use the Write tool to save your COMPLETE deliverable — the full claims JSON object you will return, nothing trimmed — to ' + spillPath(l.key) + ' BEFORE you call the structured-output tool. That file is yours alone; no other lens writes it.',
  'The file must contain the same claims/checked/gaps content you return: it is a safety net, not a draft. If validation rejects your return, the orchestrator recovers this file instead of re-dispatching you.',
].join('\n') : ''

// Parity-checked against explore_fanout.schema.json (the sidecar path's -S file) by
// `node .claude/scripts/schema_parity.js` — see that script's header. Edit BOTH or the check fails.
//
// Schema caps and field descriptions: orchestration SKILL §9. The claims contract lives in
// .claude/commands/agents/explore_agents.md §Claims Schema.
// SCHEMA-SSOT-BEGIN
const CLAIMS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['claims', 'checked'],
  properties: {
    checked: {
      type: 'object',
      additionalProperties: false,
      required: ['toolsUsed', 'stoppedAt', 'basis'],
      properties: {
        toolsUsed: { type: 'array', items: { type: 'string' } },
        stoppedAt: { type: 'string', enum: ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked'] },
        basis: { type: 'string' },
      },
    },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['subject', 'polarity', 'claim', 'bearing'],
        properties: {
          subject: { type: 'string' },
          polarity: { type: 'string', enum: ['exists', 'absent', 'partial', 'unclear'] },
          claim: { type: 'string' },
          evidence: EVIDENCE_SCHEMA,
          file: { type: ['string', 'null'] },
          bearing: { type: 'string', enum: ['premise-contradiction', 'reuse-candidate', 'constraint', 'blast-radius', 'context'] },
          confidence: { type: 'string', enum: ['verified', 'unverified'] },
        },
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
  },
}
// SCHEMA-SSOT-END

// `labelPrefix` namespaces the PINS log so a /research fan-out through this engine is separable from
// an /explore one in /orchestration_metrics. Cosmetic to the run, load-bearing to the cost record.
const LABEL = typeof A.labelPrefix === 'string' && A.labelPrefix.trim() ? A.labelPrefix.trim() : 'explore'
// closeCheck runs on every explore, so its verifier pin is always required; refuse before any lens starts.
const checkerOk = !!A.checker && typeof A.checker === 'object' && VALID_MODELS.includes(A.checker.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.checker.effort))
if (!checkerOk) {
  return { error: 'explore-fanout: args.checker = {model, effort} is required because every run starts the closeCheck verifier; args.checker must pin ' + pinVocab + '. Refusing before any lens starts.' }
}
const resolved = lenses.map(l => ({ ...l, label: LABEL + ':' + l.key }))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(l => [l.label, PIN(l.model) + '/' + EFF(l.effort) + ' guards:survey@' + tierOf(l.model) + ' tier:' + tierOfLens(l)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)
const RECORDED = Object.fromEntries(resolved.map(l => [l.label, leanRecord(profileOf(l), l.agentType, swapOf(l))]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

const READ_FAILURE = '(retry a transient read error; a missing path or a denial is not retried: return checked.stoppedAt `blocked` with the path and cause in checked.basis)'
const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST ' + READ_FAILURE + '. It ORIENTS you; it never caps what you investigate, and every fact in it is a claim to confirm first-party, not ground truth.\n\n'
  : ''

phase('Explore')
const raw = await parallel(resolved.map(l => () => {
  const body = l.promptPath
    ? 'Your full lens mandate is at: ' + l.promptPath + ' — read it with the Read tool and execute it exactly ' + READ_FAILURE + '.'
    : (l.prompt || '')
  return leanAgent(contextPre + body + spillContract(l) + guardRef(l), {
    label: l.label, phase: 'Explore', schema: CLAIMS_SCHEMA, model: PIN(l.model), effort: EFF(l.effort),
    agentType: l.agentType,
  }, swapOf(l), RECORDED).then(r => ({ key: l.key, result: r }))
}))

phase('Consolidate')
// A lens that died or returned nothing is NOT a lens that found nothing. Distinguishing them is the
// whole point of `checked`; a null return has no `checked` at all, so it is its own flag class.
const flags = []
const gaps = []
const perLens = []
const stamped = []
const isRecord = (value) => !!value && typeof value === 'object' && !Array.isArray(value)
const POLARITIES = ['exists', 'absent', 'partial', 'unclear']
const BEARINGS = ['premise-contradiction', 'reuse-candidate', 'constraint', 'blast-radius', 'context']
const isChecked = (value) => isRecord(value)
  && Array.isArray(value.toolsUsed)
  && value.toolsUsed.every(tool => typeof tool === 'string' && tool.trim())
  && ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked'].includes(value.stoppedAt)
  && typeof value.basis === 'string' && value.basis.trim()
const isClaim = (value) => isRecord(value)
  && typeof value.subject === 'string' && value.subject.trim()
  && POLARITIES.includes(value.polarity)
  && typeof value.claim === 'string' && value.claim.trim()
  && BEARINGS.includes(value.bearing)
  && (value.evidence === undefined || value.evidence === null || typeof value.evidence === 'string' || Array.isArray(value.evidence))
  && (value.file === undefined || value.file === null || typeof value.file === 'string')
  && (value.confidence === undefined || ['verified', 'unverified'].includes(value.confidence))
let nextClaimId = 1
for (let i = 0; i < resolved.length; i++) {
  const lens = resolved[i]
  const r = raw[i]
  const res = r ? r.result : null
  if (!isRecord(res)) {
    const recovery = spills(lens)
      ? 'read ' + spillPath(lens.key)
      : 'run /salvage_fanout <transcriptDir> ' + lens.key
    flags.push({ kind: 'lens-no-return', lens: lens.key, detail: 'lens returned no schema object — its dimension is UNCOVERED, not clear. Recover BEFORE re-dispatching: ' + recovery + ' (the runtime journal/transcript holds any paid-for work)' })
    perLens.push({ key: lens.key, claims: null, stoppedAt: null, basis: null, toolsUsed: null, status: 'failed', rejected: 0 })
    continue
  }
  if (!Array.isArray(res.claims) || !isChecked(res.checked)) {
    flags.push({ kind: 'lens-invalid-shape', lens: lens.key, detail: 'lens returned a malformed claims/checked object — its dimension is UNCOVERED, not a completed zero' })
    perLens.push({ key: lens.key, claims: null, stoppedAt: null, basis: null, toolsUsed: null, status: 'uncovered', rejected: 0 })
    continue
  }
  const checked = res.checked
  let lensGapCount = 0
  if (Array.isArray(res.gaps)) {
    for (const gap of res.gaps) {
      if (typeof gap === 'string' && gap.trim()) {
        gaps.push({ lens: lens.key, gap })
        lensGapCount++
      }
    }
  }
  let rejected = 0
  let valid = 0
  for (let claimIndex = 0; claimIndex < res.claims.length; claimIndex++) {
    const c = res.claims[claimIndex]
    const id = 'C' + nextClaimId++  // by place, rejected or not: a rejected entry never shifts a neighbour's id
    if (!isClaim(c)) {
      rejected++
      flags.push({ kind: 'lens-invalid-entry', lens: lens.key, detail: 'claim ' + id + ' (entry ' + claimIndex + ') fails the required claim shape — skipped; it keeps its id and valid neighbors keep theirs' })
      continue
    }
    stamped.push({ ...c, id, lens: lens.key, index: claimIndex })
    valid++
  }
  const coverageStopped = checked.stoppedAt === 'blocked'
    || (checked.toolsUsed.length === 0 && checked.stoppedAt !== 'trigger-not-met')
  const row = {
    key: lens.key,
    claims: valid,
    observed: res.claims.length,
    rejected,
    stoppedAt: checked.stoppedAt,
    basis: checked.basis,
    toolsUsed: checked.toolsUsed.length,
    gaps: lensGapCount,
    status: rejected || lensGapCount || coverageStopped ? 'partial' : 'completed',
  }
  perLens.push(row)
  if (coverageStopped && valid === 0 && checked.stoppedAt !== 'trigger-not-met') {
    row.status = 'uncovered'
  }
  if (checked.stoppedAt === 'blocked') {
    flags.push({ kind: 'lens-blocked', lens: lens.key, detail: 'the lens stopped at a blocked read after ' + valid + ' valid claim(s); its coverage is ' + row.status.toUpperCase() })
  }
  if (lensGapCount > 0) {
    flags.push({ kind: 'lens-gaps', lens: lens.key, detail: lensGapCount + ' explicit gap(s) keep this lens from completed coverage' })
  }
  // An empty result is credible only against its provenance. `trigger-not-met` is the legitimate
  // empty (the lens was dispatched but its precondition did not hold); zero claims with zero tool
  // calls and no such declaration is a lens that did not run, which must never read as "all clear".
  if (valid === 0 && rejected === 0) {
    const st = checked.stoppedAt
    const tools = checked.toolsUsed.length
    if (st === 'trigger-not-met') {
      flags.push({ kind: 'lens-trigger-not-met', lens: lens.key, detail: checked.basis })
    } else if (st === 'blocked' || tools === 0) {
      flags.push({ kind: 'lens-did-not-run', lens: lens.key, detail: 'zero claims with ' + tools + ' recorded tool calls (stoppedAt=' + st + '). Its dimension is UNCOVERED — re-dispatch or cover it inline before treating the topic as explored.' })
    } else if (lensGapCount === 0) {
      flags.push({ kind: 'lens-empty-verified', lens: lens.key, detail: 'swept ' + tools + ' targets and found nothing (stoppedAt=' + st + ')' })
    }
  }
}

// Confidence is engine-enforced, not self-reported. A lens claiming `verified` without the field that
// backs it up is the fabrication shape these two rules exist to catch
// (feedback_delegate_output_trust): presence needs quoted output, absence needs a proving command.
const nonEmpty = (s) => typeof s === 'string' && s.trim().length > 0
const processedClaimIds = new Set()
for (const c of stamped) {
  const reportedConfidence = c.confidence
  if ((c.polarity === 'exists' || c.polarity === 'partial') && !(Array.isArray(c.evidence) ? c.evidence.length > 0 : nonEmpty(c.evidence))) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-no-evidence', lens: c.lens, detail: c.subject + ' — claimed verified with no quoted evidence' }) }
    c.confidence = 'unverified'
  }
  if (c.polarity === 'absent' && !(Array.isArray(c.evidence) && c.evidence.some(item => item && typeof item.command === 'string' && item.output === ''))) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-unproven-absence', lens: c.lens, detail: c.subject + ' — absence claimed verified with no proving command' }) }
    c.confidence = 'unverified'
  }
  if (c.confidence !== 'verified' && c.confidence !== 'unverified') { c.confidence = 'unverified' }
  if (c.confidence !== reportedConfidence) processedClaimIds.add(c.id)
}

// Group on SUBJECT ONLY — never on `file`. review_fanout.js dedups review findings by `file:line`
// because a finding IS a location; a claim is not. `file` is merely where the evidence lives, and
// several unrelated claims legitimately share one file. Measured on the maiden run (2026-08-05):
// keying on `file` produced TWO false contradictions — three unrelated claims about plan_drive.md, and
// "the ExitPlanMode hook exists" versus "no write-lock exists" in settings.json — because a shared
// location was read as a shared subject. The ACTION also differs from review_fanout: two lenses
// reaching the same fact independently is corroboration worth keeping, not a duplicate to drop.
const normKey = (s) => {
  let k = s.trim().toLowerCase().replace(/\s+/g, ' ')
  // A path-like subject is the same thing whether a lens wrote the bare filename or a repo-relative
  // path — the same maiden run MISSED a genuine contradiction because one lens said
  // `feedback_x.md` and another said `auto-memory/feedback_x.md`. Collapse to the basename. A
  // basename collision between two genuinely different files surfaces as a contradiction to
  // adjudicate, which is the safe direction to fail in.
  if (k.includes('/') && /\.[a-z0-9]{1,5}$/.test(k)) { k = k.slice(k.lastIndexOf('/') + 1) }
  return k
}
const keyOf = (c) => normKey(c.subject)
const groups = new Map()
for (const c of stamped) {
  const k = keyOf(c)
  if (!groups.has(k)) { groups.set(k, []) }
  groups.get(k).push(c)
}

const contradictions = []
let claims = []
for (const [k, group] of groups) {
  const lensesInGroup = [...new Set(group.map(c => c.lens))]
  const polarities = [...new Set(group.map(c => c.polarity))]
  // Disagreement counts only ACROSS lenses. One lens making several claims about a subject is making
  // several points — reading that as self-contradiction was a maiden-run false positive.
  const contested = lensesInGroup.length > 1 && polarities.length > 1
  if (contested) {
    // Lenses disagree about the state of the same thing. One of them is wrong, and which one is
    // not the engine's call — but an unflagged contradiction silently resolves to whichever claim
    // sorted first, which is the misinformation this engine exists to surface.
    contradictions.push({
      key: k,
      subject: group[0].subject,
      positions: group.map(c => ({ lens: c.lens, polarity: c.polarity, claim: c.claim, confidence: c.confidence, evidence: c.evidence || null, journalSelector: c.id })),
    })
  }
  // Corroboration ANNOTATES; it never merges. An earlier version kept the "best-evidenced" claim per
  // subject and dropped the rest — which silently discarded three distinct claims one lens had made
  // about a single file (measured, maiden run). Ranking among claims is triage, not a correctness
  // filter, so nothing is dropped here, for the same reason review_fanout.js never caps `findings`
  // (feedback_exhaust_review_findings_before_locking).
  for (const c of group) {
    const agreeing = lensesInGroup.filter(l => l !== c.lens && group.some(o => o.lens === l && o.polarity === c.polarity))
    const extra = {}
    if (contested) { extra.contested = true }
    if (agreeing.length > 0) { extra.corroboratedBy = agreeing }
    if (contested || agreeing.length > 0) processedClaimIds.add(c.id)
    claims.push({ ...c, ...extra })
  }
}

// Rank by how the claim bears on the plan about to be written: a contradicted premise changes the
// topic, a reuse candidate changes the design, a constraint changes the steps, context changes nothing.
const BEARING = { 'premise-contradiction': 0, 'reuse-candidate': 1, constraint: 2, 'blast-radius': 3, context: 4 }
claims.sort((a, b) => {
  const ca = a.contested ? 0 : 1, cb = b.contested ? 0 : 1
  if (ca !== cb) { return ca - cb }
  const ba = BEARING[a.bearing] ?? 9, bb = BEARING[b.bearing] ?? 9
  if (ba !== bb) { return ba - bb }
  const va = a.confidence === 'verified' ? 0 : 1, vb = b.confidence === 'verified' ? 0 : 1
  return va - vb
})

const closed = await closeCheck('claims', { items: claims, keyOf: c => c.lens + '#' + c.index, out: (SPILL_DIR || '.claude/scratch') + '/post_check_claims.json',
  seats: resolved.map(l => Object.assign({}, l, { tier: tierOfLens(l) })) })
claims = closed.items
const verification = closed.verification

const counts = {
  lenses: lenses.length,
  claims: claims.length,
  verified: claims.filter(c => c.confidence === 'verified').length,
  contested: claims.filter(c => c.contested).length,
  corroborated: claims.filter(c => c.corroboratedBy).length,
  premiseContradictions: claims.filter(c => c.bearing === 'premise-contradiction').length,
  reuseCandidates: claims.filter(c => c.bearing === 'reuse-candidate').length,
  contradictions: contradictions.length,
  gaps: gaps.length,
  rejected: perLens.reduce((total, lens) => total + (lens.rejected || 0), 0),
  uncoveredLenses: perLens.filter(lens => lens.status === 'failed' || lens.status === 'uncovered').length,
}
log('explore-fanout: ' + counts.lenses + ' lenses → ' + counts.claims + ' claims ('
  + counts.verified + ' verified, ' + counts.premiseContradictions + ' premise-contradiction, '
  + counts.reuseCandidates + ' reuse-candidate), ' + counts.contradictions + ' contradictions, '
  + counts.gaps + ' gaps, ' + counts.uncoveredLenses + ' UNCOVERED lenses')
// Never silent: an uncovered dimension is the one failure that makes the whole dossier misleading.
if (counts.uncoveredLenses > 0) {
  log('WARNING uncovered: ' + perLens.filter(lens => lens.status === 'failed' || lens.status === 'uncovered').map(lens => lens.key).join(', '))
}

const coverageLenses = perLens.map(lens => ({
  key: lens.key,
  claims: lens.claims,
  status: lens.status,
  rejected: lens.rejected || 0,
}))
const coverage = {
  requested: resolved.length,
  completed: coverageLenses.filter(lens => lens.status === 'completed').length,
  partial: coverageLenses.filter(lens => lens.status === 'partial').length,
  failed: coverageLenses.filter(lens => lens.status === 'failed').length,
  uncovered: coverageLenses.filter(lens => lens.status === 'uncovered').length,
  lenses: coverageLenses,
}
const unavailable = coverage.failed + coverage.uncovered
const status = unavailable === coverage.requested
  ? 'failed'
  : (unavailable > 0 ? 'uncovered' : (coverage.partial > 0 ? 'partial' : 'completed'))
if (RESULT_MODE === 'bounded') {
  // The byte cap holds so one orchestrator read carries every count, flag and uncovered lens; tools/workflow_replay.mjs recovers
  // the full result locally from the run record and journal (session_digest.py --workflow-full), with no model call.
  // The string clip, collection widths, depth and count of fully detailed claims and contradictions serve that cap only: keep enough of
  // each collection to orient the reader, show contested and corroborated claims inline first, and shed the rest to the journal by
  // journalSelector in the shed order below, never dropping a count, a contradiction or an uncovered lens. review_fanout.js holds a copy because a Workflow script cannot import.
  const MAX_RESULT_BYTES = 8192
  const clipped = new Set()
  const clipText = (value, collection, max = 160) => {
    if (typeof value !== 'string') return value
    const chars = [...value]
    if (chars.length <= max) return value
    clipped.add(collection)
    return chars.slice(0, max).join('') + ' …[+' + (chars.length - max) + ' chars]'
  }
  const boundValue = (value, collection, depth = 0) => {
    if (typeof value === 'string') return clipText(value, collection)
    if (value === null || typeof value !== 'object') return value
    if (depth >= 4) { clipped.add(collection); return null }
    if (Array.isArray(value)) {
      if (value.length > 8) clipped.add(collection)
      return value.slice(0, 8).map(item => boundValue(item, collection, depth + 1))
    }
    const entries = Object.entries(value)
    if (entries.length > 24) clipped.add(collection)
    const out = {}
    for (const [rawKey, member] of entries.slice(0, 24)) {
      const key = clipText(rawKey, collection, 80)
      if (Object.prototype.hasOwnProperty.call(out, key)) { clipped.add(collection); continue }
      out[key] = boundValue(member, collection, depth + 1)
    }
    return out
  }
  const claimPreview = (claim, detailed) => {
    if (!detailed) {
      clipped.add('claims')
      const out = {
        id: claim.id,
        lens: claim.lens,
        index: claim.index,
        verdict: claim.verdict,
        confidence: claim.confidence,
        journalSelector: claim.id,
      }
      if (claim.contested) out.contested = true
      if (Array.isArray(claim.corroboratedBy)) out.corroboratedBy = [...claim.corroboratedBy]
      return out
    }
    const out = boundValue(claim, 'claims')
    out.journalSelector = claim.id
    return out
  }
  const contradictionPreview = (row, detailed) => {
    if (detailed) return boundValue(row, 'contradictions')
    clipped.add('contradictions')
    return {
      key: clipText(row.key, 'contradictions'),
      subject: clipText(row.subject, 'contradictions'),
      positions: row.positions.map(position => ({
        lens: position.lens,
        polarity: position.polarity,
        confidence: position.confidence,
        journalSelector: position.journalSelector,
      })),
    }
  }
  // Same rule as review_fanout.js's findings/flags: an engine flag has no journal copy (see
  // `archive.doesNotContain` below), so it stays represented — compact (kind, lens, a short detail),
  // never dropped outright — rather than either fully inline (the earlier bug: unbounded, never shed)
  // or absent. Every flag object is exactly {kind, lens?, detail}, so this preview loses nothing
  // structurally — only clipText's own truncation of a long `detail` marks the collection clipped.
  const flagPreview = (row) => ({ kind: row.kind, lens: row.lens ?? null, detail: clipText(row.detail || '', 'flags', 120) })
  const state = {
    nonVerified: boundedVerification(verification).nonVerified,
    claims: claims.filter((claim, index) => index < 4 || processedClaimIds.has(claim.id))
      .map((claim, index) => claimPreview(claim, index < 4)),
    contradictions: contradictions.map((row, index) => contradictionPreview(row, index < 3)),
    flags: flags.map(flagPreview),
    gaps: gaps.slice(0, 6).map(row => boundValue(row, 'gaps')),
    perLens: perLens.slice(0, 10).map(row => boundValue(row, 'perLens')),
  }
  const totals = {
    nonVerified: state.nonVerified.length,
    claims: claims.length,
    contradictions: contradictions.length,
    flags: flags.length,
    gaps: gaps.length,
    perLens: perLens.length,
  }
  if (SPILL_DIR) {
    state.spillMetadata = []
    totals.spillMetadata = 1 + resolved.filter(spills).length + resolved.filter(lens => !spills(lens)).length
  }
  const fullCounts = Object.assign({}, counts, {
    flags: flags.length,
    perLens: perLens.length,
  })
  if (SPILL_DIR) fullCounts.spillMetadata = totals.spillMetadata
  const commandBase = 'python3 .claude/tools/session_digest.py --workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind explore'
  const expectedHash = '--expect-journal-sha256 <sha256-from-manifest>'
  const renderBounded = () => {
    const preview = {}
    for (const name of Object.keys(totals)) {
      const shown = state[name].length
      const omitted = totals[name] - shown
      preview[name] = {
        total: totals[name], shown, omitted, clipped: clipped.has(name),
        complete: omitted === 0 && !clipped.has(name),
      }
    }
    const output = {
      claims: state.claims,
      contradictions: state.contradictions,
      flags: state.flags,
      gaps: state.gaps,
      counts,
      perLens: state.perLens,
      verification: { ...boundedVerification(verification), nonVerified: state.nonVerified },
    }
    output.delivery = {
      contract: 'native-fanout-bounded/v1',
      engine: 'explore-fanout',
      resultMode: 'bounded',
      status,
      maxResultBytes: MAX_RESULT_BYTES,
      byteLimitExceeded: false,
      payloadComplete: Object.values(preview).every(row => row.complete),
      processedState: {
        nonVerifiedInline: state.nonVerified.length === totals.nonVerified,
        changedClaimsInline: true,
        contradictionsInline: true,
        engineFlagsInline: true,
      },
      counts: fullCounts,
      coverage: {
        status,
        requested: coverage.requested,
        completed: coverage.completed,
        partial: coverage.partial,
        failed: coverage.failed,
        uncovered: coverage.uncovered,
        rowsPreviewed: state.perLens.length,
        rowsOmitted: perLens.length - state.perLens.length,
      },
      preview,
      archive: {
        source: 'workflow-journal',
        delivered: false,
        relativePath: 'journal.jsonl',
        validation: 'required',
        transcriptDirRequired: true,
        selectors: 'C<number>',
        sourceOrder: 'started-agent order, then item-array order',
        contains: 'raw lens claims, checked provenance, and gaps',
        doesNotContain: 'engine confidence changes, contested/corroborated tags, contradictions, or engine flags; those remain inline',
        commands: {
          manifest: commandBase + ' --workflow-manifest',
          select: commandBase + ' --workflow-select <ID> ' + expectedHash,
          page: commandBase + ' --workflow-page items|lenses|reports|gaps --page <N> --page-size <N> ' + expectedHash,
          full: commandBase + ' --workflow-full ' + expectedHash,
        },
      },
    }
    return output
  }
  const utf8Bytes = (value) => {
    let bytes = 0
    for (const ch of JSON.stringify(value)) {
      const point = ch.codePointAt(0)
      bytes += point <= 0x7f ? 1 : (point <= 0x7ff ? 2 : (point <= 0xffff ? 3 : 4))
    }
    return bytes
  }
  // Least-protected first: `gaps` are raw per-lens prose, journal-recoverable (see `archive.contains`
  // above). `flags`, `contradictions` and `claims` are compact by now (see above) but each names a
  // distinct signal the reader needs, so once `gaps` is gone the three tails shed IN TURN (one item off
  // whichever still has any, alternating) rather than draining one to zero while the others stay full.
  // `perLens` (per-lens coverage status) sheds last, ahead only of `counts`, which is never shed.
  const shedOrder = ['gaps']
  const tailShed = ['flags', 'contradictions', 'claims']
  let tailTurn = 0
  const pickShed = () => {
    for (const name of shedOrder) { if (state[name].length > 0) return name }
    for (let i = 0; i < tailShed.length; i++) {
      const name = tailShed[(tailTurn + i) % tailShed.length]
      if (state[name].length > 0) { tailTurn = (tailTurn + i + 1) % tailShed.length; return name }
    }
    if (state.perLens.length > 0) return 'perLens'
    if (state.nonVerified.length > 0) return 'nonVerified'
    return null
  }
  let output = renderBounded()
  while (utf8Bytes(output) > MAX_RESULT_BYTES) {
    const name = pickShed()
    if (!name) break
    state[name].pop()
    output = renderBounded()
  }
  if (utf8Bytes(output) > MAX_RESULT_BYTES) output.delivery.byteLimitExceeded = true
  return output
}

const output = { claims, contradictions, flags, gaps, counts, perLens, spillDir: SPILL_DIR, verification }
if (SPILL_DIR) {
  output.spills = Object.fromEntries(resolved.filter(spills).map(lens => [lens.key, spillPath(lens.key)]))
  const inlineLabels = resolved.filter(lens => !spills(lens)).map(lens => lens.key)
  if (inlineLabels.length) { output.inlineLabels = inlineLabels }
}
return output
