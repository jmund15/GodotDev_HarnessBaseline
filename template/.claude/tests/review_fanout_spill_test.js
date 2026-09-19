#!/usr/bin/env node
const fs = require('fs')
const path = require('path')

const src = fs.readFileSync(path.join(__dirname, '..', 'workflows', 'review_fanout.js'), 'utf8')
const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

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

const checked = {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture input was read'}
const withCoverage = value => {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !Object.prototype.hasOwnProperty.call(value, 'findings')) return value
  return {
    ...value,
    checked: Object.prototype.hasOwnProperty.call(value, 'checked') ? value.checked : checked,
    gaps: Object.prototype.hasOwnProperty.call(value, 'gaps') ? value.gaps : [],
  }
}

async function run(args, returned) {
  const prompts = []
  const calls = []
  const logs = []
  const agent = async (prompt, opts) => {
    prompts.push(prompt)
    calls.push({ prompt, opts })
    const value = (typeof returned === 'function') ? returned(prompt, opts) : returned
    return opts.phase === 'Merge' ? value : withCoverage(value)
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const fn = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)
  const result = await fn({ resultMode: 'full', ...args }, agent, parallel, () => {}, message => logs.push(message))
  return { result, prompts, calls, logs }
}

async function attempt(args, returned) {
  try { return { run: await run(args, returned), thrown: null } }
  catch (error) { return { run: null, thrown: error } }
}

const finding = (overrides = {}) => ({
  agent: 'lens', action: 'FIX', category: 'bug', critical: false,
  file: 'target.js:1', description: 'finding', old: null, new: null,
  question: null, options: null, scope: null, rationale: 'fixture rationale',
  ...overrides,
})

const reviewAgent = (key, overrides = {}) => ({
  key, prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose',
  ...overrides,
})

const sourceFindings = {
  'review:strong': finding({
    agent: 'strong', critical: true, action: 'FIX', category: 'bug',
    file: 'shared.js:1', description: 'strong source',
  }),
  'review:weak': finding({
    agent: 'weak', critical: false, action: 'PLAN', category: 'improvement',
    file: 'shared.js:2', description: 'weak source',
  }),
}

async function runMerge(mergedFindings, sources = sourceFindings) {
  return run({
    agents: [reviewAgent('strong'), reviewAgent('weak')],
    consolidate: true,
  }, (_prompt, opts) => opts.phase === 'Merge'
    ? { findings: mergedFindings }
    : { findings: [sources[opts.label]] })
}

function mergedFinding(overrides = {}) {
  return finding({
    agent: 'strong, weak', critical: true, action: 'FIX', category: 'bug',
    file: 'strong.js:1', description: 'merged source', merged_from: ['F1', 'F2'],
    ...overrides,
  })
}

function checkFallback(label, runResult, expected = ['strong source', 'weak source']) {
  const descriptions = (runResult.result.findings || []).map(f => f.description)
  check(label,
    descriptions.length === expected.length
      && descriptions.every((description, i) => description === expected[i])
      && runResult.result.flags.some(f => f.kind === 'consolidate-invalid'),
    JSON.stringify(runResult.result))
}

;(async () => {
  const clean = await run({
    agents: [reviewAgent('one')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, { findings: [] })
  const boundedClean = await run({
    agents: [reviewAgent('one')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
    resultMode: 'bounded',
  }, { findings: [] })
  check('legal repository-relative scratch spill root runs', !clean.result.error, JSON.stringify(clean.result))
  check('read-only contract carves out only the lens spill',
    clean.prompts[0] && clean.prompts[0].includes('EXCEPT your own spill file .claude/scratch/review-spills/review_one.spill.md'),
    clean.prompts[0])
  check('lens writes complete schema result before returning',
    clean.prompts[0] && clean.prompts[0].includes('Write your FULL JSON deliverable') && clean.prompts[0].includes('BEFORE returning'),
    clean.prompts[0])
  check('clean result reports spill evidence',
    clean.result.spills && clean.result.spills.one === '.claude/scratch/review-spills/review_one.spill.md'
      && boundedClean.result.delivery.payloadComplete === false
      && boundedClean.result.delivery.preview.spillMetadata.total === 2
      && boundedClean.result.delivery.preview.spillMetadata.omitted === 2,
    JSON.stringify({ full: clean.result, bounded: boundedClean.result }))
  check('workflow evidence label matches review:<key>',
    clean.calls[0] && clean.calls[0].opts.label === 'review:one',
    JSON.stringify(clean.calls.map(call => call.opts)))
  check('required agentType forwards to the review agent',
    clean.calls[0] && clean.calls[0].opts.agentType === 'general-purpose',
    JSON.stringify(clean.calls.map(call => call.opts)))

  const noWrite = await run({
    agents: [reviewAgent('scout', { agentType: 'Explore' })],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, { findings: [] })
  check('no-Write review agents return inline without an impossible spill contract',
    !noWrite.result.error
      && noWrite.prompts[0].includes('Read-only: do NOT modify, create, or delete any file.')
      && !noWrite.prompts[0].includes('Write your FULL JSON deliverable')
      && Array.isArray(noWrite.result.inlineLabels)
      && noWrite.result.inlineLabels.includes('scout')
      && !('scout' in (noWrite.result.spills || {})),
    JSON.stringify({ result: noWrite.result, prompt: noWrite.prompts[0] }))

  const deadNoWrite = await run({
    agents: [reviewAgent('dead-scout', { agentType: 'Plan' })],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, null)
  const deadNoWriteFlag = deadNoWrite.result.flags.find(f => f.kind === 'lens-no-return')
  check('no-Write recovery never claims a spill file exists',
    deadNoWriteFlag && !deadNoWriteFlag.detail.includes('review_dead-scout.spill.md')
      && deadNoWriteFlag.detail.includes('/salvage_fanout'),
    JSON.stringify(deadNoWrite.result))

  const dead = await run({
    agents: [reviewAgent('dead')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, null)
  const deadFlag = dead.result.flags && dead.result.flags.find(f => f.kind === 'lens-no-return')
  check('null return names deterministic spill recovery path',
    deadFlag && deadFlag.detail.includes('.claude/scratch/review-spills/review_dead.spill.md'),
    JSON.stringify(dead.result))

  const legacy = await run({ agents: [reviewAgent('legacy')], consolidate: false }, { findings: [] })
  check('omitted spillDir keeps prior read-only contract',
    !legacy.result.error && legacy.prompts[0].includes('Read-only: do NOT modify, create, or delete any file.') && !legacy.prompts[0].includes('EXCEPT your own spill file'),
    legacy.prompts[0])
  check('omitted spillDir adds no spill metadata', !('spillDir' in legacy.result), JSON.stringify(legacy.result))

  const illegalRoots = [
    'reports/review-spills',
    '.claude/scratch/../../outside',
    'C:/repo/.claude/scratch/review-spills',
    'project/.claude/scratch/review-spills',
    'C:/Users/test/AppData/Local/Temp/claude/review-spills',
  ]
  for (const spillDir of illegalRoots) {
    const invalidRoot = await run({
      agents: [reviewAgent('bad')], spillDir, consolidate: false,
    }, { findings: [] })
    check('spillDir rejects non-contained root: ' + spillDir,
      !!invalidRoot.result.error && invalidRoot.prompts.length === 0,
      JSON.stringify(invalidRoot.result))
  }

  const duplicate = await run({
    agents: [reviewAgent('same'), reviewAgent('same')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, { findings: [] })
  check('duplicate lens keys fail before dispatch',
    !!duplicate.result.error && duplicate.prompts.length === 0 && duplicate.result.error.includes('same'),
    JSON.stringify(duplicate.result))

  const collision = await run({
    agents: [reviewAgent('a:b'), reviewAgent('a_b')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, { findings: [] })
  check('sanitized spill-path collisions fail before dispatch',
    !!collision.result.error && collision.prompts.length === 0,
    JSON.stringify(collision.result))

  const caseFoldCollision = await run({
    agents: [reviewAgent('Lens'), reviewAgent('lens')],
    spillDir: '.claude/scratch/review-spills',
    consolidate: false,
  }, { findings: [] })
  check('spill-path collisions case-fold for Windows',
    !!caseFoldCollision.result.error && caseFoldCollision.prompts.length === 0,
    JSON.stringify(caseFoldCollision.result))

  const missingKey = await run({
    agents: [{ prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose' }],
    consolidate: false,
  }, { findings: [] })
  check('missing lens key fails before dispatch',
    !!missingKey.result.error && missingKey.prompts.length === 0,
    JSON.stringify(missingKey.result))

  const missingAgentType = await run({
    agents: [{ key: 'missing-type', prompt: 'Review it.', model: 'sonnet', effort: 'medium' }],
    consolidate: false,
  }, { findings: [] })
  check('missing agentType fails before dispatch',
    !!missingAgentType.result.error && missingAgentType.prompts.length === 0,
    JSON.stringify(missingAgentType.result))

  for (const badAgent of [null, []]) {
    const invalidAgent = await attempt({ agents: [badAgent], consolidate: false }, { findings: [] })
    check('invalid review agent object fails legibly before property access: ' + JSON.stringify(badAgent),
      !invalidAgent.thrown && invalidAgent.run && !!invalidAgent.run.result.error
        && invalidAgent.run.prompts.length === 0,
      invalidAgent.thrown ? String(invalidAgent.thrown.stack || invalidAgent.thrown) : JSON.stringify(invalidAgent.run.result))
  }

  const codexMax = await run({
    agents: [reviewAgent('codex', { model: 'gpt-test', effort: 'max' })],
    consolidate: false,
    __transport: {
      name: 'codex', ids: ['gpt-test'], default: 'gpt-test',
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
  }, { findings: [] })
  check('injected Codex max effort is accepted and forwarded',
    !codexMax.result.error && codexMax.calls[0] && codexMax.calls[0].opts.effort === 'max',
    JSON.stringify({ result: codexMax.result, calls: codexMax.calls.map(call => call.opts) }))

  const invalidTransports = [
    ['missing ids', { name: 'codex', default: 'gpt-test', efforts: ['max'] }],
    ['empty ids', { name: 'codex', ids: [], default: 'gpt-test', efforts: ['max'] }],
    ['malformed ids', { name: 'codex', ids: 'gpt-test', default: 'gpt-test', efforts: ['max'] }],
    ['non-string ids', { name: 'codex', ids: ['gpt-test', null], default: 'gpt-test', efforts: ['max'] }],
    ['missing efforts', { name: 'codex', ids: ['gpt-test'], default: 'gpt-test' }],
    ['empty efforts', { name: 'codex', ids: ['gpt-test'], default: 'gpt-test', efforts: [] }],
    ['malformed efforts', { name: 'codex', ids: ['gpt-test'], default: 'gpt-test', efforts: 'max' }],
    ['non-string efforts', { name: 'codex', ids: ['gpt-test'], default: 'gpt-test', efforts: ['max', null] }],
    ['missing default', { name: 'codex', ids: ['gpt-test'], efforts: ['max'] }],
    ['empty default', { name: 'codex', ids: ['gpt-test'], default: '', efforts: ['max'] }],
    ['default outside ids', { name: 'codex', ids: ['gpt-test'], default: 'other', efforts: ['max'] }],
  ]
  for (const [label, __transport] of invalidTransports) {
    const invalidTransport = await run({
      agents: [reviewAgent('invalid-transport', { model: 'gpt-test', effort: 'max' })],
      consolidate: false,
      __transport,
    }, { findings: [] })
    check('invalid injected transport fails before review dispatch: ' + label,
      !!invalidTransport.result.error && invalidTransport.prompts.length === 0,
      JSON.stringify(invalidTransport.result))
  }

  const omittedEffort = await run({
    agents: [reviewAgent('default-effort', { effort: undefined })],
    consolidate: false,
  }, { findings: [] })
  check('omitted review effort takes the medium default',
    !omittedEffort.result.error && omittedEffort.calls[0] && omittedEffort.calls[0].opts.effort === 'medium',
    JSON.stringify({ result: omittedEffort.result, calls: omittedEffort.calls.map(call => call.opts) }))

  const invalidEffort = await run({
    agents: [reviewAgent('bad-effort', { effort: 'max' })],
    consolidate: false,
  }, { findings: [] })
  check('present unsupported review effort fails before dispatch',
    !!invalidEffort.result.error && invalidEffort.prompts.length === 0,
    JSON.stringify(invalidEffort.result))

  for (const model of [null, '', 0, false]) {
    const invalidModel = await run({
      agents: [reviewAgent('falsey-model', { model })],
      consolidate: false,
    }, { findings: [] })
    check('present falsey review model fails before dispatch: ' + JSON.stringify(model),
      !!invalidModel.result.error && invalidModel.prompts.length === 0,
      JSON.stringify(invalidModel.result))
  }

  const validMerge = await runMerge([mergedFinding()])
  check('valid semantic merge is accepted',
    validMerge.result.findings.length === 1
      && !validMerge.result.flags.some(f => f.kind === 'consolidate-invalid'),
    JSON.stringify(validMerge.result))

  checkFallback('unknown provenance id falls back to the deterministic list',
    await runMerge([mergedFinding({ merged_from: ['F1', 'F2', 'F9'] })]))
  checkFallback('empty provenance id falls back to the deterministic list',
    await runMerge([mergedFinding({ merged_from: ['F1', 'F2', ''] })]))
  checkFallback('empty provenance list falls back to the deterministic list',
    await runMerge([
      mergedFinding({ description: 'first merged', merged_from: [] }),
      mergedFinding({ description: 'second merged', merged_from: ['F1', 'F2'] }),
    ]))
  checkFallback('duplicate provenance id falls back to the deterministic list',
    await runMerge([
      mergedFinding({ description: 'first merged', merged_from: ['F1'] }),
      mergedFinding({ description: 'second merged', merged_from: ['F1', 'F2'] }),
    ]))
  checkFallback('merge cannot lower strongest critical value',
    await runMerge([mergedFinding({ critical: false })]))
  checkFallback('merge cannot lower strongest action tier',
    await runMerge([mergedFinding({ action: 'PLAN' })]))
  checkFallback('merge cannot lower strongest category tier',
    await runMerge([mergedFinding({ category: 'improvement' })]))

  const weakSources = {
    'review:strong': finding({
      agent: 'weak-one', critical: false, action: 'PLAN', category: 'improvement',
      file: 'weak-shared.js:1', description: 'weak source one',
    }),
    'review:weak': finding({
      agent: 'weak-two', critical: false, action: 'PLAN', category: 'improvement',
      file: 'weak-shared.js:2', description: 'weak source two',
    }),
  }
  const weakDescriptions = ['weak source one', 'weak source two']
  checkFallback('merge cannot raise strongest critical value',
    await runMerge([mergedFinding({ critical: true, action: 'PLAN', category: 'improvement' })], weakSources), weakDescriptions)
  checkFallback('merge cannot raise strongest action tier',
    await runMerge([mergedFinding({ critical: false, action: 'FIX', category: 'improvement' })], weakSources), weakDescriptions)
  checkFallback('merge cannot raise strongest category tier',
    await runMerge([mergedFinding({ critical: false, action: 'PLAN', category: 'bug' })], weakSources), weakDescriptions)

  const offTransport = await run({
    agents: [
      reviewAgent('one', { model: 'gpt-test' }),
      reviewAgent('two', { model: 'gpt-test' }),
    ],
    consolidate: true,
    __transport: {
      name: 'codex', ids: ['gpt-test'], default: 'gpt-test',
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
  }, (_prompt, opts) => opts.phase === 'Merge'
    ? { findings: [mergedFinding()] }
    : { findings: [finding({ file: opts.label + '.js:1', description: opts.label, agent: opts.label })] })
  const mergeCall = offTransport.calls.find(call => call.opts.phase === 'Merge')
  check('consolidation log names the model passed to agent()',
    mergeCall && mergeCall.opts.model === 'gpt-test'
      && offTransport.logs.some(line => line.includes('"review:consolidate":"gpt-test/low/general-purpose"')),
    JSON.stringify({ calls: offTransport.calls.map(call => call.opts), logs: offTransport.logs }))

  console.log(`review-fanout spills: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(err => {
  console.error(err)
  process.exit(1)
})
