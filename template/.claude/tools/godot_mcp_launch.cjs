#!/usr/bin/env node
'use strict'
/**
 * Cross-platform launcher for the `godot` MCP server (`godot-mcp-runtime`).
 *
 * Why this exists: the project `.mcp.json` used to say `cmd /c npx -y godot-mcp-runtime@<pin>` with
 * `GODOT_PATH=${GODOT_BIN}`, which is three separate failures on a cloud Linux box.
 *
 *   - `cmd /c` is a Windows wrapper. On Linux the server never started at all, and the Godot MCP
 *     was therefore absent from every cloud session.
 *   - `GODOT_BIN` is a workstation environment variable. On cloud it is unset, so the literal
 *     `${GODOT_BIN}` reached the runtime and it refused to launch.
 *   - npx resolves its package from its own `_npx` cache, which a fresh snapshot has never filled.
 *
 * This file replaces the wrapper with an argv a shell is not needed to build, resolves the engine
 * from what the machine actually has, and lets `cloud-setup.sh` pre-warm the npx cache.
 *
 * The engine resolution order is deliberately narrow:
 *   1. `GODOT_PATH`, when it names an EXISTING FILE. A bare `godot` is refused even when a file of
 *      that name happens to exist: the runtime accepts the bare name as a PATH lookup, and a PATH
 *      lookup is how the wrong-engine-binary incident happened (a stale user-scope 4.6.2 server
 *      rewrote `.godot` caches and downgraded the csproj SDK pin).
 *   2. The one-line pointer file `cloud-setup.sh` writes, `<prefix>/godot/GODOT_BIN`, under `/opt`
 *      (root or passwordless sudo) then `~/.local`.
 *   3. Nothing: exit 1 and name where the engine comes from -- `/workstation_setup` Phase 1 pin 2
 *      locally, `.claude/cloud-setup.sh` on cloud.
 *
 * Everything the process touches is injectable (`main(deps)`), so the proof runs with no engine,
 * no Xvfb and no npm.
 */
const childProcess = require('node:child_process')
const os = require('node:os')
const path = require('node:path')
const fs = require('node:fs')

const PACKAGE = 'godot-mcp-runtime'
const USAGE = 'usage: godot_mcp_launch.cjs ' + PACKAGE + '@<version>\n' +
  '  (the project .mcp.json godot entry passes the pinned spec; nothing is resolved by default)\n'
const ENGINE_HINT =
  'no Godot engine found. Set GODOT_PATH to the engine binary, or run /workstation_setup ' +
  '(Phase 1, pin 2) so the pin is installed and recorded; on cloud the environment Setup script ' +
  '.claude/cloud-setup.sh installs the pinned engine and writes the pointer file this launcher ' +
  'reads. A bare `godot` from PATH is never used: the engine version must be the pinned one.'
const POINTER_NAME = 'GODOT_BIN'
const SCREEN = '1280x1024x24'
const SIGNAL_NUMBERS = { SIGHUP: 1, SIGINT: 2, SIGQUIT: 3, SIGTERM: 15 }
const FORWARDED = ['SIGINT', 'SIGTERM', 'SIGHUP']

class EngineNotFound extends Error {}

// A bare command name is not a path. `existsSync('godot')` answers for the CWD, and the runtime's
// own PATH fallback accepts the literal -- both are the uncontrolled lookup this refuses.
function isPathLike(value) {
  return /[\\/]/.test(value)
}

function engineFromPointer(root, deps) {
  const pointer = path.join(root, 'godot', POINTER_NAME)
  if (!deps.existsSync(pointer)) return null
  let value = ''
  try { value = String(deps.readFile(pointer, 'utf8') || '').trim() } catch (err) { return null }
  return value && isPathLike(value) && deps.isFile(value) ? value : null
}

/** The engine binary this machine should run, or null when none is pinned. */
function resolveEngine(options) {
  const opts = options || {}
  const env = opts.env || process.env
  const deps = {
    existsSync: opts.existsSync || fs.existsSync,
    isFile: opts.isFile || ((p) => { try { return fs.statSync(p).isFile() } catch (err) { return false } }),
    readFile: opts.readFile || ((p, enc) => fs.readFileSync(p, enc)),
  }
  const roots = opts.sdkRoots ||
    ['/opt', path.join((opts.homedir || os.homedir)(), '.local')]

  const configured = String(env.GODOT_PATH || '').trim()
  if (configured && isPathLike(configured) && deps.isFile(configured)) return configured
  for (const root of roots) {
    const found = engineFromPointer(root, deps)
    if (found) return found
  }
  return null
}

/** The lowest free X display at or below 99, or null when every one is taken. POSIX paths only:
 *  Xvfb exists on Linux, and joining with `path` would emit `\` on a Windows host. */
function findFreeDisplay(options) {
  const opts = options || {}
  const existsSync = opts.existsSync || fs.existsSync
  const x11 = opts.x11Dir || '/tmp/.X11-unix'
  for (let n = opts.highest || 99; n > 0; n--) {
    if (!existsSync('/tmp/.X' + n + '-lock') && !existsSync(x11 + '/X' + n)) return n
  }
  return null
}

/** The process to run, the argv, the env it inherits and whether Xvfb has to come first. */
function buildPlan(spec, options) {
  const opts = options || {}
  const platform = opts.platform || process.platform
  const env = Object.assign({}, opts.env || process.env)

  const engine = resolveEngine(opts)
  if (!engine) throw new EngineNotFound(ENGINE_HINT)
  env.GODOT_PATH = engine

  let xvfb = false
  let display = null
  if (platform === 'linux' && !String(env.DISPLAY || '').trim()) {
    const free = findFreeDisplay(opts)
    if (free !== null) { xvfb = true; display = ':' + free; env.DISPLAY = display }
  }

  // `npx` is `npx.cmd` on Windows, which only cmd.exe can start; passing args with `shell: true`
  // is deprecated in Node, so Windows names cmd.exe explicitly. The spec is a fixed argv element
  // from `.mcp.json`, never user input.
  const win = platform === 'win32'
  return {
    command: win ? 'cmd.exe' : 'npx',
    args: win ? ['/d', '/s', '/c', 'npx', '-y', spec] : ['-y', spec],
    spawnOptions: { stdio: 'inherit', shell: false },
    env, engine, xvfb, display,
  }
}

/**
 * Run the server. `code` Xvfb first when the plan asks for it, then npx with inherited stdio,
 * exit with the child's code, re-raise the child's exit signal, and forward an inbound signal to
 * the child so a cancelled session does not leave an engine or an Xvfb behind.
 */
function main(deps) {
  const d = deps || {}
  const argv = d.argv || process.argv.slice(2)
  const spawn = d.spawn || childProcess.spawn
  const exit = d.exit || ((code) => process.exit(code))
  const selfKill = d.selfKill || ((signal) => process.kill(process.pid, signal))
  const on = d.on || ((signal, handler) => process.on(signal, handler))
  const writeErr = d.writeErr || ((text) => process.stderr.write(text))
  const spec = String(argv[0] || '').trim()
  if (!spec) { writeErr(USAGE); return exit(1) }

  let plan
  try {
    plan = buildPlan(spec, d)
    const values = JSON.parse(childProcess.execFileSync('python3', [
      path.join(__dirname, '../scripts/engine_userdata.py'), '--repo', path.join(__dirname, '../..'),
    ], { encoding: 'utf8', env: Object.assign({}, process.env, plan.env), windowsHide: true }))
    Object.assign(plan.env, values)
    writeErr('USERDIR=' + values.USERDIR + '\n')
  } catch (err) {
    writeErr('godot_mcp_launch: ' + err.message + '\n')
    return exit(1)
  }

  let xvfb = null
  if (plan.xvfb) {
    xvfb = spawn('Xvfb', [plan.display, '-screen', '0', SCREEN], { stdio: 'ignore' })
    if (xvfb && typeof xvfb.on === 'function') {
      xvfb.on('error', (err) => writeErr('godot_mcp_launch: Xvfb did not start (' + err.message +
                                         '); headless operations continue without a display\n'))
    }
  }
  const stopXvfb = () => { if (xvfb) { try { xvfb.kill('SIGTERM') } catch (err) { /* already gone */ } } }

  const child = spawn(plan.command, plan.args, Object.assign({}, plan.spawnOptions, { env: plan.env }))
  for (const signal of d.signals || FORWARDED) {
    on(signal, () => {
      try { child.kill(signal) } catch (err) { /* already gone */ }
      stopXvfb()
      exit(128 + (SIGNAL_NUMBERS[signal] || 0))
    })
  }
  child.on('error', (err) => {
    stopXvfb()
    writeErr('godot_mcp_launch: cannot run ' + plan.command + ' (' + err.message + ')\n')
    exit(1)
  })
  child.on('exit', (code, signal) => {
    stopXvfb()
    if (signal) {
      try { selfKill(signal) } catch (err) { /* unsupported signal: fall through to the code */ }
      exit(128 + (SIGNAL_NUMBERS[signal] || 0))
      return
    }
    exit(code)
  })
}

module.exports = {
  ENGINE_HINT, EngineNotFound, FORWARDED, POINTER_NAME, SIGNAL_NUMBERS, USAGE,
  buildPlan, findFreeDisplay, isPathLike, main, resolveEngine,
}

if (require.main === module) main()
