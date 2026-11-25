// Engine helper blocks: schema_parity.js --write fills the fenced copies from scripts/engine_src/common.js,
// and its default check fails a hand-edited copy. Each case runs the real script on a planted temp root.
const assert = require('assert/strict')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')

const repo = path.resolve(__dirname, '..', '..')
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'schema_parity_blocks_'))
const root = path.join(tmp, 'root')
for (const rel of ['.claude/workflows', '.claude/scripts/engine_src', '.claude/schemas']) {
  if (fs.existsSync(path.join(repo, rel))) fs.cpSync(path.join(repo, rel), path.join(root, rel), { recursive: true })
}
fs.mkdirSync(path.join(root, '.claude/scripts'), { recursive: true })
fs.copyFileSync(path.join(repo, '.claude/scripts/schema_parity.js'), path.join(root, '.claude/scripts/schema_parity.js'))
const script = path.join(root, '.claude/scripts/schema_parity.js')
const dispatch = path.join(root, '.claude/workflows/dispatch.js')
const unfence = text => text.split('\n').filter(l => !/^\/\/ ENGINE-BLOCK-(BEGIN|END) /.test(l)).join('\n')
for (const file of fs.readdirSync(path.join(root, '.claude/workflows'))) {
  const abs = path.join(root, '.claude/workflows', file)
  if (file.endsWith('.js')) fs.writeFileSync(abs, unfence(fs.readFileSync(abs, 'utf8')))
}
const clean = fs.readFileSync(dispatch, 'utf8')

function run(...args) {
  const r = spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' })
  assert.ok(r.status === 0 || r.status === 1 || r.status === 2, 'CRASH: exit ' + r.status + ' ' + r.stderr)
  return r
}
function reset() { fs.writeFileSync(dispatch, clean) }

try {
  // Clean HEAD copy: fill the fences, then the default check passes.
  assert.equal(run('--write').status, 0, 'write on HEAD copy must succeed')
  assert.equal(run().status, 0, 'check on a freshly written copy must pass')
  const filled = fs.readFileSync(dispatch, 'utf8')
  assert.match(filled, /\/\/ ENGINE-BLOCK-BEGIN closeCheck/, 'closeCheck must be fenced in dispatch')
  // The fill only adds fence lines: stripping them gives the original bytes.
  const unfenced = filled.split('\n').filter(l => !/^\/\/ ENGINE-BLOCK-(BEGIN|END) /.test(l)).join('\n')
  assert.equal(unfenced, clean, 'write must change nothing but the fence lines')

  // Planted code change in one engine's copy: the check fails and names the engine and block.
  reset(); run('--write')
  fs.writeFileSync(dispatch, fs.readFileSync(dispatch, 'utf8').replace('const closeCheck = async (mode,', 'const closeCheck = async (mode, planted,'))
  const drift = run()
  assert.equal(drift.status, 1, 'a hand-edited closeCheck must fail the check')
  assert.match(drift.stderr, /dispatch\.js/, 'the failure must name the engine')
  assert.match(drift.stderr, /closeCheck/, 'the failure must name the block')

  // Comment-only change: not drift, the check passes.
  reset(); run('--write')
  fs.writeFileSync(dispatch, fs.readFileSync(dispatch, 'utf8').replace('const closeCheck = async', '// reworded\nconst closeCheck = async'))
  assert.equal(run().status, 0, 'a comment in a fenced block is not drift')

  // Fill restores the planted copy to the source.
  fs.writeFileSync(dispatch, fs.readFileSync(dispatch, 'utf8').replace('const closeCheck = async (mode,', 'const closeCheck = async (mode, planted,'))
  assert.equal(run('--write').status, 0)
  assert.equal(run().status, 0, 'after --write the planted copy must pass')
  assert.ok(!fs.readFileSync(dispatch, 'utf8').includes('planted'), 'write must remove the hand edit')

  // Outside workflow directory: a filled copy of a real engine must match; a hand-edited one must fail.
  reset(); run('--write')
  const outside = path.join(tmp, 'outside')
  fs.mkdirSync(outside)
  const outsideWf = path.join(outside, 'dispatch.js')
  const filledDispatch = fs.readFileSync(dispatch, 'utf8')
  fs.writeFileSync(outsideWf, filledDispatch)
  assert.equal(run(outside).status, 0, 'an outside directory with a matching block must pass')
  fs.writeFileSync(outsideWf, filledDispatch.replace('const closeCheck = async (mode,', 'const closeCheck = async (mode, planted,'))
  const outsideDrift = run(outside)
  assert.equal(outsideDrift.status, 1, 'a hand-edited block in an outside directory must fail the check')
  assert.match(outsideDrift.stderr, /dispatch\.js/, 'the failure must name the outside file')
  assert.match(outsideDrift.stderr, /closeCheck/, 'the failure must name the block')

  console.log('PASS schema parity blocks: fill is a no-op outside fences, hand-edited helper fails with its engine and block, comments are not drift')
} finally {
  fs.rmSync(tmp, { recursive: true, force: true })
}
