#!/usr/bin/env node
// Prove the dispatch engines give every author job an amend line naming <promptPath>.amend.md, the one
// channel a parent has to correct a running delegate (Workflow agents have no inbox), and give a
// read-only shape none, so non-author runs pay nothing.
const fs = require('fs')
const path = require('path')
const os = require('os')
const { execFileSync } = require('child_process')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const HOST_TRANSPORT = { name: 'host', ids: ['m-big', 'm-small'] }
const AMEND = (p) => 'Amendments: the harness delivers ' + p + '.amend.md to you when your parent writes or changes it; a delivered amendment overrides the brief, and your report names what you applied.'

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

async function run(engine, args) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'workflows', engine), 'utf8')
  const body = src.replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '').replace('export const meta', 'const meta')
  const prompts = []
  const agent = async (prompt) => { prompts.push(prompt); return 'done' }
  const parallel = async thunks => Promise.all(thunks.map(fn => fn()))
  const result = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body)(
    require('./_engine_run').withKinds({ ...args, __transport: HOST_TRANSPORT }), agent, parallel, () => {}, () => {})
  return { result, prompts }
}

const jobs = [{ label: 'w', promptPath: 'w.md', kind: 'author' }, { label: 'r', promptPath: 'r.md', kind: 'lens' }]
  .map(j => ({ ...j, model: 'm-small', effort: 'low' }))

;(async () => {
  for (const [engine, args] of [['dispatch.js', { jobs }], ['dispatch_chains.js', { chains: jobs.map(j => ({ name: j.label, jobs: [j] })) }]]) {
    try {
      const { result, prompts } = await run(engine, args)
      check(engine + ' dispatches both jobs', !(result && result.error) && prompts.length === 2, JSON.stringify(result).slice(0, 300))
      check(engine + ' gives the author job its amend line', prompts.some(p => p.includes(AMEND('w.md'))))
      check(engine + ' gives the read-only job none', prompts.some(p => p.includes('r.md')) && !prompts.some(p => p.includes('r.md.amend.md')))
    } catch (e) {
      check(engine + ' runs under the stub', false, e.stack)
    }
    try {
      const { prompts } = await run(engine, { ...args, __repoRoot: '/work/parent' })
      check(engine + ' names the amend file by the absolute brief path', prompts.some(p => p.includes(AMEND('/work/parent/w.md'))), prompts.join('\n').slice(0, 300))
    } catch (e) {
      check(engine + ' runs with a repo root', false, e.stack)
    }
  }
  // The sidecar launchers read the brief through sidecar_common.sh's sc_read_prompt.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'amend-'))
  const brief = path.join(dir, 'b.md').replace(/\\/g, '/')
  fs.writeFileSync(brief, 'brief\n')
  const lib = path.join(__dirname, '..', 'scripts', 'lib', 'sidecar_common.sh').replace(/\\/g, '/')
  const read = (shape) => execFileSync('bash', ['-c', 'source "$1"; SC_PROMPT_FILE="$2"; SC_SHAPE="$3"; sc_read_prompt; printf %s "$SC_PROMPT"', '_', lib, brief, shape], { encoding: 'utf8' })
  try {
    const nativeBrief = process.platform === 'win32' ? execFileSync('cygpath', ['-w', brief], { encoding: 'utf8' }).trim() : brief
    check('a sidecar author brief ends with its amend line', read('author').includes(AMEND(nativeBrief)))
    check('a sidecar review brief carries none', !read('review').includes('.amend.md'))
  } catch (e) {
    check('sc_read_prompt runs', false, e.stack)
  }
  fs.rmSync(dir, { recursive: true, force: true })
  console.log('\n' + passed + ' pass, ' + failed + ' fail')
  process.exit(failed ? 1 : 0)
})()
