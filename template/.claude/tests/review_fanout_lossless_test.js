#!/usr/bin/env node
const fs = require('fs')
const path = require('path')
const assert = require('node:assert/strict')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(path.join(__dirname, '..', 'workflows', 'review_fanout.js'), 'utf8')
const body = source.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
let passed = 0
let failed = 0
const checked = (more = {}) => ({
  toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture input was read', ...more,
})
const withCoverage = value => {
  if (!value || typeof value !== 'object' || value instanceof Error || Array.isArray(value)) return value
  if (!Object.prototype.hasOwnProperty.call(value, 'findings')) return value
  return {
    ...value,
    checked: Object.prototype.hasOwnProperty.call(value, 'checked') ? value.checked : checked(),
    gaps: Object.prototype.hasOwnProperty.call(value, 'gaps') ? value.gaps : [],
  }
}
const finding = (description, more = {}) => ({
  agent: 'source', action: 'FIX', category: 'bug', critical: true,
  file: 'example.py:42', description, rationale: 'Independent source evidence', ...more,
})
const config = {agents: ['one', 'two'].map(key => ({
  key, prompt: 'Review fixture.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose',
})), consolidate: false, resultMode: 'full'}
async function run(replies, extra = {}) {
  const agent = async (prompt, opts) => {
    if (opts.phase === 'Merge') {
      if (replies.merge instanceof Error) throw replies.merge
      return typeof replies.merge === 'function' ? replies.merge(prompt, opts) : replies.merge
    }
    const value = replies[opts.label.slice('review:'.length)]
    if (value instanceof Error) throw value
    return withCoverage(value)
  }
  const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn).catch(() => null)))
  return new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)(
    {...config, ...extra}, agent, parallel, () => {}, () => {})
}
async function check(name, test) {
  try { await test(); passed++; console.log('ok   ' + name) }
  catch (error) { failed++; console.log('FAIL ' + name + ': ' + error.message) }
}
;(async () => {
  await check('distinct findings at one anchor both survive with true received count', async () => {
    const out = await run({one: {findings: [finding('lifetime')]}, two: {findings: [finding('validation')]}})
    assert.equal(out.counts.raw, 2)
    assert.equal(out.findings.length, 2)
    assert.deepEqual(new Set(out.findings.map(f => f.description)), new Set(['lifetime', 'validation']))
  })
  await check('source records preserve evidence before any semantic merge', async () => {
    const first = finding('first', {old: 'quoted original A', new: 'replacement A'})
    const second = finding('second', {old: 'quoted original B', new: 'replacement B'})
    const out = await run({one: {findings: [first]}, two: {findings: [second]}})
    assert.equal(out.sources.length, 2)
    assert.deepEqual(out.sources.map(s => s.finding), [first, second])
    assert.equal(new Set(out.sources.map(s => s.id)).size, 2)
  })
  await check('caller IDs cannot collide with engine-owned source identity', async () => {
    const out = await run({one: {findings: [finding('first', {id: 'F999'})]},
      two: {findings: [finding('second', {id: 'F999'})]}})
    assert.deepEqual(out.sources.map(s => s.id), ['F1', 'F2'])
    assert.deepEqual(out.sources.map(s => s.finding.id), ['F999', 'F999'])
    assert.deepEqual(out.findings.map(f => f.id), ['F1', 'F2'])
  })
  await check('severity cannot erase a different finding at the same location', async () => {
    const out = await run({one: {findings: [finding('critical')]},
      two: {findings: [finding('separate noncritical', {critical: false})]}})
    assert.equal(out.findings.length, 2)
    assert.equal(out.sources.length, 2)
  })
  await check('G15 non-null locations require a non-empty path:line anchor', async () => {
    const out = await run({one: {findings: [finding('null location', {file: null})]},
      two: {findings: [finding('blank location', {file: ''}), finding('no line', {file: 'path.py'})]}})
    assert.equal(out.sources.length, 1)
    assert.equal(out.sources[0].finding.file, null)
    assert.equal(out.counts.rejected, 2)
    assert.equal(out.perAgent.find(r => r.key === 'two').status, 'partial')
  })
  await check('thrown lens remains named and uncovered rather than crashing reporting', async () => {
    const out = await run({one: new Error('transport failure'), two: {findings: []}})
    assert.ok(out.flags.some(f => f.lens === 'one' && f.kind === 'lens-no-return'))
    const row = out.perAgent.find(r => r.key === 'one')
    assert.equal(row.count, null)
    assert.notEqual(row.status, 'completed')
  })
  for (const invalid of [{}, {findings: null}, {findings: 'bad'}]) {
    await check('malformed report is not a valid empty lens: ' + JSON.stringify(invalid), async () => {
      const out = await run({one: invalid, two: {findings: []}})
      assert.ok(out.flags.some(f => f.lens === 'one'))
      assert.equal(out.perAgent.find(r => r.key === 'one').count, null)
    })
  }
  await check('malformed finding cannot crash or silently produce clean coverage', async () => {
    const out = await run({one: {findings: [null]}, two: {findings: []}})
    assert.ok(out.flags.some(f => f.lens === 'one'))
    assert.equal(out.findings.length, 0)
  })
  await check('empty reports complete only with positive checked provenance', async () => {
    const out = await run({one: {findings: []}, two: {findings: []}})
    assert.deepEqual(out.flags, [])
    assert.ok(out.perAgent.every(r => r.status === 'completed' && r.count === 0))
    assert.equal(out.sources.length, 0)
  })
  await check('G3 an empty result without checked provenance is uncovered, not clean', async () => {
    const out = await run({one: {findings: [], checked: null}, two: {findings: []}})
    const row = out.perAgent.find(r => r.key === 'one')
    assert.equal(row.status, 'uncovered')
    assert.ok(out.flags.some(f => f.lens === 'one' && f.kind.includes('coverage')))
  })
  await check('G3 checked gaps make a lens partial even when it returned findings', async () => {
    const out = await run({one: {findings: [finding('partial work')], gaps: ['could not read sibling']}, two: {findings: []}})
    assert.equal(out.perAgent.find(r => r.key === 'one').status, 'partial')
    assert.ok(out.flags.some(f => f.lens === 'one' && f.kind === 'lens-gaps'))
  })
  for (const merge of [null, {findings: []}, {findings: [finding('invalid merge', {merged_from: ['F1', 'F1']})]}]) {
    await check('failed or incomplete merge falls back to every original: ' + JSON.stringify(merge), async () => {
      const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]}, merge}, {consolidate: true})
      assert.equal(out.counts.raw, 2)
      assert.equal(out.findings.length, 2)
      assert.equal(out.sources.length, 2)
      assert.ok(out.flags.length > 0)
    })
  }
  await check('partly malformed lens cannot claim completed coverage', async () => {
    const out = await run({one: {findings: [finding('valid'), null]}, two: {findings: []}})
    assert.equal(out.findings.length, 1)
    assert.notEqual(out.perAgent.find(r => r.key === 'one').status, 'completed')
  })
  await check('object without required finding fields is invalid evidence', async () => {
    const out = await run({one: {findings: [{}]}, two: {findings: []}})
    assert.equal(out.findings.length, 0)
    assert.ok(out.flags.some(f => f.lens === 'one'))
    assert.notEqual(out.perAgent.find(r => r.key === 'one').status, 'completed')
  })
  const grouped = (more = {}) => ({findings: [finding('merger text', {merged_from: ['F1', 'F2'], ...more})]})
  await check('different edit pairs are never joined into an invalid replacement', async () => {
    const out = await run({one: {findings: [finding('first', {old: 'before A', new: 'after A'})]},
      two: {findings: [finding('second', {old: 'before B', new: 'after B'})]}, merge: grouped()}, {consolidate: true})
    assert.equal(out.findings[0].old, null)
    assert.equal(out.findings[0].new, null)
    assert.deepEqual(out.sources.map(s => s.finding.old), ['before A', 'before B'])
  })
  await check('identical edit pairs remain verbatim instead of being duplicated', async () => {
    const same = finding('same defect', {old: 'before', new: 'after'})
    const out = await run({one: {findings: [same]}, two: {findings: [same]}, merge: grouped()}, {consolidate: true})
    assert.equal(out.findings[0].old, 'before')
    assert.equal(out.findings[0].new, 'after')
    assert.equal(out.counts.raw, 2)
    assert.equal(out.counts.exactDuplicates, 1)
  })
  await check('merger cannot invent question options scope or claim text', async () => {
    const same = finding('original claim', {question: 'source question', options: ['source option'], scope: ['source.py']})
    const out = await run({one: {findings: [same]}, two: {findings: [same]},
      merge: grouped({question: 'invented question', options: ['invented'], scope: ['outside.py']})}, {consolidate: true})
    assert.equal(out.findings[0].question, same.question)
    assert.deepEqual(out.findings[0].options, same.options)
    assert.deepEqual(out.findings[0].scope, same.scope)
    assert.equal(out.findings[0].description, same.description)
  })
  await check('singleton grouping cannot rewrite original claim fields', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: {findings: [finding('invented A', {merged_from: ['F1']}), finding('invented B', {merged_from: ['F2']})]}},
      {consolidate: true})
    assert.deepEqual(out.findings.map(f => f.description), ['first', 'second'])
  })
  await check('merger can return membership only without copying original evidence', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: {findings: [{merged_from: ['F1', 'F2']}]}}, {consolidate: true})
    assert.equal(out.findings.length, 1)
    assert.deepEqual(out.flags, [])
    assert.equal(out.findings[0].critical, true)
  })
  await check('G6 merge groups cannot cross file anchors', async () => {
    const out = await run({one: {findings: [finding('first', {file: 'one.py:1'})]},
      two: {findings: [finding('second', {file: 'two.py:2'})]},
      merge: {findings: [{merged_from: ['F1', 'F2']}]}}, {consolidate: true})
    assert.equal(out.findings.length, 2)
    assert.ok(out.flags.some(f => f.kind === 'consolidate-invalid' && f.detail.includes('different files')))
  })
  await check('G12 successful merge output is re-sorted by critical and action tier', async () => {
    const out = await run({
      one: {findings: [finding('weak', {critical: false, action: 'PLAN', category: 'improvement'})]},
      two: {findings: [finding('strong', {critical: true, action: 'FIX', category: 'bug'})]},
      merge: {findings: [{merged_from: ['F1']}, {merged_from: ['F2']}]},
    }, {consolidate: true})
    assert.deepEqual(out.findings.map(f => f.description), ['strong', 'weak'])
  })
  await check('thrown merge falls back to all available original findings', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: new Error('merge transport failure')}, {consolidate: true})
    assert.equal(out.findings.length, 2)
    assert.equal(out.sources.length, 2)
    assert.ok(out.flags.some(f => f.kind === 'consolidate-no-return'))
  })
  await check('merge input uses engine IDs even when sources supply conflicting IDs', async () => {
    const out = await run({one: {findings: [finding('first', {id: 'foreign'})]},
      two: {findings: [finding('second', {id: 'foreign'})]}, merge: prompt => {
        const input = JSON.parse(prompt.split('INPUT:\n')[1])
        assert.deepEqual(input.map(f => f.id), ['F1', 'F2'])
        return {findings: input.map(f => ({merged_from: [f.id]}))}
      }}, {consolidate: true})
    assert.deepEqual(out.flags, [])
  })
  await check('conflicting grouped options and scope stay on sources only', async () => {
    const out = await run({one: {findings: [finding('first', {options: ['A'], scope: ['a.py']})]},
      two: {findings: [finding('second', {options: ['B'], scope: ['b.py']})]},
      merge: grouped()}, {consolidate: true})
    assert.equal(out.findings[0].options, null)
    assert.equal(out.findings[0].scope, null)
    assert.deepEqual(out.sources.map(s => s.finding.options), [['A'], ['B']])
  })
  for (const field of ['options', 'scope']) {
    await check('invalid ' + field + ' members cannot claim completed coverage', async () => {
      const out = await run({one: {findings: [finding('invalid', {[field]: [42]})]}, two: {findings: []}})
      assert.equal(out.sources.length, 0)
      assert.equal(out.perAgent[0].status, 'partial')
    })
  }
  await check('full mode remains uncapped and preserves the complete legacy shape', async () => {
    const originals = Array.from({length: 40}, (_, i) => finding('finding-' + i + 'x'.repeat(400)))
    const out = await run({one: {findings: originals}, two: {findings: []}}, {resultMode: 'full'})
    assert.ok(Array.isArray(out.findings))
    assert.ok(Array.isArray(out.sources))
    assert.equal(out.sources.length, originals.length)
    assert.deepEqual(out.sources.map(row => row.finding), originals)
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') > 8192)
    assert.equal(Object.prototype.hasOwnProperty.call(out, 'delivery'), false)
  })
  await check('default bounded mode stays within 8192 UTF-8 bytes with full counts and selectors', async () => {
    const large = Array.from({length: 240}, (_, i) => finding('finding-' + i + '界'.repeat(4000), {
      rationale: 'rationale-' + i + 'λ'.repeat(4000),
    }))
    const out = await run({one: {findings: large}, two: {findings: []}}, {resultMode: undefined})
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192,
      'bounded bytes=' + Buffer.byteLength(JSON.stringify(out), 'utf8'))
    assert.equal(out.delivery.contract, 'native-fanout-bounded/v1')
    assert.equal(out.delivery.engine, 'review-fanout')
    assert.equal(out.delivery.status, 'completed')
    assert.equal(out.counts.raw, large.length)
    assert.equal(out.delivery.counts.sources, large.length)
    assert.equal(out.delivery.payloadComplete, false)
    assert.ok(Array.isArray(out.findings) && Array.isArray(out.sources))
    assert.ok(Array.isArray(out.flags) && Array.isArray(out.perAgent))
    assert.equal(typeof out.reports, 'object')
    assert.deepEqual(out.sources.map(row => row.journalSelector), out.sources.map(row => row.id))
    assert.ok(out.findings.every(row => Array.isArray(row.journalSelectors) && row.journalSelectors.length > 0))
  })
  await check('a large planted run with many rejected-entry flags stays within the byte bound', async () => {
    const bigFindings = Array.from({ length: 60 }, (_, i) => finding('finding-' + i + '界'.repeat(3000)))
    const manyInvalidEntries = Array.from({ length: 300 }, () => ({ bad: 'x'.repeat(80) }))
    const out = await run({
      one: { findings: [...bigFindings, ...manyInvalidEntries] },
      two: { findings: [] },
    }, { resultMode: undefined })
    const bytes = Buffer.byteLength(JSON.stringify(out), 'utf8')
    assert.ok(bytes <= 8192, 'bounded bytes=' + bytes)
    assert.equal(out.delivery.byteLimitExceeded, false)
    assert.equal(out.flags.length > 0, true)
    assert.equal(out.counts.rejected, manyInvalidEntries.length)
  })
  await check('a bounded run that sheds state names the resume recovery path beside the journal path', async () => {
    const bigFindings = Array.from({ length: 60 }, (_, i) => finding('finding-' + i + '界'.repeat(3000)))
    const manyInvalidEntries = Array.from({ length: 300 }, () => ({ bad: 'x'.repeat(80) }))
    const out = await run({
      one: { findings: [...bigFindings, ...manyInvalidEntries] },
      two: { findings: [] },
    }, { resultMode: undefined })
    assert.equal(out.delivery.payloadComplete, false)
    assert.ok(out.delivery.resume, 'delivery.resume missing when state was shed')
    assert.equal(out.delivery.resume.costsNewModelSpend, false)
    assert.match(out.delivery.resume.how, /resultMode.*full/)
    assert.ok(out.delivery.resume.contains)
  })
  await check('a small complete bounded run declares no resume recovery is needed', async () => {
    const out = await run({ one: { findings: [finding('small')] }, two: { findings: [] } }, { resultMode: 'bounded' })
    assert.equal(out.delivery.payloadComplete, true)
    assert.equal(Object.prototype.hasOwnProperty.call(out.delivery, 'resume'), false)
  })
  await check('bounded review accounts for collections and declares exact journal retrieval', async () => {
    const out = await run({one: {findings: [finding('valid'), null], report: 'R'.repeat(1000)}, two: {findings: []}}, {resultMode: 'bounded'})
    const preview = out.delivery.preview
    const archive = out.delivery.archive
    assert.deepEqual(Object.keys(preview).sort(), ['askIds', 'criticalIds', 'findings', 'flags', 'perAgent', 'reports', 'sources'])
    assert.equal(preview.findings.total, out.counts.total)
    assert.equal(preview.sources.total, out.counts.raw)
    assert.equal(preview.flags.total, 1)
    assert.equal(preview.reports.total, 1)
    assert.equal(preview.perAgent.total, 2)
    assert.ok(Object.values(preview).every(row => row.shown + row.omitted === row.total))
    assert.equal(archive.delivered, false)
    assert.equal(archive.relativePath, 'journal.jsonl')
    assert.equal(archive.validation, 'required')
    assert.equal(JSON.stringify(archive).includes('.claude/scratch'), false)
    assert.ok(archive.commands.manifest.includes('--workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind review --workflow-manifest'))
    assert.ok(archive.commands.select.includes('--workflow-select <ID> --expect-journal-sha256 <sha256-from-manifest>'))
    assert.ok(archive.commands.page.includes('--workflow-page items|lenses|reports|gaps'))
    assert.ok(archive.commands.full.includes('--workflow-full --expect-journal-sha256 <sha256-from-manifest>'))
  })
  await check('G7 bounded review keeps every merged view and engine flag inline', async () => {
    const valid = Array.from({length: 8}, (_, i) => finding('processed-' + i))
    const out = await run({
      one: {findings: [...valid.slice(0, 4), ...Array(9).fill(null)]},
      two: {findings: valid.slice(4)},
      merge: prompt => {
        const input = JSON.parse(prompt.split('INPUT:\n')[1])
        const ids = input.map(f => f.id)
        return {findings: Array.from({length: 4}, (_, i) => ({merged_from: ids.slice(i * 2, i * 2 + 2)})).reverse()}
      },
    }, {resultMode: 'bounded', consolidate: true})
    assert.equal(out.findings.length, out.counts.merged)
    assert.equal(out.flags.length, out.delivery.counts.flags)
    assert.equal(out.delivery.preview.findings.omitted, 0)
    assert.equal(out.delivery.preview.flags.omitted, 0)
    assert.deepEqual(new Set(out.findings.map(f => f.id)), new Set(['M1', 'M2', 'M3', 'M4']))
    assert.equal(out.delivery.processedState.mergedFindingsInline, true)
    assert.equal(out.delivery.processedState.engineFlagsInline, true)
  })
  await check('C9 bounded delivery names every critical and ASK finding by id however much it sheds', async () => {
    const big = Array.from({length: 14}, (_, i) => finding('critical-' + i + '界'.repeat(3000),
      i % 2 ? {action: 'ASK', question: 'which?', options: ['a', 'b']} : {}))
    const out = await run({one: {findings: big}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192)
    assert.ok(out.findings.length < big.length, 'fixture must shed findings to exercise the contract')
    assert.ok(Array.isArray(out.delivery.criticalFindingIds), 'delivery.criticalFindingIds missing')
    assert.equal(out.delivery.criticalFindingIds.length, big.length)
    assert.equal(out.delivery.askFindingIds.length, 7)
    assert.ok(out.delivery.criticalFindingIds.every(id => /^F\d+$/.test(id)))
    assert.ok(out.delivery.askFindingIds.every(id => out.delivery.criticalFindingIds.includes(id)))
    assert.equal(out.delivery.processedState.criticalIdsInline, true)
    assert.equal(out.delivery.processedState.askIdsInline, true)
    assert.deepEqual(out.delivery.mergedSelectors, {})
  })
  await check('C9 a bounded run without critical or ASK findings delivers empty id lists, not absent ones', async () => {
    const out = await run({one: {findings: [finding('plain', {critical: false})]}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.deepEqual(out.delivery.criticalFindingIds, [])
    assert.deepEqual(out.delivery.askFindingIds, [])
    assert.equal(out.delivery.processedState.criticalIdsInline, true)
    assert.equal(out.delivery.processedState.askIdsInline, true)
  })
  await check('C9 a merged critical finding lists its member F-ids under mergedSelectors', async () => {
    const valid = Array.from({length: 4}, (_, i) => finding('crit-' + i))
    const out = await run({
      one: {findings: valid.slice(0, 2)}, two: {findings: valid.slice(2)},
      merge: prompt => {
        const input = JSON.parse(prompt.split('INPUT:\n')[1])
        const ids = input.map(f => f.id)
        return {findings: [{merged_from: ids.slice(0, 2)}, {merged_from: ids.slice(2)}]}
      },
    }, {resultMode: 'bounded', consolidate: true})
    assert.deepEqual(new Set(out.delivery.criticalFindingIds), new Set(['M1', 'M2']))
    assert.equal(out.delivery.mergedSelectors.M1.length, 2)
    assert.equal(out.delivery.mergedSelectors.M2.length, 2)
  })
  await check('C9 only a pathological run sheds id lists, and then criticalIdsInline turns false while askIdsInline reports its own list', async () => {
    const huge = Array.from({length: 1500}, (_, i) => finding('c' + i))
    const out = await run({one: {findings: huge}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192)
    assert.equal(out.delivery.counts.criticalIds, huge.length)
    assert.ok(out.delivery.criticalFindingIds.length < huge.length)
    assert.equal(out.delivery.processedState.criticalIdsInline, false)
    assert.equal(out.delivery.processedState.askIdsInline, true, 'no ASK ids were shed, so the ASK flag stays true on its own')
    assert.equal(out.delivery.preview.criticalIds.omitted, huge.length - out.delivery.criticalFindingIds.length)
  })
  await check('C8 regression guard: two findings in one file on different lines can merge into one group', async () => {
    const a = finding('same defect at 10', {file: 'example.py:10'})
    const b = finding('same defect at 40', {file: 'example.py:40'})
    const out = await run({
      one: {findings: [a]}, two: {findings: [b]},
      merge: prompt => {
        const input = JSON.parse(prompt.split('INPUT:\n')[1])
        return {findings: [{merged_from: input.map(f => f.id)}]}
      },
    }, {resultMode: 'full', consolidate: true})
    assert.equal(out.counts.merged, 1)
    assert.equal(out.findings.length, 1)
    assert.equal(out.findings[0].merged_from.length, 2)
  })
  await check('bounded status distinguishes bad coverage and marks complete small payloads', async () => {
    const partial = await run({one: {findings: [finding('valid'), null]}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.equal(partial.delivery.status, 'partial')
    assert.equal(partial.perAgent.find(r => r.key === 'one').status, 'partial')
    const uncovered = await run({one: new Error('transport'), two: {findings: []}}, {resultMode: 'bounded'})
    assert.equal(uncovered.delivery.status, 'uncovered')
    assert.equal(uncovered.perAgent.find(r => r.key === 'one').status, 'failed')
    const failedAll = await run({one: new Error('transport'), two: new Error('transport')}, {resultMode: 'bounded'})
    assert.equal(failedAll.delivery.status, 'failed')
    const small = await run({one: {findings: [finding('small')]}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.equal(small.delivery.payloadComplete, true)
    assert.ok(Object.values(small.delivery.preview).every(row => row.complete))
  })
  await check('invalid resultMode fails before dispatch', async () => {
    let dispatched = false
    const out = await run({
      one: {findings: [finding('first')]},
      two: {findings: []},
      merge: () => { dispatched = true; return {findings: []} },
    }, {resultMode: 'tiny', consolidate: true})
    assert.ok(out.error)
    assert.equal(dispatched, false)
  })
  const mergeModel = async (models, extra = {}) => {
    let pinned = null
    const agents = models.map((model, i) => ({
      key: 'lens' + i, prompt: 'Review fixture.', model, effort: 'medium', agentType: 'general-purpose',
    }))
    const replies = {merge: (prompt, opts) => { pinned = opts.model; return {findings: []} }}
    agents.forEach((a, i) => { replies[a.key] = {findings: [finding('lens ' + i)]} })
    await run(replies, {agents, consolidate: true, ...extra})
    return pinned
  }
  for (const [name, models, expected] of [
    ['an all-sonnet panel', ['sonnet', 'sonnet'], 'sonnet'],
    ['a sonnet panel with one opus lens', ['sonnet', 'sonnet', 'opus'], 'sonnet'],
    ['an all-opus panel', ['opus', 'opus'], 'opus'],
    ['an unpinned panel takes the engine default', [undefined, undefined], 'sonnet'],
  ]) {
    await check('consolidation follows the caller tier: ' + name + ' merges on ' + expected, async () => {
      assert.equal(await mergeModel(models), expected)
    })
  }
  await check('consolidation on a foreign transport uses the transport default', async () => {
    const transport = {name: 'codex', ids: ['gpt-test'], default: 'gpt-test', efforts: ['low', 'medium']}
    assert.equal(await mergeModel(['gpt-test', 'gpt-test'], {__transport: transport}), 'gpt-test')
  })
  console.log(`review-fanout lossless: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(error => { console.error(error); process.exit(1) })
