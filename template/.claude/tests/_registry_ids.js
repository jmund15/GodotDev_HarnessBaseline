// The registry's exact Anthropic judge ids (model_registry.anthropic_ids), the list build_plan passes the engine as
// args.anthropicIds. Engine proofs read it here so a registry row added for a new judge never strands a copied list.
const { execFileSync } = require('child_process')
const path = require('path')

const TOOLS = path.join(__dirname, '..', 'tools')
module.exports = JSON.parse(execFileSync(process.platform === 'win32' ? 'python' : 'python3',
  ['-c', 'import json, sys; sys.path.insert(0, sys.argv[1]); import model_registry; print(json.dumps(model_registry.anthropic_ids()))', TOOLS],
  { encoding: 'utf8' }))
