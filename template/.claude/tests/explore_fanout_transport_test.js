#!/usr/bin/env node
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const { isDeepStrictEqual } = require('util')

// Writes the run as Claude Code records it (<session>/workflows/<runId>.json beside the transcript directory's
// journal.jsonl, one started and one result row per agent call, the result stored as JSON text) and replays it.
function replayBoundedRun(ran, returned) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'explore-g8-'))
  try {
    const runId = 'wf_g8replay-000'
    const transcriptDir = path.join(root, 'subagents', 'workflows', runId)
    fs.mkdirSync(transcriptDir, { recursive: true })
    fs.mkdirSync(path.join(root, 'workflows'))
    fs.writeFileSync(path.join(root, 'workflows', runId + '.json'),
      JSON.stringify({ runId, script: src, args: ran.args, result: ran.result }))
    const rows = [{ type: 'launched' }]
    ran.calls.forEach((opts, index) => {
      const identity = { key: 'v2:g8-' + index, agentId: 'ag8' + index }
      rows.push({ type: 'started', ...identity, label: opts.label, phase: opts.phase })
      rows.push({ type: 'result', ...identity, result: JSON.stringify(returned(ran.prompts[index], opts)) })
    })
    fs.writeFileSync(path.join(transcriptDir, 'journal.jsonl'), rows.map(row => JSON.stringify(row)).join('\n') + '\n')
    return spawnSync(process.execPath, [path.join(__dirname, '..', 'tools', 'workflow_replay.mjs'), transcriptDir],
      { encoding: 'utf8', maxBuffer: 64 << 20 })
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}

function fieldDifferences(expected, actual, prefix = '') {
  if (isDeepStrictEqual(expected, actual)) return []
  if (expected === null || actual === null || typeof expected !== 'object' || typeof actual !== 'object') return [prefix]
  return [...new Set([...Object.keys(expected), ...Object.keys(actual)])].flatMap(key =>
    fieldDifferences(expected[key], actual[key], prefix ? prefix + '.' + key : key))
}

const engineRun = require('./_engine_run')

const EXPLORE = path.join(__dirname, '..', 'workflows', 'explore_fanout.js')
const src = fs.readFileSync(EXPLORE, 'utf8')
const engine = engineRun.load(EXPLORE)
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
// The explore run starts its closeCheck verifier, so every run carries args.checker. The test pins it from the
// transport under test (its first id, and medium when the transport declares it); the engine never picks one.
const pinnedChecker = (transport) => {
  if (!transport || !Array.isArray(transport.ids) || !transport.ids.length) return undefined
  const checker = { model: transport.ids[0] }
  if (Array.isArray(transport.efforts) && transport.efforts.length) checker.effort = transport.efforts.includes('medium') ? 'medium' : transport.efforts[0]
  return checker
}
const withHost = args => engineRun.withHost(args, HOST_TRANSPORT)

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
const lens = (overrides = {}) => ({
  key: 'lens', prompt: 'Inspect.', model: 'opus', effort: 'low', agentType: 'general-purpose', ...overrides,
})
const claim = (index, padding = '') => ({
  subject: 'subject-' + index, polarity: 'exists', claim: 'claim-' + index + padding,
  evidence: 'evidence-' + index + padding, verification: null, file: 'file-' + index + '.js:1',
  bearing: 'context', confidence: 'verified',
})

async function run(args, returned = {
  claims: [],
  checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
}) {
  const calls = []
  const logs = []
  const prompts = []
  const agent = async (prompt, opts) => {
    prompts.push(prompt)
    calls.push(opts)
    return typeof returned === 'function' ? returned(prompt, opts) : returned
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const transport = Object.prototype.hasOwnProperty.call(args, '__transport') ? args.__transport : HOST_TRANSPORT
  const passedArgs = withHost({ resultMode: 'full', checker: pinnedChecker(transport), ...args })
  const result = await engine(passedArgs, agent, parallel, () => {}, line => logs.push(line))
  return { result, calls, logs, prompts, args: passedArgs }
}

;(async () => {
  const absent = await run({ __transport: undefined, lenses: [lens({ key: 'absent', model: 'opus', effort: 'low' })] })
  check('no injected vocabulary fails closed naming __transport',
    String(absent.result.error || '').includes('__transport') && absent.calls.length === 0, JSON.stringify(absent.result))
  const host = await run({
    lenses: [lens({ key: 'host', model: 'opus', effort: 'xhigh', tier: 'architect' })],
  })
  check('an injected transport with no declared efforts keeps its ids and the native rungs',
    !host.result.error && host.calls[0] && host.calls[0].model === 'opus'
      && host.calls[0].effort === 'xhigh',
    JSON.stringify({ result: host.result, calls: host.calls }))
  check('PINS logs the declared tier beside the pin', host.logs.some(l => String(l).startsWith('PINS ') && String(l).includes('tier:architect')), JSON.stringify(host.logs))

  const codex = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    lenses: [lens({ key: 'codex', model: 'gpt-5.6-luna', effort: 'max' })],
  })
  check('provider ids and native max effort are the vocabulary',
    !codex.result.error && codex.calls[0] && codex.calls[0].model === 'gpt-5.6-luna'
      && codex.calls[0].effort === 'max',
    JSON.stringify({ result: codex.result, calls: codex.calls }))

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
      lenses: [lens({ key: 'invalid-transport', model: 'gpt-test', effort: 'max' })],
    })
    check('invalid injected transport fails before explore dispatch: ' + label,
      !!invalidTransport.result.error && invalidTransport.calls.length === 0,
      JSON.stringify(invalidTransport.result))
  }

  const omitted = await run({
    lenses: [lens({ key: 'default', model: 'opus', effort: undefined })],
  })
  check('omitted effort is not defaulted to medium where the transport declares no efforts',
    !omitted.result.error && omitted.calls[0] && omitted.calls[0].effort === undefined,
    JSON.stringify({ result: omitted.result, calls: omitted.calls }))

  const invalid = await run({
    __transport: { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] },
    lenses: [lens({ key: 'invalid', model: 'opus', effort: 'max' })],
  })
  check('present unsupported effort fails before dispatch',
    !!invalid.result.error && invalid.calls.length === 0,
    JSON.stringify(invalid.result))

  const wrongProvider = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    lenses: [lens({ key: 'wrong', model: 'opus', effort: 'low' })],
  })
  check('provider selector rejects another transport\'s names',
    !!wrongProvider.result.error && wrongProvider.calls.length === 0,
    JSON.stringify(wrongProvider.result))

  const caseFoldCollision = await run({
    spillDir: '.claude/scratch/explore-results',
    lenses: [
      lens({ key: 'Lens', prompt: 'Inspect one.' }),
      lens({ key: 'lens', prompt: 'Inspect two.' }),
    ],
  })
  check('spill collisions case-fold for Windows',
    !!caseFoldCollision.result.error && caseFoldCollision.calls.length === 0,
    JSON.stringify(caseFoldCollision.result))

  for (const agentType of [undefined, '', '   ']) {
    const missingProfile = await run({
      lenses: [{ key: 'profile', prompt: 'Inspect.', model: 'opus', effort: 'low', agentType }],
    })
    check('missing or blank agentType fails before dispatch: ' + JSON.stringify(agentType),
      !!missingProfile.result.error && missingProfile.calls.length === 0,
      JSON.stringify(missingProfile.result))
  }

  const forwardedProfile = await run({ lenses: [lens({ agentType: 'claude' })] })
  check('agentType forwards to native explore agent',
    forwardedProfile.calls[0] && forwardedProfile.calls[0].agentType === 'claude',
    JSON.stringify(forwardedProfile.calls))

  const blockedAfterClaim = await run({lenses: [lens({key: 'blocked'})]}, {
    claims: [claim(1)],
    checked: {toolsUsed: ['Read:fixture'], stoppedAt: 'blocked', basis: 'a later read was denied'},
    gaps: ['the denied target was not checked'],
  })
  check('G4 a lens that found a claim and then blocked is partial, not completed',
    blockedAfterClaim.result.perLens[0].status === 'partial'
      && blockedAfterClaim.result.counts.uncoveredLenses === 0,
    JSON.stringify(blockedAfterClaim.result))

  const blankCoverage = await run({lenses: [lens({key: 'blank'})]}, {
    claims: [],
    checked: {toolsUsed: [''], stoppedAt: 'exhausted-leads', basis: ''},
  })
  check('G5 blank tools and basis cannot count as a checked empty sweep',
    blankCoverage.result.perLens[0].status === 'uncovered'
      && blankCoverage.result.flags.some(row => row.kind === 'lens-invalid-shape'),
    JSON.stringify(blankCoverage.result))

  for (const agentType of ['Explore', 'Plan']) {
    const inline = await run({
      spillDir: '.claude/scratch/explore-results',
      lenses: [lens({ key: agentType.toLowerCase(), agentType })],
    })
    check(agentType + ' profile never receives impossible spill contract',
      !inline.result.error
        && !inline.prompts[0].includes('SPILL-BEFORE-VALIDATE')
        && Array.isArray(inline.result.inlineLabels)
        && inline.result.inlineLabels.includes(agentType.toLowerCase())
        && !Object.prototype.hasOwnProperty.call(inline.result.spills || {}, agentType.toLowerCase()),
      JSON.stringify({ result: inline.result, prompt: inline.prompts[0] }))
  }

  const writerSpill = await run({
    spillDir: '.claude/scratch/explore-results',
    lenses: [lens({ key: 'writer' })],
  })
  const boundedWriterSpill = await run({
    resultMode: 'bounded',
    spillDir: '.claude/scratch/explore-results',
    lenses: [lens({ key: 'writer' })],
  })
  check('write-capable profile receives and reports exact spill path',
    writerSpill.prompts[0].includes('SPILL-BEFORE-VALIDATE')
      && (writerSpill.result.spills || {}).writer === '.claude/scratch/explore-results/writer.spill.md'
      && boundedWriterSpill.result.delivery.payloadComplete === false
      && boundedWriterSpill.result.delivery.preview.spillMetadata.total === 2
      && boundedWriterSpill.result.delivery.preview.spillMetadata.omitted === 2,
    JSON.stringify({ full: writerSpill.result, bounded: boundedWriterSpill.result }))

  check('every explore result carries unavailable verification without a verifier profile',
    [host, writerSpill, boundedWriterSpill].every(r => r.result.verification.status === 'unavailable' && !('postCheck' in r.result)),
    JSON.stringify([host.result.verification, writerSpill.result.verification, boundedWriterSpill.result.verification]))

  for (const spillDir of ['reports/explore', '.claude/scratch/../../outside', 'C:/repo/.claude/scratch/explore']) {
    const escaped = await run({ spillDir, lenses: [lens({ key: 'writer' })] })
    check('spillDir outside repository scratch fails before dispatch: ' + spillDir,
      !!escaped.result.error && escaped.calls.length === 0,
      JSON.stringify(escaped.result))
  }

  const largeClaims = Array.from({ length: 240 }, (_, i) => claim(i, '界'.repeat(4000)))
  const bounded = await run({ resultMode: undefined, lenses: [lens({ key: 'bounded' })] }, {
    claims: largeClaims,
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
    gaps: ['gap-' + 'λ'.repeat(4000)],
  })
  check('default bounded result stays within 8192 UTF-8 bytes with exact full counts and selectors',
    Buffer.byteLength(JSON.stringify(bounded.result), 'utf8') <= 8192
      && bounded.result.delivery.contract === 'native-fanout-bounded/v1'
      && bounded.result.delivery.engine === 'explore-fanout'
      && bounded.result.counts.claims === largeClaims.length
      && bounded.result.delivery.counts.claims === largeClaims.length
      && bounded.result.delivery.payloadComplete === false
      && Array.isArray(bounded.result.claims)
      && bounded.result.claims.every(row => row.journalSelector === row.id),
    JSON.stringify({ length: Buffer.byteLength(JSON.stringify(bounded.result), 'utf8'), result: bounded.result }))
  const archive = bounded.result.delivery.archive || {}
  const preview = bounded.result.delivery.preview || {}
  check('bounded explore declares exact journal commands and accounts for every collection',
    archive.delivered === false
      && archive.relativePath === 'journal.jsonl'
      && archive.validation === 'required'
      && archive.commands.manifest.includes('--workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind explore --workflow-manifest')
      && archive.commands.select.includes('--workflow-select <ID> --expect-journal-sha256 <sha256-from-manifest>')
      && archive.commands.page.includes('--workflow-page items|lenses|reports|gaps')
      && archive.commands.full.includes('--workflow-full --expect-journal-sha256 <sha256-from-manifest>')
      && Object.keys(preview).sort().join(',') === 'claims,contradictions,flags,gaps,nonVerified,perLens'
      && Object.values(preview).every(row => row.shown + row.omitted === row.total),
    JSON.stringify({ archive, preview }))

  const bigClaims = Array.from({ length: 60 }, (_, i) => claim(i, '界'.repeat(3000)))
  const manyInvalidEntries = Array.from({ length: 300 }, () => ({ bad: 'x'.repeat(80) }))
  const shedRun = await run({ resultMode: undefined, lenses: [lens({ key: 'shed' })] }, {
    claims: [...bigClaims, ...manyInvalidEntries],
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
  })
  const shedBytes = Buffer.byteLength(JSON.stringify(shedRun.result), 'utf8')
  check('a large planted run with many rejected-entry flags stays within the byte bound',
    shedBytes <= 8192
      && shedRun.result.delivery.byteLimitExceeded === false
      && shedRun.result.flags.length > 0
      && shedRun.result.perLens[0].rejected === manyInvalidEntries.length,
    'bytes=' + shedBytes)
  check('a bounded run that sheds state offers no resume and keeps the archive retrieval commands',
    shedRun.result.delivery.payloadComplete === false
      && !Object.prototype.hasOwnProperty.call(shedRun.result.delivery, 'resume')
      && Object.keys(shedRun.result.delivery.archive.commands).sort().join(',') === 'full,manifest,page,select',
    JSON.stringify(shedRun.result.delivery))
  const smallComplete = await run({ resultMode: 'bounded', lenses: [lens({ key: 'small' })] }, {
    claims: [claim(1)],
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
  })
  check('a small complete bounded run declares no resume recovery is needed',
    smallComplete.result.delivery.payloadComplete === true
      && !Object.prototype.hasOwnProperty.call(smallComplete.result.delivery, 'resume'),
    JSON.stringify(smallComplete.result.delivery))

  const subjects = Array.from({length: 8}, (_, i) => 'shared-' + i + '-' + '界'.repeat(200))
  const processedReturned = (_prompt, opts) => {
    const key = opts.label.slice('explore:'.length)
    const claims = subjects.map((subject, i) => key === 'b' ? {
      subject, polarity: 'absent', claim: 'absent-' + i, verification: 'git grep fixture',
      file: 'file-' + i + '.js:2', bearing: 'constraint', confidence: 'verified',
    } : {
      subject, polarity: 'exists', claim: key + '-exists-' + i,
      evidence: key === 'a' ? null : 'quoted-' + i,
      file: 'file-' + i + '.js:1', bearing: 'constraint', confidence: 'verified',
    })
    return {claims, checked: {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture read'}}
  }
  // G8 (revised for B1): the confidence-downgrade/contested/corroboration COMPUTATION is identical
  // whether resultMode is 'full' or 'bounded' — bounded only changes how the already-computed state is
  // RENDERED (shed to fit the byte bound). Verify the computation in `full` mode, where nothing sheds,
  // and verify the byte bound + honest accounting + named recovery path separately in `bounded` mode —
  // asserting BOTH properties on the bounded render (as the original G8 did) is a contradiction: this
  // scenario's processed state does not fit in 8192 bytes, so keeping it complete would mean the byte
  // bound is not actually enforced (defect B1).
  const processedFull = await run({
    resultMode: 'full',
    lenses: [lens({key: 'a'}), lens({key: 'b'}), lens({key: 'c'})],
  }, processedReturned)
  check('G8a full explore computes contested/corroborated/downgraded claims correctly',
    processedFull.result.contradictions.every(row => row.positions.every(position => /^C\d+$/.test(position.journalSelector)))
      && processedFull.result.claims.filter(row => row.lens === 'a').every(row => row.confidence === 'unverified' && row.contested && row.corroboratedBy.includes('c')),
    JSON.stringify(processedFull.result))
  const processedBounded = await run({
    resultMode: 'bounded',
    lenses: [lens({key: 'a'}), lens({key: 'b'}), lens({key: 'c'})],
  }, processedReturned)
  const processedBytes = Buffer.byteLength(JSON.stringify(processedBounded.result), 'utf8')
  check('G8b bounded explore stays within the byte bound and accounts for the same processed state',
    processedBytes <= 8192
      && processedBounded.result.delivery.byteLimitExceeded === false
      && processedBounded.result.delivery.counts.claims === processedFull.result.claims.length
      && processedBounded.result.delivery.counts.contradictions === processedFull.result.contradictions.length
      && processedBounded.result.delivery.counts.flags === processedFull.result.flags.length
      && Object.values(processedBounded.result.delivery.preview).every(row => row.shown + row.omitted === row.total)
      && !Object.prototype.hasOwnProperty.call(processedBounded.result.delivery, 'resume'),
    'bytes=' + processedBytes + ' ' + JSON.stringify(processedBounded.result.delivery))

  // The archive pages hold only raw lens returns, so engine-derived fields (downgraded confidence, contested and
  // corroborated tags, contradictions, flag details) come back by local replay of the bounded run.
  const retained = processedBounded.result
  const replayed = replayBoundedRun(processedBounded, processedReturned)
  let differences = ['(replay exited ' + replayed.status + ': ' + String(replayed.stderr || replayed.error || '').slice(0, 300) + ')']
  if (replayed.status === 0) {
    differences = fieldDifferences(JSON.parse(JSON.stringify(processedFull.result)), JSON.parse(replayed.stdout))
  }
  check('G8c the local replay of the bounded run recovers every processed field of the full result',
    differences.length === 0 && retained.delivery.payloadComplete === false,
    JSON.stringify({differentFields: differences.length, first: differences.slice(0, 24),
      fullClaims: processedFull.result.claims.length, retainedClaims: retained.claims.length,
      fullContradictions: processedFull.result.contradictions.length, retainedContradictions: retained.contradictions.length,
      fullFlags: processedFull.result.flags.length, retainedFlags: retained.flags.length,
      longFlagDetails: processedFull.result.flags.filter(row => row.detail.length > 120).length}))

  const fullClaims = [claim(1), claim(2)]
  const full = await run({ resultMode: 'full', lenses: [lens({ key: 'full' })] }, {
    claims: fullClaims,
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
  })
  check('full mode preserves every original claim and stable source ID',
    Array.isArray(full.result.claims)
      && full.result.claims.map(c => c.id).join(',') === 'C1,C2'
      && full.result.claims.every((row, index) => row.claim === fullClaims[index].claim)
      && !Object.prototype.hasOwnProperty.call(full.result, 'delivery'),
    JSON.stringify(full.result))

  const malformed = await run({ resultMode: 'bounded', lenses: [lens({ key: 'bad' })] }, {
    claims: 'not-an-array',
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
  })
  check('malformed explore source is uncovered rather than a completed zero',
    malformed.result.delivery.status === 'failed'
      && malformed.result.perLens[0].status === 'uncovered'
      && malformed.result.counts.claims === 0,
    JSON.stringify(malformed.result))

  const invalidMode = await run({ resultMode: 'tiny', lenses: [lens()] })
  check('invalid resultMode fails before dispatch',
    !!invalidMode.result.error && invalidMode.calls.length === 0,
    JSON.stringify(invalidMode.result))

  check('engine header documents the argument schema the command no longer lists',
    /^\/\/ Args: \{lenses: \[\{key, promptPath, model, effort, agentType, tier\}\], contextPrefixPath/m.test(src))
  const rendered = await run({ lenses: [lens()] })
  check('a rendered lens prompt carries no character-count target',
    rendered.prompts.length > 0 && rendered.prompts.every(p => !/chars target/.test(p)))

  console.log(`explore_fanout transport: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(err => {
  console.error(err)
  process.exit(1)
})
