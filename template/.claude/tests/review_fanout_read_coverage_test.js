#!/usr/bin/env node
const fs = require('fs')
const path = require('path')
const vm = require('vm')
const source = fs.readFileSync(path.join(__dirname, '..', 'workflows', 'review_fanout.js'), 'utf8')
const start = source.indexOf('const WHOLE = [1, Infinity]')
const end = source.indexOf('// True when `spans` hold', start)
if (start < 0 || end <= start) throw new Error('coverage adapter helpers exist')
const helpers = source.slice(start, end)
const run = vm.runInNewContext(`(() => { const READ_WINDOW = 2000, SHELL_TOOLS = ['bash', 'powershell']; ${helpers}; return {shellReads, claimedReads: typeof claimedReads === 'function' ? claimedReads : null} })()`)
const corpus = JSON.parse(fs.readFileSync(path.join(__dirname, 'read_coverage_corpus.json'), 'utf8'))
let failed = false
for (const row of corpus) {
  const actual = run.shellReads(row.command, '.').map(([file, spans]) => [file.replace(/^\.\//, ''), spans.map(([first, last]) => [first, last === Infinity ? 'end' : last])])
  if (JSON.stringify(actual) === JSON.stringify(row.reads)) console.log('ok   ' + row.name)
  else { failed = true; console.log('FAIL ' + row.name + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(row.reads)) }
}
// review_result.py claimed_reads: a chained shell entry credits each file its own spans, never the last file all of them.
const claims = [
  ['a chained shell read credits each file its own spans', ['Bash:cat docs/a.md && sed -n 1,5p docs/b.md'], [['docs/a.md', [[1, 'end']]], ['docs/b.md', [[1, 5]]]]],
  ['a Read entry keeps its suffix spans', ['Read:docs/c.md:10-20'], [['docs/c.md', [[10, 20]]]]],
  ['a search credits no lines', ['Grep:docs/d.md'], []],
  ['a PowerShell Get-Content of a Windows path gets path credit', ['PowerShell:Get-Content C:\\a\\b.diff'], [['c:/a/b.diff', [[1, 'end']]]]],
  ['a double-quoted Windows path gets path credit', ['PowerShell:Get-Content "C:\\a\\b.diff"'], [['c:/a/b.diff', [[1, 'end']]]]],
]
for (const [name, tools, expected] of claims) {
  const actual = run.claimedReads ? run.claimedReads(tools).map(r => [r.path.split('\u0000')[1], r.spans.map(([first, last]) => [first, last === Infinity ? 'end' : last])]) : 'no claimedReads helper'
  if (JSON.stringify(actual) === JSON.stringify(expected)) console.log('ok   ' + name)
  else { failed = true; console.log('FAIL ' + name + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expected)) }
}
// Checkout identity: a seat's read credits a must-read entry only from the same checkout (review_result.py gap_verdict).
const gapStart = source.indexOf('const WHOLE = [1, Infinity]')
const gapEnd = source.indexOf('const isMandateMap', gapStart)
const judge = vm.runInNewContext(`(() => { const READ_WINDOW = 2000, SHELL_TOOLS = ['bash', 'powershell'];
  const isRecord = (v) => !!v && typeof v === 'object' && !Array.isArray(v); const GAP_REASONS = ['outside-lens', 'capability', 'missing'];
  ${source.slice(gapStart, gapEnd)}; return { claimedReads, gapVerdict } })()`)
const REPO = '/c/repo'
const judgeCases = [
  ['a foreign checkout read does not credit a repo-relative entry', REPO, ['Read:/other/checkout/plans/x.md:1-10'], 'plans/x.md', ['plans/x.md']],
  ['a drive-form read of the same checkout credits the entry', REPO, ['Read:C:/repo/plans/X.MD:1-10'], 'plans/x.md', []],
  ['a worktree read credits the worktree row', REPO + '/.claude/worktrees/w1', ['Read:/c/repo/.claude/worktrees/w1/plans/x.md:1-10'], 'plans/x.md', []],
  ['a worktree file does not credit the main row', REPO, ['Read:/c/repo/.claude/worktrees/w1/plans/x.md:1-10'], 'plans/x.md', ['plans/x.md']],
]
for (const [name, root, tools, entryPath, expectedUnread] of judgeCases) {
  const reads = judge.claimedReads(tools, root)
  const verdict = judge.gapVerdict([], [{ path: entryPath, lines: 10 }], reads, root)
  const actual = verdict.unread
  if (JSON.stringify(actual) === JSON.stringify(expectedUnread)) console.log('ok   ' + name)
  else { failed = true; console.log('FAIL ' + name + ': unread ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expectedUnread)) }
}
process.exitCode = failed ? 1 : 0
