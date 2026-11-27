const assert = require('assert/strict')
const fs = require('fs')
const path = require('path')
const vm = require('vm')
const file = path.resolve(__dirname, '../scripts/schema_parity.js')
function check(mutate) {
  let code
  const fakeFs = Object.assign({}, fs, {readFileSync: (file, encoding) => {
    let value = fs.readFileSync(file, encoding)
    if (mutate && String(file).endsWith('review_fanout.js')) value = mutate(value)
    return value
  }})
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {require: name => name === 'fs' ? fakeFs : require(name), __dirname: path.dirname(file), console: {log() {}, error() {}}, process: {argv: ['node', file], exit: n => {code = n}}})
  return code
}
// adaptation.json is consumer-local: a consumer whose copy carries no `proof_bindings` (the key this
// project authors to bind its workflows to proofs) binds no engine here, so only the schema checks run.
const adaptation = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../skills/project_subsystems/adaptation.json'), 'utf8'))
const bindings = adaptation.proof_bindings || {}
const bound = Object.keys(bindings).filter(k => k.startsWith('.claude/workflows/') && bindings[k].includes('engine_close_check_remaining_test.js')).map(k => path.basename(k, '.js'))
for (const engine of bound) {
  const twin = path.resolve(__dirname, '../workflows', engine + '.schema.json')
  if (fs.existsSync(twin)) JSON.parse(fs.readFileSync(twin, 'utf8'))
}
assert.equal(check(), 0, 'all inline schemas and contract blocks must match')
// Consumed schema semantics fail; wording, comments and unrelated source never do.
// The rules sentence has one copy per engine (a Workflow script cannot import a shared constant): one engine's
// rewording is drift, so the copies hold one text.
assert.notEqual(check(s => s.replace('const EVIDENCE_RULES = ', 'const EVIDENCE_RULES = "reworded" + ')), 0, 'one engine rewording the rules sentence must fail')
assert.equal(check(s => s.replace('const VERDICTS = [', '// reworded\nconst VERDICTS = [')), 0, 'a comment in the close-check block is not schema')
assert.equal(check(s => s.replace(/("description": ")/, '$1reworded ')), 0, 'a schema description is not schema')
assert.notEqual(check(s => s.replace('"type": "array"', '"type": "object"')), 0, 'a type change in an engine copy must fail')
assert.notEqual(check(s => s.replace('"oneOf"', '"anyOf"')), 0, 'a structural change in an engine copy must fail')
if (Object.keys(bindings).length) {
  for (const engine of bound) {
    assert.ok(fs.existsSync(path.resolve(__dirname, '../workflows', engine + '.js')), engine + ': a bound engine must exist')
  }
  assert.ok((adaptation.proof_corpus || {})['test_native_caller_contracts.py'].includes('.claude/workflows'))
} else {
  console.log('SKIP proof bindings: this consumer\'s adaptation.json carries no proof_bindings')
}
console.log('PASS schema parity: schema twins and evidence schema semantics, descriptions ignored, one rules text, schema drift rejected, proof bindings')
