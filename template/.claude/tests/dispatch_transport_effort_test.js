const fs = require('fs')
const path = require('path')
const assert = require('node:assert/strict')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(path.join(__dirname, '../workflows/dispatch.js'), 'utf8').replace('export const meta', 'const meta')
const job = (model, effort) => ({label: 'fixture', promptPath: 'fixture.md', model, effort, agentType: 'Explore'})
async function run(args) {
  const calls = []
  const agent = async (_prompt, opts) => { calls.push(opts); return 'done' }
  const output = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', source)(
    args, agent, fs => Promise.all(fs.map(f => f())), () => {}, () => {})
  return {output, calls}
}
;(async () => {
  const transport = {name: 'codex', ids: ['gpt-test'], default: 'gpt-test', efforts: ['low', 'max']}
  let failures = 0
  for (const [name, args, accepted] of [
    ['native max forwarded', {jobs: [job('gpt-test', 'max')], __transport: transport}, true],
    ['native capability list governs', {jobs: [job('gpt-test', 'medium')], __transport: transport}, false],
    ['host max still rejected', {jobs: [job('sonnet', 'max')]}, false],
    ['empty injected ids cannot reopen host vocabulary', {jobs: [job('sonnet', 'low')], __transport: {...transport, ids: []}}, false],
    ['empty injected efforts fail before dispatch', {jobs: [job('gpt-test', 'low')], __transport: {...transport, efforts: []}}, false],
  ]) {
    try {
      const r = await run(args)
      assert.equal(!r.output.error, accepted)
      assert.equal(r.calls.length, accepted ? 1 : 0)
      if (accepted) assert.equal(r.calls[0].effort, 'max')
      console.log('ok   ' + name)
    } catch (e) { failures++; console.log('FAIL ' + name + ': ' + e.message) }
  }
  console.log('dispatch transport: ' + (5 - failures) + '/5 passed')
  process.exit(failures ? 1 : 0)
})()
