export const meta = {
  name: 'dispatch',
  description: 'Generic pinned dispatch engine: run N caller-supplied jobs with MANDATORY per-job model+effort pins, prompts passed as file paths (args stay small), PINS logging for /orchestration_metrics. The zero-authoring replacement for bare Agent-tool fan-outs.',
  phases: [{ title: 'Dispatch', detail: 'all jobs concurrent (engine queues past the concurrency cap)' }],
}

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'dispatch: args must be JSON-serializable {jobs: [{label, promptPath, model, effort}, ...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
const jobs = Array.isArray(A.jobs) ? A.jobs : []

// Endpoint pins — hooks/model_pin_translate.py injects __pin off-Anthropic; identity when absent.
// Anthropic names stay the canonical vocabulary, so validation below is untouched. Inlined per
// script because the Workflow sandbox has no require/import.
const PIN = (m) => (A.__pin && A.__pin.roles && A.__pin.roles[m]) || (A.__pin && A.__pin.model) || m
const EFF = (e) => (A.__pin && A.__pin.effort && A.__pin.effort[e]) || e

// Strict by design — this engine IS the enforcement point for explicit pins (CLAUDE.md §Model
// Delegation, Workflow-first). No silent model floor, no silent effort default: a missing pin is
// the caller's bug, surfaced loudly. review_fanout.js is the lenient sibling for review lenses.
const VALID_MODELS = ['opus', 'sonnet', 'haiku', 'fable']
const VALID_EFFORTS = ['low', 'medium', 'high', 'xhigh']

const bad = jobs.filter(j => !j || !j.label || !j.promptPath || !VALID_MODELS.includes(j.model) || !VALID_EFFORTS.includes(j.effort))
if (jobs.length === 0 || bad.length > 0) {
  return {
    error: 'Every job needs {label, promptPath, model, effort} with model in [' + VALID_MODELS.join('|') + '] and effort in [' + VALID_EFFORTS.join('|') + ']. Write each prompt to a scratchpad file and pass its path — never inline large prompts into args (gotcha_workflow_args_generation_fidelity).',
    badJobs: bad.map(j => (j && j.label) || '(unlabeled)'),
  }
}

log('PINS ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, PIN(j.model) + '/' + EFF(j.effort)]))))
if (A.__pin) log('ENDPOINT-TRANSLATED: ' + jobs.map(j => j.model + '->' + PIN(j.model) + '/' + EFF(j.effort)).join(', '))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Concurrency safety: with >1 concurrent job, tests and the csharp-ls LSP are machine-wide
// single-flight (gotcha_workflow_single_flight_concurrency). A single job gets no such restriction.
const CONCURRENCY_GUARD = jobs.length > 1 ? [
  '',
  '=== ORCHESTRATION GUARD (you are one of several agents running CONCURRENTLY) ===',
  'Do NOT run tests, builds, or /regression_gate (GdUnit4 named-pipe is machine-wide single-flight). Do NOT use the csharp-ls LSP (single-flight wrapper) — use Grep/Read instead. If your task file mandates a test/build run, STOP and report that it needs a serialized dispatch.',
].join('\n') : ''

// Delegate rails — ONE home (.claude/guards/<shape>.md), three consumers (this engine,
// review_fanout.js, hooks/session_model_rails.py in sidecar-delegate mode). Never inline a copy
// here: two copies is the drift failure the shared file exists to prevent.
// Shape decides which rule families are reachable; the job's own `model` decides how much is spelled
// out (instruction_quality §3, "tier rails by the model that RECEIVES them").
// Unlike model/effort, a missing or unrecognized shape is NOT a caller bug — it means the
// orchestrator asserts no specific shape applies, which is exactly what `any` covers.
const VALID_SHAPES = ['any', 'survey', 'review', 'author']
const TIER_OF = { sonnet: 'strict', haiku: 'strict', opus: 'terse', fable: 'none' }
const shapeOf = (j) => VALID_SHAPES.includes(j.shape) ? j.shape : 'any'
// Off-Anthropic, the RECEIVING model is deepseek whatever role name the caller pinned, and deepseek
// sits in the strict band — so endpoint translation overrides the role-derived tier.
const tierOf = (j) => A.__pin ? 'strict' : (TIER_OF[j.model] || 'strict')
const guardRef = (j) => {
  const tier = tierOf(j)
  if (tier === 'none') { return '' }
  return [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/' + shapeOf(j) + '.md with the Read tool and follow its `## ' + tier + '` section. Read ONLY that section — the other tiers are for other models.',
  ].join('\n')
}
log('SHAPES ' + JSON.stringify(Object.fromEntries(jobs.map(j => [j.label, shapeOf(j) + '/' + tierOf(j)]))))

// Return-path spill (optional): every agent's final text lands in ORCHESTRATOR context permanently,
// so a wide fan-out of verbose deliverables is the dominant driver of context growth per turn. With
// args.spillDir set, each agent writes its full deliverable to disk and returns only a bounded digest;
// the orchestrator Reads the file only for the jobs it actually acts on. Absent spillDir, the inline-
// return contract is unchanged (callers that need the full text in-context keep working untouched).
const SPILL_DIR = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
const spillPath = (label) => SPILL_DIR + '/' + String(label).replace(/[^A-Za-z0-9._-]/g, '_') + '.md'

// The read-only guard must except the spill file, or a readOnly job is told to write and not-write.
// It is prompt-level only, and deliberately has NO marker backstop: a dispatch.js run mixes
// readOnly and author jobs, so arming it would warn on every legitimate authored write.
const readOnlyGuard = (j) => SPILL_DIR
  ? '\n=== READ-ONLY: do NOT modify, create, or delete any file EXCEPT your own spill file ' + spillPath(j.label) + '. ==='
  : '\n=== READ-ONLY: do NOT modify, create, or delete any file. ==='

const spillContract = (j) => SPILL_DIR ? [
  '',
  '=== RETURN-PATH CONTRACT (overrides any "return the full result" wording in your brief) ===',
  'Write your FULL deliverable to ' + spillPath(j.label) + ' using the Write tool. That file is yours alone — no other agent writes it.',
  'Then return ONLY: (a) a digest of at most ' + DIGEST_WORDS + ' words covering what the caller must decide or act on, and (b) a final line exactly `FULL: ' + spillPath(j.label) + '`.',
  'Do NOT restate the full deliverable in your final message. If you could not write the file, say so in the digest instead of pasting the content.',
].join('\n') : ''

// Two labels that sanitize to the same filename would silently overwrite each other's deliverable —
// data loss with no signal, in the one path whose whole point is that the orchestrator never reads
// the full text. Fail loud; the caller renames a label.
if (SPILL_DIR) {
  const seen = {}
  const collided = jobs.filter(j => { const p = spillPath(j.label); const dup = !!seen[p]; seen[p] = true; return dup })
  if (collided.length > 0) {
    return {
      error: 'args.spillDir is set, but these labels collide after filename sanitization ([^A-Za-z0-9._-] -> _) and would overwrite each other: '
        + collided.map(j => j.label).join(', ') + '. Give each job a label that is distinct in those characters.',
    }
  }
  log('SPILL-DIR ' + SPILL_DIR + ' (digest cap ' + DIGEST_WORDS + ' words/job)')
}

phase('Dispatch')
const results = await parallel(jobs.map(j => () => {
  const ctx = A.contextPath
    ? 'SHARED CONTEXT for this dispatch is at: ' + A.contextPath + ' — read it with the Read tool FIRST (retry once if the read fails).\n\n'
    : ''
  const prompt = ctx
    + 'Your full task brief is at: ' + j.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails). Your final message is the deliverable: return the result the brief asks for, self-contained, no meta-commentary.'
    + (j.readOnly ? readOnlyGuard(j) : '')
    + CONCURRENCY_GUARD
    + guardRef(j)
    + spillContract(j)
  return agent(prompt, { label: j.label, phase: 'Dispatch', model: PIN(j.model), effort: EFF(j.effort) })
    .then(r => [j.label, r])
}))

const out = Object.fromEntries(results.filter(Boolean))
return SPILL_DIR ? { spillDir: SPILL_DIR, digests: out } : out
