#!/usr/bin/env node
// Minimal proof for tools/workflow_replay.mjs: a planted one-call run replays to its recorded result, and a run whose
// journal lost the call's result exits 1 with nothing on stdout. Usage with no path exits 2.
//   node .claude/tests/workflow_replay_contract_test.js
// Every file is planted in a temp root with the runtime's record and journal shapes; no real run is read.
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')

const TOOL = path.join(__dirname, '..', 'tools', 'workflow_replay.mjs')
const RUN_ID = 'wf_planted'

let passed = 0
let failed = 0
function check(label, condition, detail = '') {
  console.log((condition ? 'ok   ' : 'FAIL ') + label)
  if (condition) passed++
  else {
    failed++
    if (detail) console.log('     ' + String(detail).slice(0, 1200))
  }
}

const record = {
  runId: RUN_ID,
  workflowName: 'planted-workflow',
  status: 'completed',
  script: "const reply = await agent('planted prompt', { label: 'planted-lens' })\nreturn { got: reply }\n",
  args: {},
  result: { got: 'ok' },
}
const journal = [
  { type: 'launched' },
  { type: 'started', key: 'planted-key', agentId: 'planted-agent', label: 'planted-lens' },
  { type: 'result', key: 'planted-key', agentId: 'planted-agent', result: 'ok' },
]

const roots = []
function plantSession(rows) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'workflow-replay-contract-'))
  roots.push(root)
  fs.mkdirSync(path.join(root, 'workflows'), { recursive: true })
  const transcriptDir = path.join(root, 'subagents', 'workflows', RUN_ID)
  fs.mkdirSync(transcriptDir, { recursive: true })
  fs.writeFileSync(path.join(root, 'workflows', RUN_ID + '.json'), JSON.stringify(record))
  fs.writeFileSync(path.join(transcriptDir, 'journal.jsonl'), rows.map(row => JSON.stringify(row)).join('\n') + '\n')
  return transcriptDir
}
const replay = args => spawnSync(process.execPath, [TOOL, ...args], { encoding: 'utf8', maxBuffer: 16 << 20 })

try {
  const good = replay([plantSession(journal)])
  check('a planted run replays to its recorded result, exit 0', good.status === 0, good.stderr)
  check('the replay prints the full result as JSON', good.stdout.trim() === '{"got":"ok"}', good.stdout)

  const lost = replay([plantSession(journal.filter(row => row.type !== 'result'))])
  check('a journal missing the call result exits 1', lost.status === 1, 'exit ' + lost.status)
  check('the gap is named on stderr with nothing on stdout',
    /workflow_replay: agent .*planted-lens.* has no result in the journal/.test(lost.stderr) && lost.stdout === '',
    lost.stderr + lost.stdout)

  const usage = replay([])
  check('no transcript path exits 2', usage.status === 2, 'exit ' + usage.status)
} finally {
  for (const root of roots) fs.rmSync(root, { recursive: true, force: true })
}

console.log(`${passed}/${passed + failed} checks pass`)
process.exitCode = failed ? 1 : 0
