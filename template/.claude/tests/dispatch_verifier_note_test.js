#!/usr/bin/env node
// Proof that a failed closeCheck verifier names its error: a verifier agent that throws comes back as
// verification.status 'unavailable' carrying the thrown message in verification.note.
// Run: node .claude/tests/dispatch_verifier_note_test.js
const assert = require('node:assert/strict')
const fs = require('fs')
const path = require('path')
const engineRun = require('./_engine_run')

const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'dispatch_readonly', 'm4_survey_args.json'), 'utf8')).args
const SCRIPT = path.join(__dirname, '..', 'workflows', 'dispatch.js')
const CLAIM = { id: 'c1', subject: 'fixture', claim: 'fixture claim', polarity: 'exists', bearing: 'context', evidence: [] }

const args = JSON.parse(JSON.stringify(FIXTURE))
args.checker = { model: 'claude-haiku-5-5', effort: 'medium' }
args.__toolProfiles.verify = { agentType: 'general-purpose', tier: 'fanout' }
const host = { name: 'host', ids: ['claude-haiku-5-5', 'claude-sonnet-5-5[1m]'] }

;(async () => {
  const agent = async (prompt, opts) => {
    if (opts.phase === 'Verify') throw new Error('fixture verifier failure')
    return 'done\n```claims\n' + JSON.stringify(CLAIM) + '\n```\n'
  }
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f()))
  const result = await engineRun.load(SCRIPT)(engineRun.withHost(args, host), agent, parallel, () => {}, () => {})
  assert.ok(result && result.verification, 'verification present: ' + JSON.stringify(result).slice(0, 300))
  assert.equal(result.verification.status, 'unavailable')
  assert.equal(result.verification.note, 'fixture verifier failure')
  console.log('ok   a failed verifier returns status unavailable with its error in note')
})().catch(error => { console.error('FAIL dispatch verifier note:', error); process.exit(1) })
