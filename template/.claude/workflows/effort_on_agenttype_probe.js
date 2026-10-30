export const meta = {
  name: 'effort-on-agenttype-probe',
  description: 'Measure effort behavior across built-in agent types',
  phases: [{ title: 'Effort', detail: 'agentType x model x effort, identical prompt' }],
}
// POST-CHECK exempt (benchmark/probe self-scoring): a benchmark or probe engine scores its own output.

// THE QUESTION
// ------------
// The harness routes fan-outs through Workflow specifically because the `Agent` tool has no effort
// parameter and silently inherits SESSION effort -- so an Explore lens dispatched from an
// opus/high session would run sonnet/high, a cell the ladder says is actively worse than
// sonnet/medium. Recommending `agentType: 'Explore'` is only safe if `opts.effort` still binds when
// `opts.agentType` is set. That is an assumption until measured, so this measures it.
//
// The probe needs a task with real deliberation headroom and a SHORT answer, so the signal lands in
// token count rather than in output length. Counting the tokens is the measurement; the answer only
// has to be checkable so a degenerate cell is visible.
const PROMPT = `Solve this and reply with ONLY the final number. Use no tools.

Five containers hold 3, 7, 12, 18 and 26 litres. You may repeatedly pick any two containers and
replace BOTH of their contents with the absolute difference of the two. Repeat until one single
value remains that cannot change further.

What is the largest value that can remain? Reply with only that number.`

const CELLS = []
for (const agentType of ['Explore', 'general-purpose']) {
  for (const model of ['sonnet', 'haiku']) {
    for (const effort of ['low', 'high']) {
      CELLS.push({ label: `${agentType}-${model}-${effort}`, agentType, model, effort })
    }
  }
}

// TOOL PROFILES — the inventory each cell of this probe launches with. A native child INHERITS the
// session's MCP set, so every entry is `inherited` with effectiveMcp unknown, never a strict claim.
// hooks/workflow_provider_guard.py resolves args.__toolProfileDefaults; the probe declares nothing.
const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
log('TOOL-PROFILES ' + JSON.stringify(Object.fromEntries(CELLS.map(c => [c.label, TOOLP.d[c.label] || TOOLP.f['any'] || null]))))

phase('Effort')

const out = await parallel(CELLS.map(c => () =>
  agent(PROMPT, { label: c.label, phase: 'Effort', agentType: c.agentType, model: c.model, effort: c.effort })
    .then(r => ({ ...c, outcome: 'ok', answer: String(r || '').trim().slice(0, 120) }))
    .catch(e => ({ ...c, outcome: 'ERROR', detail: String(e).slice(0, 200) }))
))

for (const r of out.filter(Boolean)) log(`${r.label}: ${r.outcome} ${JSON.stringify(r.answer || r.detail)}`)
return { cells: out.filter(Boolean) }
