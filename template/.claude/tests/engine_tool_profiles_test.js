// Proof that every loadable fan-out engine logs a TOOL-PROFILES entry for each child it actually
// spawns, label-matched, and marks a native child's enforcement `inherited` unless the engine
// launched it as its row's generated agent type (`agent-type`).
//
// The hook (hooks/workflow_provider_guard.py) resolves each job's `kind` into args.__toolProfiles (by label)
// and a single-kind engine's `args.kind` into args.__kindProfile. The runtime keeps log() output in
// <session>/workflows/<runId>.json, so the journal is what a later reader sees. Nothing here proves
// the runtime RESTRICTED a native child's tools; `agent-type` records only that the engine chose the
// generated type, whose tool list the live gate proves.
//
// Only the shipped engines are listed here; a project's own engine proof requires `_engine_tool_profiles`
// and adds its row.
//
// Run: node .claude/tests/engine_tool_profiles_test.js
const fs = require('fs')
const path = require('path')
const { run, toolProfilesLine, checkEngineMatrix, P, KINDED, PROFILED, JOB } = require('./_engine_tool_profiles')

const ENGINES = {
  'dispatch.js': { perJob: true, labels: ['j', 'k'],
    build: (kind) => ({ jobs: [{ ...JOB, label: 'j', kind }, { ...JOB, label: 'k', model: 'sonnet', kind }],
      __toolProfiles: kind ? { j: P[kind], k: P[kind], ...PROFILED } : { ...PROFILED } }) },
  'dispatch_chains.js': { perJob: true, labels: ['j'],
    build: (kind) => ({ chains: [{ name: 'c', jobs: [{ ...JOB, label: 'j', kind }] }],
      __toolProfiles: kind ? { j: P[kind], ...PROFILED } : { ...PROFILED } }) },
  'review_fanout.js': { perJob: true, labels: ['review:k'],
    build: (kind) => ({ agents: [{ key: 'k', promptPath: 'p.md', model: 'fable', effort: 'medium', kind, mustRead: [{ path: 'x', lines: 1 }] }],
      __toolProfiles: kind ? { k: P[kind], ...PROFILED } : { ...PROFILED } }) },
  'doc_architecture_audit.js': { labels: ['da-structural', 'da-crossref', 'da-domain'],
    build: (kind) => ({ context: 'ctx', docRoot: 'root', model: 'sonnet', effort: 'medium', tier: 'fanout', kind, __kindProfile: kind ? P[kind] : undefined }) },
  'idea_fanout.js': { labels: ['gen:t', 'gen:u'],
    build: (kind) => ({ generators: [{ key: 't', instr: 'i' }, { key: 'u', instr: 'j' }], contextPrefix: 'ctx', kind, __kindProfile: kind ? P[kind] : undefined }) },
  'worklog_relevance.js': { labels: ['worklog:relevance'],
    build: (kind) => ({ scope: 's', model: 'sonnet', effort: 'medium', kind, __kindProfile: kind ? P[kind] : undefined }) },
}

;(async () => {
  let failed = 0
  let total = 0
  const check = (name, ok, detail) => {
    total++
    if (!ok) failed++
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok || !detail ? '' : '   [' + detail + ']'))
  }

  const typeOf = (r, label) => (r.calls.find((c) => c.label === label) || {}).agentType
  const errOf = (r) => String((r.result && r.result.error) || '')

  for (const [file, e] of Object.entries(ENGINES)) {
    await checkEngineMatrix(file, e, check)
  }

  // A job still carrying a retired key is refused with the replacement named, before any agent() call.
  for (const [file, key, value] of [['dispatch.js', 'agentType', 'general-purpose'], ['dispatch.js', 'shape', 'review'],
    ['dispatch.js', 'readOnly', true], ['dispatch_chains.js', 'agentType', 'Explore'],
    ['review_fanout.js', 'shape', 'review']]) {
    const a = ENGINES[file].build('reviewer')
    for (const site of ['jobs', 'lenses', 'agents']) if (a[site]) a[site][0][key] = value
    if (a.chains) a.chains[0].jobs[0][key] = value
    const r = await run(file, a)
    check(file + ': a job carrying ' + key + ' is refused naming the replacement',
      r.calls.length === 0 && new RegExp(key).test(errOf(r)) && /kind/.test(errOf(r)), errOf(r))
  }

  // A job whose kind the hook did not resolve (no __toolProfiles entry) fails closed: the engine infers nothing.
  for (const file of ['dispatch.js', 'dispatch_chains.js', 'review_fanout.js']) {
    const a = ENGINES[file].build('reviewer')
    a.__toolProfiles = { ...PROFILED }
    const r = await run(file, a)
    check(file + ': a kind with no resolved profile starts no agent', r.calls.length === 0 && /kind/.test(errOf(r)), errOf(r))
  }

  // An author seat of the review engine runs as lane-author and, holding Write, spills.
  for (const [file, label] of [['review_fanout.js', 'review:k']]) {
    const r = await run(file, { ...ENGINES[file].build('author'), spillDir: '.claude/scratch/author_seat' })
    check(file + ': an author seat runs as lane-author and gets the spill contract',
      typeOf(r, label) === 'lane-author' && /\.claude\/scratch\/author_seat/.test((r.calls.find((c) => c.label === label) || {}).prompt || ''), errOf(r))
  }

  // A tier comes from the kind row: dispatch.js records it on the PINS row over any tier the job writes.
  const tiered = await run('dispatch.js', { ...ENGINES['dispatch.js'].build('author'), tier: 'fanout' })
  const pinsLine = tiered.logs.find((m) => m.startsWith('PINS ')) || ''
  check('dispatch.js: the PINS row records the kind row tier, not a tier the call writes',
    /lane-author tier:executor/.test(pinsLine), pinsLine.slice(0, 300))

  // The concurrency line is chosen by whether the child can write, not by its shell: every public kind holds Bash,
  // so dispatch.js's writer and read-only variants are dispatch_spill_root_test.js's. This engine is read-only, so
  // each concurrent child carries the read-only variant, which tells it to run a cited proof through machine_slots.
  const readOnlyLine = JSON.parse(fs.readFileSync(path.join(__dirname, '../guards/single_flight.json'), 'utf8')).readOnly
  const concurrent = await run('review_fanout.js', { agents: ['a', 'b'].map((key) => ({ key, promptPath: 'p.md', model: 'sonnet',
    kind: 'reviewer', mustRead: [{ path: 'x', lines: 1 }] })), __toolProfiles: { a: P.reviewer, b: P.reviewer, ...PROFILED } })
  check('review_fanout: every concurrent child gets the shared read-only concurrency line',
    ['review:a', 'review:b'].every((l) => ((concurrent.calls.find((c) => c.label === l) || {}).prompt || '').includes(readOnlyLine)),
    errOf(concurrent))

  const CODEX = { name: 'codex', ids: ['gpt-test-native'],
    efforts: ['low', 'medium', 'high', 'max'] }
  const auditArgs = { context: 'fixture manifest', docRoot: 'vault', model: 'gpt-test-native',
    effort: 'high', tier: 'fanout', checker: { model: 'gpt-test-native', effort: 'medium' }, __transport: CODEX,
    kind: 'reviewer', __kindProfile: P.reviewer }
  const audit = await run('doc_architecture_audit.js', auditArgs)
  check('doc audit: caller pin reaches all three lenses on a provider session',
    audit.calls.length === 3 && audit.calls.every(c => c.model === 'gpt-test-native' && c.effort === 'high')
    && audit.result.lensesCompleted === 3 && Array.isArray(audit.result.findings), JSON.stringify(audit.result))
  const badAudit = await run('doc_architecture_audit.js', { ...auditArgs, model: 'sonnet' })
  check('doc audit: incompatible literal model starts no lens',
    badAudit.calls.length === 0 && /model/.test(badAudit.result.error || ''), JSON.stringify(badAudit.result))
  const missingAudit = await run('doc_architecture_audit.js', { ...auditArgs, effort: undefined })
  check('doc audit: absent effort starts no lens',
    missingAudit.calls.length === 0 && /effort/.test(missingAudit.result.error || ''), JSON.stringify(missingAudit.result))
  const emptyAudit = await run('doc_architecture_audit.js', auditArgs, () => 'lens failed')
  const emptyResult = emptyAudit.result || {}
  check('doc audit: three failed lenses return an explicit zero-lens partial result',
    emptyAudit.calls.length === 3 && emptyResult.lensesCompleted === 0
    && JSON.stringify(emptyResult.failedLenses) === JSON.stringify(['structural', 'crossref', 'domain'])
    && Array.isArray(emptyResult.findings) && emptyResult.findings.length === 0 && emptyResult.linkGraph === null
    && emptyAudit.logs.some(m => /PARTIAL: only 0\/3 lenses/.test(m)), JSON.stringify(emptyResult))

  const ideas = { generators: [{ key: 'one', instr: 'x' }, { key: 'two', instr: 'y' }],
    contextPrefix: 'fixture', critic: { model: 'gpt-test-native', effort: 'medium' }, __transport: CODEX,
    kind: 'lens', __kindProfile: P.lens }
  const ideaResult = opts => opts.label.startsWith('gen:')
    ? { candidates: [{ name: opts.label, shape: 'fixture' }] } : { notes: [] }
  const invalidIdea = await run('idea_fanout.js', { ...ideas,
    generators: [{ key: 'one', instr: 'x', model: 'sonnet' }, ideas.generators[1]] }, null, ideaResult)
  check('idea fanout: an explicit invalid generator model starts no lens',
    invalidIdea.calls.length === 0 && /model/.test(invalidIdea.result.error || ''), JSON.stringify(invalidIdea.result))
  const invalidCritic = await run('idea_fanout.js', { ...ideas,
    critics: [{ key: 'bad', instr: 'critique', effort: 'xhigh' }] }, null, ideaResult)
  check('idea fanout: an explicit invalid critic effort starts no generator',
    invalidCritic.calls.length === 0 && /effort/.test(invalidCritic.result.error || ''), JSON.stringify(invalidCritic.result))
  const unpinnedIdeas = await run('idea_fanout.js', ideas, null, ideaResult)
  check('idea fanout: a generator with no model or effort starts no lens',
    unpinnedIdeas.calls.length === 0 && /every job \(orchestration §5\)/.test(unpinnedIdeas.result.error || ''), JSON.stringify(unpinnedIdeas.result))
  const NO_MEDIUM = { name: 'no-medium', ids: ['nm-test-native'],
    efforts: ['low', 'high', 'max'] }
  const noEffortIdeas = await run('idea_fanout.js', { ...ideas, __transport: NO_MEDIUM, critic: { model: 'nm-test-native', effort: 'high' },
    generators: [{ key: 'one', instr: 'x', model: 'nm-test-native' }, ideas.generators[1]] }, null, ideaResult)
  check('idea fanout: a generator with a model but no effort starts no lens where efforts are declared',
    noEffortIdeas.calls.length === 0 && /every job \(orchestration §5\)/.test(noEffortIdeas.result.error || ''), JSON.stringify(noEffortIdeas.result))

  const fixtureArgs = { ...KINDED, fixtures: [{ id: 'one', agent: 'reviewer', model: 'gpt-test-native', effort: 'low',
    expected: { minCount: 0, maxCount: 1, required: [], forbidden: [] } }], validator: { model: 'gpt-test-native', effort: 'medium' }, __transport: CODEX }
  const fixtureResult = opts => opts.label.startsWith('validate:') ? { validated: true, reason: 'ok' } : '[]'
  const fixture = await run('test_agents_fixtures.js', fixtureArgs, null, fixtureResult)
  check('agent fixtures: a valid native model passes through dispatch and validation',
    fixture.calls.length === 2 && fixture.calls.every(c => c.model === 'gpt-test-native')
    && fixture.result.passed === 1, JSON.stringify(fixture.result))
  const effortOf = (r, prefix) => (r.calls.find(c => c.label.startsWith(prefix)) || {}).effort
  const noEffort = await run('test_agents_fixtures.js', { ...fixtureArgs,
    fixtures: [{ ...fixtureArgs.fixtures[0], effort: undefined }] }, null, fixtureResult)
  check('agent fixtures: an omitted fixture effort starts no dispatch where efforts are declared',
    noEffort.calls.length === 0 && /effort/.test(noEffort.result.error || ''), JSON.stringify(noEffort.result))
  const pinnedFixture = await run('test_agents_fixtures.js', { ...fixtureArgs,
    fixtures: [{ ...fixtureArgs.fixtures[0], effort: 'high' }] }, null, fixtureResult)
  check('agent fixtures: an explicit legal effort reaches the agent under test',
    effortOf(pinnedFixture, 'dispatch:') === 'high', JSON.stringify(pinnedFixture.calls.map(c => [c.label, c.effort])))
  const checkerPinned = await run('test_agents_fixtures.js', { ...KINDED, fixtures: [{ ...fixtureArgs.fixtures[0], model: 'nm-test-native', effort: 'low' }],
    validator: { model: 'nm-test-native', effort: 'high' }, __transport: NO_MEDIUM }, null, fixtureResult)
  check('agent fixtures: the validator pins from args.validator where medium is illegal',
    checkerPinned.calls.length === 2 && effortOf(checkerPinned, 'validate:') === 'high' && effortOf(checkerPinned, 'dispatch:') === 'low',
    JSON.stringify(checkerPinned.calls.map(c => [c.label, c.effort])))
  const badFixture = await run('test_agents_fixtures.js', { ...fixtureArgs,
    fixtures: [{ ...fixtureArgs.fixtures[0], model: 'sonnet' }] }, null, fixtureResult)
  check('agent fixtures: an invalid explicit model starts no dispatch',
    badFixture.calls.length === 0 && /model/.test(badFixture.result.error || ''), JSON.stringify(badFixture.result))
  const badFixtureEffort = await run('test_agents_fixtures.js', { ...fixtureArgs,
    fixtures: [{ ...fixtureArgs.fixtures[0], effort: 'xhigh' }] }, null, fixtureResult)
  check('agent fixtures: an invalid explicit effort starts no dispatch',
    badFixtureEffort.calls.length === 0 && /effort/.test(badFixtureEffort.result.error || ''), JSON.stringify(badFixtureEffort.result))

  // The probe engines that took their profile from __toolProfileDefaults: two take one call-level kind;
  // the two that measure agent types (a cell's own agentType is the variable) log a measurement row instead.
  const pins = { model: 'sonnet', effort: 'medium' }
  const SINGLE = {
    'test_agents_fixtures.js': { fixtures: [{ id: 'one', agent: 'reviewer', expected: { minCount: 0, maxCount: 1, required: [], forbidden: [] }, ...pins }], validator: pins },
    'test_skill_pressure.js': { skillContent: 'Rule: x. rationalization: y.', subject: pins, scorer: pins, validator: pins },
  }
  for (const [file, base] of Object.entries(SINGLE)) {
    const name = file.replace(/\.js$/, '')
    const none = await run(file, base, null, null, { loose: true })
    check(name + ': a kindless call is refused before any agent() call',
      none.calls.length === 0 && /kind/.test(errOf(none)), errOf(none) || JSON.stringify(none.calls.map((c) => c.label)))
    const kinded = await run(file, { ...base, ...KINDED }, null, null, { loose: true })
    check(name + ': a reviewer call runs every child as lane-review',
      kinded.calls.length > 0 && kinded.calls.every((c) => c.agentType === 'lane-review'),
      errOf(kinded) || JSON.stringify(kinded.calls.map((c) => [c.label, c.agentType])))
    const line = toolProfilesLine(kinded.logs)
    const rec = Object.values(line || {}).find((v) => v && v.kind === 'reviewer') || {}
    check(name + ': TOOL-PROFILES records the kind row as agent-type',
      rec.enforcement === 'agent-type' && rec.agentType === 'lane-review', JSON.stringify(line))
  }
  const MEASURED = {
    'agenttype_input_cost_probe.js': { models: ['sonnet'] },
    'harness_bloat_probe.js': {},
  }
  for (const [file, base] of Object.entries(MEASURED)) {
    const name = file.replace(/\.js$/, '')
    const r = await run(file, base, null, null, { loose: true })
    const line = toolProfilesLine(r.logs) || {}
    check(name + ': runs without a kind and records each cell as a measurement row',
      r.calls.length > 0 && Object.keys(line).length === r.calls.length && Object.values(line).every((v) => v && v.enforcement === 'measurement'),
      errOf(r) || JSON.stringify(line).slice(0, 300))
    check(name + ': a cell keeps the agentType it measures',
      r.calls.every((c) => (line[c.label] || {}).agentType === (c.agentType || 'default')), JSON.stringify(r.calls.map((c) => [c.label, c.agentType])))
  }
  for (const file of [...Object.keys(SINGLE), ...Object.keys(MEASURED)]) {
    check(file + ': reads no __toolProfileDefaults',
      !/__toolProfileDefaults|TOOLP\.f\[/.test(fs.readFileSync(path.join(__dirname, '../workflows', file), 'utf8')), '')
  }

  console.log((failed ? 'FAIL ' : 'PASS ') + 'engine_tool_profiles_test (' + total + ' cases)')
  process.exit(failed ? 1 : 0)
})()
