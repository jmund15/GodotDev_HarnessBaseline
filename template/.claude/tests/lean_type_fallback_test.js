#!/usr/bin/env node
// Prove every engine's verifier spawn retries once as general-purpose when its generated agent type is
// not loaded in the session, and still throws on any other error. The helper is copied per engine.
const fs = require('fs'), path = require('path')
const ENGINES = ['dispatch', 'dispatch_chains', 'doc_architecture_audit', 'explore_fanout', 'review_fanout', 'worklog_relevance']
let pass = 0, fail = 0
const ck = (n, c, d) => { if (c) { console.log('  PASS  ' + n); pass++ } else { console.log('  FAIL  ' + n + (d ? '\n        ' + d : '')); fail++ } }
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
;(async () => {
console.log('verifier generated-type fallback - per engine')
for (const name of ENGINES) {
  const file = path.join(__dirname, '..', 'workflows', name + '.js')
  if (!fs.existsSync(file)) { console.log('skip ' + name + ': its layer is not installed'); continue }
  const src = fs.readFileSync(file, 'utf8')
  const a = src.indexOf('const verifierAgent = ')
  const b = a < 0 ? -1 : src.indexOf('\n})\n', a)
  ck(name + ': defines verifierAgent', a >= 0 && b > a)
  ck(name + ': the Verify spawn goes through verifierAgent', /verification = await verifierAgent\(/.test(src) && !/verification = await agent\(/.test(src))
  if (a < 0 || b < 0) continue
  const block = src.slice(a, b + 3)
  const run = async (agentImpl) => {
    const calls = [], logs = []
    const agent = (p, o) => { calls.push(o.agentType); return agentImpl(calls.length) }
    const fn = new AsyncFunction('agent', 'log', block + '\nreturn verifierAgent("p", { label: "L", agentType: "lane-verify" })')
    let ret, err
    try { ret = await fn(agent, (s) => logs.push(s)) } catch (e) { err = e }
    return { calls, logs, ret, err }
  }
  let r = await run(async (n) => { if (n === 1) throw new Error("agent type 'lane-verify' not found"); return 'ok' })
  ck(name + ': not-found retries once as general-purpose', JSON.stringify(r.calls) === '["lane-verify","general-purpose"]' && r.ret === 'ok', JSON.stringify(r))
  ck(name + ': the fallback is logged', r.logs.some(l => /^FALLBACK L lane-verify/.test(l)), JSON.stringify(r.logs))
  r = await run(async () => { throw new Error('quota exceeded') })
  ck(name + ': any other error is rethrown without a retry', r.calls.length === 1 && r.err && /quota/.test(r.err.message), JSON.stringify(r.calls))
  r = await run(async () => 'fine')
  ck(name + ': a loaded type spawns once', JSON.stringify(r.calls) === '["lane-verify"]')
}
console.log('\n' + pass + ' pass, ' + fail + ' fail')
process.exit(fail ? 1 : 0)
})()
