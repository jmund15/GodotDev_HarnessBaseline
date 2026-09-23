// Proof that every fan-out engine reads a delegate's guard tier from the injected registry map
// (`args.__rails`, written by hooks/workflow_provider_guard.py) and falls back to `detailed` without it.
// Four engines used to carry hand-copied TIER_OF tables that disagreed on fable (`fable` vs `none`).
// Run: node .claude/tests/engine_rails_test.js
const fs = require('fs')
const path = require('path')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

const RAILS = { fable: 'minimal', 'claude-fable-5-1': 'minimal', opus: 'condensed', sonnet: 'detailed' }
const JOB = { label: 'j', promptPath: 'p.md', model: 'fable', effort: 'medium', agentType: 'Explore' }
const ENGINES = {
  'dispatch.js': (extra) => ({ jobs: [{ ...JOB }], ...extra }),
  'dispatch_chains.js': (extra) => ({ chains: [{ name: 'c', jobs: [{ ...JOB }] }], ...extra }),
  'explore_fanout.js': (extra) => ({ lenses: [{ key: 'k', promptPath: 'p.md', model: 'fable', agentType: 'Explore' }], ...extra }),
  'review_fanout.js': (extra) => ({ agents: [{ key: 'k', promptPath: 'p.md', model: 'fable', effort: 'medium', agentType: 'Explore' }], ...extra }),
}

async function prompts(file, args, logs) {
  const src = fs.readFileSync(path.join(__dirname, '../workflows', file), 'utf8')
    .replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')
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
  const result = await new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', src)(
    args, agent, parallel, pipeline, () => {}, (m) => { if (logs) logs.push(m) })
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
  console.log('\n' + (total - failed) + '/' + total + ' passed')
  process.exit(failed ? 1 : 0)
})()
