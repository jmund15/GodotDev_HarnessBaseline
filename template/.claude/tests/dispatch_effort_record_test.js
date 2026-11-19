// Proof for the Workflow route's run record: dispatch.js logs each job's requested effort and case on its PINS line
// (`req:`, `case:`) and its justification on a JUSTIFICATION line, which orchestration_metrics.pin_tokens reads.
// Runs the engine source on planted args and captures its log lines.
const fs = require('fs')
const path = require('path')
const assert = require('node:assert/strict')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(path.join(__dirname, '../workflows/dispatch.js'), 'utf8').replace('export const meta', 'const meta')
const transport = {name: 'codex', ids: ['planted'], default: 'planted', efforts: ['low', 'max']}
async function run(args) {
  const logs = []
  const agent = async () => 'done'
  const output = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', source)(
    require('./_engine_run').withKinds(args), agent, fs => Promise.all(fs.map(f => f())), () => {}, line => logs.push(line))
  return {output, logs}
}
const pinsValue = (logs, label) => {
  const line = logs.find(l => l.startsWith('PINS '))
  return JSON.parse(line.slice(5))[label]
}
;(async () => {
  let failures = 0
  const cases = [
    ['a capped job names its requested effort and case', {
      jobs: [{label: 'a', promptPath: 'x.md', model: 'planted', effort: 'max', kind: 'reviewer',
              effortCases: {planted: {requestedEffort: 'max', effortCase: 'capped'}}}],
      __transport: transport}, logs => {
      assert.match(pinsValue(logs, 'a'), /\breq:max\b/)
      assert.match(pinsValue(logs, 'a'), /\bcase:capped\b/)
    }],
    ['a job with no record names both as a dash', {
      jobs: [{label: 'b', promptPath: 'x.md', model: 'planted', effort: 'low', kind: 'reviewer'}],
      __transport: transport}, logs => {
      assert.match(pinsValue(logs, 'b'), /req:-(\s|$)/)
      assert.match(pinsValue(logs, 'b'), /case:-(\s|$)/)
    }],
    ['a job justification logs on its own line, never in the PINS value', {
      jobs: [{label: 'c', promptPath: 'x.md', model: 'planted', effort: 'low', kind: 'reviewer',
              justification: 'pick: owner the brief is long'}],
      __transport: transport}, logs => {
      const line = logs.find(l => l.startsWith('JUSTIFICATION '))
      assert.deepEqual(JSON.parse(line.slice('JUSTIFICATION '.length)), {c: 'pick: owner the brief is long'})
      assert.doesNotMatch(pinsValue(logs, 'c'), /justification/)
    }],
  ]
  for (const [name, args, check] of cases) {
    try {
      const r = await run(args)
      assert.ok(!r.output.error, r.output.error)
      check(r.logs)
      console.log('ok   ' + name)
    } catch (e) { failures++; console.log('FAIL ' + name + ': ' + e.message) }
  }
  if (failures) process.exit(1)
})()
