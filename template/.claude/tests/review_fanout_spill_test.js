#!/usr/bin/env node
const fs = require('fs')
const path = require('path')

const engineRun = require('./_engine_run')

const FANOUT = path.join(__dirname, '..', 'workflows', 'review_fanout.js')
const src = fs.readFileSync(FANOUT, 'utf8')
const engine = engineRun.load(FANOUT)
// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'] }
const withHost = args => engineRun.withHost(args, HOST_TRANSPORT)
// The review run starts a self-started merge step, so every run carries args.checker. The host transport pins
// sonnet at medium; an injected transport pins from its own ids and efforts. The engine never picks one.
const HOST_CHECKER = { model: 'sonnet', effort: 'medium' }
const EFFORTED = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] }
const pinnedChecker = (transport) => {
  if (!transport || !Array.isArray(transport.ids) || !transport.ids.length) return undefined
  const checker = { model: transport.ids[0] }
  if (Array.isArray(transport.efforts) && transport.efforts.length) checker.effort = transport.efforts.includes('medium') ? 'medium' : transport.efforts[0]
  return checker
}

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

const checked = {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'fixture input was read'}
const withCoverage = value => {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !Object.prototype.hasOwnProperty.call(value, 'findings')) return value
  return {
    ...value,
    checked: Object.prototype.hasOwnProperty.call(value, 'checked') ? value.checked : checked,
    gaps: Object.prototype.hasOwnProperty.call(value, 'gaps') ? value.gaps : [],
  }
}

async function run(args, returned) {
  const prompts = []
  const calls = []
  const logs = []
  const agent = async (prompt, opts) => {
    prompts.push(prompt)
    calls.push({ prompt, opts })
    const value = (typeof returned === 'function') ? returned(prompt, opts) : returned
    return opts.phase === 'Merge' ? value : withCoverage(value)
  }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const pin = pinnedChecker(args.__transport) || HOST_CHECKER
  // A fold object the test builds without a model takes the checker's pin: the orchestrator pins every job, and the engine never does.
  const fold = args.fold && typeof args.fold === 'object' && args.fold.model === undefined ? { ...args.fold, ...pin } : args.fold
  const result = await engine(withHost({ resultMode: 'full', checker: pin, ...args, ...(fold ? { fold } : {}) }), agent, parallel, () => {}, message => logs.push(message))
  return { result, prompts, calls, logs }
}

async function attempt(args, returned) {
  try { return { run: await run(args, returned), thrown: null } }
  catch (error) { return { run: null, thrown: error } }
}

const finding = (overrides = {}) => ({
  agent: 'lens', action: 'FIX', category: 'bug', critical: false,
  file: 'target.js:1', description: 'finding', old: null, new: null,
  question: null, options: null, scope: null, rationale: 'fixture rationale',
  ...overrides,
})

// Every seat carries its must-read list; `fixture` is the file the default provenance reads whole.
const reviewAgent = (key, overrides = {}) => ({
  key, prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose',
  mustRead: [{ path: 'fixture', lines: 1 }],
  ...overrides,
})

const sourceFindings = {
  'review:strong': finding({
    agent: 'strong', critical: true, action: 'FIX', category: 'bug',
    file: 'shared.js:1', description: 'strong source',
  }),
  'review:weak': finding({
    agent: 'weak', critical: false, action: 'PLAN', category: 'improvement',
    file: 'shared.js:2', description: 'weak source',
  }),
}

async function runMerge(mergedFindings, sources = sourceFindings) {
  return run({
    agents: [reviewAgent('strong'), reviewAgent('weak')],
  }, (_prompt, opts) => opts.phase === 'Merge'
    ? { findings: mergedFindings }
    : { findings: [sources[opts.label]] })
}

function mergedFinding(overrides = {}) {
  return finding({
    agent: 'strong, weak', critical: true, action: 'PLAN', category: 'bug',
    file: 'strong.js:1', description: 'merged source', merged_from: ['F1', 'F2'],
    ...overrides,
  })
}

function checkFallback(label, runResult, expected = ['strong source', 'weak source']) {
  const descriptions = (runResult.result.findings || []).map(f => f.description)
  check(label,
    descriptions.length === expected.length
      && descriptions.every((description, i) => description === expected[i])
      && runResult.result.flags.some(f => f.kind === 'consolidate-invalid'),
    JSON.stringify(runResult.result))
}

// FOLD STAGES. A run without `args.fold` keeps the current review contract plus the one `verification` line every
// result carries: the fingerprint covers the result, every agent() option and every log line in full and bounded modes.
const crypto = require('crypto')
const NO_FOLD_FINGERPRINTS = {
  full: 'a2cadab2ac710700ab6d14e647b46223e3a51b81aa93b26541f5c59ddfee0b34',
  bounded: 'a8a1b62abb91cf955e9c243ffd59fae0c96c852369ad876da540f47524703d24',
}
const fingerprint = (r) => crypto.createHash('sha256').update(JSON.stringify({
  result: r.result, opts: r.calls.map(c => c.opts), logs: r.logs,
})).digest('hex')
const foldFixtureReply = (prompt, opts) => {
  if (opts.phase === 'Merge') return { findings: [{ merged_from: ['F1'] }, { merged_from: ['F2'] }, { merged_from: ['F3'] }] }
  if (opts.label === 'review:one') {
    return { findings: [
      finding({ agent: 'one', file: '.claude/plans/p.md:3', description: 'typo', old: 'teh', new: 'the' }),
      finding({ agent: 'one', action: 'ASK', file: '.claude/plans/p.md:9', description: 'which default?', question: 'A or B?' }),
    ] }
  }
  return { findings: [finding({ agent: 'two', critical: true, file: '.claude/plans/p.md:4', description: 'refutes premise', rationale: 'x.py says "return None"' })] }
}
const noFoldArgs = (mode) => ({
  agents: [reviewAgent('one'), reviewAgent('two')],
  spillDir: '.claude/scratch/fold_fixture', resultMode: mode,
})
const TEMPLATE = '.claude/fixture/fold_template.md'
const SNAPSHOT = '.claude/scratch/fold_fixture/snapshot.json'
const PLAN_P = '.claude/plans/p.md'
const subjectOf = (files, over = {}) => ({ root: '.', snapshot: SNAPSHOT, files, ...over })
const FOLD = { subjects: [subjectOf([PLAN_P])], templatePath: TEMPLATE, tier: 'fanout', model: 'sonnet', effort: 'medium' }
const POST_CHECK_LINE = 'python3 .claude/tools/post_check.py review --workflow-dir "<transcriptDir-from-Workflow-result>" --out .claude/scratch/fold_fixture/post_check.json'
const goodDigest = (over = {}) => Object.assign({
  applied: [{ id: 'F1', change: 'fixed typo in step 3', source: 'finding' }],
  deferred: [{ id: 'F2', class: 'taste-fork', reason: 'taste fork' }, { id: 'F3', class: 'spike', reason: 'measurement needed' }],
  designDelta: [],
  changedPaths: [PLAN_P],
}, over)
const foldReply = ({ digest } = {}) => (prompt, opts) => {
  if (opts.phase === 'Fold') return digest === undefined ? goodDigest() : digest
  return foldFixtureReply(prompt, opts)
}
const flagKinds = (r) => (r.result.flags || []).map(f => f.kind)

// The run's bar reaches the fold prompt and every seat's contract (S4). A caller's rigor never reaches the engine.
const BAR_TEXT = 'BAR: this run\'s rigor is normal. Report a finding only when it counts at that rigor.'
async function barCases() {
  const barred = await run({ ...noFoldArgs('full'), fold: FOLD, __rigor: 'normal', __barText: BAR_TEXT }, foldReply())
  const foldCall = barred.calls.find(call => call.opts.phase === 'Fold')
  check('fold prompt carries Bar when __rigor is set',
    foldCall && foldCall.prompt.includes('Bar: normal'), JSON.stringify(foldCall && foldCall.prompt.slice(0, 400)))
  const seat = barred.calls.find(call => call.opts.label && call.opts.label.startsWith('review:'))
  check('agent prompt carries the bar text',
    seat && seat.prompt.includes(BAR_TEXT), JSON.stringify(seat && seat.prompt.slice(0, 200)))
  const plain = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply())
  const plainFold = plain.calls.find(call => call.opts.phase === 'Fold')
  check('fold prompt omits Bar without __rigor',
    plainFold && !plainFold.prompt.includes('Bar:'), JSON.stringify(plainFold && plainFold.prompt.slice(0, 400)))
  // The engine computes each row's `counts` at the bar, so the fold never judges it from prose.
  const thoroughTypo = (prompt, opts) => {
    const reply = foldReply()(prompt, opts)
    if (opts.label === 'review:one') reply.findings[0].minRigor = 'thorough'
    return reply
  }
  const foldRows = (call) => {
    const block = call && call.prompt.slice(call.prompt.indexOf('CONSOLIDATED FINDINGS ('))
    const match = block && block.match(/```json\n([\s\S]*?)\n```/)
    return match ? JSON.parse(match[1]) : []
  }
  const stamped = foldRows((await run({ ...noFoldArgs('full'), fold: FOLD, __rigor: 'normal', __barText: BAR_TEXT }, thoroughTypo))
    .calls.find(call => call.opts.phase === 'Fold'))
  const byOld = (rows, old) => rows.find(row => row.old === old)
  const ask = stamped.find(row => row.action === 'ASK')
  check('fold rows carry counts computed at the bar',
    byOld(stamped, 'teh') && byOld(stamped, 'teh').counts === false && ask && ask.counts === true,
    JSON.stringify(stamped.map(row => [row.id, row.minRigor, row.counts])))
  check('fold rows carry no counts without __rigor',
    foldRows(plainFold).length > 0 && foldRows(plainFold).every(row => !('counts' in row)),
    JSON.stringify(foldRows(plainFold).map(row => [row.id, row.counts])))
  // The engine's counts mirror tools/finding_bar.py counts on every grade shape.
  const shapes = [{ critical: true, minRigor: 'thorough' }, { action: 'ASK', question: 'q?', minRigor: 'thorough' },
    { action: 'PLAN', options: [], minRigor: 'thorough' }, { action: 'PLAN', options: [' '], minRigor: 'thorough' },
    { action: 'PLAN', options: ['a', 'b'], minRigor: 'thorough' }, { minRigor: 'light' }, { minRigor: 'normal' },
    { minRigor: 'thorough' }, {}, { minRigor: 'bogus' }, { minRigor: 'constructor' }, { minRigor: 'toString' },
    { minRigor: '__proto__' }]
  const matrix = (prompt, opts) => opts.phase === 'Fold' ? goodDigest()
    : { findings: shapes.map((s, i) => finding({ agent: 'one', file: PLAN_P + ':' + (i + 1), description: 'shape ' + i, ...s })) }
  const engineCounts = {}
  for (const rigor of ['light', 'normal', 'thorough']) {
    const rows = foldRows((await run({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD, __rigor: rigor }, matrix))
      .calls.find(call => call.opts.phase === 'Fold'))
    engineCounts[rigor] = rows.map(r => [r, r.counts])
  }
  const py = require('child_process').execFileSync('python3', ['-c',
    'import json, sys; sys.path.insert(0, sys.argv[1]); import finding_bar; d = json.load(sys.stdin); '
    + 'print(json.dumps({k: [finding_bar.counts(r, k) for r, _ in v] for k, v in d.items()}))', path.join(__dirname, '..', 'tools')],
  { input: JSON.stringify(engineCounts), encoding: 'utf8' })
  const expected = JSON.parse(py)
  const mismatched = Object.keys(engineCounts).flatMap(k => engineCounts[k]
    .filter(([, got], i) => got !== expected[k][i]).map(([r, got]) => k + ':' + r.description + ' engine ' + got))
  check('engine counts match finding_bar.counts on every grade shape at every rigor',
    Object.values(engineCounts).every(v => v.length === shapes.length) && mismatched.length === 0,
    JSON.stringify(mismatched.length ? mismatched : Object.values(engineCounts).map(v => v.length)))
}

async function foldCases() {
  const cp = require('child_process')
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'j3-publish-'))
  try {
    const originals = [finding({ id: 'F1', evidence: [{ file: FANOUT, quote: 'export const meta' }] }),
      finding({ id: 'F2', evidence: [{ file: FANOUT, quote: 'absent fixture quote' }] })]
    const input = path.join(tmp, 'findings.json')
    const output = path.join(tmp, 'report.json')
    fs.writeFileSync(input, JSON.stringify({ sources: originals.map(f => ({ id: f.id, finding: f })),
      view: [mergedFinding({ id: 'M1' })] }))
    const done = cp.spawnSync('python3', [path.join(__dirname, '..', 'tools', 'post_check.py'),
      'review', '--findings', input, '--out', output], { encoding: 'utf8' })
    const report = JSON.parse(fs.readFileSync(output, 'utf8'))
    check('grouped publication fixture uses a real original-ID failing report', done.status === 0
      && report.status === 'fail' && report.counts.VERIFIED === 1 && report.counts.UNVERIFIED === 1, JSON.stringify(report))
    const grouped = await run({ agents: [reviewAgent('strong'), reviewAgent('weak')],
      __toolProfiles: { verify: { agentType: 'general-purpose', tier: 'fanout' } } },
      (prompt, opts) => opts.phase === 'Verify' ? { status: report.status, counts: report.counts,
        verdicts: Object.fromEntries(Object.entries(report.verdicts).map(([id, v]) => [id, v.verdict])),
        report: prompt.match(/--out ([\w./:-]+)/)[1] }
        : opts.phase === 'Merge' ? { findings: [mergedFinding()] }
          : { findings: [sourceFindings[opts.label]] })
    check('native publisher validates originals then displays the weakest member verdict',
      grouped.result.verification.status === 'fail' && grouped.result.verification.counts.VERIFIED === 1
      && grouped.result.verification.counts.UNVERIFIED === 1
      && grouped.result.findings[0].verdict === 'UNVERIFIED', JSON.stringify(grouped.result))
  } finally { fs.rmSync(tmp, { recursive: true, force: true }) }
  const clean = await run({ ...noFoldArgs('full'), fold: FOLD,
    __toolProfiles: { verify: { agentType: 'general-purpose', tier: 'fanout' } } },
  (prompt, opts) => opts.phase === 'Verify' ? { status: 'pass', counts: { VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: 0 },
    verdicts: {}, report: prompt.match(/--out ([\w./:-]+)/)[1] } : { findings: [] })
  const cleanVerify = clean.calls.find(call => call.opts.phase === 'Verify')
  check('a clean review skips the fold and still names each fold subject\'s snapshot to the post-check',
    clean.result.fold.status === 'skipped' && cleanVerify && cleanVerify.prompt.includes("--reviewed-snapshot '" + SNAPSHOT + "'"),
    JSON.stringify({ fold: clean.result.fold && clean.result.fold.status, prompt: cleanVerify && cleanVerify.prompt.slice(0, 600) }))
  const ruling = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest: goodDigest({
    applied: [{ id: 'F1', change: 'owner wording', source: 'ruling' }],
    designDelta: [PLAN_P + ': owner wording'],
  }) }))
  check('a ruling with different applied wording is provisional: the post-check owns the old/new facts',
    ruling.result.fold.status === 'provisional' && !flagKinds(ruling).some(k => k.startsWith('fold-')),
    JSON.stringify(ruling.result.flags))
  const foldCall = ruling.calls.find(call => call.opts.phase === 'Fold')
  const pinsFor = (r, label) => r.logs.filter(l => l.startsWith('PINS ')).map(l => {
    try { return JSON.parse(l.slice(5))[label] } catch { return null }
  }).find(v => typeof v === 'string')
  check('the fold stage\'s PINS log carries the fold tier',
    foldCall && / tier:fanout$/.test(pinsFor(ruling, foldCall.opts.label) || ''),
    JSON.stringify({ label: foldCall && foldCall.opts.label, pins: ruling.logs.filter(l => l.startsWith('PINS ')) }))
  check('native fold has a stable promptPath and polls its amendment before action',
    ruling.result.fold.files.promptPath === '.claude/scratch/fold_fixture/fold_prompt.md'
      && ruling.calls.find(c => c.opts.phase === 'Fold').prompt.includes(
        'Amend: at each item and before returning, Read .claude/scratch/fold_fixture/fold_prompt.md.amend.md with the Read tool (Glob and Grep hide gitignored files); a not-found error means no amendment. A found amendment overrides the brief. Name what you applied.')
      && ruling.calls.find(c => c.opts.phase === 'Fold').prompt.includes('Persist this full engine brief with Write to the Prompt file before any subject action.')
      && ruling.calls.find(c => c.opts.phase === 'Fold').prompt.includes('Prompt file: .claude/scratch/fold_fixture/fold_prompt.md'))
  check('native digest schema admits ruling without changing designDelta strings',
    ruling.calls.find(c => c.opts.phase === 'Fold').opts.schema.properties.applied.items.properties.source.enum.includes('ruling'))
  for (const mode of ['full', 'bounded']) {
    const r = await run(noFoldArgs(mode), foldFixtureReply)
    const got = fingerprint(r)
    check('no args.fold: ' + mode + ' preserves its result, options and logs',
      got === NO_FOLD_FINGERPRINTS[mode], got)
    check('no args.fold: ' + mode + ' spawns no Fold stage',
      !r.calls.some(c => c.opts.phase === 'Fold'))
    check('no args.fold: ' + mode + ' result carries the verification block for the quote verdicts',
      r.result.verification.status === 'unavailable', String(r.result.verification))
  }
  const bareSeat = await run({ agents: [reviewAgent('one')] }, { findings: [] })
  check('a review with no spillDir still names its post-check, writing under .claude/scratch',
    bareSeat.result.verification.status === 'unavailable' && bareSeat.result.verification.report.startsWith('.claude/scratch/post_check-verify-'),
    String(bareSeat.result.verification))

  const noSpill = await run({ agents: [reviewAgent('one')], fold: FOLD }, foldFixtureReply)
  check('args.fold without spillDir fails before any dispatch', !!noSpill.result.error && noSpill.calls.length === 0)
  const T = { templatePath: TEMPLATE, tier: 'fanout' }
  for (const [label, fold, transport] of [
    ['no subjects', { ...T }],
    ['an empty subjects array', { ...T, subjects: [] }],
    ['a non-array subjects', { ...T, subjects: subjectOf([PLAN_P]) }],
    ['the retired planPath', { ...T, planPath: PLAN_P }],
    ['the retired planPaths', { ...T, planPaths: [PLAN_P] }],
    ['a subject with no files', { ...T, subjects: [subjectOf([])] }],
    ['a subject with no snapshot', { ...T, subjects: [{ root: '.', files: [PLAN_P] }] }],
    ['an absolute subject file', { ...T, subjects: [subjectOf(['C:/x/plan.md'])] }],
    ['a subject file escaping the repo', { ...T, subjects: [subjectOf(['../plan.md'])] }],
    ['an absolute root', { ...T, subjects: [subjectOf([PLAN_P], { root: '/x' })] }],
    ['a root escaping the repo', { ...T, subjects: [subjectOf([PLAN_P], { root: '../x' })] }],
    ['an absolute snapshot', { ...T, subjects: [subjectOf([PLAN_P], { snapshot: 'C:/x/s.json' })] }],
    ['a path with a space', { ...T, subjects: [subjectOf(['.claude/plans/p q.md'])] }],
    ['a path with a shell metacharacter', { ...T, subjects: [subjectOf(['.claude/plans/p;q.md'])] }],
    ['a newPath escaping the repo', { ...T, subjects: [subjectOf([PLAN_P], { newPaths: ['../t.py'] })] }],
    ['a file repeated across subjects', { ...T, subjects: [subjectOf([PLAN_P]), subjectOf([PLAN_P.toUpperCase()])] }],
    ['an empty templatePath', { subjects: FOLD.subjects, templatePath: '', tier: 'fanout' }],
    ['an absolute templatePath', { subjects: FOLD.subjects, templatePath: 'C:/x/t.md', tier: 'fanout' }],
    ['a templatePath escaping the repo', { subjects: FOLD.subjects, templatePath: '../t.md', tier: 'fanout' }],
    ['an unknown tier with no model', { subjects: FOLD.subjects, templatePath: TEMPLATE, tier: 'nope', model: null }],
    ['an illegal model pin', { subjects: FOLD.subjects, templatePath: TEMPLATE, model: 'gpt-9' }],
    ['an illegal effort pin', { subjects: FOLD.subjects, templatePath: TEMPLATE, model: 'sonnet', effort: 'max' }, EFFORTED],
  ]) {
    const bad = await run({ ...noFoldArgs('full'), fold, ...(transport ? { __transport: transport } : {}) }, foldFixtureReply)
    check('args.fold with ' + label + ' fails before any dispatch', !!bad.result.error && bad.calls.length === 0, JSON.stringify(bad.result.error))
  }
  const retired = await run({ ...noFoldArgs('full'), fold: { ...T, planPath: PLAN_P } }, foldFixtureReply)
  check('the retired planPath error names subjects as the replacement', /subjects/.test(String(retired.result.error)), String(retired.result.error))

  const LEDGER = { stage: 'plan-check', subject: 'plan:' + PLAN_P, roster: '.claude/scratch/fold_fixture/roster.json' }
  for (const [label, ledger] of [
    ['a stage no ledger knows', { ...LEDGER, stage: 'nope' }],
    ['a subject with no kind', { ...LEDGER, subject: PLAN_P }],
    ['a subject of an unknown kind', { ...LEDGER, subject: 'task:x' }],
    ['a roster path with a space', { ...LEDGER, roster: 'a b.json' }],
    ['an absolute roster path', { ...LEDGER, roster: '/x/r.json' }],
    ['a scopeJson with a metacharacter', { ...LEDGER, scopeJson: 'a;b.json' }],
    ['a non-object ledger', 'plan-check'],
    ['a ledger with no roster', { stage: 'plan-check', subject: LEDGER.subject }],
    ['a task with a space', { ...LEDGER, task: 'a b' }],
    ['no reviewed file (no scopeJson, blobs or fold subject)', LEDGER],
  ]) {
    const bad = await run({ ...noFoldArgs('full'), ledger }, foldFixtureReply)
    check('args.ledger with ' + label + ' fails before any seat runs', !!bad.result.error && bad.calls.length === 0, JSON.stringify(bad.result.error))
  }
  const ledgerSrc = fs.readFileSync(path.join(__dirname, '..', 'tools', 'review_ledger.py'), 'utf8')
  const ledgerTuple = name => (ledgerSrc.match(new RegExp('^' + name + ' = \\(([^)]*)\\)', 'm')) || [, ''])[1].match(/"([^"]+)"/g).map(s => s.slice(1, -1))
  const engineStages = (src.match(/const LEDGER_STAGES = \[([^\]]*)\]/) || [, ''])[1].match(/'([^']+)'/g).map(s => s.slice(1, -1))
  check('the engine ledger stages equal review_ledger STAGES', JSON.stringify(engineStages) === JSON.stringify(ledgerTuple('STAGES')), JSON.stringify(engineStages))
  const SCOPED = { ...LEDGER, scopeJson: '.claude/scratch/fold_fixture/scope.json' }
  check('args.ledger accepts a blob map in place of a scope file',
    !(await run({ ...noFoldArgs('full'), ledger: { ...LEDGER, blobs: '.claude/scratch/fold_fixture/blobs.json' } }, foldFixtureReply)).result.error)
  for (const stage of ledgerTuple('STAGES')) {
    const good = await run({ ...noFoldArgs('full'), ledger: { ...SCOPED, stage } }, foldFixtureReply)
    check('args.ledger accepts the review_ledger stage ' + stage, !good.result.error, JSON.stringify(good.result.error))
  }
  for (const kind of ledgerTuple('SUBJECT_KINDS')) {
    const good = await run({ ...noFoldArgs('full'), ledger: { ...SCOPED, subject: kind + ':x.md' } }, foldFixtureReply)
    check('args.ledger accepts the review_ledger subject kind ' + kind, !good.result.error, JSON.stringify(good.result.error))
  }
  const ledgered = await run({ ...noFoldArgs('full'), ledger: { ...LEDGER, noNewDecision: true, scopeJson: '.claude/scratch/fold_fixture/scope.json', task: 'urf-1' } }, foldFixtureReply)
  check('a valid args.ledger rides into the verification block',
    ledgered.result.verification.status === 'unavailable',
    JSON.stringify(ledgered.result.verification))

  const ok = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply())
  const plain = await run(noFoldArgs('full'), foldFixtureReply)
  const seatPrompts = (r) => r.calls.filter(c => c.opts.phase === 'Review').map(c => c.prompt)
  check('seat prompts are unchanged by args.fold', JSON.stringify(seatPrompts(ok)) === JSON.stringify(seatPrompts(plain)))
  const phases = ok.calls.map(c => c.opts.phase)
  check('with args.fold, the Fold stage runs once and no Verify stage runs',
    phases.filter(p => p === 'Fold').length === 1 && !phases.includes('Verify'), phases.join(','))
  const fold = ok.calls.find(c => c.opts.phase === 'Fold')
  const ids = ['F1', 'F2', 'F3']
  check('Fold receives its subject lines, a findings path, verdict path and the complete source set',
    fold && fold.opts.label === 'review:fold' && fold.prompt.startsWith('FOLD STAGE of a review.')
      && fold.prompt.includes('\nRoot: .\n') && fold.prompt.includes('\nSnapshot: ' + SNAPSHOT + '\n')
      && fold.prompt.includes('\nFiles: ' + PLAN_P + '\n') && !fold.prompt.includes('Pre-fold copy') && !fold.prompt.includes('planPath')
      && ids.every(id => fold.prompt.includes('"id":"' + id + '"'))
      && fold.prompt.includes('fold_findings.json') && fold.prompt.includes('fold_verdicts.json')
      && fold.prompt.includes('verify_claims.py --findings')
      && fold.prompt.includes(TEMPLATE) && !fold.prompt.includes('fold_claims.json'),
    fold && fold.prompt.slice(0, 900))
  const defaulted = await run({ ...noFoldArgs('full'), fold: { subjects: FOLD.subjects, tier: 'fanout' } }, foldReply())
  const defaultedFold = defaulted.calls.find(c => c.opts.phase === 'Fold')
  check('an args.fold without templatePath reads the one default fold brief',
    !defaulted.result.error && defaultedFold && defaultedFold.prompt.includes('Read .claude/commands/agents/review_fold.md '),
    JSON.stringify(defaulted.result.error))
  check('Fold has only its own write scope and receives both checker paths',
    fold && !fold.prompt.includes('Read-only: do NOT modify') && fold.prompt.includes('Write only')
      && fold.prompt.includes('fold_findings.json') && fold.prompt.includes('fold_verdicts.json'))
  check('fold schema requires judgment and changed paths only, never a fact the checker computes',
    fold && ['applied', 'deferred', 'designDelta', 'changedPaths'].every(k => (fold.opts.schema.required || []).includes(k))
      && ['decisions', 'verdicts', 'verdictPath', 'mechanicalChecks'].every(k => !(k in fold.opts.schema.properties))
      && ['source', 'noTest'].every(k => k in fold.opts.schema.properties.applied.items.properties)
      && fold.opts.schema.properties.deferred.items.required.includes('class'),
    fold && JSON.stringify(fold.opts.schema))
  check('the fold prompt asks for no verdict, verdictPath or question-null filter and applies each resolved PLAN option',
    fold && !fold.prompt.includes('verdictPath') && !fold.prompt.includes('Return a verdicts row') && !fold.prompt.includes('question null')
      && fold.prompt.includes('each PLAN') && fold.prompt.includes('options[0]'),
    fold && fold.prompt.slice(0, 1800))
  check('a well-formed digest validates and is returned',
    ok.result.fold && ok.result.fold.status === 'provisional' && ok.result.fold.digest.applied[0].id === 'F1', JSON.stringify(ok.result.fold))
  check('a digest that passes the engine checks stays provisional and the result names the post-check',
    ok.result.fold && ok.result.fold.status !== 'ok' && ok.result.verification.status === 'unavailable'
      && ok.result.fold.postFoldCheck === undefined,
    JSON.stringify({ fold: ok.result.fold, verification: ok.result.verification }))
  const prof = ok.logs.find(l => l.startsWith('TOOL-PROFILES '))
  check('TOOL-PROFILES omits Verify and records Fold', !!prof && !prof.includes('review:verify') && prof.includes('review:fold'), prof)

  const arch = await run({ ...noFoldArgs('full'), fold: { ...FOLD, model: 'opus', effort: 'high' } }, foldReply())
  const archFold = arch.calls.find(c => c.opts.phase === 'Fold')
  check('fold stage takes its declared model and effort',
    archFold && archFold.opts.model === 'opus' && archFold.opts.effort === 'high', archFold && JSON.stringify(archFold.opts))

  const newPathRun = await run({ ...noFoldArgs('full'), fold: { ...FOLD, subjects: [subjectOf([PLAN_P], { newPaths: ['Tests/Logic/NewTest.cs'] })] } }, foldReply())
  const newPathFold = newPathRun.calls.find(c => c.opts.phase === 'Fold')
  check('declared new paths ride the fold prompt and its write scope',
    newPathFold && newPathFold.prompt.includes('\nNew paths: Tests/Logic/NewTest.cs\n')
      && /Write only: [^\n]*Tests\/Logic\/NewTest\.cs/.test(newPathFold.prompt), newPathFold && newPathFold.prompt.slice(0, 700))

  for (const [label, digest, kind] of [
    ['no changedPaths list', goodDigest({ changedPaths: undefined }), 'fold-invalid-shape'],
    ['no deferred list', goodDigest({ deferred: undefined }), 'fold-invalid-shape'],
    ['an applied row with no change text', goodDigest({ applied: [{ id: 'F1' }] }), 'fold-invalid-shape'],
    ['no schema object', null, 'fold-no-return'],
  ]) {
    const r = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest }))
    check('fold digest with ' + label + ' is flagged ' + kind,
      flagKinds(r).includes(kind) && r.result.fold.status === 'invalid', JSON.stringify(r.result.flags))
  }
  for (const [label, digest] of [
    ['an id absent from applied and deferred', goodDigest({ deferred: [{ id: 'F2', class: 'taste-fork', reason: 'taste fork' }] })],
    ['an id listed twice', goodDigest({ deferred: [{ id: 'F1', class: 'conflict', reason: 'conflict' }, { id: 'F2', class: 'taste-fork', reason: 'x' }, { id: 'F3', class: 'spike', reason: 'y' }] })],
    ['stale decisions, verdicts and mechanicalChecks fields', goodDigest({ decisions: [], verdicts: [], verdictPath: 'x', mechanicalChecks: [] })],
  ]) {
    const r = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest }))
    check('fold digest with ' + label + ' stays provisional: post_check holds the id, the engine checks no fact',
      r.result.fold.status === 'provisional' && !flagKinds(r).some(k => k.startsWith('fold-')), JSON.stringify(r.result.flags))
  }
  const unchangedClaim = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest: goodDigest({ changedPaths: [] }) }))
  check('the engine reads no disk, so an empty changedPaths stays provisional and the post-check decides',
    unchangedClaim.result.fold.status === 'provisional' && !flagKinds(unchangedClaim).includes('fold-unchanged-plan'),
    JSON.stringify(unchangedClaim.result.flags))

  const own = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest: goodDigest({
    applied: [{ id: 'F1', change: 'wrote a better change', source: 'fold' }] }) }))
  check('a fold-written change is valid', own.result.fold.status !== 'invalid', JSON.stringify(own.result.flags))

  const adapted = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest: goodDigest({
    applied: [{ id: 'F1', change: 'merged with another finding fix', source: 'adapted' }] }) }))
  check('an adapted change is valid', adapted.result.fold.status !== 'invalid', JSON.stringify(adapted.result.flags))

  const noTest = await run({ ...noFoldArgs('full'), fold: FOLD }, foldReply({ digest: goodDigest({
    applied: [{ id: 'F1', change: 'typo', source: 'finding', noTest: 'docs only' }] }) }))
  check('an applied entry may carry a noTest reason', noTest.result.fold.status === 'provisional', JSON.stringify(noTest.result.flags))

  const broad = await run({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD },
    (_prompt, opts) => opts.phase === 'Fold'
      ? goodDigest({
        applied: ['F1', 'F3', 'F4'].map(id => ({ id, change: 'applied ' + id, source: 'finding' })),
        deferred: [{ id: 'F2', class: 'owner-input', reason: 'owner-input' }],
      })
      : { findings: [
        finding({ agent: 'one', file: PLAN_P + ':1', description: 'ordinary fix' }),
        finding({ agent: 'one', file: PLAN_P + ':2', action: 'ASK', question: 'A or B?', options: ['A', 'B'] }),
        finding({ agent: 'one', file: PLAN_P + ':3', critical: true, description: 'critical correction' }),
        finding({ agent: 'one', file: PLAN_P + ':4', action: 'PLAN', scope: ['plan section'], options: ['Apply the design work'], description: 'test-readiness design work' }),
      ] })
  check('fold applies critical, PLAN and test-readiness findings; holds ASK',
    broad.result.fold && broad.result.fold.status === 'provisional' && !flagKinds(broad).some(k => k === 'fold-applied-held'), JSON.stringify(broad.result))

  const evidenceApplied = await run({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD },
    (_prompt, opts) => opts.phase === 'Fold' ? goodDigest({
      applied: [{ id: 'F1', change: 'applied claim', source: 'finding' }], deferred: [],
    }) : { findings: [finding({ agent: 'one', file: PLAN_P + ':1', refuting: true, evidence: [{ file: 'source.js:4', quote: 'not present' }] })] })
  check('an applied evidence-bearing or refuting id is provisional: verdicts are the checker\'s, never the digest\'s',
    evidenceApplied.result.fold.status === 'provisional' && !flagKinds(evidenceApplied).some(k => k.startsWith('fold-')),
    JSON.stringify(evidenceApplied.result.flags))

  const refutingMerged = await run({
    agents: [reviewAgent('one'), reviewAgent('two')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD,
  }, (_prompt, opts) => {
    if (opts.phase === 'Merge') return { findings: [{ merged_from: ['F1', 'F2'] }] }
    if (opts.phase === 'Fold') return goodDigest({
      applied: [{ id: 'M1', change: 'corrected source premise', source: 'finding' }], deferred: [],
    })
    const one = opts.label === 'review:one'
    return { findings: [finding({ agent: opts.label, file: PLAN_P + ':' + (one ? '1' : '2'), refuting: one,
      evidence: [{ file: 'source.js:' + (one ? '1' : '2'), quote: one ? 'first quote' : 'second quote' }] })] }
  })
  const mergedFold = refutingMerged.calls.find(c => c.opts.phase === 'Fold')
  check('merged view unions evidence and carries a member refutation into the sole Fold stage',
    mergedFold && !refutingMerged.calls.some(c => c.opts.phase === 'Verify')
      && mergedFold.prompt.includes('"refuting":true') && mergedFold.prompt.includes('first quote') && mergedFold.prompt.includes('second quote')
      && refutingMerged.result.fold.status === 'provisional', JSON.stringify(refutingMerged.result))

  const bounded = await run({ ...noFoldArgs('bounded'), fold: FOLD }, foldReply())
  check('bounded delivery carries the fold digest, status and the post-check inline',
    bounded.result.fold && bounded.result.fold.status === 'provisional' && JSON.stringify(bounded.result.fold).includes('F2')
      && bounded.result.verification.status === 'unavailable', JSON.stringify(bounded.result.fold))
  const empty = await run({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD }, () => ({ findings: [] }))
  check('with no findings the Fold stage does not run but the post-check is still named',
    !empty.calls.some(c => c.opts.phase === 'Fold') && empty.result.fold && empty.result.fold.status === 'skipped'
      && empty.result.verification.status === 'unavailable')
  const strayFold = await run({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD },
    () => ({ findings: [finding({ agent: 'one', file: 'src/other.js:1', description: 'outside the subject' })] }))
  check('a single-subject fold leaves a finding citing a file outside its subject unfolded and held, not a routing failure',
    !flagKinds(strayFold).includes('fold-routing') && !strayFold.calls.some(c => c.opts.phase === 'Fold')
      && strayFold.result.fold.status === 'skipped' && strayFold.result.fold.outOfSubject.join() === 'F1',
    JSON.stringify({ flags: strayFold.result.flags, fold: strayFold.result.fold }))
}

// MANY SUBJECTS: one review over split plans. Each finding routes to the one subject whose files hold its
// cited file, and one fold job per subject runs the digest checks.
const PLAN_A = '.claude/plans/a.md'
const PLAN_B = '.claude/plans/b.md'
const JOINT = { subjects: [subjectOf([PLAN_A]), subjectOf([PLAN_B])], templatePath: TEMPLATE, tier: 'fanout' }
const jointSeat = (files) => () => ({ findings: files.map((file, i) => finding({ agent: 'one', file, description: 'finding ' + i })) })
const digestFor = (ids, prompt = '') => goodDigest({
  applied: ids.map(id => ({ id, change: 'applied ' + id, source: 'finding' })), deferred: [],
})
const idsIn = (prompt) => ['F1', 'F2', 'F3', 'F4'].filter(id => prompt.includes('"id":"' + id + '"'))
const jointReply = (files, digestOf = (ids, prompt) => digestFor(ids, prompt)) => (prompt, opts) =>
  opts.phase === 'Fold' ? digestOf(idsIn(prompt), prompt) : jointSeat(files)()
const jointArgs = (fold, mode = 'full') => ({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', resultMode: mode, fold })

async function jointFoldCases() {
  const files = [PLAN_A + ':3', PLAN_B + ':5', PLAN_A + ':12', '.claude\\plans\\b.md:9']
  const ok = await run(jointArgs(JOINT), jointReply(files))
  const folds = ok.calls.filter(c => c.opts.phase === 'Fold')
  const byPlan = (plan) => folds.find(c => c.prompt.includes('\nFiles: ' + plan + '\n'))
  check('many subjects run one fold job per subject with distinct labels',
    folds.length === 2 && new Set(folds.map(c => c.opts.label)).size === 2 && !ok.calls.some(c => c.opts.phase === 'Verify'),
    JSON.stringify(folds.map(c => c.opts.label)))
  check('each fold job receives only the findings whose file its subject holds',
    byPlan(PLAN_A) && JSON.stringify(idsIn(byPlan(PLAN_A).prompt)) === '["F1","F3"]'
      && byPlan(PLAN_B) && JSON.stringify(idsIn(byPlan(PLAN_B).prompt)) === '["F2","F4"]',
    JSON.stringify(folds.map(c => idsIn(c.prompt))))
  const writeLine = (c) => (c.prompt.match(/Write only: [^\n]*/) || [''])[0]
  check('each fold job writes only its own subject files, findings, verdicts and digest',
    byPlan(PLAN_A) && byPlan(PLAN_B) && writeLine(byPlan(PLAN_A)).includes(PLAN_A) && !writeLine(byPlan(PLAN_A)).includes(PLAN_B)
      && writeLine(byPlan(PLAN_B)).includes(PLAN_B) && !writeLine(byPlan(PLAN_B)).includes(PLAN_A)
      && writeLine(byPlan(PLAN_A)).replace(PLAN_A, '') !== writeLine(byPlan(PLAN_B)).replace(PLAN_B, ''),
    folds.map(writeLine).join(' | '))
  check('a clean fold over many subjects returns provisional with one result per subject and one post-check',
    ok.result.fold && ok.result.fold.status === 'provisional' && Array.isArray(ok.result.fold.subjects)
      && ok.result.fold.subjects.map(p => p.subject.files.join('')).join(',') === PLAN_A + ',' + PLAN_B
      && ok.result.fold.subjects.every(p => p.status === 'provisional')
      && ok.result.verification.status === 'unavailable',
    JSON.stringify(ok.result.fold))
  const prof = ok.logs.find(l => l.startsWith('TOOL-PROFILES '))
  check('TOOL-PROFILES records every fold label', !!prof && folds.every(c => prof.includes(c.opts.label)), prof)

  const stray = await run(jointArgs(JOINT), jointReply([PLAN_A + ':3', 'src/other.js:1']))
  check('a finding citing a file in no subject is held and the routed subjects still fold',
    !flagKinds(stray).includes('fold-routing') && stray.result.fold.status === 'provisional'
      && stray.result.fold.outOfSubject.join() === 'F2' && stray.calls.filter(c => c.opts.phase === 'Fold').length === 1,
    JSON.stringify({ flags: stray.result.flags, fold: stray.result.fold }))

  const bare = await run(jointArgs(JOINT), jointReply(['a.md:3', 'plans/b.md:5']))
  const bareFolds = bare.calls.filter(c => c.opts.phase === 'Fold')
  check('a finding citing a path suffix routes to the one subject it names',
    !flagKinds(bare).includes('fold-routing') && bareFolds.length === 2,
    JSON.stringify({ flags: bare.result.flags, folds: bareFolds.map(c => idsIn(c.prompt)) }))

  const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, 'fold_routing_vectors.json'), 'utf8'))
  const REPO_ROOT = 'C:/work/{{PROJECT_NAME}}'
  for (const v of vectors.cases) {
    const plans = v.plans || vectors.plans
    const cited = v.file.split('<root>').join(REPO_ROOT)
    const r = await run(jointArgs({ ...JOINT, repoRoot: REPO_ROOT, subjects: plans.map(p => subjectOf([p], { root: v.subjectRoot || '.' })) }), jointReply([cited]))
    const folded = plans.findIndex(p => r.calls.some(c => c.opts.phase === 'Fold' && c.prompt.includes('\nFiles: ' + p + '\n') && idsIn(c.prompt).includes('F1')))
    const detail = ((r.result.flags || []).find(f => f.kind === 'fold-routing') || {}).detail || ''
    const held = ((r.result.fold || {}).outOfSubject || []).includes('F1')
    const got = folded >= 0 ? folded : held || /no subject file: F1\b/.test(detail) ? 'unrouted' : /several: F1\b/.test(detail) ? 'ambiguous' : null
    check('shared routing vector ' + JSON.stringify(v.file) + ' routes to ' + v.expect, got === v.expect,
      JSON.stringify({ got, flags: r.result.flags }))
  }

  const noRoot = await run(jointArgs(JOINT), jointReply([REPO_ROOT + '/' + PLAN_A + ':3']))
  const noRootDetail = ((noRoot.result.flags || []).find(f => f.kind === 'fold-routing') || {}).detail || ''
  check('an absolute citation with no args.fold.repoRoot stays unrouted and the flag names the missing repoRoot',
    /no subject file: F1\b/.test(noRootDetail) && noRootDetail.includes('this call passed no args.fold.repoRoot') && noRoot.result.fold.status === 'invalid',
    JSON.stringify(noRoot.result.flags))
  const wrongRoot = await run(jointArgs({ ...JOINT, repoRoot: 'C:/elsewhere/checkout' }), jointReply([REPO_ROOT + '/' + PLAN_A + ':3']))
  const wrongDetail = ((wrongRoot.result.flags || []).find(f => f.kind === 'fold-routing') || {}).detail || ''
  check('an absolute citation under another root stays unrouted, never suffix-matched, and the flag does not blame a missing repoRoot',
    /no subject file: F1\b/.test(wrongDetail) && !wrongDetail.includes('passed no args.fold.repoRoot'), JSON.stringify(wrongRoot.result.flags))
  for (const bad of ['relative/checkout', '.', 42, 'C:/repo/../other']) {
    const refused = await run(jointArgs({ ...JOINT, repoRoot: bad }), jointReply([]))
    check('args.fold.repoRoot ' + JSON.stringify(bad) + ' is refused before any agent runs',
      !!refused.result.error && refused.result.error.includes('repoRoot') && refused.calls.length === 0, JSON.stringify(refused.result))
  }
  const slashed = await run(jointArgs({ ...JOINT, repoRoot: REPO_ROOT.replace(/\//g, '\\') + '\\' }), jointReply([REPO_ROOT + '/' + PLAN_A + ':3']))
  check('a repoRoot spelled with backslashes and a trailing separator resolves like the slashed spelling',
    !flagKinds(slashed).includes('fold-routing'), JSON.stringify(slashed.result.flags))

  const WT_PLAN = '.claude/worktrees/peer/.claude/plans/composition.md'
  const incident = await run(jointArgs({ ...JOINT, repoRoot: REPO_ROOT, subjects: [subjectOf([WT_PLAN])] }),
    jointReply([REPO_ROOT + '/' + WT_PLAN + ':12', REPO_ROOT.replace(/\//g, '\\') + '\\' + WT_PLAN.replace(/\//g, '\\') + ':30']))
  check('absolute citations of a worktree plan, subject root ".", fold both findings',
    !flagKinds(incident).includes('fold-routing') && incident.result.fold.status === 'provisional'
      && incident.calls.filter(c => c.opts.phase === 'Fold').some(c => idsIn(c.prompt).join() === 'F1,F2'),
    JSON.stringify({ flags: incident.result.flags, fold: incident.result.fold }))

  const halfBad = await run(jointArgs(JOINT), jointReply(files, (ids, prompt) =>
    prompt.includes('\nFiles: ' + PLAN_B + '\n') ? digestFor(ids.slice(1), prompt) : digestFor(ids, prompt)))
  const planB = halfBad.result.fold && (halfBad.result.fold.subjects || []).find(p => p.subject.files[0] === PLAN_B)
  check('a subject digest that omits an id is still provisional: post_check holds that id alone',
    !flagKinds(halfBad).includes('fold-id-coverage') && halfBad.result.fold.status === 'provisional'
      && planB && planB.status === 'provisional',
    JSON.stringify(halfBad.result.fold))

  const bounded = await run(jointArgs(JOINT, 'bounded'), jointReply(files))
  check('bounded delivery carries each subject fold inline',
    bounded.result.fold && bounded.result.fold.status === 'provisional' && Array.isArray(bounded.result.fold.subjects)
      && bounded.result.fold.subjects.length === 2 && JSON.stringify(bounded.result.fold).includes('F4')
      && bounded.result.verification.status === 'unavailable',
    JSON.stringify(bounded.result.fold))
}
// POST-CHECK. The engine cannot read disk, so its passing fold is provisional; post_check.py re-derives
// verdicts from the engine-authored fold prompt, the journal digest, the snapshot blobs and the file bytes.
const childProcess = require('child_process')
const os = require('os')
const REPO = path.join(__dirname, '..', '..')
const CHECK_DIR = '.claude/scratch/fold_check_fixture_' + process.pid + '_' + Date.now().toString(36)
const CHECK_PLAN = CHECK_DIR + '/p.md'
async function verificationCases() {
  const dir = path.join(REPO, CHECK_DIR)
  fs.rmSync(dir, { recursive: true, force: true })
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, 'src.txt'), 'def x():\n    return None\n')
  fs.writeFileSync(path.join(dir, 'p.md'), 'Step 3: teh default.\n')
  const snap = childProcess.spawnSync('python3', [path.join(REPO, '.claude/tools/post_check.py'), 'snapshot', '--root', '.', '--files', CHECK_PLAN,
    '--out', CHECK_DIR + '/snapshot.json'], { cwd: REPO, encoding: 'utf8' })
  fs.writeFileSync(path.join(dir, 'p.md'), 'Step 3: the default.\n')
  const seat = { findings: [finding({ agent: 'one', file: CHECK_PLAN + ':1', description: 'typo', old: 'teh default', new: 'the default',
    evidence: [{ file: CHECK_DIR + '/src.txt:2', quote: 'return None' }] })] }
  const digestOf = (prompt, over = {}) => goodDigest(Object.assign({
    applied: [{ id: 'F1', change: 'typo', source: 'finding' }], deferred: [],
    changedPaths: [CHECK_PLAN],
  }, over))
  const foldArgs = { agents: [reviewAgent('one')], spillDir: CHECK_DIR,
    fold: { subjects: [subjectOf([CHECK_PLAN], { snapshot: CHECK_DIR + '/snapshot.json' })], templatePath: TEMPLATE, tier: 'fanout' } }
  const checkRun = async (label, over) => {
    const r = await run(foldArgs, (prompt, opts) => opts.phase === 'Fold' ? digestOf(prompt, over) : seat)
    const call = r.calls.find(c => c.opts.phase === 'Fold')
    const digest = r.result.fold && r.result.fold.digest
    const wf = fs.mkdtempSync(path.join(os.tmpdir(), 'foldwf-'))
    fs.writeFileSync(path.join(wf, 'agent-fold1.jsonl'), JSON.stringify({ type: 'user', message: { role: 'user', content: call.prompt } }) + '\n')
    fs.writeFileSync(path.join(wf, 'journal.jsonl'), [
      { type: 'started', key: 'one', agentId: 'review1', label: 'review:one', phase: 'Review' },
      { type: 'result', key: 'one', agentId: 'review1', result: withCoverage(seat) },
      { type: 'started', key: 'k', agentId: 'fold1', label: call.opts.label, phase: 'Fold' },
      { type: 'result', key: 'k', agentId: 'fold1', result: digest },
    ].map(row => JSON.stringify(row)).join('\n') + '\n')
    const out = path.join(wf, 'fold_check.json')
    const proc = childProcess.spawnSync('python3', [path.join(REPO, '.claude/tools/fold_check.py'), '--workflow-dir', wf, '--out', out], { cwd: REPO, encoding: 'utf8' })
    const report = fs.existsSync(out) ? JSON.parse(fs.readFileSync(out, 'utf8')) : null
    return { r, proc, report, label }
  }
  try {
    check('the snapshot command writes the manifest the fold prompt names', snap.status === 0 && fs.existsSync(path.join(dir, 'snapshot.json')), snap.stderr)
    const normal = await checkRun('normal', {})
    check('a normal fold passes the shared post-fold checker with equal independent verdicts',
      normal.r.result.fold.status === 'provisional' && normal.proc.status === 0 && normal.report
        && JSON.stringify(normal.report.independentVerdicts) === JSON.stringify([{ id: 'F1', verdict: 'VERIFIED' }]),
      normal.proc.stderr + ' ' + JSON.stringify(normal.report))
    fs.writeFileSync(path.join(dir, 'p.md'), 'Step 3: owner-selected wording.\n')
    const ruling = await checkRun('ruling', {
      applied: [{ id: 'F1', change: 'owner selected a different fix', source: 'ruling' }],
    })
    check('a native ruling with different wording produces the required re-review unit',
      ruling.r.result.fold.status === 'provisional' && ruling.proc.status === 0 && ruling.report
        && ruling.report.nextRound.some(block => block.seats.some(seat => seat.role === 're-review'
          && seat.units.includes('F1') && seat.mandates.includes('one'))),
      ruling.proc.stderr + ' ' + JSON.stringify(ruling.report))
    fs.writeFileSync(path.join(dir, 'p.md'), 'Step 3: the default.\n')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
}

;(async () => {
  const clean = await run({
    agents: [reviewAgent('one')],
    spillDir: '.claude/scratch/review-spills',
  }, { findings: [] })
  const boundedClean = await run({
    agents: [reviewAgent('one')],
    spillDir: '.claude/scratch/review-spills',
    resultMode: 'bounded',
  }, { findings: [] })
  check('legal repository-relative scratch spill root runs', !clean.result.error, JSON.stringify(clean.result))
  check('read-only contract carves out only the lens spill',
    clean.prompts[0] && clean.prompts[0].includes('EXCEPT your own spill file .claude/scratch/review-spills/review_one.spill.md'),
    clean.prompts[0])
  check('lens writes complete schema result before returning',
    clean.prompts[0] && clean.prompts[0].includes('Write your FULL JSON deliverable') && clean.prompts[0].includes('BEFORE returning'),
    clean.prompts[0])
  check('clean result reports spill evidence',
    clean.result.spills && clean.result.spills.one === '.claude/scratch/review-spills/review_one.spill.md'
      && boundedClean.result.delivery.payloadComplete === false
      && boundedClean.result.delivery.preview.spillMetadata.total === 2
      && boundedClean.result.delivery.preview.spillMetadata.omitted === 2,
    JSON.stringify({ full: clean.result, bounded: boundedClean.result }))
  check('workflow evidence label matches review:<key>',
    clean.calls[0] && clean.calls[0].opts.label === 'review:one',
    JSON.stringify(clean.calls.map(call => call.opts)))
  check('a given agentType forwards when no profile row swaps it',
    clean.calls[0] && clean.calls[0].opts.agentType === 'general-purpose',
    JSON.stringify(clean.calls.map(call => call.opts)))

  const noWrite = await run({
    agents: [reviewAgent('scout', { agentType: 'Explore' })],
    spillDir: '.claude/scratch/review-spills',
  }, { findings: [] })
  check('no-Write review agents return inline without an impossible spill contract',
    !noWrite.result.error
      && noWrite.prompts[0].includes('Read-only: do NOT modify, create, or delete any file.')
      && !noWrite.prompts[0].includes('Write your FULL JSON deliverable')
      && Array.isArray(noWrite.result.inlineLabels)
      && noWrite.result.inlineLabels.includes('scout')
      && !('scout' in (noWrite.result.spills || {})),
    JSON.stringify({ result: noWrite.result, prompt: noWrite.prompts[0] }))

  // The lean-type swap reads the guard's coverage verdict, and the spill decision reads the type the
  // lens actually runs as: lane-review has no Write tool, so a covered lens returns inline.
  const leanRow = { shape: 'review', builtins: ['Read', 'Glob', 'Grep'], mcpServers: [], add: [], remove: [],
    overrideMode: 'shape-default', enforcement: 'inherited', agentType: 'lane-review' }
  const leanSpill = await run({
    agents: [reviewAgent('lean'), reviewAgent('open')],
    spillDir: '.claude/scratch/review-spills',
    __toolProfileDefaults: { review: leanRow },
    __coverage: { lean: { covered: ['review'], needs: [], unread: [] } },
  }, { findings: [] })
  const leanCall = leanSpill.calls.find(call => call.opts.label === 'review:lean') || { opts: {}, prompt: '' }
  const openCall = leanSpill.calls.find(call => call.opts.label === 'review:open') || { opts: {}, prompt: '' }
  check('a coverage-proven lens runs as lane-review and returns inline without a spill contract',
    !leanSpill.result.error && leanCall.opts.agentType === 'lane-review'
      && !leanCall.prompt.includes('Write your FULL JSON deliverable')
      && !('lean' in (leanSpill.result.spills || {}))
      && (leanSpill.result.inlineLabels || []).includes('lean'),
    JSON.stringify({ result: leanSpill.result, opts: leanCall.opts }))
  check('a lens with no coverage entry stays general-purpose and keeps its spill contract',
    openCall.opts.agentType === 'general-purpose'
      && openCall.prompt.includes('Write your FULL JSON deliverable')
      && (leanSpill.result.spills || {}).open === '.claude/scratch/review-spills/review_open.spill.md',
    JSON.stringify({ result: leanSpill.result, opts: openCall.opts }))

  const deadNoWrite = await run({
    agents: [reviewAgent('dead-scout', { agentType: 'Plan' })],
    spillDir: '.claude/scratch/review-spills',
  }, null)
  const deadNoWriteFlag = deadNoWrite.result.flags.find(f => f.kind === 'lens-no-return')
  check('no-Write recovery never claims a spill file exists',
    deadNoWriteFlag && !deadNoWriteFlag.detail.includes('review_dead-scout.spill.md')
      && deadNoWriteFlag.detail.includes('/salvage_fanout'),
    JSON.stringify(deadNoWrite.result))

  const dead = await run({
    agents: [reviewAgent('dead')],
    spillDir: '.claude/scratch/review-spills',
  }, null)
  const deadFlag = dead.result.flags && dead.result.flags.find(f => f.kind === 'lens-no-return')
  check('null return names deterministic spill recovery path',
    deadFlag && deadFlag.detail.includes('.claude/scratch/review-spills/review_dead.spill.md'),
    JSON.stringify(dead.result))

  const legacy = await run({ agents: [reviewAgent('legacy')] }, { findings: [] })
  check('omitted spillDir keeps prior read-only contract',
    !legacy.result.error && legacy.prompts[0].includes('Read-only: do NOT modify, create, or delete any file.') && !legacy.prompts[0].includes('EXCEPT your own spill file'),
    legacy.prompts[0])
  check('omitted spillDir adds no spill metadata', !('spillDir' in legacy.result), JSON.stringify(legacy.result))

  const illegalRoots = [
    'reports/review-spills',
    '.claude/scratch/../../outside',
    'C:/repo/.claude/scratch/review-spills',
    'project/.claude/scratch/review-spills',
    'C:/Users/test/AppData/Local/Temp/claude/review-spills',
  ]
  for (const spillDir of illegalRoots) {
    const invalidRoot = await run({
      agents: [reviewAgent('bad')], spillDir,
    }, { findings: [] })
    check('spillDir rejects non-contained root: ' + spillDir,
      !!invalidRoot.result.error && invalidRoot.prompts.length === 0,
      JSON.stringify(invalidRoot.result))
  }

  const duplicate = await run({
    agents: [reviewAgent('same'), reviewAgent('same')],
    spillDir: '.claude/scratch/review-spills',
  }, { findings: [] })
  check('duplicate lens keys fail before dispatch',
    !!duplicate.result.error && duplicate.prompts.length === 0 && duplicate.result.error.includes('same'),
    JSON.stringify(duplicate.result))

  const collision = await run({
    agents: [reviewAgent('a:b'), reviewAgent('a_b')],
    spillDir: '.claude/scratch/review-spills',
  }, { findings: [] })
  check('sanitized spill-path collisions fail before dispatch',
    !!collision.result.error && collision.prompts.length === 0,
    JSON.stringify(collision.result))

  const caseFoldCollision = await run({
    agents: [reviewAgent('Lens'), reviewAgent('lens')],
    spillDir: '.claude/scratch/review-spills',
  }, { findings: [] })
  check('spill-path collisions case-fold for Windows',
    !!caseFoldCollision.result.error && caseFoldCollision.prompts.length === 0,
    JSON.stringify(caseFoldCollision.result))

  const missingKey = await run({
    agents: [{ prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: 'general-purpose' }],
  }, { findings: [] })
  check('missing lens key fails before dispatch',
    !!missingKey.result.error && missingKey.prompts.length === 0,
    JSON.stringify(missingKey.result))

  const missingAgentType = await run({
    agents: [{ key: 'missing-type', prompt: 'Review it.', model: 'sonnet', effort: 'medium', mustRead: [{ path: 'fixture', lines: 1 }] }],
  }, { findings: [] })
  check('an omitted agentType dispatches on the runtime default',
    !missingAgentType.result.error && missingAgentType.prompts.length === 1,
    JSON.stringify(missingAgentType.result))

  const blankAgentType = await run({
    agents: [{ key: 'blank-type', prompt: 'Review it.', model: 'sonnet', effort: 'medium', agentType: ' ' }],
  }, { findings: [] })
  check('a blank agentType fails before dispatch',
    !!blankAgentType.result.error && blankAgentType.prompts.length === 0,
    JSON.stringify(blankAgentType.result))

  for (const badAgent of [null, []]) {
    const invalidAgent = await attempt({ agents: [badAgent] }, { findings: [] })
    check('invalid review agent object fails legibly before property access: ' + JSON.stringify(badAgent),
      !invalidAgent.thrown && invalidAgent.run && !!invalidAgent.run.result.error
        && invalidAgent.run.prompts.length === 0,
      invalidAgent.thrown ? String(invalidAgent.thrown.stack || invalidAgent.thrown) : JSON.stringify(invalidAgent.run.result))
  }

  const codexMax = await run({
    agents: [reviewAgent('codex', { model: 'gpt-test', effort: 'max' })],
    __transport: {
      name: 'codex', ids: ['gpt-test'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
  }, { findings: [] })
  check('an injected provider max effort is accepted and forwarded',
    !codexMax.result.error && codexMax.calls[0] && codexMax.calls[0].opts.effort === 'max',
    JSON.stringify({ result: codexMax.result, calls: codexMax.calls.map(call => call.opts) }))

  const invalidTransports = [
    ['missing ids', { name: 'codex', efforts: ['max'] }],
    ['empty ids', { name: 'codex', ids: [], efforts: ['max'] }],
    ['malformed ids', { name: 'codex', ids: 'gpt-test', efforts: ['max'] }],
    ['non-string ids', { name: 'codex', ids: ['gpt-test', null], efforts: ['max'] }],
    ['empty efforts', { name: 'codex', ids: ['gpt-test'], efforts: [] }],
    ['malformed efforts', { name: 'codex', ids: ['gpt-test'], efforts: 'max' }],
    ['non-string efforts', { name: 'codex', ids: ['gpt-test'], efforts: ['max', null] }],
  ]
  for (const [label, __transport] of invalidTransports) {
    const invalidTransport = await run({
      agents: [reviewAgent('invalid-transport', { model: 'gpt-test', effort: 'max' })],
      __transport,
    }, { findings: [] })
    check('invalid injected transport fails before review dispatch: ' + label,
      !!invalidTransport.result.error && invalidTransport.prompts.length === 0,
      JSON.stringify(invalidTransport.result))
  }

  const omittedEffort = await run({
    agents: [reviewAgent('default-effort', { effort: undefined })],
  }, { findings: [] })
  check('omitted review effort is not defaulted to medium where the transport declares no efforts',
    !omittedEffort.result.error && omittedEffort.calls[0] && omittedEffort.calls[0].opts.effort === undefined,
    JSON.stringify({ result: omittedEffort.result, calls: omittedEffort.calls.map(call => call.opts) }))

  const invalidEffort = await run({
    agents: [reviewAgent('bad-effort', { effort: 'max' })],
    __transport: { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] },
  }, { findings: [] })
  check('present unsupported review effort fails before dispatch',
    !!invalidEffort.result.error && invalidEffort.prompts.length === 0,
    JSON.stringify(invalidEffort.result))

  for (const model of [null, '', 0, false]) {
    const invalidModel = await run({
      agents: [reviewAgent('falsey-model', { model })],
    }, { findings: [] })
    check('present falsey review model fails before dispatch: ' + JSON.stringify(model),
      !!invalidModel.result.error && invalidModel.prompts.length === 0,
      JSON.stringify(invalidModel.result))
  }

  const oneSeatMerge = await run({
    agents: [reviewAgent('one')],
  }, (_prompt, opts) => ({ findings: [finding({ file: '.claude/a.js:1', agent: opts.label })] }))
  check('one seat never starts Merge', !oneSeatMerge.calls.some(c => c.opts.phase === 'Merge'))

  const disjointMerge = await run({
    agents: [reviewAgent('one'), reviewAgent('two')],
  }, (_prompt, opts) => ({ findings: [finding({ file: opts.label + '.js:1', agent: opts.label })] }))
  check('two seats with no shared file never start Merge', !disjointMerge.calls.some(c => c.opts.phase === 'Merge'))

  const candidates = await run({
    agents: [reviewAgent('one'), reviewAgent('two'), reviewAgent('three')],
  }, (_prompt, opts) => {
    if (opts.phase === 'Merge') return { findings: [{ merged_from: ['F1', 'F2'] }, { merged_from: ['F3'] }] }
    if (opts.label === 'review:three') {
      return { findings: [finding({ file: 'shared.js:3', agent: opts.label, description: opts.label }),
                          finding({ file: 'unique.js:1', agent: opts.label, description: 'untouched' })] }
    }
    const file = 'shared.js:' + (opts.label === 'review:one' ? '1' : '2')
    return { findings: [finding({ file, agent: opts.label, description: opts.label })] }
  })
  const candidateMerge = candidates.calls.find(c => c.opts.phase === 'Merge')
  check('shared-file candidates run one Explore Merge at fanout/medium',
    candidateMerge && candidateMerge.opts.agentType === 'Explore' && candidateMerge.opts.model === 'sonnet'
      && candidateMerge.opts.effort === 'medium', candidateMerge && JSON.stringify(candidateMerge.opts))
  check('Merge sees only candidate ids and candidate partition adds untouched singleton',
    candidateMerge && candidateMerge.prompt.includes('"id":"F1"') && candidateMerge.prompt.includes('"id":"F2"')
      && candidateMerge.prompt.includes('"id":"F3"') && !candidateMerge.prompt.includes('"id":"F4"')
      && candidates.result.findings.length === 3
      && candidates.result.findings.some(f => f.id === 'F4')
      && candidates.result.findings.some(f => f.id === 'F3')
      && candidates.result.findings.some(f => f.merged_from && f.merged_from.join(',') === 'F1,F2')
      && !candidates.result.flags.some(f => f.kind === 'consolidate-invalid'),
    JSON.stringify({ prompt: candidateMerge && candidateMerge.prompt, result: candidates.result }))

  const evidenceFold = await run({
    agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture',
    fold: FOLD,
  }, (_prompt, opts) => opts.phase === 'Fold'
    ? goodDigest({
      applied: [{ id: 'F1', change: 'applied verified evidence', source: 'finding' }], deferred: [],
    })
    : { findings: [finding({ agent: 'one', file: PLAN_P + ':1', evidence: [{ file: 'source.js:4', quote: 'source claim' }], refuting: true })] })
  const evidenceFoldCall = evidenceFold.calls.find(c => c.opts.phase === 'Fold')
  check('fold replaces Verify and runs claim checks from its own stage',
    !evidenceFold.calls.some(c => c.opts.phase === 'Verify') && evidenceFoldCall
      && evidenceFoldCall.prompt.includes('verify_claims.py --findings')
      && evidenceFoldCall.prompt.includes('fold_findings.json')
      && evidenceFoldCall.prompt.includes('fold_verdicts.json')
      && !('verdicts' in evidenceFoldCall.opts.schema.properties),
    JSON.stringify({ phases: evidenceFold.calls.map(c => c.opts.phase), fold: evidenceFoldCall }))
  check('fold digest VERIFIED evidence permits its applied finding',
    evidenceFold.result.fold && evidenceFold.result.fold.status === 'provisional',
    JSON.stringify({ fold: evidenceFold.result.fold, flags: evidenceFold.result.flags }))

  const broadFold = await run({
    agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture',
    fold: FOLD,
  }, (_prompt, opts) => opts.phase === 'Fold'
    ? goodDigest({
      applied: [
        { id: 'F1', change: 'fixed typo', source: 'finding' },
        { id: 'F3', change: 'resolved critical correction', source: 'finding' },
        { id: 'F4', change: 'resolved design finding', source: 'finding' },
        { id: 'F5', change: 'completed test-readiness item', source: 'finding' },
      ], deferred: [{ id: 'F2', class: 'owner-input', reason: 'owner-input' }],
    })
    : { findings: [
      finding({ agent: 'one', file: PLAN_P + ':1', description: 'ordinary fix' }),
      finding({ agent: 'one', file: PLAN_P + ':1', action: 'ASK', question: 'A or B?', options: ['A', 'B'] }),
      finding({ agent: 'one', file: PLAN_P + ':1', critical: true, description: 'critical correction' }),
      finding({ agent: 'one', file: PLAN_P + ':1', action: 'PLAN', scope: ['plan section'], options: ['Apply the design work'], description: 'design work' }),
      finding({ agent: 'one', file: PLAN_P + ':1', description: 'test-readiness work' }),
    ] })
  check('fold applies critical, PLAN and test-readiness findings; holds ASK',
    broadFold.result.fold && broadFold.result.fold.status === 'provisional'
      && !flagKinds(broadFold).includes('fold-applied-held'), JSON.stringify(broadFold.result))

  const holdPrompt = broadFold.calls.find(c => c.opts.phase === 'Fold')
  const foldTemplate = fs.readFileSync(path.join(__dirname, '..', 'commands', 'agents', 'review_fold.md'), 'utf8')
  check('the fold prompt defers its hold list to the template, which names every hold class',
    holdPrompt && holdPrompt.prompt.includes('Read ' + TEMPLATE) && holdPrompt.prompt.includes('Hold exactly the cases steps 4 and 5 of the template list')
      && !['owner-input', 'own-design'].some(k => holdPrompt.prompt.includes('(' + k + ')'))
      && !holdPrompt.prompt.includes('Apply every supported finding')
      && ['owner-input', 'own-design', 'unbased-number', 'new-mechanism'].every(k => foldTemplate.includes('`' + k + '`')),
    holdPrompt && holdPrompt.prompt.slice(0, 1600))
  check('the fold prompt defers the not-VERIFIED hold and its --ruled exception to template step 5',
    holdPrompt && holdPrompt.prompt.includes('steps 4 and 5') && !holdPrompt.prompt.includes('not VERIFIED (`unverified`)')
      && foldTemplate.includes('applied only with a VERIFIED verdict') && foldTemplate.includes('--ruled'),
    holdPrompt && holdPrompt.prompt.slice(0, 400))

  const altReply = (_prompt, opts) => opts.phase === 'Fold'
    ? goodDigest({
      applied: [{ id: 'F1', change: 'took A', source: 'finding' }, { id: 'F2', change: 'took C', source: 'finding' }],
      deferred: [{ id: 'F3', class: 'own-design', reason: 'own-design' }],
    })
    : { findings: [
      finding({ agent: 'one', file: PLAN_P + ':1', action: 'PLAN', scope: ['plan section'], description: 'with alternative', options: ['Do A', 'Alternative: B; wins when C'] }),
      finding({ agent: 'one', file: PLAN_P + ':1', action: 'PLAN', scope: ['plan section'], description: 'no alternative', options: ['Do C'] }),
      finding({ agent: 'one', file: PLAN_P + ':1', action: 'PLAN', scope: ['plan section'], description: 'open', question: 'Which?', options: ['Do D', 'Do E; wins when F'] }),
    ] }
  const altArgs = mode => ({ agents: [reviewAgent('one')], spillDir: '.claude/scratch/fold_fixture', fold: FOLD, resultMode: mode })
  const altFull = await run(altArgs('full'), altReply)
  const expectedAlt = [{ id: 'F1', applied: 'Do A', alternative: 'Alternative: B; wins when C' }]
  check('the fold digest lists each applied resolved PLAN that carries an alternative',
    altFull.result.fold && JSON.stringify(altFull.result.fold.digest.appliedWithAlternative) === JSON.stringify(expectedAlt),
    JSON.stringify(altFull.result.fold))
  const altBounded = await run(altArgs('bounded'), altReply)
  check('the bounded fold block carries appliedWithAlternative whole',
    altBounded.result.fold && JSON.stringify(altBounded.result.fold.appliedWithAlternative) === JSON.stringify(expectedAlt),
    JSON.stringify(altBounded.result.fold))

  const validMerge = await runMerge([mergedFinding()])
  check('valid semantic merge is accepted',
    validMerge.result.findings.length === 1
      && !validMerge.result.flags.some(f => f.kind === 'consolidate-invalid'),
    JSON.stringify(validMerge.result))

  checkFallback('unknown provenance id falls back to the deterministic list',
    await runMerge([mergedFinding({ merged_from: ['F1', 'F2', 'F9'] })]))
  checkFallback('empty provenance id falls back to the deterministic list',
    await runMerge([mergedFinding({ merged_from: ['F1', 'F2', ''] })]))
  checkFallback('empty provenance list falls back to the deterministic list',
    await runMerge([
      mergedFinding({ description: 'first merged', merged_from: [] }),
      mergedFinding({ description: 'second merged', merged_from: ['F1', 'F2'] }),
    ]))
  checkFallback('duplicate provenance id falls back to the deterministic list',
    await runMerge([
      mergedFinding({ description: 'first merged', merged_from: ['F1'] }),
      mergedFinding({ description: 'second merged', merged_from: ['F1', 'F2'] }),
    ]))
  checkFallback('merge cannot lower strongest critical value',
    await runMerge([mergedFinding({ critical: false })]))
  checkFallback('merge cannot name a less restrictive decider than its members',
    await runMerge([mergedFinding({ action: 'FIX' })]))
  checkFallback('merge cannot name a more restrictive decider than its members',
    await runMerge([mergedFinding({ action: 'ASK' })]))
  checkFallback('merge cannot lower strongest category tier',
    await runMerge([mergedFinding({ category: 'improvement' })]))

  const weakSources = {
    'review:strong': finding({
      agent: 'weak-one', critical: false, action: 'PLAN', category: 'improvement',
      file: 'weak-shared.js:1', description: 'weak source one',
    }),
    'review:weak': finding({
      agent: 'weak-two', critical: false, action: 'PLAN', category: 'improvement',
      file: 'weak-shared.js:2', description: 'weak source two',
    }),
  }
  const weakDescriptions = ['weak source one', 'weak source two']
  checkFallback('merge cannot raise strongest critical value',
    await runMerge([mergedFinding({ critical: true, action: 'PLAN', category: 'improvement' })], weakSources), weakDescriptions)
  checkFallback('merge of two PLANs cannot name FIX',
    await runMerge([mergedFinding({ critical: false, action: 'FIX', category: 'improvement' })], weakSources), weakDescriptions)
  checkFallback('merge cannot raise strongest category tier',
    await runMerge([mergedFinding({ critical: false, action: 'PLAN', category: 'bug' })], weakSources), weakDescriptions)

  const offTransport = await run({
    agents: [
      reviewAgent('one', { model: 'gpt-test' }),
      reviewAgent('two', { model: 'gpt-test' }),
    ],
    __transport: {
      name: 'codex', ids: ['gpt-test'],
      efforts: ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    },
  }, (_prompt, opts) => opts.phase === 'Merge'
    ? { findings: [mergedFinding()] }
    : { findings: [finding({ file: 'shared.js:' + (opts.label === 'review:one' ? '1' : '2'), description: opts.label, agent: opts.label })] })
  const mergeCall = offTransport.calls.find(call => call.opts.phase === 'Merge')
  check('the review:merge agent runs on the fanout model at medium effort as Explore, and its PINS log names it at its tier',
    mergeCall && mergeCall.opts.label === 'review:merge' && mergeCall.opts.model === 'gpt-test'
      && mergeCall.opts.effort === 'medium' && mergeCall.opts.agentType === 'Explore'
      && offTransport.logs.some(line => line.includes('"review:merge":"gpt-test/medium/Explore tier:fanout"')),
    JSON.stringify({ calls: offTransport.calls.map(call => call.opts), logs: offTransport.logs }))

  await foldCases()
  await jointFoldCases()
  await verificationCases()
  await barCases()

  console.log(`review-fanout spills: ${passed} passed, ${failed} failed`)
  process.exit(failed ? 1 : 0)
})().catch(err => {
  console.error(err)
  process.exit(1)
})
