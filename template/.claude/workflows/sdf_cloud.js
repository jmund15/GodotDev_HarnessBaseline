export const meta = {
  name: 'sdf-cloud',
  description: 'Run one cloud cell from a rendered S/D/F prompt file and capture the raw answer; cloud-sdf runs whole staircases',
  phases: [{ title: 'Cells' }],
}
// POST-CHECK exempt (benchmark/probe self-scoring): a benchmark or probe engine scores its own output.

// args: { jobs: [{arm, model, rung, rep, file, key?}] }
//   file : absolute path to the RENDERED prompt. The arm reads it; that file IS the prompt.
//   key  : optional expected answer. When present the cell is scored inline (numeric axes only);
//          otherwise the raw answer is returned for the axis's own scorer to grade offline.
const JOBS = args.jobs || []
// Every job pins model, and effort when the transport declares efforts. The transport supplies the vocabulary only.
const TRANSPORT = args.__transport || {}
const IDS = Array.isArray(TRANSPORT.ids) ? TRANSPORT.ids : []
const EFFORTS = Array.isArray(TRANSPORT.efforts) ? TRANSPORT.efforts : null

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  // `extra_files_read` guards the CLOSED-BOOK threat. It does not guard the other one, which is
  // COMPUTATIONAL OFFLOAD: an arm that writes a script to compute the answer has not demonstrated
  // the reasoning the axis measures, and an ai-worker deployment has no tools to offload to, so
  // such a pass does not transfer. Measured: one Sonnet cell reported extra_files_read=0 honestly
  // and had still used Bash x6 + Write x2 to parse the citation graph.
  required: ['answer', 'extra_files_read', 'computed_with_tools'],
  properties: {
    answer: { type: 'string', description: 'Your complete answer to the task in the file. For a numeric task, the bare number.' },
    extra_files_read: { type: 'number', description: 'How many files you read BESIDES the one prompt file. Report honestly; 0 is expected.' },
    computed_with_tools: { type: 'boolean', description: 'True if you used ANY tool other than reading the one prompt file to derive your answer -- running a script, writing and executing code, computing a graph or ordering programmatically. Reading the prompt file does not count. Report honestly; this does not make your answer wrong, it records which configuration produced it.' },
    notes: { type: 'string', description: 'Optional: where you got stuck, or what you had to assume. <=100 words.' },
  },
}

const bad = []
if (!IDS.length || (TRANSPORT.efforts !== undefined && !EFFORTS)) bad.push('args.__transport is missing or malformed; the provider guard supplies the native model and effort vocabulary')
JOBS.forEach((j, i) => {
  for (const f of ['arm', 'model', 'rung', 'file']) {
    if (j[f] === undefined || j[f] === null || j[f] === '') bad.push(`job[${i}]: missing '${f}'`)
  }
  if (j.model && !IDS.includes(j.model)) bad.push(`job[${i}]: model ${JSON.stringify(j.model)} is not in __transport.ids`)
  if (EFFORTS) {
    if (j.effort === undefined || j.effort === null || j.effort === '') bad.push(`job[${i}]: missing 'effort'`)
    else if (!EFFORTS.includes(j.effort)) bad.push(`job[${i}]: effort ${JSON.stringify(j.effort)} is not in __transport.efforts`)
  }
})
if (bad.length) bad.unshift('pin model and effort on every job (orchestration §5)')
if (bad.length) {
  log(`HARNESS ERROR -- refusing to dispatch: ${[...new Set(bad)].join('; ')}`)
  return { error: 'malformed args', detail: [...new Set(bad)], dispatched: 0 }
}

phase('Cells')

const results = await parallel(JOBS.map(j => () => {
  const prompt = `Read the file at this exact path, in full:

${j.file}

That file contains a complete, self-contained task: reference material followed by a question.
Do the task it states and answer its question.

RULES
  - Work ONLY from the contents of that one file. Do not read any other file, and do not use any
    knowledge of this repository. Everything you need is in it.
  - If the task asks for a number, put the bare number in \`answer\` with no units and no prose.
  - Report in \`extra_files_read\` how many files you read besides that one. Answer honestly.
    A truthful non-zero count is useful; a false zero silently corrupts the measurement.`
  // TOOL PROFILES — the inventory this cell launches with. A native child INHERITS the session's MCP
  // set, so the entry is `inherited` with effectiveMcp unknown, never a strict claim of enforcement.
  const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
  const tpLabel = `${j.arm}-${j.rung}-r${j.rep || 1}`
  log('TOOL-PROFILES ' + JSON.stringify({ [tpLabel]: TOOLP.d[tpLabel] || TOOLP.f['any'] || null }))
  return agent(prompt, {
    label: `${j.arm}-${j.rung}-r${j.rep || 1}`,
    phase: 'Cells',
    model: j.model,
    effort: j.effort,
    schema: SCHEMA,
  }).then(r => ({ ...j, outcome: 'ok', result: r }))
    .catch(e => ({ ...j, outcome: 'ERROR', detail: String(e).slice(0, 200) }))
}))

const out = results.filter(Boolean)
let pass = 0, fail = 0, ungraded = 0, contaminated = 0
for (const r of out) {
  if (r.outcome !== 'ok' || !r.result) { log(`${r.arm} ${r.rung} r${r.rep || 1}: ${r.outcome}`); continue }
  const a = String(r.result.answer || '').trim()
  const extra = r.result.extra_files_read || 0
  if (extra > 0) contaminated++
  if (r.key === undefined || r.key === null) {
    ungraded++
    log(`${r.arm} ${r.rung} r${r.rep || 1}: answer=${JSON.stringify(a.slice(0, 60))} extra=${extra} (ungraded)`)
    continue
  }
  const norm = a.replace(/[^0-9.-]/g, '')
  const ok = norm === String(r.key).trim()
  if (ok) pass++; else fail++
  log(`${r.arm} ${r.rung} r${r.rep || 1}: ${ok ? 'PASS' : 'FAIL'} got=${JSON.stringify(a.slice(0, 40))} key=${r.key} extra=${extra}`)
}
log(`totals: ${pass} pass, ${fail} fail, ${ungraded} ungraded, ${contaminated} cell(s) read extra files`)
return { pass, fail, ungraded, contaminated, cells: out }
