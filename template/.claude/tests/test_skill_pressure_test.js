// Proof that test_skill_pressure pins every agent it launches: the synthesizer included, which runs the scorer's judgment pin.
// Run: node .claude/tests/test_skill_pressure_test.js
const assert = require('node:assert/strict')
const path = require('path')
const engineRun = require('./_engine_run')

const TRANSPORT = { name: 'host', ids: ['opus', 'sonnet'], efforts: ['low', 'high'] }
const ARGS = { kind: 'reviewer', skillName: 's', skillContent: 'Rule: never skip the proof.',
  subject: { model: 'sonnet', effort: 'low' }, scorer: { model: 'opus', effort: 'high' }, validator: { model: 'opus', effort: 'low' },
  __transport: TRANSPORT }

;(async () => {
  const calls = []
  const agent = async (_prompt, opts) => { calls.push(opts); return opts.label.startsWith('synthesize:')
    ? { items: [{ source: 'r1', prompt: 'p', excerpt: 'e' }] } : { verdict: 'HOLDS', validated: true, reason: 'r', response: 'x' } }
  // `pipeline` is a Workflow-runtime global the host harness does not provide, so the run ends there, after the synthesizer.
  await engineRun.load(path.join(__dirname, '../workflows/test_skill_pressure.js'))(
    engineRun.withKinds(ARGS), agent, fs => Promise.all(fs.map(f => f())), () => {}, () => {}).catch(e => assert.match(e.message, /pipeline is not defined/))
  assert.equal(calls.length, 1, 'the synthesizer is the one call before the pipeline')
  const synth = calls.find(o => o.label.startsWith('synthesize:'))
  assert.deepEqual([synth.model, synth.effort], ['opus', 'high'], 'the synthesizer runs the scorer pin')
  console.log('ok   the test_skill_pressure synthesizer is pinned')
})().catch(e => { console.log('FAIL ' + e.message); process.exit(1) })
