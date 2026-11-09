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

const flags = process.argv.slice(2).filter(a => a.startsWith('--'))
const argPair = process.argv.slice(2).filter(a => !a.startsWith('--'))
const pairs = argPair.length === 2 ? [argPair] : workflowPairs()
if ((argPair.length !== 0 && argPair.length !== 2) || flags.some(f => f !== '--write' && f !== '--check')) {
  console.error('usage: node schema_parity.js [--write|--check] [<script.js> <schema.json>]')
  process.exit(2)
}

const ENGINE_DIR = path.join(ROOT, '.claude', 'workflows')
// The engines carrying shared blocks differ per project: a project may add one (key_reachability) or
// omit one, and an absent workflow has no blocks to check.
const BLOCK_ENGINES = ['dispatch', 'dispatch_chains', 'review_fanout', 'explore_fanout', 'idea_fanout',
  'doc_architecture_audit', 'key_reachability', 'worklog_relevance']
  .filter(e => fs.existsSync(path.join(ENGINE_DIR, e + '.js')))
const ENGINE_SOURCE = path.join(ROOT, '.claude', 'scripts', 'engine_src', 'common.js')
const BLOCK_BEGIN = '// ENGINE-BLOCK-BEGIN '
const BLOCK_END = '// ENGINE-BLOCK-END '
const TOP_DECL = /^(const|let|var|function|async function|class)\s+([A-Za-z_$][\w$]*)/
const TOP_STOP = /^(const |let |var |function |async function |class |\/\/|[A-Za-z_$'"`\/]|\S)/

function sourceBlocks() {
  const lines = fs.readFileSync(ENGINE_SOURCE, 'utf8').split('\n')
  const blocks = new Map()
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith(BLOCK_BEGIN)) continue
    const name = lines[i].slice(BLOCK_BEGIN.length).trim()
    const end = lines.indexOf(BLOCK_END + name, i)
    if (end < 0) throw new Error('no ' + BLOCK_END + name + ' in engine_src/common.js')
    blocks.set(name, lines.slice(i + 1, end))
    i = end
  }
  return blocks
}

function declaredAt(lines, name) {
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(TOP_DECL)
    if (!m || m[2] !== name) continue
    let end = i + 1
    while (end < lines.length && !(TOP_STOP.test(lines[end]) && !/^[}\])]/.test(lines[end]))) end++
    while (end > i && lines[end - 1].trim() === '') end--
    return { start: i, end }
  }
  return null
}

function fenceAt(lines, name) {
  const begin = lines.indexOf(BLOCK_BEGIN + name)
  if (begin < 0) return null
  const end = lines.indexOf(BLOCK_END + name, begin)
  if (end < 0) throw new Error('unclosed ' + BLOCK_BEGIN + name)
  return { begin, end }
}

// Comments and blank lines are not drift: a helper's behavior is its code.
const codeOf = lines => lines.filter(l => l.trim() !== '' && !/^\s*\/\//.test(l)).map(l => l.trimEnd()).join('\n')

function blockPass(write) {
  const source = sourceBlocks()
  const problems = []
  for (const file of BLOCK_ENGINES.map(e => e + '.js')) {
    const abs = path.join(ENGINE_DIR, file)
    let lines = fs.readFileSync(abs, 'utf8').split('\n')
    let touched = false
    for (const [name, text] of source) {
      const fence = fenceAt(lines, name)
      if (fence) {
        if (codeOf(lines.slice(fence.begin + 1, fence.end)) === codeOf(text)) continue
        if (write) {
          lines = [...lines.slice(0, fence.begin + 1), ...text, ...lines.slice(fence.end)]
          touched = true
        } else {
          problems.push('DIVERGED block ' + name + ' in ' + file + ': edit .claude/scripts/engine_src/common.js, then run schema_parity.js --write')
        }
        continue
      }
      const decl = declaredAt(lines, name)
      if (!decl) continue
      if (write) {
        lines = [...lines.slice(0, decl.start), BLOCK_BEGIN + name, ...text, BLOCK_END + name, ...lines.slice(decl.end)]
        touched = true
      } else {
        problems.push('UNFENCED block ' + name + ' in ' + file + ': run schema_parity.js --write')
      }
    }
    if (touched) fs.writeFileSync(abs, lines.join('\n'))
  }
  return problems
}

let failed = 0
if (flags.includes('--write')) blockPass(true)
const blockProblems = blockPass(false)
if (blockProblems.length) {
  console.error(blockProblems.join('\n'))
  failed = 1
}
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
