export const meta = {
  name: 'test-agents-fixtures',
  description: 'Run synthetic review and audit agent fixtures',
  phases: [
    { title: 'Dispatch', detail: 'assemble CONTEXT+prompt from structured args; spawn each fixture agent (parallel)' },
    { title: 'Assert', detail: 'extract JSON findings (malformed = FAIL); run the 5-clause matcher' },
    { title: 'Validate', detail: 'validator review on PASS-only; REJECTED overrides to FAIL' },
  ],
}
// POST-CHECK exempt (fixture self-scoring): it scores its own fixtures' output.
// Args: {fixtures: [{id, agent, model, effort?, ...}], effort?, checklists, agentTemplates, findingSchema, validator: {model, effort}}.
// Every fixture pins its own model, and its effort or the call's args.effort when the transport declares efforts.
// args.validator pins the validator that starts on each PASS; it is required and validated before any agent starts.

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'test-agents-fixtures: args must be JSON-serializable {fixtures: [...]}. Received a non-JSON string. ' + ((e && e.message) || '') }
}

// Model vocabulary comes only from args.__transport, which hooks/workflow_provider_guard.py injects on
// every Workflow call: {name, ids, efforts?}. This engine holds no model list, so a
// call the guard did not rewrite fails closed. Inlined per script because the Workflow sandbox has
// no require/import.
const TRANSPORT = A.__transport
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
if (!TRANSPORT || typeof TRANSPORT !== 'object' || !validStringList(TRANSPORT.ids)
  || (TRANSPORT.efforts !== undefined && !validStringList(TRANSPORT.efforts))) {
  return { error: 'test-agents-fixtures: args.__transport is missing or malformed. hooks/workflow_provider_guard.py injects the endpoint\'s model vocabulary ({name, ids, efforts?}); without it this engine has no legal pins and refuses to guess.' }
}
const PIN = (m) => m
const EFF = (e) => e
const fixtures = Array.isArray(A.fixtures) ? A.fixtures : []
const checklists = A.checklists || {}
const agentTemplates = A.agentTemplates || {}
const findingSchema = A.findingSchema || ''

if (fixtures.length === 0) {
  return { error: 'No fixtures in args. /test_agents (Claude) must glob+parse the fixture files and pass them via args.fixtures (workflow scripts cannot read files).' }
}

// Static blocks (Step 2c ARCH RULES, Step 2d.3 test-mode instruction) — embedded, not passed.
const ARCH_RULES = [
  '- Logging: JmoLogger only (Info/Warning/Error). Never GD.Print.',
  '- Control flow: No nested if/else. Use early returns. Always use brackets {}.',
  '- Nullability: [Export] = null! requires [RequiredExport] + ValidateRequiredExports().',
  '- Events: Initialize with = delegate { } to avoid null checks.',
  '- Node retrieval: Use NodeExts (GetFirstChildOfType, TryGetNode). Never GetNode in _Process.',
  '- Interfaces: Nodes interact via Interfaces, not concrete classes.',
  '- Resources: Shared Resources must never cache per-instance mutable state.',
  '- Pool reset: Cleanup that READS references must run BEFORE nullifying.',
  '- DestroyStrategy: onFinished callback must be invoked exactly once.',
  '- Test helpers: Must be wrapped in #if TOOLS / #endif.',
].join('\n')
const TEST_MODE = 'IMPORTANT TEST MODE: All context is provided below. Do NOT use Read, Grep, or Glob tools to access project files — analyze ONLY the provided context. Return your findings as a JSON array and nothing else.'
// The platform relays the triggering user message to every agent as authoritative; an agent under test
// whose fixture prompt looks unrelated can answer that message instead of its prompt.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'

const VALIDATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    validated: { type: 'boolean', description: 'true if findings correctly identify the planted violation and are well-formed' },
    reason: { type: 'string' },
  },
  required: ['validated', 'reason'],
}

// ---- pure helpers ----
function buildContext(fx) {
  const parts = [
    '=== DIFF ===',
    fx.diff || '(none)',
    '',
    '=== FULL FILE CONTENTS ===',
    '--- ' + (fx.filePath || 'unknown') + ' ---',
    fx.code || '',
    '',
    '=== ARCHITECTURE RULES (ABBREVIATED) ===',
    ARCH_RULES,
    '',
    '=== FINDING SCHEMA ===',
    findingSchema,
  ]
  if (fx.transcriptCorrections) {
    parts.push('', '=== TRANSCRIPT CORRECTIONS ===', fx.transcriptCorrections)
  }
  return parts.join('\n')
}

function buildPrompt(fx) {
  const template = agentTemplates[fx.agent] || ''
  const context = buildContext(fx)
  const repl = {
    '{{CONTEXT}}': context,
    '{{PR_NUM}}': 'TEST-000',
    '{{BRANCH}}': 'test/agent-fixture',
    '{{TRANSCRIPT_CORRECTIONS}}': fx.transcriptCorrections || '',
    '{{CHECKLIST_CDS}}': checklists['C+D+S'] || '',
    '{{CHECKLIST_RP}}': checklists['R+P'] || '',
    '{{CHECKLIST_I}}': checklists['I'] || '',
    '{{CODE_QUALITY_CHECKLIST}}': checklists['full'] || '',
    '{{TEST_QUALITY_CHECKLIST}}': checklists['test'] || '',
  }
  let body = template
  for (const k of Object.keys(repl)) { body = body.split(k).join(repl[k]) }
  // If the template lacked a {{CONTEXT}} slot, append context so the agent still has it.
  if (!template.includes('{{CONTEXT}}')) { body = body + '\n\n' + context }
  return TEST_MODE + '\n\n' + body + '\n\n' + RELAY_LINE
}

function extractFindings(text) {
  if (typeof text !== 'string') { return null }
  const start = text.indexOf('[')
  const end = text.lastIndexOf(']')
  if (start === -1 || end === -1 || end < start) { return null }
  try {
    const parsed = JSON.parse(text.slice(start, end + 1))
    return Array.isArray(parsed) ? parsed : null
  } catch (e) { return null }
}

function ciEq(a, b) { return String(a == null ? '' : a).trim().toLowerCase() === String(b == null ? '' : b).trim().toLowerCase() }
function ciIncludes(hay, needle) { return String(hay == null ? '' : hay).toLowerCase().includes(String(needle == null ? '' : needle).toLowerCase()) }

function matchesSpec(f, spec) {
  if (!ciEq(f.action, spec.action)) { return false }
  if (!ciEq(f.category, spec.category)) { return false }
  if (!ciIncludes(f.file, spec.fileContains)) { return false }
  const kws = Array.isArray(spec.keywords) ? spec.keywords : []
  if (kws.length > 0 && !kws.some(k => ciIncludes(f.description, k))) { return false }
  return true
}

function runAssertions(findings, expected, agentName) {
  const min = expected.minCount, max = expected.maxCount
  if (findings.length < min || findings.length > max) {
    return { pass: false, reason: 'Finding count ' + findings.length + ' outside [' + min + ', ' + max + ']' }
  }
  for (const spec of (expected.required || [])) {
    if (!findings.some(f => matchesSpec(f, spec))) {
      return { pass: false, reason: 'No finding matched Required spec: ' + JSON.stringify(spec) }
    }
  }
  for (const spec of (expected.forbidden || [])) {
    if (findings.some(f => matchesSpec(f, spec))) {
      return { pass: false, reason: 'A finding matched Forbidden spec: ' + JSON.stringify(spec) }
    }
  }
  for (const f of findings) {
    if (!ciEq(f.agent, agentName)) {
      return { pass: false, reason: 'Finding agent "' + (f.agent || '') + '" != fixture agent "' + agentName + '"' }
    }
  }
  return { pass: true, reason: '' }
}

function validatorPrompt(fx, findings) {
  const planted = (fx.expected.required || []).map(s => JSON.stringify(s)).join('; ')
  return [
    'You are a test validation agent. Review these findings from agent "' + fx.agent + '" and determine if they correctly identify the planted violation in the test fixture.',
    '',
    '=== FIXTURE CODE ===',
    fx.code || '',
    '',
    '=== PLANTED VIOLATION (Required finding spec) ===',
    planted,
    '',
    '=== AGENT FINDINGS ===',
    JSON.stringify(findings, null, 2),
    '',
    'Evaluate: (1) Do the findings correctly identify the planted violation (not a different issue)? (2) Are they well-formed (proper action/category, sensible rationale)? (3) If a FIX tier, is the fix suggestion actually correct?',
    'Set validated=true only if the findings genuinely identify the planted violation.',
    RELAY_LINE,
  ].join('\n')
}

// Every fixture pins its own model, and its own effort when the transport declares an effort vocabulary.
const VALID_MODELS = TRANSPORT.ids
const VALID_EFFORTS = TRANSPORT.efforts
const pinVocab = 'model in [' + VALID_MODELS.join('|') + ']' + (VALID_EFFORTS ? ' and effort in [' + VALID_EFFORTS.join('|') + ']' : '')
for (const fx of fixtures) {
  if (!fx || typeof fx !== 'object') {
    return { error: 'test-agents-fixtures: every fixture must be an object.' }
  }
}
// A fixture without its own effort takes the call's args.effort: both are the caller's pins.
for (const fx of fixtures) if (fx && fx.effort === undefined && A.effort !== undefined) fx.effort = A.effort
const unpinned = fixtures.filter(fx => !VALID_MODELS.includes(fx.model) || (VALID_EFFORTS !== undefined && !VALID_EFFORTS.includes(fx.effort)))
if (unpinned.length > 0) {
  return { error: 'test-agents-fixtures: pin model and effort on every job (orchestration §5). Unpinned or illegal: '
    + unpinned.map(fx => fx.id || 'fixture').join(', ') + '. Each fixture needs ' + pinVocab + '.',
  badJobs: unpinned.map(fx => fx.id || 'fixture') }
}
// Each PASS starts the validator (validate:<id>), so with any fixture present its pin is required before the first dispatch.
// The validator judges every PASS, so the caller pins it (args.validator); it never takes the guard-filled args.checker.
const validatorOk = !!A.validator && typeof A.validator === 'object' && VALID_MODELS.includes(A.validator.model)
  && (VALID_EFFORTS === undefined || VALID_EFFORTS.includes(A.validator.effort))
if (!validatorOk) {
  return { error: 'test-agents-fixtures: pin model and effort on every job (orchestration §5). args.validator = {model, effort} is required because the validator starts on every PASS; it must pin ' + pinVocab + '. Refusing before any fixture starts.' }
}

// TOOL PROFILES — the inventory each dispatched agent-under-test and each validator launches with. A
// native child INHERITS the session's MCP set, so every entry is `inherited` with effectiveMcp
// unknown, never a strict claim of enforcement. A validator whose fixture never PASSes is listed but
// never spawned; that is the same set the engine can reach.
const TOOLP = (() => { try { const a = (typeof args === 'string') ? JSON.parse(args || '{}') : (args || {}); return { d: (a && a.__toolProfiles) || {}, f: (a && a.__toolProfileDefaults) || {} } } catch (e) { return { d: {}, f: {} } } })()
const tpOf = (label) => TOOLP.d[label] || TOOLP.f['review'] || null
log('TOOL-PROFILES ' + JSON.stringify(Object.fromEntries(
  fixtures.flatMap(fx => ['dispatch:' + fx.id, 'validate:' + fx.id]).map(l => [l, tpOf(l)]))))

// ---- pipeline: dispatch -> assert -> PASS-only validate ----
const results = await pipeline(
  fixtures,
  // stage 1: dispatch the agent under test (model from fixture; NO schema — must catch malformed JSON as FAIL)
  (fx) => {
    const pinned = fx.effort !== undefined ? { effort: EFF(fx.effort) } : {}
    return agent(buildPrompt(fx), { label: 'dispatch:' + fx.id, phase: 'Dispatch', model: PIN(fx.model), ...pinned })
      .then(response => ({ response }))
  },
  // stage 2: extract findings + 5-clause matcher (pure deterministic code)
  (prev, fx) => {
    const findings = extractFindings(prev.response)
    if (findings === null) {
      return { id: fx.id, agent: fx.agent, pass: false, reason: 'No valid JSON array in response', findingCount: 0, findings: [], validated: null, response: prev.response }
    }
    const a = runAssertions(findings, fx.expected, fx.agent)
    return { id: fx.id, agent: fx.agent, pass: a.pass, reason: a.reason, findingCount: findings.length, findings, validated: null, response: prev.response }
  },
  // stage 3: validation on PASS-only; REJECTED overrides to FAIL
  async (prev, fx) => {
    if (!prev.pass) { return prev }
    const v = await agent(validatorPrompt(fx, prev.findings), { label: 'validate:' + fx.id, phase: 'Validate', model: PIN(A.validator.model), ...(A.validator.effort !== undefined ? { effort: EFF(A.validator.effort) } : {}), schema: VALIDATE_SCHEMA })
    if (!v.validated) {
      return { ...prev, pass: false, reason: 'Validator REJECTED: ' + v.reason, validated: false }
    }
    return { ...prev, validated: true }
  }
)

const clean = results.filter(Boolean)
const passed = clean.filter(r => r.pass).length
const failed = clean.length - passed
return { total: clean.length, passed, failed, results: clean }
