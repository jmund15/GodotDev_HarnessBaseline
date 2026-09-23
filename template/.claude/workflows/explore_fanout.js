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
const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
if (!['bounded', 'full'].includes(RESULT_MODE)) {
  return { error: 'explore-fanout: resultMode must be `bounded` or `full`.' }
}

// Optional paid-output spill. A Workflow script cannot write files, so only a receiving profile
// with Write may promise one. Runtime journals remain the native durable result source for all profiles.
const SPILL_DIR = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
const NORMAL_SPILL_DIR = SPILL_DIR ? SPILL_DIR.replace(/\\/g, '/') : null
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
if (!spillRootAllowed) {
  return { error: 'explore-fanout: spillDir must be repository-relative and contained by .claude/scratch/. Received: ' + SPILL_DIR }
}
const spillPath = (key) => SPILL_DIR + '/' + String(key).replace(/[^A-Za-z0-9._-]/g, '_') + '.spill.md'
const NO_WRITE_AGENT_TYPES = ['Explore', 'Plan']
const spills = (l) => !!SPILL_DIR && !NO_WRITE_AGENT_TYPES.includes(l.agentType)

// Endpoint vocabulary — hooks/workflow_provider_guard.py injects __transport off-Anthropic.
// Presence means provider mode: incomplete registry data fails before dispatch rather than reopening
// the Anthropic vocabulary. Inlined because the Workflow sandbox has no require/import.
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
  return { error: 'explore-fanout: args.__transport needs non-empty string arrays `ids` and `efforts`, plus a non-empty `default` contained in `ids`.' }
}

// A missing effort takes a bounded floor. A present invalid pin fails before dispatch.
const VALID_MODELS = HAS_TRANSPORT ? TRANSPORT.ids : ['opus', 'sonnet', 'haiku', 'fable']
const VALID_EFFORTS = HAS_TRANSPORT ? TRANSPORT.efforts : ['low', 'medium', 'high', 'xhigh']
const DEFAULT_EFFORT = VALID_EFFORTS.includes('medium') ? 'medium' : VALID_EFFORTS[0]
const hasEffort = (l) => Object.prototype.hasOwnProperty.call(l, 'effort') && l.effort !== undefined
const effortOf = (l) => hasEffort(l) ? l.effort : DEFAULT_EFFORT

const badAgentObjects = lenses.filter(l => !l || typeof l !== 'object' || Array.isArray(l))
if (badAgentObjects.length) {
  return { error: 'explore-fanout: every args.lenses entry must be an object.' }
}
const bad = lenses.filter(l => typeof l.key !== 'string' || !l.key.trim()
  || !(l.promptPath || l.prompt)
  || !VALID_MODELS.includes(l.model)
  || (hasEffort(l) && !VALID_EFFORTS.includes(l.effort))
  || typeof l.agentType !== 'string' || !l.agentType.trim())
if (lenses.length === 0 || bad.length > 0) {
  return {
    error: 'Every lens needs {key, promptPath (or prompt), model, agentType}; optional effort must be in [' + VALID_EFFORTS.join('|') + '] and model in [' + VALID_MODELS.join('|') + ']. Mandates live in .claude/commands/agents/explore_agents.md (or .claude/commands/research.md for res-* lenses) — write the resolved text to a scratchpad file and pass its path; never inline a large mandate into args (gotcha_workflow_args_generation_fidelity).',
    badLenses: bad.map(l => l.key || '(unkeyed)'),
  }
}
const duplicateKeys = lenses.map(l => l.key).filter((key, i, keys) => keys.indexOf(key) !== i)
if (duplicateKeys.length) {
  return { error: 'explore-fanout: duplicate lens key(s): ' + [...new Set(duplicateKeys)].join(', ') }
}
if (SPILL_DIR) {
  const writerPaths = lenses.filter(spills).map(l => spillPath(l.key).toLowerCase())
  const collisions = writerPaths.filter((p, i, paths) => paths.indexOf(p) !== i)
  if (collisions.length) {
    return { error: 'explore-fanout: lens keys collide after spill-path sanitization: ' + [...new Set(collisions)].join(', ') }
  }
  log('SPILL-DIR ' + SPILL_DIR)
}

// Rails appended to EVERY lens, in two layers matching review_fanout.js. DOCTRINE (what a good
// survey looks like — routing, and the three absence gotchas) lives in .claude/guards/{any,survey}.md,
// ONE home shared with dispatch.js and hooks/session_model_rails.py; this engine injects a reference,
// never a copy. MACHINE SAFETY + the output contract stay inline because they are this engine's own
// invariants (read-only by construction, it owns CLAIMS_SCHEMA, it knows the lens count) and hold
// at every guard tier.
// Guard tier per RECEIVING model is registry data (`railTier`), injected as `args.__rails` by
// hooks/workflow_provider_guard.py because a Workflow script cannot read files. A model the map
// does not name, or a call the hook did not rewrite, reads `detailed`: the fail-safe direction.
const RAILS = (A.__rails && typeof A.__rails === 'object') ? A.__rails : {}
const tierOf = (m) => RAILS[m] || 'detailed'
const CONCURRENT = lenses.length > 1
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// The read-only line below is prompt-level. Its advisory backstop is armed OUTSIDE this script by
// .claude/hooks/readonly_marker_arm.py (a Workflow script has no filesystem, require, or clock).
const BASE_CONTRACT = (l) => [
  '',
  '=== ENGINE CONTRACT ===',
  RELAY_LINE,
  spills(l) ? 'Read-only: do NOT modify, create, or delete any file EXCEPT the single spill file named in the SPILL-BEFORE-VALIDATE contract below.' : 'Read-only: do NOT modify, create, or delete any file.',
  CONCURRENT ? 'You are one of several lenses running CONCURRENTLY: do NOT run Godot or C# tests, builds, scripts/verify.ps1 or /regression_gate (the GdUnit4 named pipe and the engine are machine-wide single-flight); Python and Node proofs under .claude/tests/ are not single-flight, so run them. Do NOT use the csharp-ls LSP (single-flight wrapper) — anchor with Grep and Read instead. If your mandate needs a Godot or C# test run or call-site enumeration via the LSP, report it as a gap; it needs a serialized dispatch.' : null,
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
].filter(line => line !== null).join('\n')
// DOCTRINE on top, tiered by the receiving model (instruction_quality §3). Shape is fixed `survey`:
// this engine is discovery-shaped by construction, so unlike dispatch.js there is nothing to select.
const guardRef = (l) => {
  const tier = tierOf(l.model)
  return BASE_CONTRACT(l) + '\n' + [
    '',
    '=== DELEGATE RAILS ===',
    'Read .claude/guards/survey.md with the Read tool and follow its `## ' + tier + '` section, then do the same for the `## ' + tier + '` section of .claude/guards/any.md. Read ONLY those sections — the other tiers are for other models.',
  ].join('\n')
}

// Spill-before-validate: the deliverable is written to disk BEFORE the structured-output attempt, so
// a validation rejection can never destroy completed work (measured 2026-08-08 — three lenses
// rejected 5x each with complete deliverables on disk). The caller recovers the file instead of
// re-dispatching; on success the file is redundant and may be ignored.
const spillContract = (l) => spills(l) ? [
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
const resolved = lenses.map(l => ({ ...l, label: LABEL + ':' + l.key, effort: effortOf(l) }))
log('PINS ' + JSON.stringify(Object.fromEntries(resolved.map(l => [l.label, PIN(l.model) + '/' + EFF(l.effort) + ' guards:survey@' + tierOf(l.model)]))))
if (A.justification) log('EFFORT-JUSTIFICATION: ' + A.justification)

const contextPre = A.contextPrefixPath
  ? 'SHARED CONTEXT for every lens of this dispatch is at: ' + A.contextPrefixPath + ' — read it with the Read tool FIRST (retry once if the read fails). It ORIENTS you; it never caps what you investigate, and every fact in it is a claim to confirm first-party, not ground truth.\n\n'
  : ''

phase('Explore')
const raw = await parallel(resolved.map(l => () => {
  const body = l.promptPath
    ? 'Your full lens mandate is at: ' + l.promptPath + ' — read it with the Read tool and execute it exactly (retry once if the read fails).'
    : (l.prompt || '')
  return agent(contextPre + body + spillContract(l) + guardRef(l), {
    label: l.label, phase: 'Explore', schema: CLAIMS_SCHEMA, model: PIN(l.model), effort: EFF(l.effort),
    agentType: l.agentType,
  }).then(r => ({ key: l.key, result: r }))
}))

phase('Consolidate')
// A lens that died or returned nothing is NOT a lens that found nothing. Distinguishing them is the
// whole point of `checked`; a null return has no `checked` at all, so it is its own flag class.
const flags = []
const gaps = []
const perLens = []
const stamped = []
const isRecord = (value) => !!value && typeof value === 'object' && !Array.isArray(value)
const POLARITIES = ['exists', 'absent', 'partial', 'unclear']
const BEARINGS = ['premise-contradiction', 'reuse-candidate', 'constraint', 'blast-radius', 'context']
const isChecked = (value) => isRecord(value)
  && Array.isArray(value.toolsUsed)
  && value.toolsUsed.every(tool => typeof tool === 'string' && tool.trim())
  && ['nothing-in-scope', 'exhausted-leads', 'trigger-not-met', 'blocked'].includes(value.stoppedAt)
  && typeof value.basis === 'string' && value.basis.trim()
const isClaim = (value) => isRecord(value)
  && typeof value.subject === 'string' && value.subject.trim()
  && POLARITIES.includes(value.polarity)
  && typeof value.claim === 'string' && value.claim.trim()
  && BEARINGS.includes(value.bearing)
  && (value.evidence === undefined || value.evidence === null || typeof value.evidence === 'string')
  && (value.verification === undefined || value.verification === null || typeof value.verification === 'string')
  && (value.file === undefined || value.file === null || typeof value.file === 'string')
  && (value.confidence === undefined || ['verified', 'unverified'].includes(value.confidence))
let nextClaimId = 1
for (let i = 0; i < resolved.length; i++) {
  const lens = resolved[i]
  const r = raw[i]
  const res = r ? r.result : null
  if (!isRecord(res)) {
    const recovery = spills(lens)
      ? 'read ' + spillPath(lens.key)
      : 'run /salvage_fanout <transcriptDir> ' + lens.key
    flags.push({ kind: 'lens-no-return', lens: lens.key, detail: 'lens returned no schema object — its dimension is UNCOVERED, not clear. Recover BEFORE re-dispatching: ' + recovery + ' (the runtime journal/transcript holds any paid-for work)' })
    perLens.push({ key: lens.key, claims: null, stoppedAt: null, basis: null, toolsUsed: null, status: 'failed', rejected: 0 })
    continue
  }
  if (!Array.isArray(res.claims) || !isChecked(res.checked)) {
    flags.push({ kind: 'lens-invalid-shape', lens: lens.key, detail: 'lens returned a malformed claims/checked object — its dimension is UNCOVERED, not a completed zero' })
    perLens.push({ key: lens.key, claims: null, stoppedAt: null, basis: null, toolsUsed: null, status: 'uncovered', rejected: 0 })
    continue
  }
  const checked = res.checked
  let lensGapCount = 0
  if (Array.isArray(res.gaps)) {
    for (const gap of res.gaps) {
      if (typeof gap === 'string' && gap.trim()) {
        gaps.push({ lens: lens.key, gap })
        lensGapCount++
      }
    }
  }
  let rejected = 0
  let valid = 0
  for (let claimIndex = 0; claimIndex < res.claims.length; claimIndex++) {
    const c = res.claims[claimIndex]
    if (!isClaim(c)) {
      rejected++
      flags.push({ kind: 'lens-invalid-entry', lens: lens.key, detail: 'claim entry at index ' + claimIndex + ' fails the required claim shape — skipped; valid neighbors preserved' })
      continue
    }
    stamped.push({ ...c, id: 'C' + nextClaimId++, lens: lens.key })
    valid++
  }
  const coverageStopped = checked.stoppedAt === 'blocked'
    || (checked.toolsUsed.length === 0 && checked.stoppedAt !== 'trigger-not-met')
  const row = {
    key: lens.key,
    claims: valid,
    observed: res.claims.length,
    rejected,
    stoppedAt: checked.stoppedAt,
    basis: checked.basis,
    toolsUsed: checked.toolsUsed.length,
    gaps: lensGapCount,
    status: rejected || lensGapCount || coverageStopped ? 'partial' : 'completed',
  }
  perLens.push(row)
  if (coverageStopped && valid === 0 && checked.stoppedAt !== 'trigger-not-met') {
    row.status = 'uncovered'
  }
  if (checked.stoppedAt === 'blocked') {
    flags.push({ kind: 'lens-blocked', lens: lens.key, detail: 'the lens stopped at a blocked read after ' + valid + ' valid claim(s); its coverage is ' + row.status.toUpperCase() })
  }
  if (lensGapCount > 0) {
    flags.push({ kind: 'lens-gaps', lens: lens.key, detail: lensGapCount + ' explicit gap(s) keep this lens from completed coverage' })
  }
  // An empty result is credible only against its provenance. `trigger-not-met` is the legitimate
  // empty (the lens was dispatched but its precondition did not hold); zero claims with zero tool
  // calls and no such declaration is a lens that did not run, which must never read as "all clear".
  if (valid === 0 && rejected === 0) {
    const st = checked.stoppedAt
    const tools = checked.toolsUsed.length
    if (st === 'trigger-not-met') {
      flags.push({ kind: 'lens-trigger-not-met', lens: lens.key, detail: checked.basis })
    } else if (st === 'blocked' || tools === 0) {
      flags.push({ kind: 'lens-did-not-run', lens: lens.key, detail: 'zero claims with ' + tools + ' recorded tool calls (stoppedAt=' + st + '). Its dimension is UNCOVERED — re-dispatch or cover it inline before treating the topic as explored.' })
    } else if (lensGapCount === 0) {
      flags.push({ kind: 'lens-empty-verified', lens: lens.key, detail: 'swept ' + tools + ' targets and found nothing (stoppedAt=' + st + ')' })
    }
  }
}

// Confidence is engine-enforced, not self-reported. A lens claiming `verified` without the field that
// backs it up is the fabrication shape these two rules exist to catch
// (feedback_delegate_output_trust): presence needs quoted output, absence needs a proving command.
const nonEmpty = (s) => typeof s === 'string' && s.trim().length > 0
const processedClaimIds = new Set()
for (const c of stamped) {
  const reportedConfidence = c.confidence
  if ((c.polarity === 'exists' || c.polarity === 'partial') && !nonEmpty(c.evidence)) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-no-evidence', lens: c.lens, detail: c.subject + ' — claimed verified with no quoted evidence' }) }
    c.confidence = 'unverified'
  }
  if (c.polarity === 'absent' && !nonEmpty(c.verification)) {
    if (c.confidence === 'verified') { flags.push({ kind: 'downgraded-unproven-absence', lens: c.lens, detail: c.subject + ' — absence claimed verified with no proving command' }) }
    c.confidence = 'unverified'
  }
  if (c.confidence !== 'verified' && c.confidence !== 'unverified') { c.confidence = 'unverified' }
  if (c.confidence !== reportedConfidence) processedClaimIds.add(c.id)
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
      positions: group.map(c => ({ lens: c.lens, polarity: c.polarity, claim: c.claim, confidence: c.confidence, evidence: c.evidence || null, verification: c.verification || null, journalSelector: c.id })),
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
    if (contested || agreeing.length > 0) processedClaimIds.add(c.id)
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
  rejected: perLens.reduce((total, lens) => total + (lens.rejected || 0), 0),
  uncoveredLenses: perLens.filter(lens => lens.status === 'failed' || lens.status === 'uncovered').length,
}
log('explore-fanout: ' + counts.lenses + ' lenses → ' + counts.claims + ' claims ('
  + counts.verified + ' verified, ' + counts.premiseContradictions + ' premise-contradiction, '
  + counts.reuseCandidates + ' reuse-candidate), ' + counts.contradictions + ' contradictions, '
  + counts.gaps + ' gaps, ' + counts.uncoveredLenses + ' UNCOVERED lenses')
// Never silent: an uncovered dimension is the one failure that makes the whole dossier misleading.
if (counts.uncoveredLenses > 0) {
  log('WARNING uncovered: ' + perLens.filter(lens => lens.status === 'failed' || lens.status === 'uncovered').map(lens => lens.key).join(', '))
}

const coverageLenses = perLens.map(lens => ({
  key: lens.key,
  claims: lens.claims,
  status: lens.status,
  rejected: lens.rejected || 0,
}))
const coverage = {
  requested: resolved.length,
  completed: coverageLenses.filter(lens => lens.status === 'completed').length,
  partial: coverageLenses.filter(lens => lens.status === 'partial').length,
  failed: coverageLenses.filter(lens => lens.status === 'failed').length,
  uncovered: coverageLenses.filter(lens => lens.status === 'uncovered').length,
  lenses: coverageLenses,
}
const unavailable = coverage.failed + coverage.uncovered
const status = unavailable === coverage.requested
  ? 'failed'
  : (unavailable > 0 ? 'uncovered' : (coverage.partial > 0 ? 'partial' : 'completed'))
if (RESULT_MODE === 'bounded') {
  const MAX_RESULT_BYTES = 8192
  const clipped = new Set()
  const clipText = (value, collection, max = 160) => {
    if (typeof value !== 'string') return value
    const chars = [...value]
    if (chars.length <= max) return value
    clipped.add(collection)
    return chars.slice(0, max).join('') + ' …[+' + (chars.length - max) + ' chars]'
  }
  const boundValue = (value, collection, depth = 0) => {
    if (typeof value === 'string') return clipText(value, collection)
    if (value === null || typeof value !== 'object') return value
    if (depth >= 4) { clipped.add(collection); return null }
    if (Array.isArray(value)) {
      if (value.length > 8) clipped.add(collection)
      return value.slice(0, 8).map(item => boundValue(item, collection, depth + 1))
    }
    const entries = Object.entries(value)
    if (entries.length > 24) clipped.add(collection)
    const out = {}
    for (const [rawKey, member] of entries.slice(0, 24)) {
      const key = clipText(rawKey, collection, 80)
      if (Object.prototype.hasOwnProperty.call(out, key)) { clipped.add(collection); continue }
      out[key] = boundValue(member, collection, depth + 1)
    }
    return out
  }
  const claimPreview = (claim, detailed) => {
    if (!detailed) {
      clipped.add('claims')
      const out = {
        id: claim.id,
        lens: claim.lens,
        confidence: claim.confidence,
        journalSelector: claim.id,
      }
      if (claim.contested) out.contested = true
      if (Array.isArray(claim.corroboratedBy)) out.corroboratedBy = [...claim.corroboratedBy]
      return out
    }
    const out = boundValue(claim, 'claims')
    out.journalSelector = claim.id
    return out
  }
  const contradictionPreview = (row, detailed) => {
    if (detailed) return boundValue(row, 'contradictions')
    clipped.add('contradictions')
    return {
      key: clipText(row.key, 'contradictions'),
      subject: clipText(row.subject, 'contradictions'),
      positions: row.positions.map(position => ({
        lens: position.lens,
        polarity: position.polarity,
        confidence: position.confidence,
        journalSelector: position.journalSelector,
      })),
    }
  }
  // Same rule as review_fanout.js's findings/flags: an engine flag has no journal copy (see
  // `archive.doesNotContain` below), so it stays represented — compact (kind, lens, a short detail),
  // never dropped outright — rather than either fully inline (the earlier bug: unbounded, never shed)
  // or absent. Every flag object is exactly {kind, lens?, detail}, so this preview loses nothing
  // structurally — only clipText's own truncation of a long `detail` marks the collection clipped.
  const flagPreview = (row) => ({ kind: row.kind, lens: row.lens ?? null, detail: clipText(row.detail || '', 'flags', 120) })
  const state = {
    claims: claims.filter((claim, index) => index < 4 || processedClaimIds.has(claim.id))
      .map((claim, index) => claimPreview(claim, index < 4)),
    contradictions: contradictions.map((row, index) => contradictionPreview(row, index < 3)),
    flags: flags.map(flagPreview),
    gaps: gaps.slice(0, 6).map(row => boundValue(row, 'gaps')),
    perLens: perLens.slice(0, 10).map(row => boundValue(row, 'perLens')),
  }
  const totals = {
    claims: claims.length,
    contradictions: contradictions.length,
    flags: flags.length,
    gaps: gaps.length,
    perLens: perLens.length,
  }
  if (SPILL_DIR) {
    state.spillMetadata = []
    totals.spillMetadata = 1 + resolved.filter(spills).length + resolved.filter(lens => !spills(lens)).length
  }
  const fullCounts = Object.assign({}, counts, {
    flags: flags.length,
    perLens: perLens.length,
  })
  if (SPILL_DIR) fullCounts.spillMetadata = totals.spillMetadata
  const commandBase = 'python3 .claude/tools/session_digest.py --workflow-dir "<transcriptDir-from-Workflow-result>" --workflow-kind explore'
  const expectedHash = '--expect-journal-sha256 <sha256-from-manifest>'
  const renderBounded = () => {
    const preview = {}
    for (const name of Object.keys(totals)) {
      const shown = state[name].length
      const omitted = totals[name] - shown
      preview[name] = {
        total: totals[name], shown, omitted, clipped: clipped.has(name),
        complete: omitted === 0 && !clipped.has(name),
      }
    }
    const output = {
      claims: state.claims,
      contradictions: state.contradictions,
      flags: state.flags,
      gaps: state.gaps,
      counts,
      perLens: state.perLens,
    }
    output.delivery = {
      contract: 'native-fanout-bounded/v1',
      engine: 'explore-fanout',
      resultMode: 'bounded',
      status,
      maxResultBytes: MAX_RESULT_BYTES,
      byteLimitExceeded: false,
      payloadComplete: Object.values(preview).every(row => row.complete),
      processedState: {
        changedClaimsInline: true,
        contradictionsInline: true,
        engineFlagsInline: true,
      },
      counts: fullCounts,
      coverage: {
        status,
        requested: coverage.requested,
        completed: coverage.completed,
        partial: coverage.partial,
        failed: coverage.failed,
        uncovered: coverage.uncovered,
        rowsPreviewed: state.perLens.length,
        rowsOmitted: perLens.length - state.perLens.length,
      },
      preview,
      archive: {
        source: 'workflow-journal',
        delivered: false,
        relativePath: 'journal.jsonl',
        validation: 'required',
        transcriptDirRequired: true,
        selectors: 'C<number>',
        sourceOrder: 'started-agent order, then item-array order',
        contains: 'raw lens claims, checked provenance, and gaps',
        doesNotContain: 'engine confidence changes, contested/corroborated tags, contradictions, or engine flags; those remain inline',
        commands: {
          manifest: commandBase + ' --workflow-manifest',
          select: commandBase + ' --workflow-select <ID> ' + expectedHash,
          page: commandBase + ' --workflow-page items|lenses|reports|gaps --page <N> --page-size <N> ' + expectedHash,
          full: commandBase + ' --workflow-full ' + expectedHash,
        },
      },
    }
    if (!Object.values(preview).every(row => row.complete)) {
      // The journal (`archive` above) only ever held raw per-lens claims — confidence changes,
      // contested/corroborated tags, contradictions, and engine flags are computed by THIS render and
      // have no journal copy. Their full form is recoverable without new model spend: resultMode never
      // reaches an agent() prompt or option (only gates this render), so a Workflow resume of the same
      // run with resultMode:'full' replays every agent() call from cache and renders the complete,
      // unclipped state.
      output.delivery.resume = {
        contract: 'workflow-resume/v1',
        how: 'Workflow({scriptPath, resumeFromRunId: <this run\'s runId>}, {...same args, resultMode: "full"})',
        costsNewModelSpend: false,
        why: 'resultMode is read only when this result is rendered — it never reaches an agent() prompt or option — so every already-completed agent() call replays from cache.',
        contains: 'the complete processed state: every changed claim, contradiction, and engine flag in full, plus every gap and per-lens row, none clipped or omitted.',
      }
    }
    return output
  }
  const utf8Bytes = (value) => {
    let bytes = 0
    for (const ch of JSON.stringify(value)) {
      const point = ch.codePointAt(0)
      bytes += point <= 0x7f ? 1 : (point <= 0x7ff ? 2 : (point <= 0xffff ? 3 : 4))
    }
    return bytes
  }
  // Least-protected first: `gaps` are raw per-lens prose, journal-recoverable (see `archive.contains`
  // above). `flags`, `contradictions` and `claims` are compact by now (see above) but each names a
  // distinct signal the reader needs, so once `gaps` is gone the three tails shed IN TURN (one item off
  // whichever still has any, alternating) rather than draining one to zero while the others stay full.
  // `perLens` (per-lens coverage status) sheds last, ahead only of `counts`, which is never shed.
  const shedOrder = ['gaps']
  const tailShed = ['flags', 'contradictions', 'claims']
  let tailTurn = 0
  const pickShed = () => {
    for (const name of shedOrder) { if (state[name].length > 0) return name }
    for (let i = 0; i < tailShed.length; i++) {
      const name = tailShed[(tailTurn + i) % tailShed.length]
      if (state[name].length > 0) { tailTurn = (tailTurn + i + 1) % tailShed.length; return name }
    }
    if (state.perLens.length > 0) return 'perLens'
    return null
  }
  let output = renderBounded()
  while (utf8Bytes(output) > MAX_RESULT_BYTES) {
    const name = pickShed()
    if (!name) break
    state[name].pop()
    output = renderBounded()
  }
  if (utf8Bytes(output) > MAX_RESULT_BYTES) output.delivery.byteLimitExceeded = true
  return output
}

const output = { claims, contradictions, flags, gaps, counts, perLens, spillDir: SPILL_DIR }
if (SPILL_DIR) {
  output.spills = Object.fromEntries(resolved.filter(spills).map(lens => [lens.key, spillPath(lens.key)]))
  const inlineLabels = resolved.filter(lens => !spills(lens)).map(lens => lens.key)
  if (inlineLabels.length) { output.inlineLabels = inlineLabels }
}
return output
