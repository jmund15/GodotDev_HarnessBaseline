#!/usr/bin/env node
// schema_parity.js — fail loudly when a schema that MUST exist twice diverges.
//
// The Workflow sandbox has no require/import, so a workflow script must inline its schema as a JS
// object; the DeepSeek sidecar's --json-schema needs the same schema as a .json file. Two copies is
// a structural requirement, so the sync is checked mechanically instead of by comment.
//
// Usage:  node .claude/scripts/schema_parity.js <script.js> <schema.json>
//         node .claude/scripts/schema_parity.js            # checks every known pair below
//
// The .js file must delimit its schema with:
//     // SCHEMA-SSOT-BEGIN
//     const NAME = { ... }
//     // SCHEMA-SSOT-END
//
// Comparison is key-order-insensitive (both sides canonicalized). Exit 0 identical, 1 diverged,
// 2 bad usage / missing marker.

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..', '..')
const PAIRS = [
  ['.claude/workflows/worklog_relevance.js', '.claude/workflows/worklog_relevance.schema.json'],
  ['.claude/workflows/explore_fanout.js', '.claude/workflows/explore_fanout.schema.json'],
]

function extractInline(jsPath) {
  const src = fs.readFileSync(jsPath, 'utf8')
  const begin = src.indexOf('// SCHEMA-SSOT-BEGIN')
  const end = src.indexOf('// SCHEMA-SSOT-END')
  if (begin < 0 || end < 0 || end < begin) {
    throw new Error('no SCHEMA-SSOT-BEGIN/END block in ' + jsPath)
  }
  const block = src.slice(begin, end).replace('// SCHEMA-SSOT-BEGIN', '')
  const eq = block.indexOf('=')
  if (eq < 0) throw new Error('SSOT block in ' + jsPath + ' has no `const NAME = {...}` assignment')
  const expr = block.slice(eq + 1).trim().replace(/;\s*$/, '')
  return new Function('return (' + expr + ')')()
}

function canon(v) {
  if (Array.isArray(v)) return v.map(canon)
  if (v && typeof v === 'object') {
    return Object.keys(v).sort().reduce((o, k) => { o[k] = canon(v[k]); return o }, {})
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
const pairs = argPair.length === 2 ? [argPair] : PAIRS
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
process.exit(failed)
