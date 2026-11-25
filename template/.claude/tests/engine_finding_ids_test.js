#!/usr/bin/env node
// Proof that review_fanout.js gives every returned entry one id, rejected or not, the same id
// tools/review_result.py and tools/session_digest.py give it (tests/fixtures/finding_id_vectors.json,
// also read by test_finding_ids.py): a rejected entry keeps its id and states its reason, and each
// fold subject receives the ids post_check checks.
const path = require('path')
const fs = require('fs')
const assert = require('node:assert/strict')
const engineRun = require('./_engine_run')
const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'finding_id_vectors.json'), 'utf8'))
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
const engine = engineRun.load(path.join(__dirname, '..', 'workflows', 'review_fanout.js'))
const checked = { toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture input was read' }
let passed = 0
let failed = 0
const check = async (name, test) => {
  try { await test(); passed++; console.log('ok   ' + name) } catch (error) { failed++; console.log('FAIL ' + name + ': ' + error.message) }
}
const run = async () => {
  const prompts = []
  const agent = async (prompt, opts) => {
    if (opts.phase === 'Fold') { prompts.push(prompt); return undefined }
    if (opts.phase === 'Merge' || opts.phase === 'Verify') return undefined
    const seat = vectors.seats.find(s => 'review:' + s.key === opts.label)
    return { findings: seat.findings, checked, gaps: [] }
  }
  const parallel = thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn).catch(() => null)))
  const args = {
    agents: vectors.seats.map(s => ({ key: s.key, prompt: 'Review fixture.', model: 'sonnet', effort: 'medium',
      kind: 'author', mustRead: [{ path: 'fixture', lines: 40 }] })),
    resultMode: 'full', spillDir: '.claude/scratch/fold_ids_fixture',
    checker: { model: 'sonnet', effort: 'medium' },
    __toolProfiles: { verify: { agentType: 'general-purpose', tier: 'fanout' } },
    fold: { subjects: vectors.foldSubjects.map((s, i) => ({ root: '.', snapshot: '.claude/scratch/fold_ids_fixture/snap' + (i + 1) + '.json', files: s.files })),
      templatePath: '.claude/fixture/fold_template.md', tier: 'fanout', model: 'sonnet', effort: 'medium' },
  }
  const out = await engine(engineRun.withHost(args, HOST_TRANSPORT), agent, parallel, () => {}, () => {})
  return { out, prompts }
}
;(async () => {
  const { out, prompts } = await run()
  await check('every valid entry takes the id of its position across the run', async () => {
    const expected = vectors.ids.flat().filter(Boolean)
    assert.deepEqual(out.sources.map(s => s.id), expected)
  })
  await check('a rejected entry keeps its id and the flag names the id and the failed field', async () => {
    for (const rejected of vectors.rejected) {
      const flag = out.flags.find(f => f.kind === 'lens-invalid-entry' && f.lens === rejected.seat && f.detail.includes(rejected.id + ' '))
      assert.ok(flag, 'no lens-invalid-entry flag naming ' + rejected.id + ': ' + JSON.stringify(out.flags.map(f => f.detail)))
      assert.ok(flag.detail.includes(rejected.reasonContains), flag.detail)
    }
    assert.equal(out.counts.rejected, vectors.rejected.length)
  })
  await check('each fold subject is handed exactly the ids of its findings', async () => {
    assert.equal(prompts.length, vectors.foldSubjects.length)
    vectors.foldSubjects.forEach((subject, i) => {
      const line = prompts[i].split('\n').find(l => l.startsWith('CONSOLIDATED FINDINGS ('))
      assert.ok(line && line.endsWith(': ' + subject.ids.join(', ') + '):'), line)
      assert.ok(subject.ids.every(id => prompts[i].includes('"id":"' + id + '"')))
    })
  })
  console.log(`\nengine_finding_ids_test: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})()
