export const meta = {
  name: 'dispatch-chains',
  description: 'Pinned dispatch for work that is sequential WITHIN a lane and parallel ACROSS lanes. Each chain owns one isolated resource (a worktree, a subsystem) and runs its jobs in order; chains run concurrently. The sibling of dispatch.js for fan-outs whose jobs share a mutable working tree.',
  phases: [{ title: 'Chains', detail: 'one concurrent lane per chain; jobs sequential inside a lane' }],
}

// Platform contract mirrors dispatch.js: args may arrive as a JSON string or an already-parsed
// value, and an uncaught SyntaxError would name neither this workflow nor the expected shape.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'dispatch-chains: args must be JSON-serializable {chains: [{name, jobs: [{label, promptPath, model, effort}, ...]}, ...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
const chains = Array.isArray(A.chains) ? A.chains : []

// Endpoint vocabulary — hooks/workflow_provider_guard.py injects __transport off-Anthropic.
// Presence means provider mode: incomplete registry data fails before dispatch rather than reopening
// the Anthropic vocabulary.
const PIN = (m) => m
const EFF = (e) => e
const HAS_TRANSPORT = Object.prototype.hasOwnProperty.call(A, '__transport')
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
const transportValid = !HAS_TRANSPORT || (
  TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && validStringList(TRANSPORT.efforts)
  && typeof TRANSPORT.default === 'string' && TRANSPORT.default.trim()
  && TRANSPORT.ids.includes(TRANSPORT.default)
)
if (!transportValid) {
  return { error: 'dispatch-chains: args.__transport needs non-empty string arrays `ids` and `efforts`, plus a non-empty `default` contained in `ids`.' }
}

// Models stay strict. Effort may omit to the bounded medium floor; a present invalid pin fails.
const VALID_MODELS = HAS_TRANSPORT ? TRANSPORT.ids : ['opus', 'sonnet', 'haiku', 'fable']
const VALID_EFFORTS = HAS_TRANSPORT ? TRANSPORT.efforts : ['low', 'medium', 'high', 'xhigh']
const DEFAULT_EFFORT = VALID_EFFORTS.includes('medium') ? 'medium' : VALID_EFFORTS[0]
const hasEffort = (j) => Object.prototype.hasOwnProperty.call(j, 'effort') && j.effort !== undefined
const effortOf = (j) => hasEffort(j) ? j.effort : DEFAULT_EFFORT
const VALID_AGENT_TYPES = ['Explore', 'Plan', 'general-purpose']

const allJobs = chains.flatMap(c => (c && Array.isArray(c.jobs)) ? c.jobs.map(j => [c, j]) : [])
const badChain = chains.filter(c => !c || !c.name || !Array.isArray(c.jobs) || c.jobs.length === 0)
const badJob = allJobs.filter(([, j]) => !j || !j.label || !j.promptPath || !VALID_MODELS.includes(j.model)
  || (hasEffort(j) && !VALID_EFFORTS.includes(j.effort)) || !VALID_AGENT_TYPES.includes(j.agentType))
if (chains.length === 0 || badChain.length > 0 || badJob.length > 0) {
  return {
    error: 'Every chain needs {name, jobs:[...]} and every job needs {label, promptPath, model, agentType}; agentType must be in ['
      + VALID_AGENT_TYPES.join('|') + ']. Model must be in [' + VALID_MODELS.join('|') + '] and optional effort in [' + VALID_EFFORTS.join('|')
      + ']. Write each prompt to a file and pass its path — never inline large prompts into args.',
    badChains: badChain.map(c => (c && c.name) || '(unnamed)'),
    badJobs: badJob.map(([, j]) => (j && j.label) || '(unlabeled)'),
  }
}

// Labels are the progress-tree identity and (with spillDir) the deliverable filename. A collision
// across chains is silent data loss, so reject it up front rather than overwriting.
const seenLabel = {}
const dupLabel = allJobs.filter(([, j]) => { const d = !!seenLabel[j.label]; seenLabel[j.label] = true; return d })
if (dupLabel.length > 0) {
  return { error: 'Job labels must be unique across ALL chains. Duplicates: ' + dupLabel.map(([, j]) => j.label).join(', ') }
}

log('CHAINS ' + JSON.stringify(Object.fromEntries(chains.map(c => [c.name, c.jobs.length]))))
log('PINS ' + JSON.stringify(Object.fromEntries(allJobs.map(([, j]) => [j.label, PIN(j.model) + '/' + EFF(effortOf(j)) + (j.agentType ? '/' + j.agentType : '')]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// The concurrency contract this engine exists to express. Contention scope is NOT one boolean:
// a machine-wide single-flight resource (the GdUnit4 named pipe, the csharp-ls wrapper) is unsafe
// whenever ANY other chain is live, while a per-lane resource (a worktree's obj/bin) is safe
// precisely because a chain runs its own jobs one at a time. `chain.exclusiveResource` names what
// this lane owns outright; `A.sharedSingleFlight` lists what no lane may touch.
const SHARED = Array.isArray(A.sharedSingleFlight) ? A.sharedSingleFlight : []
const guardFor = (c) => {
  const lines = ['', '=== ORCHESTRATION GUARD ===']
  if (chains.length > 1) {
    lines.push('Other agents are running CONCURRENTLY in other lanes. You are the ONLY agent in your own lane'
      + (c.exclusiveResource ? ' (' + c.exclusiveResource + '), which you own outright for the duration of your task.' : '.'))
  }
  if (SHARED.length > 0) {
    lines.push('MACHINE-WIDE single-flight — do NOT use these under any circumstances, even though you own your lane: '
      + SHARED.join('; ') + '. If your brief mandates one, STOP and report that it needs a serialized dispatch.')
  }
  return lines.join('\n')
}

// Delegate rails — ONE home (.claude/guards/<shape>.md); never inline a copy here.
const VALID_SHAPES = ['any', 'survey', 'review', 'author']
// Guard tier per RECEIVING model is registry data (`railTier`), injected as `args.__rails` by
// hooks/workflow_provider_guard.py because a Workflow script cannot read files. A model the map
// does not name, or a call the hook did not rewrite, reads `detailed`: the fail-safe direction.
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
const shapeOf = (j) => VALID_SHAPES.includes(j.shape) ? j.shape : 'any'
const tierOf = (j) => RAILS[j.model] || 'detailed'
const guardRef = (j) => {
  const tier = tierOf(j)
  return ['', '=== DELEGATE RAILS ===',
    'Read .claude/guards/' + shapeOf(j) + '.md with the Read tool and follow its `## ' + tier
    + '` section. Read ONLY that section — the other tiers are for other models.'].join('\n')
}

// Return-path spill (optional), identical contract to dispatch.js: bound what lands in orchestrator
// context, and let the orchestrator Read only the deliverables it acts on.
const SPILL_DIR = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan']
const spills = (j) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(j.agentType)
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'
const spillContract = (j) => spills(j) ? ['',
  '=== RETURN-PATH CONTRACT (overrides any "return the full result" wording in your brief) ===',
  'Write your FULL deliverable to ' + spillPath(j.label) + ' using the Write tool. That file is yours alone.',
  'Then return ONLY: (a) a digest of at most ' + DIGEST_WORDS + ' words covering what the caller must decide or act on, (b) one line `couldNotSatisfy:` naming every unmet item with what stopped it (denial, missing input, out-of-lane file) and the exact intended change, or `couldNotSatisfy: none`, and (c) a final line exactly `FULL: ' + spillPath(j.label) + '`.',
  'Do NOT restate the full deliverable in your final message. If you could not write the file, say so in the digest instead of pasting the content.',
].join('\n') : ''

const INLINE_LABELS = SPILL_DIR ? allJobs.map(([, j]) => j).filter(j => !spills(j)).map(j => j.label) : []
if (SPILL_DIR) {
  const seenPath = {}
  const collided = allJobs.map(([, j]) => j).filter(spills).filter(j => { const p = spillPath(j.label).toLowerCase(); const d = !!seenPath[p]; seenPath[p] = true; return d })
  if (collided.length > 0) {
    return { error: 'args.spillDir is set, but these labels collide after filename sanitization: ' + collided.map(j => j.label).join(', ') }
  }
  log('SPILL-DIR ' + SPILL_DIR + ' (digest cap ' + DIGEST_WORDS + ' words/job)')
  if (INLINE_LABELS.length > 0) {
    log('SPILL-EXEMPT (' + NO_WRITE_AGENT_TYPES.join('/') + ' have no Write tool; these return in full INLINE and cost orchestrator context): ' + INLINE_LABELS.join(', '))
  }
}

// A later job in a chain runs against the tree its predecessors already changed, which is the whole
// point of chaining — so tell it that, and name the predecessors it must not silently contradict.
const handoff = (c, idx) => idx === 0 ? '' : ['',
  '=== CHAIN POSITION ===',
  'You are step ' + (idx + 1) + ' of ' + c.jobs.length + ' in lane `' + c.name + '`. Earlier steps in this lane ('
  + c.jobs.slice(0, idx).map(p => '`' + p.label + '`').join(', ')
  + ') have ALREADY edited this working tree and their changes are present. Read the current file bytes rather than assuming the pre-chain state, and if a change of yours would revert or contradict an earlier step, report the conflict instead of silently overwriting it.',
].join('\n')

phase('Chains')
const results = await parallel(chains.map(c => async () => {
  const out = []
  for (let i = 0; i < c.jobs.length; i++) {
    const j = c.jobs[i]
    const ctx = A.contextPath
      ? 'SHARED CONTEXT for this dispatch is at: ' + A.contextPath + ' — read it with the Read tool FIRST (retry once if the read fails).\n\n'
      : ''
    const prompt = ctx
      + 'Your full task brief is at: ' + j.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails). Your final message is the deliverable: return the result the brief asks for, self-contained, no meta-commentary.'
      + handoff(c, i)
      + guardFor(c)
      + guardRef(j)
      + spillContract(j)
    const opts = { label: j.label, phase: 'Chains', model: PIN(j.model), effort: EFF(effortOf(j)), agentType: j.agentType }
    let r
    try {
      r = await agent(prompt, opts)
    } catch (e) {
      out.push([j.label, {
        error: 'dispatch-chains: agent rejected (' + ((e && e.message) || String(e)) + '); remaining jobs in lane `' + c.name + '` were not run',
      }])
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length
        + ' (rejected result from ' + j.label + ')')
      break
    }
    if (r == null) {
      out.push([j.label, {
        error: 'dispatch-chains: agent returned no result; remaining jobs in lane `' + c.name + '` were not run',
      }])
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length
        + ' (no result from ' + j.label + ')')
      break
    }
    out.push([j.label, r])
    log('lane ' + c.name + ': ' + (i + 1) + '/' + c.jobs.length + ' done (' + j.label + ')')
  }
  return out
}))

const out = Object.fromEntries(results.filter(Boolean).flat().filter(Boolean))
return SPILL_DIR
  ? Object.assign({ spillDir: SPILL_DIR, digests: out }, INLINE_LABELS.length > 0 ? { inlineLabels: INLINE_LABELS } : {})
  : out
