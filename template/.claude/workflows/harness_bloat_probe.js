export const meta = {
  name: 'harness-bloat-probe',
  description: 'Measure per-dispatch harness context cost across agentType x model and which surfaces each shape loads',
  phases: [{ title: 'Probe', detail: 'one trivial cell per (agentType, model) combination' }],
}
// POST-CHECK exempt (benchmark/probe self-scoring): a benchmark or probe engine scores its own output.

// Two measurements, deliberately separated.
//
// HOW MUCH is the first-turn input token count in each agent's transcript -- not anything the
// agent says about itself. WHAT is this prompt, and it must not name any string it is testing
// for: the first version of this probe asked whether a "preservation-required" rule was present,
// which planted that exact string in every transcript and made the corroborating grep worthless.
// So the prompt asks for HEADINGS and the answer is checked against the real files afterwards.
const PROMPT = `Answer from your existing context only. Use NO tools -- do not read any file.

List the top-level section headings of any project-specific instruction document already in your
context (the kind a repository ships to configure an assistant). Give the headings verbatim, comma
separated, at most eight of them. If no such document is in your context, reply exactly: NONE.

Then on a second line give the same for any always-loaded memory index, or NONE.

Format:
INSTRUCTIONS: <headings, or NONE>
MEMORY: <headings, or NONE>`

const CELLS = [
  { label: 'default-sonnet',      agentType: null,              model: 'sonnet' },
  { label: 'general-sonnet',      agentType: 'general-purpose', model: 'sonnet' },
  { label: 'explore-sonnet',      agentType: 'Explore',         model: 'sonnet' },
  { label: 'plan-sonnet',         agentType: 'Plan',            model: 'sonnet' },
  { label: 'explore-opus',        agentType: 'Explore',         model: 'opus' },
  { label: 'default-haiku',       agentType: null,              model: 'haiku' },
  { label: 'general-haiku',       agentType: 'general-purpose', model: 'haiku' },
  { label: 'explore-haiku',       agentType: 'Explore',         model: 'haiku' },
]

phase('Probe')

const results = await parallel(CELLS.map(c => () => {
  // TOOL PROFILES — the inventory this cell launches with. A native child INHERITS the session's MCP
  // set, so the entry is `inherited` with effectiveMcp unknown, never a strict claim of enforcement.
  const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
  log('TOOL-PROFILES ' + JSON.stringify({ [c.label]: TOOLP.d[c.label] || TOOLP.f['any'] || null }))
  const opts = { label: c.label, phase: 'Probe', effort: 'low' }
  if (c.agentType) opts.agentType = c.agentType
  if (c.model) opts.model = c.model
  return agent(PROMPT, opts)
    .then(r => ({ ...c, outcome: 'ok', answer: String(r || '').slice(0, 500) }))
    .catch(e => ({ ...c, outcome: 'ERROR', detail: String(e).slice(0, 200) }))
}))

for (const r of results.filter(Boolean)) {
  log(`${r.label}: ${r.outcome} ${JSON.stringify(r.answer || r.detail || '')}`)
}
return { cells: results.filter(Boolean) }
