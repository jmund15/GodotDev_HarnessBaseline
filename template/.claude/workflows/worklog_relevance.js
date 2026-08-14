export const meta = {
  name: 'worklog-relevance',
  description: 'Ask ONCE per session whether an open worklog item overlaps the scope about to be worked, without loading the backlog into session context. One delegate reads the titles mirror, verifies candidates against the Obsidian worklog, and returns structured overlaps (empty when nothing matches). Requires JSON args {scope, model, effort} — a bare scope string is rejected, because the model/effort pins are the caller\'s decision and are never inherited.',
  phases: [{ title: 'Relevance', detail: 'one agent judges backlog overlap against the stated scope' }],
}

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string (the natural "just hand it the scope" call) is a caller mistake, but an uncaught
// SyntaxError names neither this workflow nor the expected shape, so the caller cannot self-correct
// and retries the same way. Catch it and fall through to the shape error below, carrying the raw
// string as the scope so the message names only what is actually still missing.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch {
  A = { scope: args }
}

// Endpoint pins — hooks/model_pin_translate.py injects __pin off-Anthropic; identity when absent.
// Anthropic names stay the canonical vocabulary, so validation below is untouched. Inlined per
// script because the Workflow sandbox has no require/import.
const PIN = (m) => (A.__pin && A.__pin.roles && A.__pin.roles[m]) || (A.__pin && A.__pin.model) || m
const EFF = (e) => (A.__pin && A.__pin.effort && A.__pin.effort[e]) || e

// Pins are the CALLER's decision (provider choice is budget-posture, and CLAUDE.md's ladder is the
// only role→model surface). No silent model floor, no session-effort inheritance.
const VALID_MODELS = ['opus', 'sonnet', 'haiku', 'fable']
const VALID_EFFORTS = ['low', 'medium', 'high', 'xhigh']

const scope = (typeof A.scope === 'string') ? A.scope.trim() : ''
const domains = Array.isArray(A.domains) ? A.domains.filter(d => typeof d === 'string' && d.trim()) : []
const promptPath = (typeof A.promptPath === 'string' && A.promptPath.trim())
  ? A.promptPath.trim()
  : '.claude/workflows/worklog_relevance.prompt.md'

if (!scope || !VALID_MODELS.includes(A.model) || !VALID_EFFORTS.includes(A.effort)) {
  return {
    error: 'args needs {scope, model, effort} with a non-empty scope, model in [' + VALID_MODELS.join('|') + '] and effort in [' + VALID_EFFORTS.join('|') + ']. Optional: domains[] to narrow the index scan, promptPath to override the shared mandate.',
  }
}

log('PINS ' + JSON.stringify({ 'worklog:relevance': PIN(A.model) + '/' + EFF(A.effort) }))
if (A.__pin) log('ENDPOINT-TRANSLATED: ' + A.model + '->' + PIN(A.model) + '/' + EFF(A.effort))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

// Parity-checked against worklog_relevance.schema.json (the sidecar path's -S file) by
// `node .claude/scripts/schema_parity.js` — see that script's header. Edit BOTH or the check fails.
// SCHEMA-SSOT-BEGIN
const RELEVANCE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['overlaps', 'checked'],
  properties: {
    // Provenance for the EMPTY case. Without it, an exhaustive check and one that never opened the
    // worklog both return `{overlaps: []}` — indistinguishable, and the empty reads as a clean sweep.
    checked: {
      type: 'object',
      additionalProperties: false,
      required: ['indexRead', 'obsidianOpened', 'shortlisted', 'stoppedAt', 'basis'],
      properties: {
        indexRead: { type: 'boolean', description: 'the titles mirror was read' },
        obsidianOpened: { type: 'boolean', description: 'true ONLY if you consulted Worklog.md itself — Read, or Grep with -C context against that path. A repo search, or reasoning from the titles mirror alone, is false.' },
        shortlisted: { type: 'array', items: { type: 'string' }, description: 'titles carried out of the index scan, BEFORE verification — the audit trail that makes an empty result falsifiable' },
        stoppedAt: { type: 'string', enum: ['triviality-gate', 'empty-shortlist', 'verified-none', 'verified-overlaps'], description: 'where the procedure ended — distinguishes a legitimate early exit from a check that simply did not run' },
        basis: { type: 'string', description: 'ONE sentence: what you examined and why it did or did not clear the overlap bar. This is the result-level justification; it never licenses prose inside overlaps[].' },
      },
    },
    overlaps: {
      type: 'array',
      description: 'Empty when nothing genuinely overlaps — that is the expected common case.',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['title', 'domain', 'relation', 'why', 'recommendation'],
        properties: {
          title: { type: 'string', description: 'worklog item title, copied verbatim' },
          domain: { type: 'string', description: "the '## <domain>' heading the item sits under" },
          relation: { type: 'string', enum: ['same-scope', 'provides-context', 'adjacent'] },
          why: { type: 'string', description: 'one or two sentences naming the shared surface (file, type, .tres, design decision)' },
          recommendation: { type: 'string', enum: ['fold-in', 'read-first', 'note-only'] },
        },
      },
    },
  },
}
// SCHEMA-SSOT-END

phase('Relevance')
const prompt = [
  'Your mandate is at: ' + promptPath + ' — read it with the Read tool and follow it exactly (retry once if the read fails).',
  'That file is the single source of truth for this check; do not substitute your own procedure.',
  '',
  'SCOPE UNDER EVALUATION (use this wherever the mandate says {{SCOPE}}):',
  scope,
  domains.length ? '\nStart the index scan from these worklog domains, but do not treat them as exhaustive: ' + domains.join(', ') : null,
  '',
  '=== READ-ONLY: do NOT modify, create, or delete any file. ===',
  'Return ONLY the JSON object of the schema. An empty `overlaps` array is the correct answer when nothing genuinely overlaps — do NOT explain that you found nothing.',
].filter(l => l !== null).join('\n')

const r = await agent(prompt, { label: 'worklog:relevance', phase: 'Relevance', schema: RELEVANCE_SCHEMA, model: PIN(A.model), effort: EFF(A.effort) })

// Unambiguous empty: `count` is always present, so a caller never has to distinguish "no overlaps"
// from "the agent returned nothing".
const overlaps = (r && Array.isArray(r.overlaps)) ? r.overlaps : []
// `checked` must reach the caller: the schema forces the delegate to declare what it inspected, and
// discarding that here would restore the exact ambiguity it exists to remove — an empty `overlaps`
// with obsidianOpened:false is an incomplete check, not a clean sweep.
const checked = (r && typeof r.checked === 'object' && r.checked) ? r.checked : null
log('OVERLAPS ' + overlaps.length + ' (stoppedAt=' + (checked ? checked.stoppedAt : 'UNREPORTED')
  + ', obsidianOpened=' + (checked ? checked.obsidianOpened : 'UNREPORTED')
  + ', shortlisted=' + (checked && Array.isArray(checked.shortlisted) ? checked.shortlisted.length : '?') + ')')
if (checked && checked.basis) { log('BASIS ' + checked.basis) }
return { count: overlaps.length, overlaps, checked }
