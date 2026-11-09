#!/usr/bin/env node
const fs = require('fs')
const path = require('path')

const src = fs.readFileSync(path.join(__dirname, '..', 'workflows', 'dispatch_chains.js'), 'utf8')
const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
// The rows the provider guard resolves from a job's `kind` into args.__toolProfiles (by label).
const ROW = { add: [], remove: [], overrideMode: 'shape-default', enforcement: 'inherited', effectiveMcp: 'unknown/inherited',
  mcpServers: ['semantic-search', 'ai-worker'] }
const KINDS = {
  reviewer: { ...ROW, kind: 'reviewer', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-review', tier: 'executor', guard: 'review' },
  author: { ...ROW, kind: 'author', builtins: ['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash'], agentType: 'lane-author', tier: 'executor', guard: 'author' },
}
const profilesOf = (args) => Object.fromEntries(((args && args.chains) || []).flatMap(c => c.jobs || [])
  .filter(j => j && KINDS[j.kind]).map(j => [j.label, KINDS[j.kind]]))
const withHost = (args) => Object.prototype.hasOwnProperty.call(args || {}, '__transport')
  ? { __toolProfiles: profilesOf(args), ...args }
  : { __toolProfiles: profilesOf(args), ...args, __transport: HOST_TRANSPORT }

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

async function run(args, agentResults = null) {
  const calls = []
  const prompts = []
  const logs = []
  const agent = async (prompt, opts) => {
    prompts.push(prompt)
    calls.push(opts)
    const value = agentResults ? agentResults[calls.length - 1] : { ok: true }
    if (value instanceof Error) throw value
    return value
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn).catch(() => null)))
  const fn = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)
  const result = await fn(withHost(args), agent, parallel, () => {}, line => logs.push(line))
  return { result, calls, prompts, logs }
}

;(async () => {
  const absent = await run({
    __transport: undefined,
    chains: [{ name: 'lane', jobs: [{ label: 'a', promptPath: 'a.md', model: 'opus', effort: 'low', kind: 'reviewer' }] }],
  })
  check('no injected vocabulary fails closed naming __transport',
    String(absent.result.error || '').includes('__transport') && absent.calls.length === 0, JSON.stringify(absent.result))
  const host = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'a', promptPath: 'a.md', model: 'opus', effort: 'low', kind: 'reviewer', tier: 'architect' }] }],
  })
  check('an injected transport with no declared efforts accepts its ids and `tier`', !host.result.error, JSON.stringify(host.result))
  check('the kind row sets the agent type passed to agent()', host.calls[0] && host.calls[0].agentType === 'lane-review', JSON.stringify(host.calls))
  check('PINS logs the declared tier beside the pin', host.logs.some(l => String(l).startsWith('PINS ') && String(l).includes('tier:architect')), JSON.stringify(host.logs))

  const codex = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    chains: [{ name: 'lane', jobs: [{ label: 'c', promptPath: 'c.md', model: 'gpt-5.6-luna', effort: 'max', kind: 'author' }] }],
  })
  check('provider ids replace Anthropic vocabulary', !codex.result.error, JSON.stringify(codex.result))
  check('provider job keeps its exact model', codex.calls[0] && codex.calls[0].model === 'gpt-5.6-luna', JSON.stringify(codex.calls))
  check('provider job accepts and forwards native max effort', codex.calls[0] && codex.calls[0].effort === 'max', JSON.stringify(codex.calls))

  const invalidTransports = [
    ['missing ids', { name: 'codex', efforts: ['max'] }],
    ['empty ids', { name: 'codex', ids: [], efforts: ['max'] }],
    ['malformed ids', { name: 'codex', ids: 'gpt-test', efforts: ['max'] }],
    ['non-string ids', { name: 'codex', ids: ['gpt-test', null], efforts: ['max'] }],
    ['empty efforts', { name: 'codex', ids: ['gpt-test'], efforts: [] }],
    ['malformed efforts', { name: 'codex', ids: ['gpt-test'], efforts: 'max' }],
    ['non-string efforts', { name: 'codex', ids: ['gpt-test'], efforts: ['max', null] }],
  ]
  for (const [label, __transport] of invalidTransports) {
    const invalidTransport = await run({
      __transport,
      chains: [{ name: 'lane', jobs: [{ label: 'invalid-transport', promptPath: 'x.md', model: 'gpt-test', effort: 'max', kind: 'reviewer' }] }],
    })
    check('invalid injected transport fails before chain dispatch: ' + label,
      !!invalidTransport.result.error && invalidTransport.calls.length === 0,
      JSON.stringify(invalidTransport.result))
  }

  const wrongProvider = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    chains: [{ name: 'lane', jobs: [{ label: 'bad-model', promptPath: 'x.md', model: 'opus', effort: 'low', kind: 'reviewer' }] }],
  })
  check('provider selector rejects another transport\'s names', !!wrongProvider.result.error, JSON.stringify(wrongProvider.result))

  const omittedEffort = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'default-effort', promptPath: 'x.md', model: 'opus', kind: 'reviewer' }] }],
  })
  check('omitted effort is not defaulted to medium where the transport declares no efforts',
    !omittedEffort.result.error && omittedEffort.calls[0] && omittedEffort.calls[0].effort === undefined,
    JSON.stringify({ result: omittedEffort.result, calls: omittedEffort.calls }))

  const invalidEffort = await run({
    __transport: { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] },
    chains: [{ name: 'lane', jobs: [{ label: 'bad-effort', promptPath: 'x.md', model: 'opus', effort: 'max', kind: 'reviewer' }] }],
  })
  check('present unsupported effort fails before dispatch on the native rungs',
    !!invalidEffort.result.error && invalidEffort.calls.length === 0,
    JSON.stringify(invalidEffort.result))

  const legacyXhigh = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'legacy-xhigh', promptPath: 'x.md', model: 'opus', effort: 'xhigh', kind: 'reviewer' }] }],
  })
  check('a transport with no declared efforts accepts the native xhigh rung',
    !legacyXhigh.result.error && legacyXhigh.calls[0] && legacyXhigh.calls[0].effort === 'xhigh',
    JSON.stringify(legacyXhigh.result))

  const badType = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'bad-type', promptPath: 'x.md', model: 'opus', effort: 'low', kind: 'nonesuch' }] }],
  })
  check('an unknown kind fails before dispatch', !!badType.result.error && badType.calls.length === 0, JSON.stringify(badType.result))

  const missingType = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'missing-type', promptPath: 'x.md', model: 'opus', effort: 'low' }] }],
  })
  check('a kindless job fails before dispatch', !!missingType.result.error && missingType.calls.length === 0, JSON.stringify(missingType.result))

  const retiredType = await run({
    chains: [{ name: 'lane', jobs: [{ label: 'retired', promptPath: 'x.md', model: 'opus', effort: 'low', kind: 'reviewer', agentType: 'Explore' }] }],
  })
  check('a job carrying agentType fails before dispatch, naming kind',
    /agentType/.test(String(retiredType.result.error)) && /kind/.test(String(retiredType.result.error)) && retiredType.calls.length === 0,
    JSON.stringify(retiredType.result))

  const stoppedLane = await run({
    chains: [{ name: 'lane', jobs: [
      { label: 'first', promptPath: 'first.md', model: 'opus', effort: 'low', kind: 'reviewer' },
      { label: 'second', promptPath: 'second.md', model: 'opus', effort: 'low', kind: 'reviewer' },
    ] }],
  }, [null, { ok: true }])
  check('a null result stops the dependent lane',
    stoppedLane.calls.length === 1 && stoppedLane.result.first
      && stoppedLane.result.first.error.includes('remaining jobs')
      && !Object.prototype.hasOwnProperty.call(stoppedLane.result, 'second'),
    JSON.stringify(stoppedLane))

  const rejectedLane = await run({
    chains: [{ name: 'lane', jobs: [
      { label: 'paid', promptPath: 'paid.md', model: 'opus', effort: 'low', kind: 'reviewer' },
      { label: 'rejected', promptPath: 'rejected.md', model: 'opus', effort: 'low', kind: 'reviewer' },
      { label: 'skipped', promptPath: 'skipped.md', model: 'opus', effort: 'low', kind: 'reviewer' },
    ] }],
  }, [{ paid: true }, new Error('transport rejection'), { skipped: false }])
  check('G2 a rejected agent keeps earlier paid output and a named lane-stop record',
    rejectedLane.calls.length === 2
      && rejectedLane.result.paid && rejectedLane.result.paid.paid === true
      && rejectedLane.result.rejected
      && rejectedLane.result.rejected.error.includes('remaining jobs')
      && rejectedLane.result.rejected.error.includes('rejected')
      && !Object.prototype.hasOwnProperty.call(rejectedLane.result, 'skipped'),
    JSON.stringify(rejectedLane))

  const mixedSpill = await run({
    spillDir: '.claude/scratch/chain-results',
    chains: [{ name: 'lane', jobs: [
      { label: 'scout', promptPath: 'scout.md', model: 'opus', effort: 'low', kind: 'reviewer' },
      { label: 'author', promptPath: 'author.md', model: 'opus', effort: 'low', kind: 'author' },
    ] }],
  })
  check('no-Write agent stays inline under spillDir',
    !mixedSpill.prompts[0].includes('Write your FULL deliverable')
      && mixedSpill.result.inlineLabels.includes('scout'), JSON.stringify(mixedSpill.result))
  check('write-capable agent keeps the spill contract',
    mixedSpill.prompts[1].includes('Write your FULL deliverable')
      && !mixedSpill.result.inlineLabels.includes('author'), mixedSpill.prompts[1])

  const caseFoldCollision = await run({
    spillDir: '.claude/scratch/chain-results',
    chains: [{ name: 'lane', jobs: [
      { label: 'Author', promptPath: 'one.md', model: 'opus', effort: 'low', kind: 'author' },
      { label: 'author', promptPath: 'two.md', model: 'opus', effort: 'low', kind: 'author' },
    ] }],
  })
  check('spill collisions case-fold for Windows',
    !!caseFoldCollision.result.error && caseFoldCollision.calls.length === 0,
    JSON.stringify(caseFoldCollision.result))

  console.log(`dispatch-chains transport: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(err => {
  console.error(err)
  process.exit(1)
})
