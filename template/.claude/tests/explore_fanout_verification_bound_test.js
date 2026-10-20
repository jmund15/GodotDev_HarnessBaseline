#!/usr/bin/env node
const assert = require('node:assert/strict')
const path = require('path')
const engineRun = require('./_engine_run')
const engine = engineRun.load(path.join(__dirname, '..', 'workflows', 'explore_fanout.js'))
;(async () => {
  const claims = Array.from({length: 1500}, (_, i) => ({ subject: 'subject-' + i, claim: 'claim-' + i,
    polarity: 'exists', bearing: 'context', evidence: [] }))
  const out = await engine(engineRun.withHost({ lenses: [{key: 'seat', prompt: 'Explore fixture.', model: 'fixture-model', agentType: 'general-purpose'}], resultMode: 'bounded', checker: {model: 'fixture-model'} }),
    async () => ({claims, gaps: [], checked: {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture'}}),
    thunks => Promise.all(thunks.map(fn => fn())), () => {}, () => {})
  assert.ok(Buffer.byteLength(JSON.stringify(out), 'utf8') <= 8192, 'bounded bytes=' + Buffer.byteLength(JSON.stringify(out), 'utf8'))
  assert.equal(out.verification.status, 'unavailable')
  assert.equal(out.verification.counts.UNCHECKED, claims.length)
  assert.equal(typeof out.verification.report, 'string')
  assert.equal(out.delivery.processedState.nonVerifiedInline, false)
  assert.deepEqual(out.verification.nonVerified, claims.slice(0, out.verification.nonVerified.length).map((_, i) => 'seat#' + i))
  assert.equal(out.delivery.preview.nonVerified.total, claims.length)
  assert.equal(out.delivery.preview.nonVerified.shown, out.verification.nonVerified.length)
  assert.equal(out.delivery.preview.nonVerified.omitted, claims.length - out.verification.nonVerified.length)
  console.log('PASS explore bounded verification cap and ordered preview')
})().catch(error => { console.error('FAIL explore bounded verification:', error); process.exit(1) })
