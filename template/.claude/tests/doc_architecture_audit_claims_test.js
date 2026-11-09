#!/usr/bin/env node
// doc_architecture_audit.js: each finding about a file's content carries `file` and a verbatim `evidence` quote,
// and the result names the claims post-check that verifies them.
const fs = require('fs')
const path = require('path')

const engineRun = require('./_engine_run')

const AUDIT = path.join(__dirname, '..', 'workflows', 'doc_architecture_audit.js')
const engine = engineRun.load(AUDIT)
const HOST_TRANSPORT = { name: 'host', ids: ['m-one', 'm-two'], efforts: ['low', 'medium', 'high'] }

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

async function run(args, reply) {
  const calls = []
  const agent = async (prompt, opts) => { calls.push({ prompt, opts }); return reply(prompt, opts) }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const result = await engine(engineRun.withHost({ context: 'CONTEXT', docRoot: 'D:/vault', model: 'm-one', effort: 'low', tier: 'fanout', kind: 'reviewer', checker: { model: 'm-one', effort: 'low' }, ...args }, HOST_TRANSPORT),
    agent, parallel, () => {}, () => {})
  return { result, calls }
}

const finding = (over = {}) => ({
  agent: 'da-structural', check: 'S2', severity: 'warning', action: 'FIX', system: 'Combat', description: 'missing callout',
  recommendation: 'add it', handler: 'inline:search_replace', options: null, scope: ['Combat/Quick Reference.md'],
  file: 'D:/vault/Combat/Quick Reference.md:12', evidence: '> [!info] System Overview', ...over,
})

;(async () => {
  const r = await run({}, (_prompt, opts) => ({ findings: opts.label === 'da-structural' ? [finding(), finding({ system: 'Spells', file: null, evidence: null })] : [] }))
  check('a missing verifier profile returns unavailable, never a hand-run post-check', r.result.verification.status === 'unavailable'
    && r.result.findings.every(f => f.verdict === 'UNCHECKED' && f.confidence === 'unverified') && !('postCheck' in r.result),
    JSON.stringify(r.result.verification))
  check('findings keep their file and evidence through consolidation',
    r.result.findings.some(f => f.file === 'D:/vault/Combat/Quick Reference.md:12' && f.evidence === '> [!info] System Overview'),
    JSON.stringify(r.result.findings))
  const prompt = r.calls[0].prompt
  check('the lens prompt asks for file and a verbatim evidence quote for a finding about a file\'s content',
    /"file"/.test(prompt) && /"evidence"/.test(prompt) && /VERBATIM/.test(prompt), prompt.slice(0, 600))
  check('absence instructions never ask for null canonical evidence', !prompt.includes('sets both to null'), prompt.slice(-1200))
  const props = r.calls[0].opts.schema.properties.findings.items.properties
  check('the findings schema uses canonical evidence items and optional file',
    props.file && props.evidence && props.evidence.type === 'array'
      && !r.calls[0].opts.schema.properties.findings.items.required.includes('file')
      && props.evidence.items.oneOf.length === 4,
    JSON.stringify(props))
  console.log('doc_architecture_audit claims: ' + passed + ' passed, ' + failed + ' failed')
  process.exit(failed ? 1 : 0)
})().catch(error => { console.error(error); process.exit(1) })
