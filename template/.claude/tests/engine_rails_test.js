// Proof that every fan-out engine reads a delegate's guard tier from the injected registry map
// (`args.__rails`, written by hooks/workflow_provider_guard.py) and falls back to `detailed` without it.
// Four engines used to carry hand-copied TIER_OF tables that disagreed on fable (`fable` vs `none`).
// Run: node .claude/tests/engine_rails_test.js
const fs = require('fs')
const path = require('path')
const engineRun = require('./_engine_run')
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] }
// The test pins the self-started verifier and merge steps through args.checker; the engine never picks them.
const HOST_CHECKER = { model: 'sonnet', effort: 'medium' }
const withHost = (args) => Object.prototype.hasOwnProperty.call(args || {}, '__transport') ? args : { ...args, checker: (args && args.checker) || HOST_CHECKER, __transport: HOST_TRANSPORT }

const RAILS = { fable: 'minimal', 'claude-fable-5-1': 'minimal', opus: 'condensed', sonnet: 'detailed' }
const JOB = { label: 'j', promptPath: 'p.md', model: 'fable', effort: 'medium', agentType: 'Explore' }
const ENGINES = {
  'dispatch.js': (extra) => ({ jobs: [{ ...JOB }], ...extra }),
  'dispatch_chains.js': (extra) => ({ chains: [{ name: 'c', jobs: [{ ...JOB }] }], ...extra }),
  'explore_fanout.js': (extra) => ({ lenses: [{ key: 'k', promptPath: 'p.md', model: 'fable', effort: 'medium', agentType: 'Explore' }], ...extra }),
  'review_fanout.js': (extra) => ({ agents: [{ key: 'k', promptPath: 'p.md', model: 'fable', effort: 'medium', agentType: 'Explore', mustRead: [{ path: 'x', lines: 1 }] }], ...extra }),
}

async function prompts(file, args, logs) {
  const script = path.join(__dirname, '../workflows', file)
  const seen = []
  const agent = async (prompt) => {
    seen.push(String(prompt))
    return { findings: [], claims: [], gaps: [], checked: { toolsUsed: ['Read:x'], stoppedAt: 'done', basis: 'fixture' } }
  }
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f()))
  const pipeline = async (items, ...stages) => Promise.all(items.map(async (it) => {
    let v = it
    for (const s of stages) v = await s(v)
    return v
  }))
  const result = await engineRun.load(script, src => 'const pipeline = ' + pipeline.toString() + '\n' + src)(
    withHost(args), agent, parallel, () => {}, (m) => { if (logs) logs.push(m) })
  return { seen, result }
}

;(async () => {
  let failed = 0
  let total = 0
  const check = (name, ok, detail) => {
    total++
    if (!ok) failed++
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok || !detail ? '' : '   [' + detail + ']'))
  }
  for (const [file, build] of Object.entries(ENGINES)) {
    const withRails = await prompts(file, build({ __rails: RAILS }))
    const first = withRails.seen[0] || ''
    check(file + ': a fable job with __rails reads the `## minimal` guard section',
      first.includes('`## minimal`'), withRails.result && withRails.result.error || first.slice(-300))
    const bare = await prompts(file, build({}))
    const firstBare = bare.seen[0] || ''
    check(file + ': without __rails a job reads `## detailed`',
      firstBare.includes('`## detailed`'), bare.result && bare.result.error || firstBare.slice(-300))
    const src = fs.readFileSync(path.join(__dirname, '../workflows', file), 'utf8')
    check(file + ': no hand-copied TIER_OF table remains', !/\bTIER_OF\b/.test(src))
  }
  // Inline delivery: the hook supplies `__railsText` for the pairs a call uses; each engine pastes it
  // after the brief under a standing-rules header, and keeps the pointer only as the fallback.
  const INLINE_KEY = { 'dispatch.js': 'any/minimal', 'dispatch_chains.js': 'any/minimal',
    'explore_fanout.js': 'survey/minimal', 'review_fanout.js': 'review/minimal' }
  for (const [file, build] of Object.entries(ENGINES)) {
    const logs = []
    const inline = await prompts(file, build({ __rails: RAILS, __railsText: { [INLINE_KEY[file]]: 'RAILS-BODY-XYZ' } }), logs)
    const p = inline.seen[0] || ''
    check(file + ': inline text arrives after the brief under the standing-rules header, no pointer',
      p.includes('standing rules for how you work') && p.includes('RAILS-BODY-XYZ') && !p.includes('Read .claude/guards')
        && p.indexOf('RAILS-BODY-XYZ') > p.indexOf('p.md'), inline.result && inline.result.error || p.slice(-300))
    const emptyLogs = []
    const empty = await prompts(file, build({ __rails: RAILS, __railsText: { [INLINE_KEY[file]]: '' } }), emptyLogs)
    check(file + ': an empty entry falls back to the pointer', (empty.seen[0] || '').includes('Read .claude/guards'))
    const missLogs = []
    await prompts(file, build({ __rails: RAILS, __railsText: { 'other/detailed': 'x' } }), missLogs)
    check(file + ': a present map missing the job key logs RAILS-FALLBACK', missLogs.some(l => String(l).includes('RAILS-FALLBACK')),
      missLogs.join(' | ').slice(0, 300))
  }
  // Driver notes: `__driverNotes[<pinned model>]` is pasted after the rails for a job on that model,
  // on the inline path and on the pointer fallback, and never for a model it does not name or railTier none.
  for (const [file, build] of Object.entries(ENGINES)) {
    const NOTES = { fable: '[session] Driver notes (fable):\n- NOTE-XYZ (ref)' }
    const withNotes = await prompts(file, build({ __rails: RAILS, __driverNotes: NOTES, __railsText: { [INLINE_KEY[file]]: 'RAILS-BODY-XYZ' } }))
    const wp = withNotes.seen[0] || ''
    check(file + ': driver notes follow the inline rails', wp.includes('NOTE-XYZ') && wp.indexOf('NOTE-XYZ') > wp.indexOf('RAILS-BODY-XYZ'),
      withNotes.result && withNotes.result.error || wp.slice(-300))
    const ptr = await prompts(file, build({ __rails: RAILS, __driverNotes: NOTES }))
    check(file + ': driver notes follow the pointer fallback too', (ptr.seen[0] || '').includes('NOTE-XYZ'), (ptr.seen[0] || '').slice(-300))
    const other = await prompts(file, build({ __rails: RAILS, __driverNotes: { sonnet: 'NOTE-XYZ' } }))
    check(file + ': notes for another model do not reach this job', !(other.seen[0] || '').includes('NOTE-XYZ'))
  }
  const noneNotes = await prompts('dispatch.js', { jobs: [{ ...JOB, railTier: 'none' }], __rails: RAILS, __driverNotes: { fable: 'NOTE-XYZ' } })
  check('dispatch.js: railTier none sends no driver notes either', !(noneNotes.seen[0] || '').includes('NOTE-XYZ'))
  const chainsBare = await prompts('dispatch_chains.js', ENGINES['dispatch_chains.js']({}))
  check('dispatch_chains.js: the fallback pointer names any.md', (chainsBare.seen[0] || '').includes('guards/any.md'),
    (chainsBare.seen[0] || '').slice(-300))
  // A per-job railTier override wins over the registry map: the rail-tier battery varies the tier
  // with the model held fixed, through the production prompt.
  const withOverride = await prompts('dispatch.js', { jobs: [{ ...JOB, railTier: 'condensed' }], __rails: RAILS })
  check('dispatch.js: a job railTier overrides __rails',
    (withOverride.seen[0] || '').includes('`## condensed`') && !(withOverride.seen[0] || '').includes('`## minimal`'),
    withOverride.result && withOverride.result.error || (withOverride.seen[0] || '').slice(-300))
  const noRails = await prompts('dispatch.js', { jobs: [{ ...JOB, railTier: 'none' }], __rails: RAILS })
  check('dispatch.js: railTier none sends no DELEGATE RAILS block',
    noRails.seen.length === 1 && !noRails.seen[0].includes('DELEGATE RAILS'),
    noRails.result && noRails.result.error || (noRails.seen[0] || '').slice(-300))
  const badTier = await prompts('dispatch.js', { jobs: [{ ...JOB, railTier: 'strict' }], __rails: RAILS })
  check('dispatch.js: an unknown railTier is rejected before dispatch',
    badTier.seen.length === 0 && !!(badTier.result && badTier.result.error), JSON.stringify(badTier.result).slice(0, 200))
  // The choices block is for a writing job; a read-only job (Explore, no write tool) does not get it.
  const chainJob = (agentType) => ({ chains: [{ name: 'c', jobs: [{ ...JOB, agentType }] }] })
  const writing = await prompts('dispatch_chains.js', chainJob('general-purpose'))
  check('dispatch_chains.js: a writing job gets the CHOICES block', (writing.seen[0] || '').includes('=== CHOICES'),
    writing.result && writing.result.error || (writing.seen[0] || '').slice(-300))
  const reading = await prompts('dispatch_chains.js', chainJob('Explore'))
  check('dispatch_chains.js: a read-only job gets no CHOICES block', !(reading.seen[0] || '').includes('=== CHOICES'),
    reading.result && reading.result.error || (reading.seen[0] || '').slice(-300))
  console.log('\n' + (total - failed) + '/' + total + ' passed')
  process.exit(failed ? 1 : 0)
})()
