#!/usr/bin/env node
const fs = require('fs')
const path = require('path')

const src = fs.readFileSync(path.join(__dirname, '..', 'workflows', 'explore_fanout.js'), 'utf8')
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
  const fn = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)
  const result = await fn({ resultMode: 'full', ...args }, agent, parallel, () => {}, line => logs.push(line))
  return { result, calls, logs, prompts }
}

;(async () => {
  const anthropic = await run({
    lenses: [lens({ key: 'anthropic', model: 'opus', effort: 'xhigh' })],
  })
  check('Anthropic fallback keeps role and effort vocabulary',
    !anthropic.result.error && anthropic.calls[0] && anthropic.calls[0].model === 'opus'
      && anthropic.calls[0].effort === 'xhigh',
    JSON.stringify({ result: anthropic.result, calls: anthropic.calls }))

  const codex = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'], default: 'gpt-5.6-luna',
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    lenses: [lens({ key: 'codex', model: 'gpt-5.6-luna', effort: 'max' })],
  })
  check('provider ids and native max effort replace Anthropic vocabulary',
    !codex.result.error && codex.calls[0] && codex.calls[0].model === 'gpt-5.6-luna'
      && codex.calls[0].effort === 'max',
    JSON.stringify({ result: codex.result, calls: codex.calls }))

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
  check('omitted effort takes the medium default',
    !omitted.result.error && omitted.calls[0] && omitted.calls[0].effort === 'medium',
    JSON.stringify({ result: omitted.result, calls: omitted.calls }))

  const invalid = await run({
    lenses: [lens({ key: 'invalid', model: 'opus', effort: 'max' })],
  })
  check('present unsupported effort fails before dispatch',
    !!invalid.result.error && invalid.calls.length === 0,
    JSON.stringify(invalid.result))

  const wrongProvider = await run({
    __transport: {
      name: 'codex', ids: ['gpt-5.6-luna'], default: 'gpt-5.6-luna',
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
    lenses: [lens({ key: 'wrong', model: 'opus', effort: 'low' })],
  })
  check('provider selector rejects Anthropic aliases',
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
      && Object.keys(preview).sort().join(',') === 'claims,contradictions,flags,gaps,perLens'
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
  check('a bounded run that sheds state names the resume recovery path beside the journal path',
    shedRun.result.delivery.payloadComplete === false
      && !!shedRun.result.delivery.resume
      && shedRun.result.delivery.resume.costsNewModelSpend === false
      && /resultMode.*full/.test(shedRun.result.delivery.resume.how)
      && !!shedRun.result.delivery.resume.contains,
    JSON.stringify(shedRun.result.delivery))
  const smallComplete = await run({ resultMode: 'bounded', lenses: [lens({ key: 'small' })] }, {
    claims: [claim(1)],
    checked: { toolsUsed: ['Read:test'], stoppedAt: 'exhausted-leads', basis: 'fixture' },
  })
  check('a small complete bounded run declares no resume recovery is needed',
    smallComplete.result.delivery.payloadComplete === true
      && !Object.prototype.hasOwnProperty.call(smallComplete.result.delivery, 'resume'),
    JSON.stringify(smallComplete.result.delivery))

  const subjects = Array.from({length: 8}, (_, i) => 'shared-' + i)
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
      && (processedBounded.result.delivery.payloadComplete
        || (!!processedBounded.result.delivery.resume && processedBounded.result.delivery.resume.costsNewModelSpend === false)),
    'bytes=' + processedBytes + ' ' + JSON.stringify(processedBounded.result.delivery))

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

  console.log(`explore_fanout transport: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(err => {
  console.error(err)
  process.exit(1)
})
