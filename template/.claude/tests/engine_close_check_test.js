#!/usr/bin/env node
// closeCheck checks no claim: /review runs tools/quote_check.py over a findings file (T4). The block returns every
// item UNCHECKED, starts no agent, and still refuses a duplicate or missing source key. The block is read from
// scripts/engine_src/common.js, the one source every engine copies (schema_parity.js keeps the copies equal).
const assert = require('assert/strict')
const fs = require('fs')
const path = require('path')

const common = fs.readFileSync(path.join(__dirname, '..', 'scripts', 'engine_src', 'common.js'), 'utf8')
const block = (name) => {
  const begin = '// ENGINE-BLOCK-BEGIN ' + name + '\n'
  const from = common.indexOf(begin)
  const to = common.indexOf('// ENGINE-BLOCK-END ' + name)
  assert.ok(from >= 0 && to > from, 'common.js has no block ' + name)
  return common.slice(from + begin.length, to)
}
let agents = 0
const agent = async () => { agents++; throw new Error('closeCheck must start no agent') }
const closeCheck = new Function('agent', 'log', ['VERDICTS', 'applyVerdicts', 'closeCheck'].map(block).join('\n') + '\nreturn closeCheck')(agent, () => {})

const keyOf = c => c.lens + '#' + c.index
const items = [{ lens: 'a', index: 1, text: 'x' }, { lens: 'b', index: 2, text: 'y' }]

;(async () => {
  const { items: out, verification } = await closeCheck('claims', { items, keyOf })
  assert.deepEqual(out.map(c => c.verdict), ['UNCHECKED', 'UNCHECKED'])
  assert.deepEqual(out.map(c => c.confidence), ['unverified', 'unverified'])
  assert.equal(verification.status, 'unavailable')
  assert.deepEqual(verification.counts, { VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: 2 })
  assert.equal(agents, 0)
  console.log('PASS closeCheck returns every item UNCHECKED and starts no agent')

  await assert.rejects(closeCheck('claims', { items: [items[0], items[0]], keyOf }), /duplicate or missing source key/)
  await assert.rejects(closeCheck('claims', { items: [{ lens: 'a' }], keyOf }), /duplicate or missing source key/)
  console.log('PASS closeCheck refuses a duplicate or missing source key')
})().catch(e => { console.error(e); process.exit(1) })
