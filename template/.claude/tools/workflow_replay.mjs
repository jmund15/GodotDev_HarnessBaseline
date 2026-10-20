#!/usr/bin/env node
// Replays a finished Workflow run locally and prints its full processed result, with no model call.
//
//   node .claude/tools/workflow_replay.mjs <transcriptDir>
//
// <transcriptDir> is <session dir>/subagents/workflows/<runId>. Claude Code's run record for it is
// <session dir>/workflows/<runId>.json, holding the exact `script`, the `args` the guard injected and the
// returned `result`. The recorded script runs under Node with every agent() call answered from
// <transcriptDir>/journal.jsonl, first with the recorded args, which must reproduce the recorded result
// byte for byte, then with args.resultMode = 'full'; stdout is that second result as JSON.
//
// Call matching: the journal's `key` is the runtime's cache key and cannot be recomputed from the record, so
// the nth agent() call carrying a label takes the nth call of the recorded run with that label. The record's
// `workflowProgress` agent entries list those calls in call order with the agentId that answered each, which
// excludes a resumed run's dead attempts. A record without them falls back to the journal's started rows: a
// row repeating the previous same-label row's key after that row ended without a result is a retry of one
// call, and a journal with no labels at all matches every call in started-row order. A `result` row answers
// the call (JSON text is parsed when the call passed a schema); a `failed` row resolves null; a progress
// entry that errored before launch (no agentId) rejects with its recorded error. These failure semantics
// were measured on this machine's recorded runs (2026-10-09); the recorded-args check catches a run they miss.
//
// Exit 0 prints only the result. Exit 1 prints `workflow_replay: <gap>` on stderr and nothing on stdout when
// the record is missing or unreadable, the record lacks a script or result, a call has no journal row or no
// result, a journal call goes unused, the script needs a global the replay does not model (`budget`,
// `workflow`), or the recorded-args replay does not reproduce the recorded result. Exit 2 is usage.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export class ReplayGap extends Error {}

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const DATE_ERROR = 'Date.now() / new Date() are unavailable in workflow scripts (breaks resume). Stamp results after the workflow returns, or pass timestamps via args.'
class WorkflowDate extends Date {
  constructor() { throw new Error(DATE_ERROR) }
  static now() { throw new Error(DATE_ERROR) }
}
const workflowMath = Object.create(Math)
workflowMath.random = () => { throw new Error('Math.random() is unavailable in workflow scripts (breaks resume).') }

export function locateRun(transcriptDir) {
  const dir = path.resolve(transcriptDir)
  const runId = path.basename(dir)
  const workflowsDir = path.dirname(dir)
  if (path.basename(workflowsDir) !== 'workflows' || path.basename(path.dirname(workflowsDir)) !== 'subagents') {
    throw new ReplayGap('transcript directory ' + dir + ' is not <session dir>/subagents/workflows/<runId>')
  }
  const sessionDir = path.dirname(path.dirname(workflowsDir))
  return { runId, transcriptDir: dir, record: path.join(sessionDir, 'workflows', runId + '.json'), journal: path.join(dir, 'journal.jsonl') }
}

export function readRecord(file) {
  let text
  try { text = fs.readFileSync(file, 'utf8') } catch (e) { throw new ReplayGap('run record ' + file + ' is missing or unreadable: ' + e.message) }
  let record
  try { record = JSON.parse(text) } catch (e) { throw new ReplayGap('run record ' + file + ' is not JSON: ' + e.message) }
  if (!record || typeof record !== 'object' || Array.isArray(record)) throw new ReplayGap('run record ' + file + ' is not a JSON object')
  if (typeof record.script !== 'string' || !record.script.trim()) throw new ReplayGap('run record ' + file + ' holds no script string')
  if (!Object.prototype.hasOwnProperty.call(record, 'result') || record.result === undefined || record.result === null) {
    throw new ReplayGap('run record ' + file + ' holds no recorded result to check the replay against')
  }
  if (record.args !== null && record.args !== undefined && typeof record.args !== 'object' && typeof record.args !== 'string') {
    throw new ReplayGap('run record ' + file + ' holds args of type ' + typeof record.args + ', a format the replay does not read')
  }
  return record
}

const labelOf = row => (typeof row.label === 'string' ? row.label : null)
const nameOf = label => (label === null ? '(unlabeled)' : label)

// -> {started: [row], outcomes: Map(agentId -> result|failed row)}.
export function readJournal(file) {
  let text
  try { text = fs.readFileSync(file, 'utf8') } catch (e) { throw new ReplayGap('journal ' + file + ' is missing or unreadable: ' + e.message) }
  const rows = []
  text.split('\n').forEach((line, index) => {
    if (!line.trim()) return
    try { rows.push(JSON.parse(line)) } catch (e) { throw new ReplayGap('journal ' + file + ' line ' + (index + 1) + ' is not JSON') }
  })
  const outcomes = new Map()
  for (const row of rows) {
    if ((row.type === 'result' || row.type === 'failed') && typeof row.agentId === 'string') outcomes.set(row.agentId, row)
  }
  const started = rows.filter(row => row.type === 'started')
  if (started.some(row => typeof row.agentId !== 'string' || typeof row.key !== 'string')) {
    throw new ReplayGap('journal ' + file + ' has a started row without agentId or key')
  }
  return { started, outcomes }
}

const outcomeOf = (journal, agentId) => {
  const end = journal.outcomes.get(agentId)
  return { outcome: end ? end.type : null, result: end && end.type === 'result' ? end.result : undefined }
}

// The calls of the recorded run, in call order, bucketed by the label a script call is matched on.
// -> {bucketOf(label) -> bucket, buckets: Map(bucket -> [slot])}; slot = {label, phase, agentIds, outcome, result}.
// The record's workflowProgress names the agent that answered each call of THIS run (a resumed run's journal
// also holds the dead run's attempts), so it is used when present; otherwise the journal's started rows are.
export function callTable(record, journal) {
  const buckets = new Map()
  const add = (bucket, slot) => { if (!buckets.has(bucket)) buckets.set(bucket, []); buckets.get(bucket).push(slot) }
  const phaseOf = new Map(journal.started.map(row => [row.agentId, typeof row.phase === 'string' ? row.phase : null]))
  const progress = Array.isArray(record.workflowProgress)
    ? record.workflowProgress.filter(entry => entry && entry.type === 'workflow_agent') : []
  if (progress.length) {
    const ordered = [...progress].sort((x, y) => Number(x.index ?? 0) - Number(y.index ?? 0))
    for (const entry of ordered) {
      const label = typeof entry.label === 'string' ? entry.label : null
      const id = typeof entry.agentId === 'string' ? entry.agentId : null
      const end = id ? outcomeOf(journal, id)
        : { outcome: entry.state === 'error' ? 'rejected' : null, result: undefined, error: typeof entry.error === 'string' ? entry.error : '' }
      add(label, { label, phase: id ? (phaseOf.get(id) ?? null) : null, agentIds: id ? [id] : [], ...end })
    }
    return { bucketOf: label => label, buckets }
  }
  const unlabeled = journal.started.every(row => labelOf(row) === null)
  const last = new Map()
  for (const row of journal.started) {
    const label = labelOf(row)
    const bucket = unlabeled ? null : label
    const end = outcomeOf(journal, row.agentId)
    const prior = last.get(bucket)
    if (prior && prior.key === row.key && prior.outcome !== 'result') {
      prior.agentIds.push(row.agentId)
      Object.assign(prior, end)
      continue
    }
    const slot = { label, phase: phaseOf.get(row.agentId), key: row.key, agentIds: [row.agentId], ...end }
    last.set(bucket, slot)
    add(bucket, slot)
  }
  return { bucketOf: label => (unlabeled ? null : label), buckets }
}

const unmodeled = name => new Proxy(function () {}, {
  get(_target, prop) { throw new ReplayGap('the script reads `' + name + '.' + String(prop) + '`, a global the replay does not model') },
  apply() { throw new ReplayGap('the script calls `' + name + '()`, a global the replay does not model') },
})


// Runs the recorded script once against the journal. -> the script's return value.
export async function runScript(script, args, calls) {
  const body = script.replace(/^export\s+const\s+meta\s*=/m, 'const meta =')
  let fn
  try {
    fn = new AsyncFunction('Date', 'Math', 'args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'budget', 'workflow', body)
  } catch (e) { throw new ReplayGap('the recorded script does not compile as a Workflow body: ' + e.message) }
  const table = new Map([...calls.buckets].map(([bucket, list]) => [bucket, list.map(slot => ({ ...slot, used: false }))]))
  const cursor = new Map()
  let gap = null
  const fatal = message => { if (!gap) gap = new ReplayGap(message); throw gap }
  const agent = async (prompt, opts) => {
    const o = opts || {}
    const label = typeof o.label === 'string' ? o.label : null
    const bucket = calls.bucketOf(label)
    const list = table.get(bucket) || []
    const n = cursor.get(bucket) || 0
    cursor.set(bucket, n + 1)
    const slot = list[n]
    if (!slot) fatal('agent call ' + (n + 1) + ' labeled ' + nameOf(label) + ' has no journal row')
    slot.used = true
    if (typeof o.phase === 'string' && slot.phase !== null && slot.phase !== o.phase) {
      fatal('agent call ' + nameOf(label) + ' runs in phase ' + o.phase + ' but its journal row ' + slot.agentIds.join(',') + ' is phase ' + slot.phase)
    }
    if (slot.outcome === null) fatal('agent ' + nameOf(label) + ' (' + (slot.agentIds.join(',') || 'no agent id') + ') has no result in the journal')
    if (slot.outcome === 'rejected') throw new Error(slot.error)
    if (slot.outcome === 'failed') return null
    if (o.schema && typeof slot.result === 'string') {
      try { return JSON.parse(slot.result) } catch (e) { fatal('agent ' + nameOf(label) + ' (' + slot.agentIds.join(',') + ') has a schema result that is not JSON') }
    }
    return slot.result
  }
  const settle = fn => Promise.resolve().then(fn).catch(e => { if (gap || e instanceof ReplayGap) throw (gap || e); return null })
  const parallel = async thunks => Promise.all(thunks.map(settle))
  const pipeline = async (items, ...stages) => Promise.all(items.map((item, index) => settle(async () => {
    let value
    for (let s = 0; s < stages.length; s++) value = await (s === 0 ? stages[s](item, index) : stages[s](value, item, index))
    return value
  })))
  let result
  try {
    result = await fn(WorkflowDate, workflowMath, args, agent, parallel, pipeline, () => {}, () => {}, unmodeled('budget'), unmodeled('workflow'))
  } catch (e) {
    if (gap || e instanceof ReplayGap) throw (gap || e)
    throw new ReplayGap('the recorded script threw during replay: ' + (e && e.message ? e.message : String(e)))
  }
  if (gap) throw gap
  const unused = [...table.values()].flat().filter(slot => !slot.used)
  if (unused.length) {
    throw new ReplayGap('the replayed script never called ' + unused.length + ' journal agent(s): '
      + unused.map(slot => nameOf(slot.label) + ' (' + (slot.agentIds.join(',') || 'no agent id') + ')').join(', '))
  }
  return result
}

const copyArgs = args => (args === null || args === undefined) ? undefined : JSON.parse(JSON.stringify(args))
function fullArgs(args) {
  let parsed = args
  if (typeof args === 'string') {
    try { parsed = JSON.parse(args) } catch (e) { throw new ReplayGap('the recorded args are a string that is not JSON') }
  }
  if (parsed === null || parsed === undefined) parsed = {}
  if (typeof parsed !== 'object' || Array.isArray(parsed)) throw new ReplayGap('the recorded args are not an object')
  return { ...JSON.parse(JSON.stringify(parsed)), resultMode: 'full' }
}

// -> the full processed result of the run in <transcriptDir>.
export async function replay(transcriptDir) {
  const run = locateRun(transcriptDir)
  const record = readRecord(run.record)
  const calls = callTable(record, readJournal(run.journal))
  const recorded = await runScript(record.script, copyArgs(record.args), calls)
  if (JSON.stringify(recorded) !== JSON.stringify(record.result)) {
    throw new ReplayGap('replaying run ' + run.runId + ' with its recorded args does not reproduce the recorded result, so its full result cannot be trusted')
  }
  return runScript(record.script, fullArgs(record.args), calls)
}

async function main(argv) {
  if (argv.length !== 1 || argv[0].startsWith('-')) {
    process.stderr.write('usage: node .claude/tools/workflow_replay.mjs <transcriptDir>\n')
    return 2
  }
  try {
    const result = await replay(argv[0])
    process.stdout.write(JSON.stringify(result) + '\n')
    return 0
  } catch (e) {
    process.stderr.write('workflow_replay: ' + (e instanceof ReplayGap ? e.message : (e && e.stack) || String(e)) + '\n')
    return 1
  }
}

const samePath = (a, b) => { try { return fs.realpathSync(a) === fs.realpathSync(b) } catch (e) { return false } }
if (process.argv[1] && samePath(process.argv[1], fileURLToPath(import.meta.url))) {
  main(process.argv.slice(2)).then(code => { process.exitCode = code })
}
