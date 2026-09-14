#!/usr/bin/env node
// Prove dispatch.js's NO-RETURN guard FIRES on a planted dead agent and stays silent on a clean run.
// The first version of this guard tested the [label, null] PAIR for truthiness and could never fire
// (2026-09-08); a guard nobody has seen fire is indistinguishable from one that cannot.
const fs = require('fs'), path = require('path')
const SRC = path.join(__dirname, '..', 'workflows', 'dispatch.js')
const src = fs.readFileSync(SRC, 'utf8')
const a = src.indexOf('const out = Object.fromEntries(results.filter(Boolean))')
if (a < 0) { console.error('CANNOT RUN: the return-assembly block was not found in dispatch.js'); process.exit(2) }
const block = src.slice(a)

let pass = 0, fail = 0
const ck = (n, c, d) => { if (c) { console.log('  PASS  ' + n); pass++ } else { console.log('  FAIL  ' + n + (d ? '\n        ' + d : '')); fail++ } }

// The block reads jobs/results/spills/spillPath/SPILL_DIR/INLINE_LABELS/log from the engine scope.
const run = ({ jobs, results, spillDir }) => {
  const logs = []
  const spillPath = (label) => spillDir + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'
  const spills = (j) => !!spillDir && !['Explore', 'Plan'].includes(j.agentType)
  const fn = new Function('jobs', 'results', 'spills', 'spillPath', 'SPILL_DIR', 'INLINE_LABELS', 'log', block)
  const ret = fn(jobs, results, spills, spillPath, spillDir || null, [], (s) => logs.push(s))
  return { ret, logs }
}
const J = (label, agentType) => ({ label, agentType: agentType || 'general-purpose' })

console.log('dispatch.js NO-RETURN guard - firing proof')

// clean run, inline contract
let r = run({ jobs: [J('a'), J('b')], results: [['a', 'ok-a'], ['b', 'ok-b']] })
ck('1a clean inline run carries no noReturn key', !('noReturn' in r.ret), JSON.stringify(r.ret))
ck('1b clean inline run returns the digests as the object', r.ret.a === 'ok-a' && r.ret.b === 'ok-b')
ck('1c nothing logged on a clean run', r.logs.length === 0, JSON.stringify(r.logs))

// PLANTED DEAD AGENT: the engine yields [label, null] for a stalled/dead agent — a truthy pair.
r = run({ jobs: [J('a'), J('dead')], results: [['a', 'ok-a'], ['dead', null]] })
ck('2a a null result FIRES the guard', !!r.ret.noReturn, JSON.stringify(r.ret))
ck('2b it names exactly the dead label', JSON.stringify(r.ret.noReturn && r.ret.noReturn.labels) === '["dead"]')
ck('2c the recovery route names /salvage_fanout with the label', /\/salvage_fanout <transcriptDir> dead/.test(r.ret.noReturn && r.ret.noReturn.recover))
ck('2d the live label is untouched beside it', r.ret.a === 'ok-a')
ck('2e NO-RETURN is logged for the narrator too', r.logs.some(l => /^NO-RETURN dead/.test(l)), JSON.stringify(r.logs))

// a label that never came back at all (filtered out entirely) is equally dead
r = run({ jobs: [J('a'), J('gone')], results: [['a', 'ok-a']] })
ck('3 a label missing from results is flagged', JSON.stringify(r.ret.noReturn && r.ret.noReturn.labels) === '["gone"]')

// spill contract: the route points at the spill file FIRST, and noReturn rides beside digests, never inside
r = run({ jobs: [J('a'), J('dead')], results: [['a', 'd-a'], ['dead', null]], spillDir: '/tmp/spill' })
ck('4a spill run: noReturn rides beside digests', !!r.ret.noReturn && !!r.ret.digests)
ck('4b spill run: digests carries no noReturn/__noReturn key', !('noReturn' in r.ret.digests) && !('__noReturn' in r.ret.digests))
ck('4c spill run: route names the spill file before salvage', /\/tmp\/spill\/dead\.md else \/salvage_fanout/.test(r.ret.noReturn.recover), r.ret.noReturn.recover)

// an Explore job has no spill file, so its route is salvage only
r = run({ jobs: [J('scout', 'Explore')], results: [['scout', null]], spillDir: '/tmp/spill' })
ck('5 a no-write agent type routes straight to salvage (no spill path)', /^scout -> \/salvage_fanout/.test(r.ret.noReturn.recover), r.ret.noReturn.recover)

console.log('\n' + pass + ' pass, ' + fail + ' fail')
process.exit(fail ? 1 : 0)
