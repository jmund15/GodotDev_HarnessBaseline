#!/usr/bin/env node
const path = require('path')
const assert = require('node:assert/strict')
const engineRun = require('./_engine_run')
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
// The merge step is self-started, so it pins from args.checker. The test supplies the host pin for the host transport only.
const HOST_CHECKER = { model: 'sonnet', effort: 'medium' }
const withHost = args => {
  const hosted = engineRun.withHost(args, HOST_TRANSPORT)
  return hosted.checker ? hosted : { ...hosted, checker: HOST_CHECKER }
}
const engine = engineRun.load(path.join(__dirname, '..', 'workflows', 'review_fanout.js'))
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
// A legal gap (commands/agents/review_agents.md *Seat coverage contract*) names an item its seat's must-read list holds.
const CAP = { item: 'src/scene.tscn', reason: 'capability' }
// A seat's must-read list when its test is not about one: the `fixture` file the default provenance reads whole,
// plus each item a typed gap of its reply names.
const FIXTURE_ENTRY = { path: 'fixture', lines: 40 }
const mustFor = reply => [FIXTURE_ENTRY, ...((reply && Array.isArray(reply.gaps)) ? reply.gaps : [])
  .filter(gap => gap && typeof gap.item === 'string' && gap.item.trim()).map(gap => ({ path: gap.item, lines: 40 }))]
const finding = (description, more = {}) => ({
  agent: 'source', action: 'FIX', category: 'bug', critical: true,
  file: 'example.py:42', description, old: 'fixture old', new: 'fixture new', rationale: 'Independent source evidence', ...more,
})
const config = {agents: ['one', 'two'].map(key => ({
  key, prompt: 'Review fixture.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose',
})), resultMode: 'full'}
let agentCalls = 0
async function run(replies, extra = {}) {
  agentCalls = 0
  const agent = async (prompt, opts) => {
    agentCalls++
    if (opts.phase === 'Merge') {
      if (replies.merge instanceof Error) throw replies.merge
      return typeof replies.merge === 'function' ? replies.merge(prompt, opts) : replies.merge
    }
    const value = replies[opts.label.slice('review:'.length)]
    if (value instanceof Error) throw value
    return withCoverage(value)
  }
  const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn).catch(() => null)))
  const args = {...config, ...extra}
  args.agents = args.agents.map(a => a.mustRead === undefined ? {...a, mustRead: mustFor(replies[a.key])} : a)
  return engine(withHost(args), agent, parallel, () => {}, () => {})
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
  await check('the close check expects the rejected entries the journal still holds', async () => {
    const commands = []
    const agent = async (prompt, opts) => {
      if (opts.phase === 'Verify') { commands.push(prompt); return undefined }
      return opts.phase === 'Merge' ? undefined : withCoverage({one: {findings: [finding('null location', {file: null})]},
        two: {findings: [finding('blank location', {file: ''}), finding('no line', {file: 'path.py'})]}}[opts.label.slice('review:'.length)])
    }
    const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn).catch(() => null)))
    const args = {...config, __toolProfiles: {verify: {agentType: 'general-purpose', tier: 'fanout'}}}
    args.agents = args.agents.map(a => ({...a, mustRead: [FIXTURE_ENTRY]}))
    const out = await engine(withHost(args), agent, parallel, () => {}, () => {})
    assert.equal(out.counts.rejected, 2)
    assert.equal(commands.length, 1)
    assert.ok(commands[0].includes('--expect-total ' + (out.counts.total + 2) + ' '), commands[0])
    assert.ok(commands[0].includes('--expect-sources ' + (out.counts.raw + 2) + ' '), commands[0])
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
    const out = await run({one: {findings: [], checked: null, gaps: [{ item: 'fixture', reason: 'outside-lens' }]}, two: {findings: []}})
    const row = out.perAgent.find(r => r.key === 'one')
    assert.equal(row.status, 'uncovered')
    const unlisted = await run({one: {findings: [], checked: null}, two: {findings: []}})
    assert.equal(unlisted.perAgent.find(r => r.key === 'one').status, 'failed', 'with no provenance its must-read entry is unread')
    assert.ok(out.flags.some(f => f.lens === 'one' && f.kind.includes('coverage')))
  })
  await check('G3 a capability gap makes a lens partial even when it returned findings', async () => {
    const out = await run({one: {findings: [finding('partial work')], gaps: [CAP]}, two: {findings: []}})
    assert.equal(out.perAgent.find(r => r.key === 'one').status, 'partial')
    assert.ok(out.flags.some(f => f.lens === 'one' && f.kind === 'lens-gaps'))
  })
  for (const merge of [null, {findings: []}, {findings: [finding('invalid merge', {merged_from: ['F1', 'F1']})]}]) {
    await check('failed or incomplete merge falls back to every original: ' + JSON.stringify(merge), async () => {
      const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]}, merge})
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
      two: {findings: [finding('second', {old: 'before B', new: 'after B'})]}, merge: grouped()})
    assert.equal(out.findings[0].old, null)
    assert.equal(out.findings[0].new, null)
    assert.deepEqual(out.sources.map(s => s.finding.old), ['before A', 'before B'])
  })
  await check('identical edit pairs remain verbatim instead of being duplicated', async () => {
    const same = finding('same defect', {old: 'before', new: 'after'})
    const out = await run({one: {findings: [same]}, two: {findings: [same]}, merge: grouped()})
    assert.equal(out.findings[0].old, 'before')
    assert.equal(out.findings[0].new, 'after')
    assert.equal(out.counts.raw, 2)
    assert.equal(out.counts.exactDuplicates, 1)
  })
  await check('merged group carries the strongest minRigor', async () => {
    const out = await run({one: {findings: [finding('first defect', {minRigor: 'thorough'})]},
      two: {findings: [finding('second defect', {minRigor: 'normal'})]}, merge: grouped()})
    assert.equal(out.findings[0].minRigor, 'normal')
  })
  await check('merged group drops minRigor when a member lacks it', async () => {
    const out = await run({one: {findings: [finding('first defect', {minRigor: 'light'})]},
      two: {findings: [finding('second defect')]}, merge: grouped()})
    assert.equal(Object.prototype.hasOwnProperty.call(out.findings[0], 'minRigor'), false)
  })
  await check('merger cannot invent question options scope or claim text', async () => {
    const same = finding('original claim', {question: 'source question', options: ['source option'], scope: ['source.py']})
    const out = await run({one: {findings: [same]}, two: {findings: [same]},
      merge: grouped({question: 'invented question', options: ['invented'], scope: ['outside.py']})})
    assert.equal(out.findings[0].question, same.question)
    assert.deepEqual(out.findings[0].options, same.options)
    assert.deepEqual(out.findings[0].scope, same.scope)
    assert.equal(out.findings[0].description, same.description)
  })
  await check('singleton grouping cannot rewrite original claim fields', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: {findings: [finding('invented A', {merged_from: ['F1']}), finding('invented B', {merged_from: ['F2']})]}})
    assert.deepEqual(out.findings.map(f => f.description), ['first', 'second'])
  })
  await check('merger can return membership only without copying original evidence', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: {findings: [{merged_from: ['F1', 'F2']}]}})
    assert.equal(out.findings.length, 1)
    assert.deepEqual(out.flags, [])
    assert.equal(out.findings[0].critical, true)
  })
  await check('G6 merge groups cannot cross file anchors', async () => {
    const out = await run({one: {findings: [finding('first', {file: 'one.py:1'}), finding('third', {file: 'two.py:3'})]},
      two: {findings: [finding('second', {file: 'two.py:2'}), finding('fourth', {file: 'one.py:4'})]},
      merge: {findings: [{merged_from: ['F1', 'F3']}, {merged_from: ['F2', 'F4']}]}})
    assert.equal(out.findings.length, 4)
    assert.ok(out.flags.some(f => f.kind === 'consolidate-invalid' && f.detail.includes('different files')))
  })
  await check('G12 successful merge output is re-sorted by critical and action tier', async () => {
    const out = await run({
      one: {findings: [finding('weak', {critical: false, action: 'PLAN', category: 'improvement'})]},
      two: {findings: [finding('strong', {critical: true, action: 'FIX', category: 'bug'})]},
      merge: {findings: [{merged_from: ['F1']}, {merged_from: ['F2']}]},
    })
    assert.deepEqual(out.findings.map(f => f.description), ['strong', 'weak'])
  })
  await check('thrown merge falls back to all available original findings', async () => {
    const out = await run({one: {findings: [finding('first')]}, two: {findings: [finding('second')]},
      merge: new Error('merge transport failure')})
    assert.equal(out.findings.length, 2)
    assert.equal(out.sources.length, 2)
    assert.ok(out.flags.some(f => f.kind === 'consolidate-no-return'))
  })
  await check('merge input uses engine IDs even when sources supply conflicting IDs', async () => {
    const out = await run({one: {findings: [finding('first', {id: 'foreign'})]},
      two: {findings: [finding('second', {id: 'foreign'})]}, merge: prompt => {
        const input = JSON.parse(prompt.split('CANDIDATE FINDINGS:\n')[1])
        assert.deepEqual(input.map(f => f.id), ['F1', 'F2'])
        return {findings: input.map(f => ({merged_from: [f.id]}))}
      }})
    assert.deepEqual(out.flags, [])
  })
  await check('conflicting grouped options and scope come from the deciding member; sources keep their own', async () => {
    const out = await run({one: {findings: [finding('first', {options: ['A'], scope: ['a.py']})]},
      two: {findings: [finding('second', {options: ['B'], scope: ['b.py']})]},
      merge: grouped()})
    assert.deepEqual(out.findings[0].options, ['A'])
    assert.deepEqual(out.findings[0].scope, ['a.py'])
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
  await check('a bounded run that sheds rows offers no resume and keeps the archive retrieval commands', async () => {
    const bigFindings = Array.from({ length: 60 }, (_, i) => finding('finding-' + i + '界'.repeat(3000)))
    const manyInvalidEntries = Array.from({ length: 300 }, () => ({ bad: 'x'.repeat(80) }))
    const out = await run({
      one: { findings: [...bigFindings, ...manyInvalidEntries] },
      two: { findings: [] },
    }, { resultMode: undefined })
    assert.equal(out.delivery.payloadComplete, false)
    assert.ok(out.findings.length < bigFindings.length, 'fixture must shed findings')
    assert.equal(Object.prototype.hasOwnProperty.call(out.delivery, 'resume'), false, 'a shed result must not offer a paid retrieval resume')
    const commands = out.delivery.archive.commands
    assert.deepEqual(Object.keys(commands).sort(), ['full', 'manifest', 'page', 'select'])
    assert.ok(commands.full.includes('--workflow-full --expect-journal-sha256 <sha256-from-manifest>'))
  })
  // Flags with no archive copy: the journal holds raw lens returns only, so these engine-computed kinds never shed.
  const NEVER_SHED = kind => kind.startsWith('consolidate-') || kind === 'tier-shape' || kind === 'fold-routing'
  const drainArgs = mode => ({
    resultMode: mode, spillDir: '.claude/scratch/fold_fixture',
    fold: { subjects: [{ root: '.', snapshot: '.claude/scratch/fold_fixture/snapshot.json', files: ['.claude/plans/p.md'] }],
      templatePath: '.claude/fixture/fold_template.md', tier: 'fanout', model: 'sonnet', effort: 'medium' },
  })
  const drainReplies = () => ({
    one: { findings: [
      ...Array.from({ length: 400 }, (_, i) => finding('drain-' + i + '界'.repeat(2000), { file: 'src/drain' + i + '.py:1' })),
      finding('ask no question', { critical: false, action: 'ASK', old: null, new: null, question: null, options: ['A'] }),
      finding('fix no edit', { critical: false, old: null, new: null }),
      finding('absolute citation', { file: '/elsewhere/abs.py:1' }),
      ...Array.from({ length: 300 }, () => ({ bad: 'x'.repeat(80) })),
    ] },
    two: { findings: [finding('second lens', { file: 'src/drain0.py:1' })] },
    merge: new Error('merge died'),
  })
  await check('an oversized bounded run drains every other group and still retains every consolidate-*, tier-shape and fold-routing flag', async () => {
    const full = await run(drainReplies(), drainArgs('full'))
    const kept = full.flags.filter(f => NEVER_SHED(f.kind))
    assert.ok(kept.some(f => f.kind.startsWith('consolidate-')), 'fixture must raise a consolidate-* flag')
    assert.ok(kept.some(f => f.kind === 'tier-shape'), 'fixture must raise a tier-shape flag')
    assert.ok(kept.some(f => f.kind === 'fold-routing'), 'fixture must raise a fold-routing flag')
    const out = await run(drainReplies(), drainArgs('bounded'))
    assert.equal(out.delivery.payloadComplete, false)
    assert.equal(Object.prototype.hasOwnProperty.call(out.delivery, 'resume'), false)
    assert.deepEqual(out.flags.filter(f => NEVER_SHED(f.kind)).map(f => f.kind), kept.map(f => f.kind))
    assert.equal(out.sources.length, 0, 'sources drain first')
    assert.equal(out.flags.filter(f => !NEVER_SHED(f.kind)).length, 0, 'every sheddable flag drains')
    assert.equal(out.findings.length, 0, 'every finding drains')
    assert.equal(out.perAgent.length, 0, 'perAgent drains')
    assert.equal(out.delivery.preview.flags.total, full.flags.length)
    assert.equal(out.delivery.preview.flags.shown, kept.length)
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192 || out.delivery.byteLimitExceeded === true)
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
    assert.deepEqual(Object.keys(preview).sort(), ['criticalIds', 'decisionIds', 'findings', 'flags', 'nonVerified', 'perAgent', 'reports', 'sources'])
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
        const input = JSON.parse(prompt.split('CANDIDATE FINDINGS:\n')[1])
        const ids = input.map(f => f.id)
        return {findings: Array.from({length: 4}, (_, i) => ({merged_from: ids.slice(i * 2, i * 2 + 2)})).reverse()}
      },
    }, {resultMode: 'bounded'})
    assert.equal(out.findings.length, out.counts.merged)
    assert.equal(out.flags.length, out.delivery.counts.flags)
    assert.equal(out.delivery.preview.findings.omitted, 0)
    assert.equal(out.delivery.preview.flags.omitted, 0)
    assert.deepEqual(new Set(out.findings.map(f => f.id)), new Set(['M1', 'M2', 'M3', 'M4']))
    assert.equal(out.delivery.processedState.mergedFindingsInline, true)
    assert.equal(out.delivery.processedState.engineFlagsInline, true)
  })
  await check('C9 bounded delivery names every critical and decision finding by id however much it sheds', async () => {
    const big = Array.from({length: 14}, (_, i) => finding('critical-' + i + '界'.repeat(3000),
      i % 2 ? {action: 'ASK', question: 'which?', options: ['a', 'b']} : {}))
    const out = await run({one: {findings: big}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192)
    assert.ok(out.findings.length < big.length, 'fixture must shed findings to exercise the contract')
    assert.ok(Array.isArray(out.delivery.criticalFindingIds), 'delivery.criticalFindingIds missing')
    assert.equal(out.delivery.criticalFindingIds.length, big.length)
    assert.equal(out.delivery.decisionFindingIds.length, 7)
    assert.ok(out.delivery.criticalFindingIds.every(id => /^F\d+$/.test(id)))
    assert.ok(out.delivery.decisionFindingIds.every(id => out.delivery.criticalFindingIds.includes(id)))
    assert.equal(out.delivery.processedState.criticalIdsInline, true)
    assert.equal(out.delivery.processedState.decisionIdsInline, true)
    assert.deepEqual(out.delivery.mergedSelectors, {})
  })
  await check('C9 a bounded run without critical or decision findings delivers empty id lists, not absent ones', async () => {
    const out = await run({one: {findings: [finding('plain', {critical: false})]}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.deepEqual(out.delivery.criticalFindingIds, [])
    assert.deepEqual(out.delivery.decisionFindingIds, [])
    assert.equal(out.delivery.processedState.criticalIdsInline, true)
    assert.equal(out.delivery.processedState.decisionIdsInline, true)
  })
  await check('C9 a merged critical finding lists its member F-ids under mergedSelectors', async () => {
    const valid = Array.from({length: 4}, (_, i) => finding('crit-' + i))
    const out = await run({
      one: {findings: valid.slice(0, 2)}, two: {findings: valid.slice(2)},
      merge: prompt => {
        const input = JSON.parse(prompt.split('CANDIDATE FINDINGS:\n')[1])
        const ids = input.map(f => f.id)
        return {findings: [{merged_from: ids.slice(0, 2)}, {merged_from: ids.slice(2)}]}
      },
    }, {resultMode: 'bounded'})
    assert.deepEqual(new Set(out.delivery.criticalFindingIds), new Set(['M1', 'M2']))
    assert.equal(out.delivery.mergedSelectors.M1.length, 2)
    assert.equal(out.delivery.mergedSelectors.M2.length, 2)
  })
  await check('C9 only a pathological run sheds id lists, and then criticalIdsInline turns false while decisionIdsInline reports its own list', async () => {
    const huge = Array.from({length: 1500}, (_, i) => finding('c' + i))
    const out = await run({one: {findings: huge}, two: {findings: []}}, {resultMode: 'bounded'})
    assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192)
    assert.equal(out.verification.status, 'unavailable')
    assert.equal(out.verification.counts.UNCHECKED, huge.length)
    assert.equal(typeof out.verification.report, 'string')
    assert.equal(out.delivery.processedState.nonVerifiedInline, false)
    assert.deepEqual(out.verification.nonVerified, huge.slice(0, out.verification.nonVerified.length).map((_, i) => 'F' + (i + 1)))
    assert.equal(out.delivery.preview.nonVerified.total, huge.length)
    assert.equal(out.delivery.preview.nonVerified.shown, out.verification.nonVerified.length)
    assert.equal(out.delivery.preview.nonVerified.omitted, huge.length - out.verification.nonVerified.length)
    assert.equal(out.delivery.counts.criticalIds, huge.length)
    assert.ok(out.delivery.criticalFindingIds.length < huge.length)
    assert.equal(out.delivery.processedState.criticalIdsInline, false)
    assert.equal(out.delivery.processedState.decisionIdsInline, true, 'no decision ids were shed, so the decision flag stays true on its own')
    assert.equal(out.delivery.preview.criticalIds.omitted, huge.length - out.delivery.criticalFindingIds.length)
  })
  await check('F4 a merged ASK+FIX group comes out an ASK and is listed in decisionFindingIds', async () => {
    const out = await run({
      one: {findings: [finding('same defect', {critical: false})]},
      two: {findings: [finding('same defect', {critical: false, action: 'ASK', old: null, new: null,
        question: 'Does the owner still author this file?', options: ['Keep it', 'Retire it; wins when the owner stopped authoring it']})]},
      merge: {findings: [{merged_from: ['F1', 'F2']}]},
    }, {resultMode: 'bounded'})
    assert.equal(out.findings.length, 1)
    assert.equal(out.findings[0].action, 'ASK')
    assert.deepEqual(out.delivery.decisionFindingIds, ['M1'])
    assert.deepEqual(out.delivery.mergedSelectors, {M1: ['F1', 'F2']})
  })
  await check('O2 a merged ASK keeps every distinct member question and the union of its ASK options', async () => {
    const out = await run({
      one: {findings: [finding('same defect', {critical: false, action: 'ASK', old: null, new: null,
        question: 'Keep the file?', options: ['Keep it', 'Retire it; wins when unused']})]},
      two: {findings: [
        finding('same defect', {critical: false, action: 'ASK', old: null, new: null,
          question: 'Who owns it?', options: ['Retire it; wins when unused', 'Move it; wins when shared']}),
        finding('same defect', {critical: false, action: 'PLAN', old: null, new: null, question: ' ', scope: ['a.py'],
          options: ['Plan only; wins when never']}),
        finding('same defect', {critical: false, action: 'ASK', old: null, new: null,
          question: 'Keep the file?', options: ['Keep it']}),
      ]},
      merge: {findings: [{merged_from: ['F1', 'F2', 'F3', 'F4']}]},
    })
    assert.equal(out.findings.length, 1)
    assert.equal(out.findings[0].action, 'ASK')
    assert.equal(out.findings[0].question, 'Keep the file? / Who owns it?')
    assert.deepEqual(out.findings[0].options, ['Keep it', 'Retire it; wins when unused', 'Move it; wins when shared'])
  })
  await check('F49 a merged FIX + resolved PLAN + open PLAN group is the open PLAN, with its question, options and scope', async () => {
    const out = await run({
      one: {findings: [
        finding('same defect', {critical: false}),
        finding('same defect', {critical: false, action: 'PLAN', old: null, new: null, question: null,
          options: ['Resolved plan'], scope: ['resolved.py']}),
      ]},
      two: {findings: [finding('same defect', {critical: false, action: 'PLAN', old: null, new: null,
        question: 'Which design?', options: ['Design A', 'Design B; wins when shared'], scope: ['open.py']})]},
      merge: {findings: [{merged_from: ['F1', 'F2', 'F3']}]},
    }, {resultMode: 'bounded'})
    assert.equal(out.findings.length, 1)
    const row = out.findings[0]
    assert.deepEqual([row.action, row.question, row.options, row.scope],
      ['PLAN', 'Which design?', ['Design A', 'Design B; wins when shared'], ['open.py']])
    assert.deepEqual(out.delivery.decisionFindingIds, ['M1'])
  })
  await check('F49 a merged FIX + resolved PLAN group is the resolved PLAN, with its options and scope', async () => {
    const out = await run({
      one: {findings: [finding('same defect', {critical: false})]},
      two: {findings: [finding('same defect', {critical: false, action: 'PLAN', old: null, new: null, question: null,
        options: ['Resolved plan'], scope: ['resolved.py']})]},
      merge: {findings: [{merged_from: ['F1', 'F2']}]},
    }, {resultMode: 'bounded'})
    const row = out.findings[0]
    assert.deepEqual([row.action, row.question, row.options, row.scope], ['PLAN', null, ['Resolved plan'], ['resolved.py']])
    assert.deepEqual(out.delivery.decisionFindingIds, [])
  })
  await check('F4 a merge that names the least restrictive decider is invalid', async () => {
    const out = await run({
      one: {findings: [finding('same defect', {critical: false})]},
      two: {findings: [finding('same defect', {critical: false, action: 'PLAN', old: null, new: null, scope: ['a.py']})]},
      merge: {findings: [{merged_from: ['F1', 'F2'], action: 'FIX'}]},
    })
    assert.ok(out.flags.some(f => f.kind === 'consolidate-invalid'))
    assert.equal(out.findings.length, 2)
  })
  await check('F29 decisionFindingIds lists every ASK and open PLAN, never a resolved PLAN or FIX', async () => {
    const out = await run({one: {findings: [
      finding('fix', {critical: false}),
      finding('ask', {critical: false, action: 'ASK', old: null, new: null, question: 'Owner intent?', options: ['A']}),
      finding('open plan', {critical: false, action: 'PLAN', old: null, new: null, question: 'Which design?', options: ['A'], scope: ['a.py']}),
      finding('resolved plan', {critical: false, action: 'PLAN', old: null, new: null, question: null, options: ['A'], scope: ['a.py']}),
    ]}, two: {findings: []}}, {resultMode: 'bounded'})
    const idOf = d => out.findings.find(f => f.description === d).id
    assert.deepEqual(new Set(out.delivery.decisionFindingIds), new Set([idOf('ask'), idOf('open plan')]))
    assert.equal(Object.prototype.hasOwnProperty.call(out.delivery, 'askFindingIds'), false)
    assert.equal(out.delivery.processedState.decisionIdsInline, true)
  })
  const tierShape = out => out.flags.filter(f => f.kind === 'tier-shape')
  await check('F28 tier-shape fires once per violated predicate with its count and finding ids', async () => {
    const out = await run({one: {findings: [
      finding('ask no question', {critical: false, action: 'ASK', old: null, new: null, question: null, options: ['A']}),
      finding('ask recommended', {critical: false, action: 'ASK', old: null, new: null, question: 'Owner intent?', options: ['A (Recommended)']}),
      finding('bare alternative', {critical: false, action: 'PLAN', old: null, new: null, options: ['A', 'B'], scope: ['a.py']}),
      finding('plan no scope', {critical: false, action: 'PLAN', old: null, new: null, options: ['A'], scope: null}),
      finding('fix no edit', {critical: false, old: null, new: null}),
    ]}, two: {findings: []}})
    const byPredicate = Object.fromEntries(tierShape(out).map(f => [f.detail.split(':')[0], f.detail]))
    assert.deepEqual(Object.keys(byPredicate).sort(),
      ['alternative-without-wins-when', 'ask-null-question', 'ask-recommended', 'fix-null-edit', 'plan-no-scope'])
    assert.equal(byPredicate['ask-null-question'], 'ask-null-question: 1 finding (F1)')
    assert.equal(byPredicate['ask-recommended'], 'ask-recommended: 1 finding (F2)')
    assert.equal(byPredicate['alternative-without-wins-when'], 'alternative-without-wins-when: 1 finding (F3)')
    assert.equal(byPredicate['plan-no-scope'], 'plan-no-scope: 1 finding (F4)')
    assert.equal(byPredicate['fix-null-edit'], 'fix-null-edit: 1 finding (F5)')
    assert.equal(out.perAgent.find(r => r.key === 'one').status, 'completed')
  })
  await check('F28 tier-shape stays silent on compliant findings', async () => {
    const out = await run({one: {findings: [
      finding('fix', {critical: false}),
      finding('ask', {critical: false, action: 'ASK', old: null, new: null, question: 'Is this still the owner\'s file?',
        options: ['Keep it', 'Retire it; wins when the owner stopped authoring it']}),
      finding('resolved plan', {critical: false, action: 'PLAN', old: null, new: null, question: null,
        options: ['Do A', 'Alternative: B; wins when C'], scope: ['a.py']}),
    ]}, two: {findings: []}})
    assert.deepEqual(tierShape(out), [])
  })
  await check('C8 regression guard: two findings in one file on different lines can merge into one group', async () => {
    const a = finding('same defect at 10', {file: 'example.py:10'})
    const b = finding('same defect at 40', {file: 'example.py:40'})
    const out = await run({
      one: {findings: [a]}, two: {findings: [b]},
      merge: prompt => {
        const input = JSON.parse(prompt.split('CANDIDATE FINDINGS:\n')[1])
        return {findings: [{merged_from: input.map(f => f.id)}]}
      },
    }, {resultMode: 'full'})
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
    const out = await run({
      one: {findings: [finding('first')]},
      two: {findings: []},
      merge: {findings: []},
    }, {resultMode: 'tiny'})
    assert.ok(out.error)
    assert.equal(agentCalls, 0)
  })
  const mergeModel = async (models, extra = {}) => {
    let pinned = null
    const agents = models.map((model, i) => ({
      key: 'lens' + i, prompt: 'Review fixture.', model, effort: 'medium', agentType: 'general-purpose',
    }))
    const replies = {merge: (prompt, opts) => { pinned = opts.model; return {findings: []} }}
    agents.forEach((a, i) => { replies[a.key] = {findings: [finding('lens ' + i)]} })
    await run(replies, {agents, ...extra})
    return pinned
  }
  for (const [name, models, expected] of [
    ['an all-sonnet panel', ['sonnet', 'sonnet'], 'sonnet'],
    ['a sonnet panel with one opus lens', ['sonnet', 'sonnet', 'opus'], 'sonnet'],
    ['an all-opus panel', ['opus', 'opus'], 'sonnet'],
  ]) {
    await check('consolidation runs on the fanout tier pin whatever the panel: ' + name + ' merges on ' + expected, async () => {
      assert.equal(await mergeModel(models), expected)
    })
  }
  await check('consolidation pins from args.checker on the transport under test', async () => {
    const transport = {name: 'off', ids: ['gpt-test'], efforts: ['low', 'medium']}
    assert.equal(await mergeModel(['gpt-test', 'gpt-test'], {__transport: transport, checker: {model: 'gpt-test', effort: 'low'}}), 'gpt-test')
  })
  await check('an unpinned panel is refused before any agent starts', async () => {
    const out = await run({}, {agents: [{key: 'lens0', prompt: 'Review fixture.', effort: 'medium', agentType: 'general-purpose'}]})
    assert.match(String(out.error || ''), /every job \(orchestration §5\)/)
    assert.equal(agentCalls, 0)
  })
  const seatAgents = keys => keys.map(key => ({ key, prompt: 'Review fixture.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose' }))
  const mandateRun = async (replies, catalog, extra = {}) => run(replies, {
    agents: seatAgents(Object.keys(replies)), __lensMandates: catalog, ...extra })
  await check('S-D a two-mandate seat returning checked.mandates keeps that map; the same seat without one is not-reached', async () => {
    const catalog = { one: ['cq.C', 'cq.D'], two: ['cq.C', 'cq.D'] }
    const out = await mandateRun({
      one: { findings: [], checked: checked({ mandates: { 'cq.C': 'clean', 'cq.D': 'not-reached' } }) },
      two: { findings: [] },
    }, catalog)
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.deepEqual(rows.one.mandates, { 'cq.C': 'clean', 'cq.D': 'not-reached' })
    assert.deepEqual(rows.two.mandates, { 'cq.C': 'not-reached', 'cq.D': 'not-reached' })
  })
  await check('S-D a map naming an unknown key or a bad value is invalid: every key not-reached', async () => {
    const catalog = { one: ['cq.C', 'cq.D'], two: ['cq.C', 'cq.D'] }
    const out = await mandateRun({
      one: { findings: [], checked: checked({ mandates: { 'cq.C': 'clean', 'cq.X': 'clean' } }) },
      two: { findings: [], checked: checked({ mandates: { 'cq.C': 'clean', 'cq.D': 'maybe' } }) },
    }, catalog)
    for (const row of out.perAgent) assert.deepEqual(row.mandates, { 'cq.C': 'not-reached', 'cq.D': 'not-reached' })
  })
  await check('S-D a merged seat key expands through every lens it names', async () => {
    const out = await mandateRun({
      'a+b': { findings: [], checked: checked({ mandates: { 'x.1': 'clean', 'x.2': 'findings' } }) },
    }, { a: ['x.1'], b: ['x.2'] })
    assert.deepEqual(out.perAgent[0].mandates, { 'x.1': 'clean', 'x.2': 'findings' })
  })
  await check('S-D a merged seat key the guard injected whole resolves through that entry', async () => {
    const out = await mandateRun({
      'a+b': { findings: [], checked: checked({ mandates: { 'x.1': 'clean', 'x.2': 'findings' } }) },
    }, { 'a+b': ['x.1', 'x.2'] })
    assert.deepEqual(out.perAgent[0].mandates, { 'x.1': 'clean', 'x.2': 'findings' })
  })
  await check('S-D the engine applies the seat downgrade: a capability-partial seat keeps its map tokens', async () => {
    const catalog = { part: ['cq.C', 'cq.D', 'cq.S'], none: ['cq.C', 'cq.D'] }
    const out = await mandateRun({
      part: { findings: [finding('valid', { agent: 'part' })], gaps: [CAP],
        checked: checked({ mandates: { 'cq.C': 'clean', 'cq.D': 'findings', 'cq.S': 'not-reached' } }) },
      none: { findings: [], gaps: [{ item: 'fixture', reason: 'outside-lens' }], checked: checked({ toolsUsed: [], mandates: { 'cq.C': 'clean', 'cq.D': 'clean' } }) },
    }, catalog)
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.equal(rows.part.status, 'partial')
    assert.deepEqual(rows.part.mandates, { 'cq.C': 'clean', 'cq.D': 'findings', 'cq.S': 'not-reached' })
    assert.equal(rows.part.capabilityPartial, true)
    assert.equal(rows.none.status, 'uncovered')
    assert.deepEqual(rows.none.mandates, { 'cq.C': 'not-reached', 'cq.D': 'not-reached' })
  })
  await check('S-D a one-mandate seat derives its outcome from the seat status', async () => {
    const catalog = { done: ['m'], clean: ['m'], part: ['m'], dead: ['m'], shape: ['m'], blk: ['m'] }
    const out = await mandateRun({
      done: { findings: [finding('valid', { agent: 'done' })] },
      clean: { findings: [] },
      part: { findings: [finding('valid', { agent: 'part' })], gaps: [CAP] },
      dead: new Error('no return'),
      shape: { findings: 'not an array' },
      blk: { findings: [], checked: checked({ stoppedAt: 'blocked' }) },
    }, catalog)
    const got = Object.fromEntries(out.perAgent.map(r => [r.key, r.mandates.m]))
    assert.deepEqual(got, { done: 'findings', clean: 'clean', part: 'incomplete', dead: 'failed', shape: 'not-reached', blk: 'blocked' })
  })
  await check('S-D every row carries checkedTargets: the seat\'s toolsUsed strings, empty when none came back', async () => {
    const catalog = { one: ['m'], two: ['m'] }
    const out = await mandateRun({
      one: { findings: [], checked: checked({ toolsUsed: ['Read:a.cs', 'Grep:Source/'] }) },
      two: new Error('no return'),
    }, catalog)
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.deepEqual(rows.one.checkedTargets, ['Read:a.cs', 'Grep:Source/'])
    assert.deepEqual(rows.two.checkedTargets, [])
  })
  await check('S-D a seat with no lens expansion yields an empty mandates map, not a guess', async () => {
    const out = await mandateRun({ one: { findings: [] } }, {})
    assert.deepEqual(out.perAgent[0].mandates, {})
  })
  await check('S12 every seat with a mandate is told to return checked.mandates, a one-mandate seat included', async () => {
    const prompts = {}
    const agent = async (prompt, opts) => { prompts[opts.label] = prompt; return withCoverage({ findings: [] }) }
    const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn)))
    await engine(
      withHost({ agents: seatAgents(['wide', 'narrow']).map(a => ({ ...a, mustRead: [FIXTURE_ENTRY] })), resultMode: 'full',
        __lensMandates: { wide: ['cq.C', 'cq.D'], narrow: ['tq'] } }), agent, parallel, () => {}, () => {})
    assert.ok(prompts['review:wide'].includes('checked.mandates'))
    assert.ok(prompts['review:wide'].includes('cq.C') && prompts['review:wide'].includes('cq.D'))
    assert.ok(prompts['review:narrow'].includes('checked.mandates') && prompts['review:narrow'].includes('tq'))
  })
  await check('U4 a bounded panel of twelve seats under a forced byte cap keeps every seat\'s map in delivery.coverage.seats', async () => {
    const keys = Array.from({ length: 12 }, (_, i) => 'seat' + i)
    const catalog = Object.fromEntries(keys.map(k => [k, [k + '.a', k + '.b']]))
    const replies = Object.fromEntries(keys.map(k => [k, {
      findings: Array.from({ length: 6 }, (_, n) => finding('bulk ' + k + ' ' + n, { agent: k, description: 'd'.repeat(300) })),
      checked: checked({ mandates: { [k + '.a']: 'findings', [k + '.b']: 'clean' } }),
    }]))
    const out = await mandateRun(replies, catalog, { resultMode: 'bounded' })
    assert.ok(out.perAgent.length < 12, 'the cap must shed rows for this case to bite: ' + out.perAgent.length)
    const seats = out.delivery.coverage.seats
    assert.equal(seats.length, 12)
    for (const k of keys) {
      const row = seats.find(s => s.key === k)
      assert.deepEqual(row.mandates, { [k + '.a']: 'findings', [k + '.b']: 'clean' })
      assert.equal(row.checkedTargets, 1)
      assert.equal(row.count, 6)
    }
  })
  await check('S12 each perAgent row carries its valid gap strings as gapTexts; delivery.coverage.seats carries their count only', async () => {
    const replies = {one: {findings: [], gaps: [{ item: 'src/a.cs', reason: 'capability' }]}, two: {findings: []}}
    const out = await run(replies)
    assert.deepEqual(out.perAgent.find(r => r.key === 'one').gapTexts, ['src/a.cs (capability)'])
    assert.deepEqual(out.perAgent.find(r => r.key === 'two').gapTexts, [])
    const bounded = await mandateRun({ one: { findings: [], gaps: [{ item: 'src/b.cs', reason: 'outside-lens' }] } }, { one: ['m'] }, { resultMode: 'bounded' })
    assert.equal(bounded.delivery.coverage.seats[0].gaps, 1)
    assert.ok(!Object.prototype.hasOwnProperty.call(bounded.delivery.coverage.seats[0], 'gapTexts'))
  })
  await check('U4 a wide panel whose seats report several long gaps stays inside the byte cap, seat maps carrying the gap count only', async () => {
    const keys = Array.from({ length: 12 }, (_, i) => 'seat' + i)
    const catalog = Object.fromEntries(keys.map(k => [k, [k + '.a']]))
    const gaps = Array.from({ length: 4 }, (_, n) => ({ item: 'gap' + n + '/' + 'g'.repeat(300), reason: 'capability' }))
    const replies = Object.fromEntries(keys.map(k => [k, {
      findings: [], gaps, checked: checked({ mandates: { [k + '.a']: 'clean' } }),
    }]))
    const out = await mandateRun(replies, catalog, { resultMode: 'bounded' })
    assert.equal(out.delivery.byteLimitExceeded, false)
    assert.equal(out.delivery.coverage.seats.length, 12)
    assert.ok(out.delivery.coverage.seats.every(s => s.gaps === 4 && !('gapTexts' in s)))
  })
  await check('S12 a partial seat that is not capability-partial covers nothing, even with a valid map', async () => {
    const catalog = { rej: ['cq.C', 'cq.D'], inv: ['cq.C', 'cq.D'], blk: ['cq.C', 'cq.D'] }
    const map = { 'cq.C': 'clean', 'cq.D': 'findings' }
    const out = await mandateRun({
      rej: { findings: [finding('valid', { agent: 'cq.D' }), null], gaps: [CAP], checked: checked({ mandates: map }) },
      inv: { findings: [finding('valid', { agent: 'cq.D' })], gaps: [CAP], checked: checked({ basis: '', mandates: map }) },
      blk: { findings: [finding('valid', { agent: 'cq.D' })], gaps: [CAP], checked: checked({ stoppedAt: 'blocked', mandates: map }) },
    }, catalog)
    for (const row of out.perAgent) {
      assert.equal(row.status, 'partial', row.key)
      assert.equal(row.capabilityPartial, false, row.key)
      assert.deepEqual(row.mandates, { 'cq.C': 'incomplete', 'cq.D': 'incomplete' }, row.key)
    }
  })
  await check('S12 a gapped seat with no map is not capability-partial: one key stays incomplete, several stay not-reached', async () => {
    const out = await mandateRun({
      one: { findings: [], gaps: [CAP] },
      many: { findings: [], gaps: [CAP] },
    }, { one: ['m'], many: ['cq.C', 'cq.D'] })
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.equal(rows.one.capabilityPartial, false)
    assert.equal(rows.many.capabilityPartial, false)
    assert.deepEqual(rows.one.mandates, { m: 'incomplete' })
    assert.deepEqual(rows.many.mandates, { 'cq.C': 'not-reached', 'cq.D': 'not-reached' })
  })
  await check('S12 capabilityPartial rides in perAgent and in delivery.coverage.seats', async () => {
    const out = await mandateRun({
      gapped: { findings: [], gaps: [CAP], checked: checked({ mandates: { m: 'clean' } }) },
      plain: { findings: [], checked: checked({ mandates: { m: 'clean' } }) },
    }, { gapped: ['m'], plain: ['m'] }, { resultMode: 'bounded' })
    const seats = Object.fromEntries(out.delivery.coverage.seats.map(r => [r.key, r]))
    assert.equal(seats.gapped.capabilityPartial, true)
    assert.deepEqual(seats.gapped.mandates, { m: 'clean' })
    assert.equal(seats.plain.capabilityPartial, false)
  })
  await check('U4 a small bounded panel lists the same seat maps beside its rows', async () => {
    const out = await mandateRun({ one: { findings: [] } }, { one: ['m'] }, { resultMode: 'bounded' })
    assert.deepEqual(out.delivery.coverage.seats, [{ key: 'one', status: 'completed', stoppedAt: 'exhausted-leads', mandates: { m: 'clean' },
      checkedTargets: 1, count: 0, gaps: 0, capabilityPartial: false }])
  })
  await check('U4 a wide panel whose seats log many targets stays inside the byte cap, seat maps carrying the target count only', async () => {
    const keys = Array.from({ length: 12 }, (_, i) => 'seat' + i)
    const catalog = Object.fromEntries(keys.map(k => [k, [k + '.a']]))
    const targets = Array.from({ length: 40 }, (_, n) => 'Read:Source/Some/Long/Path/File' + n + '.cs')
    const replies = Object.fromEntries(keys.map(k => [k, {
      findings: [finding('bulk ' + k, { agent: k })],
      checked: checked({ toolsUsed: targets, mandates: { [k + '.a']: 'findings' } }),
    }]))
    const out = await mandateRun(replies, catalog, { resultMode: 'bounded' })
    assert.equal(out.delivery.byteLimitExceeded, false)
    assert.ok(out.delivery.coverage.seats.every(s => s.checkedTargets === 40))
  })
  const listed = (key, mustRead) => ({ key, prompt: 'Review fixture.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose', mustRead })
  const A_CS = { path: 'src/a.cs', lines: 437 }
  const IMPACT = { path: 'src/Core/ImpactPhysics.cs', lines: 900 }
  const MUST = [A_CS, IMPACT]
  await check('MR1 a must-read entry the seat never read, with no legal gap, fails the seat at exhausted-leads and keeps its findings', async () => {
    const out = await run({ one: { findings: [finding('partial look')], checked: checked({ toolsUsed: ['Read:src/a.cs:1-437'] }) }, two: { findings: [] } },
      { agents: [listed('one', MUST), listed('two', [A_CS])] })
    const row = out.perAgent.find(r => r.key === 'one')
    assert.equal(row.status, 'failed')
    assert.deepEqual(row.unread, ['src/Core/ImpactPhysics.cs'])
    assert.equal(out.sources.length, 1)
    const flag = out.flags.find(f => f.lens === 'one' && f.kind === 'lens-unread-must-read')
    assert.ok(flag && flag.detail.includes('src/Core/ImpactPhysics.cs') && flag.detail.includes('exhausted-leads'), JSON.stringify(out.flags))
    assert.equal(out.perAgent.find(r => r.key === 'two').status, 'failed', 'two never read src/a.cs either')
    const bounded = await run({ one: { findings: [], checked: checked({ toolsUsed: ['Read:src/a.cs'] }) }, two: { findings: [], checked: checked({ toolsUsed: ['Read:C:/repo/src/a.cs'] }) } },
      { agents: [listed('one', MUST), listed('two', [A_CS])], resultMode: 'bounded' })
    assert.equal(bounded.delivery.coverage.status, 'uncovered')
    const seats = Object.fromEntries(bounded.delivery.coverage.seats.map(s => [s.key, s]))
    assert.deepEqual(seats.one.unread, ['src/Core/ImpactPhysics.cs'])
    assert.equal(seats.two.status, 'completed', 'an absolute Read target engages its repo-relative entry')
  })
  await check('MR2 outside-lens, missing and capability gaps on listed entries keep the seat ok', async () => {
    const read = checked({ toolsUsed: ['Read:src/a.cs'], mandates: { m: 'clean' } })
    const out = await mandateRun({
      outside: { findings: [], checked: read, gaps: [{ item: 'src/Core/ImpactPhysics.cs', reason: 'outside-lens' }] },
      gone: { findings: [], checked: read, gaps: [{ item: 'src/Core/ImpactPhysics.cs', reason: 'missing' }] },
      tool: { findings: [], checked: read, gaps: [{ item: 'src/Core/ImpactPhysics.cs', reason: 'capability' }] },
    }, { outside: ['m'], gone: ['m'], tool: ['m'] }, { agents: ['outside', 'gone', 'tool'].map(k => listed(k, MUST)) })
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.equal(rows.outside.status, 'completed')
    assert.equal(rows.gone.status, 'completed')
    assert.equal(rows.tool.status, 'partial')
    assert.equal(rows.tool.capabilityPartial, true)
    assert.deepEqual(rows.tool.mandates, { m: 'clean' })
    assert.deepEqual(rows.outside.gapTexts, ['src/Core/ImpactPhysics.cs (outside-lens)'])
    assert.ok(!out.flags.some(f => f.kind === 'lens-unread-must-read' || f.kind === 'lens-invalid-gaps'), JSON.stringify(out.flags))
  })
  await check('MR3 a gap that excuses no listed entry is a note on a seat that read its list, and fails nothing', async () => {
    const read = checked({ toolsUsed: ['Read:src/a.cs', 'Read:src/Core/ImpactPhysics.cs'] })
    const out = await run({
      text: { findings: [], checked: read, gaps: ['could not trace callers'] },
      unlisted: { findings: [], checked: read, gaps: [{ item: 'src/other.cs', reason: 'capability' }] },
      reason: { findings: [], checked: read, gaps: [{ item: 'src/a.cs', reason: 'ran out of time' }] },
      extra: { findings: [], checked: read, gaps: [{ item: 'src/a.cs', reason: 'capability', note: 'free text' }] },
      short: { findings: [], checked: checked({ toolsUsed: ['Read:src/a.cs'] }), gaps: [{ item: 'src/other.cs', reason: 'outside-lens' }] },
    }, { agents: ['text', 'unlisted', 'reason', 'extra', 'short'].map(k => listed(k, MUST)) })
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    for (const key of ['text', 'unlisted', 'reason', 'extra']) assert.equal(rows[key].status, 'completed', key)
    assert.equal(rows.short.status, 'failed')
    assert.deepEqual(rows.unlisted.gapNotes, ['src/other.cs (capability)'])
    assert.ok(out.flags.some(f => f.lens === 'text' && f.kind === 'lens-gap-notes'))
    assert.ok(!out.flags.some(f => f.kind === 'lens-invalid-gaps'), JSON.stringify(out.flags))
  })
  await check('MR3b a gap with an item and no reason is a note that names no undefined reason', async () => {
    const read = checked({ toolsUsed: ['Read:src/a.cs', 'Read:src/Core/ImpactPhysics.cs'] })
    const out = await run({
      bare: { findings: [], checked: read, gaps: [{ item: 'src/other.cs' }] },
    }, { agents: ['bare'].map(k => listed(k, MUST)) })
    const rows = Object.fromEntries(out.perAgent.map(r => [r.key, r]))
    assert.equal(rows.bare.status, 'completed')
    assert.deepEqual(rows.bare.gapNotes, ['src/other.cs (no reason)'])
    assert.ok(!out.flags.some(f => f.detail && f.detail.includes('undefined')), JSON.stringify(out.flags))
  })
  await check('MR4 every seat is told its entries with their line counts, the range form and the closed gap reasons; a malformed mustRead refuses the run', async () => {
    const prompts = {}
    const agent = async (prompt, opts) => { prompts[opts.label] = prompt; return withCoverage({ findings: [] }) }
    const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn)))
    await engine(withHost({ agents: [listed('one', MUST)], resultMode: 'full' }), agent, parallel, () => {}, () => {})
    const prompt = prompts['review:one']
    assert.ok(prompt.includes('MUST-READ') && prompt.includes('src/a.cs (437 lines)') && prompt.includes('src/Core/ImpactPhysics.cs (900 lines)'), prompt)
    assert.ok(prompt.includes('Read:<path>:<first>-<last>'), prompt)
    for (const reason of ['outside-lens', 'capability', 'missing']) assert.ok(prompt.includes(reason), reason)
    for (const bad of ['src/a.cs', [], ['src/a.cs'], [{ path: 'src/a.cs' }], [{ path: ' ', lines: 3 }], [{ path: 'src/a.cs', lines: -1 }],
      [{ path: 'src/a.cs', lines: 2.5 }], [{ path: 'src/a.cs', lines: '3' }], [{ path: 'src/a.cs', lines: 3, note: 'x' }], [7]]) {
      const out = await engine(withHost({ agents: [listed('one', bad)] }), agent, parallel, () => {}, () => {})
      assert.ok(out.error && out.error.includes('mustRead'), JSON.stringify(bad) + ' -> ' + JSON.stringify(out))
    }
  })
  await check('MR5 a must-read entry read only in part fails the seat, naming the lines it read', async () => {
    const out = await run({ part: { findings: [], checked: checked({ toolsUsed: ['Read:src/a.cs:1-437', 'Read:src/Core/ImpactPhysics.cs:1-437'] }) } },
      { agents: [listed('part', MUST)] })
    const row = out.perAgent[0]
    assert.equal(row.status, 'failed')
    assert.deepEqual(row.unread, ['src/Core/ImpactPhysics.cs'])
    const flag = out.flags.find(f => f.kind === 'lens-unread-must-read')
    assert.ok(flag && flag.detail.includes('src/Core/ImpactPhysics.cs (read 1-437 of 900)'), JSON.stringify(out.flags))
  })
  await check('MR6 ranges that together cover a must-read entry pass; a hole, or a bare Read of a file past the Read window, fails', async () => {
    const BIG = { path: 'src/Big.cs', lines: 2600 }
    const replies = {
      halves: ['Read:src/a.cs:1-437', 'Read:src/Core/ImpactPhysics.cs:438-900', 'Read:src/Core/ImpactPhysics.cs:1-437'],
      comma: ['Read:src/a.cs:1-437', 'Read:src/Core/ImpactPhysics.cs:1-500,450-900'],
      absolute: ['Read:C:/repo/src/a.cs:1-200', 'Read:src/a.cs:201-437', 'Read:src/Core/ImpactPhysics.cs'],
      paged: ['Read:src/a.cs', 'Read:src/Big.cs:1-2000', 'Read:src/Big.cs:2001-2600'],
      window: ['Read:src/a.cs', 'Read:src/Big.cs'],
      hole: ['Read:src/a.cs:1-437', 'Read:src/Core/ImpactPhysics.cs:1-400', 'Read:src/Core/ImpactPhysics.cs:402-900'],
    }
    const lists = { paged: [A_CS, BIG], window: [A_CS, BIG] }
    const out = await run(Object.fromEntries(Object.entries(replies).map(([k, tools]) => [k, { findings: [], checked: checked({ toolsUsed: tools }) }])),
      { agents: Object.keys(replies).map(k => listed(k, lists[k] || MUST)) })
    const got = Object.fromEntries(out.perAgent.map(r => [r.key, r.status]))
    assert.deepEqual(got, { halves: 'completed', comma: 'completed', absolute: 'completed', paged: 'completed', window: 'failed', hole: 'failed' })
  })
  await check('MR7 a shell read covers its entry whole; a search covers no line', async () => {
    const out = await run({
      shell: { findings: [], checked: checked({ toolsUsed: ['Read:src/a.cs:1-437', 'Bash:git diff -- src/Core/ImpactPhysics.cs'] }) },
      search: { findings: [], checked: checked({ toolsUsed: ['Read:src/a.cs:1-437', 'Grep:src/Core/ImpactPhysics.cs'] }) },
    }, { agents: [listed('shell', MUST), listed('search', MUST)] })
    const got = Object.fromEntries(out.perAgent.map(r => [r.key, r.status]))
    assert.deepEqual(got, { shell: 'completed', search: 'failed' })
  })
  await check('MR8 an agents array with a seat carrying no mustRead is refused at the call, naming the seat, before any agent runs', async () => {
    let calls = 0
    const agent = async () => { calls++; return withCoverage({ findings: [] }) }
    const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn)))
    const bare = { key: 'bare', prompt: 'Review fixture.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose' }
    const out = await engine(withHost({ agents: [listed('one', MUST), bare] }), agent, parallel, () => {}, () => {})
    assert.ok(out.error && out.error.includes('mustRead') && out.error.includes('bare') && !out.error.includes('one,'), JSON.stringify(out))
    assert.equal(calls, 0)
  })
  console.log(`review-fanout lossless: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(error => { console.error(error); process.exit(1) })
