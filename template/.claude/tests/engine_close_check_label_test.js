// Proof: the close check's verifier run label names its own run. The verifier finds its journal by that label, so
// two runs whose findings share ids (every review numbers its findings F1..Fn) must not share it; a resumed run with
// the same inputs keeps its label, so its cached verifier replays.
const assert = require('assert/strict')
const path = require('path')
const { load } = require('./_engine_run')
const root = path.resolve(__dirname, '../..')
const run = load(path.join(root, '.claude/workflows/dispatch.js'))

const text = '```claims\n' + JSON.stringify({ id: 'F1', confidence: 'verified', evidence: [] }) + '\n```'
const base = {
  jobs: [{ label: 'tracer', promptPath: 'brief.md', model: 'test-producer', effort: 'low', kind: 'lens', railTier: 'none' }],
  __transport: { name: 'test', ids: ['test-producer', 'test-verifier'], efforts: ['none', 'low', 'high'] },
  checker: { model: 'test-verifier', effort: 'low' },
  __returns: { tracer: 'claims' },
  __toolProfiles: { verify: { agentType: 'lane-verify', tier: 'fanout' },
    tracer: { kind: 'lens', builtins: ['Read', 'Glob', 'Grep'], mcpServers: [], add: [], remove: [], agentType: 'lane-survey', tier: 'fanout', guard: 'survey' } },
}

async function verifierLabel(spillDir) {
  let label = null
  const args = Object.assign(JSON.parse(JSON.stringify(base)), spillDir ? { spillDir } : {})
  await run(args, async (_prompt, opts) => {
    if (opts.label === 'tracer') return text
    label = opts.label
    return null
  }, tasks => Promise.all(tasks.map(f => f())), () => {}, () => {})
  return label
}

;(async () => {
  const a = await verifierLabel('.claude/scratch/label-a')
  const b = await verifierLabel('.claude/scratch/label-b')
  const again = await verifierLabel('.claude/scratch/label-a')
  assert.ok(a && a.startsWith('verify-'), 'the verifier ran under a verify- label: ' + a)
  assert.notEqual(a, b, 'two runs with the same finding ids and different spill dirs share a verifier label')
  assert.equal(a, again, 'the same inputs keep their label, so a resumed verifier replays')
  console.log('ok engine_close_check_label_test: ' + a + ' / ' + b)
})().catch(error => { console.error(error); process.exit(1) })
