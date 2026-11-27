#!/usr/bin/env node
// Proof: with args.__repoRoot set, every path an engine hands an agent for its spill files and every
// close-check command carries the absolute root (and a leading `cd`); unset, the relative form is unchanged.
const fs = require('fs')
const path = require('path')
const engineRun = require('./_engine_run')

const WORKFLOWS = path.join(__dirname, '..', 'workflows')
const ROOT = 'C:/work/checkout'
const SPILL = '.claude/scratch/rr/spill'
const TRANSPORT = { name: 'host', ids: ['sonnet', 'opus'] }
const PROFILES = { verify: { agentType: 'lane-verify', tier: 'fanout' } }
let failed = 0
const check = (label, ok, detail) => {
  console.log((ok ? 'ok   ' : 'FAIL ') + label)
  if (!ok) { failed++; if (detail) console.log('     ' + detail) }
}

async function prompts(script, args) {
  const seen = []
  const agent = async (prompt) => { seen.push(prompt); return null }
  try {
    await engineRun.load(path.join(WORKFLOWS, script))(
      engineRun.withKinds({ __transport: TRANSPORT, checker: { model: 'sonnet', effort: 'medium' }, __toolProfiles: PROFILES, ...args }),
      agent, thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn))), () => {}, () => {})
  } catch (e) { seen.push('CRASH ' + String((e && e.stack) || e)) }
  return seen
}

const job = { label: 'a', promptPath: 'a.md', model: 'sonnet', effort: 'low', kind: 'author' }
const SPILL_ENGINES = {
  'dispatch.js': { jobs: [job] },
  'dispatch_chains.js': { chains: [{ name: 'lane', jobs: [job] }] },
  'review_fanout.js': { agents: [{ key: 'one', prompt: 'Review it.', model: 'sonnet', effort: 'low', kind: 'author', mustRead: [{ path: 'fixture', lines: 1 }] }] },
}
const CLOSE_ENGINES = ['dispatch', 'dispatch_chains', 'review_fanout', 'doc_architecture_audit', 'worklog_relevance']

;(async () => {
  for (const [script, shape] of Object.entries(SPILL_ENGINES)) {
    const rooted = await prompts(script, { ...shape, spillDir: SPILL, __repoRoot: ROOT })
    check(script + ' names its spill file under the absolute root',
      rooted.some(p => p.includes(ROOT + '/' + SPILL + '/')) && !rooted.some(p => p.startsWith('CRASH')), rooted.join('\n---\n').slice(0, 600))
    const bare = await prompts(script, { ...shape, spillDir: SPILL })
    check(script + ' keeps the relative spill path when __repoRoot is unset',
      bare.some(p => p.includes(SPILL + '/')) && !bare.some(p => p.includes(ROOT) || p.startsWith('CRASH')), bare.join('\n---\n').slice(0, 600))
  }

  for (const name of CLOSE_ENGINES) {
    const unit = engineRun.load(path.join(WORKFLOWS, name + '.js'), s => 'const A = args;\n'
      + s.match(/\/\/ CLOSE-CHECK-BEGIN([\s\S]*?)\/\/ CLOSE-CHECK-END/)[1]
      + '\nreturn closeCheck("claims", {items: [], keyOf: c => c.id, out: ".claude/scratch/rr/post_check.json", spillDir: args.spillDir})')
    for (const withRoot of [true, false]) {
      let command = ''
      await unit({ __transport: TRANSPORT, checker: { model: 'sonnet', effort: 'medium' }, __toolProfiles: PROFILES,
        spillDir: SPILL, ...(withRoot ? { __repoRoot: ROOT } : {}) },
      async (prompt) => { command = prompt.split('Run exactly this command with Bash: ')[1].split('\n')[0]; return null },
      thunks => Promise.all(thunks.map(fn => fn())), () => {}, () => {})
      if (withRoot) {
        check(name + ' close check runs from the root with absolute out and spill paths',
          command.startsWith("cd '" + ROOT + "' && python3 .claude/tools/post_check.py")
            && command.includes('--out ' + ROOT + '/.claude/scratch/rr/post_check-')
            && command.includes("--spill-dir '" + ROOT + '/' + SPILL + "'"), command)
      } else {
        check(name + ' close check keeps the relative command when __repoRoot is unset',
          command.startsWith('python3 .claude/tools/post_check.py') && !command.includes(ROOT)
            && command.includes('--out .claude/scratch/rr/post_check-') && command.includes("--spill-dir '" + SPILL + "'"), command)
      }
    }
  }
  process.exit(failed ? 1 : 0)
})()
