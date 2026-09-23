#!/usr/bin/env node
// Prove every agent prompt that review_fanout.js, explore_fanout.js and dispatch.js dispatch carries the
// relay line: the platform relays the triggering user message to each agent as authoritative, and a
// lens whose mandate looks unrelated answered that message instead of its brief.
const fs = require('fs')
const path = require('path')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const RELAY = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'

let passed = 0
let failed = 0
function check(label, condition, detail = '') {
  console.log((condition ? 'ok   ' : 'FAIL ') + label)
  if (condition) passed++
  else {
    failed++
    if (detail) console.log('     ' + detail)
  }
}

async function run(engine, args, returned) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'workflows', engine), 'utf8')
  const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '').replace('export const meta', 'const meta')
  const prompts = []
  const agent = async (prompt, opts) => {
    if (opts.phase !== 'Merge') prompts.push(prompt)
    return returned
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const result = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)(
    args, agent, parallel, () => {}, () => {})
  return { result, prompts }
}

const checked = { toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture' }
const cases = [
  ['review_fanout.js', {
    resultMode: 'full', consolidate: false,
    agents: ['one', 'two'].map(key => ({ key, prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose' })),
  }, { findings: [], checked, gaps: [] }],
  ['explore_fanout.js', {
    resultMode: 'full',
    lenses: ['one', 'two'].map(key => ({ key, prompt: 'Inspect.', model: 'opus', effort: 'low', agentType: 'general-purpose' })),
  }, { claims: [], checked }],
  ['dispatch.js', {
    jobs: ['one', 'two'].map(label => ({ label, promptPath: label + '.md', model: 'sonnet', effort: 'low', agentType: 'general-purpose' })),
  }, 'done'],
]

;(async () => {
  for (const [engine, args, returned] of cases) {
    try {
      const { result, prompts } = await run(engine, args, returned)
      check(engine + ' dispatches its agents', !(result && result.error) && prompts.length === 2, JSON.stringify(result).slice(0, 300))
      check(engine + ' gives every agent the relay line', prompts.length > 0 && prompts.every(p => p.includes(RELAY)))
    } catch (e) {
      check(engine + ' runs under the stub', false, e.stack)
    }
  }
  console.log('\n' + passed + ' pass, ' + failed + ' fail')
  process.exit(failed ? 1 : 0)
})()
