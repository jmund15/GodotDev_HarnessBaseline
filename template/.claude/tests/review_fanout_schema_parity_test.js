#!/usr/bin/env node
const fs = require('fs')
const path = require('path')
const assert = require('node:assert/strict')

const engineRun = require('./_engine_run')

const canonical = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'schemas', 'review_findings.json'), 'utf8'))
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
let captured = null
const agent = async (_prompt, opts) => {
  if (opts.phase === 'Review') captured = opts.schema
  return {
    findings: [],
    checked: {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture read'},
    gaps: [],
  }
}
const parallel = thunks => Promise.all(thunks.map(fn => fn()))

;(async () => {
  await engineRun.load(path.join(__dirname, '..', 'workflows', 'review_fanout.js'))(engineRun.withHost({
    resultMode: 'full',
    checker: {model: 'sonnet', effort: 'medium'},
    agents: [{key: 'lens', prompt: 'Review.', model: 'sonnet', effort: 'medium', kind: 'author',
      mustRead: [{path: 'fixture', lines: 1}]}],
  }, HOST_TRANSPORT), agent, parallel, () => {}, () => {})
  const {description: _schemaDescription, ...canonicalShape} = canonical
  assert.deepEqual(captured, canonicalShape)
  assert.deepEqual(canonical.required, ['findings', 'checked'])
  assert.deepEqual(canonical.properties.checked.required, ['toolsUsed', 'stoppedAt', 'basis'])
  assert.equal(canonical.properties.findings.items.properties.file.pattern, '^.+:[1-9][0-9]*$')
  const gap = canonical.properties.gaps.items
  assert.deepEqual([gap.type, gap.additionalProperties, gap.required], ['object', false, ['item', 'reason']])
  assert.deepEqual(gap.properties.reason.enum, ['outside-lens', 'capability', 'missing'])
  console.log('ok   G3/G15 review schema parity between the canonical schema and the engine\'s, with its required keys, file pattern and gap shape')
})().catch(error => {
  console.error('FAIL G3/G15 review schema parity:', error.message)
  process.exit(1)
})
