export const meta = {
  name: 'test-agents-fixtures',
  description: 'Run synthetic agent integration fixtures: assemble CONTEXT+prompt deterministically, dispatch each review/audit agent, apply the 5-clause assertion matcher as code, then PASS-only Haiku validation',
  phases: [
    { title: 'Dispatch', detail: 'assemble CONTEXT+prompt from structured args; spawn each fixture agent (parallel)' },
    { title: 'Assert', detail: 'extract JSON findings (malformed = FAIL); run the 5-clause matcher' },
    { title: 'Validate', detail: 'Haiku review on PASS-only; REJECTED overrides to FAIL' },
  ],
}

// Platform contract: `args` arrives as a JSON STRING. Parse-guard.
const A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
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
  return TEST_MODE + '\n\n' + body
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
  ].join('\n')
}

const VALID_MODELS = ['opus', 'sonnet', 'haiku']

// ---- pipeline: dispatch -> assert -> PASS-only validate ----
const results = await pipeline(
  fixtures,
  // stage 1: dispatch the agent under test (model from fixture; NO schema — must catch malformed JSON as FAIL)
  (fx) => {
    const model = VALID_MODELS.includes(fx.model) ? fx.model : 'sonnet'
    return agent(buildPrompt(fx), { label: 'dispatch:' + fx.id, phase: 'Dispatch', model })
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
  // stage 3: Haiku validation on PASS-only; REJECTED overrides to FAIL
  async (prev, fx) => {
    if (!prev.pass) { return prev }
    const v = await agent(validatorPrompt(fx, prev.findings), { label: 'validate:' + fx.id, phase: 'Validate', model: 'haiku', schema: VALIDATE_SCHEMA })
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
