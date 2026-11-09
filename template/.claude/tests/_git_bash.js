// The bash this repo's launchers run, resolved once by tools/sidecar_launch.git_bash (its one owner). A bare
// `bash` from Node on Windows can resolve to System32's WSL shim, which cannot see a C:/ path.
const path = require('path')
const { spawnSync } = require('child_process')

let cached
module.exports = function gitBash() {
  if (cached) return cached
  if (process.platform !== 'win32') return (cached = 'bash')
  const tools = path.resolve(__dirname, '..', 'tools')
  const done = spawnSync('python3', ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); from sidecar_launch import git_bash; print(git_bash())', tools], { encoding: 'utf8' })
  const found = (done.stdout || '').trim()
  if (done.status !== 0 || !found) throw new Error('no Git Bash: ' + (done.stderr || '').trim())
  return (cached = found)
}
