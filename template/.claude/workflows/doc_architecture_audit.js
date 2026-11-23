export const meta = {
  name: 'doc-architecture-audit',
  description: 'Audit documentation structure, links and domain coverage',
  phases: [
    { title: 'Audit', detail: 'da-structural + da-crossref + da-domain in parallel over the shared CONTEXT' },
    { title: 'Consolidate', detail: 'dedup by system+check, sort by severity, detect cross-agent patterns, compute health rating' },
  ],
}

// Platform contract: args is a JSON STRING. Parse-guard (legible failure on malformed/truncated JSON
// rather than an uncaught SyntaxError — the CONTEXT block is large and truncation-at-boundary is the
// realistic failure mode).
// ENGINE-BLOCK-BEGIN A
let A
// ENGINE-BLOCK-END A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'args is not valid JSON — /doc_architecture_audit must pass {context, docRoot, model, effort, kind}. ' + ((e && e.message) || '') }
}
// KIND PROFILE. The call names one `kind` (scout|lens|reviewer|author|judge|gate) for every child it spawns;
// hooks/workflow_transport.py resolves that row of guards/tool_profiles.json into args.__kindProfile. Each
// child runs as the row's generated agent type, whose `tools` frontmatter is the whole inventory, so a recorded
// entry is `agent-type`; the runtime restricts no per-child MCP server, so effectiveMcp is the row's list.
// ENGINE-BLOCK-BEGIN KP
const KP = (A.__kindProfile && typeof A.__kindProfile === 'object') ? A.__kindProfile : null
// ENGINE-BLOCK-END KP
if (typeof A.kind !== 'string' || !A.kind.trim() || !KP || KP.kind !== A.kind || !KP.agentType) {
  return { error: 'doc-architecture-audit: args.kind is required (scout|lens|reviewer|author|judge|gate), resolved by hooks/workflow_transport.py into args.__kindProfile. The three audit lenses run as that kind.' }
}
// ENGINE-BLOCK-BEGIN KP_RECORD
const KP_RECORD = Object.assign({}, KP, { enforcement: 'agent-type', effectiveMcp: (KP.mcpServers || []).join(', ') })
// ENGINE-BLOCK-END KP_RECORD
// EVIDENCE-SSOT-BEGIN
const EVIDENCE_SCHEMA = {
  "description": "The evidence contract's machine twin (tools/quote_check.py owns the semantics and is its one executable implementation). A claim's `evidence` is a list of items, each exactly one of two kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it.",
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
        "description": "A quote from a local file, or an owner quote when `file` is the session transcript `.jsonl` (the words are in one owner-typed record). Holds when the quote, whitespace runs collapsed, is in the file; a line range is never checked.",
        "properties": {
          "file": {
            "type": "string",
            "description": "path relative to the checkout root or absolute, optionally with :N or :N-M"
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
        "description": "The stdout of a read-only git command on quote_check.py's allow-list, run without a shell from the checkout root. Holds when the stdout holds the output; an empty output holds only when stdout is empty and every pathspec the command names is tracked.",
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
      }
    ]
  }
}
const EVIDENCE_RULES = "The evidence contract's machine twin (tools/quote_check.py owns the semantics and is its one executable implementation). A claim's `evidence` is a list of items, each exactly one of two kinds, one item per source: never several sources joined in one string, never a re-rendered structure, never a `memory:` prefix. Every key here is a standard JSON Schema keyword, so the CLI's strict validator accepts it."
// EVIDENCE-SSOT-END


// args.__transport = {name, ids, efforts?}, injected by hooks/workflow_transport.py. `efforts` is absent when
// the transport declares no effort vocabulary. No engine supplies a default pin.
// ENGINE-BLOCK-BEGIN TRANSPORT
const TRANSPORT = A.__transport
// ENGINE-BLOCK-END TRANSPORT
// ENGINE-BLOCK-BEGIN validStringList
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
// ENGINE-BLOCK-END validStringList
// ENGINE-BLOCK-BEGIN transportValid
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
// ENGINE-BLOCK-END transportValid
if (!transportValid) {
  return { error: 'doc-architecture-audit: args.__transport is missing or malformed; the entry (hooks/pre_workflow_dispatch.py) injects the native model and effort vocabulary.' }
}
const EFFORT_VOCAB = TRANSPORT.efforts
const pinVocab = 'model in [' + TRANSPORT.ids.join('|') + ']' + (EFFORT_VOCAB ? ' and effort in [' + EFFORT_VOCAB.join('|') + ']' : '')
// The three audit lenses take the caller's top-level pin; nothing fills it when it is absent or illegal.
if (!TRANSPORT.ids.includes(A.model) || (EFFORT_VOCAB !== undefined && !EFFORT_VOCAB.includes(A.effort))) {
  return { error: 'doc-architecture-audit: pin model and effort on every job (orchestration §5). Unpinned or illegal: da-structural, da-crossref, da-domain. args needs a caller-selected ' + pinVocab + '.',
    badJobs: ['da-structural', 'da-crossref', 'da-domain'] }
}

const CONTEXT = A.context || ''
const DOC_ROOT = A.docRoot || ''
if (!CONTEXT) {
  return { error: 'No context in args. /doc_architecture_audit (Claude) must build the System Manifest + read Start Here and pass the assembled CONTEXT block via args.context (workflow scripts cannot read files).' }
}

const FINDING_SCHEMA_TEXT = [
  'Return findings as a JSON array. Each finding object:',
  '{ "agent": "<your agent key>", "check": "S1-S4|X1-X4|D1-D7", "severity": "critical|warning|info", "action": "FIX|ASK",',
  '  "system": "SystemName or null", "description": "what the issue is", "recommendation": "imperative actionable fix",',
  '  "handler": "delegate:/doc_full | delegate:/doc_start_here_update | inline:search_replace | inline:rename | inline:hub_create | null",',
  '  "options": null | ["Option A (Recommended) — reason", "Option B — reason"], "scope": ["affected paths or system names"],',
  '  "file": "<path of the doc the finding is about>:<line> or null", "evidence": [{"file": "<absolute path>", "quote": "<VERBATIM text>"}] }',
  'Evidence: ' + EVIDENCE_RULES,
  '`file` and `evidence` are REQUIRED for a finding about a file\'s content (a missing section, a stale table row, a broken link text): the quote is copied character for character from the doc you read, never your summary, because the caller re-reads the file for it (tools/quote_check.py) and a quote that is not there is a miss. A finding about a file\'s absence or a folder\'s shape sets file to null and supplies canonical command evidence; without a supported command, evidence is an empty array.',
  'Severity: critical=broken nav/links/missing-blocking-docs; warning=template/format/unidirectional drift; info=hub/naming/orphan/migration.',
  'Action: FIX=mechanical fix with known handler (options null); ASK=needs user judgment (handler null, ranked options recommended-first).',
  'No hallucinated findings — only report issues verified by reading actual files; if uncertain, skip. If no issue for a check, omit it.',
].join('\n')

const READ_TOOLING = 'Read vault docs with native Read/Glob/Grep under the documentation root: ' + DOC_ROOT + ' . Do NOT re-read Start Here or the folder structure — they are already in the CONTEXT block below (push-don\'t-pull). IMPORTANT: the vault is on OneDrive (Files-On-Demand), so a doc read can transiently return empty/"No such file" while the file hydrates. An empty or missing read is NOT proof of absence — retry the same Read once before concluding a doc is missing, and only flag a doc as absent if the CONTEXT manifest also lacks it. Never emit a finding from a single failed read.'

const STRUCTURAL = [
  'You are auditing documentation STRUCTURE for this project (stack per `.claude/reference/project_stack.md`).',
  'Your role: check template completeness, compliance, formatting standards, and naming consistency across all documented systems.',
  READ_TOOLING, '', CONTEXT, '', FINDING_SCHEMA_TEXT, '',
  '## Your Checks',
  'SCOPE: apply S1-S4 ONLY to manifest entries with classification=="system". SKIP classification=="domain" (containers, not systems) and classification=="entity-doc" (a folder of per-entity docs — follows the entity-doc conventions, exempt from the 4-doc template per documentation_structure.md). Flagging an entity-doc or domain folder for missing template docs is a hallucinated finding.',
  'S1 Template Completeness: for each system in the manifest, verify expected 4-doc files exist (Quick Reference, Architecture, Designer Usage, Retrospective). Flag systems with only a Design Document as MIGRATE candidates (info).',
  'S2 Template Compliance: sample up to 8 Quick References. Verify standard structure (System Overview callout, Document Index table, Core Types table, Key Properties/Exports table, File Map, Related Systems callout). Flag missing/reordered sections as warnings.',
  'S3 Formatting Compliance: sample up to 6 Architecture/Usage docs. Verify collapsible callouts (not ### inside ## sections), Changelog section present, Related Documents callout present. Flag violations as warnings.',
  'S4 Naming Consistency: check "Usage.md" vs "Designer Usage.md" inconsistency across systems. Flag orphan files not part of the standard template and not referenced by the Quick Reference (orphan → ASK; naming → FIX inline:rename).',
  'Tool budget: <=20 doc reads total. Prioritize breadth over depth. Set every finding\'s "agent" to "da-structural".',
].join('\n')

const CROSSREF = [
  'You are auditing CROSS-REFERENCES and system boundaries in documentation for this project (stack per `.claude/reference/project_stack.md`).',
  'Your role: build a directed link graph from Related Systems callouts, verify link integrity, detect boundary overlaps.',
  READ_TOOLING, '', CONTEXT, '', FINDING_SCHEMA_TEXT, '',
  '## Your Checks',
  'X1 Bidirectional Links: read ALL Quick References. Extract the "> [!info] Related Systems" callout. Build a directed graph (A→B means A mentions B). Report every A→B lacking B→A as a warning.',
  'X2 Wikilink Validity: for every [[wikilink]] in Related Systems callouts, verify the target exists (cross-ref the manifest). Broken links = critical. Also check format consistency.',
  'X3 Boundary Clarity: compare Core Types tables across QRs. Same type name in multiple systems → warning (unclear boundary; which system owns it? → ASK).',
  'X4 Shared-File Cross-Refs: compare File Map sections. Overlapping file paths across two systems should cross-reference each other in Related Systems; flag missing as warnings.',
  'Tool budget: <=25 doc reads; you MUST read all Quick References for the link graph — prioritize these.',
  'Also include a link-graph summary. Set every finding\'s "agent" to "da-crossref". Return an object {"findings": [...], "linkGraph": {"edges": N, "bidirectional": N, "unidirectional": N, "unidirectionalBySystem": {"SystemName": <count of inbound one-way links this system fails to return>}}}. unidirectionalBySystem MUST list every system that is the TARGET of >=1 inbound link it does not reciprocate, keyed by system name, with the count of missing back-links; the values MUST sum to "unidirectional". This lets the report render per-system link status as a deterministic lookup instead of parsing your prose.',
].join('\n')

const DOMAIN = [
  'You are auditing DOMAIN hierarchy and navigation quality in documentation for this project (stack per `.claude/reference/project_stack.md`).',
  'Your role: verify Start Here coverage, domain classification accuracy, entry-point correctness, hub needs, archived-doc hygiene, and domain cohesion/isolation.',
  READ_TOOLING, '', CONTEXT, '', FINDING_SCHEMA_TEXT, '',
  '## Your Checks',
  'D1 Start Here Coverage: cross-ref manifest vs Start Here wikilinks. Every non-excluded system should appear in "By Domain" (missing = critical). Systems with Usage/Designer-Usage need a Designer row in "By Role" (missing = warning). Systems with Architecture need a Developer row (missing = warning).',
  'D2 Domain Assignment: sample a few QRs to verify the domain classification matches the system\'s actual scope. Misclassifications = warning (ASK: correct domain?).',
  'D3 Entry Point Drift: systems linking to a Design Document that now have a Quick Reference → warning (upgrade entry point).',
  'D4 Domain Hub Detection & Integrity: domains with 3+ systems forming a dependency chain/pipeline warrant a hub doc. Threshold is pipeline coupling, not count. Flag a MISSING hub as info with rationale + suggested scope. SEPARATELY, where a `_Hub.md` ALREADY exists, read it and verify integrity: (a) it lists every system currently in its domain (a hub missing a since-added system is STALE = warning), and (b) its inter-system / inter-hub wikilinks resolve against the manifest (a broken hub link = warning). Reading the 1-2 existing hubs is in-budget — do it. Do NOT recommend forcing hubs onto small loosely-coupled domains; absence of a hub is only a finding when the coupling threshold is met.',
  'D5 Archived Hygiene: if any (Archived) folders exist, check Superseded-By links point to valid active systems, and active docs do not reference archived docs in Related Systems. Issues = warning.',
  'D6 Domain Cohesion & Reorganization: for each domain with 4+ systems, count intra-domain vs cross-domain references. If a subset of 3+ tightly-coupled systems is functionally distinct from the rest, recommend promotion to its own domain folder (structural) = warning; if it just needs a navigational overview, that is a hub (info, D4).',
  'D7 Domain Isolation: flag domains with <=2 implemented systems (a system is "implemented" when its manifest templateFormat != "empty") that share cross-references with another small domain (<=3 systems) — merge candidates. Merge threshold: combined <=6 systems, shared cross-refs/persona, neither has strong standalone identity. Flag as warning with ranked merge options.',
  'Tool budget: <=18 doc reads. Reuse QR data across checks. Set every finding\'s "agent" to "da-domain".',
].join('\n')

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: true,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: true,
        properties: {
          agent: { type: 'string' },
          check: { type: 'string', enum: ['S1', 'S2', 'S3', 'S4', 'X1', 'X2', 'X3', 'X4', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7'] },
          severity: { type: 'string', enum: ['critical', 'warning', 'info'] },
          action: { type: 'string', enum: ['FIX', 'ASK'] },
          system: { type: ['string', 'null'] },
          description: { type: 'string' },
          recommendation: { type: 'string' },
          handler: { type: ['string', 'null'] },
          options: { type: ['array', 'null'], items: { type: 'string' } },
          scope: { type: 'array', items: { type: 'string' } },
          file: { type: ['string', 'null'] },
          evidence: EVIDENCE_SCHEMA,
        },
        required: ['agent', 'check', 'severity', 'action', 'system', 'description', 'recommendation', 'handler', 'options', 'scope'],
      },
    },
    linkGraph: {
      type: ['object', 'null'],
      additionalProperties: true,
      properties: { edges: { type: 'number' }, bidirectional: { type: 'number' }, unidirectional: { type: 'number' } },
    },
  },
  required: ['findings'],
}

phase('Audit')
// TOOL PROFILES — each audit lens launches as the call's kind row.
const tpOf = () => KP_RECORD
const RECORDED = Object.fromEntries(['da-structural', 'da-crossref', 'da-domain'].map(l => [l, tpOf(l)]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
// ENGINE-BLOCK-BEGIN RELAY_LINE
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// ENGINE-BLOCK-END RELAY_LINE
const audit = (prompt, label) => agent(prompt + '\n' + RELAY_LINE, { label, phase: 'Audit', agentType: KP.agentType, schema: FINDINGS_SCHEMA, model: A.model, effort: A.effort })

const [structRes, crossRes, domainRes] = await parallel([
  () => audit(STRUCTURAL, 'da-structural'),
  () => audit(CROSSREF, 'da-crossref'),
  () => audit(DOMAIN, 'da-domain'),
])

phase('Consolidate')
const all = []
for (const [lens, r] of [['da-structural', structRes], ['da-crossref', crossRes], ['da-domain', domainRes]]) {
  if (r && Array.isArray(r.findings)) { all.push(...r.findings.map((f, i) => Object.assign({}, f, { lens, index: i + 1 }))) }
}
const linkGraph = (crossRes && crossRes.linkGraph) ? crossRes.linkGraph : null

// Lens-completion tracking: parallel() resolves a thrown agent to null, so a failed lens would
// otherwise vanish silently and a 2-of-3 audit would read as complete. Surface it for the report.
const lensStatus = {
  structural: !!(structRes && Array.isArray(structRes.findings)),
  crossref: !!(crossRes && Array.isArray(crossRes.findings)),
  domain: !!(domainRes && Array.isArray(domainRes.findings)),
}
const lensesCompleted = Object.values(lensStatus).filter(Boolean).length
const failedLenses = Object.keys(lensStatus).filter(k => !lensStatus[k])

// 3a: dedup by (system + check), keep higher severity. NOTE check codes are agent-disjoint
// (S*=structural, X*=crossref, D*=domain), so a (system::check) key only ever collapses repeats
// from the SAME agent — cross-agent overlap on one system is intentionally preserved here and
// surfaces via the cross-agent-pattern pass (3b). System-less findings (S4 orphans, D4 hubs,
// D7 merges) are not same-system dups, so they pass through untouched (no null::<check> collapse).
const SEV = { critical: 3, warning: 2, info: 1 }
const byKey = new Map()
const noSystem = []
for (const f of all) {
  if (!f.system) { noSystem.push(f); continue }
  const key = f.system + '::' + (f.check || '?')
  const existing = byKey.get(key)
  if (!existing || (SEV[f.severity] || 0) > (SEV[existing.severity] || 0)) { byKey.set(key, f) }
}
const deduped = [...byKey.values(), ...noSystem]
deduped.sort((a, b) => (SEV[b.severity] || 0) - (SEV[a.severity] || 0))

// 3a-bis: scrub in-band agent self-correction artifacts from path-shaped scope fields (e.g. an agent
// that typed "DraftPath... no — Documentation/Networking/Quick Reference.md" mid-thought). The JSON
// schema validates these (still a string) but /doc_audit_fix would consume the corrupted path, and the
// Machine Findings verbatim copy would carry the garble. Cleaning here keeps the verbatim copy clean.
const cleanScope = (s) => {
  if (typeof s !== 'string') { return s }
  const m = s.match(/(?:\.\.\.|…)\s*no\b\s*[—–-]\s*(.+)$/i)
  if (m && /[\\/.]/.test(m[1])) { return m[1].trim() }
  return s.trim()
}
for (const f of deduped) {
  if (Array.isArray(f.scope)) { f.scope = f.scope.map(cleanScope) }
}

// 3b: cross-agent patterns — systems flagged by 2+ distinct agents.
const bySystem = new Map()
for (const f of deduped) {
  if (!f.system) { continue }
  if (!bySystem.has(f.system)) { bySystem.set(f.system, { agents: new Set(), checks: [] }) }
  const e = bySystem.get(f.system)
  e.agents.add(f.agent)
  e.checks.push(f.check)
}
// Null-system findings (D-level / S4 — hub, merge, folder-move, multi-file style) carry their target in
// scope[], not in `system`, so the grouping above silently drops them. That defeats cross-agent detection
// exactly when it matters most: e.g. a D7 fold (system=null, scope names the folder) coinciding with an S4
// placement finding on the same folder. Attribute a null-system finding to a concrete system ONLY when its
// scope names EXACTLY ONE already-flagged system — single-target → safe to attribute; multi-target (a domain
// split listing 8 systems) → ambiguous, leave unattributed. Never invents a system not already flagged.
const knownSystems = Array.from(bySystem.keys())
for (const f of deduped) {
  if (f.system || !Array.isArray(f.scope)) { continue }
  const matched = new Set()
  for (const s of f.scope) {
    if (typeof s !== 'string') { continue }
    const segs = s.split(/[\\/]/).filter(Boolean)
    for (const sys of knownSystems) {
      if (s === sys || segs.includes(sys)) { matched.add(sys) }
    }
  }
  if (matched.size === 1) {
    const e = bySystem.get([...matched][0])
    e.agents.add(f.agent)
    e.checks.push(f.check)
  }
}
const crossAgentPatterns = []
for (const [system, e] of bySystem) {
  if (e.agents.size >= 2) { crossAgentPatterns.push({ system, agents: Array.from(e.agents), checks: e.checks }) }
}

const counts = {
  critical: deduped.filter(f => f.severity === 'critical').length,
  warning: deduped.filter(f => f.severity === 'warning').length,
  info: deduped.filter(f => f.severity === 'info').length,
}

// 3d: health rating (most-severe first). X1 unidirectional-link findings are the lowest-stakes, most-
// mechanical drift and scale linearly with cross-reference density (one finding per missing back-link),
// so a pile of them alone must NOT promote a 0-critical, structurally-sound vault past MINOR DRIFT. The
// MODERATE-DEBT warning clause therefore gates on STRUCTURAL (non-X1) warnings — template / boundary /
// naming / classification / domain. Total warnings still register the doc set as MINOR DRIFT.
const x1Warnings = deduped.filter(f => f.severity === 'warning' && f.check === 'X1').length
const structuralWarnings = counts.warning - x1Warnings
let healthRating
if (counts.critical >= 3) { healthRating = 'NEEDS OVERHAUL' }
else if (counts.critical >= 1 || structuralWarnings >= 7 || crossAgentPatterns.length >= 3) { healthRating = 'MODERATE DEBT' }
else if (counts.warning >= 4 || crossAgentPatterns.length >= 1) { healthRating = 'MINOR DRIFT' }
else { healthRating = 'HEALTHY' }

log('audit: ' + deduped.length + ' findings (' + counts.critical + 'C/' + counts.warning + 'W[' + structuralWarnings + ' structural/' + x1Warnings + ' link]/' + counts.info + 'I), ' + crossAgentPatterns.length + ' cross-agent patterns → ' + healthRating + (lensesCompleted < 3 ? ' — PARTIAL: only ' + lensesCompleted + '/3 lenses (' + failedLenses.join(',') + ' failed)' : ''))


return { findings: deduped, crossAgentPatterns, counts, healthRating, ratingBasis: { x1Warnings, structuralWarnings }, linkGraph, lensStatus, lensesCompleted, failedLenses }
