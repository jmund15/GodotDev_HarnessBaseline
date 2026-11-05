const assert = require('assert/strict')
const fs = require('fs')
const path = require('path')
const { load } = require('./_engine_run')
const os = require('os')
const {spawnSync} = require('child_process')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'remaining-engines-'))
fs.writeFileSync(path.join(temp, 'fixture'), 'true quote\n')
const liveStart = fs.readFileSync(path.resolve(__dirname, 'fixtures/evidence/review/journal.jsonl'), 'utf8').trim().split('\n').map(JSON.parse).find(row => row.type === 'started')
const root = path.resolve(__dirname, '../..')
const engines = ['dispatch', 'explore_fanout', 'review_fanout', 'doc_architecture_audit', 'dispatch_chains', 'key_reachability', 'worklog_relevance']
const base = { __transport: { ids: ['producer', 'checker'] }, checker: { model: 'checker', effort: 'low' }, __toolProfiles: { verify: { agentType: 'lane-verify', tier: 'fanout' } } }
// Every closeCheck argument must reach the verifier command in all seven engines: each engine carries its own
// copy of the block, and a copy that drops an argument runs a weaker check without failing anywhere else.
const reachCases = [
  { args: { fold: { findings: 'f.json', digest: 'd.json', snapshot: 's.json' }, root: 'subject-root', spillDir: 'spill', expectTotal: 3, expectSources: 2,
      ledger: { stage: 'candidate', subject: 'doc:x', roster: 'r.json', scopeJson: 'sc.json', task: 't', premiseCommits: 'p.json', head: 'h', blobs: 'b.json', noNewDecision: true } },
    flags: ['findings', 'digest', 'snapshot', 'root', 'spill-dir', 'expect-total', 'expect-sources', 'stage', 'subject', 'roster', 'scope-json', 'task', 'premise-commits', 'head', 'blobs', 'no-new-decision', 'seats-file'] },
  { args: { fold: { subjects: [{ snapshot: 'a.json' }] } }, flags: ['reviewed-snapshot', 'run-label'] },
]
const reachSeats = [{ label: 'review:seat', model: 'producer', effort: 'low', tier: 'fanout', mandates: [], mustRead: [] }]
const blockPattern = /\/\/ CLOSE-CHECK-BEGIN[\s\S]*?\/\/ CLOSE-CHECK-END/
const plantMissing = flag => source => {
  const block = source.match(blockPattern)[0]
  const site = ['--' + flag, "'" + flag + "'"].find(token => block.includes(token))
  assert.ok(site, flag + ': no site in the block to plant')
  return source.replace(block, block.replace(site, site.replace(flag, 'x-' + flag)))
}
async function missingFlags(file, transform = source => source) {
  const missing = []
  for (const { args, flags } of reachCases) {
    const unit = load(file, source => 'const A = args;\n' + transform(source).match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn closeCheck("review", {items: [], keyOf: c => c.id, out: "report.json", seats: A.__seats, ...A.__reach})')
    let prompt = ''
    await unit({ ...base, __reach: args, __seats: reachSeats }, async text => { prompt = text; throw Error('fixture unavailable') }, undefined, undefined, () => {})
    for (const flag of flags) if (!prompt.includes('--' + flag + ' ')) missing.push(flag)
  }
  return missing
}
;(async () => {
  for (const name of engines.filter(name => !process.argv[2] || process.argv[2] === name)) {
    const file = path.join(root, '.claude/workflows', name + '.js')
    if (!fs.existsSync(file)) continue // key_reachability.js is project-only (keep-local) and never ships to the baseline
    const source = fs.readFileSync(file, 'utf8')
    const block = source.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)
    assert.ok(block, name + ': missing close-check')
    assert.ok(source.includes('EVIDENCE-SSOT-BEGIN'), name + ': missing evidence contract')
    assert.ok(source.includes("'Evidence: ' + EVIDENCE_RULES"), name + ': producer prompt must carry canonical evidence rules')
    const runUnit = load(file, () => 'const A = args;\n' + block[1] + '\nreturn closeCheck("claims", {items: A.items, keyOf: item => item.key, out: "report.json", root: "subject root"})')
    const run = (args, reply) => runUnit(args, reply, undefined, undefined, () => {})
    const items = [{key: 'lens#2'}, {key: 'lens#1'}, {key: 'lens#3'}]
    const reply = { status: 'fail', counts: { VERIFIED: 1, UNVERIFIED: 1, UNCHECKED: 1 }, verdicts: {'lens#1': 'VERIFIED', 'lens#2': 'UNVERIFIED', 'lens#3': 'UNCHECKED'} }
    let observedPrompt
    const checked = await run({...base, items}, async (prompt, opts) => {
      observedPrompt = prompt
      assert.equal(opts.model, 'checker'); assert.equal(opts.effort, 'low')
      return {...reply, report: prompt.match(/--out ("[^"]+"|\S+)/)[1].replace(/^"|"$/g, '')}
    })
    assert.ok(observedPrompt.includes("--root 'subject root'"), 'root must be shell-literal, not interpolated')
    assert.deepEqual(checked.items.map(item => item.verdict), ['UNVERIFIED', 'VERIFIED', 'UNCHECKED'])
    assert.deepEqual(checked.items.map(item => item.confidence), ['unverified', 'verified', 'unverified'])
    const subset = await run({...base, items: [items[0]]}, async (prompt) => ({...reply, report: prompt.match(/--out (\S+)/)[1]}))
    assert.equal(subset.verification.status, 'fail', name + ': filtered items must not invalidate complete source report')
    assert.equal(subset.items[0].verdict, 'UNVERIFIED')
    const failed = await run({...base, items}, async () => { throw Error('no verifier') })
    assert.equal(failed.verification.status, 'unavailable')
    assert.ok(failed.items.every(item => item.verdict === 'UNCHECKED' && item.confidence === 'unverified'))
    assert.match(failed.verification.note || '', /no verifier/, name + ': an unavailable verification names its cause')
    // Captured live (wf_6a53dc1d-09d): the checker skipped reading --out and returned a placeholder verdict key.
    const placeholder = await run({...base, items}, async prompt => ({status: 'pass', counts: {VERIFIED: 3, UNVERIFIED: 0, UNCHECKED: 0},
      verdicts: {'claim-ALL': 'VERIFIED'}, report: prompt.match(/--out (\S+)/)[1]}))
    assert.equal(placeholder.verification.status, 'unavailable')
    assert.match(placeholder.verification.note || '', /incomplete verifier report/, name + ': a rejected report names why')
    // The command itself prints the verdict map, so the checker copies output it already holds.
    const printer = observedPrompt.split('Run exactly this command with Bash: ')[1].split('\n')[0].split(' ; ').pop()
    fs.writeFileSync(path.join(temp, observedPrompt.match(/--out (\S+)/)[1].replace(/^'|'$/g, '')), JSON.stringify({status: 'fail', counts: reply.counts,
      verdicts: Object.fromEntries(Object.entries(reply.verdicts).map(([k, v]) => [k, {verdict: v, items: []}]))}))
    const printed = spawnSync('bash', ['-c', printer], {cwd: temp, encoding: 'utf8'})
    assert.equal(printed.status, 0, name + ': verdict printer failed: ' + printed.stderr)
    assert.deepEqual(JSON.parse(printed.stdout.trim().split('\n').pop()), {status: 'fail', counts: reply.counts, verdicts: reply.verdicts},
      name + ': the command must print status, counts and the verdict map from --out')
    for (const invalid of [[{key: 'same'}, {key: 'same'}], [{}, {key: 'present'}]]) {
      await assert.rejects(run({...base, items: invalid}, async () => { throw Error('must not launch') }), /source key/)
    }
    console.log('PASS ' + name + ': source-key verdicts, downgrade, unavailable, root, invalid identities')
    assert.deepEqual(await missingFlags(file), [], name + ': every closeCheck argument must reach the verifier command')
    for (const flag of reachCases.flatMap(row => row.flags)) {
      assert.deepEqual(await missingFlags(file, plantMissing(flag)), [flag], name + ': a copy missing --' + flag + ' must fail the suite')
    }
    console.log('PASS ' + name + ': every closeCheck argument reaches the verifier command; a copy missing one fails')
    if (name === 'dispatch') continue
    const transport = {...base.__transport, name: 'fixture', efforts: ['low', 'high']}
    const checkedSource = {toolsUsed: ['Read:fixture'], stoppedAt: 'exhausted-leads', basis: 'read fixture'}
    const evidence = [{file: path.join(temp, 'fixture'), quote: 'true quote'}]
    const claim = (subject, index) => ({subject, claim: subject, polarity: 'exists', bearing: index ? 'premise-contradiction' : 'context', evidence: index === 2 ? [] : [{file: path.join(temp, 'fixture'), quote: index ? 'fake quote' : 'true quote'}], confidence: 'verified'})
    const specs = {
      explore_fanout: {args: {resultMode: 'full', lenses: [{key: 'lens', prompt: 'scan', model: 'producer', effort: 'low', agentType: 'Explore'}]}, reply: {claims: [claim('true', 0), claim('fake', 1), claim('empty', 2)], checked: checkedSource}, member: 'claims'},
      review_fanout: {args: {resultMode: 'full', agents: [{key: 'lens', prompt: 'scan', model: 'producer', effort: 'low', mustRead: [{path: 'fixture', lines: 1}]}]}, reply: {findings: [0,1,2].map(i => ({agent: 'lens', action: 'FIX', category: 'bug', critical: false, file: 'fixture:' + (i+1), description: ['true','fake','empty'][i], old: null, new: null, question: null, options: null, scope: null, rationale: 'why', evidence: claim('x', i).evidence})), checked: checkedSource}, member: 'findings'},
      doc_architecture_audit: {args: {context: 'context', model: 'producer', effort: 'low', tier: 'fanout'}, reply: {findings: [0,1,2].map(i => ({agent: 'da-structural', check: 'S2', severity: i === 1 ? 'critical' : 'warning', action: 'FIX', system: null, description: ['true','fake','empty'][i], recommendation: 'fix', handler: null, options: null, scope: [], evidence: claim('x',i).evidence}))}, member: 'findings'},
      dispatch_chains: {args: {chains: [{name: 'lane', jobs: [{label: 'job', promptPath: 'brief', model: 'producer', effort: 'low', agentType: 'Explore'}]}]}, reply: '```claims\n' + [claim('true',0),claim('fake',1),claim('empty',2)].map(JSON.stringify).join('\n') + '\n```', member: null},
      key_reachability: {args: {model: 'producer', effort: 'low', tasks: {task: {root: temp, ids: ['true','fake','empty']}}}, member: 'rulings'},
      worklog_relevance: {args: {scope: 'scope', model: 'producer', effort: 'low'}, reply: {overlaps: [0,1,2].map(i => ({title: ['true','fake','empty'][i], domain: 'domain', relation: 'same-scope', why: 'why', recommendation: 'read-first', evidence: claim('x',i).evidence})), checked: {indexRead: true, obsidianOpened: true, shortlisted: [], stoppedAt: 'verified-overlaps', basis: 'read', evidence}}, member: 'overlaps'},
    }
    const spec = specs[name]
    if (name === 'review_fanout') {
      spec.args.ledger = {stage:'candidate', subject:'doc:fixture', roster:'roster.json', premiseCommits:'premises.json', head:'a'.repeat(40), blobs:'blobs.json'}
      for (const field of ['premiseCommits','head','blobs']) {
        for (const invalid of ['../escape', '/absolute', 'x;bad']) {
          let calls = 0
          const refused = await load(file)({...base, __transport:transport, ...spec.args, ledger:{...spec.args.ledger, [field]:invalid}}, async () => {calls++; return null}, tasks => Promise.all(tasks.map(fn => fn())), () => {}, () => {})
          assert.ok(refused.error && refused.error.includes(field), field + ': unsafe input accepted')
          assert.equal(calls, 0, field + ': validation must precede launch')
        }
      }
    }
    if (name === 'key_reachability') {
      const unavailable = await load(file)({...base, __transport: transport, ...spec.args}, async () => {throw Error('producer unavailable')}, tasks => Promise.all(tasks.map(fn => fn())), () => {}, () => {})
      assert.equal(unavailable.verification.status, 'unavailable')
      assert.ok(unavailable.rulings.every(row => row.verdict === 'UNCHECKED' && row.lens && row.index === 1))
    }
    const runFull = async failure => {
      const verdicts = {}
      let verifierCalls = 0
      const events = []
      const result = await load(file, source => process.env.CLOSE_CHECK_DELETE_APPLY ? source.replace('applyVerdicts(items, verification.verdicts, keyOf)', 'items') : source)({...base, __transport: transport, root: temp, ...spec.args}, async (prompt, opts) => {
        const record = value => {
          events.push({...liveStart, key: opts.label, agentId: opts.label, label: opts.label, phase: opts.phase, prompt})
          events.push({type: 'result', key: opts.label, agentId: opts.label, result: value})
          return value
        }
        if (opts.label.startsWith('verify-')) {
          verifierCalls++
          if (failure) throw Error('unavailable')
          if (name === 'review_fanout') {
            for (const [field, flag] of [['premiseCommits','premise-commits'], ['head','head'], ['blobs','blobs']]) {
              assert.ok(prompt.includes('--' + flag + " '" + spec.args.ledger[field] + "'"), 'review frozen ' + flag)
            }
          }
          const writes = (prompt.split('Run exactly this command with Bash: ')[0].split('in order:\n')[1] || '').split('\n').filter(Boolean)
          assert.ok(writes.length, name + ': missing launched seats write')
          for (const command of writes) {
            const write = spawnSync('bash', ['-c', command], {cwd:temp, encoding:'utf8'})
            assert.equal(write.status, 0, write.stderr)
          }
          const reportPath = prompt.match(/--out (\S+)/)[1]
          const seatsFile = path.join(temp, reportPath.replace(/\.json$/, '.seats.json'))
          const launched = JSON.parse(fs.readFileSync(seatsFile))
          const producers = events.filter(event => event.type === 'started' && !['Verify','Fold','Merge'].includes(event.phase))
          assert.deepEqual(Object.keys(launched).sort(), producers.map(event => event.label).sort(), name + ': seats must name launched producers')
          assert.ok(Object.values(launched).every(seat => seat.model === 'producer' && seat.effort === 'low'
            && ['tier','mandates','mustRead'].every(key => key in seat)), name + ': incomplete launched seats')
          const directory = path.join(temp, name, 'session/subagents/workflows/wf_fixture')
          fs.mkdirSync(directory, {recursive: true})
          events.push({...liveStart, key: opts.label, agentId: opts.label, label: opts.label, phase: 'Verify'})
          fs.writeFileSync(path.join(directory, 'journal.jsonl'), events.map(JSON.stringify).join('\n') + '\n')
          const out = path.join(temp, name + '-report.json')
          const mode = name === 'review_fanout' ? 'review' : 'claims'
          const argv = ['.claude/tools/post_check.py', mode, '--workflow-dir', directory, '--root', temp, '--out', out]
          if (mode === 'review') argv.push('--expect-total', '3')
          const done = spawnSync('python3', argv, {cwd: root, encoding: 'utf8'})
          // claims mode exits on its claims; review mode exits on its run outcome, here with no failed check
          const exitCode = mode === 'review' ? 0 : 1
          if (done.status !== exitCode) console.error(name + ': checker failed', done.stdout, done.stderr)
          assert.equal(done.status, exitCode, done.stdout + done.stderr)
          const report = JSON.parse(fs.readFileSync(out, 'utf8'))
          assert.equal(report.status, 'fail', JSON.stringify(report))
          if (process.env.DEBUG_CLOSE) console.error(name, JSON.stringify(report))
          return {status: report.status, counts: report.counts, verdicts: Object.fromEntries(Object.entries(report.verdicts).map(([key, row]) => [key, row.verdict])), report: prompt.match(/--out (\S+)/)[1]}
        }
        if (name === 'doc_architecture_audit' && opts.label !== 'da-structural') return record({findings: []})
        if (name === 'key_reachability') {
          const id = opts.label.split(':').pop()
          verdicts[opts.label + '#1'] = id === 'true' ? 'VERIFIED' : id === 'fake' ? 'UNVERIFIED' : 'UNCHECKED'
          return record({id, task: 'task', root: temp, ruling: 'KEEP', claim: id, cites: id === 'empty' ? [] : [{path: 'fixture', quote: id === 'true' ? 'true quote' : 'fake quote', lines: '1', exists: true, supports: 'yes'}], confidence: 'high', derivation: [], couldNotSatisfy: ''})
        }
        const rows = spec.reply.claims || spec.reply.findings || spec.reply.overlaps || [0,1,2]
        rows.forEach((row, index) => {
          const key = name === 'explore_fanout' ? 'lens#' + (index+1) : name === 'review_fanout' ? 'F' + (index+1) : opts.label + '#' + (index+1)
          verdicts[key] = ['VERIFIED','UNVERIFIED','UNCHECKED'][index]
        })
        if (name === 'worklog_relevance') verdicts['worklog:relevance#checked'] = 'VERIFIED'
        return record(spec.reply)
      }, tasks => Promise.all(tasks.map(fn => fn())), () => {}, () => {})
      assert.equal(verifierCalls, 1, name + ': close stage not called')
      assert.equal(result.verification.status, failure ? 'unavailable' : 'fail')
      const rows = spec.member ? result[spec.member] : result.job.match(/```claims\n([\s\S]*?)```/)[1].trim().split('\n').map(JSON.parse)
      assert.equal(rows.length, 3, JSON.stringify(result))
      if (name === 'worklog_relevance') assert.equal(result.checked.verdict, failure ? 'UNCHECKED' : 'VERIFIED')
      for (const row of rows) {
        const identity = row.subject || row.description || row.id || row.title
        assert.equal(row.verdict, failure ? 'UNCHECKED' : identity === 'true' ? 'VERIFIED' : identity === 'fake' ? 'UNVERIFIED' : 'UNCHECKED', name + ':' + identity)
      }
    }
    await runFull(false)
    await runFull(true)
    if (spec.member && spec.reply) spec.reply[spec.member].reverse()
    else if (name === 'key_reachability') spec.args.tasks.task.ids.reverse()
    else if (name === 'dispatch_chains') spec.reply = '```claims\n' + spec.reply.match(/```claims\n([\s\S]*?)```/)[1].trim().split('\n').reverse().join('\n') + '\n```'
    await runFull(false)
    if (name === 'explore_fanout') {
      spec.reply.claims.splice(1, 0, {claim: 'shape-invalid source'})
      await runFull(false)
    }
    console.log('PASS ' + name + ': real checker, both source orders, applied verification, failed verifier')
  }
})().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => fs.rmSync(temp, {recursive: true, force: true}))
