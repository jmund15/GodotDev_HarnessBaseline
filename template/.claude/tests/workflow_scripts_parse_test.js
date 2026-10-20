// Every workflow script parses the way the Workflow runtime loads it: the `export const meta = {...}`
// literal on its own, then the body as an async function (top-level `await` and `return` are legal
// there). Engine tests strip the meta block before evaluating the body, so a quoting slip inside
// meta passes them and still fails every live launch.
//
//   node .claude/tests/workflow_scripts_parse_test.js [dir]
//
// `dir` defaults to .claude/workflows; a planted copy of a broken script proves the check fires.
const fs = require('fs')
const path = require('path')

const dir = process.argv[2] || path.join(__dirname, '..', 'workflows')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const META = /^export const meta = (\{[\s\S]*?^\})\r?\n/m

const files = fs.readdirSync(dir).filter(f => f.endsWith('.js')).sort()
const fails = []
for (const f of files) {
  const src = fs.readFileSync(path.join(dir, f), 'utf8')
  const m = src.match(META)
  if (!m) { fails.push(f + ': no `export const meta = {` block'); continue }
  try {
    const meta = new Function('return (' + m[1] + ')')()
    if (typeof meta.name !== 'string' || typeof meta.description !== 'string') {
      fails.push(f + ': meta lacks a string name or description')
    }
  } catch (e) {
    fails.push(f + ': meta does not parse: ' + e.message)
  }
  try {
    new AsyncFunction(src.replace(META, ''))
  } catch (e) {
    fails.push(f + ': body does not parse: ' + e.message)
  }
}
for (const f of fails) console.log('FAIL ' + f)
console.log(`${files.length - new Set(fails.map(x => x.split(':')[0])).size}/${files.length} workflow scripts parse`)
process.exit(fails.length || !files.length ? 1 : 0)
