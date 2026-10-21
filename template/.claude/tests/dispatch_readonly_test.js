#!/usr/bin/env node
// Proof that dispatch.js keeps a read-only promise by tool set, not by prompt text alone.
// The base args are the m4 survey dispatch as the provider hook rewrote them (captured from the run
// record, trimmed to job m4-FA): shape survey, no readOnly flag, the brief's Bash need widened into the
// profile, so the job ran as general-purpose with every tool.
// Run: node .claude/tests/dispatch_readonly_test.js
const fs = require('fs')
const path = require('path')
const engineRun = require('./_engine_run')

const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'dispatch_readonly', 'm4_survey_args.json'), 'utf8')).args
const SCRIPT = path.join(__dirname, '..', 'workflows', 'dispatch.js')
const clone = (v) => JSON.parse(JSON.stringify(v))

const argsFor = (edit) => {
  const a = clone(FIXTURE)
  edit(a)
  return a
}
const noBash = (a) => {
  const job = a.jobs[0]
  delete job.tools
  const row = a.__toolProfiles[job.label]
  row.builtins = row.builtins.filter(t => t !== 'Bash')
  row.add = []
  a.__coverage[job.label] = { covered: [], needs: [], unread: [] }
}
const covered = (a) => { a.__coverage[a.jobs[0].label].covered = ['survey'] }
const lane = (a) => {
  a.__toolProfiles[a.jobs[0].label].agentType = 'lane-survey'
  a.__toolProfileDefaults.survey.agentType = 'lane-survey'
}

async function run(args, agentImpl) {
  const calls = [], logs = []
  const agent = async (prompt, opts) => {
    calls.push({ prompt: String(prompt), opts })
    return agentImpl ? agentImpl(calls.length, opts) : 'ok'
  }
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f()))
  const result = await engineRun.load(SCRIPT)(engineRun.withHost(args), agent, parallel, () => {}, (m) => logs.push(String(m)))
  return { calls, logs, result }
}

;(async () => {
  let failed = 0, total = 0
  const check = (name, ok, detail) => {
    total++
    if (!ok) failed++
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok || !detail ? '' : '   [' + detail + ']'))
  }
  const READONLY = 'READ-ONLY: do NOT modify'
  const j = (r) => JSON.stringify(r.result).slice(0, 300)

  let r = await run(argsFor(() => {}))
  check('m4 replay: the job as dispatched runs, as general-purpose with its widened profile',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'general-purpose', j(r))
  check('m4 replay: a survey profile that holds Bash is logged as not read-only',
    r.logs.some(l => /^NOT-READ-ONLY m4-FA/.test(l) && l.includes('Bash')), r.logs.join(' | ').slice(0, 300))
  check('m4 replay: it gets no read-only line it could not keep', !r.calls[0].prompt.includes(READONLY))

  r = await run(argsFor(a => { a.jobs[0].readOnly = true }))
  check('readOnly:true on a profile that holds Bash is refused before any agent starts',
    r.calls.length === 0 && r.result && /m4-FA/.test(String(r.result.error)) && /Bash/.test(String(r.result.error)), j(r))

  r = await run(argsFor(a => { noBash(a); a.jobs[0].readOnly = true }))
  check('readOnly:true with no lane type to run as is refused before any agent starts',
    r.calls.length === 0 && r.result && /m4-FA/.test(String(r.result.error)), j(r))

  r = await run(argsFor(a => { noBash(a); lane(a); covered(a); a.jobs[0].readOnly = true }))
  check('readOnly:true on a covered survey job runs as the lane type, which holds no write tool',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-survey' && r.calls[0].prompt.includes(READONLY), j(r))

  r = await run(argsFor(a => { noBash(a); lane(a); covered(a); a.jobs[0].readOnly = true }),
    async (n, opts) => { if (opts.agentType === 'lane-survey') throw new Error("agent type 'lane-survey' not found"); return 'ok' })
  check('a read-only job whose lane type is not loaded is never retried as general-purpose',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-survey', r.calls.map(c => c.opts.agentType).join(','))
  check('that job comes back as a no-return the caller can see',
    r.result && r.result.noReturn && r.result.noReturn.labels.includes('m4-FA'), j(r))

  r = await run(argsFor(a => { noBash(a) }))
  check('a survey job with no write tool in its profile is read-only by its shape: the prompt says so',
    r.calls.length === 1 && r.calls[0].prompt.includes(READONLY), j(r))
  check('and an unenforced shape-derived claim is logged',
    r.logs.some(l => /^READ-ONLY-UNENFORCED m4-FA/.test(l)), r.logs.join(' | ').slice(0, 300))

  r = await run(argsFor(a => { noBash(a); a.jobs[0].readOnly = false }))
  check('readOnly:false opts a shape-derived claim out', r.calls.length === 1 && !r.calls[0].prompt.includes(READONLY), j(r))

  r = await run(argsFor(a => { a.jobs[0].shape = 'author'; a.__toolProfiles['m4-FA'].shape = 'author' }))
  check('an author job is not read-only', r.calls.length === 1 && !r.calls[0].prompt.includes(READONLY), j(r))

  console.log('\n' + (total - failed) + ' pass, ' + failed + ' fail')
  process.exit(failed ? 1 : 0)
})()
