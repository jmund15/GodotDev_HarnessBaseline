export const meta = {
  name: 'review-fanout',
  description: 'Run parallel read-only review; optional plan verify and fold',
  phases: [
    { title: 'Review', detail: 'dispatch each supplied agent prompt in parallel (single-flight guard appended); the engine then ingests every valid finding as an F-id source and sorts the view by critical→tier→category, with no agent' },
    { title: 'Merge', detail: 'when findings from different seats cite the same file one review:merge agent groups the findings on those files that make the same claim; every other finding remains a singleton' },
    { title: 'Fold', detail: 'optional (args.fold): one author job per subject (args.fold.subjects) checks quoted claims and applies the findings routed to its files per the fold brief (args.fold.templatePath, default .claude/commands/agents/review_fold.md), then returns a digest the engine shape-checks; a passing fold stays provisional until the independent close check returns its verdicts' },
  ],
}

// USAGE CONTRACT. Keep `args` flat: push the shared input once through `contextPrefix` and give each
// agent a `prompt` of its mandate only. Weakness compensated: orchestrator models emit malformed JSON for
// nested, escape-dense args (gotcha_workflow_args_generation_fidelity). Removal condition: a model or
// transport that round-trips nested args. Fallback for a large design or a JSON-parse failure on dispatch:
// write the payload to brief files and run the lenses through dispatch.js with `promptPath` per lens and
// `contextPath` for the shared input; the pins and the PINS row carry over.
// Pins: every agent carries its own model (and effort when args.__transport declares efforts). args.checker =
// {model, effort} pins the two steps this engine starts itself, both checked by the engine: the closeCheck verifier every
// run starts and the review:merge step (its groups must cover every id once). The provider guard fills it when the call
// omits it. It is validated before any agent starts. args.__transport = {name, ids, efforts?}.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'review-fanout: args must be JSON-serializable {agents: [{label, prompt, model, effort}, ...], context}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
const agents = Array.isArray(A.agents) ? A.agents : []
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

const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
if (!['bounded', 'full'].includes(RESULT_MODE)) {
  return { error: 'review-fanout: resultMode must be `bounded` or `full`.' }
}

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. `efforts` is absent when the transport declares none.
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
if (!transportValid) {
  return { error: 'review-fanout: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary (non-empty string array `ids`, optional non-empty `efforts`); without it this engine has no legal pins and refuses to guess.' }
}
const PIN = (m) => m
const EFF = (e) => e
// The Read tool's default window: a Read naming no line range returns at most this many lines from line 1.
// tools/review_result.py `READ_WINDOW` holds the same value.
const READ_WINDOW = 2000
const contextPrefix = A.contextPrefix || '' // optional shared CONTEXT prepended to every agent prompt
if (agents.length === 0) {
  return { error: 'No agents in args. The calling command (Claude) must assemble each agent prompt (from review_agents.md / session_audit_agents.md / etc.) and pass them via args.agents = [{key, mustRead, prompt?, promptPath?, model?, effort?, agentType?}] (+ optional args.contextPrefixPath, args.justification).' }
}
const badAgentObjects = agents.filter(a => !a || typeof a !== 'object' || Array.isArray(a))
if (badAgentObjects.length) {
  return { error: 'review-fanout: every args.agents entry must be an object.' }
}

const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
if (!spillRootAllowed) {
  return { error: 'review-fanout: spillDir must be repository-relative and contained by .claude/scratch/. Received: ' + SPILL_REL }
}
const SPILL_DIR = SPILL_REL && repoRootOf(A) ? repoRootOf(A) + '/' + NORMAL_SPILL_DIR : SPILL_REL
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.spill.md'

// TOOL PROFILES. hooks/workflow_provider_guard.py resolves args.__toolProfiles (keyed by the agent's
// `key`) for a job that DECLARED a shape or a delta, and args.__toolProfileDefaults (one row per
// shape) otherwise. A native child inherits the session's MCP set, so a profile is `inherited`
// unless this engine launches the job as its row's generated agent type (`agentType`, rendered by
// `tools/tool_profiles.py agents` into .claude/agents/), which is then stamped `agent-type`.
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
const profileOf = (a) => TOOLP.d[a.key] || TOOLP.d['review:' + a.key] || TOOLP.f['review'] || null
const swappedType = (a) => leanTypeOf([a.key, 'review:' + a.key], a.agentType, profileOf(a))
const effectiveType = (a) => swappedType(a) || a.agentType
// lane-review returns its deliverable inline only: salvage reads the transcript's final payload.
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan', 'lane-review', 'lane-survey']
const NO_SHELL_AGENT_TYPES = ['lane-review', 'lane-survey']
const spills = (a) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(effectiveType(a))
const readOnlyContract = (a) => spills(a)
  ? 'Read-only: do NOT modify, create, or delete any file EXCEPT your own spill file ' + spillPath(a.label) + '.'
  : 'Read-only: do NOT modify, create, or delete any file.'
const spillContract = (a) => spills(a) ? [
  'Write your FULL JSON deliverable to ' + spillPath(a.label) + ' BEFORE returning the same object through structured output. That file is yours alone.',
  'If structured output fails after the write, the caller recovers this paid-for review from that exact path.',
].join('\n') : ''

// Rails appended to EVERY fanned agent — protects all consumers by construction, in two layers.
// DOCTRINE (what a good review looks like) lives in .claude/guards/{any,review}.md, ONE home shared
// with dispatch.js and hooks/session_model_rails.py; the Workflow hook inlines it, and this engine
// holds no copy (the Read pointer is the fallback).
// MACHINE SAFETY + the output contract stay inline below, because they are this engine's own
// invariants (its seats are read-only by construction, it owns FINDINGS_SCHEMA, it knows the agent count) and
// hold at every guard tier.
// Tier is per-agent (instruction_quality §3, "tier rails by the model that RECEIVES them"), so the
// reference is built per agent below rather than as one shared constant.
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
// MACHINE SAFETY + delivery mechanics, owned by this engine and shipped at EVERY tier (detailed,
// condensed, minimal). Only DOCTRINE tiers by model — a strongest-row lens that wedges the single-flight GdUnit4 pipe or
// writes to the tree does the same damage a weakest-row lens would, and this engine's seats are
// read-only by construction, so those bars are not the receiving model's to earn out of.
const CONCURRENT = agents.length > 1
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// The read-only line below is prompt-level. Its advisory backstop is armed OUTSIDE this script by
// .claude/hooks/readonly_marker_arm.py (a Workflow script has no filesystem, require, or clock),
// which arms for any engine carrying the next line.
// READONLY-FANOUT-ENGINE
const BASE_CONTRACT = (a) => [
  '',
  '=== ENGINE CONTRACT ===',
  RELAY_LINE,
  readOnlyContract(a),
  CONCURRENT ? 'You are one of several agents running CONCURRENTLY: do NOT run a single-flight tool (a test runner, build, engine or language server that allows one process per machine or per checkout; your delegate rails name this project\'s) — use Grep/Read instead of a language server. Python and Node proofs under .claude/tests/ are not single-flight'
    + (NO_SHELL_AGENT_TYPES.includes(effectiveType(a)) ? '; you have no shell, so cite a proof by path instead of running it.' : '; run the edited unit\'s proof file, not harness_tests.py (its machine slots are shared; rules/harness_tooling.md).')
    + ' If your mandate requires a single-flight run, STOP and report that it needs a serialized dispatch.' : null,
  'COVERAGE: `checked.toolsUsed` lists each read/search as `tool:target`, and each Read as `Read:<path>:<first>-<last>`, the lines it returned; a Read entry with no range counts as lines 1-' + READ_WINDOW + ', the Read tool\'s default window. `checked.stoppedAt` names why you stopped; `checked.basis` says what you examined. Each `gaps` entry is `{"item": <the path you did not read>, "reason": "outside-lens" | "capability" | "missing"}`: another seat\'s lens owns the item, it needs a tool you lack (running code, a shell, the engine), or the path does not exist. Only such a gap excuses a MUST-READ entry; state any other limit (a check you could not run, a file outside the list) in `checked.basis`. Empty findings without positive checked provenance are UNCOVERED, not clean.',
  'MUST-READ: you are done only when your Read ranges in `checked.toolsUsed` together cover every line of each entry below, or a `gaps` item names the entry with a legal reason. Read a file past the default window in ranges. A shell read of the file counts whole; a search covers no line. An entry read only in part fails this seat whatever `checked.stoppedAt` says, and the panel does not re-run a failed seat. Entries: ' + (a.mustRead || []).map(entry => entry.path + ' (' + entry.lines + ' lines)').join(', '),
  mandateKeysOf(a).length > 0 ? 'MANDATES: `checked.mandates` maps each of ' + mandateKeysOf(a).join(', ') + ' to `findings`, `clean` or `not-reached`. Report `not-reached` for a mandate you did not read every changed file for; a missing or unknown key voids the map.' : null,
  'Evidence: ' + EVIDENCE_RULES,
  'OUTPUT: return ONLY the JSON object `{"findings": [...], "checked": {...}, "gaps": [...]}` per the schema — no prose around it.',
  spillContract(a) || null,
].filter(l => l !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3).
// Registry driverNotes per pinned model; rails key on (shape, tier) and carry no model, so notes travel in args.__driverNotes.
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
const guardRef = (x) => { const r = railsRef(x); return r && r + driverNotes(x.model) }
const railsRef = (a) => {
  const tier = tierOf(a.model)
  const inline = inlineRails(a.label || a.key, 'review', tier)
  if (inline) return BASE_CONTRACT(a) + '\n' + inline
  return BASE_CONTRACT(a) + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/review.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md and of each layer overlay beside them (.claude/guards/<name>.<layer>.md) that exists. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n')
}

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    // Lens-level structured report the brief mandates beside its findings (a per-file density
    // table, a coverage census). A schema field survives compaction and the metrics collector
    // can read it; prose beside the JSON does neither (audit S3-11, 2026-09-09).
    report: { type: ['string', 'null'], description: 'lens-level table or census the brief asked for, markdown; null when the brief asked for none' },
    checked: {
      type: 'object', additionalProperties: false,
      required: ['toolsUsed', 'stoppedAt', 'basis'],
      properties: {
        toolsUsed: { type: 'array', items: { type: 'string' } },
        stoppedAt: { type: 'string', enum: ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked'] },
        basis: { type: 'string' },
        mandates: { type: 'object', additionalProperties: true, description: 'outcome per mandate key (findings, clean or not-reached)' },
      },
    },
    gaps: {
      type: 'array',
      description: 'one entry per must-read item the seat did not read, with its closed reason',
      items: {
        type: 'object', additionalProperties: false,
        required: ['item', 'reason'],
        properties: {
          item: { type: 'string' },
          reason: { type: 'string', enum: ['outside-lens', 'capability', 'missing'] },
        },
      },
    },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: {
          agent: { type: 'string' },
          action: { type: 'string', enum: ['FIX', 'ASK', 'PLAN'] },
          category: { type: 'string', enum: ['bug', 'rule', 'improvement'] },
          critical: { type: 'boolean' },
          file: { type: ['string', 'null'], pattern: '^.+:[1-9][0-9]*$', description: 'location as "path/to/file.cs:line" — include the line number; an anchor for humans, never finding identity (the engine assigns F-ids, and two findings may share one anchor)' },
          // Schema caps and field descriptions: orchestration SKILL §9.
          description: { type: 'string', description: 'the defect, briefly enough to act on without the rationale; no restatement of the rationale' },
          old: { type: ['string', 'null'], description: 'minimal excerpt to locate/replace — the smallest span that makes the edit unambiguous, never the whole method or file' },
          new: { type: ['string', 'null'], description: 'literal replacement text for `old` at the same granularity for FIX; a fix the seat can only describe is a PLAN with `scope` and `options` (orchestrator_action_protocol §Finding Schema)' },
          question: { type: ['string', 'null'] },
          options: { type: ['array', 'null'], items: { type: 'string' } },
          scope: { type: ['array', 'null'], items: { type: 'string' } },
          refuting: { type: 'boolean', description: 'true when the finding says the plan misstates a cited source' },
          evidence: EVIDENCE_SCHEMA,
          rationale: { type: 'string', description: 'why it is wrong and what breaks, briefly; cite the rule or invariant rather than re-deriving it' },
        },
        required: ['agent', 'action', 'category', 'description', 'rationale'],
      },
    },
  },
  required: ['findings', 'checked'],
}

// Every agent pins its own model, and its own effort when the transport declares an effort vocabulary.
const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
// A seat keyed by registry lenses takes its lens's tier: args.__lensTiers, injected by
// hooks/workflow_provider_guard.py from tools/lens.py. The lens is a fact the call carries, so it
// wins over a `tier` the seat or the call writes -- the PINS row must record the tier the guard
// checked against the pin.
const LENS_TIERS = (A.__lensTiers && typeof A.__lensTiers === 'object') ? A.__lensTiers : {}
const tierOfAgent = (a) => LENS_TIERS[a.key] || a.tier || A.tier || '-'
// Catalog keys (adaptation.json review_mandates) per seat key, injected beside __lensTiers from tools/lens.py.
// The guard keys a merged seat `a+b` whole, holding the union; a map keyed per lens expands `a+b` part by part.
const LENS_MANDATES = (A.__lensMandates && typeof A.__lensMandates === 'object') ? A.__lensMandates : {}
const mandateKeysOf = (a) => Array.isArray(LENS_MANDATES[a.key]) ? [...new Set(LENS_MANDATES[a.key])]
  : [...new Set(String(a.key).split('+').flatMap(part => Array.isArray(LENS_MANDATES[part]) ? LENS_MANDATES[part] : []))]
// No pin is filled: a seat without a legal model (or without a legal effort, when the transport declares efforts) fails here.
const unpinned = agents.filter(a => !a || !VALID_MODELS.includes(a.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(a.effort)))
if (unpinned.length) {
  return { error: 'review-fanout: pin model and effort on every job (orchestration §5). Unpinned or illegal: '
    + unpinned.map(a => (a && a.key) || 'agent').join(', ')
    + '. Each agent needs ' + pinVocab + ' (transport ' + TRANSPORT.name + ').' }
}
// The closeCheck verifier starts in every run and the review:merge step when seats cite a shared file; both use args.checker.
const checkerOk = !!A.checker && typeof A.checker === 'object' && VALID_MODELS.includes(A.checker.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.checker.effort))
if (!checkerOk) {
  return { error: 'review-fanout: args.checker = {model, effort} is required because every run starts the closeCheck verifier (the provider guard fills it when omitted); args.checker must pin ' + pinVocab + '. Refusing before any agent starts.' }
}
const badKeys = agents.filter(a => typeof a.key !== 'string' || !a.key.trim())
if (badKeys.length) {
  return { error: 'review-fanout: every agent needs a non-empty key.' }
}
const badAgentTypes = agents.filter(a => a.agentType != null && (typeof a.agentType !== 'string' || !a.agentType.trim()))
if (badAgentTypes.length) {
  return { error: 'review-fanout: an agentType, when given, must be a non-empty string; omit it for the runtime default.' }
}
// Every seat carries its must-read list, its done condition: exactly {path, lines} per entry, the shape
// tools/review_result.py `valid_must_read` accepts; that module's `gap_verdict` docstring names the contract's home.
const validMustRead = (value) => Array.isArray(value) && value.length > 0 && value.every(entry => !!entry
  && typeof entry === 'object' && !Array.isArray(entry) && Object.keys(entry).length === 2 && typeof entry.path === 'string' && !!entry.path.trim()
  && Number.isInteger(entry.lines) && entry.lines >= 0)
const badMustRead = agents.filter(a => !validMustRead(a.mustRead))
if (badMustRead.length) {
  return { error: 'review-fanout: every seat needs mustRead, a non-empty array of {path, lines} entries: the subject files or diff paths its lens covers, each with its line count (`python3 .claude/tools/lens_briefs.py must-read <list file>` prints them). Missing or malformed on: ' + badMustRead.map(a => a.key).join(', ') }
}
const duplicateKeys = agents.map(a => a.key).filter((key, i, keys) => keys.indexOf(key) !== i)
if (duplicateKeys.length) {
  return { error: 'review-fanout: duplicate agent key(s): ' + [...new Set(duplicateKeys)].join(', ') }
}
if (SPILL_DIR) {
  const spillPaths = agents.filter(spills).map(a => spillPath('review:' + a.key).toLowerCase())
  const spillCollisions = spillPaths.filter((path, i, paths) => paths.indexOf(path) !== i)
  if (spillCollisions.length) {
    return { error: 'review-fanout: agent keys collide after spill-path sanitization: ' + [...new Set(spillCollisions)].join(', ') }
  }
}

// FOLD STAGE (args.fold = {subjects: [{root, snapshot, files, newPaths?}], repoRoot?, templatePath?, model, effort?}; model is required and effort when the transport declares efforts; templatePath defaults to DEFAULT_FOLD_TEMPLATE). One author
// job per subject checks evidence, applies its findings and returns the change digest. A finding routes to the one subject
// whose `files` hold its cited file; one citing no file goes to the sole subject of a one-subject fold. `snapshot` names the
// pre-fold bytes the caller took with `post_check.py snapshot` before dispatch (a Workflow script cannot run shell).
// `repoRoot` is the absolute checkout path the subject roots are relative to (workflow_provider_guard.py fills it from the session's working directory unless the caller wrote one): a Workflow script
// cannot read it, and a finding may cite a file absolutely (the findings schema allows it). A citation that is exactly
// <repoRoot>/<root>/<file> routes to that file; any other absolute citation, or any with no repoRoot, routes nowhere and is flagged.
// tools/fold_check.py route() re-derives this routing; tests/fold_routing_vectors.json holds both to one rule.
// hooks/readonly_marker_arm.py allows the fold's writes to each subject's files and newPaths.
const FOLD = (A.fold === undefined || A.fold === null) ? null : A.fold
const repoRelative = (value) => {
  const p = typeof value === 'string' ? value.trim().replace(/\\/g, '/') : ''
  return (!p || /^([A-Za-z]:)?\//.test(p) || /(^|\/)\.\.(\/|$)/.test(p)) ? '' : p
}
// Every path that reaches a prompt or a command line: repository-relative, and only these characters.
const SAFE_PATH = /^[\w./:\\-]+$/
const safePath = (value) => { const p = repoRelative(value); return (p && SAFE_PATH.test(p)) ? p : '' }
const DEFAULT_FOLD_TEMPLATE = '.claude/commands/agents/review_fold.md'
let FOLD_PIN = null
if (FOLD) {
  if (typeof FOLD !== 'object' || Array.isArray(FOLD)) {
    return { error: 'review-fanout: args.fold must be an object {subjects: [{root, snapshot, files, newPaths?}], repoRoot?, templatePath?, tier, model?, effort?}.' }
  }
  if (!SPILL_DIR) {
    return { error: 'review-fanout: args.fold needs args.spillDir: the findings, verdicts and digest are written there.' }
  }
  if (FOLD.planPath !== undefined || FOLD.planPaths !== undefined) {
    return { error: 'review-fanout: args.fold.planPath and args.fold.planPaths are retired; pass args.fold.subjects = [{root, snapshot, files, newPaths?}] (one subject per plan, snapshot from `python3 .claude/tools/post_check.py snapshot`).' }
  }
  if (!Array.isArray(FOLD.subjects) || FOLD.subjects.length === 0) {
    return { error: 'review-fanout: args.fold.subjects must be a non-empty array of {root, snapshot, files, newPaths?}.' }
  }
  const subjects = []
  for (const [i, raw] of FOLD.subjects.entries()) {
    const where = 'args.fold.subjects[' + i + ']'
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return { error: 'review-fanout: ' + where + ' must be an object {root, snapshot, files, newPaths?}.' }
    }
    const root = safePath(raw.root === undefined ? '.' : raw.root)
    const snapshot = safePath(raw.snapshot)
    if (!root) return { error: 'review-fanout: ' + where + '.root must be a repository-relative path of safe characters. Received: ' + String(raw.root) }
    if (!snapshot) return { error: 'review-fanout: ' + where + '.snapshot must be a repository-relative path to the manifest `post_check.py snapshot` wrote. Received: ' + String(raw.snapshot) }
    const listed = (field) => raw[field] === undefined && field === 'newPaths' ? [] : (Array.isArray(raw[field]) ? raw[field].map(safePath) : null)
    const files = listed('files')
    const newPaths = listed('newPaths')
    if (!files || files.length === 0 || files.some(f => !f)) {
      return { error: 'review-fanout: ' + where + '.files must be a non-empty array of repository-relative paths of safe characters. Received: ' + JSON.stringify(raw.files) }
    }
    if (!newPaths || newPaths.some(f => !f)) {
      return { error: 'review-fanout: ' + where + '.newPaths must be an array of repository-relative paths of safe characters. Received: ' + JSON.stringify(raw.newPaths) }
    }
    subjects.push({ root, snapshot, files, newPaths })
  }
  const claimed = subjects.flatMap(s => s.files.map(f => (s.root + '/' + f).toLowerCase()))
  if (new Set(claimed).size !== claimed.length) {
    return { error: 'review-fanout: args.fold.subjects repeats a file: ' + claimed.join(', ') }
  }
  const repoRoot = FOLD.repoRoot === undefined ? '' : String(FOLD.repoRoot).trim().replace(/\\/g, '/').replace(/\/+$/, '')
  if (FOLD.repoRoot !== undefined && (typeof FOLD.repoRoot !== 'string' || !/^([A-Za-z]:)?\//.test(repoRoot) || /(^|\/)\.\.(\/|$)/.test(repoRoot))) {
    return { error: 'review-fanout: args.fold.repoRoot must be the absolute path of the checkout the subject roots are relative to, without `..`. Received: ' + String(FOLD.repoRoot) }
  }
  const template = safePath(FOLD.templatePath === undefined ? DEFAULT_FOLD_TEMPLATE : FOLD.templatePath)
  if (!template) {
    return { error: 'review-fanout: args.fold.templatePath must be a repository-relative path to the fold brief; omit it for the default. Received: ' + String(FOLD.templatePath) }
  }
  if (!VALID_MODELS.includes(FOLD.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(FOLD.effort))) {
    return { error: 'review-fanout: pin model and effort on every job (orchestration §5). Unpinned or illegal: args.fold (review:fold). Each fold job needs ' + pinVocab + '.' }
  }
  const joint = subjects.length > 1
  FOLD_PIN = {
    joint,
    jobs: subjects.map((subject, i) => {
      const stem = SPILL_DIR + '/' + (joint ? 'fold' + (i + 1) + '_' : '')
      return {
        subject,
        label: joint ? 'review:fold:' + (i + 1) : 'review:fold',
        files: { findings: stem + (joint ? 'findings.json' : 'fold_findings.json'),
          verdicts: stem + (joint ? 'verdicts.json' : 'fold_verdicts.json'),
          digest: stem + (joint ? 'digest.json' : 'fold_digest.json'),
          promptPath: stem + (joint ? 'prompt.md' : 'fold_prompt.md') },
      }
    }),
    repoRoot,
    templatePath: template,
    model: FOLD.model,
    effort: FOLD.effort,
  }
}

// Ledger inputs are forwarded to the in-run verifier after consolidation.
const LEDGER_STAGES = ['candidate', 'pr-review', 'pre-merge', 'pr-ready', 'design', 'plan-check']
const LEDGER = (A.ledger === undefined || A.ledger === null) ? null : A.ledger
if (LEDGER !== null) {
  if (typeof LEDGER !== 'object' || Array.isArray(LEDGER)) {
    return { error: 'review-fanout: args.ledger must be an object {stage, subject, roster, scopeJson?, task?, noNewDecision?}.' }
  }
  if (!LEDGER_STAGES.includes(LEDGER.stage)) {
    return { error: 'review-fanout: args.ledger.stage must be one of ' + LEDGER_STAGES.join(', ') + '. Received: ' + String(LEDGER.stage) }
  }
  if (typeof LEDGER.subject !== 'string' || !/^(session|pr|plan|doc):[\w./:-]+$/.test(LEDGER.subject)) {
    return { error: 'review-fanout: args.ledger.subject must be session:<id>, pr:<n>, plan:<path> or doc:<path>. Received: ' + String(LEDGER.subject) }
  }
  const roster = safePath(LEDGER.roster)
  if (!roster) return { error: 'review-fanout: args.ledger.roster must be a repository-relative path of safe characters. Received: ' + String(LEDGER.roster) }
  const scope = LEDGER.scopeJson === undefined ? null : safePath(LEDGER.scopeJson)
  if (LEDGER.scopeJson !== undefined && !scope) {
    return { error: 'review-fanout: args.ledger.scopeJson must be a repository-relative path of safe characters. Received: ' + String(LEDGER.scopeJson) }
  }
  if (LEDGER.premiseCommits !== undefined && !safePath(LEDGER.premiseCommits)) return { error: 'review-fanout: args.ledger.premiseCommits must be a repository-relative JSON list path of safe characters.' }
  for (const field of ['head', 'blobs']) {
    if (LEDGER[field] !== undefined && !safePath(LEDGER[field])) return { error: 'review-fanout: args.ledger.' + field + ' must use repository-relative safe characters.' }
  }
  if (LEDGER.task !== undefined && (typeof LEDGER.task !== 'string' || !/^[\w.-]+$/.test(LEDGER.task))) {
    return { error: 'review-fanout: args.ledger.task must be a task id of word characters, dots and dashes. Received: ' + String(LEDGER.task) }
  }
}
const resolved = agents.map(a => ({
  ...a,
  label: 'review:' + a.key,
  askedType: a.agentType,
  swapped: swappedType(a),
}))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(a => [a.label, a.model + '/' + a.effort + '/' + (effectiveType(a) || 'general-purpose') + ' guards:review@' + tierOf(a.model) + ' tier:' + tierOfAgent(a)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Every label this run spawns, on ONE line at launch: the caller's lenses, plus the
// `review:merge` stage this script starts when multiple seats cite a shared file. Each
// entry records the type the job runs as. A job that falls back from a missing generated type is
// re-recorded in a TOOL-PROFILES-REVISED line. `log()` output is retained in
// <session>/workflows/<runId>.json.
const RECORDED = Object.fromEntries(resolved.map(a => [a.label, leanRecord(profileOf(a), a.askedType, a.swapped)]))
RECORDED['review:merge'] =leanRecord(TOOLP.f['review'] || null, 'Explore', null)
if (FOLD_PIN) {
  for (const job of FOLD_PIN.jobs) RECORDED[job.label] = leanRecord(TOOLP.f['author'] || null, 'general-purpose', null)
}
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

const READ_FAILURE = '(retry a transient read error; a missing path or a denial is not retried: return checked.stoppedAt `blocked` with the path and cause in checked.basis)'
const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST ' + READ_FAILURE + '.\n\n'
  : (contextPrefix ? (contextPrefix + '\n\n') : '')

phase('Review')
const raw = await parallel(resolved.map(a => () => {
  const opts = {
    label: a.label, phase: 'Review', schema: FINDINGS_SCHEMA,
    model: PIN(a.model), effort: EFF(a.effort), agentType: a.askedType,
  }
  const body = a.promptPath
    ? 'Your full lens mandate is at: ' + a.promptPath + ' — read it with the Read tool and execute it exactly ' + READ_FAILURE + '.'
    : (a.prompt || '')
  const prompt = contextPre + body + guardRef(a)
  // Preserve null (a schema rejection after retries, or a dead agent): it MUST NOT collapse into an
  // empty findings array — a dead lens reading as "clean review" is the silent-coverage-loss shape.
  return leanAgent(prompt, opts, a.swapped, RECORDED).then(r => ({ key: a.key, result: r }))
}))

// Lossless ingestion: every valid finding becomes an engine-owned source (F1, F2, ...) in
// lens/result order. Location is an anchor, never identity — nothing dedups by file:line.
const isRecord = (v) => !!v && typeof v === 'object' && !Array.isArray(v)
const ACTIONS = ['FIX', 'ASK', 'PLAN']
const CATS = ['bug', 'rule', 'improvement']
const STOPS = ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked']
const isChecked = (value) => isRecord(value)
  && Array.isArray(value.toolsUsed)
  && value.toolsUsed.every(tool => typeof tool === 'string' && tool.trim())
  && STOPS.includes(value.stoppedAt)
  && typeof value.basis === 'string' && value.basis.trim()
const MANDATE_OUTCOMES = ['findings', 'clean', 'not-reached']
// The seat coverage contract, judged as tools/review_result.py `gap_verdict` judges it on the sidecar
// route; that docstring names the contract's home.
const GAP_REASONS = ['outside-lens', 'capability', 'missing']
// The path a `tool:target` entry names, lower-cased: review_result.py `target_path` without its repo-root
// strip, which samePath's suffix match on a `/` boundary replaces.
const targetPath = (target) => {
  const text = String(target)
  const cut = text.indexOf(':')
  const tool = cut >= 0 ? text.slice(0, cut).trim().toLowerCase() : ''
  let name = (cut >= 0 ? text.slice(cut + 1) : text).trim().replace(/\\/g, '/').replace(/(?:\s+\([^()]*\))+\s*$/, '')
  if (tool === 'bash' || tool === 'powershell') {
    const words = name.split(/\s+/).filter(Boolean)
    const dash = words.lastIndexOf('--')
    const operands = (dash >= 0 ? words.slice(dash + 1) : words.slice(1)).filter(w => !w.startsWith('-'))
    name = operands.length ? operands[operands.length - 1] : ''
  }
  name = name.replace(/^['"]+|['"]+$/g, '').replace(/:\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/, '').replace(/\/+$/, '')
  return name.split('/').filter(seg => seg !== '.').join('/').toLowerCase()
}
const samePath = (a, b) => !!a && !!b && (a === b || a.endsWith('/' + b) || b.endsWith('/' + a))
// The [first, last] line spans of its file one `tool:target` entry read: a Read's `:<first>-<last>` suffix or comma
// list of them, else the Read window from line 1; a shell read whole, since its span is not in the entry; any
// other tool, a search included, none. review_result.py `target_spans` applies the same rule.
const targetSpans = (target) => {
  const text = String(target)
  const cut = text.indexOf(':')
  const tool = cut >= 0 ? text.slice(0, cut).trim().toLowerCase() : ''
  if (tool === 'bash' || tool === 'powershell') return [[1, Infinity]]
  if (tool !== 'read') return []
  const name = text.slice(cut + 1).trim().replace(/\\/g, '/').replace(/(?:\s+\([^()]*\))+\s*$/, '').replace(/^['"]+|['"]+$/g, '')
  const suffix = name.match(/:(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$/)
  if (!suffix) return [[1, READ_WINDOW]]
  return suffix[1].split(',').map(part => { const [first, last] = part.split('-').map(Number); return [first, last === undefined ? first : last] })
}
// True when `spans` hold at least one read and together cover lines 1 to `lines`.
const covers = (spans, lines) => {
  if (!spans.length) return false
  let following = 1
  for (const [first, last] of [...spans].sort((x, y) => x[0] - y[0] || x[1] - y[1])) {
    if (first > following) break
    following = Math.max(following, last + 1)
  }
  return following > lines
}
const spansText = (spans) => {
  const merged = []
  for (const [first, last] of [...spans].sort((x, y) => x[0] - y[0] || x[1] - y[1])) {
    if (merged.length && first <= merged[merged.length - 1][1] + 1) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], last)
    else merged.push([first, last])
  }
  return merged.map(([a, b]) => a + '-' + (b === Infinity ? 'end' : b)).join(', ') || 'none'
}
const entrySpans = (entry, reads) => {
  const path = targetPath('Read:' + entry.path)
  return reads.filter(r => samePath(path, r.path)).flatMap(r => r.spans)
}
const isGap = (gap, mustRead) => isRecord(gap) && Object.keys(gap).length === 2
  && typeof gap.item === 'string' && !!gap.item.trim() && GAP_REASONS.includes(gap.reason)
  && mustRead.some(entry => samePath(targetPath('Read:' + entry.path), targetPath('Read:' + gap.item)))
// {legal, notes, unread, unreadText}: the gaps with a listed item and a closed reason; every other gap entry, which
// excuses nothing and is a note (every seat here has a must-read list); the paths of the must-read entries whose
// lines the `reads` ({path, spans}) do not cover and no legal gap names; and each such path with the lines its seat
// did read.
const gapVerdict = (gaps, mustRead, reads) => {
  const legal = []
  const notes = []
  if (gaps !== undefined && !Array.isArray(gaps)) notes.push(gaps)
  else for (const gap of (gaps || [])) (isGap(gap, mustRead) ? legal : notes).push(gap)
  const named = legal.map(gap => targetPath('Read:' + gap.item))
  const short = mustRead.filter(entry => !covers(entrySpans(entry, reads), entry.lines)
    && !named.some(n => samePath(targetPath('Read:' + entry.path), n)))
  return { legal, notes, unread: short.map(entry => entry.path),
    unreadText: short.map(entry => entry.path + ' (read ' + spansText(entrySpans(entry, reads)) + ' of ' + entry.lines + ')') }
}
const isMandateMap = (value, keys) => isRecord(value) && keys.length > 0
  && Object.keys(value).length === keys.length
  && keys.every(key => MANDATE_OUTCOMES.includes(value[key]))
const isLocation = (value) => value === undefined || value === null
  || (typeof value === 'string' && /^.+:[1-9][0-9]*$/.test(value.trim()))
// One evidence item matches a single kind of EVIDENCE_SCHEMA; review_result.py `_evidence_item` is the twin.
const isEvidenceItem = (e) => isRecord(e) && Object.values(e).every(v => typeof v === 'string')
  && EVIDENCE_SCHEMA.items.oneOf.filter(kind => kind.required.every(key => key in e) && Object.keys(e).every(key => key in kind.properties)).length === 1
const isStringOrNull = (v) => v === undefined || v === null || typeof v === 'string'
const isStringListOrNull = (v) => v === undefined || v === null || (Array.isArray(v) && v.every(x => typeof x === 'string'))
// The first reason `f` is not a review finding, else null. review_result.py `finding_problem` is the twin and
// tests/fixtures/finding_id_vectors.json holds both to one verdict.
const findingProblem = (f) => {
  if (!isRecord(f)) return 'entry is not an object'
  for (const field of ['agent', 'description', 'rationale']) {
    if (typeof f[field] !== 'string' || !f[field].trim()) return '`' + field + '` is missing or blank'
  }
  if (!ACTIONS.includes(f.action)) return '`action` is not FIX, ASK or PLAN'
  if (!CATS.includes(f.category)) return '`category` is not bug, rule or improvement'
  if (f.critical !== undefined && typeof f.critical !== 'boolean') return '`critical` is not a boolean'
  if (!isLocation(f.file)) return '`file` is not path:line'
  for (const field of ['old', 'new', 'question']) if (!isStringOrNull(f[field])) return '`' + field + '` is not a string'
  for (const field of ['options', 'scope']) if (!isStringListOrNull(f[field])) return '`' + field + '` is not a list of strings'
  if (f.refuting !== undefined && typeof f.refuting !== 'boolean') return '`refuting` is not a boolean'
  if (f.evidence !== undefined) {
    if (!Array.isArray(f.evidence)) return '`evidence` is not a list'
    const bad = f.evidence.findIndex(e => !isEvidenceItem(e))
    if (bad >= 0) return '`evidence` item ' + bad + ' is none of the evidence kinds'
  }
  return null
}
// Final per-mandate outcome of one seat, keyed by catalog key: the ledger records these tokens as they are.
// A valid `checked.mandates` map supplies each key's token; without one, a one-key seat takes its seat
// status and any other seat reaches nothing. The seat status then bounds every token: a failed seat fails,
// an uncovered seat reaches nothing (blocked when it stopped blocked), and a partial seat covers nothing
// unless it is capability-partial: partial only because of capability gaps. Such a seat keeps its map's tokens,
// because C9's "a not-reached, INCOMPLETE, blocked or failed outcome covers nothing" reads per mandate and per
// engaged file: the seat's own map and the ledger's engagement floor decide which pairs a gap left incomplete.
const seatMandates = (lens, status, valid, checked, map, capabilityPartial) => {
  const keys = mandateKeysOf({ key: lens })
  const mapped = isMandateMap(map, keys)
  const token = key => mapped ? map[key] : (keys.length === 1 ? (valid > 0 ? 'findings' : 'clean') : 'not-reached')
  const outcome = t => status === 'failed' ? 'failed'
    : (status === 'uncovered' || t === 'not-reached') ? (checked && checked.stoppedAt === 'blocked' ? 'blocked' : 'not-reached')
    : status === 'partial' && !capabilityPartial ? 'incomplete'
    : t
  return Object.fromEntries(keys.map(key => [key, outcome(token(key))]))
}
const sources = []
let entryCount = 0
const flags = []
const reports = {}  // lens key -> lens-level report (schema `report`), passed through untouched
const perAgent = []
for (let i = 0; i < resolved.length; i++) {
  const lens = resolved[i].key
  const entry = raw[i]
  // Index-addressed on purpose: a parallel wrapper that nulls a thrown job erases its key, and the
  // lens name must survive anyway — a failed lens is UNCOVERED, never anonymous, never clean.
  const result = entry ? entry.result : null
  if (!isRecord(result)) {
    const agent = resolved[i]
    const recovery = agent && spills(agent)
      ? 'Recover the paid-for review from ' + spillPath('review:' + lens) + ' before re-dispatching.'
      : 'Recover BEFORE re-dispatching: /salvage_fanout <transcriptDir> ' + lens + '.'
    flags.push({ kind: 'lens-no-return', lens, detail: 'agent returned no schema object after retries — its review axis is UNCOVERED, not clean. ' + recovery })
    perAgent.push({ key: lens, count: null, status: 'failed', capabilityPartial: false, mandates: seatMandates(lens, 'failed', 0, null, null, false), checkedTargets: [] })
    continue
  }
  if (typeof result.report === 'string' && result.report.trim()) { reports[lens] = result.report }
  if (!Array.isArray(result.findings)) {
    flags.push({ kind: 'lens-invalid-shape', lens, detail: 'agent returned a schema object whose `findings` is not an array — its review axis is UNCOVERED, not clean. A malformed report is never a zero.' })
    perAgent.push({ key: lens, count: null, status: 'uncovered', rejected: 0, capabilityPartial: false, mandates: seatMandates(lens, 'uncovered', 0, null, null, false), checkedTargets: [] })
    continue
  }
  const coverageValid = isChecked(result.checked)
  const checked = coverageValid ? result.checked : null
  if (!coverageValid) {
    flags.push({ kind: 'lens-invalid-coverage', lens, detail: '`checked` needs nonblank tool:target entries, a valid stoppedAt, and a nonblank basis. Findings are preserved, but this axis cannot claim complete coverage.' })
  }
  let rejected = 0
  let valid = 0
  // An entry's id is its place in the run, rejected or not, so a rejected entry never shifts a neighbour's id:
  // review_result.py `ingest` and session_digest.py `build_workflow_result_archive` number the same way.
  result.findings.forEach((f, index) => {
    const id = 'F' + (++entryCount)
    const problem = findingProblem(f)
    if (problem) {
      rejected++
      flags.push({ kind: 'lens-invalid-entry', lens, detail: 'finding ' + id + ' (entry ' + index + ') rejected: ' + problem + ' — skipped; it keeps its id and valid neighbors keep theirs.' })
      return
    }
    sources.push({ id, lens, index, finding: f })
    valid++
  })
  const mustRead = resolved[i].mustRead
  const claimed = isRecord(result.checked) && Array.isArray(result.checked.toolsUsed)
    ? result.checked.toolsUsed.filter(t => typeof t === 'string' && t.trim()) : []
  const reads = claimed.map(t => ({ path: targetPath(t), spans: targetSpans(t) })).filter(r => r.path && r.spans.length)
  const verdict = gapVerdict(result.gaps, mustRead, reads)
  const gapTexts = verdict.legal.map(gap => gap.item + ' (' + gap.reason + ')')
  for (const text of gapTexts) flags.push({ kind: 'lens-gaps', lens, detail: text })
  const capability = verdict.legal.filter(gap => gap.reason === 'capability').length
  const gapNotes = verdict.notes.map(gap => isRecord(gap) && typeof gap.item === 'string'
    ? gap.item + ' (' + (gap.reason === undefined ? 'no reason' : String(gap.reason).slice(0, 80)) + ')' : String(JSON.stringify(gap)).slice(0, 200))
  for (const text of gapNotes) flags.push({ kind: 'lens-gap-notes', lens, detail: text })
  if (verdict.unread.length) {
    flags.push({ kind: 'lens-unread-must-read', lens, detail: 'must-read entries not read in full, with no legal gap: ' + verdict.unreadText.join(', ')
      + '; stoppedAt=' + (checked ? checked.stoppedAt : 'invalid') + ' does not excuse them. This seat failed: its coverage is uncovered, and the panel does not re-run it.' })
  }
  const map = result.checked && result.checked.mandates
  // A blocked stop the seat's own map names (`not-reached` keys in a valid map), with a must-read list to prove
  // engagement, stopped on those mandates only; review_result.ingest's `named_block` is the Python twin.
  const namedBlock = !!checked && checked.stoppedAt === 'blocked' && Array.isArray(mustRead)
    && isMandateMap(map, mandateKeysOf({ key: lens })) && Object.values(map).includes('not-reached')
  let rowStatus = rejected || capability ? 'partial' : 'completed'
  if (!coverageValid) {
    rowStatus = valid > 0 ? 'partial' : 'uncovered'
  } else if (checked.stoppedAt === 'blocked' || checked.toolsUsed.length === 0) {
    rowStatus = valid > 0 || (namedBlock && checked.toolsUsed.length > 0) ? 'partial' : 'uncovered'
    flags.push({ kind: 'lens-incomplete-coverage', lens, detail: 'stoppedAt=' + checked.stoppedAt + ' with ' + checked.toolsUsed.length + ' checked targets; this axis is not complete.' })
  }
  if (verdict.unread.length) rowStatus = 'failed'
  // Capability-partial (computed here, the one home of this fact): a seat that did not fail, no rejected entry, valid provenance, a valid
  // mandate map, and a shortfall that is the seat's own limit: a capability gap, or a blocked stop its map names.
  // A map's `not-reached` key is that limit, not a disqualifier.
  const capabilityPartial = rowStatus !== 'failed' && !rejected && !!checked && checked.toolsUsed.length > 0
    && (namedBlock || (checked.stoppedAt !== 'blocked' && capability > 0)) && isMandateMap(map, mandateKeysOf({ key: lens }))
  perAgent.push({
    key: lens, count: valid, status: rowStatus, rejected,
    stoppedAt: checked ? checked.stoppedAt : null,
    basis: checked ? checked.basis : null,
    toolsUsed: checked ? checked.toolsUsed.length : null,
    gaps: gapTexts.length,
    gapTexts,
    gapNotes,
    unread: verdict.unread,
    capabilityPartial,
    mandates: seatMandates(lens, rowStatus, valid, checked, map, capabilityPartial),
    checkedTargets: checked ? [...checked.toolsUsed] : [],
  })
}

// Step 1 sort (views only): critical first, then tier (FIX→ASK→PLAN), then category
// (bug→rule→improvement). Sorting reorders views; sources stay intact, complete, and in order.
const TIER = { FIX: 0, ASK: 1, PLAN: 2 }
const CAT = { bug: 0, rule: 1, improvement: 2 }
const nonblank = (v) => typeof v === 'string' && v.trim() !== ''
const isDecision = (f) => f.action === 'ASK' || (f.action === 'PLAN' && nonblank(f.question))
// A merged group keeps its most restrictive decider, ASK > open PLAN > resolved PLAN > FIX, so an owner-held ASK or an
// open PLAN survives a merge. review_result.py `_decider_rank` ranks the same way.
const deciderRank = (f) => f.action === 'ASK' ? 0 : f.action === 'PLAN' ? (nonblank(f.question) ? 1 : 2) : f.action === 'FIX' ? 3 : 9
// A merged group's deciding member carries its question, options and scope; a merged ASK carries every distinct member
// question and its ASK members' options.
const mergedDecider = (members) => {
  const decider = members.reduce((a, b) => (deciderRank(a) <= deciderRank(b) ? a : b), members[0])
  const own = { action: decider.action, question: decider.question ?? null, options: decider.options ?? null, scope: decider.scope ?? null }
  if (decider.action !== 'ASK') return own
  const question = [...new Set(members.map(m => m.question).filter(nonblank))].join(' / ') || null
  const options = [...new Set(members.filter(m => m.action === 'ASK' && Array.isArray(m.options)).flatMap(m => m.options))]
  return Object.assign(own, { question, options: options.length ? options : null })
}
const viewOf = (s) => Object.assign({}, s.finding, { id: s.id })
const compareFindings = (a, b) => {
  const ca = a.critical ? 0 : 1, cb = b.critical ? 0 : 1
  if (ca !== cb) { return ca - cb }
  const ta = TIER[a.action] ?? 9, tb = TIER[b.action] ?? 9
  if (ta !== tb) { return ta - tb }
  return (CAT[a.category] ?? 9) - (CAT[b.category] ?? 9)
}
const sourced = sources.map(viewOf)
sourced.sort(compareFindings)

// Merge only files cited by findings from multiple seats; all other findings remain singletons.
const MERGE_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: {
          merged_from: { type: 'array', items: { type: 'string' }, description: 'candidate engine F-ids grouped as one defect; every candidate id appears in exactly one entry' },
        },
        required: ['merged_from'],
      },
    },
  },
  required: ['findings'],
}
const fileSeats = new Map()
for (const source of sources) {
  const file = typeof source.finding.file === 'string' ? source.finding.file.replace(/:[1-9][0-9]*$/, '') : null
  if (!file) continue
  if (!fileSeats.has(file)) fileSeats.set(file, new Set())
  fileSeats.get(file).add(source.lens)
}
const candidateFiles = new Set([...fileSeats].filter(([, seats]) => seats.size > 1).map(([file]) => file))
const mergeCandidates = sources.filter(source => {
  const file = typeof source.finding.file === 'string' ? source.finding.file.replace(/:[1-9][0-9]*$/, '') : null
  return !!file && candidateFiles.has(file)
})
let final = sourced
if (mergeCandidates.length > 0) {
  phase('Merge')
  const mergePrompt = [
    'Group these review findings by SAME DEFECT: same file AND same claim, regardless of line. Different defects in one file stay separate. Unsure stays alone.',
    'Return ONLY group membership: {"findings": [{"merged_from": ["F1", "F2"]}, ...]}. Every input id in exactly one group; unmerged findings get their own single-id group.',
    'The only ids are the input F-ids. No other fields, no prose.',
    '',
    'CANDIDATE FINDINGS:',
    JSON.stringify(mergeCandidates.map(viewOf)),
  ].join('\n')
  const mergeModel = A.checker.model
  const mergeEffort = A.checker.effort
  log('PINS ' + JSON.stringify({ 'review:merge': mergeModel + '/' + mergeEffort + '/Explore tier:fanout' }))
  let res = null
  try {
    res = await agent(mergePrompt, {
      label: 'review:merge', phase: 'Merge', schema: MERGE_SCHEMA,
      model: mergeModel, effort: mergeEffort, agentType: 'Explore',
    })
  } catch (e) {
    res = null
  }
  const out = (isRecord(res) && Array.isArray(res.findings)) ? res.findings : null
  if (!out) {
    flags.push({ kind: 'consolidate-no-return', detail: 'the review:merge agent returned no schema object — every original is returned unmerged.' })
  } else {
    const sourceById = new Map(mergeCandidates.map(s => [s.id, s.finding]))
    const seen = new Set()
    let invalid = null
    for (const f of out) {
      if (!isRecord(f)) { invalid = 'a merge output entry is not an object'; break }
      const ids = Array.isArray(f.merged_from) ? f.merged_from : []
      if (ids.length === 0) { invalid = 'an output entry has empty merged_from'; break }
      const members = []
      for (const id of ids) {
        if (typeof id !== 'string' || !id.trim()) { invalid = 'merged_from contains an empty id'; break }
        if (!sourceById.has(id)) { invalid = 'merged_from contains unknown id ' + id; break }
        if (seen.has(id)) { invalid = 'merged_from duplicates id ' + id; break }
        seen.add(id)
        members.push(sourceById.get(id))
      }
      if (invalid) { break }
      const memberFiles = new Set(members.map(member => typeof member.file === 'string'
        ? member.file.replace(/:[1-9][0-9]*$/, '')
        : null))
      if (memberFiles.size !== 1) {
        invalid = 'merged finding groups different files for ' + ids.join(', ')
        break
      }
      // Legacy defense: a supplied classification that contradicts the strongest source
      // classification rejects the merge. Missing classification is valid (membership-only).
      const strongestCritical = members.some(member => !!member.critical)
      if (f.critical !== undefined && !!f.critical !== strongestCritical) {
        invalid = 'merged finding changes strongest critical for ' + ids.join(', ')
        break
      }
      if (f.action !== undefined && f.action !== mergedDecider(members).action) {
        invalid = 'merged finding changes the deciding action for ' + ids.join(', ')
        break
      }
      const strongestCategory = Math.min(...members.map(member => CAT[member.category] ?? 9))
      if (f.category !== undefined && (CAT[f.category] ?? 9) !== strongestCategory) {
        invalid = 'merged finding changes strongest category for ' + ids.join(', ')
        break
      }
    }
    if (!invalid && seen.size !== mergeCandidates.length) {
      const missing = mergeCandidates.filter(s => !seen.has(s.id)).map(s => s.id)
      invalid = 'merged_from omits id(s) ' + missing.join(', ')
    }
    if (invalid) {
      flags.push({ kind: 'consolidate-invalid', detail: invalid + ' — the merge was discarded and every original is returned unmerged.' })
    } else {
      // Views are built from originals only. Singleton: original plus engine id.
      // Multi-member: first-member representative, strongest classification, contributor
      // names, the deciding member's question, options and scope. Edit pair only when every
      // member shares the file/old/new tuple.
      let merges = 0
      final = out.map(f => {
        const ids = f.merged_from
        const members = ids.map(id => sourceById.get(id))
        const rep = members[0]
        if (members.length === 1) {
          return Object.assign({}, rep, { id: ids[0], merged_from: ids })
        }
        merges++
        const agents = [...new Set(members.map(m => m.agent).filter(v => typeof v === 'string' && v))]
        const decider = mergedDecider(members)
        const strongestCategory = members.reduce((a, b) => ((CAT[a] ?? 9) <= (CAT[b.category] ?? 9) ? a : b.category), members[0].category)
        const sameEdit = members.every(m => m.file === rep.file && (m.old ?? null) === (rep.old ?? null) && (m.new ?? null) === (rep.new ?? null))
        return {
          agent: agents.join(', '),
          action: decider.action,
          category: strongestCategory,
          critical: members.some(m => !!m.critical),
          file: rep.file ?? null,
          description: rep.description,
          old: sameEdit ? (rep.old ?? null) : null,
          new: sameEdit ? (rep.new ?? null) : null,
          question: decider.question,
          options: decider.options,
          scope: decider.scope,
          refuting: members.some(m => m.refuting === true),
          evidence: [...new Map(members.flatMap(m => m.evidence || []).map(e => [JSON.stringify(e), e])).values()],
          rationale: rep.rationale,
          id: 'M' + merges,
          merged_from: ids,
        }
      })
      const candidateIds = new Set(mergeCandidates.map(s => s.id))
      final.push(...sources.filter(s => !candidateIds.has(s.id)).map(s => Object.assign({}, viewOf(s), { merged_from: [s.id] })))
      final.sort(compareFindings)
    }
  }
}

// Advisory: the action tiers' checkable shape, over each seat's source finding. Never blocks.
const TIER_SHAPE = [
  ['ask-null-question', f => f.action === 'ASK' && !nonblank(f.question)],
  ['ask-recommended', f => f.action === 'ASK' && Array.isArray(f.options) && f.options.some(o => typeof o === 'string' && o.includes('(Recommended)'))],
  ['alternative-without-wins-when', f => Array.isArray(f.options) && f.options.slice(1).some(o => typeof o !== 'string' || !o.includes('wins when'))],
  ['plan-no-scope', f => f.action === 'PLAN' && !(Array.isArray(f.scope) && f.scope.some(nonblank))],
  ['fix-null-edit', f => f.action === 'FIX' && (f.old == null || f.new == null)],
]
for (const [predicate, violates] of TIER_SHAPE) {
  const ids = sources.filter(s => violates(s.finding)).map(s => s.id)
  if (ids.length) {
    flags.push({ kind: 'tier-shape', detail: predicate + ': ' + ids.length + ' finding' + (ids.length === 1 ? '' : 's') + ' (' + ids.join(', ') + ')' })
  }
}

// raw is the valid received count, before any grouping. exactDuplicates counts sources beyond
// the first per exact claim tuple (agent/caller-id provenance excluded) — descriptive only,
// nothing is discarded. rejected counts shape-invalid entries; unknown is never a clean zero.
const CLAIM_KEY = ['action', 'category', 'critical', 'file', 'description', 'old', 'new', 'question', 'options', 'scope', 'rationale']
const dupGroups = new Map()
for (const s of sources) {
  const key = JSON.stringify(CLAIM_KEY.map(k => s.finding[k] ?? null))
  dupGroups.set(key, (dupGroups.get(key) || 0) + 1)
}
let exactDuplicates = 0
for (const n of dupGroups.values()) { exactDuplicates += n - 1 }
const counts = {
  raw: sources.length,
  exactDuplicates,
  rejected: perAgent.reduce((n, r) => n + (r.rejected || 0), 0),
  merged: final.length,
  total: final.length,
  critical: final.filter(f => f.critical).length,
  fix: final.filter(f => f.action === 'FIX').length,
  ask: final.filter(f => f.action === 'ASK').length,
  plan: final.filter(f => f.action === 'PLAN').length,
}
log('review-fanout: ' + agents.length + ' agents → ' + counts.raw + ' received / ' + counts.merged + ' after merge (' + counts.critical + ' critical, ' + counts.fix + ' FIX / ' + counts.ask + ' ASK / ' + counts.plan + ' PLAN)')

const DIGEST_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    applied: { type: 'array', items: { type: 'object', additionalProperties: true, properties: { id: { type: 'string' }, change: { type: 'string', description: 'what changed and where, one line' }, source: { type: 'string', enum: ['finding', 'adapted', 'fold', 'ruling'], description: 'finding when the subject carries the fix or option the finding proposed verbatim, adapted when it carries reviewers fixes merged or reworded with no design of the fold, fold when the fold wrote its own design, ruling for a ruled id whose prompt-given ruling is not one of the finding options' }, file: { type: 'string', description: 'the subject-relative file of a fileless application over several subject files' }, noTest: { type: 'string', description: 'the reason a change to a code path needs no test' } }, required: ['id', 'change'] } },
    deferred: { type: 'array', items: { type: 'object', additionalProperties: true, properties: { id: { type: 'string' }, class: { type: 'string' }, reason: { type: 'string' } }, required: ['id', 'class', 'reason'] } },
    designDelta: { type: 'array', items: { type: 'string' }, description: 'each changed element, read from the diff against the snapshot' },
    changedPaths: { type: 'array', items: { type: 'string' }, description: 'every path created, edited, moved or deleted, relative to the subject root' },
    commits: { type: 'array', items: { type: 'object', additionalProperties: true, properties: { sha: { type: 'string' }, ids: { type: 'array', items: { type: 'string' } }, kind: { type: 'string', enum: ['test', 'fix'] } }, required: ['sha', 'ids', 'kind'] }, description: 'rev subjects only, in commit order' },
  },
  required: ['applied', 'deferred', 'designDelta', 'changedPaths'],
}
const AMEND_LINE = (promptPath) => 'Amend: at each item and before returning, if ' + promptPath + '.amend.md exists, read it; it overrides the brief. Name what you applied.'
const authorRef = (label, model, writes, promptPath) => {
  const tier = tierOf(model)
  const contract = [
    '',
    '=== ENGINE CONTRACT ===',
    RELAY_LINE,
    'Write only: ' + writes.join(', ') + '. Every other file is read-only for you.',
    AMEND_LINE(rootedPath(A, promptPath)),
    'OUTPUT: return ONLY the JSON object the schema describes — no prose around it.',
  ].join('\n')
  const inline = inlineRails(label, 'author', tier)
  const notes = driverNotes(model)
  if (inline) return contract + '\n' + inline + notes
  return contract + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/author.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md and of each layer overlay beside them (.claude/guards/<name>.<layer>.md) that exists. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n') + notes
}
const runStage = async (label, phaseName, prompt, schema, model, effort) => {
  log('PINS ' + JSON.stringify({ [label]: model + '/' + effort + '/general-purpose tier:' + ((FOLD && FOLD.tier) || A.tier || '-') }))
  try {
    return await agent(prompt, { label, phase: phaseName, schema, model, effort, agentType: 'general-purpose' })
  } catch (e) {
    return null
  }
}
const foldSubject = async (job, jobFinal) => {
  const FOLD_FILES = job.files
  const ids = jobFinal.map(f => f.id)
  const byId = new Map(jobFinal.map(f => [f.id, f]))
  const mergedIds = new Set(jobFinal.flatMap(f => (Array.isArray(f.merged_from) && f.merged_from.length > 1) ? f.merged_from : []))
  const members = sources.filter(s => mergedIds.has(s.id)).map(viewOf)
  // Fenced JSON is data: a finding that quotes a command is never read as an instruction to run it. Escaped
  // backticks keep a fence quoted inside a finding from closing the block; the parsed value is unchanged.
  const dataBlock = v => '```json\n' + JSON.stringify(v).replace(/`/g, '\\u0060') + '\n```'
  const findingsBlock = [
    'CONSOLIDATED FINDINGS (' + ids.length + ' ids: ' + ids.join(', ') + '):',
    dataBlock(jobFinal),
    members.length ? '\nMERGED MEMBERS (the sources behind each M-id):\n' + dataBlock(members) : null,
  ].filter(l => l !== null).join('\n')
  const foldPrompt = [
    'FOLD STAGE of a review. Read ' + FOLD_PIN.templatePath + ' with the Read tool and follow it (retry a transient read error; a missing path or a denial is not retried: return every finding id in `deferred` with the path and cause as its reason).',
    'Persist this full engine brief with Write to the Prompt file before any subject action. Do not write or remove its .amend.md file; it belongs to the parent.',
    'Root: ' + job.subject.root,
    'Snapshot: ' + job.subject.snapshot,
    'Files: ' + job.subject.files.join(', '),
    job.subject.newPaths.length ? 'New paths: ' + job.subject.newPaths.join(', ') : null,
    'Findings file: ' + FOLD_FILES.findings,
    'Verdicts file: ' + FOLD_FILES.verdicts,
    'Digest file: ' + FOLD_FILES.digest,
    'Prompt file: ' + FOLD_FILES.promptPath,
    'Write the CONSOLIDATED FINDINGS JSON array exactly to the Findings file. Run `' + cdRootPrefix(A) + 'python3 .claude/tools/verify_claims.py --findings ' + FOLD_FILES.findings + ' --out ' + FOLD_FILES.verdicts + '` and read the verdicts file (template steps 1 and 5).',
    'Read each cited source and decide whether it supports the finding (template step 3). A refuting finding with no evidence is deferred, never applied.',
    'Apply each supported FIX edit and each PLAN\'s options[0]. Hold exactly the cases steps 4 and 5 of the template list, and name the hold class in deferred.',
    '',
    findingsBlock,
  ].filter(l => l !== null).join('\n') + authorRef(job.label, FOLD_PIN.model, [
    ...[...job.subject.files, ...job.subject.newPaths].map(f => job.subject.root === '.' ? f : job.subject.root + '/' + f),
    FOLD_FILES.findings, FOLD_FILES.verdicts, FOLD_FILES.digest, FOLD_FILES.promptPath], FOLD_FILES.promptPath)
  const digest = await runStage(job.label, 'Fold', foldPrompt, DIGEST_SCHEMA, FOLD_PIN.model, FOLD_PIN.effort)
  const problems = []
  const listOk = (list, field) => Array.isArray(list) && list.every(e => isRecord(e) && typeof e.id === 'string' && typeof e[field] === 'string')
  if (!isRecord(digest)) {
    problems.push({ kind: 'fold-no-return', detail: 'the fold stage returned no schema object; read ' + job.subject.files.join(', ') + ' against the snapshot ' + job.subject.snapshot + ' before trusting either.' })
  } else if (!listOk(digest.applied, 'change') || !listOk(digest.deferred, 'reason') || !Array.isArray(digest.changedPaths)) {
    problems.push({ kind: 'fold-invalid-shape', detail: 'the digest lacks applied/deferred/changedPaths in schema shape.' })
  }
  problems.forEach(p => flags.push(FOLD_PIN.joint ? Object.assign(p, { subject: job.subject.files.join(', ') }) : p))
  const appliedIds = isRecord(digest) && Array.isArray(digest.applied) ? digest.applied.filter(isRecord).map(e => e.id) : []
  const appliedWithAlternative = appliedIds.map(id => byId.get(id))
    .filter(f => f && f.action === 'PLAN' && !nonblank(f.question) && Array.isArray(f.options) && f.options.length > 1)
    .map(f => ({ id: f.id, applied: f.options[0], alternative: f.options[1] }))
  return {
    status: problems.length ? 'invalid' : 'provisional',
    subject: job.subject,
    files: FOLD_FILES,
    digest: isRecord(digest) ? Object.assign({}, digest, { appliedWithAlternative }) : null,
  }
}
// The engine cannot read disk and checks only the digest's shape, so a digest is provisional: only
// tools/post_check.py computes verdicts, id coverage, old/new checks and the file diffs from the snapshot and the transcript.
let FOLD_RESULT = null
if (FOLD_PIN) {
  const routeKey = (p) => {
    const text = String(p).replace(/\\/g, '/').toLowerCase()
    const lead = (text.match(/^([a-z]:)?\//) || [''])[0]
    const out = []
    for (const seg of text.slice(lead.length).split('/')) {
      if (!seg || seg === '.') continue
      if (seg === '..' && out.length && out[out.length - 1] !== '..') out.pop()
      else if (seg !== '..' || !lead) out.push(seg)
    }
    return lead + out.join('/')
  }
  // One entry per subject file a finding names, so a finding naming two files of one subject reads as ambiguous.
  const routedTo = (f) => {
    const cited = String(f.file || '').trim().replace(/\\/g, '/').replace(/:[1-9]\d*(?:-[1-9]\d*)?$/, '')
    if (!cited) return FOLD_PIN.jobs.length === 1 ? [FOLD_PIN.jobs[0]] : []
    if (/^([a-z]:)?\//i.test(cited)) {
      const absolute = routeKey(cited)
      return FOLD_PIN.jobs.flatMap(job => job.subject.files
        .filter(p => FOLD_PIN.repoRoot && routeKey(FOLD_PIN.repoRoot + '/' + job.subject.root + '/' + p) === absolute).map(() => job))
    }
    const file = routeKey(cited)
    const exact = FOLD_PIN.jobs.flatMap(job => job.subject.files.filter(p => routeKey(p) === file).map(() => job))
    return exact.length ? exact
      : FOLD_PIN.jobs.flatMap(job => job.subject.files.filter(p => ('/' + routeKey(p)).endsWith('/' + file)).map(() => job))
  }
  const routes = final.map(f => ({ f, jobs: routedTo(f) }))
  // A relative citation no subject holds is a file the review read and the fold may not edit (a commit message, a task
  // record, a peer's file): held for the session, as fold_check.py disposes it. An absolute citation that routes
  // nowhere stays a routing failure, since only the engine can tell a wrong root from a foreign file.
  const heldOutside = r => r.jobs.length === 0 && String(r.f.file || '').trim() !== '' && !/^([a-z]:)?[\\/]/i.test(String(r.f.file).trim())
  const outOfSubject = routes.filter(heldOutside).map(r => r.f.id)
  const unrouted = routes.filter(r => r.jobs.length === 0 && !heldOutside(r)).map(r => r.f.id)
  const ambiguous = routes.filter(r => r.jobs.length > 1).map(r => r.f.id)
  if (unrouted.length || ambiguous.length) {
    const absolute = routes.some(r => r.jobs.length === 0 && /^([a-z]:)?\//i.test(String(r.f.file || '').trim().replace(/\\/g, '/')))
    const hint = absolute ? ' An absolute citation routes only when it is exactly <args.fold.repoRoot>/<subject root>/<file>' + (FOLD_PIN.repoRoot ? '.' : '; this call passed no args.fold.repoRoot.') : ''
    flags.push({ kind: 'fold-routing', detail: 'no subject file: ' + (unrouted.join(', ') || '-') + '; several: ' + (ambiguous.join(', ') || '-') + '. Those findings were not folded: every finding\'s file must be one file of exactly one subject (' + FOLD_PIN.jobs.map(j => j.subject.root + ': ' + j.subject.files.length + ' files').join(', ') + ').' + hint })
  }
  const jobFinal = (job) => routes.filter(r => r.jobs.length === 1 && r.jobs[0] === job).map(r => r.f)
  if (final.length > 0 && FOLD_PIN.jobs.some(job => jobFinal(job).length)) phase('Fold')
  const results = await parallel(FOLD_PIN.jobs.map(job => () => jobFinal(job).length
    ? foldSubject(job, jobFinal(job))
    : Promise.resolve({ status: 'skipped', reason: final.length === 0 ? 'no findings to fold' : 'no findings routed to this subject', subject: job.subject })))
  const status = (unrouted.length || ambiguous.length || results.some(p => p.status === 'invalid')) ? 'invalid'
    : results.every(p => p.status === 'skipped') ? 'skipped' : 'provisional'
  const held = outOfSubject.length ? { outOfSubject } : {}
  FOLD_RESULT = FOLD_PIN.joint
    ? Object.assign({ status, subjects: results }, final.length === 0 ? { reason: 'no findings to fold' } : {}, held)
    : Object.assign({}, results[0], { status }, held)
}
const coverageLenses = perAgent.map(r => Object.assign({
  key: r.key,
  count: r.count,
  status: r.status,
  rejected: r.rejected || 0,
}, r.unread && r.unread.length ? { unread: r.unread } : {}))
const coverage = {
  requested: resolved.length,
  completed: coverageLenses.filter(r => r.status === 'completed').length,
  partial: coverageLenses.filter(r => r.status === 'partial').length,
  failed: coverageLenses.filter(r => r.status === 'failed').length,
  uncovered: coverageLenses.filter(r => r.status === 'uncovered').length,
  lenses: coverageLenses,
}
const unavailable = coverage.failed + coverage.uncovered
const status = unavailable === coverage.requested
  ? 'failed'
  : (unavailable > 0 ? 'uncovered' : (coverage.partial > 0 ? 'partial' : 'completed'))
const memberOrder = new Map(final.flatMap(view => view.merged_from || [view.id]).map((id, index) => [id, index]))
const originalItems = sources.map(viewOf).sort((a, b) =>
  (memberOrder.get(a.id) ?? memberOrder.size) - (memberOrder.get(b.id) ?? memberOrder.size))
// The journal keeps the entries the engine rejected, so the reader's totals include them.
const closed = await closeCheck('review', { items: originalItems, keyOf: f => f.id, out: (SPILL_DIR || '.claude/scratch') + '/post_check.json', expectTotal: final.length + counts.rejected, expectSources: originalItems.length + counts.rejected, ledger: LEDGER, fold: FOLD,
  seats: resolved.map(a => Object.assign({}, a, { tier: tierOfAgent(a), mandates: a.mandates || mandateKeysOf(a) })) })
const verification = closed.verification
const displayVerdicts = Object.fromEntries(final.map(view => {
  const members = (view.merged_from || [view.id]).map(id => verification.verdicts[id])
  return [view.id, ['UNVERIFIED', 'UNCHECKED', 'VERIFIED'].find(v => members.includes(v)) || 'UNCHECKED']
}))
final = applyVerdicts(final, displayVerdicts, f => f.id)

if (RESULT_MODE === 'bounded') {
  // The byte cap bounds the rendered result so one orchestrator read holds every count, critical id and ASK id; tools/workflow_replay.mjs
  // recovers the full result locally from the run record and journal (session_digest.py --workflow-full), with no model call.
  // The other preview limits (string clip, depth, collection widths, count of fully detailed findings) serve that bound only: the loop below sheds
  // by the stated order until it holds, critical and ASK ids last, and the rest goes to the journal by journalSelectors. explore_fanout.js holds a copy because a Workflow script cannot import.
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
  // Every finding and every flag stays represented in bounded mode — never dropped outright — because
  // engine-computed state (merged views, validation flags) has no journal fallback (see `archive`
  // below): only the raw per-lens sources are journal-recoverable. What sheds first is DETAIL: a
  // compact form (ids, kind/lens, a short description) survives long after the full prose (old/new,
  // rationale, question, options, scope) is gone. `shedOrder` below only pops the compact tail once
  // every collection is already at its compact floor.
  const compactFinding = (finding, selectors) => {
    clipped.add('findings')
    const out = {
      id: finding.id,
      verdict: finding.verdict,
      confidence: finding.confidence,
      action: finding.action,
      category: finding.category,
      critical: !!finding.critical,
      file: finding.file ?? null,
      description: clipText(finding.description || '', 'findings', 120),
      journalSelectors: [...selectors],
    }
    if (Array.isArray(finding.merged_from)) out.merged_from = [...finding.merged_from]
    return out
  }
  const selectorsOf = (finding) => Array.isArray(finding.merged_from)
    ? finding.merged_from
    : (/^F\d+$/.test(finding.id || '') ? [finding.id] : [])
  const findingPreview = (finding, detailed) => {
    const selectors = selectorsOf(finding)
    if (!detailed) return compactFinding(finding, selectors)
    const out = boundValue(finding, 'findings')
    if (Array.isArray(finding.merged_from)) out.merged_from = [...finding.merged_from]
    out.journalSelectors = [...selectors]
    return out
  }
  const sourcePreview = (source) => {
    const out = boundValue(source, 'sources')
    out.journalSelector = source.id
    return out
  }
  // Every flag object is exactly {kind, lens?, detail} (see the flags.push call sites above), so this
  // preview loses nothing structurally — only clipText's own truncation of a long `detail` marks the
  // collection clipped, same rule as everywhere else in this render.
  const compactFlag = (row) => ({ kind: row.kind, lens: row.lens ?? null, detail: clipText(row.detail || '', 'flags', 120) })
  // Flag kinds the archive pages hold no copy of form a never-shed group beside `counts`: they stay outside `state`,
  // so pickShed cannot select them, and render in their original order among the flags that remain.
  const neverShedFlag = (row) => row.kind.startsWith('consolidate-') || row.kind === 'tier-shape' || row.kind === 'fold-routing'
  const flagRows = flags.map((row, index) => ({ index, row: compactFlag(row) }))
  const retainedFlags = flagRows.filter(entry => neverShedFlag(entry.row))
  // More than one source means the finding IS a group, and a group is only visible while it stays
  // inline: a merged (multi-source) finding's `old`/`new` edit pair is engine-composed and lives
  // nowhere else, so it earns the detailed tier alongside the first few (already-sorted,
  // most-decision-relevant) findings, which stay detailed while the byte bound allows; everything past that stays compact rather than disappearing.
  const mustStayInline = (finding) => Array.isArray(finding.merged_from) && finding.merged_from.length > 1
  // Critical and decision (ASK or open PLAN) findings are the ones a consumer must account for before any verdict, so their
  // ids ride beside `counts` as plain strings and shed last: with only the first three findings
  // detailed, a run of four criticals otherwise reads as one (observed 2026-09-15, wf_d34579eb-e37).
  // Only a genuinely merged critical or decision finding needs member selectors: they go in
  // `mergedSelectors`, which stays small because it lists only merged critical/decision findings.
  const mergedSelectors = {}
  for (const f of final) {
    if ((f.critical || isDecision(f)) && Array.isArray(f.merged_from) && f.merged_from.length > 1) {
      mergedSelectors[f.id] = [...f.merged_from]
    }
  }
  const state = {
    findings: final.map((finding, index) => findingPreview(finding, index < 3 || mustStayInline(finding))),
    sources: sources.slice(0, 3).map(sourcePreview),
    flags: flagRows.filter(entry => !neverShedFlag(entry.row)),
    reports: [],
    perAgent: perAgent.slice(0, 10).map(row => boundValue(row, 'perAgent')),
    nonVerified: boundedVerification(verification).nonVerified,
    decisionIds: final.filter(isDecision).map(f => f.id),
    criticalIds: final.filter(f => !!f.critical).map(f => f.id),
  }
  const totals = {
    findings: final.length,
    sources: sources.length,
    flags: flags.length,
    reports: Object.keys(reports).length,
    perAgent: perAgent.length,
    nonVerified: state.nonVerified.length,
    decisionIds: state.decisionIds.length,
    criticalIds: state.criticalIds.length,
  }
  if (SPILL_DIR) {
    state.spillMetadata = []
    totals.spillMetadata = 1 + resolved.filter(spills).length + resolved.filter(a => !spills(a)).length
  }
  const fullCounts = Object.assign({}, counts, totals)
  const commandBase = 'python3 .claude/tools/session_digest.py --workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind review'
  const expectedHash = '--expect-journal-sha256 <sha256-from-manifest>'
  // The fold digest is the session's reading surface, so it never sheds: ids stay whole, prose clips,
  // and each plan's full digest is in its files.digest.
  const boundOne = (r) => {
    const foldDigest = r.digest
    return Object.assign({
      status: r.status,
      subject: r.subject,
      files: r.files || null,
    }, r.reason ? { reason: r.reason } : {}, foldDigest ? {
      applied: (foldDigest.applied || []).map(e => ({ id: e.id, change: clipText(e.change, 'fold', 120) })),
      deferred: (foldDigest.deferred || []).map(e => ({ id: e.id, class: e.class, reason: clipText(e.reason, 'fold', 120) })),
      designDelta: (foldDigest.designDelta || []).map(d => clipText(d, 'fold', 160)),
      appliedWithAlternative: (foldDigest.appliedWithAlternative || []).map(e => ({ id: e.id, applied: clipText(e.applied, 'fold', 160), alternative: clipText(e.alternative, 'fold', 160) })),
      changedPaths: foldDigest.changedPaths || [],
      refutingIds: (foldDigest.deferred || []).filter(e => e.class === 'refutation').map(e => e.id),
    } : {})
  }
  const boundedFold = !FOLD_RESULT ? null
    : FOLD_RESULT.subjects ? Object.assign({ status: FOLD_RESULT.status, subjects: FOLD_RESULT.subjects.map(boundOne) }, FOLD_RESULT.reason ? { reason: FOLD_RESULT.reason } : {})
    : boundOne(FOLD_RESULT)
  const renderBounded = () => {
    const preview = {}
    for (const name of Object.keys(totals)) {
      const shown = state[name].length + (name === 'flags' ? retainedFlags.length : 0)
      const omitted = totals[name] - shown
      preview[name] = {
        total: totals[name], shown, omitted, clipped: clipped.has(name),
        complete: omitted === 0 && !clipped.has(name),
      }
    }
    const output = {
      findings: state.findings,
      sources: state.sources,
      counts,
      flags: [...retainedFlags, ...state.flags].sort((a, b) => a.index - b.index).map(entry => entry.row),
      reports: {},
      perAgent: state.perAgent,
    }
    if (boundedFold) output.fold = boundedFold
    output.verification = { ...boundedVerification(verification), nonVerified: state.nonVerified }
    output.delivery = {
      contract: 'native-fanout-bounded/v1',
      engine: 'review-fanout',
      resultMode: 'bounded',
      status,
      maxResultBytes: MAX_RESULT_BYTES,
      byteLimitExceeded: false,
      payloadComplete: Object.values(preview).every(row => row.complete),
      processedState: {
        mergedFindingsInline: true,
        engineFlagsInline: true,
        nonVerifiedInline: state.nonVerified.length === totals.nonVerified,
        criticalIdsInline: state.criticalIds.length === totals.criticalIds,
        decisionIdsInline: state.decisionIds.length === totals.decisionIds,
      },
      counts: fullCounts,
      criticalFindingIds: [...state.criticalIds],
      decisionFindingIds: [...state.decisionIds],
      mergedSelectors,
      coverage: {
        status,
        requested: coverage.requested,
        completed: coverage.completed,
        partial: coverage.partial,
        failed: coverage.failed,
        uncovered: coverage.uncovered,
        rowsPreviewed: state.perAgent.length,
        rowsOmitted: perAgent.length - state.perAgent.length,
        seats: perAgent.map(r => Object.assign({ key: r.key, status: r.status, stoppedAt: r.stoppedAt ?? null, mandates: r.mandates,
          checkedTargets: r.checkedTargets.length, count: r.count, gaps: r.gaps || 0, capabilityPartial: r.capabilityPartial === true },
          r.unread && r.unread.length ? { unread: r.unread } : {})),
      },
      preview,
      archive: {
        source: 'workflow-journal',
        delivered: false,
        relativePath: 'journal.jsonl',
        validation: 'required',
        transcriptDirRequired: true,
        selectors: 'F<number>',
        sourceOrder: 'read selectors.sourceOrder and selectors.idOrder from the workflow manifest',
        contains: 'raw source findings, lens coverage, reports, and gaps',
        doesNotContain: 'semantic merged views or engine validation flags; those remain inline',
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
  // Least-protected first: `reports` is already empty (journal-only). `sources` are raw prose excerpts
  // duplicated by `findings`. `findings` and `flags` are compact by now (see above), each a named kind
  // the reader needs — draining one to zero before touching the other would silently erase a whole
  // signal class, so once `reports`/`sources` are gone the two tails shed IN TURN (one item off
  // whichever still has any, alternating) so a run with many of both keeps a share of each rather than
  // all of one and none of the other. `perAgent` (per-lens coverage status) sheds next, then the decision
  // and critical id lists (a few bytes each, so only a pathological run reaches them, and
  // `processedState.criticalIdsInline` / `decisionIdsInline` turns false for the list that shed), ahead only of `counts` and the
  // never-shed flags, neither of which is in `state`.
  const shedOrder = ['reports', 'sources']
  const tailShed = ['flags', 'findings']
  const lastShed = ['perAgent', 'nonVerified', 'decisionIds', 'criticalIds']
  let tailTurn = 0
  const pickShed = () => {
    for (const name of shedOrder) { if (state[name].length > 0) return name }
    for (let i = 0; i < tailShed.length; i++) {
      const name = tailShed[(tailTurn + i) % tailShed.length]
      if (state[name].length > 0) { tailTurn = (tailTurn + i + 1) % tailShed.length; return name }
    }
    for (const name of lastShed) { if (state[name].length > 0) return name }
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

const output = { findings: final, sources, counts, flags, reports, perAgent, verification }
if (FOLD_RESULT) output.fold = FOLD_RESULT
if (SPILL_DIR) {
  const inlineLabels = resolved.filter(a => !spills(a)).map(a => a.key)
  output.spillDir = SPILL_DIR
  output.spills = Object.fromEntries(resolved.filter(spills).map(a => [a.key, spillPath(a.label)]))
  if (inlineLabels.length) output.inlineLabels = inlineLabels
}
return output
