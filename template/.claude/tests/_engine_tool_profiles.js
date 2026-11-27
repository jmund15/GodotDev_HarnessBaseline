// Shared scaffolding for the engine tool-profile proofs: the host transport pin, the profile rows
// hooks/workflow_provider_guard.py resolves, the engine runner over a stubbed agent(), and the
// per-engine assertion matrix. A proof of this project's own engine requires this and adds its row.
const path = require('path')
const engineRun = require('./_engine_run')

// The vocabulary the provider guard injects on every Workflow call; the engine holds none of its own.
const HOST_TRANSPORT = { name: 'host', ids: ['opus', 'sonnet', 'haiku', 'fable'], efforts: ['low', 'medium', 'high', 'xhigh'] }
// The test pins the self-started verifier and merge steps through args.checker, the default critics through args.critic
// and the fixtures' validator through args.validator; the engine never picks them.
const HOST_CHECKER = { model: 'sonnet', effort: 'medium' }
// The orchestrator pins every declared job; the engine takes no default. Fixtures here that omit a pin receive this host pin.
const pinJobs = (args) => {
  const pin = (j) => (j && typeof j === 'object') ? { ...HOST_CHECKER, ...j } : j
  const out = { ...args }
  for (const key of ['jobs', 'lenses', 'agents', 'generators', 'critics']) if (Array.isArray(out[key])) out[key] = out[key].map(pin)
  if (Array.isArray(out.chains)) out.chains = out.chains.map(c => ({ ...c, jobs: Array.isArray(c.jobs) ? c.jobs.map(pin) : c.jobs }))
  return out
}
const withHost = (args) => Object.prototype.hasOwnProperty.call(args || {}, '__transport') ? args : { ...pinJobs(args), checker: (args && args.checker) || HOST_CHECKER, critic: (args && args.critic) || HOST_CHECKER, validator: (args && args.validator) || HOST_CHECKER, __transport: HOST_TRANSPORT }

// The kind rows hooks/workflow_provider_guard.py resolves from guards/tool_profiles.json into args.__toolProfiles
// (per job label) and args.__kindProfile (the call-level kind of the five single-kind engines).
const ROW = { add: [], remove: [], overrideMode: 'shape-default', enforcement: 'inherited', effectiveMcp: 'unknown/inherited',
  mcpServers: ['semantic-search', 'ai-worker'], disclosure: 'full' }
const P = {
  scout: { ...ROW, kind: 'scout', builtins: ['Read', 'Glob', 'Grep', 'Bash'], agentType: 'lane-survey', tier: 'fanout', guard: 'survey', disclosure: 'full' },
  lens: { ...ROW, kind: 'lens', builtins: ['Read', 'Glob', 'Grep', 'Bash'], agentType: 'lane-survey', tier: 'fanout', guard: 'survey', disclosure: 'full' },
  reviewer: { ...ROW, kind: 'reviewer', builtins: ['Read', 'Glob', 'Grep', 'Bash'], agentType: 'lane-review', tier: 'executor', guard: 'review' },
  author: { ...ROW, kind: 'author', builtins: ['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash'], agentType: 'lane-author', tier: 'executor', guard: 'author' },
  judge: { ...ROW, kind: 'judge', builtins: ['Read', 'Glob', 'Grep', 'Bash'], agentType: 'lane-review', tier: 'executor', guard: 'common' },
}
// The call-level kind of the engines that take one `kind` for every child (hook-resolved into __kindProfile).
const KINDED = { kind: 'reviewer', __kindProfile: P.reviewer }
const PROFILED = { 'review:merge': P.reviewer, 'review:fold': P.author, verify: { agentType: 'lane-verify', tier: 'fanout' } }

// Each engine's builder takes the kind and returns its args as the hook would have left them. `labels` are the
// children the engine spawns; `perJob` marks the engines that read a kind per job entry.
const JOB = { promptPath: 'p.md', model: 'fable', effort: 'medium' }

const FIXTURE_RESULT = () => ({ findings: [], claims: [], gaps: [], checked: { toolsUsed: ['Read:x'], stoppedAt: 'done', basis: 'fixture' } })

async function run(file, args, reject, respond, opts) {
  const script = path.join(__dirname, '../workflows', file)
  const spawned = []
  const calls = []
  const phases = []
  const logs = []
  const agent = async (prompt, opts) => {
    spawned.push((opts && opts.label) || null)
    calls.push({ label: opts && opts.label, agentType: opts && opts.agentType,
      model: opts && opts.model, effort: opts && opts.effort, phase: opts && opts.phase, prompt })
    const why = reject && reject(opts || {})
    if (why) throw new Error(why)
    return respond ? respond(opts || {}) : FIXTURE_RESULT()
  }
  // The runtime's parallel() resolves a thrown job to null rather than rejecting the whole batch.
  const parallel = async (thunks) => Promise.all(thunks.map((f) => f().catch(() => null)))
  const pipeline = async (items, ...stages) => Promise.all(items.map(async (it) => {
    let v = it
    for (const s of stages) v = await s(v, it)
    return v
  }))
  const result = await engineRun.load(script, src => 'const pipeline = ' + pipeline.toString() + '\n' + src)(
    (opts && opts.raw) ? args : withHost(args), agent, parallel,
    (name) => phases.push(name), (m) => logs.push(String(m)))
    .catch((e) => (opts && opts.loose) ? { error: 'threw: ' + ((e && e.message) || e) } : Promise.reject(e))
  return { spawned, calls, phases, logs, result }
}

function toolProfilesLine(logs) {
  const line = logs.find((m) => m.startsWith('TOOL-PROFILES '))
  if (!line) return null
  try {
    return JSON.parse(line.slice('TOOL-PROFILES '.length))
  } catch (e) {
    return { __unparseable: line }
  }
}

const errOf = (r) => String((r.result && r.result.error) || '')

// A kindless job (or call) is refused before any agent() call; a reviewer runs as lane-review and an
// author as lane-author; every declared label is spawned; a reviewer logs a TOOL-PROFILES entry that
// covers each spawned label and records the kind row as agent-type with the type it ran as.
async function checkEngineMatrix(file, e, check) {
  const name = file.replace(/\.js$/, '')

  const none = await run(file, e.build(undefined))
  check(name + ': a kindless ' + (e.perJob ? 'job' : 'call') + ' is refused before any agent() call',
    none.calls.length === 0 && /kind/.test(errOf(none)), errOf(none) || JSON.stringify(none.calls.map((c) => c.label)))

  for (const [kind, want] of [['reviewer', 'lane-review'], ['author', 'lane-author']]) {
    if (kind === 'author' && file === 'review_fanout.js') continue
    const r = await run(file, e.build(kind))
    const payload = toolProfilesLine(r.logs)
    check(name + ': a ' + kind + ' runs as ' + want,
      r.calls.length > 0 && r.calls.filter((c) => !/^verify-/.test(c.label)).every((c) => c.agentType === want), errOf(r) || JSON.stringify(r.calls.map((c) => [c.label, c.agentType])))
    check(name + ': a ' + kind + ' spawns every expected label',
      e.labels.every((l) => r.spawned.includes(l)), JSON.stringify(r.spawned))
    if (kind !== 'reviewer') continue
    check(name + ': logs a TOOL-PROFILES line', payload !== null && !payload.__unparseable, (r.logs || []).join(' | ').slice(0, 300))
    for (const label of r.spawned.filter((l) => l && !/^verify-/.test(l))) {
      const hit = payload && Object.keys(payload).some((k) => k === label || label.endsWith(':' + k))
      check(name + ': TOOL-PROFILES covers spawned label ' + label, !!hit, JSON.stringify(Object.keys(payload || {})))
    }
    const rec = Object.values(payload || {}).find((v) => v && v.kind === 'reviewer') || {}
    check(name + ': the entry records kind reviewer, agent-type enforcement and the type it ran as',
      rec.enforcement === 'agent-type' && rec.agentType === 'lane-review', JSON.stringify(rec))
  }
}

module.exports = { run, toolProfilesLine, checkEngineMatrix, P, KINDED, PROFILED, JOB }
