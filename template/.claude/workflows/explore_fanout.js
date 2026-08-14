export const meta = {
  name: 'explore-fanout',
  description: 'Generic read-only exploration fan-out engine: dispatch N caller-supplied discovery lenses in parallel over a shared CONTEXT, then deterministic consolidation into a claims dossier — corroboration merge, cross-lens contradiction detection, and confidence downgrade for unbacked claims. Returns claims + contradictions + flags; lens selection, trigger gating, and every downstream decision stay with the calling command.',
  phases: [
    { title: 'Explore', detail: 'dispatch each supplied lens mandate in parallel (read-only + single-flight guard appended)' },
    { title: 'Consolidate', detail: 'stamp lens, merge corroborations, detect contradictions, downgrade unbacked claims, rank by bearing' },
  ],
}

// Platform contract: args may arrive as a JSON string or an already-parsed value. A bare non-JSON
// string is a caller mistake, but an uncaught SyntaxError names neither this workflow nor the
// expected shape, so the caller cannot self-correct and retries the same way. Fail legibly instead.
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'explore-fanout: args must be JSON-serializable {lenses: [{key, promptPath, model, effort}, ...], contextPrefixPath}. Received a non-JSON string. ' + ((e && e.message) || '') }
}
const lenses = Array.isArray(A.lenses) ? A.lenses : []

// Return-path spill (optional, mirrors dispatch.js): with args.spillDir set, every lens writes its
// COMPLETE deliverable to <spillDir>/<key>.spill.md BEFORE attempting structured output, so a schema
// rejection can never destroy completed work — the file survives and the caller recovers from it
// instead of re-dispatching. Measured 2026-08-08: three lenses rejected 5x each on length caps with
// complete deliverables on disk, then re-bought the exploration.
const SPILL_DIR = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const spillPath = (key) => SPILL_DIR + '/' + String(key).replace(/[^A-Za-z0-9._-]/g, '_') + '.spill.md'
if (SPILL_DIR) {
  const seen = {}
  const collided = lenses.filter(l => { const p = spillPath(l.key); const dup = !!seen[p]; seen[p] = true; return dup })
  if (collided.length > 0) {
    return { error: 'args.spillDir is set, but these lens keys collide after filename sanitization ([^A-Za-z0-9._-] -> _) and would overwrite each other: ' + collided.map(l => l.key).join(', ') + '.' }
  }
  log('SPILL-DIR ' + SPILL_DIR)
}

// Endpoint pins — hooks/model_pin_translate.py injects __pin off-Anthropic; identity when absent.
// Anthropic names stay the canonical vocabulary, so validation below is untouched. Inlined per
// script because the Workflow sandbox has no require/import.
const PIN = (m) => (A.__pin && A.__pin.roles && A.__pin.roles[m]) || (A.__pin && A.__pin.model) || m
const EFF = (e) => (A.__pin && A.__pin.effort && A.__pin.effort[e]) || e

// Strict, matching dispatch.js and worklog_relevance.js rather than review_fanout.js's floor: which
// lens runs where is a budget-posture + ladder decision the CALLER makes, and exploration is the
// most frequently-dispatched surface in the harness, so a silent default here would quietly bill the
// whole floor to the wrong provider on every drive.
const VALID_MODELS = ['opus', 'sonnet', 'haiku', 'fable']
const VALID_EFFORTS = ['low', 'medium', 'high', 'xhigh']

const bad = lenses.filter(l => !l || !l.key || !(l.promptPath || l.prompt) || !VALID_MODELS.includes(l.model) || !VALID_EFFORTS.includes(l.effort))
if (lenses.length === 0 || bad.length > 0) {
  return {
    error: 'Every lens needs {key, promptPath (or prompt), model, effort} with model in [' + VALID_MODELS.join('|') + '] and effort in [' + VALID_EFFORTS.join('|') + ']. Mandates live in .claude/commands/agents/explore_agents.md (or .claude/commands/research.md for res-* lenses) — write the resolved text to a scratchpad file and pass its path; never inline a large mandate into args (gotcha_workflow_args_generation_fidelity).',
    badLenses: bad.map(l => (l && l.key) || '(unkeyed)'),
  }
}

// Rails appended to EVERY lens, in two layers matching review_fanout.js. DOCTRINE (what a good
// survey looks like — routing, and the three absence gotchas) lives in .claude/guards/{any,survey}.md,
// ONE home shared with dispatch.js and hooks/session_model_rails.py; this engine injects a reference,
// never a copy. MACHINE SAFETY + the output contract stay inline because they are this engine's own
// invariants (read-only by construction, it owns CLAIMS_SCHEMA, it knows the lens count) and must
// hold even at the tier where the doctrine reference is suppressed.
const TIER_OF = { sonnet: 'strict', haiku: 'strict', opus: 'terse', fable: 'none' }
// Off-Anthropic the RECEIVING model is deepseek whatever role name was pinned — strict band.
const tierOf = (m) => A.__pin ? 'strict' : (TIER_OF[m] || 'strict')
const CONCURRENT = lenses.length > 1
// The read-only line below is prompt-level. Its advisory backstop is armed OUTSIDE this script by
// .claude/hooks/readonly_marker_arm.py (a Workflow script has no filesystem, require, or clock).
const BASE_CONTRACT = [
  '',
  '=== ENGINE CONTRACT ===',
  SPILL_DIR ? 'Read-only: do NOT modify, create, or delete any file EXCEPT the single spill file named in the SPILL-BEFORE-VALIDATE contract below.' : 'Read-only: do NOT modify, create, or delete any file.',
  CONCURRENT ? 'You are one of several lenses running CONCURRENTLY: do NOT run tests, builds, or /regression_gate (the GdUnit4 named pipe is machine-wide single-flight), and do NOT use the csharp-ls LSP (single-flight wrapper) — anchor with Grep and Read instead. If your mandate needs a test run or call-site enumeration via the LSP, report it as a gap; it needs a serialized dispatch.' : null,
  'You report STATE, never advice. A claim says what IS; it never says what should be built, fixed, or preferred. Recommendations are the orchestrator\'s to make from your claims.',
  '',
  '=== CLAIMS CONTRACT (the schema validates shape; these are the rules it cannot express) ===',
  '`subject` — the thing the claim is about (symbol, path, doc heading, rule name). Use the SAME string another lens would use for the same thing: the engine groups on it to detect corroboration and contradiction.',
  '`claim` — one or two sentences of fact (~350 chars target). No rationale, no recommendation.',
  '`polarity` — `exists` (present, does what the claim says) · `absent` (searched for and genuinely not there) · `partial` (present but incomplete or divergent — usually the truest answer, prefer it over forcing exists/absent) · `unclear` (you could not settle it; say why in the claim).',
  '`evidence` — REQUIRED for `exists`/`partial`: VERBATIM tool output (a grep line, a signature, a quoted sentence), not your summary. Paraphrase survives fabrication; raw output rarely does. A single quoted line is a strong claim; a long quote is a weaker one.',
  '`verification` — REQUIRED for `absent`: the command that PROVES absence, not a search that came back empty. `ls`/`git ls-tree` on the directory, `git branch -a` + `git grep <pat> <branch>` for unmerged work, `git check-ignore -v <path>` for a gitignored dir.',
  '`bearing` — `premise-contradiction` (the topic assumes something the code refutes — the highest-value claim you can return; surface it even when it makes the rest of your sweep moot) · `reuse-candidate` (something already owns this concern) · `constraint` (a rule/invariant/gotcha the plan must respect) · `blast-radius` (a consumer a change would break) · `context` (orienting only — keep these few).',
  '`confidence` — `verified` only if you ran the tool and read the output yourself; otherwise `unverified`. The engine downgrades any claim whose evidence/verification does not back it up, so overclaiming gains you nothing.',
  '`checked` — provenance that makes an empty result falsifiable: every search you issued in `toolsUsed` as `tool:target`, where you stopped, and one short sentence of basis (~250 chars target). An empty `claims` with an empty `toolsUsed` is reported as a lens that DID NOT RUN, not as a clear result.',
  '`gaps` — anything your mandate could not establish. Silence here reads as full coverage.',
  'OUTPUT: return ONLY the JSON object `{"claims": [...], "checked": {...}}` — no prose around it. The length targets above are SOFT: the schema imposes no caps, and a shorter object always beats a longer one.',
].filter(l => l !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3). Shape is fixed `survey`:
// this engine is discovery-shaped by construction, so unlike dispatch.js there is nothing to select.
const guardRef = (m) => {
  const tier = tierOf(m)
  if (tier === 'none') { return BASE_CONTRACT }
  return BASE_CONTRACT + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/survey.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n')
}

// Spill-before-validate: the deliverable is written to disk BEFORE the structured-output attempt, so
// a validation rejection can never destroy completed work (measured 2026-08-08 — three lenses
// rejected 5x each with complete deliverables on disk). The caller recovers the file instead of
// re-dispatching; on success the file is redundant and may be ignored.
const spillContract = (l) => SPILL_DIR ? [
  '',
  '=== SPILL-BEFORE-VALIDATE (your deliverable must survive even if validation rejects it) ===',
  'Use the Write tool to save your COMPLETE deliverable — the full claims JSON object you will return, nothing trimmed — to ' + spillPath(l.key) + ' BEFORE you call the structured-output tool. That file is yours alone; no other lens writes it.',
  'The file must contain the same claims/checked/gaps content you return: it is a safety net, not a draft. If validation rejects your return, the orchestrator recovers this file instead of re-dispatching you.',
].join('\n') : ''

// Parity-checked against explore_fanout.schema.json (the sidecar path's -S file) by
// `node .claude/scripts/schema_parity.js` — see that script's header. Edit BOTH or the check fails.
//
// DESCRIPTIONS ARE DELIBERATELY ABSENT. Measured 2026-08-05: a first version carrying the full field
// contract as `description` strings (~4KB) was rejected at dispatch by every agent with "output schema
// too large to classify safely" — all four lenses died before running. The contract lives in
// .claude/commands/agents/explore_agents.md §Claims Schema, which every mandate points at.
//
// NO maxLength ANYWHERE. The first version capped basis at 300 and claim at 400; measured 2026-08-08,
// the three widest lenses were rejected 5x each on exactly those caps (every rejection cites
// "/checked/basis: must NOT have more than 300 characters") with COMPLETE deliverables on disk, then
// re-dispatched — the exact failure class feedback_schema_caps_must_not_invalidate_delegate_work
// exists to prevent. The schema here is validation only (types, enums, required); length lives in the
// CLAIMS CONTRACT as soft targets, where a long-but-correct answer degrades gracefully instead of
// failing closed.
// SCHEMA-SSOT-BEGIN
const CLAIMS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['claims', 'checked'],
  properties: {
    checked: {
      type: 'object',
      additionalProperties: false,
      required: ['toolsUsed', 'stoppedAt', 'basis'],
      properties: {
        toolsUsed: { type: 'array', items: { type: 'string' } },
        stoppedAt: { type: 'string', enum: ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked'] },
        basis: { type: 'string' },
      },
    },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['subject', 'polarity', 'claim', 'bearing'],
        properties: {
          subject: { type: 'string' },
          polarity: { type: 'string', enum: ['exists', 'absent', 'partial', 'unclear'] },
          claim: { type: 'string' },
          evidence: { type: ['string', 'null'] },
          verification: { type: ['string', 'null'] },
          file: { type: ['string', 'null'] },
          bearing: { type: 'string', enum: ['premise-contradiction', 'reuse-candidate', 'constraint', 'blast-radius', 'context'] },
          confidence: { type: 'string', enum: ['verified', 'unverified'] },
        },
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
  },
}
// SCHEMA-SSOT-END

// `labelPrefix` namespaces the PINS log so a /research fan-out through this engine is separable from
// an /explore one in /orchestration_metrics. Cosmetic to the run, load-bearing to the cost record.
const LABEL = typeof A.labelPrefix === 'string' && A.labelPrefix.trim() ? A.labelPrefix.trim() : 'explore'
const resolved = lenses.map(l => ({ ...l, label: LABEL + ':' + l.key }))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(l => [l.label, PIN(l.model) + '/' + EFF(l.effort) + ' guards:survey@' + tierOf(l.model)]))))
if (A.__pin) log('ENDPOINT-TRANSLATED: ' + resolved.map(l => l.model + '->' + PIN(l.model) + '/' + EFF(l.effort)).join(', '))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST (retry once if the read fails). It ORIENTS you; it never caps what you investigate, and every fact in it is a claim to confirm first-party, not ground truth.\n\n'
  : ''

phase('Explore')
const raw = await parallel(resolved.map(l => () => {
  const body = l.promptPath
    ? 'Your full lens mandate is at: ' + l.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails).'
    : (l.prompt || '')
  return agent(contextPre + body + spillContract(l) + guardRef(l.model), {
    label: l.label, phase: 'Explore', schema: CLAIMS_SCHEMA, model: PIN(l.model), effort: EFF(l.effort),
  }).then(r => ({ key: l.key, result: r }))
}))

phase('Consolidate')
// A lens that died or returned nothing is NOT a lens that found nothing. Distinguishing them is the
// whole point of `checked`; a null return has no `checked` at all, so it is its own flag class.
const flags = []
const gaps = []
const perLens = []
const stamped = []
for (const r of raw) {
  if (!r) { flags.push({ kind: 'lens-no-return', lens: '(unknown)', detail: 'the engine received no result object for one lens — treat its dimension as UNCOVERED' }); continue }
  const res = r.result
  if (!res || typeof res !== 'object') {
    flags.push({ kind: 'lens-no-return', lens: r.key, detail: 'lens returned no schema object — its dimension is UNCOVERED, not clear' + (SPILL_DIR ? '. Recover the deliverable from ' + spillPath(r.key) + ' (or the agent transcript) BEFORE re-dispatching' : '') })
    perLens.push({ key: r.key, claims: 0, stoppedAt: null, basis: null })
    continue
  }
  const claims = Array.isArray(res.claims) ? res.claims : []
  const checked = (res.checked && typeof res.checked === 'object') ? res.checked : null
  if (Array.isArray(res.gaps)) { for (const g of res.gaps) { if (typeof g === 'string' && g.trim()) { gaps.push({ lens: r.key, gap: g }) } } }
  perLens.push({
    key: r.key, claims: claims.length,
    stoppedAt: checked ? (checked.stoppedAt || null) : null,
    basis: checked ? (checked.basis || null) : null,
    toolsUsed: (checked && Array.isArray(checked.toolsUsed)) ? checked.toolsUsed.length : 0,
  })
  // An empty result is credible only against its provenance. `trigger-not-met` is the legitimate
  // empty (the lens was dispatched but its precondition did not hold); zero claims with zero tool
  // calls and no such declaration is a lens that did not run, which must never read as "all clear".
  if (claims.length === 0) {
    const st = checked ? checked.stoppedAt : null
    const tools = (checked && Array.isArray(checked.toolsUsed)) ? checked.toolsUsed.length : 0
    if (st === 'trigger-not-met') {
      flags.push({ kind: 'lens-trigger-not-met', lens: r.key, detail: (checked && checked.basis) || 'precondition did not hold' })
    } else if (st === 'blocked' || tools === 0 || !checked) {
      flags.push({ kind: 'lens-did-not-run', lens: r.key, detail: 'zero claims with ' + tools + ' recorded tool calls (stoppedAt=' + (st || 'UNREPORTED') + '). Its dimension is UNCOVERED — re-dispatch or cover it inline before treating the topic as explored.' })
    } else {
      flags.push({ kind: 'lens-empty-verified', lens: r.key, detail: 'swept ' + tools + ' targets and found nothing (stoppedAt=' + st + ')' })
    }
  }
  for (const c of claims) {
    if (!c || typeof c !== 'object' || typeof c.subject !== 'string') { continue }
    stamped.push({ ...c, lens: r.key })
  }
}

// Confidence is engine-enforced, not self-reported. A lens claiming `verified` without the field that
// backs it up is the fabrication shape these two rules exist to catch
// (feedback_delegate_output_trust): presence needs quoted output, absence needs a proving command.
const nonEmpty = (s) => typeof s === 'string' && s.trim().length > 0
for (const c of stamped) {
  if ((c.polarity === 'exists' || c.polarity === 'partial') && !nonEmpty(c.evidence)) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-no-evidence', lens: c.lens, detail: c.subject + ' — claimed verified with no quoted evidence' }) }
    c.confidence = 'unverified'
  }
  if (c.polarity === 'absent' && !nonEmpty(c.verification)) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-unproven-absence', lens: c.lens, detail: c.subject + ' — absence claimed verified with no proving command' }) }
    c.confidence = 'unverified'
  }
  if (c.confidence !== 'verified' && c.confidence !== 'unverified') { c.confidence = 'unverified' }
}

// Group on SUBJECT ONLY — never on `file`. review_fanout.js dedups review findings by `file:line`
// because a finding IS a location; a claim is not. `file` is merely where the evidence lives, and
// several unrelated claims legitimately share one file. Measured on the maiden run (2026-08-05):
// keying on `file` produced TWO false contradictions — three unrelated claims about plan_drive.md, and
// "the ExitPlanMode hook exists" versus "no write-lock exists" in settings.json — because a shared
// location was read as a shared subject. The ACTION also differs from review_fanout: two lenses
// reaching the same fact independently is corroboration worth keeping, not a duplicate to drop.
const normKey = (s) => {
  let k = s.trim().toLowerCase().replace(/\s+/g, ' ')
  // A path-like subject is the same thing whether a lens wrote the bare filename or a repo-relative
  // path — the same maiden run MISSED a genuine contradiction because one lens said
  // `feedback_x.md` and another said `auto-memory/feedback_x.md`. Collapse to the basename. A
  // basename collision between two genuinely different files surfaces as a contradiction to
  // adjudicate, which is the safe direction to fail in.
  if (k.includes('/') && /\.[a-z0-9]{1,5}$/.test(k)) { k = k.slice(k.lastIndexOf('/') + 1) }
  return k
}
const keyOf = (c) => normKey(c.subject)
const groups = new Map()
for (const c of stamped) {
  const k = keyOf(c)
  if (!groups.has(k)) { groups.set(k, []) }
  groups.get(k).push(c)
}

const contradictions = []
const claims = []
for (const [k, group] of groups) {
  const lensesInGroup = [...new Set(group.map(c => c.lens))]
  const polarities = [...new Set(group.map(c => c.polarity))]
  // Disagreement counts only ACROSS lenses. One lens making several claims about a subject is making
  // several points — reading that as self-contradiction was a maiden-run false positive.
  const contested = lensesInGroup.length > 1 && polarities.length > 1
  if (contested) {
    // Two lenses disagree about the state of the same thing. One of them is wrong, and which one is
    // not the engine's call — but an unflagged contradiction silently resolves to whichever claim
    // sorted first, which is the misinformation this engine exists to surface.
    contradictions.push({
      key: k,
      subject: group[0].subject,
      positions: group.map(c => ({ lens: c.lens, polarity: c.polarity, claim: c.claim, confidence: c.confidence, evidence: c.evidence || null, verification: c.verification || null })),
    })
  }
  // Corroboration ANNOTATES; it never merges. An earlier version kept the "best-evidenced" claim per
  // subject and dropped the rest — which silently discarded three distinct claims one lens had made
  // about a single file (measured, maiden run). Ranking among claims is triage, not a correctness
  // filter, so nothing is dropped here, for the same reason review_fanout.js never caps `findings`
  // (feedback_exhaust_review_findings_before_locking).
  for (const c of group) {
    const agreeing = lensesInGroup.filter(l => l !== c.lens && group.some(o => o.lens === l && o.polarity === c.polarity))
    const extra = {}
    if (contested) { extra.contested = true }
    if (agreeing.length > 0) { extra.corroboratedBy = agreeing }
    claims.push({ ...c, ...extra })
  }
}

// Rank by how the claim bears on the plan about to be written: a contradicted premise changes the
// topic, a reuse candidate changes the design, a constraint changes the steps, context changes nothing.
const BEARING = { 'premise-contradiction': 0, 'reuse-candidate': 1, constraint: 2, 'blast-radius': 3, context: 4 }
claims.sort((a, b) => {
  const ca = a.contested ? 0 : 1, cb = b.contested ? 0 : 1
  if (ca !== cb) { return ca - cb }
  const ba = BEARING[a.bearing] ?? 9, bb = BEARING[b.bearing] ?? 9
  if (ba !== bb) { return ba - bb }
  const va = a.confidence === 'verified' ? 0 : 1, vb = b.confidence === 'verified' ? 0 : 1
  return va - vb
})

const counts = {
  lenses: lenses.length,
  claims: claims.length,
  verified: claims.filter(c => c.confidence === 'verified').length,
  contested: claims.filter(c => c.contested).length,
  corroborated: claims.filter(c => c.corroboratedBy).length,
  premiseContradictions: claims.filter(c => c.bearing === 'premise-contradiction').length,
  reuseCandidates: claims.filter(c => c.bearing === 'reuse-candidate').length,
  contradictions: contradictions.length,
  gaps: gaps.length,
  uncoveredLenses: flags.filter(f => f.kind === 'lens-did-not-run' || f.kind === 'lens-no-return').length,
}
log('explore-fanout: ' + counts.lenses + ' lenses → ' + counts.claims + ' claims ('
  + counts.verified + ' verified, ' + counts.premiseContradictions + ' premise-contradiction, '
  + counts.reuseCandidates + ' reuse-candidate), ' + counts.contradictions + ' contradictions, '
  + counts.gaps + ' gaps, ' + counts.uncoveredLenses + ' UNCOVERED lenses')
// Never silent: an uncovered dimension is the one failure that makes the whole dossier misleading.
if (counts.uncoveredLenses > 0) {
  log('WARNING uncovered: ' + flags.filter(f => f.kind === 'lens-did-not-run' || f.kind === 'lens-no-return').map(f => f.lens).join(', '))
}

return { claims, contradictions, flags, gaps, counts, perLens, spillDir: SPILL_DIR }
