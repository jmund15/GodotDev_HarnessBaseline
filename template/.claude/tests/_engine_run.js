#!/usr/bin/env node
// Runs a Workflow engine script outside the Workflow tool. Stdin: {script, args, reply}; stdout: the
// engine's return as JSON. Every agent() call returns `reply`; `args` without `__transport` gets HOST.
// A JS proof requires `load` (the script as a callable) and `withHost` (the host transport, given its host).
//
//     echo '{"script": ".claude/workflows/review_fanout.js", "args": {...}, "reply": {...}}' | node _engine_run.js
const fs = require('fs')

const HOST = { name: 'host', ids: ['fixture-model'] }
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor

const dateError = 'Date.now() / new Date() are unavailable in workflow scripts (breaks resume). Stamp results after the workflow returns, or pass timestamps via args.'
class WorkflowDate extends Date {
  constructor() { throw new Error(dateError) }
  static now() { throw new Error(dateError) }
}
const workflowMath = Object.create(Math)
workflowMath.random = () => { throw new Error('Math.random() is unavailable in workflow scripts (breaks resume).') }

const load = (script, transform = source => source) => {
  const run = new AsyncFunction('Date', 'Math', 'args', 'agent', 'parallel', 'phase', 'log',
    transform(fs.readFileSync(script, 'utf8').replace(/^export const meta = \{[\s\S]*?^\}\r?\n/m, '')))
  return (...args) => run(WorkflowDate, workflowMath, ...args)
}

// The kind rows hooks/workflow_provider_guard.py resolves from guards/tool_profiles.json into args.__toolProfiles
// (by job label or lens key) and args.__kindProfile (the call-level `args.kind` of a single-kind engine).
const ROW = { add: [], remove: [], overrideMode: 'shape-default', enforcement: 'inherited', effectiveMcp: 'unknown/inherited',
  mcpServers: ['semantic-search', 'ai-worker'] }
const KIND_ROWS = {
  scout: { ...ROW, kind: 'scout', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-survey', tier: 'fanout', guard: 'survey', disclosure: 'bare' },
  lens: { ...ROW, kind: 'lens', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-survey', tier: 'fanout', guard: 'survey', disclosure: 'pointer' },
  reviewer: { ...ROW, kind: 'reviewer', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-review', tier: 'executor', guard: 'review', disclosure: 'full' },
  author: { ...ROW, kind: 'author', builtins: ['Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash'], agentType: 'lane-author', tier: 'executor', guard: 'author', disclosure: 'full' },
  judge: { ...ROW, kind: 'judge', builtins: ['Read', 'Glob', 'Grep'], agentType: 'lane-review', tier: 'executor', guard: 'common', disclosure: 'full' },
}
const withKinds = (args) => {
  const a = { ...args }
  const entries = [
    ...(Array.isArray(a.jobs) ? a.jobs.map(j => [j, j && j.label]) : []),
    ...(Array.isArray(a.chains) ? a.chains.flatMap(c => ((c && c.jobs) || []).map(j => [j, j && j.label])) : []),
    ...(Array.isArray(a.lenses) ? a.lenses.map(l => [l, l && l.key]) : []),
    ...(Array.isArray(a.agents) ? a.agents.map(g => [g, g && g.key]) : []),
  ]
  const profiles = { 'review:merge': KIND_ROWS.reviewer, 'review:fold': KIND_ROWS.author }
  for (const [entry, key] of entries) if (entry && key && KIND_ROWS[entry.kind]) profiles[key] = KIND_ROWS[entry.kind]
  a.__toolProfiles = { ...profiles, ...(a.__toolProfiles || {}) }
  if (a.kind && KIND_ROWS[a.kind] && a.__kindProfile === undefined) a.__kindProfile = KIND_ROWS[a.kind]
  return a
}

const withHost = (args, host = HOST) =>
  withKinds(Object.prototype.hasOwnProperty.call(args || {}, '__transport') ? args : { ...args, __transport: { ...host } })

const run = ({ script, args, reply }) => load(script)(withHost(args),
  async () => reply, thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn))), () => {}, () => {})

module.exports = { load, run, withHost, withKinds, HOST, KIND_ROWS }

if (require.main === module) {
  run(JSON.parse(fs.readFileSync(0, 'utf8')))
    .then(out => console.log(JSON.stringify(out)))
    .catch(error => { console.error(error); process.exit(1) })
}
