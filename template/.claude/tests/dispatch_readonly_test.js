#!/usr/bin/env node
// Proof that dispatch.js keeps a read-only promise by the job's kind, never by prompt text or by a brief's wording.
// The base args are the m4 survey dispatch (captured from the run record, trimmed to job m4-FA) as the provider
// hook rewrites it now: the job names `kind: lens`, and the hook resolves that row into args.__toolProfiles.
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
const withKind = (kind) => (a) => {
  a.jobs[0].kind = kind
  a.__toolProfiles['m4-FA'] = a.__kindRows[kind]
}
const withBash = (a) => { a.__toolProfiles['m4-FA'] = { ...a.__toolProfiles['m4-FA'], builtins: [...a.__toolProfiles['m4-FA'].builtins, 'Bash'] } }

async function run(args, agentImpl) {
  const calls = [], logs = []
  const agent = async (prompt, opts) => {
    calls.push({ prompt: String(prompt), opts })
    return agentImpl ? agentImpl(calls.length, opts) : 'ok'
  }
  // The runtime resolves a thrown job to null rather than rejecting the batch.
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f().catch(() => null)))
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
  check('m4 replay: a lens job runs as lane-survey, the type its kind row sets',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-survey', j(r))
  check('m4 replay: a lens holds no write tool and no shell, so its prompt carries no read-only line to keep',
    !r.calls[0].prompt.includes(READONLY))
  check('m4 replay: the recorded profile is agent-type, kind lens',
    r.logs.some(l => /^TOOL-PROFILES /.test(l) && /"enforcement":"agent-type"/.test(l) && /"kind":"lens"/.test(l)), r.logs.join(' | ').slice(0, 300))

  r = await run(argsFor(a => { delete a.jobs[0].kind }))
  check('a kindless job is refused before any agent starts',
    r.calls.length === 0 && (r.result.badJobs || []).includes('m4-FA') && /kind/.test(String(r.result.error)), j(r))

  for (const key of ['readOnly', 'agentType', 'shape']) {
    r = await run(argsFor(a => { a.jobs[0][key] = key === 'readOnly' ? true : 'general-purpose' }))
    check(key + ' on a job is refused before any agent starts, naming kind',
      r.calls.length === 0 && new RegExp(key).test(String(r.result.error)) && /kind/.test(String(r.result.error)), j(r))
  }

  r = await run(argsFor(a => { withKind('reviewer')(a) }))
  check('a reviewer job runs as lane-review', r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-review', j(r))

  r = await run(argsFor(a => { withKind('reviewer')(a); withBash(a) }))
  check('a no-write kind whose profile holds Bash is told not to write through the shell',
    r.calls.length === 1 && r.calls[0].prompt.includes(READONLY), j(r))

  r = await run(argsFor(a => { withKind('author')(a) }))
  check('an author job runs as lane-author and is not read-only',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-author' && !r.calls[0].prompt.includes(READONLY), j(r))
  check('an author job gets the amend line and the choices contract',
    r.calls[0].prompt.includes('.amend.md') && r.calls[0].prompt.includes('=== CHOICES'), r.calls[0].prompt.slice(-300))

  r = await run(argsFor(() => {}),
    async (n, opts) => { if (opts.agentType === 'lane-survey') throw new Error("agent type 'lane-survey' not found"); return 'ok' })
  check('a job whose agent type is not loaded is never retried as another type',
    r.calls.length === 1 && r.calls[0].opts.agentType === 'lane-survey', r.calls.map(c => c.opts.agentType).join(','))
  check('that job comes back as a no-return the caller can see',
    r.result && r.result.noReturn && r.result.noReturn.labels.includes('m4-FA'), j(r))

  console.log('\n' + (total - failed) + ' pass, ' + failed + ' fail')
  process.exit(failed ? 1 : 0)
})()
