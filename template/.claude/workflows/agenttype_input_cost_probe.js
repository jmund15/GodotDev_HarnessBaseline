export const meta = {
  name: 'agenttype-input-cost-probe',
  description: 'Measure first-turn input cost by agent type',
  phases: [{ title: 'Probe', detail: 'identical no-tool prompt across agentType, model held fixed' }],
}
// POST-CHECK exempt (benchmark/probe self-scoring): a benchmark or probe engine scores its own output.

// THE QUESTION
// ------------
// dispatch.js's VALID_AGENT_TYPES comment claims Explore costs 27.8K first-turn input tokens against
// general-purpose's 53.4K (measured 2026-08-18, sonnet). That delta is the ONLY reason to pin a scout,
// and it is the number that decides whether exposing `agentType` in explore_fanout.js/review_fanout.js
// is worth touching three baseline-tracked files. A stale figure would silently justify the wrong call.
//
// The prompt must terminate in ONE turn with no tool calls, so the measurement lands entirely in
// first-turn input (the harness + system prompt the agentType decides) and output tokens cannot
// confound it. The answer is irrelevant; only its brevity matters.
//
// Read the result from the run's transcript dir, NOT from the return value: agent() returns final
// text, and per-agent token usage lives in agent-<id>.jsonl. Model is held fixed at sonnet to match
// the 2026-08-18 baseline — agentType is the only variable.
const PROMPT = 'Reply with exactly the word: ok\n\nUse no tools. Do not explain.'

// args.models: the model ids to hold fixed, one cell per agentType per model. Effort stays low: the prompt
// ends in one turn, so effort cannot move first-turn input.
const AGENT_TYPES = ['Explore', 'general-purpose', 'lane-survey', 'lane-review', 'lane-author']
const MODELS = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return (a && a.models) || [] } catch (e) { return [] } })()
if (!MODELS.length) throw new Error('args.models is required: the model ids to probe')
const CELLS = MODELS.flatMap(m => AGENT_TYPES.map(t => ({
  label: `${t.toLowerCase()}-${m.replace(/[^a-z0-9]+/gi, '-')}-low`, agentType: t, model: m, effort: 'low',
})))

// TOOL PROFILES — the inventory each cell of this probe launches with. A native child INHERITS the
// session's MCP set, so every entry is `inherited` with effectiveMcp unknown, never a strict claim.
// hooks/workflow_provider_guard.py resolves args.__toolProfileDefaults; the probe declares nothing.
const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
log('TOOL-PROFILES ' + JSON.stringify(Object.fromEntries(CELLS.map(c => [c.label, TOOLP.d[c.label] || TOOLP.f['any'] || null]))))

phase('Probe')

const out = await parallel(CELLS.map(c => () =>
  agent(PROMPT, { label: c.label, phase: 'Probe', agentType: c.agentType, model: c.model, effort: c.effort })
    .then(r => ({ ...c, outcome: 'ok', answer: String(r || '').trim().slice(0, 80) }))
    .catch(e => ({ ...c, outcome: 'ERROR', detail: String(e).slice(0, 200) }))
))

for (const r of out.filter(Boolean)) log(`${r.label}: ${r.outcome} ${JSON.stringify(r.answer || r.detail)}`)
return { cells: out.filter(Boolean), note: 'input-token counts live in the transcript dir agent-*.jsonl, not here' }
