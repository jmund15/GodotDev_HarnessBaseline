export const meta = {
  name: 'dispatch',
  description: 'Run independent pinned jobs in parallel; use instead of bare Agent-tool fan-outs',
  phases: [{ title: 'Dispatch', detail: 'all jobs concurrent (engine queues past the concurrency cap)' }],
}
// Claims/findings jobs close with an independent deterministic verification stage.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'dispatch: args must be JSON-serializable {jobs: [{label, promptPath, model, effort}, ...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
// EVIDENCE-SSOT-BEGIN
const EVIDENCE_SCHEMA = {
  "description": "The evidence contract's machine twin (reference/evidence_contract.md owns the semantics; tools/verify_claims.py is the one executable implementation). A claim's `evidence` is a list of items, each exactly one of four kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it.",
  "type": "array",
  "items": {
    "oneOf": [
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "file",
          "quote"
        ],
        "description": "A quote from a local file. VERIFIED when the quote (case-folded, in-order `...` gaps allowed, a leading `path:N:` or `N:` grep prefix stripped) occurs in the file; UNVERIFIED when the file is readable and lacks it or does not exist; UNCHECKED when the file cannot be read.",
        "properties": {
          "file": {
            "type": "string",
            "description": "repository-relative or absolute path, optionally with :N or :N-M; the line range is reported, never decisive"
          },
          "quote": {
            "type": "string",
            "description": "the file's raw bytes, copied verbatim; one source per item"
          }
        }
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "command",
          "output"
        ],
        "description": "The stdout of a read-only git command from the allow-list (git grep, ls-files, ls-tree, log, show, branch, check-ignore, cat-file, rev-parse, diff --stat or --name-only, status --short), run without a shell from the repository root. VERIFIED when stdout holds the output, or when output is empty, stdout is empty and every pathspec the command names matches a tracked path. Any other command, a shell metacharacter, an unlisted flag or a timeout is UNCHECKED.",
        "properties": {
          "command": {
            "type": "string",
            "description": "one git command, no pipe, redirect or substitution"
          },
          "output": {
            "type": "string",
            "description": "a verbatim run of the command's stdout, or the empty string for an absent claim"
          }
        }
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "owner"
        ],
        "description": "The owner's verbatim words. VERIFIED when an owner turn of the session transcript holds them (an AskUserQuestion answer is an owner turn when the item also carries its `question`; a runtime envelope is not), or when a row of the named task record holds them and still resolves. A delegate-recorded, missing or unresolved row, or an unreadable transcript, is UNCHECKED.",
        "properties": {
          "owner": {
            "type": "string",
            "description": "the owner's words, verbatim"
          },
          "question": {
            "type": "string",
            "description": "the AskUserQuestion question the owner answered; required when the quote is a picked option label"
          },
          "task": {
            "type": "string",
            "description": "a task record id whose condition rows may hold the words when this session's transcript does not"
          }
        }
      },
      {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "url",
          "quote"
        ],
        "description": "A quote from an external page. A fetched artifact (fetch_source.sh manifest) is checked like a file; without one the item is UNCHECKED.",
        "properties": {
          "url": {
            "type": "string",
            "description": "the page the quote was read from"
          },
          "quote": {
            "type": "string",
            "description": "verbatim from the fetched bytes"
          },
          "artifact": {
            "type": "string",
            "description": "local path of the fetched bytes"
          }
        }
      }
    ]
  }
}
const EVIDENCE_RULES = "The evidence contract's machine twin (reference/evidence_contract.md owns the semantics; tools/verify_claims.py is the one executable implementation). A claim's `evidence` is a list of items, each exactly one of four kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it."
// EVIDENCE-SSOT-END

const jobs = Array.isArray(A.jobs) ? A.jobs : []

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. `efforts` is absent when the transport declares none.
// args.checker = {model, effort}: the pin for the verifier this engine starts itself (closeCheck). Required
// only when the run reaches that step, and validated before any agent starts. No engine supplies a default pin.
const transport = A.__transport
const validStrings = values => Array.isArray(values) && values.length > 0
  && values.every(value => typeof value === 'string' && value.trim())
if (!transport || typeof transport !== 'object' || !validStrings(transport.ids)
  || (transport.efforts !== undefined && !validStrings(transport.efforts))) {
  return { error: 'dispatch: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary (non-empty string array `ids`, optional non-empty `efforts`); without it this engine has no legal pins and refuses to guess.' }
}
const PIN = (m) => m
const EFF = (e) => e
const VALID_MODELS = transport.ids
// Undefined when the transport declares no effort vocabulary: effort is then not a required pin.
const VALID_EFFORTS = transport.efforts
// A job takes its shape's tier: args.__shapeTiers, injected by hooks/workflow_provider_guard.py
// from guards/tool_profiles.json. The shape is a fact the call carries, so it wins over a `tier`
// the job or the call writes -- the PINS row must record the tier the guard checked against the pin.
const SHAPE_TIERS = (A.__shapeTiers && typeof A.__shapeTiers === 'object') ? A.__shapeTiers : {}
const tierOfJob = (j) => SHAPE_TIERS[j.shape] || j.tier || A.tier || '-'

// OPTIONAL third pin. `Explore` and `Plan` are read-only built-ins that receive NO project
// CLAUDE.md and no memory index at any model pin (measured 2026-08-18, twice: asked to name the
// section headings in its own context, Explore answers NONE where general-purpose quotes them).
// That halves the per-dispatch price — 27.8K vs 53.4K first-turn input tokens on a fanout-tier row — and it is
// exactly the right shape for a lens whose answer is verifiable without project doctrine.
//
// It is offered HERE, in the pinned engine, and not as a reason to leave Workflow for the `Agent`
// tool: `agentType` is a parameter of Workflow's agent(), and the Agent tool has no effort
// parameter at all. Reaching for Explore via the Agent tool would trade a halved input cost for an
// INHERITED session effort — an unrelated row's rung applied to whatever this lens is pinned to,
// which buys turns a locate-and-extract job does not need and, above a row's effort ceiling, lands
// on a cell the ladder bans. Workflow is the only mechanism that pins model, effort and agentType
// together.
//
// REQUIRED, not optional, and deliberately so. This engine mixes read-only and authoring jobs in
// one run, so it cannot default safely in either direction — and an omitted field would default to
// the EXPENSIVE side silently, which is the failure this harness already refuses for `model` and
// `effort`. Pick `general-purpose` only when the job must APPLY project rules (CLAUDE.core.md §9,
// naming conventions, the TDD domain split): those live in files Explore never receives, and an
// agent cannot follow a rule it was not given.
const VALID_AGENT_TYPES = ['Explore', 'Plan', 'general-purpose', 'lane-survey', 'lane-review', 'lane-author']

// Every job pins its own model, and its own effort when the transport declares an effort vocabulary.
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
const unpinned = jobs.filter(j => !!j && (!VALID_MODELS.includes(j.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(j.effort))))
if (unpinned.length > 0) {
  return {
    error: 'dispatch: pin model and effort on every job (orchestration §5). Unpinned or illegal: ' + unpinned.map(j => j.label || '(unlabeled)').join(', ') + '. Each job needs ' + pinVocab + '.',
    badJobs: unpinned.map(j => j.label || '(unlabeled)'),
  }
}
const bad = jobs.filter(j => !j || !j.label || !j.promptPath || !VALID_AGENT_TYPES.includes(j.agentType))
if (jobs.length === 0 || bad.length > 0) {
  return {
    error: 'Every job needs {label, promptPath, model, effort, agentType} with ' + pinVocab + ' and agentType in [' + VALID_AGENT_TYPES.join('|') + ']. State agentType, never omit it: omitting it silently buys the general-purpose start, about 1.5x the Explore start. Pick general-purpose ONLY when the job must APPLY a project rule. Write each prompt to a scratchpad file and pass its path — never inline large prompts into args (gotcha_workflow_args_generation_fidelity).',
    badJobs: bad.map(j => (j && j.label) || '(unlabeled)'),
  }
}
// OPTIONAL launch facts, recorded on the job: `pick` says WHY this pin was chosen
// (`cell|room|owner|default`), `spec` whether the slice's test intent is clearly defined
// (`tight|judged`, orchestration §5). They are optional and independent -- an omitted field is a
// real answer the readouts count as unrecorded (`pins.spec-unrecorded.all`), and the reason is lost
// if it waits for the rating, which comes after compaction. A value outside the enum is refused
// before any agent starts: a reason recorded wrongly is worse than one recorded as absent.
const VALID_PICKS = ['cell', 'room', 'owner', 'default']
const VALID_SPECS = ['tight', 'judged']
const basisFaults = []
for (const j of jobs) {
  if (!j) continue
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
// The closeCheck verifier starts only when the run declares returns or a job carries fold or ledger (the
// fence-only path is runtime and is guarded again inside closeCheck). Validate its pin before any agent starts.
const closeReachable = !!(A.__returns && Object.keys(A.__returns).length > 0) || jobs.some(j => j.fold || j.ledger)
const checkerOk = !!A.checker && typeof A.checker === 'object' && VALID_MODELS.includes(A.checker.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.checker.effort))
if (closeReachable && !checkerOk) {
  return { error: 'dispatch: args.checker = {model, effort} is required because this run reaches the closeCheck verifier; args.checker must pin ' + pinVocab + '. Refusing before any agent starts.' }
}

const pinBasis = (j) => (j.pick !== undefined ? ' pick:' + j.pick : '') + (j.spec !== undefined ? ' spec:' + j.spec : '')
log('PINS ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, PIN(j.model) + '/' + EFF(j.effort) + (j.agentType ? '/' + j.agentType : '') + ' tier:' + tierOfJob(j) + pinBasis(j)]))))
log('ENDPOINT: ' + transport.name + ' — pins are that transport\'s registry pins')
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Concurrency safety: with >1 concurrent job, the project's test runners, builds and language servers
// are single-flight (per machine or per checkout) (gotcha_workflow_single_flight_concurrency); the delegate rails name
// them. A single job has no concurrent peer, so it gets no such restriction. The text is .claude/guards/single_flight.json, which
// tools/sidecar_fanout.py renders for its children; a Workflow script cannot read files, so this copy
// is held equal to it by tests/dispatch_spill_root_test.js.
const concurrencyGuard = (j) => jobs.length > 1 ? [
  '',
  '=== ORCHESTRATION GUARD (you are one of several agents running CONCURRENTLY) ===',
  'Do NOT run a single-flight tool (a test runner, build, engine or language server that allows one process per machine or per checkout); your delegate rails name this project\'s. Python and Node proofs under .claude/tests/ are not single-flight'
    + (NO_SHELL_AGENT_TYPES.includes(effectiveType(j)) ? '; you have no shell, so cite a proof by path instead of running it.' : '; run the edited unit\'s proof file, not harness_tests.py (its machine slots are shared; rules/harness_tooling.md).')
    + ' If your task file mandates a single-flight run, STOP and report that it needs a serialized dispatch.',
].join('\n') : ''

// Delegate rails — ONE home (.claude/guards/), assembled by tools/guard_text.py for every route: the
// Workflow hook inlines it for all four engines (below), hooks/session_model_rails.py for a sidecar
// `claude` child, scripts/lib/sidecar_common.sh on the -D bare/pointer tiers. No engine holds a copy.
// Every route carries the SAME two sections — any.md's plus the shape file's. The Read pointer is
// only the fallback: delegates followed it in about half of measured cells
// (gotcha_delegate_rails_pointer_followed_half_the_time).
// Shape decides which rule families are reachable; the job's own `model` decides how much is spelled
// out (instruction_quality §3, "tier rails by the model that RECEIVES them").
// Unlike model/effort, a missing or unrecognized shape is NOT a caller bug — it means the
// orchestrator asserts no specific shape applies, which is exactly what `any` covers.
const VALID_SHAPES = ['any', 'survey', 'review', 'author']
// Guard tier per RECEIVING model is registry data (`railTier`), injected as `args.__rails` by
// hooks/workflow_provider_guard.py because a Workflow script cannot read files. A model the map
// does not name, or a call the hook did not rewrite, reads `detailed`: the fail-safe direction.
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
const shapeOf = (j) => VALID_SHAPES.includes(j.shape) ? j.shape : 'any'
// A job's own `railTier` wins over the map: it holds the model fixed while the tier varies, which is
// how /rail_battery measures a tier through this exact prompt. `none` sends no rails block.
const VALID_RAIL_TIERS = ['detailed', 'condensed', 'minimal', 'none']
const badTier = jobs.filter(j => j.railTier !== undefined && !VALID_RAIL_TIERS.includes(j.railTier))
if (badTier.length > 0) {
  return { error: 'railTier must be one of [' + VALID_RAIL_TIERS.join('|') + '] when set.', badJobs: badTier.map(j => j.label) }
}
const tierOf = (j) => j.railTier || RAILS[j.model] || 'detailed'
// Rails text per (shape, tier) is assembled from .claude/guards/ by tools/guard_text.py and injected as
// `args.__railsText` by hooks/workflow_provider_guard.py, because this script cannot read files. The
// inline text reaches every delegate; the Read pointer is only the fallback when the hook supplied
// none, and a present map missing a job's pair logs RAILS-FALLBACK so a key disagreement is visible.
const RAILS_TEXT = (A.__railsText && typeof A.__railsText === 'object') ? A.__railsText : {}
const RAILS_HEADER = '=== DELEGATE RAILS (standing rules for how you work; your task is the brief above) ==='
const inlineRails = (label, shape, tier) => {
  const text = RAILS_TEXT[shape + '/' + tier]
  if (typeof text === 'string' && text.trim()) return ['', RAILS_HEADER, text].join('\n')
  if (Object.keys(RAILS_TEXT).length > 0) log('RAILS-FALLBACK ' + label + ' ' + shape + '/' + tier)
  return null
}
// Registry driverNotes per pinned model; rails key on (shape, tier) and carry no model, so notes travel in args.__driverNotes.
const DRIVER_NOTES = (A.__driverNotes && typeof A.__driverNotes === 'object') ? A.__driverNotes : {}
const driverNotes = (model) => (typeof DRIVER_NOTES[model] === 'string' && DRIVER_NOTES[model].trim()) ? '\n\n' + DRIVER_NOTES[model] : ''
const guardRef = (x) => { const r = railsRef(x); return r && r + driverNotes(x.model) }
const railsRef = (j) => {
  const tier = tierOf(j)
  if (tier === 'none') return ''
  const shape = shapeOf(j)
  const inline = inlineRails(j.label, shape, tier)
  if (inline) return inline
  const files = shape === 'any'
    ? '.claude/guards/any.md'
    : '.claude/guards/any.md and .claude/guards/' + shape + '.md'
  return [
    '',
    '=== DELEGATE RAILS ===',
    'Read ' + files + ' with the Read tool, plus each layer overlay beside them (.claude/guards/<name>.<layer>.md) that exists, and follow the `## ' + tier + '` section of each. Read ONLY that section — the other tiers are for other models.',
  ].join('\n')
}
log('SHAPES ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, shapeOf(j) + '/' + tierOf(j)]))))

// TOOL PROFILES — what each child of this run launches with. A native child INHERITS the session's
// MCP set (the runtime restricts no per-child server), so an entry is `inherited` with effectiveMcp
// unknown unless the job runs as its generated agent type (`agent-type`). hooks/workflow_provider_guard.py resolves args.__toolProfiles for a job that
// DECLARED a shape or a delta, and args.__toolProfileDefaults (one row per shape) for the stages a
// script spawns on its own labels.
const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
// LEAN AGENT TYPE (plan lean-delegate-agent-types). Copied per engine: a Workflow script has no imports.
// A job runs as its profile's generated `<prefix>-*` type only when it asked for `general-purpose` or no
// type, its profile names an `agentType` with no add/remove delta, and hooks/workflow_provider_guard.py
// proved its brief covered: `args.__coverage[key].covered` holds the profile's shape. No entry, no swap.
// A swapped prompt ends with `args.__skillIndex`, because a `<prefix>-*` type has no Skill tool. A session
// that predates the generated type rejects it; the job retries once, as asked (the known-good type), and is re-recorded.
const LEAN = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { c: (a && a.__coverage) || {}, s: (a && typeof a.__skillIndex === 'string') ? a.__skillIndex : '' } } catch (e) { return { c: {}, s: '' } } })()
const leanTypeOf = (keys, asked, tp) => {
  const cov = keys.map(k => LEAN.c[k]).find(Boolean)
  return ((asked == null || asked === 'general-purpose') && tp && tp.agentType
    && !(tp.add || []).length && !(tp.remove || []).length
    && cov && Array.isArray(cov.covered) && cov.covered.includes(tp.shape)) ? tp.agentType : null
}
const leanRecord = (tp, asked, swapped) => tp ? Object.assign({}, tp, swapped
  ? { agentType: swapped, enforcement: 'agent-type', effectiveMcp: (tp.mcpServers || []).join(', ') }
  : { agentType: asked || 'general-purpose' }) : null
const leanAgent = (prompt, opts, swapped, rec, strict) => {
  if (!swapped) return agent(prompt, opts)
  const asked = opts.agentType || 'general-purpose'
  return agent(LEAN.s ? prompt + '\n\n' + LEAN.s : prompt, Object.assign({}, opts, { agentType: swapped })).catch(e => {
    const msg = String((e && e.message) || e)
    if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
    if (strict) {
      log('FALLBACK-REFUSED ' + opts.label + ' ' + swapped + ': a read-only job never retries as ' + asked + ', which holds write tools')
      return null
    }
    log('FALLBACK ' + opts.label + ' ' + swapped + '→' + asked)
    rec[opts.label] = Object.assign({}, rec[opts.label], { agentType: 'general-purpose', enforcement: 'inherited', effectiveMcp: 'unknown/inherited' })
    log('TOOL-PROFILES-REVISED ' + JSON.stringify(rec))
    return agent(prompt, Object.assign({}, opts, { agentType: asked }))
  })
}
const profileOf = (j) => TOOLP.d[j.label] || TOOLP.f[shapeOf(j)] || null
const swapOf = (j) => leanTypeOf([j.label], j.agentType, profileOf(j))
const effectiveType = (j) => swapOf(j) || j.agentType
const RECORDED = Object.fromEntries(jobs.map(j => [j.label, leanRecord(profileOf(j), j.agentType, swapOf(j))]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))

// Return-path spill (optional): every agent's final text lands in ORCHESTRATOR context permanently,
// so a wide fan-out of verbose deliverables is the dominant driver of context growth per turn. With
// args.spillDir set, each agent writes its full deliverable to disk and returns only a bounded digest;
// the orchestrator Reads the file only for the jobs it actually acts on. Absent spillDir, the inline-
// return contract is unchanged (callers that need the full text in-context keep working untouched).
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
// Spill files live under .claude/scratch/, the rule review_fanout.js and explore_fanout.js enforce:
// a repository-relative path with no `..` segment.
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
if (NORMAL_SPILL_DIR && (/(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR) || !/^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))) {
  return { error: 'dispatch: spillDir must be repository-relative and contained by .claude/scratch/ (for example .claude/scratch/<task>/spill). Received: ' + SPILL_REL }
}
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
const SPILL_DIR = SPILL_REL && repoRootOf(A) ? repoRootOf(A) + '/' + NORMAL_SPILL_DIR : SPILL_REL
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'

// `Explore` and `Plan` are granted every tool EXCEPT Edit/Write/NotebookEdit, so the spill contract
// asks them for a write they cannot perform: the agent reports the missing tool and returns the full
// deliverable inline, which is the exact outcome spillDir exists to prevent. Rejecting the run is the
// wrong remedy — this engine deliberately mixes read-only and authoring jobs (see VALID_AGENT_TYPES),
// so rejection would force the caller to either buy a scout the 53.4K general-purpose start to satisfy
// a return path, or drop spillDir for every job to accommodate one scout. Spill is a context
// optimization, not a correctness invariant (unlike the label collision below, which is data loss), so
// the no-write types degrade to the inline contract and the omission is REPORTED rather than silent. `tools/dispatch_result.py` writes their journaled result to the same <spillDir>/<label>.md a spilling job lands at.
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan', 'lane-review', 'lane-survey']
const NO_SHELL_AGENT_TYPES = ['lane-review', 'lane-survey']
const spills = (j) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(effectiveType(j))

// The read-only guard must except the spill file, or a readOnly job is told to write and not-write.
// The line is prompt text. Only a lane type's tool list removes the write tools, so the engine keeps
// the promise by tool set (readOnlyKept) and carries no marker backstop: a dispatch.js run mixes
// readOnly and author jobs, so arming one would warn on every legitimate authored write.
const readOnlyGuard = (j) => spills(j)
  ? '\n=== READ-ONLY: do NOT modify, create, or delete any file EXCEPT your own spill file ' + spillPath(j.label) + '. ==='
  : '\n=== READ-ONLY: do NOT modify, create, or delete any file. ==='

// Bash writes as freely as Write does, so it counts as a write tool.
const WRITE_TOOLS = ['Edit', 'Write', 'NotebookEdit', 'Bash']
const writeToolsOf = (j) => ((profileOf(j) || {}).builtins || []).filter(t => WRITE_TOOLS.includes(t))
// A shape whose default profile names a lane type and holds no write tool is read-only by its shape
// (survey, review). A brief that runs a shell widens the job's profile with Bash, which ends the claim.
const shapeReadOnly = (j) => { const d = TOOLP.f[shapeOf(j)]; return !!(d && d.agentType && !(d.builtins || []).some(t => WRITE_TOOLS.includes(t))) }
const readOnlyOf = (j) => j.readOnly === true || (j.readOnly !== false && shapeReadOnly(j) && writeToolsOf(j).length === 0)
// `readOnly: true` is a promise the engine keeps by tool set or refuses: the job must run as a shell-less lane type.
const readOnlyKept = (j) => NO_SHELL_AGENT_TYPES.includes(effectiveType(j))
const brokenPromise = jobs.filter(j => j.readOnly === true && !readOnlyKept(j))
if (brokenPromise.length > 0) {
  return {
    error: 'dispatch: readOnly:true is kept by tool set, and these jobs would run with write tools: '
      + brokenPromise.map(j => j.label + ' (' + (writeToolsOf(j).length > 0
        ? 'its tool profile holds ' + writeToolsOf(j).join(', ') + ', which a brief that runs a shell widens it with'
        : 'it would not run as a lane type: the session has not loaded the generated agent type, or hooks/workflow_provider_guard.py did not prove its brief covered') + ')').join('; ')
      + '. Dispatch them as shape author or any without readOnly:true, or drop the tool the brief needs.',
  }
}
for (const j of jobs.filter(j => j.readOnly === undefined && shapeReadOnly(j) && writeToolsOf(j).length > 0)) {
  log('NOT-READ-ONLY ' + j.label + ': its ' + shapeOf(j) + ' profile holds ' + writeToolsOf(j).join(', ') + ', so the job can write')
}
const unenforced = jobs.filter(j => j.readOnly !== true && readOnlyOf(j) && !readOnlyKept(j))
if (unenforced.length > 0) {
  log('READ-ONLY-UNENFORCED ' + unenforced.map(j => j.label).join(', ') + ': read-only by shape, but not running as a lane type, so only the prompt line holds them')
}

const spillContract = (j) => spills(j) ? [
  '',
  '=== RETURN-PATH CONTRACT (overrides any "return the full result" wording in your brief) ===',
  'Write your FULL deliverable to ' + spillPath(j.label) + ' using the Write tool. That file is yours alone — no other agent writes it.',
  'Then return ONLY: (a) a digest of at most ' + DIGEST_WORDS + ' words covering what the caller must decide or act on, (b) one line `couldNotSatisfy:` naming every unmet item with what stopped it (denial, missing input, out-of-lane file) and the exact intended change, or `couldNotSatisfy: none`, and (c) a final line exactly `FULL: ' + spillPath(j.label) + '`.',
  'Do NOT restate the full deliverable in your final message. If you could not write the file, say so in the digest instead of pasting the content.',
].join('\n') : ''

// A writing job names what it wrote beyond its spec, so review routes those units as design no
// reviewer saw (orchestration §2 *Later rounds*); `dispatch_result.py check --block choices=unit,why` validates it.
const choicesContract = (j) => (readOnlyOf(j) || NO_WRITE_AGENT_TYPES.includes(effectiveType(j))) ? '' : [
  '',
  '=== CHOICES (orchestration/reference/writer_returns.md) ===',
  'End your deliverable with a fenced block opened by ```choices holding one JSON object per line, {"unit": "<what you wrote>", "why": "<its reason>"}, for each unit whose content your brief or plan does not state. Leave the block empty when there is none. With Bash, run `python3 .claude/tools/dispatch_result.py check <your deliverable file> --block choices=unit,why` before returning; the parent runs it otherwise, and a missing block fails it.',
].join('\n')

// Two labels that sanitize to the same filename would silently overwrite each other's deliverable —
// data loss with no signal, in the one path whose whole point is that the orchestrator never reads
// the full text. Fail loud; the caller renames a label.
const INLINE_LABELS = SPILL_DIR ? jobs.filter(j => !spills(j)).map(j => j.label) : []
if (SPILL_DIR) {
  const seen = {}
  const collided = jobs.filter(j => spills(j)).filter(j => { const p = spillPath(j.label); const dup = !!seen[p]; seen[p] = true; return dup })
  if (collided.length > 0) {
    return {
      error: 'args.spillDir is set, but these labels collide after filename sanitization ([^A-Za-z0-9._-] -> _) and would overwrite each other: '
        + collided.map(j => j.label).join(', ') + '. Give each job a label that is distinct in those characters.',
    }
  }
  log('SPILL-DIR ' + SPILL_DIR + ' (digest cap ' + DIGEST_WORDS + ' words/job)')
  if (INLINE_LABELS.length > 0) {
    log('SPILL-EXEMPT (' + NO_WRITE_AGENT_TYPES.join('/') + ' have no Write tool; these return in full INLINE and cost orchestrator context; python3 .claude/tools/dispatch_result.py <transcriptDir> writes each to ' + SPILL_DIR + '/<label>.md): ' + INLINE_LABELS.join(', '))
  }
}

// The platform relays the triggering user message to every agent as authoritative; a job whose
// brief looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// How a parent corrects a running author job: Workflow agents have no inbox (orchestration §4).
const AMEND_LINE = (promptPath) => 'Amend: at each item and before returning, if ' + promptPath + '.amend.md exists, read it; it overrides the brief. Name what you applied.'

phase('Dispatch')
const results = await parallel(jobs.map(j => () => {
  const ctx = A.contextPath
    ? 'SHARED CONTEXT for this dispatch is at: ' + A.contextPath + ' — read it with the Read tool FIRST (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it).\n\n'
    : ''
  const prompt = ctx
    + 'Evidence: ' + EVIDENCE_RULES + '\n'
    + 'Your full task brief is at: ' + j.promptPath + ' — read it with the Read tool and execute it exactly (retry a transient read error; report a missing path or a denial as couldNotSatisfy, do not retry it). Your final message is the deliverable: return the result the brief asks for, self-contained, no meta-commentary.'
    + '\n' + RELAY_LINE
    + (shapeOf(j) === 'author' ? '\n' + AMEND_LINE(j.promptPath) : '')
    + (readOnlyOf(j) ? readOnlyGuard(j) : '')
    + concurrencyGuard(j)
    + guardRef(j)
    + spillContract(j)
    + choicesContract(j)
  const opts = { label: j.label, phase: 'Dispatch', model: PIN(j.model), effort: EFF(j.effort) }
  if (j.agentType) opts.agentType = j.agentType
  return leanAgent(prompt, opts, swapOf(j), RECORDED, j.readOnly === true).then(r => [j.label, r])
}))

// `inlineLabels` rides the RETURN value, not just log(): the caller reads this object, and an
// exemption visible only in the progress narrator is the same silent omission this guard exists to end.
const out = Object.fromEntries(results.filter(Boolean))
// A null result is a dead or stalled agent, not an empty deliverable. Name it and the recovery
// route in the RETURN value: the work it paid for sits in its spill file or transcript, and
// `/salvage_fanout` lives in commands/, which a caller reaching this engine directly never read.
// A dead agent still yields the truthy pair [label, null], so filter(Boolean) keeps it and it
// lands in `out` as label: null — test the VALUE, not membership.
const NO_RETURN_JOBS = jobs.filter(j => !(j.label in out) || out[j.label] == null)
const NO_RETURN = NO_RETURN_JOBS.map(j => j.label)
let noReturn = null
if (NO_RETURN.length > 0) {
  const route = NO_RETURN_JOBS.map(j => j.label + ' -> ' + (spills(j) ? spillPath(j.label) + ' else ' : '') + '/salvage_fanout <transcriptDir> ' + j.label).join('; ')
  log('NO-RETURN ' + NO_RETURN.join(', ') + ' — recover before re-dispatching: ' + route)
  noReturn = { labels: NO_RETURN, recover: route }
}
// `noReturn` rides beside spillDir/digests, never inside `digests` (callers iterate it as labels).
// The recovery route leads the result, so a truncated preview still shows how to land inline returns.
const inlineRecover = INLINE_LABELS.length > 0
  ? 'No Write tool for ' + INLINE_LABELS.join(', ') + ': their deliverables came back inline. Land them with: python3 .claude/tools/dispatch_result.py <this run\'s transcript dir> --out-dir ' + SPILL_DIR
  : null


// CLOSE-CHECK-BEGIN
function repoRootOf(a) {
  const root = a && typeof a.__repoRoot === 'string' ? a.__repoRoot.trim().replace(/\\/g, '/').replace(/\/+$/, '') : ''
  return /^([A-Za-z]:)?\//.test(root) ? root : ''
}
function rootedPath(a, p) {
  const root = repoRootOf(a)
  return root && typeof p === 'string' && !/^([A-Za-z]:)?[\\/]/.test(p) ? root + '/' + p.replace(/^\.\//, '') : p
}
function cdRootPrefix(a) {
  const root = repoRootOf(a)
  return root ? "cd '" + root.replace(/'/g, "'\"'\"'") + "' && " : ''
}
const VERDICTS = ['VERIFIED', 'UNVERIFIED', 'UNCHECKED']
const applyVerdicts = (items, verdicts, keyOf) => items.map(item => {
  const supplied = verdicts[keyOf(item)]
  const verdict = VERDICTS.includes(supplied) ? supplied : 'UNCHECKED'
  return Object.assign({}, item, { verdict, confidence: verdict === 'VERIFIED' ? 'verified' : 'unverified' })
})
const boundedVerification = verification => ({
  status: verification.status, counts: verification.counts, report: verification.report,
  ...(verification.note ? { note: verification.note } : {}),
  nonVerified: Object.keys(verification.verdicts).filter(key => verification.verdicts[key] !== 'VERIFIED'),
})
// A session that predates the generated verifier type rejects it; the verifier retries once as general-purpose.
const verifierAgent = (prompt, opts) => agent(prompt, opts).catch(e => {
  const msg = String((e && e.message) || e)
  if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
  log('FALLBACK ' + opts.label + ' ' + opts.agentType + '→general-purpose')
  return agent(prompt, Object.assign({}, opts, { agentType: 'general-purpose' }))
})
const closeCheck = async (mode, { items, keyOf, out, root = A.root, spillDir, expectTotal, expectSources, ledger, fold, seats = [] }) => {
  const keys = items.map(keyOf)
  if (keys.some(key => typeof key !== 'string' || !key.trim() || /(?:undefined|null)#|#(?:undefined|null)$/.test(key))
    || new Set(keys).size !== keys.length) throw new Error('closeCheck: duplicate or missing source key')
  const quoteArg = value => "'" + String(value).replace(/'/g, "'\"'\"'") + "'"
  const identity = JSON.stringify([mode, items.map(keyOf)])
  let hash = 2166136261
  for (let i = 0; i < identity.length; i++) hash = Math.imul(hash ^ identity.charCodeAt(i), 16777619)
  const label = 'verify-' + (hash >>> 0).toString(36)
  out = rootedPath(A, out.replace(/\.json$/, '-' + label + '.json'))
  // The verifier may report --out resolved (absolute, either separator) and may trail prose; the report must LEAD with the file.
  const reportsOut = report => {
    const lead = String(report).replace(/\\/g, '/').trim().match(/^(.*?\.json)(?![\w-])/), o = out.replace(/\\/g, '/').replace(/^\.\//, '')
    return !!lead && (lead[1] === o || lead[1].endsWith('/' + o))
  }
  const profile = A.__toolProfiles && A.__toolProfiles.verify
  const transport = A.__transport
  const checker = A.checker || {}
  const model = checker.model
  const effort = checker.effort
  if (typeof model !== 'string' || !Array.isArray(transport.ids) || !transport.ids.includes(model)) {
    throw new Error('closeCheck: args.checker.model is missing or outside args.__transport.ids; refusing the checked route')
  }
  if (Array.isArray(transport.efforts) && !transport.efforts.includes(effort)) {
    throw new Error('closeCheck: args.checker.effort is missing or outside args.__transport.efforts; refusing the checked route')
  }
  const schema = {
    type: 'object', required: ['status', 'counts', 'verdicts', 'report'], additionalProperties: false,
    properties: {
      status: { type: 'string', enum: ['pass', 'fail', 'unavailable'] },
      counts: { type: 'object', required: VERDICTS, additionalProperties: false,
        properties: Object.fromEntries(VERDICTS.map(v => [v, { type: 'integer', minimum: 0 }])) },
      verdicts: { type: 'object', additionalProperties: { type: 'string', enum: VERDICTS } },
      report: { type: 'string' },
      note: { type: 'string' },
    },
  }
  let verification
  try {
    if (!profile || !profile.agentType || profile.tier !== 'fanout') throw new Error('missing verifier fanout profile')
    let command = 'python3 .claude/tools/post_check.py ' + mode
      + (fold && fold.findings ? ' --findings ' + quoteArg(fold.findings) : ' --run-label ' + label)
      + ' --out ' + (/^[\w./:-]+$/.test(out) ? out : quoteArg(out))
    if (fold && fold.findings) {
      if (fold.digest) command += ' --digest ' + quoteArg(fold.digest)
      if (fold.snapshot) command += ' --snapshot ' + quoteArg(fold.snapshot)
    }
    if (root) command += ' --root ' + quoteArg(root)
    if (spillDir) command += ' --spill-dir ' + quoteArg(rootedPath(A, spillDir))
    if (expectTotal !== undefined) command += ' --expect-total ' + expectTotal
    if (expectSources !== undefined) command += ' --expect-sources ' + expectSources
    if (ledger) {
      for (const [field, flag] of Object.entries({stage: 'stage', subject: 'subject', roster: 'roster', scopeJson: 'scope-json', task: 'task', premiseCommits: 'premise-commits', head: 'head', blobs: 'blobs'})) {
        if (ledger[field]) command += ' --' + flag + ' ' + quoteArg(ledger[field])
      }
      if (ledger.noNewDecision) command += ' --no-new-decision'
    }
    const seatsFile = out.replace(/\.json$/, '.seats.json')
    const launched = Object.fromEntries(seats.map(seat => [seat.label, {
      model: seat.model, effort: seat.effort, tier: seat.tier || null,
      mandates: seat.mandates || [], mustRead: seat.mustRead || [],
    }]))
    if (new Set(seats.map(seat => seat.label)).size !== seats.length) throw new Error('duplicate launched seat label')
    const writer = 'import sys; from pathlib import Path; p=Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[2] + "\\n", encoding="utf-8", newline="\\n")'
    if (mode === 'review') command += ' --seats-file ' + quoteArg(seatsFile)
    const printer = 'import json, sys; r = json.load(open(sys.argv[1], encoding="utf-8")); print(json.dumps({"status": r["status"], "counts": r["counts"], "verdicts": {k: v["verdict"] for k, v in r["verdicts"].items()}}))'
    command = cdRootPrefix(A) + 'python3 -c ' + quoteArg(writer) + ' ' + quoteArg(seatsFile) + ' ' + quoteArg(JSON.stringify(launched)) + ' && ' + command
      + ' ; python3 -c ' + quoteArg(printer) + ' ' + quoteArg(out)
    log('PINS ' + JSON.stringify({[label]: model + '/' + effort + '/' + profile.agentType + ' tier:fanout'}))
    verification = await verifierAgent('Run exactly this command with Bash: ' + command
      + '\nIts last output line is one JSON object holding status, counts and verdicts, read from the --out file; it prints even when the check exits 1.'
      + ' Return ONLY {status, counts, verdicts, report, note}: copy status, counts and verdicts from that line unchanged. Set report to the --out path and nothing else; put any observation in note (optional).'
      + ' Do not check evidence yourself, change permissions, or run another command.',
    { label, phase: 'Verify', agentType: profile.agentType, model, effort, schema })
    if (!verification || !['pass', 'fail'].includes(verification.status) || !reportsOut(verification.report)
      || !verification.verdicts || !verification.counts
      || items.some(item => !VERDICTS.includes(verification.verdicts[keyOf(item)]))) throw new Error('incomplete verifier report')
    const reported = Object.values(verification.verdicts)
    if (reported.some(v => !VERDICTS.includes(v))) throw new Error('illegal verifier verdict')
    const observed = Object.fromEntries(VERDICTS.map(v => [v, reported.filter(verdict => verdict === v).length]))
    if (VERDICTS.some(v => verification.counts[v] !== observed[v])
      || new Set(items.map(keyOf)).size !== items.length
      || (verification.status === 'pass' && (observed.UNVERIFIED > 0 || observed.UNCHECKED > 0))) throw new Error('inconsistent verifier report')
    verification.report = out  // the checker's text only proves it read this file; readers open the engine's own path
  } catch (e) {
    verification = { status: 'unavailable', counts: { VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: items.length },
      verdicts: Object.fromEntries(items.map(item => [keyOf(item), 'UNCHECKED'])), report: out,
      note: String((e && e.message) || e) }
  }
  return { items: applyVerdicts(items, verification.verdicts, keyOf), verification }
}
// CLOSE-CHECK-END

const CLAIM_FENCE = /```(?:claims|findings)[ \t]*\r?\n([\s\S]*?)```/g
const inlineClaims = []
for (const [label, result] of Object.entries(out)) {
  if (typeof result !== 'string') continue
  let index = 0
  for (const match of result.matchAll(CLAIM_FENCE)) {
    for (const line of match[1].split(/\r?\n/).filter(line => line.trim())) {
      index++
      try {
        const claim = JSON.parse(line)
        if (claim && typeof claim === 'object' && !Array.isArray(claim)) inlineClaims.push({ label, index, claim })
      } catch (e) { /* The deterministic report records malformed rows. */ }
    }
  }
}
const reviewJob = jobs.find(job => job.fold || job.ledger)
const declared = A.__returns && Object.keys(A.__returns).length > 0
const hasFence = Object.values(out).some(value => typeof value === 'string' && /```(?:claims|findings)[ \t]*\r?\n/.test(value))
let verification = null
if (declared || hasFence || reviewJob) {
  const claimItems = inlineClaims.map(row => Object.assign({}, row.claim, { __key: String(row.claim.id || row.label + '#' + row.index) }))
  // A fence no job declared reaches here without a validated args.checker: the run still returns, every claim
  // UNCHECKED and the reason named, instead of throwing after every job has spent.
  const unpinnedVerifier = !checkerOk && {
    items: applyVerdicts(claimItems, Object.fromEntries(claimItems.map(item => [item.__key, 'UNCHECKED'])), claim => claim.__key),
    verification: { status: 'unavailable', counts: { VERIFIED: 0, UNVERIFIED: 0, UNCHECKED: claimItems.length },
      verdicts: Object.fromEntries(claimItems.map(item => [item.__key, 'UNCHECKED'])),
      report: 'no verifier ran: a job returned a claims fence and args.checker was not pinned (orchestration §5)' },
  }
  const checked = unpinnedVerifier || await closeCheck(reviewJob ? 'review' : 'claims', {
    items: claimItems,
    keyOf: claim => claim.__key,
    out: (SPILL_DIR || '.claude/scratch') + (reviewJob ? '/post_check_review.json' : '/post_check_claims.json'),
    spillDir: SPILL_DIR,
    fold: reviewJob && reviewJob.fold,
    ledger: reviewJob && reviewJob.ledger,
    seats: jobs.map(j => Object.assign({}, j, { tier: tierOfJob(j) })),
  })
  verification = checked.verification
  let position = 0
  for (const [label, result] of Object.entries(out)) {
    if (typeof result !== 'string') continue
    out[label] = result.replace(CLAIM_FENCE, (fence, body) => fence.replace(body, body.split(/\r?\n/).map(line => {
      if (!line.trim()) return line
      try {
        const claim = JSON.parse(line)
        if (!claim || typeof claim !== 'object' || Array.isArray(claim)) return line
        const applied = Object.assign({}, checked.items[position++])
        delete applied.__key
        return JSON.stringify(applied)
      } catch (e) { return line }
    }).join('\n')))
  }
}
const returned = SPILL_DIR
  ? Object.assign(inlineRecover ? { inlineRecover } : {}, { spillDir: SPILL_DIR, digests: out }, INLINE_LABELS.length > 0 ? { inlineLabels: INLINE_LABELS } : {}, noReturn ? { noReturn } : {})
  : (noReturn ? Object.assign({ noReturn }, out) : out)
return verification ? Object.assign(returned, { verification }) : returned
