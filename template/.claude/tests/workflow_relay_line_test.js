#!/usr/bin/env node
// Prove every ordinary engine under workflows/ carries the relay line in its agent prompts: the platform
// relays the triggering user message to each agent as authoritative, and a lens whose mandate looks
// unrelated answered that message instead of its brief. The engine list is enumerated from git, not
// hand-listed, so a new engine without the line fails here; only the measurement workflows named in
// tools/orchestration_metrics.py MEASUREMENT_WORKFLOWS are skipped (their agents are a benchmark
// instrument's internal stages, not delegated work).
const fs = require('fs')
const path = require('path')
const { execFileSync } = require('child_process')
const engineRun = require('./_engine_run')
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] }
// The test pins the self-started verifier and merge steps through args.checker; the engine never picks them.
const HOST_CHECKER = { model: 'sonnet', effort: 'medium' }
const withHost = (args) => Object.prototype.hasOwnProperty.call(args || {}, '__transport') ? args : { ...args, checker: (args && args.checker) || HOST_CHECKER, __transport: HOST_TRANSPORT }
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
  const script = path.join(__dirname, '..', 'workflows', engine)
  const prompts = []
  const agent = async (prompt, opts) => {
    if (opts.phase !== 'Merge') prompts.push(prompt)
    return returned
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const result = await engineRun.load(script)(
    withHost(args), agent, parallel, () => {}, () => {})
  return { result, prompts }
}

// The skip list is READ from its owner, never copied: a copied list drifts and silently stops covering
// the engine a new measurement workflow adds.
function measurementWorkflows() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'tools', 'orchestration_metrics.py'), 'utf8')
  const block = src.match(/MEASUREMENT_WORKFLOWS = frozenset\(\{([\s\S]*?)\}\)/)
  if (!block) throw new Error('MEASUREMENT_WORKFLOWS not found in tools/orchestration_metrics.py')
  return new Set([...block[1].matchAll(/'([^']+)'/g)].map(m => m[1]))
}

// Every tracked engines/*.js present in the working tree — a tracked file deleted from the tree has no
// prompts to check, and an untracked one is not yet part of the battery. A project that tracks no engine
// yet (a fresh install, whose bootstrap stages only a few files) counts every untracked one.
function engines() {
  const skip = measurementWorkflows()
  const root = path.join(__dirname, '..', '..')
  const git = args => execFileSync('git', [...args, '--', '.claude/workflows'], { cwd: root, encoding: 'utf8' })
    .split('\n').filter(f => f.endsWith('.js'))
  const tracked = git(['ls-files'])
  const listed = tracked.length ? tracked : git(['ls-files', '--others', '--exclude-standard'])
  if (listed.length === 0) throw new Error('git ls-files returned no .claude/workflows/*.js')
  const out = []
  for (const rel of listed) {
    const abs = path.join(root, rel)
    if (!fs.existsSync(abs)) continue
    const src = fs.readFileSync(abs, 'utf8')
    const meta = src.match(/^export const meta = \{[\s\S]*?^\}/m)
    const name = meta && (meta[0].match(/name: '([^']+)'/) || [])[1]
    if (!name) throw new Error(rel + ': no meta.name to match MEASUREMENT_WORKFLOWS on')
    if (!skip.has(name)) out.push([rel, src])
  }
  return out
}

const checked = { toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture' }
const cases = [
  ['review_fanout.js', {
    resultMode: 'full',
    agents: ['one', 'two'].map(key => ({ key, prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose', mustRead: [{ path: 'fixture', lines: 1 }] })),
  }, { findings: [], checked, gaps: [] }],
  ['explore_fanout.js', {
    resultMode: 'full',
    lenses: ['one', 'two'].map(key => ({ key, prompt: 'Inspect.', model: 'opus', effort: 'low', agentType: 'general-purpose' })),
  }, { claims: [], checked }],
  ['dispatch.js', {
    jobs: ['one', 'two'].map(label => ({ label, promptPath: label + '.md', model: 'sonnet', effort: 'low', agentType: 'general-purpose' })),
  }, 'done'],
  ['dispatch_chains.js', {
    chains: ['one', 'two'].map(name => ({ name, jobs: [{ label: name, promptPath: name + '.md', model: 'sonnet', effort: 'low', agentType: 'general-purpose' }] })),
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
  // Every ordinary engine, enumerated rather than listed: the declaration must carry the exact literal
  // (a reworded copy is a different line) and be referenced again, since a declared-but-unused const
  // leaves the prompts bare.
  let covered = []
  try {
    covered = engines()
  } catch (e) {
    check('the engine enumeration runs', false, e.stack)
  }
  check('the enumeration reaches the ordinary engines', covered.length >= 4, 'found ' + covered.length)
  for (const [rel, src] of covered) {
    check(rel + ' declares the relay line', src.includes("const RELAY_LINE = '" + RELAY + "'"))
    check(rel + ' uses it in a prompt', src.split('RELAY_LINE').length > 2)
  }
  console.log('\n' + passed + ' pass, ' + failed + ' fail')
  process.exit(failed ? 1 : 0)
})()
