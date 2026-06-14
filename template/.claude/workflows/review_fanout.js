export const meta = {
  name: 'review-fanout',
  description: 'Generic read-only review fan-out engine: dispatch N caller-supplied review/audit agents in parallel over a shared CONTEXT, then Step-1 consolidation (dedup by file:line, sort by critical→tier→category) per the Orchestrator Action Protocol. Returns merged findings; Step 1.5 verification + the user-gated action walkthrough stay with the calling command.',
  phases: [
    { title: 'Review', detail: 'dispatch each supplied agent prompt in parallel (single-flight guard appended)' },
    { title: 'Consolidate', detail: 'merge, dedup by file:line, sort by critical→tier→category' },
  ],
}

// Platform contract: args is a JSON STRING. Parse-guard.
const A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
const agents = Array.isArray(A.agents) ? A.agents : []
const contextPrefix = A.contextPrefix || '' // optional shared CONTEXT prepended to every agent prompt
if (agents.length === 0) {
  return { error: 'No agents in args. The calling command (Claude) must assemble each agent prompt (from review_agents.md / session_audit_agents.md / etc.) and pass them via args.agents = [{key, prompt, model?}].' }
}

// Single-flight + read-only guard appended to EVERY fanned agent — protects all consumers by construction.
const GUARD = [
  '',
  '=== ORCHESTRATION GUARD (you are one of several agents running CONCURRENTLY) ===',
  'Read-only: do NOT modify files. Do NOT run tests or invoke /regression_gate — the GdUnit4 named-pipe is single-flight and concurrent runs wedge it. Do NOT use the csharp-ls LSP (documentSymbol/findReferences) — it is a single-flight wrapper and concurrent calls wedge it; use Grep/Read only.',
  'Return ONLY your JSON findings array (wrapped as {"findings": [...]}) per the schema — no prose around it.',
].join('\n')

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
          description: { type: 'string' },
          old: { type: ['string', 'null'] },
          new: { type: ['string', 'null'] },
          question: { type: ['string', 'null'] },
          options: { type: ['array', 'null'], items: { type: 'string' } },
          scope: { type: ['array', 'null'], items: { type: 'string' } },
          rationale: { type: 'string' },
        },
        required: ['agent', 'action', 'category', 'description', 'rationale'],
      },
    },
  },
  required: ['findings'],
}

// 'fable' is requestable but never a default — reserve for explicit high-fidelity dispatch.
const VALID_MODELS = ['opus', 'sonnet', 'haiku', 'fable']
// Floor: a caller that omits (or mis-spells) model must NOT silently inherit the session model —
// under Fable that turns a 6-lens fan-out into 6 Fable agents. Default to sonnet; callers escalate explicitly.
const DEFAULT_MODEL = 'sonnet'

phase('Review')
const raw = await parallel(agents.map(a => () => {
  const model = VALID_MODELS.includes(a.model) ? a.model : DEFAULT_MODEL
  const opts = { label: 'review:' + (a.key || 'agent'), phase: 'Review', schema: FINDINGS_SCHEMA, model }
  const prompt = (contextPrefix ? (contextPrefix + '\n\n') : '') + (a.prompt || '') + GUARD
  return agent(prompt, opts).then(r => ({ key: a.key, findings: (r && Array.isArray(r.findings)) ? r.findings : [] }))
}))

phase('Consolidate')
const merged = []
for (const r of raw) { if (r && Array.isArray(r.findings)) { merged.push(...r.findings) } }

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

const counts = {
  total: deduped.length,
  critical: deduped.filter(f => f.critical).length,
  fix: deduped.filter(f => f.action === 'FIX').length,
  ask: deduped.filter(f => f.action === 'ASK').length,
  plan: deduped.filter(f => f.action === 'PLAN').length,
}
log('review-fanout: ' + agents.length + ' agents → ' + counts.total + ' findings (' + counts.critical + ' critical, ' + counts.fix + ' FIX / ' + counts.ask + ' ASK / ' + counts.plan + ' PLAN)')

return { findings: deduped, counts, perAgent: raw.map(r => ({ key: r.key, count: r.findings.length })) }
