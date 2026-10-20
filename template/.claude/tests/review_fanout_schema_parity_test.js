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
  captured = opts.schema
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
    agents: [{key: 'lens', prompt: 'Review.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose',
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
  const protocol = fs.readFileSync(path.join(__dirname, '..', 'commands', 'agents', 'orchestrator_action_protocol.md'), 'utf8')
  const rails = fs.readFileSync(path.join(__dirname, '..', 'guards', 'review.md'), 'utf8')
  for (const [name, text] of [
    ['protocol', protocol],
    ['detailed rail', rails.split('\n## detailed\n')[1].split('\n## condensed\n')[0]],
    ['condensed rail', rails.split('\n## condensed\n')[1].split('\n## minimal\n')[0]],
    ['canonical new', canonical.properties.findings.items.properties.new.description],
    ['engine new', captured.properties.findings.items.properties.new.description],
  ]) {
    assert.match(text, /literal replacement text/, name)
    assert.match(text, /PLAN.*scope.*options/, name)
  }
  console.log('ok   G3/G15 review schema parity and literal FIX replacement contract across protocol, rails and both schemas')
})().catch(error => {
  console.error('FAIL G3/G15 review schema parity:', error.message)
  process.exit(1)
})
