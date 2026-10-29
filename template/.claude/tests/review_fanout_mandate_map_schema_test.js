#!/usr/bin/env node
// A seat that carries catalog mandates gets a schema requiring `checked.mandates` with exactly those keys, each
// one of findings, clean or not-reached: the engine scores only that map, and a seat whose brief named the map
// still returned none twice, so every mandate it judged clean scored not-reached. A seat with no mandate keys
// keeps the canonical schema (review_fanout_schema_parity_test.js).
const path = require('path')
const assert = require('node:assert/strict')

const engineRun = require('./_engine_run')

const HOST_TRANSPORT = { name: 'host', ids: ['fixture-model'] }
const schemas = {}
const agent = async (_prompt, opts) => {
  if (opts.phase === 'Review') schemas[opts.label.replace(/^review:/, '')] = opts.schema
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
    checker: {model: 'fixture-model', effort: 'medium'},
    __lensMandates: {'lens-a+lens-b': ['m.A', 'm.B']},
    agents: [
      {key: 'lens-a+lens-b', label: 'merged', prompt: 'Review.', model: 'fixture-model', effort: 'medium',
        agentType: 'general-purpose', mustRead: [{path: 'fixture', lines: 1}]},
      {key: 'plain', label: 'plain', prompt: 'Review.', model: 'fixture-model', effort: 'medium',
        agentType: 'general-purpose', mustRead: [{path: 'fixture', lines: 1}]},
    ],
  }, HOST_TRANSPORT), agent, parallel, () => {}, () => {})
  const merged = schemas['lens-a+lens-b'].properties.checked
  assert.deepEqual(merged.required, ['toolsUsed', 'stoppedAt', 'basis', 'mandates'])
  assert.deepEqual(merged.properties.mandates.required, ['m.A', 'm.B'])
  assert.equal(merged.properties.mandates.additionalProperties, false)
  for (const key of ['m.A', 'm.B']) {
    assert.deepEqual(merged.properties.mandates.properties[key].enum, ['findings', 'clean', 'not-reached'])
  }
  assert.deepEqual(schemas.plain.properties.checked.required, ['toolsUsed', 'stoppedAt', 'basis'])
  console.log('ok   a seat with mandate keys must return checked.mandates over exactly those keys; a seat without keeps the canonical schema')
})().catch(error => {
  console.error('FAIL mandate map schema:', error.message)
  process.exit(1)
})
