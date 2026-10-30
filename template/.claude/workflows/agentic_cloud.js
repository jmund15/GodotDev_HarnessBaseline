export const meta = {
  name: 'agentic-cloud',
  description: 'Run cloud agentic benchmark cells',
  phases: [{ title: 'Agentic cells' }],
}
// POST-CHECK exempt (benchmark/probe self-scoring): a benchmark or probe engine scores its own output.

// args: { corpus: "<materialised corpus root>",
//         jobs: [{arm, model, rung, rep, task, cap, key}] }
//
// The key material arrives INLINE from the orchestrator. An earlier version loaded keys.json
// through an agent; the model reshaped the object, every field interpolated as `undefined`, and
// five cells returned fluent empty answers that were nearly recorded as a capability result.
// Workflow scripts have no filesystem access, so the orchestrator pushes the data -- a model is
// never the transport for material that must arrive verbatim.
const CORPUS = args.corpus
const JOBS = args.jobs || []
// Every job pins model, and effort when the transport declares efforts. The transport supplies the vocabulary only.
const TRANSPORT = args.__transport || {}
const IDS = Array.isArray(TRANSPORT.ids) ? TRANSPORT.ids : []
const EFFORTS = Array.isArray(TRANSPORT.efforts) ? TRANSPORT.efforts : null

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['answer', 'files_read', 'read_count', 'finished'],
  properties: {
    answer: { type: 'string', description: 'The value asked for, exactly as read. Empty string if you could not determine it.' },
    files_read: { type: 'array', items: { type: 'string' }, description: 'Every path you read or listed, in order.' },
    read_count: { type: 'number', description: 'Total read+list operations you performed.' },
    finished: { type: 'boolean', description: 'true only if you are reporting a value you actually read.' },
    reasoning: { type: 'string', description: 'The chain you followed, one line per hop. <=120 words.' },
  },
}

// Refuse to spend anything on a malformed job. A hole in an interpolated prompt produces a
// well-formed empty answer, which is indistinguishable from a capability failure.
const bad = []
if (!IDS.length || (TRANSPORT.efforts !== undefined && !EFFORTS)) bad.push('args.__transport is missing or malformed; the provider guard supplies the native model and effort vocabulary')
if (!CORPUS) bad.push('no corpus root')
JOBS.forEach((j, i) => {
  for (const f of ['arm', 'model', 'rung', 'task', 'cap', 'key']) {
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

phase('Agentic cells')

const results = await parallel(JOBS.map(j => () => {
  const prompt = `You are a file-reading agent working inside this directory:
${CORPUS}

Everything you need is under that directory and nothing outside it is relevant. You may ONLY learn
about these files by reading and listing them. You have no other knowledge of this tree and must
not guess at any value you have not read. Every path below is relative to that directory.

TASK
${j.task}

HOW TO WORK
  - Use ONLY file reading and directory listing, and only inside the directory named above.
  - Never state a value you have not actually read out of a file.
  - You may perform at most ${j.cap} read or list operations. If you reach that limit without an
    answer, set finished=false and leave answer empty. A guessed answer is worse than no answer.
  - Report every path you read or listed, in order.`
  // TOOL PROFILES — the inventory this cell launches with. A native child INHERITS the session's MCP
  // set, so the entry is `inherited` with effectiveMcp unknown, never a strict claim of enforcement.
  const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
  const tpLabel = `${j.arm}-${j.rung}-r${j.rep}`
  log('TOOL-PROFILES ' + JSON.stringify({ [tpLabel]: TOOLP.d[tpLabel] || TOOLP.f['any'] || null }))
  return agent(prompt, {
    label: `${j.arm}-${j.rung}-r${j.rep}`,
    phase: 'Agentic cells',
    model: j.model,
    effort: j.effort,
    schema: SCHEMA,
  }).then(r => ({ ...j, outcome: 'ok', result: r }))
    .catch(e => ({ ...j, outcome: 'ERROR', detail: String(e).slice(0, 200) }))
}))

const out = results.filter(Boolean)
let pass = 0, fail = 0, harness = 0
for (const r of out) {
  if (r.outcome !== 'ok' || !r.result) { harness++; log(`${r.arm} ${r.rung} r${r.rep}: ${r.outcome}`); continue }
  const res = r.result
  // Zero reads means the arm never touched the corpus: it was not asked, so it cannot have failed.
  if (res.read_count === 0) {
    harness++
    log(`${r.arm} ${r.rung} r${r.rep}: !! NO-READS -- harness fault, NOT a model result`)
    continue
  }
  const over = res.read_count > r.cap
  const correct = !over && res.finished
    && String(res.answer).trim().replace(/[^0-9.-]/g, '') === String(r.key).trim()
  if (correct) pass++; else fail++
  log(`${r.arm} ${r.rung} r${r.rep}: ${correct ? 'PASS' : 'FAIL'} answer=${JSON.stringify(res.answer)} `
    + `key=${r.key} reads=${res.read_count}/${r.cap}${over ? ' OVER-CAP' : ''}`)
}
log(`totals: ${pass} pass, ${fail} fail, ${harness} harness-fault (unscored)`)
return { pass, fail, harness, cells: out }
