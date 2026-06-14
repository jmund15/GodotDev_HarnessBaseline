export const meta = {
  name: 'idea-fanout',
  description: 'Lens-diverse idea divergence + independent critique engine. N caller-supplied generator agents (each a DISTINCT divergence lens) produce raw schema-structured candidates over a shared CONTEXT -> code-merge + lens-tag -> M independent critics judge the FULL merged pool (default dedup / fit / coverage-gap). Returns the raw pool + critic annotations. Curation, honing, ranking, and the per-cluster user-react loop STAY with the calling skill.',
  phases: [
    { title: 'Generate', detail: 'each supplied lens generates raw candidates in parallel (no self-filter, no condense, work from pushed CONTEXT only)' },
    { title: 'Critique', detail: 'independent critics judge the FULL merged pool (barrier): dedup, fit, coverage-gap' },
  ],
}

// Platform contract: args is a JSON STRING. Parse-guard.
const A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
const generators = Array.isArray(A.generators) ? A.generators : []
const contextPrefix = A.contextPrefix || '' // the pushed cluster CONTEXT, prepended to every agent prompt
const genCount = A.genCount || '15-20'      // raw candidates each generator returns

if (generators.length === 0) {
  return { error: 'No generators in args. The calling skill (Claude) must assemble each lens prompt and pass them via args.generators = [{key, instr, model?}], plus args.contextPrefix (the pushed cluster CONTEXT) and optional args.genFields / args.critics.' }
}
// Lens-diversity is the value lever, not agent count. A single lens IS the inline Diverge phase — no fan-out needed.
if (generators.length < 2) {
  return { error: 'Fan-out needs >=2 DISTINCT lenses (diversity is the lever). One lens is just the single-agent Diverge phase — run that inline instead of dispatching this engine.' }
}

// 'fable' is requestable but never a default — reserve for explicit high-fidelity dispatch.
const VALID_MODELS = ['opus', 'sonnet', 'haiku', 'fable']
// Asymmetric defaults: divergence wants the strong model; criticism (rigor) does not. A caller that
// omits (or mis-spells) model must NOT silently inherit the session model — under Fable that turns the
// fan-out into Fable agents. Generators default opus; critics floor to sonnet; callers override per agent.
const GEN_DEFAULT_MODEL = 'opus'
const CRITIC_DEFAULT_MODEL = 'sonnet'

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
const GEN_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    candidates: {
      type: 'array',
      items: { type: 'object', additionalProperties: false, properties: candProps, required: candRequired },
    },
  },
  required: ['candidates'],
}

const CRITIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    notes: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: { topic: { type: 'string' }, finding: { type: 'string' } },
        required: ['topic', 'finding'],
      },
    },
    proposedAdditions: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
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
const GEN_GUARD = [
  '',
  '=== GENERATION GUARD (you are one of several divergence lenses running CONCURRENTLY) ===',
  'Work ONLY from the CONTEXT above. Do NOT search the codebase or vault (fan-out search returns intermittent false-empties); every fact you need was pushed to you.',
  'Do NOT self-filter, do NOT condense, do NOT rank. Raw divergence: quantity and lens-fidelity beat polish. Stay strictly inside your assigned lens; respect every BOUNDARY in the CONTEXT (reference named systems, never redefine them).',
  'Return ONLY your JSON candidate list per the schema — no prose around it.',
].join('\n')

phase('Generate')
const genResults = await parallel(generators.map((g) => () => {
  const model = VALID_MODELS.includes(g.model) ? g.model : GEN_DEFAULT_MODEL
  const prompt = (contextPrefix ? contextPrefix + '\n\n' : '')
    + '## YOUR DIVERGENCE LENS: ' + (g.key || 'lens') + '\n' + (g.instr || '')
    + '\n\nReturn ' + genCount + ' candidate ideas as structured data.' + GEN_GUARD
  return agent(prompt, { label: 'gen:' + (g.key || 'lens'), phase: 'Generate', schema: GEN_SCHEMA, model })
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
const critics = (Array.isArray(A.critics) && A.critics.length) ? A.critics : DEFAULT_CRITICS

// Critics are FRESH, INDEPENDENT agents — never the generators self-grading
// (red_team_must_be_independent_dispatch: shared premises bias verdicts).
const CRITIC_GUARD = [
  '',
  '=== CRITIC GUARD (you are an INDEPENDENT critic — you did NOT generate these candidates) ===',
  'Judge ONLY the pushed pool + CONTEXT. Do NOT search the codebase or vault. Reference every candidate by its index.',
  'Return ONLY your JSON per the schema — no prose around it.',
].join('\n')

phase('Critique')
const critiqueResults = await parallel(critics.map((c) => () => {
  const model = VALID_MODELS.includes(c.model) ? c.model : CRITIC_DEFAULT_MODEL
  const prompt = (contextPrefix ? contextPrefix + '\n\n' : '')
    + '## MERGED CANDIDATE POOL (' + merged.length + ' items, indexed)\n' + pool
    + '\n\n## YOUR CRITIC LENS: ' + (c.key || 'critic') + '\n' + (c.instr || '') + CRITIC_GUARD
  return agent(prompt, { label: 'critic:' + (c.key || 'critic'), phase: 'Critique', schema: CRITIC_SCHEMA, model })
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
