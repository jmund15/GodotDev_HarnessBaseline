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

const withHost = (args, host = HOST) =>
  Object.prototype.hasOwnProperty.call(args || {}, '__transport') ? args : { ...args, __transport: { ...host } }

const run = ({ script, args, reply }) => load(script)(withHost(args),
  async () => reply, thunks => Promise.all(thunks.map(fn => Promise.resolve().then(fn))), () => {}, () => {})

module.exports = { load, run, withHost, HOST }

if (require.main === module) {
  run(JSON.parse(fs.readFileSync(0, 'utf8')))
    .then(out => console.log(JSON.stringify(out)))
    .catch(error => { console.error(error); process.exit(1) })
}
