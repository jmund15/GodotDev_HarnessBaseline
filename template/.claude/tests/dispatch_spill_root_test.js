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

// The rows the provider hook resolves from a job's `kind` into args.__toolProfiles (by label).
const ROW = { add: [], remove: [], overrideMode: 'shape-default', enforcement: 'inherited', effectiveMcp: 'unknown/inherited',
  mcpServers: ['semantic-search', 'ai-worker'] }
const KINDS = {
  lens: { ...ROW, kind: 'lens', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-survey', tier: 'fanout', guard: 'survey' },
  reviewer: { ...ROW, kind: 'reviewer', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-review', tier: 'executor', guard: 'review' },
  author: { ...ROW, kind: 'author', builtins: ['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash'], agentType: 'lane-author', tier: 'executor', guard: 'author' },
}
const profilesOf = (args) => {
  const all = [...(args.jobs || []), ...(args.chains || []).flatMap(c => c.jobs)]
  return Object.fromEntries(all.filter(j => KINDS[j.kind]).map(j => [j.label, KINDS[j.kind]]))
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
      { __transport: HOST, __toolProfiles: profilesOf(args), ...args }, agent, parallel, () => {}, line => logs.push(String(line)))
  } catch (e) {
    result = { crash: String((e && e.stack) || e) }
  }
  return { result, calls, prompts, logs }
}

const job = (label, kind) => ({ label, promptPath: label + '.md', model: 'model-a', effort: 'low', kind: kind || 'author' })
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

  // A call carrying only `jobsFile` reaches the engine unexpanded when the PreToolUse chain did not run: say so.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const r = await run(script, { jobsFile: '.claude/scratch/x/native.args.json' })
    check(script + ' names the unexpanded jobsFile when it gets no jobs',
      !r.result.crash && typeof r.result.error === 'string' && r.result.error.includes('jobsFile')
        && r.result.error.includes('pre_workflow_dispatch') && r.calls.length === 0, JSON.stringify(r.result))
  }

  // One shared source for the single-flight guard text.
  const src = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'guards', 'single_flight.json'), 'utf8'))
  const render = (shell) => [src.header, src.rule + (shell ? src.withShell : src.noShell) + src.stop].join('\n')
  const two = await run('dispatch.js', { jobs: [job('author'), job('survey', 'lens')] })
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
    const r = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer'), job('reader', 'lens')]))
    check(script + ' asks a writing job for its choices block', !r.result.crash && asksChoices(r.prompts[0] || ''), r.prompts[0])
    check(script + ' never asks a no-write agent for choices', !r.result.crash && r.prompts[1] && !asksChoices(r.prompts[1]), r.prompts[1])
  }
  // A job whose profile cannot write returns inline; the run names the tool that lands it at <spillDir>/<label>.md.
  const exempt = await run('dispatch.js', argsFor('dispatch.js', '.claude/scratch/spill', [job('writer'), job('reader', 'lens')]))
  check('dispatch.js hands a no-write job to dispatch_result.py',
    !exempt.result.crash && exempt.logs.some(line => line.includes('SPILL-EXEMPT') && line.includes('dispatch_result.py')), exempt.logs.join(' | '))
  // The result leads with that recovery route: a truncated result preview still shows it.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const r = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer'), job('reader', 'lens')]))
    const first = r.result && !r.result.crash ? Object.keys(r.result)[0] : ''
    check(script + ' result leads with the inline recovery route',
      first === 'inlineRecover' && String(r.result.inlineRecover).includes('dispatch_result.py')
        && String(r.result.inlineRecover).includes('reader'), JSON.stringify(r.result).slice(0, 300))
    const all = await run(script, argsFor(script, '.claude/scratch/spill', [job('writer')]))
    check(script + ' carries no recovery route when every job spills',
      !all.result.crash && !('inlineRecover' in all.result), JSON.stringify(all.result).slice(0, 300))
  }
  // A kind that cannot write is never asked for choices, whatever else the job carries.
  const rv = await run('dispatch.js', { jobs: [job('rv', 'reviewer')] })
  check('dispatch.js never asks a reviewer for choices', !rv.result.crash && rv.prompts[0] && !asksChoices(rv.prompts[0]), rv.prompts[0])
  // A job names its tools by kind alone: a kindless job and a retired key are refused before any agent starts.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const kindless = Object.assign(job('k'), { kind: undefined })
    const none = await run(script, argsFor(script, undefined, [kindless]))
    check(script + ' refuses a kindless job before any agent starts',
      !none.result.crash && /kind/.test(String(none.result.error)) && none.calls.length === 0, JSON.stringify(none.result))
    const old = await run(script, argsFor(script, undefined, [Object.assign(job('ro', 'reviewer'), { readOnly: true })]))
    check(script + ' refuses readOnly naming kind',
      !old.result.crash && /readOnly/.test(String(old.result.error)) && /kind/.test(String(old.result.error)) && old.calls.length === 0, JSON.stringify(old.result))
    const typed = await run(script, argsFor(script, undefined, [Object.assign(job('ty', 'reviewer'), { agentType: 'general-purpose' })]))
    check(script + ' refuses agentType naming kind',
      !typed.result.crash && /agentType/.test(String(typed.result.error)) && typed.calls.length === 0, JSON.stringify(typed.result))
    const ran = await run(script, argsFor(script, undefined, [job('rv', 'reviewer'), job('au', 'author')]))
    check(script + ' runs a reviewer as lane-review and an author as lane-author',
      !ran.result.crash && ran.calls.length === 2 && ran.calls[0].agentType === 'lane-review' && ran.calls[1].agentType === 'lane-author',
      JSON.stringify(ran.calls))
  }

  // A job dispatched from a linked worktree reports under the project root, not the worktree.
  for (const script of ['dispatch.js', 'dispatch_chains.js']) {
    const spill = { ...argsFor(script, '.claude/scratch/spill', [job('writer')]) }
    const from = async root => run(script, { ...spill, __repoRoot: root })
    const wt = await from('C:/proj/.claude/worktrees/wt-a')
    check(script + ' roots the return path at the project root when run in a worktree',
      !wt.result.crash && wt.prompts[0] && wt.prompts[0].includes('C:/proj/.claude/scratch/spill/writer.md')
        && !wt.prompts[0].includes('worktrees/wt-a/.claude/scratch'), wt.prompts[0] || JSON.stringify(wt.result))
    const main = await from('C:/proj')
    check(script + ' keeps the checkout root for a main checkout',
      !main.result.crash && main.prompts[0] && main.prompts[0].includes('C:/proj/.claude/scratch/spill/writer.md'), main.prompts[0])
  }

  console.log('dispatch spill root: ' + passed + ' passed, ' + failed + ' failed')
  process.exit(failed ? 1 : 0)
})().catch(err => { console.error(err); process.exit(1) })
