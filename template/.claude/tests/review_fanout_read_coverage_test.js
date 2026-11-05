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
  const actual = run.claimedReads ? run.claimedReads(tools).map(r => [r.path, r.spans.map(([first, last]) => [first, last === Infinity ? 'end' : last])]) : 'no claimedReads helper'
  if (JSON.stringify(actual) === JSON.stringify(expected)) console.log('ok   ' + name)
  else { failed = true; console.log('FAIL ' + name + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expected)) }
}
process.exitCode = failed ? 1 : 0
