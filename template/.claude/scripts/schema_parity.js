#!/usr/bin/env node
// schema_parity.js — fail loudly when a schema that MUST exist twice diverges.
//
// The Workflow sandbox has no require/import, so a workflow script must inline its schema as a JS
// object; the DeepSeek sidecar's --json-schema needs the same schema as a .json file. Two copies is
// a structural requirement, so the sync is checked mechanically instead of by comment.
//
// Usage:  node .claude/scripts/schema_parity.js <script.js> <schema.json>
//         node .claude/scripts/schema_parity.js            # checks every pair in .claude/workflows/
//
// With no arguments, every .claude/workflows/<name>.js carrying the marker pairs with
// <name>.schema.json beside it.
// The .js file must delimit its schema with:
//     // SCHEMA-SSOT-BEGIN
//     const NAME = { ... }
//     // SCHEMA-SSOT-END
//
// Comparison is key-order-insensitive and ignores string `description`s (both sides canonicalized). With no
// arguments, and after an explicit pair, it also checks each workflow's EVIDENCE-SSOT schema against
// .claude/schemas/evidence.json, and that every such workflow's EVIDENCE_RULES sentence is one text.
// Exit 0 identical, 1 diverged, 2 bad usage / missing marker.

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', '..')
const MARKER = '// SCHEMA-SSOT-BEGIN'

function workflowPairs() {
  const dir = path.join(ROOT, '.claude', 'workflows')
  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.js') && fs.readFileSync(path.join(dir, f), 'utf8').includes(MARKER))
    .sort()
    .map(f => ['.claude/workflows/' + f, '.claude/workflows/' + f.replace(/\.js$/, '.schema.json')])
}

function extractInline(jsPath) {
  const src = fs.readFileSync(jsPath, 'utf8')
  const begin = src.indexOf(MARKER)
  const end = src.indexOf('// SCHEMA-SSOT-END')
  if (begin < 0 || end < 0 || end < begin) {
    throw new Error('no SCHEMA-SSOT-BEGIN/END block in ' + jsPath)
  }
  const block = src.slice(begin, end).replace(MARKER, '')
  const eq = block.indexOf('=')
  if (eq < 0) throw new Error('SSOT block in ' + jsPath + ' has no `const NAME = {...}` assignment')
  const expr = block.slice(eq + 1).trim().replace(/;\s*$/, '')
  const evidence = src.match(/\/\/ EVIDENCE-SSOT-BEGIN([\s\S]*?)\/\/ EVIDENCE-SSOT-END/)
  return new Function((evidence ? evidence[1] : '') + '\nreturn (' + expr + ')')()
}

// Key-order-insensitive, and blind to prose: a string `description` is documentation, not schema.
function canon(v) {
  if (Array.isArray(v)) return v.map(canon)
  if (v && typeof v === 'object') {
    return Object.keys(v).sort().filter(k => !(k === 'description' && typeof v[k] === 'string'))
      .reduce((o, k) => { o[k] = canon(v[k]); return o }, {})
  }
  return v
}

function diff(a, b, at, out) {
  const ka = a && typeof a === 'object' ? Object.keys(a) : []
  const kb = b && typeof b === 'object' ? Object.keys(b) : []
  if (JSON.stringify(canon(a)) === JSON.stringify(canon(b))) return
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) {
    out.push('  ' + at + '\n    js:   ' + JSON.stringify(a) + '\n    json: ' + JSON.stringify(b))
    return
  }
  for (const k of new Set([...ka, ...kb])) {
    if (!(k in a)) { out.push('  ' + at + '.' + k + ' — missing on the JS side'); continue }
    if (!(k in b)) { out.push('  ' + at + '.' + k + ' — missing on the JSON side'); continue }
    diff(a[k], b[k], at + '.' + k, out)
  }
}

const argPair = process.argv.slice(2)
const pairs = argPair.length === 2 ? [argPair] : workflowPairs()
if (argPair.length !== 0 && argPair.length !== 2) {
  console.error('usage: node schema_parity.js [<script.js> <schema.json>]')
  process.exit(2)
}

let failed = 0
for (const [js, json] of pairs) {
  const jsAbs = path.isAbsolute(js) ? js : path.join(ROOT, js)
  const jsonAbs = path.isAbsolute(json) ? json : path.join(ROOT, json)
  let inline, file
  try {
    inline = extractInline(jsAbs)
    file = JSON.parse(fs.readFileSync(jsonAbs, 'utf8'))
  } catch (e) {
    console.error('ERROR ' + js + ' <-> ' + json + ': ' + e.message)
    failed = 2
    continue
  }
  if (JSON.stringify(canon(inline)) === JSON.stringify(canon(file))) {
    console.log('OK   ' + js + ' <-> ' + json)
  } else {
    const out = []
    diff(inline, file, '$', out)
    console.error('DIVERGED ' + js + ' <-> ' + json)
    console.error(out.join('\n'))
    failed = failed || 1
  }
}
// Every workflow carrying an EVIDENCE-SSOT block must hold the same evidence schema as schemas/evidence.json.
// Only what a validator consumes is compared; the engines' close-check source is covered by their behavioral suites.
const evidenceFile = JSON.parse(fs.readFileSync(path.join(ROOT, '.claude/schemas/evidence.json'), 'utf8'))
const workflowDir = path.join(ROOT, '.claude/workflows')
const engines = fs.readdirSync(workflowDir).sort().filter(f => f.endsWith('.js'))
  .filter(f => fs.readFileSync(path.join(workflowDir, f), 'utf8').includes('// EVIDENCE-SSOT-BEGIN'))
  .map(f => f.replace(/\.js$/, ''))
for (const name of engines) {
  const source = fs.readFileSync(path.join(workflowDir, name + '.js'), 'utf8')
  const block = source.match(/\/\/ EVIDENCE-SSOT-BEGIN[\s\S]*?\/\/ EVIDENCE-SSOT-END/)
  let schema
  try { schema = block && new Function(block[0] + '\nreturn EVIDENCE_SCHEMA')() } catch (e) { schema = undefined }
  if (!schema || JSON.stringify(canon(schema)) !== JSON.stringify(canon(evidenceFile))) {
    console.error('DIVERGED evidence schema: ' + name)
    failed = failed || 1
  }
}
// The rules sentence each engine shows its agents has one copy per engine; every copy must be one text.
const rulesOf = name => {
  const m = fs.readFileSync(path.join(workflowDir, name + '.js'), 'utf8').match(/^const EVIDENCE_RULES = (.*)$/m)
  return m ? m[1].trim() : null
}
const ruleTexts = new Map(engines.map(name => [name, rulesOf(name)]))
if (new Set(ruleTexts.values()).size > 1) {
  console.error('DIVERGED EVIDENCE_RULES text: ' + [...ruleTexts].map(([n, t]) => n + '=' + (t ? t.length + ' chars' : 'absent')).join(', '))
  failed = failed || 1
}
if (!failed) console.log('OK   evidence schema parity (' + engines.length + ' engines)')
process.exit(failed)
