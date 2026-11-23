// Proof that the fan-out engines send every delegate the one rails text the entry injects (`args.__railsText`,
// from guards/rails.md, T4). No model or kind gets its own tier, and no prompt points the delegate at a file to Read.
// A call without the text refuses before any agent starts, so a delegate never runs without its rules.
// Run: node .claude/tests/engine_rails_test.js
const path = require('path')
const engineRun = require('./_engine_run')

const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] }
const RAILS = 'RAILS-MARK standing rule for every delegate.'
const JOB = { label: 'j', promptPath: 'p.md', model: 'fable', effort: 'medium', kind: 'reviewer' }

async function prompts(file, args) {
  const seen = []
  const agent = async (prompt) => {
    seen.push(String(prompt))
    return { findings: [], claims: [], gaps: [], checked: { toolsUsed: ['Read:x'], stoppedAt: 'done', basis: 'fixture' } }
  }
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f()))
  const result = await engineRun.load(path.join(__dirname, '../workflows', file))(
    engineRun.withKinds({ __transport: HOST_TRANSPORT, ...args }), agent, parallel, () => {}, () => {})
  return { seen, result }
}

;(async () => {
  let failed = 0
  let total = 0
  const check = (name, ok, detail) => {
    total++
    if (!ok) failed++
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok || !detail ? '' : '   [' + detail + ']'))
  }
  const count = (text, needle) => text.split(needle).length - 1

  for (const model of ['fable', 'opus', 'sonnet']) {
    const { seen } = await prompts('dispatch.js', { __railsText: RAILS, jobs: [{ ...JOB, model }] })
    const prompt = seen[0] || ''
    check('dispatch.js: a ' + model + ' job carries the rails text once', count(prompt, RAILS) === 1, prompt.slice(-200))
    check('dispatch.js: a ' + model + ' job reads no tier section by pointer', !/`## (minimal|condensed|detailed)`/.test(prompt) && !prompt.includes('Read .claude/guards/'))
  }

  const refused = await prompts('dispatch.js', { __railsText: '', jobs: [{ ...JOB }] })
  check('dispatch.js: an empty __railsText refuses naming the entry', String(refused.result && refused.result.error).includes('__railsText') && refused.seen.length === 0, JSON.stringify(refused.result))

  const lens = await prompts('explore_fanout.js', { __railsText: RAILS, lenses: [{ key: 'k', promptPath: 'p.md', model: 'fable', effort: 'medium', kind: 'lens' }], contextPrefixPath: 'ctx.md' })
  const lensPrompt = lens.seen[0] || ''
  check('explore_fanout.js: the lens prompt carries the rails text once', count(lensPrompt, RAILS) === 1, lensPrompt.slice(-200))

  console.log('\n' + (total - failed) + '/' + total + ' passed')
  process.exit(failed ? 1 : 0)
})()
