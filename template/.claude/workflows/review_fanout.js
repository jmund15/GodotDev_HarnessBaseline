export const meta = {
  name: 'review-fanout',
  description: 'Generic read-only review fan-out engine: dispatch N caller-supplied review/audit agents in parallel over a shared CONTEXT, then Step-1 consolidation (lossless sources plus a sorted view) per the Orchestrator Action Protocol. Returns merged findings; Step 1.5 verification + the user-gated action walkthrough stay with the calling command.',
  phases: [
    { title: 'Review', detail: 'dispatch each supplied agent prompt in parallel (single-flight guard appended)' },
    { title: 'Consolidate', detail: 'ingest every valid finding as an engine-owned source (F-id), then sort the view by critical→tier→category' },
    { title: 'Merge', detail: 'optional one-agent semantic consolidation (args.consolidate; default on at >=12 sourced findings) — merges same-defect findings by meaning over the intact sources, never filters' },
  ],
}

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
const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
if (!['bounded', 'full'].includes(RESULT_MODE)) {
  return { error: 'review-fanout: resultMode must be `bounded` or `full`.' }
}

// Endpoint vocabulary — hooks/workflow_provider_guard.py injects __transport off-Anthropic.
// Presence means provider mode: incomplete registry data fails before dispatch rather than reopening
// the Anthropic vocabulary.
const HAS_TRANSPORT = Object.prototype.hasOwnProperty.call(A, '__transport')
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
const transportValid = !HAS_TRANSPORT || (
  TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && validStringList(TRANSPORT.efforts)
  && typeof TRANSPORT.default === 'string' && TRANSPORT.default.trim()
  && TRANSPORT.ids.includes(TRANSPORT.default)
)
if (!transportValid) {
  return { error: 'review-fanout: args.__transport needs non-empty string arrays `ids` and `efforts`, plus a non-empty `default` contained in `ids`.' }
}
const TRANSPORT_IDS = HAS_TRANSPORT ? TRANSPORT.ids : []
const TRANSPORT_EFFORTS = HAS_TRANSPORT ? TRANSPORT.efforts : []
const PIN = (m) => m
const EFF = (e) => e
const contextPrefix = A.contextPrefix || '' // optional shared CONTEXT prepended to every agent prompt
if (agents.length === 0) {
  return { error: 'No agents in args. The calling command (Claude) must assemble each agent prompt (from review_agents.md / session_audit_agents.md / etc.) and pass them via args.agents = [{key, prompt?, promptPath?, model?, effort?, agentType}] (+ optional args.contextPrefixPath, args.justification).' }
}
const badAgentObjects = agents.filter(a => !a || typeof a !== 'object' || Array.isArray(a))
if (badAgentObjects.length) {
  return { error: 'review-fanout: every args.agents entry must be an object.' }
}

const SPILL_DIR = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const NORMAL_SPILL_DIR = SPILL_DIR ? SPILL_DIR.replace(/\\/g, '/') : null
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
if (!spillRootAllowed) {
  return { error: 'review-fanout: spillDir must be repository-relative and contained by .claude/scratch/. Received: ' + SPILL_DIR }
}
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.spill.md'
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan']
const spills = (a) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(a.agentType)
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
// invariants (it is read-only by construction, it owns FINDINGS_SCHEMA, it knows the agent count) and
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
// MACHINE SAFETY + delivery mechanics, owned by this engine and shipped at EVERY tier (strict,
// terse, fable). Only DOCTRINE tiers by model — a fable lens that wedges the single-flight GdUnit4 pipe or
// writes to the tree does the same damage a sonnet lens would, and this engine is read-only by
// construction, so those bars are not the receiving model's to earn out of.
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
  CONCURRENT ? 'You are one of several agents running CONCURRENTLY: do NOT run a machine-wide single-flight tool (a test runner, build, engine or language server that allows one process per machine; your delegate rails name this project\'s) — use Grep/Read instead of a language server. Python and Node proofs under .claude/tests/ are not single-flight, so run them. If your mandate requires a single-flight run, STOP and report that it needs a serialized dispatch.' : null,
  'COVERAGE: `checked.toolsUsed` lists each read/search as `tool:target`; `checked.stoppedAt` names why you stopped; `checked.basis` says what you examined. Put every unread or blocked part of the mandate in `gaps`. Empty findings without positive checked provenance are UNCOVERED, not clean.',
  'OUTPUT: return ONLY the JSON object `{"findings": [...], "checked": {...}, "gaps": [...]}` per the schema — no prose around it.',
  spillContract(a) || null,
].filter(l => l !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3).
const guardRef = (a) => {
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
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
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
          // NO maxLength caps, never maxItems on `findings` — a capped array silently DROPS findings
          // (feedback_exhaust_review_findings_before_locking), and per-field caps were removed 2026-08-08:
          // the same caps class rejected explore lenses 5x each with complete deliverables on disk
          // (feedback_schema_caps_must_not_invalidate_delegate_work). The stated budget in each
          // description below is the soft target; the schema rejects nothing for length.
          description: { type: 'string', description: 'the defect in 2-3 sentences (~400 chars); no restatement of the rationale' },
          old: { type: ['string', 'null'], description: 'minimal excerpt to locate/replace — the smallest span that makes the edit unambiguous, never the whole method or file' },
          new: { type: ['string', 'null'], description: 'replacement for `old` at the same granularity' },
          question: { type: ['string', 'null'] },
          options: { type: ['array', 'null'], items: { type: 'string' } },
          scope: { type: ['array', 'null'], items: { type: 'string' } },
          rationale: { type: 'string', description: 'why it is wrong and what breaks (~500 chars); cite the rule or invariant rather than re-deriving it' },
        },
        required: ['agent', 'action', 'category', 'description', 'rationale'],
      },
    },
  },
  required: ['findings', 'checked'],
}

// 'fable' is requestable but never a default — reserve for explicit high-fidelity dispatch.
// On a provider session the transport's ids REPLACE the Anthropic vocabulary rather than
// joining it: concat left `opus`/`sonnet` legal in the engine while the guard denied them,
// so the two homes of one rule disagreed and the engine was the permissive one.
const VALID_MODELS = HAS_TRANSPORT ? TRANSPORT_IDS : ['opus', 'sonnet', 'haiku', 'fable']
const DEFAULT_MODEL = HAS_TRANSPORT ? TRANSPORT.default : 'sonnet'
const VALID_EFFORTS = HAS_TRANSPORT ? TRANSPORT_EFFORTS : ['low', 'medium', 'high', 'xhigh']
const DEFAULT_EFFORT = VALID_EFFORTS.includes('medium') ? 'medium' : VALID_EFFORTS[0]
const hasEffort = (a) => Object.prototype.hasOwnProperty.call(a, 'effort') && a.effort !== undefined

// Omitted pins take bounded floors. Present invalid pins fail rather than silently changing the run.
const badModels = agents.filter(a => a.model !== undefined && !VALID_MODELS.includes(a.model))
if (badModels.length) {
  return { error: 'review-fanout: unrecognized model pin(s): '
    + badModels.map(a => (a.key || 'agent') + '->' + a.model).join(', ')
    + '. Legal here: ' + VALID_MODELS.join(', ')
    + (A.__transport ? ' (transport ' + A.__transport.name + ')' : ' (Anthropic session)')
    + '. Omit `model` to take the ' + DEFAULT_MODEL + ' floor deliberately.' }
}
const badEfforts = agents.filter(a => hasEffort(a) && !VALID_EFFORTS.includes(a.effort))
if (badEfforts.length) {
  return { error: 'review-fanout: unrecognized effort pin(s): '
    + badEfforts.map(a => (a.key || 'agent') + '->' + String(a.effort)).join(', ')
    + '. Legal here: ' + VALID_EFFORTS.join(', ')
    + '. Omit `effort` to take the ' + DEFAULT_EFFORT + ' floor deliberately.' }
}
const badKeys = agents.filter(a => typeof a.key !== 'string' || !a.key.trim())
if (badKeys.length) {
  return { error: 'review-fanout: every agent needs a non-empty key.' }
}
const badAgentTypes = agents.filter(a => typeof a.agentType !== 'string' || !a.agentType.trim())
if (badAgentTypes.length) {
  return { error: 'review-fanout: every agent needs a non-empty agentType.' }
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

const resolved = agents.map(a => ({
  ...a,
  label: 'review:' + a.key,
  model: a.model === undefined ? DEFAULT_MODEL : a.model,
  effort: hasEffort(a) ? a.effort : DEFAULT_EFFORT,
}))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(a => [a.label, a.model + '/' + a.effort + '/' + a.agentType + ' guards:review@' + tierOf(a.model)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)
else if (resolved.some(a => a.effort !== DEFAULT_EFFORT)) log('WARNING: non-default effort pin without args.justification — name the ambiguity it resolves')

const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST (retry once if the read fails).\n\n'
  : (contextPrefix ? (contextPrefix + '\n\n') : '')

phase('Review')
const raw = await parallel(resolved.map(a => () => {
  const opts = {
    label: a.label, phase: 'Review', schema: FINDINGS_SCHEMA,
    model: PIN(a.model), effort: EFF(a.effort), agentType: a.agentType,
  }
  const body = a.promptPath
    ? 'Your full lens mandate is at: ' + a.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails).'
    : (a.prompt || '')
  const prompt = contextPre + body + guardRef(a)
  // Preserve null (a schema rejection after retries, or a dead agent): it MUST NOT collapse into an
  // empty findings array — a dead lens reading as "clean review" is the silent-coverage-loss shape.
  return agent(prompt, opts).then(r => ({ key: a.key, result: r }))
}))

phase('Consolidate')
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
const isLocation = (value) => value === undefined || value === null
  || (typeof value === 'string' && /^.+:[1-9][0-9]*$/.test(value.trim()))
const isFinding = (f) => isRecord(f)
  && typeof f.agent === 'string' && f.agent.trim()
  && ACTIONS.includes(f.action) && CATS.includes(f.category)
  && typeof f.description === 'string' && f.description.trim()
  && typeof f.rationale === 'string' && f.rationale.trim()
  && (f.critical === undefined || typeof f.critical === 'boolean')
  && isLocation(f.file)
  && (f.old === undefined || f.old === null || typeof f.old === 'string')
  && (f.new === undefined || f.new === null || typeof f.new === 'string')
  && (f.question === undefined || f.question === null || typeof f.question === 'string')
  && (f.options === undefined || f.options === null || (Array.isArray(f.options) && f.options.every(v => typeof v === 'string')))
  && (f.scope === undefined || f.scope === null || (Array.isArray(f.scope) && f.scope.every(v => typeof v === 'string')))
const sources = []
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
    perAgent.push({ key: lens, count: null, status: 'failed' })
    continue
  }
  if (typeof result.report === 'string' && result.report.trim()) { reports[lens] = result.report }
  if (!Array.isArray(result.findings)) {
    flags.push({ kind: 'lens-invalid-shape', lens, detail: 'agent returned a schema object whose `findings` is not an array — its review axis is UNCOVERED, not clean. A malformed report is never a zero.' })
    perAgent.push({ key: lens, count: null, status: 'uncovered', rejected: 0 })
    continue
  }
  const coverageValid = isChecked(result.checked)
  const checked = coverageValid ? result.checked : null
  if (!coverageValid) {
    flags.push({ kind: 'lens-invalid-coverage', lens, detail: '`checked` needs nonblank tool:target entries, a valid stoppedAt, and a nonblank basis. Findings are preserved, but this axis cannot claim complete coverage.' })
  }
  let rejected = 0
  let valid = 0
  result.findings.forEach((f, index) => {
    if (!isFinding(f)) {
      rejected++
      flags.push({ kind: 'lens-invalid-entry', lens, detail: 'finding entry at index ' + index + ' fails required-field/enum/location shape (agent, action, category, path:line, description, rationale) — skipped; valid neighbors preserved.' })
      return
    }
    sources.push({ id: 'F' + (sources.length + 1), lens, index, finding: f })
    valid++
  })
  let gapRejected = 0
  let gapCount = 0
  if (result.gaps !== undefined && !Array.isArray(result.gaps)) {
    gapRejected++
  } else {
    for (const gap of (result.gaps || [])) {
      if (typeof gap !== 'string' || !gap.trim()) { gapRejected++; continue }
      gapCount++
      flags.push({ kind: 'lens-gaps', lens, detail: gap })
    }
  }
  if (gapRejected) {
    flags.push({ kind: 'lens-invalid-gaps', lens, detail: gapRejected + ' malformed gap entr' + (gapRejected === 1 ? 'y was' : 'ies were') + ' rejected.' })
  }
  rejected += gapRejected
  let rowStatus = rejected || gapCount ? 'partial' : 'completed'
  if (!coverageValid) {
    rowStatus = valid > 0 ? 'partial' : 'uncovered'
  } else if (checked.stoppedAt === 'blocked' || checked.toolsUsed.length === 0) {
    rowStatus = valid > 0 ? 'partial' : 'uncovered'
    flags.push({ kind: 'lens-incomplete-coverage', lens, detail: 'stoppedAt=' + checked.stoppedAt + ' with ' + checked.toolsUsed.length + ' checked targets; this axis is not complete.' })
  }
  perAgent.push({
    key: lens, count: valid, status: rowStatus, rejected,
    stoppedAt: checked ? checked.stoppedAt : null,
    basis: checked ? checked.basis : null,
    toolsUsed: checked ? checked.toolsUsed.length : null,
    gaps: gapCount,
  })
}

// Step 1 sort (views only): critical first, then tier (FIX→ASK→PLAN), then category
// (bug→rule→improvement). Sorting reorders views; sources stay intact, complete, and in order.
const TIER = { FIX: 0, ASK: 1, PLAN: 2 }
const CAT = { bug: 0, rule: 1, improvement: 2 }
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

// Optional merge: the model judges GROUP MEMBERSHIP ONLY over the intact sources. It returns
// merged_from id lists; the engine builds every view from originals, never model-authored text.
const MERGE_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: {
          merged_from: { type: 'array', items: { type: 'string' }, description: 'engine F-ids grouped as one defect; every input id appears in exactly one entry' },
        },
        required: ['merged_from'],
      },
    },
  },
  required: ['findings'],
}

const consolidate = (typeof A.consolidate === 'boolean') ? A.consolidate : (sourced.length >= 12)
let final = sourced
if (consolidate && sourced.length > 1) {
  phase('Merge')
  const mergePrompt = [
    'Group these review findings by SAME DEFECT: same file AND same claim, regardless of line. Different defects in one file stay separate. Unsure stays alone.',
    'Return ONLY group membership: {"findings": [{"merged_from": ["F1", "F2"]}, ...]}. Every input id in exactly one group; unmerged findings get their own single-id group.',
    'The only ids are the input F-ids. No other fields, no prose.',
    '',
    'INPUT:',
    JSON.stringify(sources.map(viewOf)),
  ].join('\n')
  // Grouping is enumerable work: it never runs above the cheapest tier the caller pinned. A foreign
  // transport has one legal model set, so it takes that transport's default.
  const TIER_RANK = ['haiku', 'sonnet', 'opus', 'fable']
  const consolidationModel = HAS_TRANSPORT ? DEFAULT_MODEL
    : TIER_RANK[Math.min(...resolved.map(a => TIER_RANK.indexOf(a.model)))]
  const consolidationEffort = VALID_EFFORTS.includes('low') ? 'low' : DEFAULT_EFFORT
  log('PINS ' + JSON.stringify({ 'review:consolidate': consolidationModel + '/' + consolidationEffort + '/general-purpose' }))
  let res = null
  try {
    res = await agent(mergePrompt, {
      label: 'review:consolidate', phase: 'Merge', schema: MERGE_SCHEMA,
      model: consolidationModel, effort: consolidationEffort, agentType: 'general-purpose',
    })
  } catch (e) {
    res = null
  }
  const out = (isRecord(res) && Array.isArray(res.findings)) ? res.findings : null
  if (!out) {
    flags.push({ kind: 'consolidate-no-return', detail: 'the consolidation agent returned no schema object — every original is returned unmerged.' })
  } else {
    const sourceById = new Map(sources.map(s => [s.id, s.finding]))
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
      const strongestAction = Math.min(...members.map(member => TIER[member.action] ?? 9))
      if (f.action !== undefined && (TIER[f.action] ?? 9) !== strongestAction) {
        invalid = 'merged finding changes strongest action for ' + ids.join(', ')
        break
      }
      const strongestCategory = Math.min(...members.map(member => CAT[member.category] ?? 9))
      if (f.category !== undefined && (CAT[f.category] ?? 9) !== strongestCategory) {
        invalid = 'merged finding changes strongest category for ' + ids.join(', ')
        break
      }
    }
    if (!invalid && seen.size !== sources.length) {
      const missing = sources.filter(s => !seen.has(s.id)).map(s => s.id)
      invalid = 'merged_from omits id(s) ' + missing.join(', ')
    }
    if (invalid) {
      flags.push({ kind: 'consolidate-invalid', detail: invalid + ' — the merge was discarded and every original is returned unmerged.' })
    } else {
      // Views are built from originals only. Singleton: original plus engine id.
      // Multi-member: first-member representative, strongest classification, contributor
      // names. Edit pair only when every member shares the file/old/new tuple.
      let merges = 0
      const sameJSON = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
      final = out.map(f => {
        const ids = f.merged_from
        const members = ids.map(id => sourceById.get(id))
        const rep = members[0]
        if (members.length === 1) {
          return Object.assign({}, rep, { id: ids[0], merged_from: ids })
        }
        merges++
        const agents = [...new Set(members.map(m => m.agent).filter(v => typeof v === 'string' && v))]
        const strongestAction = members.reduce((a, b) => ((TIER[a] ?? 9) <= (TIER[b.action] ?? 9) ? a : b.action), members[0].action)
        const strongestCategory = members.reduce((a, b) => ((CAT[a] ?? 9) <= (CAT[b.category] ?? 9) ? a : b.category), members[0].category)
        const sameEdit = members.every(m => m.file === rep.file && (m.old ?? null) === (rep.old ?? null) && (m.new ?? null) === (rep.new ?? null))
        const sameQ = members.every(m => sameJSON(m.question, rep.question))
        const sameO = members.every(m => sameJSON(m.options, rep.options))
        const sameS = members.every(m => sameJSON(m.scope, rep.scope))
        return {
          agent: agents.join(', '),
          action: strongestAction,
          category: strongestCategory,
          critical: members.some(m => !!m.critical),
          file: rep.file ?? null,
          description: rep.description,
          old: sameEdit ? (rep.old ?? null) : null,
          new: sameEdit ? (rep.new ?? null) : null,
          question: sameQ ? rep.question : null,
          options: sameO ? rep.options : null,
          scope: sameS ? rep.scope : null,
          rationale: rep.rationale,
          id: 'M' + merges,
          merged_from: ids,
        }
      })
      final.sort(compareFindings)
    }
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

const coverageLenses = perAgent.map(r => ({
  key: r.key,
  count: r.count,
  status: r.status,
  rejected: r.rejected || 0,
}))
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
if (RESULT_MODE === 'bounded') {
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
  // A merged (multi-source) finding's `old`/`new` edit pair is engine-composed and lives nowhere else,
  // so it earns the detailed tier alongside the first few (already-sorted, most-decision-relevant)
  // findings; everything past that stays compact rather than disappearing.
  const mustStayInline = (finding) => Array.isArray(finding.merged_from) && finding.merged_from.length > 1
  // Critical and ASK findings are the ones a consumer must account for before any verdict, so their
  // ids ride beside `counts` as plain strings and shed last: with only the first three findings
  // detailed, a run of four criticals otherwise reads as one (observed 2026-09-15, wf_d34579eb-e37).
  // A merged id's members go in `mergedSelectors`, which stays small because it lists only merged
  // critical/ASK findings.
  const mergedSelectors = {}
  for (const f of final) {
    if ((f.critical || f.action === 'ASK') && Array.isArray(f.merged_from) && f.merged_from.length > 1) {
      mergedSelectors[f.id] = [...f.merged_from]
    }
  }
  const state = {
    findings: final.map((finding, index) => findingPreview(finding, index < 3 || mustStayInline(finding))),
    sources: sources.slice(0, 3).map(sourcePreview),
    flags: flags.map(compactFlag),
    reports: [],
    perAgent: perAgent.slice(0, 10).map(row => boundValue(row, 'perAgent')),
    askIds: final.filter(f => f.action === 'ASK').map(f => f.id),
    criticalIds: final.filter(f => !!f.critical).map(f => f.id),
  }
  const totals = {
    findings: final.length,
    sources: sources.length,
    flags: flags.length,
    reports: Object.keys(reports).length,
    perAgent: perAgent.length,
    askIds: state.askIds.length,
    criticalIds: state.criticalIds.length,
  }
  if (SPILL_DIR) {
    state.spillMetadata = []
    totals.spillMetadata = 1 + resolved.filter(spills).length + resolved.filter(a => !spills(a)).length
  }
  const fullCounts = Object.assign({}, counts, totals)
  const commandBase = 'python3 .claude/tools/session_digest.py --workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind review'
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
      findings: state.findings,
      sources: state.sources,
      counts,
      flags: state.flags,
      reports: {},
      perAgent: state.perAgent,
    }
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
        criticalIdsInline: state.criticalIds.length === totals.criticalIds,
        askIdsInline: state.askIds.length === totals.askIds,
      },
      counts: fullCounts,
      criticalFindingIds: [...state.criticalIds],
      askFindingIds: [...state.askIds],
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
      },
      preview,
      archive: {
        source: 'workflow-journal',
        delivered: false,
        relativePath: 'journal.jsonl',
        validation: 'required',
        transcriptDirRequired: true,
        selectors: 'F<number>',
        sourceOrder: 'started-agent order, then item-array order',
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
    if (!Object.values(preview).every(row => row.complete)) {
      // The journal (`archive` above) only ever held raw per-lens sources — merged findings and engine
      // flags are computed by THIS render and have no journal copy. Their full form is recoverable
      // without new model spend: resultMode never reaches an agent() prompt or option (only gates this
      // render), so a Workflow resume of the same run with resultMode:'full' replays every agent() call
      // from cache and renders the complete, unclipped state.
      output.delivery.resume = {
        contract: 'workflow-resume/v1',
        how: 'Workflow({scriptPath, resumeFromRunId: <this run\'s runId>}, {...same args, resultMode: "full"})',
        costsNewModelSpend: false,
        why: 'resultMode is read only when this result is rendered — it never reaches an agent() prompt or option — so every already-completed agent() call replays from cache.',
        contains: 'the complete processed state: every merged finding and engine flag in full, plus every source and per-lens row, none clipped or omitted.',
      }
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
  // all of one and none of the other. `perAgent` (per-lens coverage status) sheds next, then the ASK
  // and critical id lists (a few bytes each, so only a pathological run reaches them, and
  // `processedState.criticalIdsInline` / `askIdsInline` turns false for the list that shed), ahead only of `counts`, which is
  // never shed.
  const shedOrder = ['reports', 'sources']
  const tailShed = ['flags', 'findings']
  const lastShed = ['perAgent', 'askIds', 'criticalIds']
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

const output = { findings: final, sources, counts, flags, reports, perAgent }
if (SPILL_DIR) {
  const inlineLabels = resolved.filter(a => !spills(a)).map(a => a.key)
  output.spillDir = SPILL_DIR
  output.spills = Object.fromEntries(resolved.filter(spills).map(a => [a.key, spillPath(a.label)]))
  if (inlineLabels.length) output.inlineLabels = inlineLabels
}
return output
