#!/usr/bin/env node
// Proof that schema_parity.js runs in any project and derives its engine set from disk: each workflow
// that calls closeCheck( or carries a CLOSE-CHECK / EVIDENCE-SSOT block must carry both blocks byte-equal
// to dispatch.js. Runs the real script inside a sandbox copy of the template's .claude tree.
// Run: node .claude/tests/schema_parity_test.js
const assert = require('node:assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')

const TEMPLATE_CLAUDE = path.join(__dirname, '..')
const SCRIPT = path.join(TEMPLATE_CLAUDE, 'scripts', 'schema_parity.js')
const WORKFLOWS = path.join(TEMPLATE_CLAUDE, 'workflows')
const EVIDENCE = path.join(TEMPLATE_CLAUDE, 'schemas', 'evidence.json')

// Copies the script, workflows and evidence schema into a temp root, applies `mutate`, runs the copy.
function sandboxRun(mutate) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'schema-parity-'))
  try {
    const claude = path.join(root, '.claude')
    fs.mkdirSync(path.join(claude, 'scripts'), { recursive: true })
    fs.mkdirSync(path.join(claude, 'schemas'), { recursive: true })
    fs.copyFileSync(SCRIPT, path.join(claude, 'scripts', 'schema_parity.js'))
    fs.cpSync(WORKFLOWS, path.join(claude, 'workflows'), { recursive: true })
    fs.copyFileSync(EVIDENCE, path.join(claude, 'schemas', 'evidence.json'))
    if (mutate) mutate(claude)
    return spawnSync(process.execPath, [path.join(claude, 'scripts', 'schema_parity.js')], { encoding: 'utf8' })
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}

const engineNames = fs.readdirSync(WORKFLOWS)
  .filter(f => f.endsWith('.js') && f !== 'dispatch.js')
  .filter(f => {
    const src = fs.readFileSync(path.join(WORKFLOWS, f), 'utf8')
    return src.includes('closeCheck(') || src.includes('// CLOSE-CHECK-BEGIN') || src.includes('// EVIDENCE-SSOT-BEGIN')
  })
  .map(f => f.replace(/\.js$/, ''))

// (a) the real engine set: exits 0 and the OK line counts exactly the engines found on disk.
{
  const r = sandboxRun()
  assert.equal(r.status, 0, 'real set exit: ' + r.status + ' stderr=' + r.stderr.slice(0, 400))
  const ok = r.stdout.match(/^OK {3}CLOSE-CHECK and EVIDENCE-SSOT parity \((\d+) engines\)$/m)
  assert.ok(ok, 'OK line missing: ' + r.stdout)
  assert.equal(Number(ok[1]), engineNames.length, 'engine count')
  console.log('ok   (a) real engine set passes and the OK line counts ' + engineNames.length + ' engines')
}

// (b) an engine that calls closeCheck( without the blocks is DIVERGED, never skipped.
{
  const r = sandboxRun(claude => {
    fs.writeFileSync(path.join(claude, 'workflows', 'rogue_engine.js'),
      '// a planted engine with no verifier blocks\nasync function run() { return closeCheck("claims", {}) }\n')
  })
  assert.equal(r.status, 1, 'planted engine exit: ' + r.status)
  assert.match(r.stderr, /DIVERGED CLOSE-CHECK: rogue_engine/)
  console.log('ok   (b) a planted engine calling closeCheck( without the blocks is DIVERGED')
}

// (c) an engine whose CLOSE-CHECK block drifts from dispatch.js's is DIVERGED.
{
  const r = sandboxRun(claude => {
    const file = path.join(claude, 'workflows', 'review_fanout.js')
    const src = fs.readFileSync(file, 'utf8')
    assert.ok(src.includes('// CLOSE-CHECK-BEGIN\n'), 'fixture anchor in review_fanout.js')
    fs.writeFileSync(file, src.replace('// CLOSE-CHECK-BEGIN\n', '// CLOSE-CHECK-BEGIN\n// drifted\n'))
  })
  assert.equal(r.status, 1, 'drifted block exit: ' + r.status)
  assert.match(r.stderr, /DIVERGED CLOSE-CHECK: review_fanout/)
  console.log('ok   (c) an engine with an edited CLOSE-CHECK block is DIVERGED')
}
