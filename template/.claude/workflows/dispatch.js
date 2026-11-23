export const meta = {
  name: 'dispatch',
  description: 'Run pinned jobs in parallel, or chains of jobs in parallel lanes with each lane in order; use instead of bare Agent-tool fan-outs',
  phases: [{ title: 'Dispatch', detail: 'jobs run concurrently; a chain runs its jobs in order inside its lane (the engine queues past the concurrency cap)' }],
}

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
// ENGINE-BLOCK-BEGIN A
let A
// ENGINE-BLOCK-END A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'dispatch: args must be JSON-serializable {jobs: [{label, promptPath, model, effort, kind}, ...]} or {chains: [{name, jobs: [...]}, ...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
}

// ONE PATH. `jobs` is a flat list: each job is its own lane, so the jobs run concurrently. `chains` is
// a list of lanes: lanes run concurrently and the jobs inside a lane run in order, each seeing the tree
// its predecessors changed (a dependent hop with no judgment between it and the next costs a parent turn
// otherwise). Both at once is refused: the caller states one shape.
const flat = Array.isArray(A.jobs) ? A.jobs : []
const chainsIn = Array.isArray(A.chains) ? A.chains : []
if (flat.length > 0 && chainsIn.length > 0) {
  return { error: 'dispatch: pass either jobs (a flat list, each job its own lane) or chains (lanes of jobs run in order), not both.' }
}
const lanes = chainsIn.length > 0
  ? chainsIn
  : flat.map(j => ({ name: (j && j.label) || '(unlabeled)', jobs: [j] }))
const jobs = lanes.flatMap(c => (c && Array.isArray(c.jobs)) ? c.jobs : [])

// Model vocabulary comes only from args.__transport, which hooks/pre_workflow_dispatch.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a call the entry did
// not rewrite fails closed. `efforts` is absent when the transport declares none.
const validStrings = values => Array.isArray(values) && values.length > 0
  && values.every(value => typeof value === 'string' && value.trim())
const transport = A.__transport
if (!transport || typeof transport !== 'object' || !validStrings(transport.ids)
  || (transport.efforts !== undefined && !validStrings(transport.efforts))) {
  return { error: 'dispatch: args.__transport is missing or malformed. hooks/pre_workflow_dispatch.py injects the endpoint\'s model vocabulary (non-empty string array `ids`, optional non-empty `efforts`); without it this engine has no legal pins and refuses to guess.' }
}
// ENGINE-BLOCK-BEGIN PIN
const PIN = (m) => m
// ENGINE-BLOCK-END PIN
// ENGINE-BLOCK-BEGIN EFF
const EFF = (e) => e
// ENGINE-BLOCK-END EFF
const VALID_MODELS = transport.ids
// Undefined when the transport declares no effort vocabulary: effort is then not a required pin.
const VALID_EFFORTS = transport.efforts

// KIND PROFILES. Each job names a `kind` (scout|lens|reviewer|author|judge|gate); the entry resolves that
// row of guards/tool_profiles.json into args.__toolProfiles[label]: agentType, tier, guard, mcpServers, builtins.
// A Workflow script cannot read the table, and no tool set is inferred from a brief or a type name.
// ENGINE-BLOCK-BEGIN TOOLP
const TOOLP = (A.__toolProfiles && typeof A.__toolProfiles === 'object') ? A.__toolProfiles : {}
// ENGINE-BLOCK-END TOOLP
const profileOf = (j) => (j && TOOLP[j.label]) || null
// A job's own `tier` wins; one that writes none takes its kind row's tier.
const tierOfJob = (j) => j.tier || (profileOf(j) || {}).tier || A.tier || '-'

// REQUIRED `kind` on every job, with no default: this engine mixes read-only and authoring jobs in one
// run, so any default would silently buy the wrong tool set. A job still carrying `agentType`, `shape` or
// `readOnly` is refused with the replacement named.
const RETIRED_KEYS = { agentType: 'the kind row sets the agent type', shape: 'use kind', readOnly: 'a job that writes is kind author; the other kinds cannot write' }
const retiredOn = (j) => Object.keys(RETIRED_KEYS).filter(k => !!j && k in j)
const kindOk = (j) => !!j && typeof j.kind === 'string' && !!j.kind.trim() && !!profileOf(j) && profileOf(j).kind === j.kind && !!profileOf(j).agentType

// Every job pins its own model, and its own effort when the transport declares an effort vocabulary.
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
const unpinned = jobs.filter(j => !!j && (!VALID_MODELS.includes(j.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(j.effort))))
if (unpinned.length > 0) {
  return {
    error: 'dispatch: pin model and effort on every job (orchestration §5). Unpinned or illegal: ' + unpinned.map(j => (j && j.label) || '(unlabeled)').join(', ') + '. Each job needs ' + pinVocab + '.',
    badJobs: unpinned.map(j => (j && j.label) || '(unlabeled)'),
  }
}
if (jobs.length === 0 && A.jobsFile) {
  return { error: 'dispatch: args.jobsFile is set (' + A.jobsFile + ') and args.jobs and args.chains are empty. hooks/pre_workflow_dispatch.py expands the file before the engine runs; it did not, so this call skipped the PreToolUse entry or the file holds no jobs. Re-issue the call once; if it repeats, read the file.' }
}
const retired = jobs.filter(j => retiredOn(j).length > 0)
if (retired.length > 0) {
  return {
    error: 'dispatch: a job names its tools by kind alone. ' + retired.map(j => (j.label || '(unlabeled)') + ' carries ' + retiredOn(j).map(k => k + ' (' + RETIRED_KEYS[k] + ')').join(', ')).join('; ') + '.',
    badJobs: retired.map(j => j.label || '(unlabeled)'),
  }
}
const badLane = lanes.filter(c => !c || !c.name || !Array.isArray(c.jobs) || c.jobs.length === 0)
const bad = jobs.filter(j => !j || !j.label || !j.promptPath || !kindOk(j))
if (jobs.length === 0 || badLane.length > 0 || bad.length > 0) {
  return {
    error: 'Every job needs {label, promptPath, model, effort, kind} with ' + pinVocab + ' and kind in [scout|lens|reviewer|author|judge|gate], resolved by the entry into args.__toolProfiles; every chain needs {name, jobs: [...]} with at least one job. State kind, never omit it: a job that writes is an author, and no tool set is inferred. Write each prompt to a scratchpad file and pass its path — never inline large prompts into args (gotcha_workflow_args_generation_fidelity).',
    badJobs: bad.map(j => (j && j.label) || '(unlabeled)'),
  }
}
const labelCount = {}
for (const j of jobs) labelCount[j.label] = (labelCount[j.label] || 0) + 1
const dupLabels = Object.keys(labelCount).filter(l => labelCount[l] > 1)
if (dupLabels.length > 0) {
  return { error: 'dispatch: job labels must be unique across every lane; their results are keyed by label. Duplicates: ' + dupLabels.join(', ') }
}
// OPTIONAL launch facts, recorded on the job: `pick` says WHY this pin was chosen (`cell|room|owner|default`),
// `spec` whether the slice's test intent is clearly defined (`tight|judged`, orchestration §5). A value outside
// the enum is refused before any agent starts: a reason recorded wrongly is worse than one recorded as absent.
// ENGINE-BLOCK-BEGIN VALID_PICKS
const VALID_PICKS = ['cell', 'room', 'owner', 'default']
// ENGINE-BLOCK-END VALID_PICKS
// ENGINE-BLOCK-BEGIN VALID_SPECS
const VALID_SPECS = ['tight', 'judged']
// ENGINE-BLOCK-END VALID_SPECS
// ENGINE-BLOCK-BEGIN basisFaults
const basisFaults = []
// ENGINE-BLOCK-END basisFaults
for (const j of jobs) {
  if (j.pick !== undefined && !VALID_PICKS.includes(j.pick)) basisFaults.push({ job: j, field: 'pick' })
  if (j.spec !== undefined && !VALID_SPECS.includes(j.spec)) basisFaults.push({ job: j, field: 'spec' })
}
if (basisFaults.length > 0) {
  return {
    error: 'dispatch: pick must be in [' + VALID_PICKS.join('|') + '] and spec must be in [' + VALID_SPECS.join('|') + '] on every job that states them (orchestration §5). Illegal: '
      + basisFaults.map(f => (f.job.label || '(unlabeled)') + ' ' + f.field + '=' + JSON.stringify(f.job[f.field])).join(', ') + '. Omit either field to record none.',
    badJobs: basisFaults.map(f => f.job.label || '(unlabeled)'),
  }
}

// ENGINE-BLOCK-BEGIN pinBasis
const pinBasis = (j) => (j.pick !== undefined ? ' pick:' + j.pick : '') + (j.spec !== undefined ? ' spec:' + j.spec : '')
// ENGINE-BLOCK-END pinBasis
// ENGINE-BLOCK-BEGIN effortRecord
const effortRecord = (j) => { const ec = (j.effortCases || {})[j.model] || {}; return ' req:' + (ec.requestedEffort || '-') + ' case:' + (ec.effortCase || '-') }
// ENGINE-BLOCK-END effortRecord
log('PINS ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, PIN(j.model) + '/' + EFF(j.effort) + '/' + profileOf(j).agentType + ' tier:' + tierOfJob(j) + pinBasis(j) + effortRecord(j)]))))
const justified = Object.fromEntries(jobs.filter(j => j.justification).map(j => [j.label, j.justification]))
if (Object.keys(justified).length) log('JUSTIFICATION ' + JSON.stringify(justified))
log('ENDPOINT: ' + transport.name + ' — pins are that transport\'s registry pins')
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Concurrency safety: with more than one lane, the project's test runners, builds and language servers
// are single-flight (per machine or per checkout). A lane names what it owns outright (`exclusiveResource`);
// `A.sharedSingleFlight` lists what no lane may touch (the runtime test slots, a language-server wrapper).
// The text is the delegate rails' own (.claude/guards/), so a job reads the same rule on every route.
const SHARED = Array.isArray(A.sharedSingleFlight) ? A.sharedSingleFlight : []
const guardFor = (lane, j) => {
  if (lanes.length < 2 && SHARED.length === 0) return ''
  const lines = ['', '=== ORCHESTRATION GUARD (you are one of several agents running CONCURRENTLY) ===',
    'Do NOT run a single-flight tool: one that allows only one process per machine or per checkout, such as a game engine or a language server. Your delegate rails name this project\'s others; a build, typecheck or test command they do not name is not single-flight, so run your brief\'s own checks with it. Python and Node proofs under .claude/tests/ are not single-flight'
      + (noShell(j) ? '; you have no shell, so cite a proof by path instead of running it.' : '; run the edited unit\'s own test file (`python -m pytest <file> -q`) before you return (rules/harness_tooling.md).')
      + ' If your task file mandates a single-flight run, STOP and report that it needs a serialized dispatch.']
  if (lanes.length > 1 && lane.exclusiveResource) lines.push('You are the ONLY agent in your own lane (' + lane.exclusiveResource + '), which you own outright for the duration of your task.')
  if (SHARED.length > 0) lines.push('MACHINE-WIDE single-flight — do NOT use these under any circumstances, even though you own your lane: ' + SHARED.join('; ') + '.')
  return lines.join('\n')
}

// Delegate rails — ONE home (.claude/guards/). The entry injects the assembled text as `args.__railsText`,
// because this script cannot read files. Every route carries the SAME two sections: common.md's plus the kind's guard file.
// The Read pointer is only the fallback: delegates followed it in about half of measured cells
// (gotcha_delegate_rails_pointer_followed_half_the_time).
// Guard tier per RECEIVING model is injected as `args.__rails`; a model the map does not name reads `detailed`.
// ENGINE-BLOCK-BEGIN RAILS
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
// ENGINE-BLOCK-END RAILS
const guardOf = (j) => profileOf(j).guard
// A job's own `railTier` wins over the map: it holds the model fixed while the tier varies. `none` sends no rails block.
const VALID_RAIL_TIERS = ['detailed', 'condensed', 'minimal', 'none']
const badTier = jobs.filter(j => j.railTier !== undefined && !VALID_RAIL_TIERS.includes(j.railTier))
if (badTier.length > 0) {
  return { error: 'railTier must be one of [' + VALID_RAIL_TIERS.join('|') + '] when set.', badJobs: badTier.map(j => j.label) }
}
const tierOf = (j) => j.railTier || RAILS[j.model] || 'detailed'
// ENGINE-BLOCK-BEGIN RAILS_TEXT
const RAILS_TEXT = (A.__railsText && typeof A.__railsText === 'object') ? A.__railsText : {}
// ENGINE-BLOCK-END RAILS_TEXT
// ENGINE-BLOCK-BEGIN RAILS_HEADER
const RAILS_HEADER = '=== DELEGATE RAILS (standing rules for how you work; your task is the brief above) ==='
// ENGINE-BLOCK-END RAILS_HEADER
// ENGINE-BLOCK-BEGIN inlineRails
const inlineRails = (label, guard, tier) => {
  const text = RAILS_TEXT[guard + '/' + tier]
  if (typeof text === 'string' && text.trim()) return ['', RAILS_HEADER, text].join('\n')
  if (Object.keys(RAILS_TEXT).length > 0) log('RAILS-FALLBACK ' + label + ' ' + guard + '/' + tier)
  return null
}
// ENGINE-BLOCK-END inlineRails
// Registry driverNotes per pinned model; rails key on (guard, tier) and carry no model, so notes travel in args.__driverNotes.
// ENGINE-BLOCK-BEGIN DRIVER_NOTES
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
// ENGINE-BLOCK-END DRIVER_NOTES
// ENGINE-BLOCK-BEGIN driverNotes
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
// ENGINE-BLOCK-END driverNotes
const guardRef = (x) => { const r = railsRef(x); return r && r + driverNotes(x.model) }
const railsRef = (j) => {
  const tier = tierOf(j)
  if (tier === 'none') return ''
  const guard = guardOf(j)
  const inline = inlineRails(j.label, guard, tier)
  if (inline) return inline
  const files = guard === 'common'
    ? '.claude/guards/common.md'
    : '.claude/guards/common.md and .claude/guards/' + guard + '.md'
  return [
    '',
    '=== DELEGATE RAILS ===',
    'Read ' + files + ' with the Read tool and follow the `## ' + tier + '` section of each. Read ONLY that section — the other tiers are for other models.',
  ].join('\n')
}
log('KINDS ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, j.kind + '/' + tierOf(j)]))))

// TOOL PROFILES — what each child of this run launches with. Every job runs as its kind row's generated
// agent type, whose `tools` frontmatter is the whole inventory, so an entry is `agent-type`.
const RECORDED = Object.fromEntries(jobs.map(j => [j.label, Object.assign({}, profileOf(j),
  { enforcement: 'agent-type', effectiveMcp: (profileOf(j).mcpServers || []).join(', ') })]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

// Return-path spill (optional): every agent's final text lands in ORCHESTRATOR context permanently, so a wide
// fan-out of verbose deliverables is the dominant driver of context growth per turn. With args.spillDir set, each
// agent writes its full deliverable to disk and returns only a bounded digest; the orchestrator Reads the file
// only for the jobs it acts on. Absent spillDir, the inline-return contract is unchanged.
// ENGINE-BLOCK-BEGIN SPILL_REL
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
// ENGINE-BLOCK-END SPILL_REL
// ENGINE-BLOCK-BEGIN NORMAL_SPILL_DIR
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
// ENGINE-BLOCK-END NORMAL_SPILL_DIR
if (NORMAL_SPILL_DIR && (/(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR) || !/^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))) {
  return { error: 'dispatch: spillDir must be repository-relative and contained by .claude/scratch/ (for example .claude/scratch/<task>/spill). Received: ' + SPILL_REL }
}
// ENGINE-BLOCK-BEGIN DIGEST_WORDS
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
// ENGINE-BLOCK-END DIGEST_WORDS
// A job dispatched from a linked worktree (<project>/.claude/worktrees/<name>) reports under the project root,
// where the brief's own paths point, not under the worktree it runs in.
// ENGINE-BLOCK-BEGIN projectRootOf
const projectRootOf = (a) => repoRootOf(a).replace(/\/\.claude\/worktrees\/[^/]+$/, '')
// ENGINE-BLOCK-END projectRootOf
// ENGINE-BLOCK-BEGIN repoRootOf
function repoRootOf(a) {
  const root = a && typeof a.__repoRoot === 'string' ? a.__repoRoot.trim().replace(/\\/g, '/').replace(/\/+$/, '') : ''
  return /^([A-Za-z]:)?\//.test(root) ? root : ''
}
// ENGINE-BLOCK-END repoRootOf
const SPILL_DIR = SPILL_REL && projectRootOf(A) ? projectRootOf(A) + '/' + NORMAL_SPILL_DIR : SPILL_REL
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'

// A kind without Edit/Write cannot create its spill file, so the spill contract would ask for a write it cannot
// perform. Spill is a context optimization, not a correctness invariant, so such a kind degrades to the inline
// contract and the omission is REPORTED (the label lands in inlineLabels on the return).
const hasTool = (j, t) => (profileOf(j).builtins || []).includes(t)
const canWrite = (j) => ['Edit', 'Write', 'NotebookEdit'].some(t => hasTool(j, t))
const noShell = (j) => !hasTool(j, 'Bash')
const spills = (j) => !!SPILL_DIR && !j.schema && hasTool(j, 'Write')

// A kind with no Edit/Write that still holds Bash can write through the shell, so its prompt carries the line.
const shellWriteGuard = (j) => (!canWrite(j) && !noShell(j))
  ? '\n=== READ-ONLY: do NOT modify, create, or delete any file, through the shell either. ==='
  : ''

const spillContract = (j) => spills(j) ? [
  '',
  '=== RETURN-PATH CONTRACT (overrides any "return the full result" wording in your brief) ===',
  'Write your FULL deliverable to ' + spillPath(j.label) + ' using the Write tool. That file is yours alone — no other agent writes it.',
  'Then return ONLY: (a) a digest of at most ' + DIGEST_WORDS + ' words covering what the caller must decide or act on, (b) one line `couldNotSatisfy:` naming every unmet item with what stopped it (denial, missing input, out-of-lane file) and the exact intended change, or `couldNotSatisfy: none`, and (c) a final line exactly `FULL: ' + spillPath(j.label) + '`.',
  'Do NOT restate the full deliverable in your final message. If you could not write the file, say so in the digest instead of pasting the content.',
].join('\n') : ''

// A writing job names what it wrote beyond its spec, so review routes those units as design no reviewer saw
// (orchestration §2 *Later rounds*).
const choicesContract = (j) => (j.schema || !canWrite(j)) ? '' : [
  '',
  '=== CHOICES (orchestration/reference/writer_returns.md) ===',
  'End your deliverable with a fenced block opened by ```choices holding one JSON object per line, {"unit": "<what you wrote>", "why": "<its reason>"}, for each unit whose content your brief or plan does not state. Leave the block empty when there is none.',
].join('\n')

// Two labels that sanitize to the same filename would silently overwrite each other's deliverable —
// data loss with no signal, in the one path whose whole point is that the orchestrator never reads
// the full text. Fail loud; the caller renames a label.
const INLINE_LABELS = SPILL_DIR ? jobs.filter(j => !spills(j)).map(j => j.label) : []
if (SPILL_DIR) {
  const seen = {}
  const collided = jobs.filter(j => spills(j)).filter(j => { const p = spillPath(j.label).toLowerCase(); const dup = !!seen[p]; seen[p] = true; return dup })
  if (collided.length > 0) {
    return {
      error: 'args.spillDir is set, but these labels collide after filename sanitization ([^A-Za-z0-9._-] -> _) and would overwrite each other: '
        + collided.map(j => j.label).join(', ') + '. Give each job a label that is distinct in those characters.',
    }
  }
  log('SPILL-DIR ' + SPILL_DIR + ' (digest cap ' + DIGEST_WORDS + ' words/job)')
  if (INLINE_LABELS.length > 0) {
    log('SPILL-EXEMPT (these kinds have no Write tool and a job with a schema returns its object; these return in full INLINE and cost orchestrator context): ' + INLINE_LABELS.join(', '))
  }
}

// A later job in a chain runs against the tree its predecessors already changed, which is the whole point of
// chaining — so tell it that, and name the predecessors it must not silently contradict.
const handoff = (c, idx) => idx === 0 ? '' : ['',
  '=== CHAIN POSITION ===',
  'You are step ' + (idx + 1) + ' of ' + c.jobs.length + ' in lane `' + c.name + '`. Earlier steps in this lane ('
  + c.jobs.slice(0, idx).map(p => '`' + p.label + '`').join(', ')
  + ') have ALREADY edited this working tree and their changes are present. Read the current file bytes rather than assuming the pre-chain state, and if a change of yours would revert or contradict an earlier step, report the conflict instead of silently overwriting it.',
].join('\n')

// The platform relays the triggering user message to every agent as authoritative; a job whose
// brief looks unrelated can answer that message instead of its brief.
// ENGINE-BLOCK-BEGIN RELAY_LINE
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// ENGINE-BLOCK-END RELAY_LINE

// Evidence: a claim a job makes is checked by its caller, not here. /review runs tools/quote_check.py over the
// findings file; this engine returns the job's text unchanged.
const EVIDENCE_RULES = 'Cite each claim with its source: a file quote, a read-only git command and its output, or the owner\'s verbatim words. Do not paraphrase a quote.'

phase(chainsIn.length > 0 ? 'Chains' : 'Dispatch')
// Labels after a lane stop: they never ran, so they have no entry and are not dead.
const notRun = []
const results = await parallel(lanes.map(c => async () => {
  const laneOut = []
  for (let i = 0; i < c.jobs.length; i++) {
    const j = c.jobs[i]
    const ctx = [A.contextPath].concat(Array.isArray(j.contextPaths) ? j.contextPaths : []).filter(p => typeof p === 'string' && p)
      .map(p => 'SHARED CONTEXT for this dispatch is at: ' + p + ' — read it with the Read tool FIRST (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it).\n\n')
      .join('')
    const prompt = ctx
      + 'Evidence: ' + EVIDENCE_RULES + '\n'
      + 'Your full task brief is at: ' + j.promptPath + ' — read it with the Read tool and execute it exactly (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it). Your final message is the deliverable: return the result the brief asks for, self-contained, no meta-commentary.'
      + '\n' + RELAY_LINE
      + handoff(c, i)
      + shellWriteGuard(j)
      + guardFor(c, j)
      + guardRef(j)
      + spillContract(j)
      + choicesContract(j)
    const opts = { label: j.label, phase: chainsIn.length > 0 ? 'Chains' : 'Dispatch', model: PIN(j.model), effort: EFF(j.effort), agentType: profileOf(j).agentType }
    if (j.schema) opts.schema = j.schema
    // A rejected agent names the rejection and, in a lane, the later steps it stopped. A null result is a dead
    // or stalled agent: it lands as label: null, which NO-RETURN names with its salvage route.
    let r = null
    try {
      r = await agent(prompt, opts)
    } catch (e) {
      const later = i < c.jobs.length - 1 ? '; remaining jobs in lane `' + c.name + '` were not run' : ''
      laneOut.push([j.label, { error: 'dispatch: agent rejected (' + ((e && e.message) || String(e)) + ')' + later }])
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length + ' (rejected result from ' + j.label + ')')
      break
    }
    laneOut.push([j.label, r])
    if (r == null) {
      log('lane ' + c.name + ': stopped at ' + (i + 1) + '/' + c.jobs.length + ' (no result from ' + j.label + ')')
      break
    }
    log('lane ' + c.name + ': ' + (i + 1) + '/' + c.jobs.length + ' done (' + j.label + ')')
  }
  for (const rest of c.jobs.slice(laneOut.length)) notRun.push(rest.label)
  return laneOut
}))

const out = Object.fromEntries(results.flat().filter(Boolean))

// A null result is a dead or stalled agent, not an empty deliverable. Name it and the recovery route in the
// RETURN value: the work it paid for sits in its spill file or transcript.
const NO_RETURN_JOBS = jobs.filter(j => !notRun.includes(j.label) && (!(j.label in out) || out[j.label] == null))
const NO_RETURN = NO_RETURN_JOBS.map(j => j.label)
let noReturn = null
if (NO_RETURN.length > 0) {
  const route = NO_RETURN_JOBS.map(j => j.label + ' -> ' + (spills(j) ? spillPath(j.label) + ' else ' : '') + '/salvage_fanout <transcriptDir> ' + j.label).join('; ')
  log('NO-RETURN ' + NO_RETURN.join(', ') + ' — recover before re-dispatching: ' + route)
  noReturn = { labels: NO_RETURN, recover: route }
}
const inlineRecover = INLINE_LABELS.length > 0
  ? 'No Write tool or a schema return for ' + INLINE_LABELS.join(', ') + ': their deliverables came back inline in this return value.'
  : null

// `noReturn` rides beside spillDir/digests, never inside `digests` (callers iterate it as labels).
const returned = SPILL_DIR
  ? Object.assign(inlineRecover ? { inlineRecover } : {}, { spillDir: SPILL_DIR, digests: out }, INLINE_LABELS.length > 0 ? { inlineLabels: INLINE_LABELS } : {}, noReturn ? { noReturn } : {})
  : (noReturn ? Object.assign({ noReturn }, out) : out)
return returned
