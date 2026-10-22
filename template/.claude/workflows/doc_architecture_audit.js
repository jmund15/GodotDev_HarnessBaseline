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
let A
try {
  A = (typeof args === 'string') ? JSON.parse(args) : (args ?? {})
} catch (e) {
  return { error: 'args is not valid JSON — /doc_architecture_audit must pass {context, docRoot, model, effort, tier}. ' + ((e && e.message) || '') }
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
    // The agent Bash route cuts a command near 8K characters mid-quote, so the seats file is written in parts.
    const COMMAND_BUDGET = 6000
    const writer = 'import sys; from pathlib import Path; p=Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); f=p.open(sys.argv[2], encoding="utf-8", newline=""); f.write(sys.argv[3]); f.close()'
    const writes = []
    for (let rest = JSON.stringify(launched); rest.length;) {
      const prefix = cdRootPrefix(A) + 'python3 -c ' + quoteArg(writer) + ' ' + quoteArg(seatsFile) + ' ' + (writes.length ? 'a' : 'w') + ' '
      if ((prefix + "'{}'").length > COMMAND_BUDGET) throw new Error('verifier seats-file path leaves no room within the command budget')
      let size = rest.length
      while ((prefix + quoteArg(rest.slice(0, size))).length > COMMAND_BUDGET) size = Math.max(1, Math.floor(size * 0.9))
      if (size < rest.length && /[\uD800-\uDBFF]/.test(rest[size - 1])) size -= 1
      writes.push(prefix + quoteArg(rest.slice(0, size)))
      rest = rest.slice(size)
    }
    if (mode === 'review') command += ' --seats-file ' + quoteArg(seatsFile)
    const printer = 'import json, sys; r = json.load(open(sys.argv[1], encoding="utf-8")); print(json.dumps({"status": r["status"], "counts": r["counts"], "verdicts": {k: v["verdict"] for k, v in r["verdicts"].items()}}))'
    command = cdRootPrefix(A) + command + ' ; python3 -c ' + quoteArg(printer) + ' ' + quoteArg(out)
    log('PINS ' + JSON.stringify({[label]: model + '/' + effort + '/' + profile.agentType + ' tier:fanout'}))
    verification = await verifierAgent('Run each seats-file write below with Bash first, one call per line, in order:\n' + writes.join('\n')
      + '\nRun exactly this command with Bash: ' + command
      + '\nIts last output line is one JSON object holding status, counts and verdicts, read from the --out file; it prints even when the check exits 1.'
      + ' Return ONLY {status, counts, verdicts, report, note}: copy status, counts and verdicts from that line unchanged. Set report to the --out path and nothing else; put any observation in note (optional).'
      + ' Do not check evidence yourself, change permissions, or run any other command.',
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

// args.__transport = {name, ids, efforts?}, injected by hooks/workflow_provider_guard.py. `efforts` is absent when
// the transport declares no effort vocabulary. args.checker = {model, effort} pins the closeCheck verifier this
// engine always starts; it is validated before any agent starts. No engine supplies a default pin.
const TRANSPORT = A.__transport
if (!TRANSPORT || !Array.isArray(TRANSPORT.ids) || !TRANSPORT.ids.length
  || !TRANSPORT.ids.every(id => typeof id === 'string' && id)
  || (TRANSPORT.efforts !== undefined && (!Array.isArray(TRANSPORT.efforts) || !TRANSPORT.efforts.length
    || !TRANSPORT.efforts.every(e => typeof e === 'string' && e)))) {
  return { error: 'doc-architecture-audit: args.__transport is missing or malformed; the provider guard supplies the native model and effort vocabulary.' }
}
const EFFORT_VOCAB = TRANSPORT.efforts
const pinVocab = 'model in [' + TRANSPORT.ids.join('|') + ']' + (EFFORT_VOCAB ? ' and effort in [' + EFFORT_VOCAB.join('|') + ']' : '')
// The three audit lenses take the caller's top-level pin; nothing fills it when it is absent or illegal.
if (!TRANSPORT.ids.includes(A.model) || (EFFORT_VOCAB !== undefined && !EFFORT_VOCAB.includes(A.effort))) {
  return { error: 'doc-architecture-audit: pin model and effort on every job (orchestration §5). Unpinned or illegal: da-structural, da-crossref, da-domain. args needs a caller-selected ' + pinVocab + '.',
    badJobs: ['da-structural', 'da-crossref', 'da-domain'] }
}
if (!['orchestrator', 'architect', 'executor', 'fanout', 'scout'].includes(A.tier)) {
  return { error: 'doc-architecture-audit: args needs a caller-selected tier, a named work shape (orchestrator|architect|executor|fanout|scout).' }
}
// closeCheck runs on every audit, so its verifier pin is always required here.
const checkerOk = !!A.checker && typeof A.checker === 'object' && TRANSPORT.ids.includes(A.checker.model)
  && (EFFORT_VOCAB === undefined || EFFORT_VOCAB.includes(A.checker.effort))
if (!checkerOk) {
  return { error: 'doc-architecture-audit: args.checker = {model, effort} is required because every run starts the closeCheck verifier; args.checker must pin ' + pinVocab + '. Refusing before any agent starts.' }
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
  '`file` and `evidence` are REQUIRED for a finding about a file\'s content (a missing section, a stale table row, a broken link text): the quote is copied character for character from the doc you read, never your summary, because a checker re-reads the file for it and drops an unmatched finding to unverified. A finding about a file\'s absence or a folder\'s shape sets file to null and supplies canonical command evidence; without a supported command, evidence is an empty array and the finding stays UNCHECKED.',
  'Severity: critical=broken nav/links/missing-blocking-docs; warning=template/format/unidirectional drift; info=hub/naming/orphan/migration.',
  'Action: FIX=mechanical fix with known handler (options null); ASK=needs user judgment (handler null, ranked options recommended-first).',
  'No hallucinated findings — only report issues verified by reading actual files; if uncertain, skip. If no issue for a check, omit it.',
].join('\n')

const READ_TOOLING = 'Read vault docs with native Read/Glob/Grep under the documentation root: ' + DOC_ROOT + ' . Do NOT re-read Start Here or the folder structure — they are already in the CONTEXT block below (push-don\'t-pull). IMPORTANT: the vault is on OneDrive (Files-On-Demand), so a doc read can transiently return empty/"No such file" while the file hydrates. An empty or missing read is NOT proof of absence — retry the same Read once before concluding a doc is missing, and only flag a doc as absent if the CONTEXT manifest also lacks it. Never emit a finding from a single failed read.'

const STRUCTURAL = [
  'You are auditing documentation STRUCTURE for the {{PROJECT_NAME}} project (stack per `.claude/reference/project_stack.md`).',
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
  'You are auditing CROSS-REFERENCES and system boundaries in documentation for the {{PROJECT_NAME}} project (stack per `.claude/reference/project_stack.md`).',
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
  'You are auditing DOMAIN hierarchy and navigation quality in documentation for the {{PROJECT_NAME}} project (stack per `.claude/reference/project_stack.md`).',
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
// TOOL PROFILES — the inventory each audit lens launches with. A native child INHERITS the session's
// MCP set, so an entry is `inherited` with effectiveMcp unknown unless the lens runs as its generated
// agent type (`agent-type`); never a strict claim of enforcement.
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
const leanAgent = (prompt, opts, swapped, rec) => {
  if (!swapped) return agent(prompt, opts)
  const asked = opts.agentType || 'general-purpose'
  return agent(LEAN.s ? prompt + '\n\n' + LEAN.s : prompt, Object.assign({}, opts, { agentType: swapped })).catch(e => {
    const msg = String((e && e.message) || e)
    if (!msg.includes("agent type '") || !msg.includes('not found')) throw e
    log('FALLBACK ' + opts.label + ' ' + swapped + '→' + asked)
    rec[opts.label] = Object.assign({}, rec[opts.label], { agentType: 'general-purpose', enforcement: 'inherited', effectiveMcp: 'unknown/inherited' })
    log('TOOL-PROFILES-REVISED ' + JSON.stringify(rec))
    return agent(prompt, Object.assign({}, opts, { agentType: asked }))
  })
}
const tpOf = (label) => TOOLP.d[label] || TOOLP.f['review'] || null
const swapOf = (label) => leanTypeOf([label], undefined, tpOf(label))
const RECORDED = Object.fromEntries(['da-structural', 'da-crossref', 'da-domain'].map(l => [l, leanRecord(tpOf(l), undefined, swapOf(l))]))
log('TOOL-PROFILES ' + JSON.stringify(RECORDED))
// The platform relays the triggering user message to every agent as authoritative; a lens whose
// mandate looks unrelated can answer that message instead of its brief.
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
const audit = (prompt, label) => leanAgent(prompt + '\n' + RELAY_LINE, { label, phase: 'Audit', schema: FINDINGS_SCHEMA, model: A.model, effort: A.effort }, swapOf(label), RECORDED)

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
let deduped = [...byKey.values(), ...noSystem]
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

const closed = await closeCheck('claims', { items: deduped, keyOf: f => f.lens + '#' + f.index, out: '.claude/scratch/doc_architecture_audit_claims.json',
  seats: ['da-structural', 'da-crossref', 'da-domain'].map(label => ({ label, model: A.model, effort: A.effort, tier: A.tier })) })
deduped = closed.items
const verification = closed.verification

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


return { findings: deduped, crossAgentPatterns, counts, healthRating, ratingBasis: { x1Warnings, structuralWarnings }, linkGraph, lensStatus, lensesCompleted, failedLenses, verification }
