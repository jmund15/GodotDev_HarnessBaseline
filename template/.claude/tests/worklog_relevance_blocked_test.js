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
const BLOCKED = { overlaps: [], checked: { indexRead: false, obsidianOpened: false, shortlisted: [],
  stoppedAt: 'blocked', basis: 'the mandate path was denied, so no check ran' } }

let failed = 0
const check = (ok, label) => { console.log((ok ? 'ok   ' : 'FAIL ') + label); if (!ok) failed++ }

;(async () => {
  let prompt = null, schema = null
  const logs = []
  const agent = async (p, opts) => { if (opts.phase === 'Relevance') { prompt = p; schema = opts.schema }; return BLOCKED }
  const out = await engineRun.load(path.join(WF, 'worklog_relevance.js'))(engineRun.withHost(
    { scope: 'fixture scope', kind: 'lens', model: 'fixture-model', effort: 'low', tier: 'fanout',
      checker: { model: 'fixture-model', effort: 'low' } }), agent,
    thunks => Promise.all(thunks.map(fn => fn())), () => {}, message => logs.push(message))
  const enumOf = s => s.properties.checked.properties.stoppedAt.enum
  check(enumOf(schema).includes('blocked'), 'the workflow schema admits stoppedAt blocked')
  check(enumOf(fileSchema).includes('blocked'), 'the sidecar schema file admits stoppedAt blocked')
  check(Object.keys(BLOCKED.checked).every(k => k in schema.properties.checked.properties),
    'a blocked result uses only fields the strict schema allows')
  check(prompt.includes('blocked') && !prompt.includes('couldNotSatisfy'),
    'the pointer prompt names the blocked status and not a field the schema forbids')
  check(out && out.checked && out.checked.stoppedAt === 'blocked' && out.count === 0,
    'the workflow returns the blocked status with the result')
  check(logs.some(l => l.startsWith('PINS ') && l.includes('"worklog:relevance":"fixture-model/low tier:fanout"')),
    'the PINS line carries the job tier so the archived row records what model served what role')
  // A result that contradicts itself by its own fields is the agent's failed output step, not a clean sweep:
  // the engine reruns once and then returns an error naming the contradiction. The RED fixture is the result
  // the engine accepted in a real run (a placeholder issued beside a malformed real answer).
  const placeholder = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'worklog_relevance', 'captured_placeholder.json'), 'utf8'))
  const engine = load => load(path.join(WF, 'worklog_relevance.js'))
  const runWith = async replies => {
    const calls = []
    const agent = async (p, opts) => {
      if (opts.phase !== 'Relevance') return {}
      calls.push(p)
      return replies[Math.min(calls.length, replies.length) - 1]
    }
    const out = await engine(engineRun.load)(engineRun.withHost(
      { scope: 'fixture scope', kind: 'lens', model: 'fixture-model', effort: 'low', tier: 'fanout',
        checker: { model: 'fixture-model', effort: 'low' } }), agent,
      thunks => Promise.all(thunks.map(fn => fn())), () => {}, () => {})
    return { out, calls }
  }
  const verdict = (checked, overlaps = []) => ({ overlaps, checked: { indexRead: true, obsidianOpened: false, shortlisted: [],
    basis: 'fixture basis', evidence: [], ...checked } })
  const overlap = { title: 'T', domain: 'D', relation: 'same-scope', why: 'shared surface', recommendation: 'fold-in', evidence: [] }
  const coherent = {
    'triviality-gate': verdict({ stoppedAt: 'triviality-gate', indexRead: false }),
    'empty-shortlist': verdict({ stoppedAt: 'empty-shortlist' }),
    'verified-none': verdict({ stoppedAt: 'verified-none', obsidianOpened: true, shortlisted: ['A'] }),
    'verified-overlaps': verdict({ stoppedAt: 'verified-overlaps', obsidianOpened: true, shortlisted: ['T'] }, [overlap]),
    blocked: BLOCKED,
  }

  const twice = await runWith([placeholder])
  check(twice.calls.length === 2, 'the captured placeholder is rerun once, not accepted and not rerun again')
  check(twice.out && typeof twice.out.error === 'string' && !('count' in twice.out),
    'two contradicting results return an error, never a clean count of 0')
  check(twice.out && /verified-overlaps/.test(twice.out.error) && /overlaps/.test(twice.out.error),
    'the error names the contradiction it found')
  check(twice.calls.length > 1 && twice.calls[1].includes('verified-overlaps') && twice.calls[1].startsWith(twice.calls[0].split('\n')[0]),
    'the rerun is the same brief plus the contradiction, so the agent can correct it')
  const healed = await runWith([placeholder, coherent['verified-overlaps']])
  check(healed.calls.length === 2 && healed.out && healed.out.count === 1 && healed.out.checked.stoppedAt === 'verified-overlaps',
    'a coherent rerun is the result')
  for (const [name, reply] of Object.entries(coherent)) {
    const one = await runWith([reply])
    check(one.calls.length === 1 && one.out && !one.out.error && one.out.checked.stoppedAt === name,
      'a coherent ' + name + ' result is accepted on the first call')
  }
  const wrong = {
    'overlaps with verified-none': verdict({ stoppedAt: 'verified-none', obsidianOpened: true, shortlisted: ['A'] }, [overlap]),
    'verified-none without opening the worklog': verdict({ stoppedAt: 'verified-none', shortlisted: ['A'] }),
    'verified-none with an empty shortlist': verdict({ stoppedAt: 'verified-none', obsidianOpened: true }),
    'empty-shortlist with a shortlist': verdict({ stoppedAt: 'empty-shortlist', shortlisted: ['A'] }),
    'triviality-gate that opened the worklog': verdict({ stoppedAt: 'triviality-gate', obsidianOpened: true }),
    'blocked with overlaps': verdict({ stoppedAt: 'blocked' }, [overlap]),
    'no checked record': { overlaps: [] },
  }
  for (const [name, reply] of Object.entries(wrong)) {
    const one = await runWith([reply])
    check(one.calls.length === 2 && one.out && typeof one.out.error === 'string', 'a result with ' + name + ' is rejected')
  }

  console.log('\nworklog_relevance_blocked_test: %d failed', failed)
  process.exit(failed ? 1 : 0)
})().catch(error => { console.error('FAIL worklog relevance blocked:', error.message); process.exit(1) })
