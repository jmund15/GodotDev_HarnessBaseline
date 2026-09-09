export const meta = {
  name: 'review-fanout',
  description: 'Generic read-only review fan-out engine: dispatch N caller-supplied review/audit agents in parallel over a shared CONTEXT, then Step-1 consolidation (dedup by file:line, sort by critical→tier→category) per the Orchestrator Action Protocol. Returns merged findings; Step 1.5 verification + the user-gated action walkthrough stay with the calling command.',
  phases: [
    { title: 'Review', detail: 'dispatch each supplied agent prompt in parallel (single-flight guard appended)' },
    { title: 'Consolidate', detail: 'merge, dedup by file:line, sort by critical→tier→category' },
    { title: 'Merge', detail: 'optional one-agent semantic consolidation (args.consolidate; default on at >=12 deduped findings) — merges same-defect findings anchored to different lines, never filters' },
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

// Endpoint vocabulary — hooks/workflow_provider_guard.py injects __transport off-Anthropic:
// {name, ids}. Anthropic role names stay canonical and are ALWAYS legal; on a provider session
// that transport's own registry ids become legal too, which is what makes a sibling model
// reachable by Workflow pin with no sidecar. Absent the key nothing changes. Inlined per script
// because the Workflow sandbox has no require/import.
const TRANSPORT_IDS = (A.__transport && Array.isArray(A.__transport.ids)) ? A.__transport.ids : []
const PIN = (m) => m
const EFF = (e) => e
const contextPrefix = A.contextPrefix || '' // optional shared CONTEXT prepended to every agent prompt
if (agents.length === 0) {
  return { error: 'No agents in args. The calling command (Claude) must assemble each agent prompt (from review_agents.md / session_audit_agents.md / etc.) and pass them via args.agents = [{key, prompt?, promptPath?, model?, effort?}] (+ optional args.contextPrefixPath, args.justification).' }
}

// Rails appended to EVERY fanned agent — protects all consumers by construction, in two layers.
// DOCTRINE (what a good review looks like) lives in .claude/guards/{any,review}.md, ONE home shared
// with dispatch.js and hooks/session_model_rails.py; this engine injects a reference, never a copy.
// MACHINE SAFETY + the output contract stay inline below, because they are this engine's own
// invariants (it is read-only by construction, it owns FINDINGS_SCHEMA, it knows the agent count) and
// must hold even at the tier where the doctrine reference is suppressed.
// Tier is per-agent (instruction_quality §3, "tier rails by the model that RECEIVES them"), so the
// reference is built per agent below rather than as one shared constant.
const TIER_OF = { sonnet: 'strict', haiku: 'strict', opus: 'terse', fable: 'fable' }
// Off-Anthropic the RECEIVING model is deepseek whatever role name was pinned — strict band.
// Off-Anthropic ids carry no TIER_OF row, so a provider session reads at the strict tier.
const tierOf = (m) => A.__transport ? 'strict' : (TIER_OF[m] || 'strict')
// MACHINE SAFETY + delivery mechanics, owned by this engine and shipped at EVERY tier (strict,
// terse, fable). Only DOCTRINE tiers by model — a fable lens that wedges the single-flight GdUnit4 pipe or
// writes to the tree does the same damage a sonnet lens would, and this engine is read-only by
// construction, so those bars are not the receiving model's to earn out of.
const CONCURRENT = agents.length > 1
// The read-only line below is prompt-level. Its advisory backstop is armed OUTSIDE this script by
// .claude/hooks/readonly_marker_arm.py (a Workflow script has no filesystem, require, or clock).
const BASE_CONTRACT = [
  '',
  '=== ENGINE CONTRACT ===',
  'Read-only: do NOT modify, create, or delete any file.',
  CONCURRENT ? 'You are one of several agents running CONCURRENTLY: do NOT run tests, builds, or /regression_gate (the GdUnit4 named pipe is machine-wide single-flight), and do NOT use the csharp-ls LSP (single-flight wrapper) — use Grep/Read. If your mandate requires a test or build run, STOP and report that it needs a serialized dispatch.' : null,
  'OUTPUT: return ONLY the JSON object `{"findings": [...]}` per the schema — no prose around it.',
].filter(l => l !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3).
const guardRef = (m) => {
  const tier = tierOf(m)
  return BASE_CONTRACT + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/review.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n')
}

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: {
          agent: { type: 'string' },
          action: { type: 'string', enum: ['FIX', 'ASK', 'PLAN'] },
          category: { type: 'string', enum: ['bug', 'rule', 'improvement'] },
          critical: { type: 'boolean' },
          file: { type: ['string', 'null'], description: 'location as "path/to/file.cs:line" — include the line number; dedup keys on this full string, so the line disambiguates distinct findings in one file' },
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
  required: ['findings'],
}

// 'fable' is requestable but never a default — reserve for explicit high-fidelity dispatch.
// On a provider session the transport's ids REPLACE the Anthropic vocabulary rather than
// joining it: concat left `opus`/`sonnet` legal in the engine while the guard denied them,
// so the two homes of one rule disagreed and the engine was the permissive one.
const VALID_MODELS = TRANSPORT_IDS.length ? TRANSPORT_IDS : ['opus', 'sonnet', 'haiku', 'fable']
// Floor: a caller that omits (or mis-spells) model must NOT silently inherit the session model —
// under Fable that turns a 6-lens fan-out into 6 Fable agents. Default to sonnet; callers escalate explicitly.
// Transport-aware: on a provider session `sonnet` is unserviceable, so an omitted pin must
// fall to that transport's own default rather than to a name the endpoint will reject.
const DEFAULT_MODEL = (A.__transport && A.__transport.default) || 'sonnet'
// Effort floor (two-class rule, orchestration §5): review/judgment lenses are bounded-by-construction —
// measured: medium lenses matched high findings at ~43% cost (plan-check, sub-architectural plans ONLY:
// P-D pin comparison 2026-07-29 found opus-high lenses catching 2.5x the defects of sonnet-medium on an
// architecturally-loaded plan); judge panels differ <=3/56 items between low/medium/high with misses-only
// degradation (J-CAL 2026-07-28). Never inherit session effort. Raising a lens above medium requires
// args.justification naming the ambiguity it resolves.
const VALID_EFFORTS = ['low', 'medium', 'high', 'xhigh']
const DEFAULT_EFFORT = 'medium'

// A MISSING model still falls to DEFAULT_MODEL -- that floor exists so an omitted pin cannot
// inherit the session model. A PRESENT-but-unrecognized one is a different animal: silently
// coercing it means a fan-out pinned to a typo, or to a transport id the engine was never told
// about, runs as sonnet and reports a clean sweep. Fail loudly instead.
const badModels = agents.filter(a => a.model && !VALID_MODELS.includes(a.model))
if (badModels.length) {
  return { error: 'review-fanout: unrecognized model pin(s): '
    + badModels.map(a => (a.key || 'agent') + '->' + a.model).join(', ')
    + '. Legal here: ' + VALID_MODELS.join(', ')
    + (A.__transport ? ' (transport ' + A.__transport.name + ')' : ' (Anthropic session)')
    + '. Omit `model` to take the ' + DEFAULT_MODEL + ' floor deliberately.' }
}

const resolved = agents.map(a => ({
  ...a,
  label: 'review:' + (a.key || 'agent'),
  model: VALID_MODELS.includes(a.model) ? a.model : DEFAULT_MODEL,
  effort: VALID_EFFORTS.includes(a.effort) ? a.effort : DEFAULT_EFFORT,
}))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(a => [a.label, a.model + '/' + a.effort + ' guards:review@' + tierOf(a.model)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)
else if (resolved.some(a => a.effort !== DEFAULT_EFFORT)) log('WARNING: non-default effort pin without args.justification — name the ambiguity it resolves')

const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST (retry once if the read fails).\n\n'
  : (contextPrefix ? (contextPrefix + '\n\n') : '')

phase('Review')
const raw = await parallel(resolved.map(a => () => {
  const opts = { label: a.label, phase: 'Review', schema: FINDINGS_SCHEMA, model: PIN(a.model), effort: EFF(a.effort) }
  const body = a.promptPath
    ? 'Your full lens mandate is at: ' + a.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails).'
    : (a.prompt || '')
  const prompt = contextPre + body + guardRef(a.model)
  // Preserve null (a schema rejection after retries, or a dead agent): it MUST NOT collapse into an
  // empty findings array — a dead lens reading as "clean review" is the silent-coverage-loss shape.
  return agent(prompt, opts).then(r => ({ key: a.key, result: r }))
}))

phase('Consolidate')
const merged = []
const flags = []
for (const r of raw) {
  if (!r || !r.result || typeof r.result !== 'object') {
    flags.push({ kind: 'lens-no-return', lens: r ? r.key : '(unknown)', detail: 'agent returned no schema object after retries — its review axis is UNCOVERED, not clean. Recover BEFORE re-dispatching: /salvage_fanout <transcriptDir> ' + (r ? r.key : '<key>') + ' (this engine writes no spill file; the transcript holds the paid-for work).' })
    continue
  }
  if (Array.isArray(r.result.findings)) { merged.push(...r.result.findings) }
}

// Step 1 dedup by file:line — keep critical:true, else the one with more specific old/new
function specificity(f) { return (f.old ? 1 : 0) + (f.new ? 1 : 0) }
const byLoc = new Map()
const noLoc = []
for (const f of merged) {
  const loc = (typeof f.file === 'string' && f.file.trim()) ? f.file.trim() : null
  if (!loc) { noLoc.push(f); continue }
  const existing = byLoc.get(loc)
  if (!existing) { byLoc.set(loc, f); continue }
  // criticality is the primary key (a critical finding must never be dropped for a more-specific
  // non-critical one); specificity is only the tiebreak among equal criticality.
  const critF = !!f.critical, critE = !!existing.critical
  const better = (critF !== critE) ? critF : (specificity(f) > specificity(existing))
  if (better) { byLoc.set(loc, f) }
}
const deduped = [...byLoc.values(), ...noLoc]

// Step 1 sort: critical first, then tier (FIX→ASK→PLAN), then category (bug→rule→improvement)
const TIER = { FIX: 0, ASK: 1, PLAN: 2 }
const CAT = { bug: 0, rule: 1, improvement: 2 }
deduped.sort((a, b) => {
  const ca = a.critical ? 0 : 1, cb = b.critical ? 0 : 1
  if (ca !== cb) { return ca - cb }
  const ta = TIER[a.action] ?? 9, tb = TIER[b.action] ?? 9
  if (ta !== tb) { return ta - tb }
  return (CAT[a.category] ?? 9) - (CAT[b.category] ?? 9)
})

// Semantic consolidation — OPTIONAL, after the deterministic dedup, never instead of it.
// The dedup above keys on the exact `file:line` string, so one defect anchored to three different
// lines survives three times (observed: four such families in one /plan_check run). This stage
// merges by MEANING. It is a merge, never a filter: nothing is dropped, re-tiered down, or
// summarized away, and a return that loses an input id is discarded in favour of the deterministic
// list — a consolidation that swallows findings is worse than none.
const MERGE_ITEM = FINDINGS_SCHEMA.properties.findings.items
const MERGE_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: Object.assign({}, MERGE_ITEM.properties, {
          merged_from: { type: 'array', items: { type: 'string' }, description: 'ids of every source finding this entry represents; every input id appears in exactly one entry' },
        }),
        required: MERGE_ITEM.required.concat(['merged_from']),
      },
    },
  },
  required: ['findings'],
}

const consolidate = (typeof A.consolidate === 'boolean') ? A.consolidate : (deduped.length >= 12)
let final = deduped
if (consolidate && deduped.length > 1) {
  phase('Merge')
  const numbered = deduped.map((f, i) => Object.assign({ id: 'F' + (i + 1) }, f))
  const mergePrompt = [
    'You are consolidating the findings of a multi-lens review. Several lenses read the same material, so ONE defect often appears two or three times anchored to different lines.',
    '',
    'RULE: MERGE, NEVER FILTER.',
    '- Merge two findings only when they are the same defect: same file AND the same claim, regardless of line number. Different defects in one file stay separate.',
    '- A merged entry keeps EVERY source `agent` value (join them with ", "), every evidence quote from every source, the most specific `old`/`new` pair among the sources, and `critical: true` if ANY source was critical.',
    '- Never drop a finding, never lower its `critical`/`action`/`category`, never replace its text with a summary. A finding you are unsure about stays as its own entry.',
    '- Every input id appears in exactly one output entry\'s `merged_from`. A finding you did not merge is returned unchanged with `merged_from: ["<its own id>"]`.',
    '',
    'INPUT FINDINGS (JSON):',
    JSON.stringify(numbered),
    '',
    'OUTPUT: only the JSON object {"findings": [...]} per the schema. No prose.',
  ].join('\n')
  log('PINS ' + JSON.stringify({ 'review:consolidate': 'opus/low/general-purpose' }))
  const res = await agent(mergePrompt, {
    label: 'review:consolidate', phase: 'Merge', schema: MERGE_SCHEMA,
    // Engine-internal pin: not a caller's, so neither the widening nor the guard's scanner
    // covers it. Falls to the transport's default so consolidation does not null out on a
    // provider session after every lens has already run.
    model: (A.__transport && A.__transport.default) || 'opus', effort: 'low', agentType: 'general-purpose',
  })
  const out = (res && Array.isArray(res.findings)) ? res.findings : null
  if (!out) {
    flags.push({ kind: 'consolidate-no-return', detail: 'the consolidation agent returned no schema object — the deterministic list is returned unmerged.' })
  } else {
    const seen = new Set()
    for (const f of out) { for (const id of (f.merged_from || [])) { seen.add(id) } }
    const missing = numbered.filter(f => !seen.has(f.id)).map(f => f.id)
    if (missing.length) {
      flags.push({ kind: 'consolidate-dropped-findings', detail: 'ids absent from every merged_from: ' + missing.join(', ') + ' — the merge was discarded and the deterministic list is returned unmerged.' })
    } else {
      final = out
    }
  }
}

const counts = {
  raw: deduped.length,
  merged: final.length,
  total: final.length,
  critical: final.filter(f => f.critical).length,
  fix: final.filter(f => f.action === 'FIX').length,
  ask: final.filter(f => f.action === 'ASK').length,
  plan: final.filter(f => f.action === 'PLAN').length,
}
log('review-fanout: ' + agents.length + ' agents → ' + counts.raw + ' deduped / ' + counts.merged + ' after merge (' + counts.critical + ' critical, ' + counts.fix + ' FIX / ' + counts.ask + ' ASK / ' + counts.plan + ' PLAN)')

return { findings: final, counts, flags, perAgent: raw.map(r => ({ key: r.key, count: (r && r.result && Array.isArray(r.result.findings)) ? r.result.findings.length : 0 })) }
