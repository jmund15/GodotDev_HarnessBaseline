#!/usr/bin/env node
// Proofs for .claude/tools/semantic_search_idle_proxy.mjs — the out-of-process idle-unloading
// frontend shim for the DreB semantic-search MCP plugin.
//
// Run: node --test .claude/tests/test_semantic_search_idle_proxy.mjs
//
// Every backend child this suite starts is one it owns; each is closed by the suite or by the
// proxy under test, and PIDs are asserted gone before the run ends.
//
// SEMANTIC_SEARCH_IDLE_TEST_PROXY points the suite at another copy of the shim, for planted-defect
// runs: each planted edit must turn its named case red.

import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { mkdirSync, rmSync, rmdirSync, writeFileSync, readFileSync, existsSync, cpSync, realpathSync } from 'node:fs'
import net from 'node:net'
import { createRequire } from 'node:module'
import path from 'node:path'
import { homedir, tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..')
const PROXY = process.env.SEMANTIC_SEARCH_IDLE_TEST_PROXY
	? path.resolve(process.env.SEMANTIC_SEARCH_IDLE_TEST_PROXY)
	: path.join(REPO, '.claude', 'tools', 'semantic_search_idle_proxy.mjs')
// Under the OS temp root: a fixture nested in a deep checkout passes Windows' path limit and git cannot stat it.
const SCRATCH = path.join(tmpdir(), 'semantic_search_idle', `run-${process.pid}-${Date.now()}`)
const BACKEND_FIRST_SEEN = new Map()
const REGISTRY = path.join(homedir(), '.claude', 'plugins', 'installed_plugins.json')
if (!process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT && !existsSync(REGISTRY)) {
	console.log('PLATFORM-SKIP-PROOF test_semantic_search_idle_proxy.mjs: no semantic-search plugin is installed on this machine')
	process.exit(0)
}

// ---------------------------------------------------------------------------
// Plugin root discovery: the single active USER-scope registration.
// ---------------------------------------------------------------------------
function activePluginRoot() {
	if (process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT) {
		return process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT
	}
	const reg = JSON.parse(readFileSync(REGISTRY, 'utf8'))
	const entries = (reg.plugins || {})['semantic-search@dreb'] || []
	const user = entries.filter((e) => e.scope === 'user')
	assert.equal(user.length, 1, 'expected exactly one user-scope semantic-search@dreb registration')
	return user[0].installPath
}

const PLUGIN = activePluginRoot()
const PLUGIN_VERSION = JSON.parse(readFileSync(path.join(PLUGIN, 'package.json'), 'utf8')).version

// ---------------------------------------------------------------------------
// SDK loaded from the plugin root — the same copy the backend runs on.
// ---------------------------------------------------------------------------
const requireFromPlugin = createRequire(path.join(PLUGIN, 'package.json'))
const importFromPlugin = (spec) => import(pathToFileURL(requireFromPlugin.resolve(spec)).href)

const [ { Client }, { StdioClientTransport }, mcpTypes ] = await Promise.all([
	importFromPlugin('@modelcontextprotocol/sdk/client/index.js'),
	importFromPlugin('@modelcontextprotocol/sdk/client/stdio.js'),
	importFromPlugin('@modelcontextprotocol/sdk/types.js'),
])

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const FIXTURE_FILES = {
	'alpha_module.py': [
		'def compute_magma_reaction_pressure(fire, earth):',
		'    """Combine fire and earth traits into a magma pressure value."""',
		'    return (fire * 0.7) + (earth * 0.3)',
		'',
	].join('\n'),
	'beta_notes.md': [
		'# Idle unload notes',
		'',
		'The idle unload returns the native ONNX working set to the operating system.',
		'When the last search completes and the idle window elapses, the backend child',
		'exits and its private bytes fall back to a small baseline.',
		'',
	].join('\n'),
	'gamma_config.json': JSON.stringify({ idle_minutes: 10, unload: 'native-onnx' }, null, 2) + '\n',
	'delta_scene.gd': [
		'extends Node',
		'',
		'func _ready() -> void:',
		'\tvar potency := compute_magma_reaction_pressure(1.0, 2.0)',
		'\tprint(potency)',
		'',
	].join('\n'),
	'epsilon_bottles.txt': [
		'Bottled reagents and reaction residues sit on the shelf.',
		'A bottle of steady hands, a bottle of quick ash, a bottle of slow iron.',
		'',
	].join('\n'),
	'.gitignore': '.search-index/\n',
}

function git(args, cwd) {
	return execFileSync('git', args, {
		cwd,
		stdio: ['ignore', 'pipe', 'pipe'],
		env: { ...process.env, GIT_CONFIG_GLOBAL: path.join(SCRATCH, 'gitconfig'), GIT_CONFIG_NOSYSTEM: '1' },
	})
}

/** Build an immutable fixture git repo at `root` (fresh each call). */
function buildFixture(root) {
	rmSync(root, { recursive: true, force: true })
	mkdirSync(root, { recursive: true })
	mkdirSync(SCRATCH, { recursive: true })
	writeFileSync(
		path.join(SCRATCH, 'gitconfig'),
		'[user]\n\tname = idle-proxy-proof\n\temail = idle-proxy-proof@example.invalid\n[init]\n\tdefaultBranch = main\n',
	)
	for (const [name, body] of Object.entries(FIXTURE_FILES)) {
		writeFileSync(path.join(root, name), body)
	}
	git(['init', '-q'], root)
	git(['add', '-A'], root)
	git(['commit', '-q', '-m', 'fixture'], root)
	return root
}

/** Copy the fixture tracked tree (and optionally its index) to `dest`. */
function copyFixture(src, dest, withIndex) {
	rmSync(dest, { recursive: true, force: true })
	mkdirSync(dest, { recursive: true })
	execFileSync('git', ['clone', '-q', '--no-hardlinks', src, dest], {
		stdio: ['ignore', 'pipe', 'pipe'],
		env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1' },
	})
	if (withIndex && existsSync(path.join(src, '.search-index'))) {
		cpSync(path.join(src, '.search-index'), path.join(dest, '.search-index'), { recursive: true })
	}
	return dest
}

/**
 * A fixture root of its own. Backends are shared per index root, so a case that must own the
 * root's only backend searches a root no other live frontend uses.
 */
function ownRoot(name) {
	return copyFixture(fx, path.join(SCRATCH, 'roots', name), true)
}

// ---------------------------------------------------------------------------
// Frontend harness
// ---------------------------------------------------------------------------
const DEBUG = 'SEMANTIC_SEARCH_IDLE_DEBUG=1'

/**
 * Start the proxy under test as an MCP server and connect a real MCP client to it.
 * Returns the client, the proxy's own PID and a live stderr tail.
 */
async function startFrontend({ cwd, env = {}, pluginRoot = PLUGIN } = {}) {
	const transport = new StdioClientTransport({
		command: process.execPath,
		args: [PROXY, pluginRoot],
		cwd: cwd ?? REPO,
		env: { ...process.env, ...env },
		stderr: 'pipe',
	})
	const stderr = []
	let pendingLine = ''
	transport.stderr?.on('data', (d) => {
		stderr.push(String(d))
		pendingLine += String(d)
		let end
		while ((end = pendingLine.indexOf('\n')) >= 0) {
			const line = pendingLine.slice(0, end)
			pendingLine = pendingLine.slice(end + 1)
			const start = line.match(/backend-start pid=(\d+)/)
			if (start) BACKEND_FIRST_SEEN.set(Number(start[1]), Date.now())
		}
	})
	const client = new Client({ name: 'idle-proxy-proof', version: '0.0.1' }, { capabilities: {} })
	client.onerror = () => {}
	await client.connect(transport)
	return {
		client,
		transport,
		stderr,
		pid: transport.pid,
		text: () => stderr.join(''),
		pids: (kind = 'backend-start pid') => [...stderr.join('').matchAll(new RegExp(`${kind}=(\\d+)`, 'g'))].map((m) => Number(m[1])),
		async close() {
			try { await client.close() } catch { /* already gone */ }
		},
	}
}

/** Spawn the proxy directly (not as an MCP server) — for startup-validation cases. */
function spawnRaw({ cwd, env = {}, pluginRoot = PLUGIN } = {}) {
	const child = spawn(process.execPath, [PROXY, pluginRoot], {
		cwd: cwd ?? REPO,
		env: { ...process.env, ...env },
		stdio: ['pipe', 'pipe', 'pipe'],
	})
	const out = { stdout: '', stderr: '' }
	child.stdout.on('data', (d) => (out.stdout += String(d)))
	child.stderr.on('data', (d) => (out.stderr += String(d)))
	const exited = new Promise((resolve) => child.once('exit', (code) => resolve({ code, ...out })))
	return { child, out, exited }
}

function pidAlive(pid) {
	try { process.kill(pid, 0); return true } catch { return false }
}

function stillRecordedBackend(pid, firstSeen, rows) {
	const current = rows.find((row) => row.pid === pid)
	if (!current) return false
	if (!Number.isFinite(firstSeen) || !Number.isFinite(current.bornMs)) return true
	return current.bornMs <= firstSeen
}

function nodeProcessBirths() {
	const script = `ConvertTo-Json -Compress -InputObject @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | ForEach-Object { @{ pid = $_.ProcessId; bornMs = ([DateTimeOffset]$_.CreationDate).ToUnixTimeMilliseconds() } })`
	return JSON.parse(execFileSync('powershell.exe', ['-NoProfile', '-Command', script], { encoding: 'utf8' }))
}

async function waitForExit(pid, timeoutMs = 15000) {
	const deadline = Date.now() + timeoutMs
	while (Date.now() < deadline) {
		if (!pidAlive(pid)) return true
		await new Promise((r) => setTimeout(r, 100))
	}
	return !pidAlive(pid)
}

async function listTools(frontend) {
	return frontend.client.listTools()
}

async function callSearch(frontend, args, timeout) {
	return frontend.client.callTool({ name: 'search', arguments: args }, undefined, timeout ? { timeout } : undefined)
}

test('teardown detects PID reuse without mistaking a peer for an owned backend', () => {
	assert.equal(stillRecordedBackend(42, 100, [{ pid: 42, bornMs: 150 }]), false)
	assert.equal(stillRecordedBackend(42, 100, [{ pid: 42, bornMs: 50 }]), true)
	assert.equal(stillRecordedBackend(42, 100, []), false)
	assert.equal(stillRecordedBackend(42, undefined, [{ pid: 42, bornMs: 50 }]), true)
	if (process.platform === 'win32') {
		const rows = nodeProcessBirths()
		assert.ok(Number.isFinite(rows.find((row) => row.pid === process.pid)?.bornMs))
		assert.equal(stillRecordedBackend(process.pid, Date.now(), rows), true)
	}
})

const resultText = (res) => (res.content || []).map((c) => c.text ?? '').join('')

test('C# hook parses every required declaration with names and no plaintext fallback', () => {
	const hook = path.join(REPO, '.claude/tools/semantic_search_csharp_hook.mjs')
	const report = JSON.parse(execFileSync(process.execPath, [hook, '--check', PLUGIN], { encoding: 'utf8' }))
	assert.equal(report.fileType, 'csharp')
	for (const name of ['Armor', 'Health', 'IArmor', 'Element', 'Hit', 'Packet', 'ApplyDamageReduction', 'Armor', 'Value']) {
		assert.ok(report.chunks.some((c) => c.name === name), `missing C# symbol ${name}`)
	}
	for (const kind of ['class', 'struct', 'interface', 'enum', 'record', 'method', 'constructor', 'property']) {
		assert.ok(report.chunks.some((c) => c.kind === kind), `missing C# kind ${kind}`)
	}
	assert.equal(report.nonCsharpType, 'python')
	assert.equal(report.parseFailureRejected, true)
})

test('C# launch injection returns named AST chunks through the proxy', async () => {
	const root = ownRoot('csharp')
	writeFileSync(path.join(root, 'Armor.cs'), 'public class Armor {\n    public float ApplyDamageReduction(float damage) => damage * 0.5f;\n}\n')
	const fe = await startFrontend({ cwd: root })
	try {
		const res = await callSearch(fe, { query: 'ApplyDamageReduction', searchDir: root, limit: 5 })
		assert.ok(!res.isError, resultText(res))
		assert.match(resultText(res), /Armor\.cs/)
		assert.match(resultText(res), /method ApplyDamageReduction/)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})


// ---------------------------------------------------------------------------
// S1 — thin end-to-end tracer
// ---------------------------------------------------------------------------
const OWNED_PIDS = new Set()
const trackPids = (f) => { for (const p of f.pids()) OWNED_PIDS.add(p) }

let fx = null
let shared = null

// S2.4's 65 s call starts in before() so its wait overlaps S1 to S2.3; the checks that need the
// call's own moment (the child still alive as the reply lands) run here, the rest in the test.
async function runSlowCall() {
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-slow'))
	const logPath = path.join(SCRATCH, 'fake-slow.log')
	rmSync(logPath, { force: true })
	const fe = await startFrontend({
		cwd: fx,
		pluginRoot: fakeRoot,
		env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', FAKE_DELAY_MS: '65000', FAKE_LOG: logPath, ...IDLE_ENV },
	})
	try {
		const started = Date.now()
		const res = await callSearch(fe, { query: 'slow', searchDir: '.' }, 120000)
		const elapsed = Date.now() - started
		const [pid] = fe.pids()
		OWNED_PIDS.add(pid)
		return { res, elapsed, alive: pidAlive(pid), logPath }
	} finally {
		await fe.close()
	}
}

let slowCall = null

before(async () => {
	mkdirSync(SCRATCH, { recursive: true })
	fx = buildFixture(path.join(SCRATCH, 'fixture-a'))
	buildFixture(path.join(SCRATCH, 'fixture-b'))
	slowCall = new Promise((r) => setTimeout(r, 0)).then(runSlowCall) // after the module finishes loading
	slowCall.catch(() => {})
	shared = await startFrontend({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1' } })
})

after(async () => {
	if (slowCall) await slowCall
	if (shared) trackPids(shared)
	if (shared) await shared.close()
	let survivors = []
	for (const pid of OWNED_PIDS) {
		if (!(await waitForExit(pid, 10000))) survivors.push(pid)
	}
	if (process.platform === 'win32' && survivors.length) {
		const rows = nodeProcessBirths()
		survivors = survivors.filter((pid) => stillRecordedBackend(pid, BACKEND_FIRST_SEEN.get(pid), rows))
	}
	rmSync(SCRATCH, { recursive: true, force: true, maxRetries: 5 })
	assert.deepEqual(survivors, [], `owned backends outlived the suite: ${survivors.join(', ')}`)
})

test('S1.1 initialize + tools/list expose the pinned upstream contract with no backend child', async () => {
	const fe = await startFrontend({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1' } })
	try {
		const listed = await listTools(fe)
		assert.equal(listed.tools.length, 1, 'exactly one tool is registered')
		const tool = listed.tools[0]
		assert.equal(tool.name, 'search')
		assert.deepEqual(tool.inputSchema.required, ['query', 'searchDir'])
		assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ['limit', 'query', 'rebuild', 'restrictToDir', 'searchDir'])
		assert.match(tool.description, /Search the codebase using natural language queries/)

		const serverVersion = fe.client.getServerVersion()
		assert.equal(serverVersion.name, 'semantic-search')
		assert.equal(serverVersion.version, PLUGIN_VERSION)

		const caps = fe.client.getServerCapabilities()
		assert.ok(caps.tools, 'tools capability advertised')
		assert.ok(caps.logging, 'logging capability advertised')

		assert.deepEqual(fe.pids(), [], 'tools/list must not start a backend child')
	} finally {
		await fe.close()
	}
})

test('S1.2 search forwards to the real backend and preserves searchDir resolution against cwd', async () => {
	const res = await callSearch(shared, { query: 'idle unload returns native memory to the operating system', searchDir: '.', limit: 5 })
	trackPids(shared)
	assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
	const text = resultText(res)
	assert.match(text, /beta_notes\.md/, 'expected fixture hit in results')
	assert.match(text, /\[Index: \d+ files, \d+ chunks\]/)
	assert.equal(shared.pids().length, 1, 'exactly one backend child served the search')
})

test('S1.3 an explicit absolute searchDir different from cwd is honoured', async () => {
	const other = path.join(SCRATCH, 'fixture-b')
	const res = await callSearch(shared, { query: 'bottled reagents', searchDir: other, limit: 5 })
	assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
	assert.match(resultText(res), /epsilon_bottles\.txt/)
	assert.ok(!/fixture-a/.test(resultText(res)), 'results come from fixture-b, not cwd')
})

test('S1.4 backend logging notifications are re-emitted to the frontend client', async () => {
	const logs = []
	shared.client.setNotificationHandler(mcpTypes.LoggingMessageNotificationSchema, (n) => { logs.push(n.params) })
	try {
		await callSearch(shared, { query: 'magma reaction pressure', searchDir: '.', rebuild: true, limit: 3 })
		assert.ok(logs.length > 0, 'expected forwarded notifications/message during a rebuild')
		assert.ok(logs.every((l) => l.level === 'info' && l.logger === 'semantic-search'))
	} finally {
		shared.client.setNotificationHandler(mcpTypes.LoggingMessageNotificationSchema, undefined)
	}
})

test('S1.5 an unknown tool is answered without starting a backend child', async () => {
	const fe = await startFrontend({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1' } })
	try {
		const res = await fe.client.callTool({ name: 'not-a-tool', arguments: {} })
		assert.equal(res.isError, true)
		assert.equal(resultText(res), 'Unknown tool: not-a-tool')
		assert.deepEqual(fe.pids(), [], 'no backend child for an unknown tool')
	} finally {
		await fe.close()
	}
})

test('S1.6 stock error results are forwarded untouched', async () => {
	const res = await callSearch(shared, { query: '   ', searchDir: '.', limit: 3 })
	assert.equal(res.isError, true)
	assert.equal(resultText(res), 'Search query cannot be empty.')
})

test('S1.7 closing the frontend closes only its own backend child', async () => {
	const fe = await startFrontend({ cwd: ownRoot('s1-7'), env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1' } })
	const res = await callSearch(fe, { query: 'bottles', searchDir: '.', limit: 3 })
	assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
	const [pid] = fe.pids()
	assert.ok(pid, 'expected a backend child pid')
	OWNED_PIDS.add(pid)
	await fe.close()
	assert.ok(await waitForExit(pid), `backend child ${pid} survived frontend exit`)
})

test('S1.9 an idle window past the timer range fails loudly at startup', async () => {
	const { child, exited } = spawnRaw({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_MINUTES: '99999999' } })
	const outcome = await Promise.race([exited, new Promise((r) => setTimeout(() => r(null), 8000))])
	if (outcome === null) child.kill('SIGKILL')
	assert.ok(outcome !== null && outcome.code !== 0, 'an idle window setTimeout cannot hold must stop the shim')
	assert.match(outcome.stderr, /SEMANTIC_SEARCH_IDLE_MINUTES/)
})

test('S1.8 an invalid SEMANTIC_SEARCH_IDLE_MINUTES fails loudly at startup', async () => {
	const { exited } = spawnRaw({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_MINUTES: 'soon' } })
	const { code, stderr } = await exited
	assert.notEqual(code, 0, 'invalid idle minutes must not start a server')
	assert.match(stderr, /SEMANTIC_SEARCH_IDLE_MINUTES/)
})

// ---------------------------------------------------------------------------
// S2 — idle lifecycle
//
// A fake backend under a fake plugin root gives the arms that the real engine cannot: a call
// that outlives the SDK's 60s default, an observable cancellation, and a child death on demand.
// ---------------------------------------------------------------------------
// Query prefixes steer one call: `quick` answers at once, `big` answers with a 1 MB text. FAKE_ECHO
// appends the backend's cwd and the searchDir it received. Like the stock server, the fake keeps
// running after its stdin closes until its in-flight calls are answered, then exits after
// FAKE_EXIT_DELAY_MS; `exit pid=` in FAKE_LOG marks the moment.
const FAKE_SERVER = String.raw`
import readline from 'node:readline'
import { appendFileSync, readFileSync } from 'node:fs'
const DELAY = Number(process.env.FAKE_DELAY_MS || 0)
const EXIT_DELAY = Number(process.env.FAKE_EXIT_DELAY_MS || 0)
const ECHO = !!process.env.FAKE_ECHO
const LOG = process.env.FAKE_LOG
const rl = readline.createInterface({ input: process.stdin })
const inFlight = new Map()
let stdinClosed = false
process.stdout.on('error', () => {})
const send = (o) => process.stdout.write(JSON.stringify(o) + '\n')
const log = (line) => { if (LOG) appendFileSync(LOG, line + '\n') }
const maybeExit = () => {
  if (stdinClosed && inFlight.size === 0) setTimeout(() => { log('exit pid=' + process.pid); process.exit(0) }, EXIT_DELAY)
}
rl.on('close', () => { stdinClosed = true; maybeExit() })
log('booted pid=' + process.pid)
rl.on('line', (line) => {
  let msg
  try { msg = JSON.parse(line) } catch { return }
  if (msg.method === 'initialize') {
    send({ jsonrpc: '2.0', id: msg.id, result: {
      protocolVersion: msg.params.protocolVersion,
      capabilities: { tools: {}, logging: {} },
      serverInfo: { name: 'semantic-search', version: '2.14.0' },
    } })
  } else if (msg.method === 'tools/list') {
    send({ jsonrpc: '2.0', id: msg.id, result: { tools: [{ name: 'search',
      description: 'fake backend for idle-unload proofs',
      inputSchema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] } }] } })
  } else if (msg.method === 'tools/call') {
    const id = msg.id
    const args = msg.params.arguments || {}
    const q = args.query
    log('args=' + JSON.stringify(args))
    log('received id=' + id + ' q=' + q)
    send({ jsonrpc: '2.0', method: 'notifications/message',
           params: { level: 'info', logger: 'semantic-search', data: 'searching' } })
    const quick = typeof q === 'string' && (q.startsWith('quick') || q.startsWith('big'))
    const timer = setTimeout(() => {
      inFlight.delete(id)
      log('answered id=' + id + ' q=' + q)
      let text = 'fake:' + q + ':pid=' + process.pid
      if (ECHO) text += ':cwd=' + process.cwd() + ':dir=' + args.searchDir
      if (typeof q === 'string' && q.startsWith('big')) text += ':' + 'x'.repeat(1024 * 1024)
      const result = process.env.FAKE_RESULT ? JSON.parse(readFileSync(process.env.FAKE_RESULT, 'utf8')) : { content: [{ type: 'text', text }] }
      send({ jsonrpc: '2.0', id, result })
      maybeExit()
    }, quick ? 0 : DELAY)
    inFlight.set(id, timer)
  } else if (msg.method === 'notifications/cancelled') {
    const id = msg.params && msg.params.requestId
    log('cancelled id=' + id + ' inflight=' + inFlight.has(id))
    if (inFlight.has(id)) {
      clearTimeout(inFlight.get(id))
      inFlight.delete(id)
      send({ jsonrpc: '2.0', id, error: { code: -32800, message: 'Request cancelled' } })
      maybeExit()
    }
  }
})
`

/** A plugin root whose `bin/server.js` is the fake above. */
function buildFakePlugin(dir) {
	rmSync(dir, { recursive: true, force: true })
	mkdirSync(path.join(dir, 'bin'), { recursive: true })
	writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ name: 'semantic-search', version: '2.14.0' }))
	writeFileSync(
		path.join(dir, '.mcp.json'),
		JSON.stringify({ mcpServers: { 'semantic-search': { command: 'node', args: ['${CLAUDE_PLUGIN_ROOT}/bin/server.js'] } } }, null, 2) + '\n',
	)
	writeFileSync(path.join(dir, 'bin', 'server.js'), FAKE_SERVER)
	cpSync(path.join(PLUGIN, 'dist'), path.join(dir, 'dist'), { recursive: true })
	// The shim resolves the MCP SDK from the plugin root, exactly as it does in production.
	execFileSync('cmd', ['/c', 'mklink', '/J', path.join(dir, 'node_modules'), path.join(PLUGIN, 'node_modules')], { stdio: 'ignore' })
	return dir
}

/** Connect a client straight to the stock backend, with no shim in between. */
async function startStock({ cwd, pluginRoot = PLUGIN } = {}) {
	const transport = new StdioClientTransport({
		command: process.execPath,
		args: [path.join(pluginRoot, 'bin', 'server.js')],
		cwd: cwd ?? REPO,
		env: { ...process.env },
	})
	const stderr = []
	transport.stderr?.on('data', (d) => stderr.push(String(d)))
	const client = new Client({ name: 'stock-proof', version: '0.0.1' }, { capabilities: {} })
	client.onerror = () => {}
	await client.connect(transport)
	return {
		client,
		pid: transport.pid,
		async close() { try { await client.close() } catch { /* already gone */ } },
	}
}

const IDLE_MS = 3000
const IDLE_ENV = { SEMANTIC_SEARCH_IDLE_MINUTES: String(IDLE_MS / 60000) }

test('S2.1 an idle window closes the backend child, the frontend survives, and the next call boots a new child', async () => {
	const fe = await startFrontend({ cwd: ownRoot('s2-1'), env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		const first = await callSearch(fe, { query: 'magma reaction pressure', searchDir: '.', limit: 3 })
		assert.notEqual(first.isError, true, `unexpected error: ${resultText(first)}`)
		const [pid] = fe.pids()
		assert.ok(pid, 'expected a backend child after the first search')
		OWNED_PIDS.add(pid)

		assert.ok(await waitForExit(pid, IDLE_MS + 20000), `backend child ${pid} still alive after the idle window`)

		const listed = await listTools(fe)
		assert.equal(listed.tools[0].name, 'search', 'the frontend outlives its backend')

		const second = await callSearch(fe, { query: 'magma reaction pressure', searchDir: '.', limit: 3 })
		assert.notEqual(second.isError, true, `unexpected error: ${resultText(second)}`)
		const pids = fe.pids()
		assert.equal(pids.length, 2, `expected two backend children, saw ${JSON.stringify(pids)}`)
		assert.notEqual(pids[0], pids[1], 'the second search must boot a fresh child')
		OWNED_PIDS.add(pids[1])
		assert.equal(resultText(second), resultText(first), 'the same query returns the same result after a cold boot')
	} finally {
		await fe.close()
	}
})

test('S2.2 startup, tools/list and pings neither boot a child nor keep one alive', async () => {
	const quiet = await startFrontend({ cwd: fx, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		await new Promise((r) => setTimeout(r, IDLE_MS + 2000))
		await listTools(quiet)
		await new Promise((r) => setTimeout(r, 500))
		assert.deepEqual(quiet.pids(), [], 'no search was ever called, so no child may exist')
	} finally {
		await quiet.close()
	}

	const fe = await startFrontend({ cwd: ownRoot('s2-2'), env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		await callSearch(fe, { query: 'bottles', searchDir: '.', limit: 3 })
		const [pid] = fe.pids()
		OWNED_PIDS.add(pid)
		// Keep asking for the tool list across the window: a list must not reset the timer.
		const deadline = Date.now() + IDLE_MS + 4000
		while (Date.now() < deadline) {
			await listTools(fe)
			await new Promise((r) => setTimeout(r, 400))
		}
		assert.ok(!pidAlive(pid), `backend child ${pid} survived the idle window despite tools/list traffic`)
	} finally {
		await fe.close()
	}
})

test('S2.3 warm, post-idle and unwrapped stock results are identical', async () => {
	const args = { query: 'idle unload returns native memory to the operating system', searchDir: '.', limit: 5 }
	const root = ownRoot('s2-3')
	const fe = await startFrontend({ cwd: root, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		const warm = await callSearch(fe, args)
		const [pid] = fe.pids()
		OWNED_PIDS.add(pid)
		assert.ok(await waitForExit(pid, IDLE_MS + 20000), 'idle close did not happen')
		const cold = await callSearch(fe, args)
		OWNED_PIDS.add(fe.pids()[1])

		const stock = await startStock({ cwd: root })
		try {
			const stockRes = await stock.client.callTool({ name: 'search', arguments: args })
			assert.equal(resultText(warm), resultText(stockRes), 'warm proxied result differs from stock')
			assert.equal(resultText(cold), resultText(stockRes), 'post-idle proxied result differs from stock')
			assert.equal(warm.isError, stockRes.isError)
			assert.deepEqual(Object.keys(warm).sort(), Object.keys(stockRes).sort())
		} finally {
			await stock.close()
		}
	} finally {
		await fe.close()
	}
})

test('S2.5 concurrent calls are both served and do not boot a second child', async () => {
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-concurrent'))
	const fe = await startFrontend({ cwd: fx, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', FAKE_DELAY_MS: '1500', ...IDLE_ENV } })
	try {
		const [a, b] = await Promise.all([
			callSearch(fe, { query: 'alpha', searchDir: '.' }),
			callSearch(fe, { query: 'beta', searchDir: '.' }),
		])
		assert.match(resultText(a), /^fake:alpha:pid=/)
		assert.match(resultText(b), /^fake:beta:pid=/)
		assert.equal(fe.pids().length, 1, 'two concurrent calls must share one child')
		OWNED_PIDS.add(fe.pids()[0])
	} finally {
		await fe.close()
	}
})

test('S2.6 two roots in one session get one backend each, and each idles out on its own', async () => {
	const other = path.join(SCRATCH, 'fixture-b')
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-roots'))
	const fe = await startFrontend({ cwd: other, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		const here = await callSearch(fe, { query: 'here', searchDir: '.' })
		const elsewhere = await callSearch(fe, { query: 'elsewhere', searchDir: fx })
		assert.match(resultText(here), /^fake:here:/)
		assert.match(resultText(elsewhere), /^fake:elsewhere:/)
		const pids = fe.pids()
		assert.equal(pids.length, 2, `expected one backend per root, saw ${JSON.stringify(pids)}`)
		for (const p of pids) OWNED_PIDS.add(p)
		for (const p of pids) assert.ok(await waitForExit(p, IDLE_MS + 20000), `backend ${p} did not idle out`)
		const again = await callSearch(fe, { query: 'here', searchDir: '.' })
		assert.match(resultText(again), /^fake:here:/)
		OWNED_PIDS.add(fe.pids().at(-1))
	} finally {
		await fe.close()
	}
})

test('S2.7 restrictToDir, limit and rebuild reach the backend unmodified; searchDir arrives absolute', async () => {
	const seen = path.join(SCRATCH, 'fake-args.log')
	rmSync(seen, { force: true })
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-args'))
	const probe = readFileSync(path.join(fakeRoot, 'bin', 'server.js'), 'utf8')
	writeFileSync(
		path.join(fakeRoot, 'bin', 'server.js'),
		probe.replace(
			'const q = args.query',
			"const q = JSON.stringify(msg.params.arguments) + '|meta=' + JSON.stringify(msg.params._meta || null)",
		),
	)
	const fe = await startFrontend({ cwd: fx, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		const res = await callSearch(fe, { query: 'passthrough', searchDir: '/tmp/a b', restrictToDir: 'src', limit: 7, rebuild: true })
		assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
		const text = resultText(res)
		assert.match(text, /"query":"passthrough"/)
		assert.ok(text.includes('"searchDir":' + JSON.stringify(path.resolve('/tmp/a b'))),
			`a searchDir that does not exist is forwarded in its resolved form: ${text}`)
		assert.match(text, /"restrictToDir":"src"/)
		assert.match(text, /"limit":7/)
		assert.match(text, /"rebuild":true/)
		OWNED_PIDS.add(fe.pids()[0])
	} finally {
		await fe.close()
	}
})

test('S2.8 an unexpected backend death reports one tool error, without retrying', async () => {
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-death'))
	const logPath = path.join(SCRATCH, 'fake-death.log')
	rmSync(logPath, { force: true })
	const fe = await startFrontend({ cwd: fx, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', FAKE_LOG: logPath, ...IDLE_ENV } })
	try {
		await callSearch(fe, { query: 'alive', searchDir: '.' })
		const [pid] = fe.pids()
		OWNED_PIDS.add(pid)
		process.kill(pid, 'SIGKILL')
		assert.ok(await waitForExit(pid, 10000), 'the planted death did not take')

		const after = await callSearch(fe, { query: 'after-death', searchDir: '.' }, 20000)
		assert.equal(after.isError, true, 'a dead backend must surface as a tool error')
		assert.match(resultText(after), /Semantic search backend unavailable/)
		const boots = readFileSync(logPath, 'utf8').split('\n').filter((l) => l.startsWith('booted')).length
		assert.equal(boots, 1, 'the shim must not silently respawn a backend after an unexpected death')

		const next = await callSearch(fe, { query: 'recovered', searchDir: '.' })
		assert.notEqual(next.isError, true, `the next call must boot a fresh child: ${resultText(next)}`)
		assert.match(resultText(next), /^fake:recovered:/)
		OWNED_PIDS.add(fe.pids().at(-1))
	} finally {
		await fe.close()
	}
})

test('S2.9 a cancelled call is forwarded as a cancellation and leaves the proxy usable', async () => {
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-cancel'))
	const logPath = path.join(SCRATCH, 'fake-cancel.log')
	rmSync(logPath, { force: true })
	const fe = await startFrontend({ cwd: fx, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', FAKE_DELAY_MS: '10000', FAKE_LOG: logPath, ...IDLE_ENV } })
	try {
		const controller = new AbortController()
		const pending = fe.client.callTool({ name: 'search', arguments: { query: 'cancel-me', searchDir: '.' } },
			undefined, { signal: controller.signal, timeout: 60000 })
		await new Promise((r) => setTimeout(r, 700))
		controller.abort()
		await assert.rejects(pending, (err) => err.name === 'AbortError' || /abort/i.test(String(err.message)))
		OWNED_PIDS.add(fe.pids()[0])

		const deadline = Date.now() + 8000
		while (Date.now() < deadline && !/cancelled id=\d+ inflight=true/.test(readFileSync(logPath, 'utf8'))) {
			await new Promise((r) => setTimeout(r, 200))
		}
		assert.match(readFileSync(logPath, 'utf8'), /cancelled id=\d+ inflight=true/, 'the cancellation never reached the backend')

		writeFileSync(logPath, '')
		const after = await callSearch(fe, { query: 'still-here', searchDir: '.' }, 40000)
		assert.notEqual(after.isError, true, `the proxy was left unusable: ${resultText(after)}`)
		assert.match(resultText(after), /^fake:still-here:/)
	} finally {
		await fe.close()
	}
})

test('S2.10 an invalid MCP_TOOL_TIMEOUT fails loudly at startup', async () => {
	const { exited } = spawnRaw({ cwd: fx, env: { MCP_TOOL_TIMEOUT: 'later' } })
	const { code, stderr } = await exited
	assert.notEqual(code, 0)
	assert.match(stderr, /MCP_TOOL_TIMEOUT/)
})

test('S2.11 a hard frontend death leaves no backend child behind', async () => {
	const fe = await startFrontend({ cwd: ownRoot('s2-11'), env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	const res = await callSearch(fe, { query: 'bottles', searchDir: '.', limit: 3 })
	assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
	const [childPid] = fe.pids()
	OWNED_PIDS.add(childPid)
	const frontendPid = fe.pid
	process.kill(frontendPid, 'SIGKILL')
	assert.ok(await waitForExit(frontendPid, 10000), 'the planted frontend death did not take')
	assert.ok(await waitForExit(childPid, 15000), `backend child ${childPid} outlived a hard frontend death`)
})

test('S2.12 unreviewed upstream bytes turn C# off, not search, and every result says so', async () => {
	const fakeRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-drift'))
	const scanner = path.join(fakeRoot, 'dist', 'scanner.js')
	writeFileSync(scanner, readFileSync(scanner, 'utf8') + '\n// unreviewed upstream change\n')
	const server = path.join(fakeRoot, 'bin', 'server.js')
	writeFileSync(server, readFileSync(server, 'utf8').replace(
		'const q = args.query',
		"const q = 'csharp=' + (process.env.SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT || 'none')",
	))
	const fe = await startFrontend({ cwd: fx, pluginRoot: fakeRoot, env: { SEMANTIC_SEARCH_IDLE_DEBUG: '1', ...IDLE_ENV } })
	try {
		const res = await callSearch(fe, { query: 'drift', searchDir: '.' })
		assert.notEqual(res.isError, true, `unexpected error: ${resultText(res)}`)
		assert.match(resultText(res), /csharp=none/, 'a drifted shim starts the stock backend, without the C# hook')
		assert.match(res.content.at(-1).text, /^C# indexing is off \(C# hook drift: scanner\.js/)
		OWNED_PIDS.add(fe.pids()[0])
	} finally {
		await fe.close()
	}
})

// ---------------------------------------------------------------------------
// S3 — one backend per index root, shared by every frontend that searches it
//
// The first frontend to search a root hosts that root's backend and listens on a named pipe; the
// others forward over it. Fake cases each get their own plugin root, so their pipes never meet.
// ---------------------------------------------------------------------------
const DBG = { SEMANTIC_SEARCH_IDLE_DEBUG: '1' }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const fakeLog = (p) => (existsSync(p) ? readFileSync(p, 'utf8') : '')
const count = (text, re) => (text.match(re) || []).length
const backendPids = (...fes) => fes.flatMap((f) => f.pids())
const own = (...fes) => { for (const p of backendPids(...fes)) OWNED_PIDS.add(p) }

async function waitFor(pred, timeoutMs, what) {
	const deadline = Date.now() + timeoutMs
	while (Date.now() < deadline) {
		if (pred()) return
		await sleep(100)
	}
	assert.fail(`timed out after ${timeoutMs}ms waiting for ${what}`)
}

/** A fake plugin, its log and an empty root of its own. */
function fakeCase(name, env = {}) {
	const pluginRoot = buildFakePlugin(path.join(SCRATCH, 'fake-plugin-' + name))
	const log = path.join(SCRATCH, 'fake-' + name + '.log')
	rmSync(log, { force: true })
	const root = path.join(SCRATCH, 'roots', name)
	rmSync(root, { recursive: true, force: true })
	mkdirSync(root, { recursive: true })
	git(['init', '-q'], root)
	const baseEnv = { ...DBG, FAKE_LOG: log, ...env }
	return {
		pluginRoot, log, root, baseEnv,
		start: (extra = {}, cwd = root) => startFrontend({ cwd, pluginRoot, env: { ...baseEnv, ...extra } }),
		booted: () => count(fakeLog(log), /^booted pid=/gm),
	}
}

/** The proxy under a hand-rolled MCP client, so the case controls stdin EOF without a kill. */
function startRawFrontend({ cwd, pluginRoot, env }) {
	const child = spawn(process.execPath, [PROXY, pluginRoot], { cwd, env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })
	let buf = ''
	let stderr = ''
	const waiters = new Map()
	child.stderr.on('data', (d) => (stderr += String(d)))
	child.stdout.on('data', (d) => {
		buf += String(d)
		let i
		while ((i = buf.indexOf('\n')) >= 0) {
			const line = buf.slice(0, i)
			buf = buf.slice(i + 1)
			let msg
			try { msg = JSON.parse(line) } catch { continue }
			if (msg.id != null && waiters.has(msg.id)) {
				waiters.get(msg.id)(msg)
				waiters.delete(msg.id)
			}
		}
	})
	let nextId = 1
	const request = (method, params) => new Promise((resolve) => {
		const id = nextId++
		waiters.set(id, resolve)
		child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
	})
	const exited = new Promise((resolve) => child.once('exit', resolve))
	return {
		pid: child.pid,
		exited,
		text: () => stderr,
		pids: () => [...stderr.matchAll(/backend-start pid=(\d+)/g)].map((m) => Number(m[1])),
		async init() {
			await request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'raw-proof', version: '0.0.1' } })
			child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n')
		},
		search: (args) => request('tools/call', { name: 'search', arguments: args }),
		endStdin: () => child.stdin.end(),
		kill: () => { try { child.kill('SIGKILL') } catch { /* gone */ } },
	}
}

function linkedFixture(name) {
	const main = buildFixture(path.join(SCRATCH, name + '-main'))
	const linked = path.join(SCRATCH, name + '-linked')
	git(['worktree', 'add', '--detach', linked, 'HEAD'], main)
	return { main: realpathSync.native(main), linked: realpathSync.native(linked) }
}

const { formatResults } = await import(pathToFileURL(path.join(PLUGIN, 'dist', 'format.js')).href)
function formattedResult(paths, preview = 'source preview') {
	return { content: [{ type: 'text', text: formatResults(paths.map((filePath) => ({
		chunk: { filePath, startLine: 2, endLine: 4, kind: 'function', name: 'example', content: preview },
		scores: { semantic: 0.8 },
	}))) + '\n\n[Index: 5 files, 7 chunks]' }] }
}

function resultHeaders(result) {
	return resultText(result).split('\n').filter((line) => /^\d+\. /.test(line))
}

test('S4.1 a cold linked worktree builds the primary index once and shares its real backend', { timeout: 240000 }, async () => {
	const { main, linked } = linkedFixture('s4-1')
	const a = await startFrontend({ cwd: linked, env: DBG })
	const b = await startFrontend({ cwd: main, env: DBG })
	try {
		assert.equal(existsSync(path.join(main, '.search-index')), false)
		const args = { query: 'magma reaction pressure', searchDir: '.', limit: 3 }
		const ra = await callSearch(a, args, 180000)
		const rb = await callSearch(b, args, 180000)
		own(a, b)
		assert.notEqual(ra.isError, true, resultText(ra))
		assert.notEqual(rb.isError, true, resultText(rb))
		assert.equal(backendPids(a, b).length, 1)
		assert.equal(existsSync(path.join(linked, '.search-index')), false)
		assert.equal(existsSync(path.join(main, '.search-index', 'search.db')), true)
		assert.ok(resultHeaders(ra).length > 0)
		assert.ok(resultHeaders(ra).every((line) => line.includes(linked + path.sep)), resultText(ra))
		assert.match(resultText(ra), /primary checkout.*rank.*preview/i)
		assert.ok(!resultText(rb).includes('primary checkout'), 'ordinary checkout keeps stock output')
		const guestLinked = await callSearch(b, { ...args, searchDir: linked })
		assert.ok(resultHeaders(guestLinked).every((line) => line.includes(linked + path.sep)), resultText(guestLinked))
		assert.equal(backendPids(a, b).length, 1)
	} finally {
		trackPids(a); trackPids(b)
		await b.close()
		await a.close()
	}
})

test('S4.2 rebasing covers relative and absolute headers, not previews or metadata', async () => {
	const { main, linked } = linkedFixture('s4-2')
	const c = fakeCase('s4-2')
	const output = path.join(SCRATCH, 's4-2-result.json')
	const preview = 'do not rewrite ' + main + ' or 1. alpha_module.py:L2 (text)'
	const original = formattedResult(['alpha_module.py', path.join(main, 'beta_notes.md'), main.replaceAll('\\', '/') + '/delta_scene.gd'], preview)
	writeFileSync(output, JSON.stringify(original))
	const fe = await c.start({ FAKE_RESULT: output }, linked)
	try {
		const res = await callSearch(fe, { query: 'quick-paths', searchDir: '.', restrictToDir: 'src', limit: 3, rebuild: true })
		assert.notEqual(res.isError, true, resultText(res))
		assert.deepEqual(resultHeaders(res), ['alpha_module.py', 'beta_notes.md', 'delta_scene.gd'].map((file, i) => `${i + 1}. ${path.join(linked, file)}:L2-4 (function example)`))
		assert.ok(resultText(res).includes('   ' + preview.slice(0, 117)), resultText(res))
		assert.match(resultText(res), /scores: semantic=0\.80/)
		assert.match(resultText(res), /\[Index: 5 files, 7 chunks\]/)
		const args = JSON.parse(fakeLog(c.log).split('\n').find((line) => line.startsWith('args=')).slice(5))
		assert.deepEqual(args, { query: 'quick-paths', searchDir: main, restrictToDir: 'src', limit: 3, rebuild: true })
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.3 each repository maps to its own primary, including a subdirectory and relative searchDir', async () => {
	const first = linkedFixture('s4-3-first')
	const second = linkedFixture('s4-3-second')
	for (const root of [second.main, second.linked]) mkdirSync(path.join(root, 'sub dir'))
	const c = fakeCase('s4-3')
	const output = path.join(SCRATCH, 's4-3-result.json')
	writeFileSync(output, JSON.stringify(formattedResult(['beta_notes.md'])))
	const fe = await c.start({ FAKE_RESULT: output }, first.linked)
	try {
		const a = await callSearch(fe, { query: 'quick-first', searchDir: '.' })
		const b = await callSearch(fe, { query: 'quick-second', searchDir: path.relative(first.linked, path.join(second.linked, 'sub dir')) })
		assert.ok(resultHeaders(a)[0].includes(path.join(first.linked, 'beta_notes.md')), resultText(a))
		assert.ok(resultHeaders(b)[0].includes(path.join(second.linked, 'sub dir', 'beta_notes.md')), resultText(b))
		const args = fakeLog(c.log).split('\n').filter((line) => line.startsWith('args=')).map((line) => JSON.parse(line.slice(5)))
		assert.deepEqual(args.map((a) => a.searchDir), [first.main, path.join(second.main, 'sub dir')])
		assert.equal(c.booted(), 2)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.4 ordinary non-Git roots keep the same directory and result bytes', async () => {
	const c = fakeCase('s4-4')
	rmSync(path.join(c.root, '.git'), { recursive: true, force: true })
	const output = path.join(SCRATCH, 's4-4-result.json')
	const original = formattedResult(['alpha_module.py'])
	writeFileSync(output, JSON.stringify(original))
	const fe = await c.start({ FAKE_RESULT: output, GIT_CEILING_DIRECTORIES: path.dirname(c.root) })
	try {
		const res = await callSearch(fe, { query: 'quick-ordinary', searchDir: '.' })
		assert.equal(resultText(res), resultText(original))
		const args = JSON.parse(fakeLog(c.log).split('\n').find((line) => line.startsWith('args=')).slice(5))
		assert.equal(args.searchDir, realpathSync.native(c.root))
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.5 a branch-only search directory cannot start a worktree index or a phantom primary directory', async () => {
	const { main, linked } = linkedFixture('s4-5')
	mkdirSync(path.join(linked, 'branch-only'))
	const c = fakeCase('s4-5')
	const fe = await c.start({}, linked)
	try {
		const res = await callSearch(fe, { query: 'quick-missing', searchDir: 'branch-only' })
		assert.equal(res.isError, true)
		assert.match(resultText(res), /primary checkout.*directory/i)
		assert.equal(c.booted(), 0)
		assert.equal(existsSync(path.join(main, 'branch-only')), false)
		assert.equal(existsSync(path.join(linked, 'branch-only', '.search-index')), false)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.6 output-format drift blocks rebasing before a backend starts', async () => {
	const { linked } = linkedFixture('s4-6')
	const c = fakeCase('s4-6')
	const format = path.join(c.pluginRoot, 'dist', 'format.js')
	writeFileSync(format, readFileSync(format, 'utf8') + '\n// format drift\n')
	const fe = await c.start({}, linked)
	try {
		const res = await callSearch(fe, { query: 'quick-drift', searchDir: '.' })
		assert.equal(res.isError, true)
		assert.match(resultText(res), /result format drift.*format\.js/i)
		assert.equal(c.booted(), 0)
		const ordinary = await callSearch(fe, { query: 'quick-ordinary-drift', searchDir: c.root })
		assert.notEqual(ordinary.isError, true, resultText(ordinary))
		assert.equal(c.booted(), 1)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.7 headers escaping the shared root fail without rewriting source text', async () => {
	const { linked } = linkedFixture('s4-7')
	const c = fakeCase('s4-7')
	const output = path.join(SCRATCH, 's4-7-result.json')
	writeFileSync(output, JSON.stringify(formattedResult(['../outside/file.py'])))
	const fe = await c.start({ FAKE_RESULT: output }, linked)
	try {
		const res = await callSearch(fe, { query: 'quick-escape', searchDir: '.' })
		assert.equal(res.isError, true)
		assert.match(resultText(res), /path outside.*shared index root/i)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S4.8 a linked checkout of a bare repository cannot index the Git object directory', async () => {
	const source = buildFixture(path.join(SCRATCH, 's4-8-source'))
	const bare = path.join(SCRATCH, 's4-8-bare')
	const linked = path.join(SCRATCH, 's4-8-linked')
	git(['clone', '--bare', '-q', source, bare], SCRATCH)
	git(['worktree', 'add', '--detach', linked, 'HEAD'], bare)
	const c = fakeCase('s4-8')
	const fe = await c.start({}, linked)
	try {
		const res = await callSearch(fe, { query: 'quick-bare', searchDir: '.' })
		assert.equal(res.isError, true)
		assert.match(resultText(res), /bare repository.*no primary checkout/i)
		assert.equal(c.booted(), 0)
		assert.equal(existsSync(path.join(bare, '.search-index')), false)
		assert.equal(existsSync(path.join(linked, '.search-index')), false)
	} finally {
		trackPids(fe)
		await fe.close()
	}
})

test('S3.1 two frontends on one root share one real backend', { timeout: 240000 }, async () => {
	const root = ownRoot('s3-1')
	const a = await startFrontend({ cwd: root, env: DBG })
	const b = await startFrontend({ cwd: root, env: DBG })
	try {
		const args = { query: 'idle unload returns native memory to the operating system', searchDir: '.', limit: 5 }
		const ra = await callSearch(a, args)
		const rb = await callSearch(b, args)
		own(a, b)
		assert.notEqual(ra.isError, true, resultText(ra))
		assert.notEqual(rb.isError, true, resultText(rb))
		assert.equal(resultText(rb), resultText(ra), 'a guest gets the host backend\'s result')
		assert.equal(backendPids(a, b).length, 1, `expected one backend for one root, saw ${JSON.stringify(backendPids(a, b))}`)
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.2 three concurrent frontends on one root boot one backend', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-2', { FAKE_DELAY_MS: '1500' })
	const fes = [await c.start(), await c.start(), await c.start()]
	try {
		const rs = await Promise.all(fes.map((f, i) => callSearch(f, { query: 'q' + i, searchDir: '.' })))
		own(...fes)
		rs.forEach((r, i) => assert.match(resultText(r), new RegExp('^fake:q' + i + ':pid=\\d+$')))
		assert.equal(c.booted(), 1, `expected one boot, log:\n${fakeLog(c.log)}`)
	} finally {
		for (const f of fes) await f.close()
	}
})

test('S3.3 two roots get two backends; each runs in its root and receives the canonical absolute searchDir', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-3', { FAKE_ECHO: '1' })
	const r1 = path.join(c.root, 'one')
	const r2 = path.join(c.root, 'two')
	mkdirSync(r1, { recursive: true })
	mkdirSync(r2, { recursive: true })
	const a = await c.start({}, r1)
	const b = await c.start({}, r2)
	try {
		const ra = await callSearch(a, { query: 'quick-one', searchDir: '.' })
		const rb = await callSearch(b, { query: 'quick-two', searchDir: '.' })
		own(a, b)
		const c1 = realpathSync.native(r1)
		const c2 = realpathSync.native(r2)
		assert.ok(resultText(ra).endsWith(':cwd=' + c1 + ':dir=' + c1), resultText(ra))
		assert.ok(resultText(rb).endsWith(':cwd=' + c2 + ':dir=' + c2), resultText(rb))
		assert.equal(c.booted(), 2, 'two roots never share a backend')
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.4 a hard-killed host fails the guest call it dispatched, without resending it', { timeout: 90000 }, async () => {
	const c = fakeCase('s3-4', { FAKE_DELAY_MS: '3000' })
	const a = await c.start()
	const b = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		const pending = callSearch(b, { query: 'slow-b', searchDir: '.' }, 60000)
		await waitFor(() => /received id=\d+ q=slow-b/.test(fakeLog(c.log)), 15000, 'the guest call to reach the backend')
		const [oldPid] = a.pids()
		own(a)
		process.kill(a.pid, 'SIGKILL')
		const res = await pending
		assert.equal(res.isError, true, `the guest call must fail with its host: ${resultText(res)}`)
		assert.match(resultText(res), /Semantic search host exited mid-call/)
		// libuv puts every non-detached child in a kill-on-close job object, so the backend dies with
		// its host instead of finishing the call as an orphan.
		assert.ok(await waitForExit(oldPid, 5000), `backend ${oldPid} outlived its hard-killed host`)
		assert.doesNotMatch(fakeLog(c.log), /answered id=\d+ q=slow-b/, 'the backend died with its host, mid-call')
		assert.equal(count(fakeLog(c.log), /^received id=\d+ q=slow-b$/gm), 1, 'a dispatched call is never resent')
		const next = await callSearch(b, { query: 'quick-next', searchDir: '.' }, 30000)
		own(b)
		assert.match(resultText(next), /^fake:quick-next:/)
		assert.equal(c.booted(), 2)
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.5 a host whose stdin closes finishes the dispatched guest call and keeps the root until its backend exits', { timeout: 90000 }, async () => {
	const c = fakeCase('s3-5', { FAKE_DELAY_MS: '1500', FAKE_EXIT_DELAY_MS: '1500' })
	const a = startRawFrontend({ cwd: c.root, pluginRoot: c.pluginRoot, env: c.baseEnv })
	const b = await c.start()
	const d = await c.start()
	try {
		await a.init()
		await a.search({ query: 'quick-a', searchDir: '.' })
		const [oldPid] = a.pids()
		OWNED_PIDS.add(oldPid)
		const pendingB = callSearch(b, { query: 'slow-b', searchDir: '.' }, 60000)
		await waitFor(() => /received id=\d+ q=slow-b/.test(fakeLog(c.log)), 15000, 'the guest call to reach the backend')
		a.endStdin()
		await waitFor(() => /host-release/.test(a.text()), 10000, 'the host to begin its release')
		const pendingD = callSearch(d, { query: 'quick-d', searchDir: '.' }, 60000)
		const rb = await pendingB
		assert.match(resultText(rb), /^fake:slow-b:/, `the dispatched call completes during release: ${resultText(rb)}`)
		// A connected pipe instance keeps the name alive on its own; drop B's so that only the
		// host's hold on its listener fences the root.
		await b.close()
		const rd = await pendingD
		own(d)
		assert.match(resultText(rd), /^fake:quick-d:/)
		assert.match(d.text(), /closing/, 'a call made during release is told the host is closing')
		const log = fakeLog(c.log)
		const exitAt = log.indexOf('exit pid=' + oldPid)
		assert.ok(exitAt >= 0 && exitAt < log.lastIndexOf('booted pid='), `the new backend must boot after the old one exits:\n${log}`)
		assert.equal(c.booted(), 2)
		await a.exited
	} finally {
		a.kill()
		await d.close()
		await b.close()
	}

	const c2 = fakeCase('s3-5b', { FAKE_DELAY_MS: '8000' })
	const a2 = await c2.start()
	const b2 = await c2.start()
	try {
		await callSearch(a2, { query: 'quick-a', searchDir: '.' })
		own(a2)
		const pending = callSearch(b2, { query: 'slow-b', searchDir: '.' }, 60000)
		await waitFor(() => /received id=\d+ q=slow-b/.test(fakeLog(c2.log)), 15000, 'the guest call to reach the backend')
		await a2.close()
		const res = await pending
		assert.equal(res.isError, true, 'an SDK close cuts off a call longer than its grace')
		assert.match(resultText(res), /Semantic search host exited mid-call/)
	} finally {
		await b2.close()
	}
})

test('S3.6 an idle host answers closing until its backend exits, then a guest re-elects', { timeout: 90000 }, async () => {
	// Under the SDK close's 2 s grace, so the fake logs its own exit instead of being killed.
	const c = fakeCase('s3-6', { ...IDLE_ENV, FAKE_EXIT_DELAY_MS: '1500' })
	const a = await c.start()
	const b = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		await callSearch(b, { query: 'quick-b', searchDir: '.' })
		const [oldPid] = a.pids()
		own(a)
		await waitFor(() => /host-release/.test(a.text()), IDLE_MS + 15000, 'the idle release')
		const r = await callSearch(b, { query: 'quick-again', searchDir: '.' }, 60000)
		own(b)
		assert.match(resultText(r), /^fake:quick-again:/)
		assert.match(b.text(), /closing/, 'the guest met the release window')
		const log = fakeLog(c.log)
		const exitAt = log.indexOf('exit pid=' + oldPid)
		assert.ok(exitAt >= 0 && exitAt < log.lastIndexOf('booted pid='), `the new backend must boot after the old one exits:\n${log}`)
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.7 cancelling one guest call cancels only that call, even when two guests use the same id', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-7', { FAKE_DELAY_MS: '4000' })
	const a = await c.start()
	const b = await c.start()
	const d = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		const controller = new AbortController()
		const pb = b.client.callTool({ name: 'search', arguments: { query: 'slow-b', searchDir: '.' } },
			undefined, { signal: controller.signal, timeout: 60000 })
		const pd = callSearch(d, { query: 'slow-d', searchDir: '.' }, 60000)
		await waitFor(() => /q=slow-b/.test(fakeLog(c.log)) && /q=slow-d/.test(fakeLog(c.log)), 15000, 'both guest calls to reach the backend')
		controller.abort()
		await assert.rejects(pb, (err) => err.name === 'AbortError' || /abort/i.test(String(err.message)))
		const rd = await pd
		own(a)
		assert.match(resultText(rd), /^fake:slow-d:/, `the other guest's call survives: ${resultText(rd)}`)
		await waitFor(() => /cancelled id=\d+ inflight=true/.test(fakeLog(c.log)), 10000, 'the cancellation to reach the backend')
		assert.equal(count(fakeLog(c.log), /cancelled id=\d+ inflight=true/g), 1)
		assert.doesNotMatch(fakeLog(c.log), /answered id=\d+ q=slow-b/)
		assert.equal(c.booted(), 1)
	} finally {
		await d.close()
		await b.close()
		await a.close()
	}
})

test('S3.8 a guest killed mid-call has its call cancelled at the backend; the host stays usable', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-8', { FAKE_DELAY_MS: '20000' })
	const a = await c.start()
	const b = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		own(a)
		callSearch(b, { query: 'slow-b', searchDir: '.' }, 60000).catch(() => {})
		await waitFor(() => /q=slow-b/.test(fakeLog(c.log)), 15000, 'the guest call to reach the backend')
		process.kill(b.pid, 'SIGKILL')
		await waitFor(() => /cancelled id=\d+ inflight=true/.test(fakeLog(c.log)), 10000, 'the dead guest\'s call to be cancelled')
		const r = await callSearch(a, { query: 'quick-after', searchDir: '.' })
		assert.match(resultText(r), /^fake:quick-after:/)
		assert.equal(c.booted(), 1)
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.9 a client presenting another key is refused and closed', { timeout: 30000 }, async () => {
	const c = fakeCase('s3-9')
	const a = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		own(a)
		const m = a.text().match(/host-listen pipe=(\S+)/)
		assert.ok(m, `the host never reported its pipe:\n${a.text()}`)
		const sock = net.connect(m[1])
		let got = ''
		sock.on('data', (d) => (got += String(d)))
		const closed = new Promise((resolve) => sock.once('close', resolve))
		sock.on('error', () => {})
		await new Promise((resolve) => sock.once('connect', resolve))
		sock.write(JSON.stringify({ t: 'hello', key: 'not-this-root', idleMinutes: 1 }) + '\n')
		sock.write(JSON.stringify({ t: 'call', id: 1, params: { name: 'search', arguments: { query: 'quick-intruder', searchDir: '.' } }, timeoutMs: 5000 }) + '\n')
		await Promise.race([closed, sleep(5000)])
		assert.match(got, /"t":"refused"/)
		assert.ok(sock.destroyed || sock.readableEnded, 'the host closes a refused client')
		assert.doesNotMatch(fakeLog(c.log), /quick-intruder/)
	} finally {
		await a.close()
	}
})

test('S3.10 a backend death under a guest is one tool error, with no respawn until the next call', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-10')
	const a = await c.start()
	const b = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		await callSearch(b, { query: 'quick-b', searchDir: '.' })
		const [pid] = a.pids()
		own(a)
		process.kill(pid, 'SIGKILL')
		assert.ok(await waitForExit(pid, 10000), 'the planted death did not take')
		const r = await callSearch(b, { query: 'quick-after-death', searchDir: '.' }, 20000)
		assert.equal(r.isError, true)
		assert.match(resultText(r), /Semantic search backend unavailable/)
		assert.equal(c.booted(), 1, 'no silent respawn')
		const next = await callSearch(b, { query: 'quick-recovered', searchDir: '.' })
		own(a, b)
		assert.match(resultText(next), /^fake:quick-recovered:/)
		assert.equal(c.booted(), 2)
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.11 frames decode intact when coalesced, split, or 1 MB long', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-11')
	const a = await c.start()
	const b = await c.start()
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		own(a)
		const big = await callSearch(b, { query: 'big', searchDir: '.' })
		assert.ok(resultText(big).length > 1024 * 1024 && resultText(big).endsWith('x'), 'a 1 MB result arrives whole')

		const m = a.text().match(/host-listen pipe=(\S+) key=(.+)$/m)
		assert.ok(m, `the host never reported its pipe and key:\n${a.text()}`)
		const sock = net.connect(m[1])
		let got = ''
		sock.on('data', (d) => (got += String(d)))
		sock.on('error', () => {})
		await new Promise((resolve) => sock.once('connect', resolve))
		const dir = realpathSync.native(c.root)
		const frame = (o) => JSON.stringify(o) + '\n'
		sock.write(frame({ t: 'hello', key: m[2], idleMinutes: 1 }) +
			frame({ t: 'call', id: 1, params: { name: 'search', arguments: { query: 'quick-raw1', searchDir: dir } }, timeoutMs: 10000 }))
		const second = frame({ t: 'call', id: 2, params: { name: 'search', arguments: { query: 'quick-raw2', searchDir: dir } }, timeoutMs: 10000 })
		for (const part of [second.slice(0, 7), second.slice(7, 40), second.slice(40)]) {
			sock.write(part)
			await sleep(50)
		}
		await waitFor(() => /quick-raw1/.test(got) && /quick-raw2/.test(got), 10000, 'both raw results')
		const results = got.split('\n').filter(Boolean).map((l) => JSON.parse(l)).filter((f) => f.t === 'result')
		assert.deepEqual(results.map((f) => f.id).sort(), [1, 2])
		sock.destroy()
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.12 a guest rebuild while another guest searches, one real backend, both succeed', { timeout: 240000 }, async () => {
	const root = ownRoot('s3-12')
	const a = await startFrontend({ cwd: root, env: DBG })
	const b = await startFrontend({ cwd: root, env: DBG })
	const d = await startFrontend({ cwd: root, env: DBG })
	try {
		const first = await callSearch(a, { query: 'bottles', searchDir: '.', limit: 3 })
		assert.notEqual(first.isError, true, resultText(first))
		const [rb, rd] = await Promise.all([
			callSearch(b, { query: 'magma reaction pressure', searchDir: '.', rebuild: true, limit: 3 }),
			callSearch(d, { query: 'bottled reagents on the shelf', searchDir: '.', limit: 3 }),
		])
		own(a, b, d)
		assert.notEqual(rb.isError, true, resultText(rb))
		assert.notEqual(rd.isError, true, resultText(rd))
		assert.match(resultText(rb), /alpha_module\.py/)
		assert.match(resultText(rd), /epsilon_bottles\.txt/)
		assert.equal(backendPids(a, b, d).length, 1, `expected one backend, saw ${JSON.stringify(backendPids(a, b, d))}`)
	} finally {
		await d.close()
		await b.close()
		await a.close()
	}
})

test('S3.13 a junction and its target are one root: one backend, one searchDir spelling', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-13', { FAKE_ECHO: '1' })
	const link = path.join(SCRATCH, 'roots', 's3-13-link')
	try { rmdirSync(link) } catch { /* absent */ }
	execFileSync('cmd', ['/c', 'mklink', '/J', link, c.root], { stdio: 'ignore' })
	const a = await c.start({}, c.root)
	const b = await c.start({}, link)
	try {
		const ra = await callSearch(a, { query: 'quick-a', searchDir: '.' })
		const rb = await callSearch(b, { query: 'quick-b', searchDir: '.' })
		own(a, b)
		const canonical = realpathSync.native(c.root)
		assert.ok(resultText(ra).endsWith(':dir=' + canonical), resultText(ra))
		assert.ok(resultText(rb).endsWith(':dir=' + canonical), resultText(rb))
		assert.equal(c.booted(), 1, 'a junction must not start a second backend on the same index')
	} finally {
		await b.close()
		await a.close()
		try { rmdirSync(link) } catch { /* gone */ }
	}
})

test('S3.14 a guest\'s longer idle window keeps the shared backend alive', { timeout: 120000 }, async () => {
	const GUEST_IDLE = { SEMANTIC_SEARCH_IDLE_MINUTES: '0.25' }
	const c = fakeCase('s3-14', IDLE_ENV)
	const a = await c.start()
	const b = await c.start(GUEST_IDLE)
	try {
		// A guest arriving after the backend started.
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		await callSearch(b, { query: 'quick-b', searchDir: '.' })
		const [pid] = a.pids()
		own(a)
		await sleep(IDLE_MS + 4000)
		assert.ok(pidAlive(pid), 'the guest\'s 15 s window must outlast the host\'s 3 s window')

		// A guest whose own call boots the backend, after a planted death.
		process.kill(pid, 'SIGKILL')
		assert.ok(await waitForExit(pid, 10000))
		await callSearch(b, { query: 'quick-dead', searchDir: '.' }, 20000)
		await callSearch(b, { query: 'quick-reboot', searchDir: '.' }, 20000)
		const reborn = a.pids().at(-1)
		own(a)
		assert.notEqual(reborn, pid, 'the guest\'s call booted a fresh backend')
		await sleep(IDLE_MS + 4000)
		assert.ok(pidAlive(reborn), 'the guest\'s window also governs a backend its own call booted')
	} finally {
		await b.close()
		await a.close()
	}
})

test('S3.15 a closing reply to a second call leaves the guest\'s earlier dispatched call to finish', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-15', { FAKE_DELAY_MS: '1500', FAKE_EXIT_DELAY_MS: '500' })
	const a = startRawFrontend({ cwd: c.root, pluginRoot: c.pluginRoot, env: c.baseEnv })
	const b = await c.start()
	try {
		await a.init()
		await a.search({ query: 'quick-a', searchDir: '.' })
		OWNED_PIDS.add(a.pids()[0])
		const first = callSearch(b, { query: 'slow-first', searchDir: '.' }, 60000)
		await waitFor(() => /received id=\d+ q=slow-first/.test(fakeLog(c.log)), 15000, 'the first guest call to reach the backend')
		a.endStdin()
		await waitFor(() => /host-release/.test(a.text()), 10000, 'the host to begin its release')
		const second = callSearch(b, { query: 'quick-second', searchDir: '.' }, 60000)
		const r1 = await first
		assert.match(resultText(r1), /^fake:slow-first:/, `the earlier call must finish during release: ${resultText(r1)}`)
		const r2 = await second
		own(b)
		assert.match(resultText(r2), /^fake:quick-second:/)
		await a.exited
	} finally {
		a.kill()
		await b.close()
	}
})

test('S3.16 a client that connects and never says hello cannot stall a release', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-16', IDLE_ENV)
	const a = await c.start()
	let sock = null
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		own(a)
		const m = a.text().match(/host-listen pipe=(\S+)/)
		assert.ok(m, 'the host never reported its pipe')
		sock = net.connect(m[1])
		sock.on('error', () => {})
		await new Promise((resolve) => sock.once('connect', resolve))
		// Well inside the host's 10 s hello deadline: only the release itself can free this socket.
		await waitFor(() => /host-released/.test(a.text()), IDLE_MS + 5000, 'the release to finish with a silent client connected')
	} finally {
		sock?.destroy()
		await a.close()
	}
})

test('S3.17 a departing guest\'s longer idle window stops counting', { timeout: 60000 }, async () => {
	const c = fakeCase('s3-17', IDLE_ENV)
	const a = await c.start()
	const b = await c.start({ SEMANTIC_SEARCH_IDLE_MINUTES: '0.25' })
	try {
		await callSearch(a, { query: 'quick-a', searchDir: '.' })
		await callSearch(b, { query: 'quick-b', searchDir: '.' })
		const [pid] = a.pids()
		own(a)
		await b.close()
		assert.ok(await waitForExit(pid, IDLE_MS + 5000), 'after the 15 s guest leaves, the host\'s 3 s window must govern')
	} finally {
		await b.close()
		await a.close()
	}
})

// Last on purpose: its 65 s call starts in `before` and runs while every other case does, so awaiting it
// here costs only what the rest of the suite has not already covered.
test('S2.4 a call that runs past 60 seconds is not aborted, and its backend is not closed in flight', async () => {
	const { res, elapsed, alive, logPath } = await slowCall
	assert.notEqual(res.isError, true, `the shim aborted a >60s call: ${resultText(res)}`)
	assert.match(resultText(res), /^fake:slow:pid=\d+$/)
	assert.ok(elapsed >= 60000, `the fake backend returned early after ${elapsed}ms`)
	assert.ok(alive, 'the child was closed while a request was in flight')
	assert.match(readFileSync(logPath, 'utf8'), /answered/)
})
