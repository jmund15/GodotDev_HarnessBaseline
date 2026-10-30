#!/usr/bin/env node
// Proof for workflows/worklog_relevance.js: a delegate that cannot read its mandate, the titles index or the
// Obsidian worklog has one truthful outcome, `checked.stoppedAt: 'blocked'` with the cause in `basis`, in both
// schema copies, the pointer prompt and the mandate; the workflow returns that status, never a clean sweep.
// Run: node .claude/tests/worklog_relevance_blocked_test.js
const fs = require('fs')
const path = require('path')
const assert = require('node:assert/strict')

const engineRun = require('./_engine_run')

const WF = path.join(__dirname, '..', 'workflows')
const fileSchema = JSON.parse(fs.readFileSync(path.join(WF, 'worklog_relevance.schema.json'), 'utf8'))
const mandate = fs.readFileSync(path.join(WF, 'worklog_relevance.prompt.md'), 'utf8')
const BLOCKED = { overlaps: [], checked: { indexRead: false, obsidianOpened: false, shortlisted: [],
  stoppedAt: 'blocked', basis: 'the mandate path was denied, so no check ran' } }

let failed = 0
const check = (ok, label) => { console.log((ok ? 'ok   ' : 'FAIL ') + label); if (!ok) failed++ }

;(async () => {
  let prompt = null, schema = null
  const logs = []
  const agent = async (p, opts) => { prompt = p; schema = opts.schema; return BLOCKED }
  const out = await engineRun.load(path.join(WF, 'worklog_relevance.js'))(engineRun.withHost(
    { scope: 'fixture scope', model: 'fixture-model', effort: 'low', tier: 'fanout',
      checker: { model: 'fixture-model', effort: 'low' } }), agent,
    thunks => Promise.all(thunks.map(fn => fn())), () => {}, message => logs.push(message))
  const enumOf = s => s.properties.checked.properties.stoppedAt.enum
  check(enumOf(schema).includes('blocked'), 'the workflow schema admits stoppedAt blocked')
  check(enumOf(fileSchema).includes('blocked'), 'the sidecar schema file admits stoppedAt blocked')
  check(Object.keys(BLOCKED.checked).every(k => k in schema.properties.checked.properties),
    'a blocked result uses only fields the strict schema allows')
  check(/missing path or a denial[^\n]*stoppedAt[^\n]*blocked/.test(prompt) && !prompt.includes('couldNotSatisfy'),
    'the pointer prompt maps a missing or denied read to stoppedAt blocked, not to a field the schema forbids')
  check(/`blocked`/.test(mandate) && /missing|denied/.test(mandate.split('`blocked`')[1] || ''),
    'the mandate defines blocked for a missing or denied read')
  check(out && out.checked && out.checked.stoppedAt === 'blocked' && out.count === 0,
    'the workflow returns the blocked status with the result')
  check(logs.some(l => l.startsWith('PINS ') && l.includes('"worklog:relevance":"fixture-model/low tier:fanout"')),
    'the PINS line carries the job tier so the archived row records what model served what role')
  console.log('\nworklog_relevance_blocked_test: %d failed', failed)
  process.exit(failed ? 1 : 0)
})().catch(error => { console.error('FAIL worklog relevance blocked:', error.message); process.exit(1) })
