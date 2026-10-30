#!/usr/bin/env node
// Proof: dispatch.js and dispatch_chains.js accept a spillDir only when it is repository-relative
// and inside .claude/scratch/ (the rule review_fanout.js and explore_fanout.js already enforce), and
// dispatch.js's single-flight guard renders exactly the shared source .claude/guards/single_flight.json
// that tools/sidecar_fanout.py writes as a `-C` file.
const fs = require('fs')
const path = require('path')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const HOST = { name: 'host', ids: ['model-a', 'model-b'], default: 'model-b' }

const body = (name) => fs.readFileSync(path.join(__dirname, '..', 'workflows', name), 'utf8')
  .replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')

let passed = 0
let failed = 0
const check = (label, ok, detail) => {
  console.log((ok ? 'ok   ' : 'FAIL ') + label)
  if (ok) passed++
  else { failed++; if (detail) console.log('     ' + detail) }
}

async function run(script, args) {
  const calls = []
  const prompts = []
  const logs = []
  const agent = async (prompt, opts) => { prompts.push(prompt); calls.push(opts); return 'done' }
  const parallel = async thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn)))
  let result
  try {
    result = await new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', body(script))(
      { __transport: HOST, ...args }, agent, parallel, () => {}, line => logs.push(String(line)))
  } catch (e) {
    result = { crash: String((e && e.stack) || e) }
  }
  return { result, calls, prompts, logs }
}

const job = (label, agentType) => ({ label, promptPath: label + '.md', model: 'model-a', effort: 'low', agentType: agentType || 'general-purpose' })
const argsFor = (script, spillDir, jobs) => script === 'dispatch.js'
  ? { spillDir, jobs }
  : { spillDir, chains: [{ name: 'lane', jobs }] }

;(async () => {
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const refused = [
      ['absolute POSIX path', '/tmp/spill'],
      ['absolute Windows path under scratch', 'C:/repo/.claude/scratch/spill'],
      ['repo-relative path outside scratch', '.claude/plans/spill'],
      ['scratch prefix without a separator', '.claude/scratchpad'],
      ['.. escape from scratch', '.claude/scratch/../plans'],
    ]
    for (const [name, dir] of refused) {
      const r = await run(script, argsFor(script, dir, [job('a')]))
      check(script + ' refuses ' + name,
        !r.result.crash && typeof r.result.error === 'string' && r.result.error.includes('.claude/scratch/')
          && r.calls.length === 0,
        JSON.stringify(r.result))
    }
    const accepted = [
      ['scratch-relative forward slashes', '.claude/scratch/pr_pipeline/spill_pr1'],
      ['scratch-relative backslashes', '.claude\\scratch\\x'],
      ['scratch-relative trailing slash', '.claude/scratch/x/'],
    ]
    for (const [name, dir] of accepted) {
      const r = await run(script, argsFor(script, dir, [job('a')]))
      check(script + ' accepts ' + name, !r.result.crash && !r.result.error && r.calls.length === 1,
        JSON.stringify(r.result))
    }
    const none = await run(script, argsFor(script, undefined, [job('a')]))
    check(script + ' runs with no spillDir', !none.result.crash && !none.result.error && none.calls.length === 1,
      JSON.stringify(none.result))
  }

  // One shared source for the single-flight guard text.
  const src = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'guards', 'single_flight.json'), 'utf8'))
  const render = (shell) => [src.header, src.rule + (shell ? src.withShell : src.noShell) + src.stop].join('\n')
  const two = await run('dispatch.js', { jobs: [job('author'), job('survey', 'lane-survey')] })
  check('dispatch.js two-job run renders the shell variant from the shared source',
    !two.result.crash && two.prompts[0] && two.prompts[0].includes('\n' + render(true) + '\n'), two.prompts[0])
  check('dispatch.js two-job run renders the no-shell variant from the shared source',
    !two.result.crash && two.prompts[1] && two.prompts[1].includes('\n' + render(false) + '\n'), two.prompts[1])
  const one = await run('dispatch.js', { jobs: [job('solo')] })
  check('dispatch.js single job carries no guard', !one.result.crash && one.prompts[0] && !one.prompts[0].includes(src.header), one.prompts[0])

  // A writing job names the units its brief or plan did not state (orchestration §4 `choices`), so
  // review can route them as design no reviewer saw; a job that cannot write is never asked.
  const asksChoices = (prompt) => /```choices/.test(prompt) && /"unit"/.test(prompt) && /"why"/.test(prompt)
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const r = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer'), job('reader', 'lane-survey')]))
    check(script + ' asks a writing job for its choices block', !r.result.crash && asksChoices(r.prompts[0] || ''), r.prompts[0])
    check(script + ' never asks a no-write agent for choices', !r.result.crash && r.prompts[1] && !asksChoices(r.prompts[1]), r.prompts[1])
  }
  // A job whose profile cannot write returns inline; the run names the tool that lands it at <spillDir>/<label>.md.
  const exempt = await run('dispatch.js', argsFor('dispatch.js', '.claude/scratch/spill', [job('writer'), job('reader', 'lane-survey')]))
  check('dispatch.js hands a no-write job to dispatch_result.py',
    !exempt.result.crash && exempt.logs.some(line => line.includes('SPILL-EXEMPT') && line.includes('dispatch_result.py')), exempt.logs.join(' | '))
  // The result leads with that recovery route: a truncated result preview still shows it.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const r = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer'), job('reader', 'lane-survey')]))
    const first = r.result && !r.result.crash ? Object.keys(r.result)[0] : ''
    check(script + ' result leads with the inline recovery route',
      first === 'inlineRecover' && String(r.result.inlineRecover).includes('dispatch_result.py')
        && String(r.result.inlineRecover).includes('reader'), JSON.stringify(r.result).slice(0, 300))
    const all = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer')]))
    check(script + ' carries no recovery route when every job spills',
      !all.result.crash && !('inlineRecover' in all.result), JSON.stringify(all.result).slice(0, 300))
  }
  const ro = await run('dispatch.js', { jobs: [Object.assign(job('ro', 'lane-survey'), { readOnly: true })] })
  check('dispatch.js never asks a readOnly job for choices', !ro.result.crash && ro.prompts[0] && !asksChoices(ro.prompts[0]), ro.prompts[0])
  // Read-only by shape alone: the type is outside NO_WRITE_AGENT_TYPES, so only readOnlyOf withholds the choices block.
  const byShape = await run('dispatch.js', {
    __toolProfileDefaults: { survey: { agentType: 'lane-survey', builtins: ['Read'] } },
    jobs: [Object.assign(job('shaped'), { shape: 'survey' })],
  })
  check('dispatch.js never asks a shape-read-only job of a writable type for choices',
    !byShape.result.crash && byShape.calls[0] && byShape.calls[0].agentType === 'general-purpose'
      && byShape.prompts[0] && !asksChoices(byShape.prompts[0]), byShape.prompts[0] || JSON.stringify(byShape.result))
  // `readOnly: true` is kept by tool set: a job that would run with write tools is refused before any agent starts.
  const broken = await run('dispatch.js', { jobs: [Object.assign(job('ro'), { readOnly: true })] })
  check('dispatch.js refuses a readOnly job that would run with write tools',
    !broken.result.crash && typeof broken.result.error === 'string' && broken.result.error.includes('readOnly:true')
      && broken.calls.length === 0, JSON.stringify(broken.result))

  // A job dispatched from a linked worktree reports under the project root, not the worktree.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const spill = { ...argsFor(script, '.claude/scratch/spill', [job('writer')]) }
    const from = async root => run(script, { ...spill, __repoRoot: root })
    const wt = await from('C:/proj/.claude/worktrees/wt-a')
    check(script + ' roots the return path at the project root when run in a worktree',
      !wt.result.crash && wt.prompts[0] && wt.prompts[0].includes('C:/proj/.claude/scratch/spill/writer.md')
        && !wt.prompts[0].includes('worktrees'), wt.prompts[0] || JSON.stringify(wt.result))
    const main = await from('C:/proj')
    check(script + ' keeps the checkout root for a main checkout',
      !main.result.crash && main.prompts[0] && main.prompts[0].includes('C:/proj/.claude/scratch/spill/writer.md'), main.prompts[0])
  }

  console.log('dispatch spill root: ' + passed + ' passed, ' + failed + ' failed')
  process.exit(failed ? 1 : 0)
})().catch(err => { console.error(err); process.exit(1) })
