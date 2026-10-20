export const meta = {
  name: 'idea-fanout',
  description: 'Run idea generators and independent critics',
  phases: [
    { title: 'Generate', detail: 'each supplied lens generates raw candidates in parallel (no self-filter, no condense, work from pushed CONTEXT only)' },
    { title: 'Critique', detail: 'independent critics judge the FULL merged pool (barrier): dedup, fit, coverage-gap' },
  ],
}
// POST-CHECK exempt (ideas not claims): it returns ideas and critiques, not claims about files.
// Args: {generators: [{key, instr, model, effort}], critics?: [{key, instr, model, effort}], contextPrefix, genCount?, genFields?,
//        critic?: {model, effort}}. Every generator and critic the caller declares pins its own model (and effort when the
//        transport declares efforts). When args.critics is omitted, the engine's default critics start and take args.critic,
//        which is then required and validated before any agent starts. No engine supplies a default pin.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'idea-fanout: args must be JSON-serializable {generators: [{key, instr, model, effort}, ...], contextPrefix}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
const generators = Array.isArray(A.generators) ? A.generators : []
const contextPrefix = A.contextPrefix || '' // the pushed cluster CONTEXT, prepended to every agent prompt
const genCount = A.genCount || '15-20'      // raw candidates each generator returns

if (generators.length === 0) {
  return { error: 'No generators in args. The calling skill (Claude) must assemble each lens prompt and pass them via args.generators = [{key, instr, model, effort}], plus args.contextPrefix (the pushed cluster CONTEXT) and optional args.genFields / args.critics / args.critic.' }
}
if (generators.length < 2) {
  return { error: 'Fan-out needs >=2 DISTINCT lenses (diversity is the lever). One lens is just the single-agent Diverge phase — run that inline instead of dispatching this engine.' }
}

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. Inlined per script because the Workflow sandbox has
// no require/import.
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
if (!TRANSPORT || typeof TRANSPORT !== 'object' || !validStringList(TRANSPORT.ids)
  || (TRANSPORT.efforts !== undefined && !validStringList(TRANSPORT.efforts))) {
  return { error: 'idea-fanout: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary ({name, ids, efforts?}); without it this engine has no legal pins and refuses to guess.' }
}
const PIN = (m) => m
const EFF = (e) => e

const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
const supplied = [...generators, ...(Array.isArray(A.critics) ? A.critics : [])]
for (const job of supplied) {
  if (!job || typeof job !== 'object') {
    return { error: 'idea-fanout: every generator and critic must be an object with a key and instruction.' }
  }
}
// Every generator and critic the caller declares pins its own model and, when the transport declares efforts, its own effort.
const unpinned = supplied.filter(job => !VALID_MODELS.includes(job.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(job.effort)))
if (unpinned.length > 0) {
  return { error: 'idea-fanout: pin model and effort on every job (orchestration §5). Unpinned or illegal: '
    + unpinned.map(job => job.key || 'job').join(', ') + '. Each generator and critic needs ' + pinVocab + '.',
  badJobs: unpinned.map(job => job.key || 'job') }
}
// The default critics (dedup, fit, coverage-gap) start only when the caller omits args.critics. Criticism is judgment,
// so they take the caller's args.critic, never the guard-filled args.checker.
const callerCritics = Array.isArray(A.critics) && A.critics.length > 0
const criticOk = !!A.critic && typeof A.critic === 'object' && VALID_MODELS.includes(A.critic.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.critic.effort))
if (!callerCritics && !criticOk) {
  return { error: 'idea-fanout: pin model and effort on every job (orchestration §5). The default critics start when args.critics is omitted, so args.critic = {model, effort} must pin ' + pinVocab + '. Refusing before any generator starts.' }
}

// Per-cluster candidate schema. name+shape always required; the caller adds typed fields via flat genFields
// descriptors (kept flat to dodge gotcha_workflow_args_generation_fidelity — no nested schema object in args).
const genFields = Array.isArray(A.genFields) ? A.genFields : []
const candProps = {
  name: { type: 'string' },
  shape: { type: 'string', description: 'one specific, concrete one-line shape — not a stub' },
}
const candRequired = ['name', 'shape']
for (const f of genFields) {
  if (!f || typeof f.name !== 'string' || !f.name || f.name === 'name' || f.name === 'shape') { continue }
  candProps[f.name] = { type: 'string', description: typeof f.desc === 'string' ? f.desc : '' }
  if (f.required) { candRequired.push(f.name) }
}
// Containers are permissive (additionalProperties: true) so a stray key on ONE candidate can never
// discard the whole list — the reject-wholesale class feedback_schema_caps_must_not_invalidate_delegate_work
// exists to prevent. Required name/shape still hold; the merged pool forwards only name/shape/lens to
// critics, and the curator filters strays.
const GEN_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    candidates: {
      type: 'array',
      items: { type: 'object', additionalProperties: true, properties: candProps, required: candRequired },
    },
  },
  required: ['candidates'],
}

const CRITIC_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    notes: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: { topic: { type: 'string' }, finding: { type: 'string' } },
        required: ['topic', 'finding'],
      },
    },
    proposedAdditions: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: { name: { type: 'string' }, shape: { type: 'string' }, rationale: { type: 'string' } },
        required: ['name', 'shape'],
      },
    },
  },
  required: ['notes'],
}

// Generators work from pushed CONTEXT ONLY — fanned-agent Grep/Glob/semantic-search return intermittent
// false-empties (gotcha_workflow_fanout_search_false_absence). No-condense survives raw seeds verbatim
// (feedback_no_unilateral_condensation) so Claude can expand them downstream.
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'

const GEN_GUARD = [
  '',
  '=== GENERATION GUARD (you are one of several divergence lenses running CONCURRENTLY) ===',
  RELAY_LINE,
  'Work ONLY from the CONTEXT above. Do NOT search the codebase or vault (fan-out search returns intermittent false-empties); every fact you need was pushed to you.',
  'Do NOT self-filter, do NOT condense, do NOT rank. Raw divergence: quantity and lens-fidelity beat polish. Stay strictly inside your assigned lens; respect every BOUNDARY in the CONTEXT (reference named systems, never redefine them).',
  'Return ONLY your JSON candidate list per the schema — no prose around it.',
].join('\n')

// TOOL PROFILES — the inventory each generator and each critic of this run launches with. A native
// child INHERITS the session's MCP set, so an entry is `inherited` with effectiveMcp unknown unless
// the child runs as its generated agent type (`agent-type`).
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
const tpOf = (label) => TOOLP.d[label] || TOOLP.f['any'] || null
const swapOf = (label) => leanTypeOf([label], undefined, tpOf(label))
const RECORDED = {}
const tpLog = (labels) => {
  for (const l of labels) RECORDED[l] = leanRecord(tpOf(l), undefined, swapOf(l))
  log('TOOL-PROFILES ' + JSON.stringify(Object.fromEntries(labels.map(l => [l, RECORDED[l]]))))
}

phase('Generate')
tpLog(generators.map(g => 'gen:' + (g.key || 'lens')))
const genResults = await parallel(generators.map((g) => () => {
  const prompt = (contextPrefix ? contextPrefix + '\n\n' : '')
    + '## YOUR DIVERGENCE LENS: ' + (g.key || 'lens') + '\n' + (g.instr || '')
    + '\n\nReturn ' + genCount + ' candidate ideas as structured data.' + GEN_GUARD
  const label = 'gen:' + (g.key || 'lens')
  return leanAgent(prompt, { label, phase: 'Generate', schema: GEN_SCHEMA, model: PIN(g.model), ...(g.effort !== undefined ? { effort: EFF(g.effort) } : {}) }, swapOf(label), RECORDED)
    .then((r) => ({ key: g.key, candidates: (r && Array.isArray(r.candidates)) ? r.candidates : [], dead: !r }))
}))

// Merge in code + tag each candidate by the lens that produced it (position-indexed; a thrown thunk is null).
const merged = []
genResults.forEach((r, i) => {
  if (!r || !Array.isArray(r.candidates)) { return }
  r.candidates.forEach((c) => merged.push({ ...c, lens: generators[i].key }))
})
const perGenerator = genResults.map((r, i) => ({ key: generators[i].key, count: r ? r.candidates.length : 0, dead: !r || !!r.dead }))
const liveGens = perGenerator.filter((p) => !p.dead && p.count > 0).length
log('idea-fanout: merged ' + merged.length + ' raw candidates from ' + liveGens + '/' + generators.length + ' live generators')

if (merged.length === 0) {
  return { error: 'All generators returned empty — fan-out produced no candidates. Do NOT treat as a clean run; re-dispatch.', perGenerator }
}

// Critics judge the FULL pool (a genuine barrier — dedup/coverage-gap need every candidate at once).
const indexed = merged.map((c, i) => ({ i, name: c.name, shape: c.shape, lens: c.lens }))
const pool = JSON.stringify(indexed)

const DEFAULT_CRITICS = [
  { key: 'dedup', instr: 'Group near-duplicate candidates by index. For each duplicate cluster, list the indices and name the single best phrasing to keep; flag candidates that differ only cosmetically. notes: topic=\'dup-cluster\', finding=\'[indices] -> keep X because ...\'.' },
  { key: 'fit', instr: 'Flag candidates that CONFLICT with the boundary/invariants stated in the CONTEXT: anything that redefines a referenced system instead of referencing it, contradicts a stated invariant, or assumes machinery the CONTEXT marks out-of-scope. For each: index + the conflict + whether FATAL or FIXABLE. SEPARATELY flag genuinely uncertain ones (needs a hook into a system outside this cluster). notes: topic=\'conflict\'|\'uncertain\', finding=\'idx N: ...\'.' },
  { key: 'coverage-gap', instr: 'Treat the merged pool as a coverage map against the design axes named in the CONTEXT (objective axes / bins / themes / sub-systems). Name the axes that are UNDER-represented or entirely MISSING (notes: topic=\'gap\', finding=...). Then propose 3-6 SPECIFIC candidates that fill the biggest gaps — full objects in proposedAdditions. This is the completeness pass: what did all generators collectively miss?' },
]
const critics = callerCritics ? A.critics : DEFAULT_CRITICS

// Critics are FRESH, INDEPENDENT agents — never the generators self-grading
// (feedback_delegate_output_trust: shared premises bias verdicts).
const CRITIC_GUARD = [
  '',
  '=== CRITIC GUARD (you are an INDEPENDENT critic — you did NOT generate these candidates) ===',
  RELAY_LINE,
  'Judge ONLY the pushed pool + CONTEXT. Do NOT search the codebase or vault. Reference every candidate by its index.',
  'Return ONLY your JSON per the schema — no prose around it.',
].join('\n')

phase('Critique')
tpLog(critics.map(c => 'critic:' + (c.key || 'critic')))
const critiqueResults = await parallel(critics.map((c) => () => {
  // A default critic takes args.critic; a caller-declared critic pins itself.
  const pin = callerCritics ? c : A.critic
  const prompt = (contextPrefix ? contextPrefix + '\n\n' : '')
    + '## MERGED CANDIDATE POOL (' + merged.length + ' items, indexed)\n' + pool
    + '\n\n## YOUR CRITIC LENS: ' + (c.key || 'critic') + '\n' + (c.instr || '') + CRITIC_GUARD
  const label = 'critic:' + (c.key || 'critic')
  return leanAgent(prompt, { label, phase: 'Critique', schema: CRITIC_SCHEMA, model: PIN(pin.model), ...(pin.effort !== undefined ? { effort: EFF(pin.effort) } : {}) }, swapOf(label), RECORDED)
    .then((r) => ({
      key: c.key,
      notes: (r && Array.isArray(r.notes)) ? r.notes : [],
      proposedAdditions: (r && Array.isArray(r.proposedAdditions)) ? r.proposedAdditions : [],
      dead: !r,
    }))
}))

const perCritic = critiqueResults.map((r, i) => ({
  key: critics[i].key,
  notes: r ? r.notes.length : 0,
  additions: r ? r.proposedAdditions.length : 0,
  dead: !r || !!r.dead,
}))
log('idea-fanout: ' + critics.length + ' critics returned (' + perCritic.filter((p) => !p.dead).length + ' live)')

return {
  rawCount: merged.length,
  candidates: merged,
  critiques: critiqueResults.filter(Boolean),
  perGenerator,
  perCritic,
}
