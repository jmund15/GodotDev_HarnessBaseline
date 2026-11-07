#!/usr/bin/env node
// Proof: dispatch.js and dispatch_chains.js pass a job's `schema` to agent(), add one read line per
// `contextPaths` entry after the shared one, and keep the spill and choices contracts off a schema job,
// whose return is an object. DISPATCH_WORKFLOWS_DIR points the proof at another copy of the engines.
const fs = require('fs')
const path = require('path')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const HOST = { name: 'host', ids: ['model-a', 'model-b'], default: 'model-b' }
const DIR = process.env.DISPATCH_WORKFLOWS_DIR || path.join(__dirname, '..', 'workflows')
const body = (name) => fs.readFileSync(path.join(DIR, name), 'utf8').replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')

let passed = 0
let failed = 0
const check = (label, ok, detail) => {
  console.log((ok ? 'ok   ' : 'FAIL ') + label)
  if (ok) passed++
  else { failed++; if (detail) console.log('     ' + detail) }
}

const SCHEMA = { type: 'object', properties: { verdict: { type: 'string' } }, required: ['verdict'] }
const PLANTED = { verdict: 'ok' }

async function run(script, args) {
  const calls = []
  const prompts = []
  const agent = async (prompt, opts) => { prompts.push(prompt); calls.push(opts); return opts.schema ? PLANTED : 'done' }
  const parallel = async thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn)))
  let result
  try {
    result = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body(script))(
      { __transport: HOST, ...args }, agent, parallel, () => {}, () => {})
  } catch (e) {
    result = { crash: String((e && e.stack) || e) }
  }
  return { result, calls, prompts }
}

const job = (label, extra) => Object.assign({ label, promptPath: label + '.md', model: 'model-a', effort: 'low', agentType: 'general-purpose' }, extra)
const argsFor = (script, jobs, extra) => Object.assign({ spillDir: '.claude/scratch/s7/spill', contextPath: 'shared.md' },
  script === 'dispatch.js' ? { jobs } : { chains: [{ name: 'lane', jobs }] }, extra)
const readLine = (p) => 'SHARED CONTEXT for this dispatch is at: ' + p + ' — read it with the Read tool FIRST'

;(async () => {
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const r = await run(script, argsFor(script, [
      job('sch', { schema: SCHEMA, contextPaths: ['a.md', 'b.md'] }), job('plain')]))
    const sch = r.calls.findIndex(o => o.label === 'sch')
    const plain = r.calls.findIndex(o => o.label === 'plain')
    check(script + ' runs', !r.result.crash && sch >= 0 && plain >= 0, JSON.stringify(r.result).slice(0, 400))
    if (r.result.crash) continue
    check(script + ' passes schema to agent()', JSON.stringify(r.calls[sch].schema) === JSON.stringify(SCHEMA))
    check(script + ' leaves a job without schema alone', !('schema' in r.calls[plain]))
    const p = r.prompts[sch]
    const at = [readLine('shared.md'), readLine('a.md'), readLine('b.md')].map(l => p.indexOf(l))
    check(script + ' reads shared, then each contextPaths entry in order',
      at.every(i => i >= 0) && at[0] < at[1] && at[1] < at[2] && p.indexOf('Your full task brief') > at[2], JSON.stringify(at))
    check(script + ' adds no contextPaths line to a job without them',
      !r.prompts[plain].includes('a.md') && r.prompts[plain].includes(readLine('shared.md')))
    check(script + ' gives a schema job no spill or choices contract',
      !p.includes('RETURN-PATH CONTRACT') && !p.includes('=== CHOICES') && r.prompts[plain].includes('RETURN-PATH CONTRACT'))
    const flat = JSON.stringify(r.result)
    check(script + ' returns the schema job\'s object whole', flat.includes('"verdict":"ok"'), flat.slice(0, 400))
  }
  console.log('dispatch native fields: ' + passed + ' passed, ' + failed + ' failed')
  process.exit(failed ? 1 : 0)
})()
