const assert = require('assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const gitBash = require('./_git_bash')
const { load } = require('./_engine_run')
const root = path.resolve(__dirname, '../..')
const script = path.join(root, '.claude/workflows/dispatch.js')
const run = load(script, source => process.env.CLOSE_CHECK_DELETE_APPLY
  ? source.replace('applyVerdicts(items, verification.verdicts, keyOf)', 'items') : source)
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'close-check-'))
// The session the verifier's check command reads runs in a repository planted in temp (the tool asks git for the
// worktrees of its cwd), so the transcript store is keyed on that checkout, never on this one.
const checkout = path.join(temp, 'checkout')
fs.mkdirSync(checkout)
assert.equal(spawnSync('git', ['init', '-q'], { cwd: checkout, encoding: 'utf8' }).status, 0)
const project = path.join(temp, 'projects', fs.realpathSync(checkout).replace(/[^A-Za-z0-9]/g, '-'))
// post_check takes its repository from where its script sits; the same tool runs here with that one location set to
// the planted checkout, so its `git worktree list` answers from there.
const postCheck = 'import sys; sys.path.insert(0, ' + JSON.stringify(path.join(root, '.claude/tools').replace(/\\/g, '/')) + '); '
  + 'import post_check; from pathlib import Path; post_check.REPO = Path(' + JSON.stringify(checkout.replace(/\\/g, '/')) + '); '
  + 'sys.argv[0] = "post_check.py"; sys.exit(post_check.main())'
const directory = path.join(project, 'session/subagents/workflows/wf_tracer')
fs.mkdirSync(directory, { recursive: true })
const fixture = fs.readFileSync(path.join(root, '.claude/tests/fixtures/evidence/review/journal.jsonl'), 'utf8').trim().split('\n').map(JSON.parse)
const start = fixture.find(e => e.type === 'started')
const evidence = path.join(temp, 'source.txt')
fs.writeFileSync(evidence, 'The tracer reads a journal.\n')
const claims = [
  { id: 'true', confidence: 'verified', evidence: [{ file: evidence, quote: 'The tracer reads a journal.' }] },
  { id: 'false', confidence: 'verified', evidence: [{ file: evidence, quote: 'A fabricated sentence.' }] },
  { id: 'empty', confidence: 'verified', evidence: [] },
]
const text = '```claims\n' + claims.map(c => JSON.stringify(c)).join('\n') + '\n```'
const base = {
  jobs: [{ label: 'tracer', promptPath: 'brief.md', model: 'test-producer', effort: 'low', kind: 'lens', railTier: 'none' }],
  __transport: { name: 'test', ids: ['test-producer', 'test-verifier'], efforts: ['none', 'low', 'high'] },
  checker: { model: 'test-verifier', effort: 'low' },
  __returns: { tracer: 'claims' },
  __toolProfiles: { verify: { agentType: 'lane-verify', tier: 'fanout' },
    tracer: { kind: 'lens', builtins: ['Read', 'Glob', 'Grep'], mcpServers: [], add: [], remove: [], agentType: 'lane-survey', tier: 'fanout', guard: 'survey' } },
}
const reportPaths = new Set()
async function check(failure, declared = true) {
  let verifierCalls = 0
  const args = JSON.parse(JSON.stringify(base))
  if (!declared) delete args.__returns
  const result = await run(args, async (prompt, opts) => {
    if (opts.label === 'tracer') return text
    verifierCalls++
    assert.equal(opts.agentType, 'lane-verify')
    assert.equal(opts.model, 'test-verifier')
    assert.equal(opts.effort, 'low')
    assert.ok(opts.schema)
    assert.ok(prompt.includes('--run-label ' + opts.label))
    const reportPath = prompt.match(/--out (\S+)/)[1]
    if (reportPaths.size) assert.ok(reportPaths.has(reportPath), 'resumed inputs must reuse their report path')
    reportPaths.add(reportPath)
    if (failure === 'throw') throw Error('verifier unavailable')
    if (failure === 'missing') return null
    const out = path.join(temp, 'report.json')
    const events = [Object.assign({}, start, { agentId: 'producer', label: 'tracer' }), { type: 'result', agentId: 'producer', result: text }, Object.assign({}, start, { agentId: 'verifier', label: opts.label })]
    fs.writeFileSync(path.join(directory, 'journal.jsonl'), events.map(JSON.stringify).join('\n') + '\n')
    const done = spawnSync('python3', ['-c', postCheck, 'claims', '--run-label', opts.label, '--out', out], { cwd: checkout, env: Object.assign({}, process.env, { CLAUDE_CONFIG_DIR: temp }), encoding: 'utf8' })
    assert.equal(done.status, 1, done.stdout + done.stderr)
    const report = JSON.parse(fs.readFileSync(out, 'utf8'))
    if (failure === 'counts') report.counts.VERIFIED = 999
    return { status: report.status, counts: report.counts, verdicts: Object.fromEntries(Object.entries(report.verdicts).map(([k, v]) => [k, v.verdict])), report: reportPath }
  }, tasks => Promise.all(tasks.map(f => f())), () => {}, () => {})
  assert.equal(verifierCalls, 1)
  const rows = result.tracer.match(/```claims\n([\s\S]*?)```/)[1].trim().split('\n').map(JSON.parse)
  assert.equal(result.verification.status, failure ? 'unavailable' : 'fail')
  assert.deepEqual(rows.map(c => c.verdict), failure ? ['UNCHECKED', 'UNCHECKED', 'UNCHECKED'] : ['VERIFIED', 'UNVERIFIED', 'UNCHECKED'])
  assert.deepEqual(rows.map(c => c.confidence), failure ? ['unverified', 'unverified', 'unverified'] : ['verified', 'unverified', 'unverified'])
  assert.equal(result.verification.counts.UNCHECKED, failure ? 3 : 1)
  if (failure) assert.deepEqual(result.verification.verdicts, { true: 'UNCHECKED', false: 'UNCHECKED', empty: 'UNCHECKED' })
}
async function refused(args, pattern, message) {
  let calls = 0
  const result = await run(args, async () => { calls++; return text },
    tasks => Promise.all(tasks.map(f => f())), () => {}, () => {}).catch(e => ({ error: String(e.message || e) }))
  assert.match(String(result.error || ''), pattern, message)
  assert.equal(calls, 0, message)
}
// The verifier prompt: seats-file writes, one Bash command per line, then the one check command.
const verifierCommands = prompt => {
  const [before, after] = prompt.split('Run exactly this command with Bash: ')
  return { writes: (before.split('in order:\n')[1] || '').split('\n').filter(Boolean), final: after.split('\n')[0] }
}
const runWrites = (writes, cwd) => writes.forEach(command => {
  const done = spawnSync(gitBash(), ['-c', command], {cwd, encoding:'utf8'})
  assert.equal(done.status, 0, done.stderr)
})
// The agent Bash route cut every verifier command of 7,822 characters or more mid-quote (6 of 135 runs; every
// command up to 6,470 ran), so a wide panel's seats file is written in commands that each stay under the budget.
async function widePanel() {
  const engines = ['dispatch', 'dispatch_chains', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  const seats = Array.from({length: 8}, (_, s) => ({label:'review:sa-harness-quality-' + s, model:'test-producer', effort:'high', tier:'fanout',
    mandates:['harness-quality'], mustRead: Array.from({length: 40}, (_, i) => ({path:".claude/scratch/session_audit_x/packet/subject-" + (s * 40 + i) + ".diff", lines: i + 1}))}))
  for (const name of engines) {
    const unit = load(path.join(root, '.claude/workflows/' + name + '.js'), source => 'const A = args;\n'
      + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn closeCheck("review", {items: [], keyOf: c => c.id, out:"report.json", seats:args.seats})')
    let observed
    await unit({...base, seats, __repoRoot: temp}, async prompt => { observed = prompt; throw Error('fixture unavailable') }, undefined, undefined, () => {})
    const { writes, final } = verifierCommands(observed)
    const longest = Math.max(final.length, ...writes.map(command => command.length))
    assert.ok(writes.length > 1 && longest <= 6000, name + ': a wide panel needs several writes, each command within 6000 characters; longest ' + longest)
    runWrites(writes, temp)
    const seatsFile = observed.match(/--out (\S+)/)[1].replace(/\.json$/, '.seats.json')
    assert.deepEqual(JSON.parse(fs.readFileSync(seatsFile, 'utf8')),
      Object.fromEntries(seats.map(({label, ...seat}) => [label, seat])), name + ': the chunked writes rebuild the launched seats exactly')
    console.log('PASS ' + name + ' wide panel: every verifier command within budget, seats file rebuilt exactly')
  }
}
async function slice4c() {
  const engines = ['dispatch', 'dispatch_chains', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  const seats = [{label:'review:seat', model:'test-producer', effort:'high', tier:'executor', mandates:['sa-harness-quality'], mustRead:[{path:'source.txt', lines:1}]}]
  for (const name of engines) {
    const unit = load(path.join(root, '.claude/workflows/' + name + '.js'), source => 'const A = args;\n'
      + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn closeCheck("review", {items: [], keyOf: c => c.id, out:"report.json", seats:args.seats, ledger:args.ledger})')
    let observed
    const result = await unit({...base, seats, ledger:{premiseCommits:'premises.json', head:'frozen-head', blobs:'blobs.json'}}, async (prompt, opts) => {
      observed = prompt
      const { writes } = verifierCommands(prompt)
      assert.ok(writes.length, name + ': missing seats-file write')
      runWrites(writes, temp)
      const report = prompt.match(/--out (\S+)/)[1]
      const seatsFile = report.replace(/\.json$/, '.seats.json')
      assert.deepEqual(JSON.parse(fs.readFileSync(path.join(temp, seatsFile))), {'review:seat': Object.fromEntries(Object.entries(seats[0]).filter(([key]) => key !== 'label'))})
      assert.ok(prompt.includes("--seats-file '" + seatsFile + "'"))
      for (const [flag, value] of [['premise-commits','premises.json'], ['head','frozen-head'], ['blobs','blobs.json']]) assert.ok(prompt.includes('--' + flag + " '" + value + "'"), name + ': missing frozen ' + flag)
      return {status:'pass', counts:{VERIFIED:0, UNVERIFIED:0, UNCHECKED:0}, verdicts:{}, report}
    }, undefined, undefined, () => {})
    assert.ok(observed)
    assert.equal(result.verification.status, 'pass', name + ': seats-file write or frozen forwarding failed')
    console.log('PASS ' + name + ' seats-file write and frozen ledger flags')
  }
}
// A verifier that reports its --out path resolved (absolute, Windows separators) reports the same file;
// a path naming another file is still refused. Captured live: wf_7c3b636b-5e2 lost 86 verdicts this way.
async function reportPathSpelling() {
  const engines = ['dispatch', 'dispatch_chains', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  for (const name of engines) {
    for (const [spell, expected] of [
      [p => 'C:\\Users\\me\\repo\\' + p.replace(/\//g, '\\'), 'pass'],
      [p => '/c/Users/me/repo/' + p, 'pass'],
      [p => 'C:\\Users\\me\\repo\\other-' + p, 'unavailable'],
      // A verifier may wrap the path in prose: the engine still opens the file.
      [p => '/c/Users/me/repo/' + p + '. Windows path: C:\\Users\\me\\repo\\' + p.replace(/\//g, '\\'), 'pass'],
    ]) {
      const unit = load(path.join(root, '.claude/workflows/' + name + '.js'), source => 'const A = args;\n'
        + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
        + '\nreturn closeCheck("claims", {items: [{id:"x"}], keyOf: c => c.id, out:".claude/scratch/report.json"})')
      let out
      const result = await unit(base, async prompt => {
        out = prompt.match(/--out (\S+)/)[1]
        return {status:'pass', counts:{VERIFIED:1, UNVERIFIED:0, UNCHECKED:0}, verdicts:{x:'VERIFIED'}, report: spell(out)}
      }, undefined, undefined, () => {})
      assert.equal(result.verification.status, expected, name + ': report path ' + spell('.claude/scratch/report.json'))
      assert.equal(result.verification.report, out, name + ': the result carries the engine\'s own --out path, not the verifier\'s text')
    }
    console.log('PASS ' + name + ' report path matches by file, not spelling')
  }
}
// A verifier may append an observation after the exact --out path: the engine keeps the verdicts and
// carries the observation in `note`.
async function reportNote() {
  const engines = ['dispatch', 'dispatch_chains', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  const observation = 'the summary printed pass but the file status is fail (copied as given).'
  for (const name of engines) {
    for (const [spell, expected, note] of [
      [p => p + '. Note: ' + observation, 'pass', 'kept'],
      [p => 'C:\\Users\\me\\repo\\' + p.replace(/\//g, '\\') + ', ' + observation, 'pass', 'kept'],
      [p => 'other-' + p + '. Note: ' + observation, 'unavailable', 'dropped'],
      [p => 'Note: ' + observation + ' ' + p, 'unavailable', 'dropped'],
    ]) {
      const unit = load(path.join(root, '.claude/workflows/' + name + '.js'), source => 'const A = args;\n'
        + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
        + '\nreturn closeCheck("claims", {items: [{id:"x"}], keyOf: c => c.id, out:".claude/scratch/report.json"})')
      let schema
      const result = await unit(base, async (prompt, opts) => {
        schema = opts.schema
        const out = prompt.match(/--out (\S+)/)[1]
        return {status:'pass', counts:{VERIFIED:1, UNVERIFIED:0, UNCHECKED:0}, verdicts:{x:'VERIFIED'}, report: spell(out), note: observation}
      }, undefined, undefined, () => {})
      assert.equal(result.verification.status, expected, name + ': ' + spell('report.json'))
      if (note === 'kept') assert.equal(result.verification.note, observation, name + ': note kept')
      else {
        const thrown = fs.readFileSync(path.join(root, '.claude/workflows/' + name + '.js'), 'utf8').match(/throw new Error\('(incomplete verifier report)'\)/)[1]
        assert.equal(result.verification.note, thrown, name + ': a rejected checker\'s note is replaced by the rejection reason')
      }
      assert.deepEqual(schema.properties.note, { type: 'string' }, name + ': the checker schema offers an optional note')
      assert.ok(!schema.required.includes('note'), name + ': note is optional')
    }
    console.log('PASS ' + name + ' report text after the out path keeps verdicts and carries the note')
  }
}
async function slice4b() {
  const failures = []
  const engines = ['dispatch', 'dispatch_chains', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  for (const name of engines) {
    const unit = load(path.join(root, '.claude/workflows/' + name + '.js'), source => 'const A = args;\n'
      + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn closeCheck("claims", {items: [{id:"x"}], keyOf: c => c.id, out:"report.json"})')
    const logs = []
    let options, pinBeforeDispatch
    const result = await unit(base, async (_, opts) => {
      options = opts
      pinBeforeDispatch = logs.some(line => line.startsWith('PINS ') && JSON.parse(line.slice(5))[opts.label] === 'test-verifier/low/lane-verify tier:fanout')
      return {status:'pass', counts:{VERIFIED:0, UNVERIFIED:0, UNCHECKED:1}, verdicts:{x:'UNCHECKED'}, report:'report-' + opts.label + '.json'}
    }, undefined, undefined, line => logs.push(line))
    try {
      assert.ok(options)
      assert.equal(result.verification.status, 'unavailable', name + ': pass with UNCHECKED accepted')
      assert.ok(pinBeforeDispatch, name + ': verifier PINS must precede dispatch')
      console.log('PASS ' + name + ' verifier PINS and UNCHECKED consistency')
    } catch (e) { failures.push(e.message) }
  }
  for (const declaration of [
    {ledger:{stage:'candidate', subject:'doc:x', roster:'roster.json', scopeJson:'scope.json'}},
    {fold:{findings:'findings.json', digest:'digest.json', snapshot:'snapshot.json'}},
  ]) {
    const args = JSON.parse(JSON.stringify(base))
    Object.assign(args.jobs[0], declaration)
    delete args.__returns
    let prompt
    await run(args, async (p, opts) => {
      if (opts.label === 'tracer') return 'digest without a claims fence'
      prompt = p
      throw Error('fixture unavailable')
    }, tasks => Promise.all(tasks.map(f => f())), () => {}, () => {})
    try {
      assert.match(prompt || '', /post_check.py review /)
      for (const [field, value] of Object.entries(declaration.ledger || declaration.fold)) {
        const flag = field === 'scopeJson' ? 'scope-json' : field
        assert.ok(prompt.includes('--' + flag + " '" + value + "'"), 'missing ' + flag)
      }
      if (declaration.fold) assert.ok(!prompt.includes('--run-label'), 'standalone findings exclude journal source')
      console.log('PASS dispatch review ' + Object.keys(declaration)[0] + ' flags and trigger')
    } catch (e) { failures.push(e.message) }
  }
  assert.deepEqual(failures, [])
}
// S9: `pick` (why this pin) and `spec` (whether the slice's test intent is clearly defined) are optional
// launch facts. A job that records neither is accepted and logs no suffix; a legal value rides the PINS
// value; an illegal enum value is refused before any agent starts.
async function pickSpec() {
  const pinsValue = lines => {
    const line = lines.find(l => l.startsWith('PINS '))
    return line ? JSON.parse(line.slice(5)).tracer.replace(/ req:\S+ case:\S+$/, '') : null
  }
  const launched = async job => {
    const args = JSON.parse(JSON.stringify(base))
    Object.assign(args.jobs[0], job)
    const lines = []
    let tracerCalls = 0
    await run(args, async (prompt, opts) => { if (opts.label === 'tracer') tracerCalls++; return opts.label === 'tracer' ? text : null },
      tasks => Promise.all(tasks.map(f => f())), () => {}, line => lines.push(line))
    return { value: pinsValue(lines), tracerCalls }
  }
  const bare = 'test-producer/low/lane-survey tier:fanout'
  for (const [job, expected, message] of [
    [{}, bare, 'a job that records neither pick nor spec is accepted and logs no suffix'],
    [{ pick: 'cell', spec: 'tight' }, bare + ' pick:cell spec:tight', 'a legal pick and spec ride the PINS value'],
    [{ pick: 'owner', spec: 'judged' }, bare + ' pick:owner spec:judged', 'every legal pick value is accepted'],
    [{ spec: 'tight' }, bare + ' spec:tight', 'spec alone is accepted: the two fields are optional independently'],
    [{ pick: 'default' }, bare + ' pick:default', 'pick alone is accepted'],
  ]) {
    const got = await launched(job)
    assert.equal(got.value, expected, message)
    assert.equal(got.tracerCalls, 1, message + ': the run still starts its job')
  }
  for (const [job, field] of [[{ pick: 'vibes', spec: 'tight' }, 'pick'], [{ pick: 'cell', spec: 'vague' }, 'spec'],
                              [{ spec: '' }, 'spec'], [{ pick: null }, 'pick']]) {
    const args = JSON.parse(JSON.stringify(base))
    Object.assign(args.jobs[0], job)
    let calls = 0
    const result = await run(args, async () => { calls++; return text },
      tasks => Promise.all(tasks.map(f => f())), () => {}, () => {}).catch(e => ({ error: String(e.message || e) }))
    const error = String(result.error || '')
    assert.match(error, new RegExp(field + ' must be in'), 'an illegal ' + field + ' must be refused naming the field')
    assert.match(error, /orchestration §5/, 'the refusal names the rule')
    assert.equal(calls, 0, 'an illegal ' + field + ' must start no agent')
  }
  console.log('PASS dispatch pick/spec: optional, a legal value rides the PINS value, an illegal enum value is refused')
}
;(async () => {
  try {
    await slice4c()
    await widePanel()
    await reportPathSpelling()
    await reportNote()
    await slice4b()
    await pickSpec()
    await check(null)
    await check(null, false)
    await check('throw')
    await check('missing')
    await check('counts')
    console.log('PASS dispatch close-check: real checker verdicts, auto-fence trigger, failed and missing verifier')
    for (const mutation of [a => delete a.checker, a => { a.checker.model = 'other-transport' },
      a => { a.checker.effort = 'xhigh' }, a => { a.checker.effort = undefined }]) {
      const args = JSON.parse(JSON.stringify(base))
      mutation(args)
      await refused(args, /args\.checker/, 'an unpinned or illegal args.checker must not start any agent')
    }
    console.log('PASS args.checker gate: missing, outside ids, or illegal effort returns before any agent call')
    {
      // An undeclared claims fence with no args.checker: the run returns every claim UNCHECKED and names why,
      // and never throws after its jobs have spent.
      const args = JSON.parse(JSON.stringify(base))
      delete args.__returns
      delete args.checker
      let verifierCalls = 0
      const result = await run(args, async (_prompt, opts) => { if (opts.label === 'tracer') return text; verifierCalls++; return null },
        tasks => Promise.all(tasks.map(f => f())), () => {}, () => {})
      const rows = result.tracer.match(/```claims\n([\s\S]*?)```/)[1].trim().split('\n').map(JSON.parse)
      assert.equal(verifierCalls, 0)
      assert.equal(result.verification.status, 'unavailable')
      assert.match(result.verification.report, /args\.checker/)
      assert.deepEqual(rows.map(c => c.verdict), ['UNCHECKED', 'UNCHECKED', 'UNCHECKED'])
    }
    console.log('PASS undeclared fence without args.checker: claims return UNCHECKED, no throw, no verifier')
    for (const [name, build, pattern] of [
      ['dispatch', extra => ({ jobs: [{ label: 'j', promptPath: 'brief.md', effort: 'low', kind: 'reviewer' }], ...extra }), /every job/],
      ['review_fanout', extra => ({ agents: [{ key: 'k', promptPath: 'brief.md', effort: 'low', kind: 'reviewer', mustRead: [{ path: 'source.txt', lines: 1 }] }], ...extra }), /every job/],
      ['explore_fanout', extra => ({ lenses: [{ key: 'k', promptPath: 'brief.md', kind: 'reviewer' }], ...extra }), /every job/],
      ['idea_fanout', extra => ({ generators: [{ key: 'one', instr: 'x', effort: 'low' }, { key: 'two', instr: 'y', model: 'test-producer', effort: 'low' }], contextPrefix: 'ctx', kind: 'lens', __kindProfile: { kind: 'lens', agentType: 'lane-survey', builtins: ['Read', 'Glob', 'Grep'], mcpServers: [] }, ...extra }), /every job/],
    ]) {
      const args = build({ __transport: base.__transport, checker: base.checker })
      const started = []
      const engine = load(path.join(root, '.claude/workflows/' + name + '.js'))
      const result = await engine(args, async (prompt, opts) => { started.push(opts.label); return text },
        tasks => Promise.all(tasks.map(f => f())), () => {}, () => {}).catch(e => ({ error: String(e.message || e) }))
      assert.match(String(result.error || ''), pattern, name + ' must refuse a job with no model')
      assert.match(String(result.error), /pin model and effort on every job \(orchestration §5\)/, name + ' names the pin rule')
      assert.deepEqual(started, [], name + ' must start no agent for an unpinned job')
      console.log('PASS ' + name + ' job with no model: refused before any agent starts')
    }
    const override = JSON.parse(JSON.stringify(base))
    override.__toolProfiles.verify.model = 'other-transport'
    override.__toolProfiles.verify.effort = 'none'
    let resolved
    await run(override, async (prompt, opts) => {
      if (opts.label === 'tracer') return text
      resolved = opts
      throw Error('fixture unavailable')
    }, tasks => Promise.all(tasks.map(f => f())), () => {}, () => {})
    assert.equal(resolved.model, 'test-verifier')
    assert.equal(resolved.effort, 'low')
    console.log('PASS verifier pin: args.checker model and effort; missing row/rungs refused; caller pins ignored')
    for (const expression of ['Date.now()', 'new Date()', 'Math.random()']) {
      await assert.rejects(load(script, () => 'return ' + expression)(),
        expression === 'Math.random()' ? /Math.random\(\).*breaks resume/ : /Date.now\(\) \/ new Date\(\) are unavailable/)
    }
    assert.equal(await load(script, () => 'return Math.imul(3, 7)')(), 21)
    console.log('PASS Workflow loader: Date.now, Date constructor and Math.random blocked; pure Math works')
    const names = load(script, source => 'const A = args;\n'
      + source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn Promise.all(args.checks.map(check => closeCheck(check.mode, {items: check.keys, keyOf: key => key, out: "report.json"})))')
    const checks = [
      { mode: 'claims', keys: ['alpha', 'beta'] },
      { mode: 'claims', keys: ['alpha', 'beta'] },
      { mode: 'claims', keys: ['alpha', 'gamma'] },
      { mode: 'findings', keys: ['alpha', 'beta'] },
      { mode: 'claims', keys: ['a,b', 'c'] },
      { mode: 'claims', keys: ['a', 'b,c'] },
    ]
    const reports = (await names(Object.assign({}, base, { checks }), async () => { throw Error('fixture unavailable') }))
      .map(result => result.verification.report)
    assert.equal(reports[0], reports[1])
    assert.equal(new Set(reports).size, checks.length - 1)
    const resumed = (await names(Object.assign({}, base, { checks }), async () => { throw Error('fixture unavailable') }))
      .map(result => result.verification.report)
    assert.deepEqual(resumed, reports)
    console.log('PASS dispatch close-check names: concurrent keys and modes differ; resumed inputs reuse paths; key boundaries preserved')
  } finally { fs.rmSync(temp, { recursive: true, force: true }) }
})().catch(e => { console.error(e); process.exitCode = 1 })
