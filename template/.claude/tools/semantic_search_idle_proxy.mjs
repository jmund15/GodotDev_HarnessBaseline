#!/usr/bin/env node
/**
 * Out-of-process frontend shim for the DreB `semantic-search` MCP plugin.
 *
 * Registered in place of `<plugin-root>/bin/server.js` under the plugin's own
 * `mcpServers.semantic-search` entry. It answers the MCP handshake and `tools/list`
 * itself from a pinned snapshot of the upstream tool contract, and starts the stock
 * backend as a stdio child on the first `tools/call`.
 *
 * One backend per index root, shared by every frontend on the machine. The first frontend to
 * search a root hosts that root's backend and listens on a named pipe derived from the root;
 * every other frontend forwards its calls for that root over the pipe. Linked worktrees use
 * their primary checkout's index root, with result paths rebased by the calling frontend.
 * Reference: `.claude/reference/semantic_search_idle.md`.
 *
 * The stock backend cache stays untouched: a child-only import hook adds C# AST support.
 * The shim controls when that child exists and who talks to it.
 */

import path from 'node:path'
import net from 'node:net'
import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { readFileSync, realpathSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { buildCsharpPatch } from './semantic_search_csharp_hook.mjs'

const CSHARP_HOOK = new URL('./semantic_search_csharp_hook.mjs', import.meta.url).href

// ---------------------------------------------------------------------------
// Arguments and configuration
// ---------------------------------------------------------------------------
const argv = process.argv.slice(2)
const pluginRootArg = argv.find((a) => !a.startsWith('--'))
if (!pluginRootArg) {
	process.stderr.write('semantic-search-idle-proxy: usage: node proxy.mjs [--print-contract] <plugin-root>\n')
	process.exit(2)
}
const PLUGIN_ROOT = path.resolve(pluginRootArg)
// Unreviewed upstream bytes turn C# off for this frontend, not search: every result then says so.
// The revision joins the share key, so a stock backend never serves a C# frontend or the reverse.
let CSHARP_REVISION = null
let CSHARP_OFF = null
try {
	CSHARP_REVISION = buildCsharpPatch(PLUGIN_ROOT).revision
} catch (err) {
	CSHARP_OFF = `C# indexing is off (${err instanceof Error ? err.message : String(err)}); .cs files are not searched. Run python3 .claude/tools/semantic_search_idle_install.py --check.`
	process.stderr.write(`semantic-search-idle-proxy: ${CSHARP_OFF}\n`)
}

const DEFAULT_IDLE_MINUTES = 10

// The SDK applies DEFAULT_REQUEST_TIMEOUT_MSEC (60s) to any request that does not pass a timeout,
// so "no per-request timeout" is not expressible as `undefined`. This is the largest value
// setTimeout accepts without overflow; the host's own tool timeout is then the only real bound.
const NO_REQUEST_TIMEOUT_MS = 2147483647
// setTimeout overflows past NO_REQUEST_TIMEOUT_MS and fires at once, so the idle window stops there.
const MAX_IDLE_MINUTES = Math.floor(NO_REQUEST_TIMEOUT_MS / 60000)

// Bump when the pipe wire format changes: shims of different protocols never share a pipe.
const SHARE_PROTOCOL = 2
// A POSIX socket leaves a stale file after a crash and nothing here proves its recovery, so
// sharing runs on win32 only; elsewhere each frontend owns a private backend per root.
const SHARED = process.platform === 'win32'
const CLOSING_WAIT_MS = 250
const ELECTION_RETRY_MS = 50
const HELLO_TIMEOUT_MS = 10000

function fail(message) {
	process.stderr.write(`semantic-search-idle-proxy: ${message}\n`)
	process.exit(2)
}

function readIdleMinutes() {
	const raw = process.env.SEMANTIC_SEARCH_IDLE_MINUTES
	if (raw === undefined || raw.trim() === '') return DEFAULT_IDLE_MINUTES
	const value = Number(raw)
	if (!Number.isFinite(value) || value <= 0 || value > MAX_IDLE_MINUTES) {
		fail(`SEMANTIC_SEARCH_IDLE_MINUTES must be a positive number of minutes up to ${MAX_IDLE_MINUTES}, got ${JSON.stringify(raw)}`)
	}
	return value
}

function readRequestTimeoutMs() {
	const raw = process.env.MCP_TOOL_TIMEOUT
	if (raw === undefined || raw.trim() === '') return NO_REQUEST_TIMEOUT_MS
	const value = Number(raw)
	if (!Number.isFinite(value) || value <= 0) {
		fail(`MCP_TOOL_TIMEOUT must be a positive finite number of milliseconds, got ${JSON.stringify(raw)}`)
	}
	return Math.floor(value)
}

const IDLE_MINUTES = readIdleMinutes()
const REQUEST_TIMEOUT_MS = readRequestTimeoutMs()

// ---------------------------------------------------------------------------
// Pinned upstream tool contract (DreB semantic-search 2.14.0, src/mcp-server.ts SEARCH_TOOL)
// ---------------------------------------------------------------------------
const SEARCH_TOOL = {
	name: 'search',
	description:
		'Search the codebase using natural language queries. Returns ranked code/doc results using semantic similarity and keyword matching. First query builds the index (may take a moment); subsequent queries are fast.',
	inputSchema: {
		type: 'object',
		properties: {
			query: { type: 'string', description: 'Search query (natural language, identifier, or path)' },
			searchDir: {
				type: 'string',
				description: 'Directory to index and search. This controls which directory gets indexed.',
			},
			restrictToDir: {
				type: 'string',
				description: 'Restrict results to files under this path (filters the already-built index).',
			},
			limit: { type: 'number', description: 'Maximum results to return (default: 20)' },
			rebuild: { type: 'boolean', description: 'Force index rebuild (default: false)' },
		},
		required: ['query', 'searchDir'],
	},
}

function readPluginVersion() {
	return JSON.parse(readFileSync(path.join(PLUGIN_ROOT, 'package.json'), 'utf8')).version
}

const SERVER_VERSION = readPluginVersion()
const RESULT_MODULE_HASHES = {
	'format.js': '03e3f8771e24baafe010894f2338c23e02de0d7bdc404c59068afa7356ee80a6',
	'mcp-server.js': 'c4f7f52d6220fc50c99df4664eec7e642d4199620f403eb3c731b18aa4564896',
}
const RESULT_FORMAT_DRIFT = resultFormatDrift()

function resultFormatDrift() {
	for (const [name, expected] of Object.entries(RESULT_MODULE_HASHES)) {
		try {
			const actual = createHash('sha256').update(readFileSync(path.join(PLUGIN_ROOT, 'dist', name))).digest('hex')
			if (actual === expected) continue
		} catch {}
		return `Semantic search result format drift: ${name}; review the upstream result format before reinstalling the proxy.`
	}
	return null
}

if (argv.includes('--print-contract')) {
	if (RESULT_FORMAT_DRIFT) fail(RESULT_FORMAT_DRIFT)
	process.stdout.write(
		JSON.stringify({
			serverInfo: { name: 'semantic-search', version: SERVER_VERSION },
			capabilities: { tools: {}, logging: {} },
			tool: SEARCH_TOOL,
		}) + '\n',
	)
	process.exit(0)
}

// ---------------------------------------------------------------------------
// Debug channel (stderr only; stdout is the MCP transport)
// ---------------------------------------------------------------------------
function debug(line) {
	if (process.env.SEMANTIC_SEARCH_IDLE_DEBUG) process.stderr.write(`[idle-proxy] ${line}\n`)
}

// ---------------------------------------------------------------------------
// MCP frontend
// ---------------------------------------------------------------------------
const requireFromPlugin = createRequire(path.join(PLUGIN_ROOT, 'package.json'))
const importFromPlugin = (spec) => import(pathToFileURL(requireFromPlugin.resolve(spec)).href)

const [ { Server }, { StdioServerTransport }, { Client }, { StdioClientTransport }, types ] = await Promise.all([
	importFromPlugin('@modelcontextprotocol/sdk/server/index.js'),
	importFromPlugin('@modelcontextprotocol/sdk/server/stdio.js'),
	importFromPlugin('@modelcontextprotocol/sdk/client/index.js'),
	importFromPlugin('@modelcontextprotocol/sdk/client/stdio.js'),
	importFromPlugin('@modelcontextprotocol/sdk/types.js'),
])

const server = new Server(
	{ name: 'semantic-search', version: SERVER_VERSION },
	{ capabilities: { tools: {}, logging: {} } },
)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const errorResult = (text) => ({ content: [{ type: 'text', text }], isError: true })
const messageOf = (err) => (err instanceof Error ? err.message : String(err))

/** Is `pid` still in the process table? */
function pidAlive(pid) {
	try {
		process.kill(pid, 0)
		return true
	} catch {
		return false
	}
}

async function waitForProcessExit(pid, timeoutMs) {
	const deadline = Date.now() + timeoutMs
	while (Date.now() < deadline) {
		if (!pidAlive(pid)) return true
		await sleep(50)
	}
	return !pidAlive(pid)
}

// ---------------------------------------------------------------------------
// Backend adapter — one stock server child, owned by the root's host alone
// ---------------------------------------------------------------------------
class StdioBackend {
	/** @param {{ pluginRoot: string, cwd: string, forwardLog: (params: object) => void }} opts */
	constructor(opts) {
		this.opts = opts
		this.client = null
		this.pid = null
		this.closed = false
	}

	async open() {
		const transport = new StdioClientTransport({
			command: process.execPath,
			args: [...(CSHARP_REVISION ? ['--import', CSHARP_HOOK] : []), path.join(this.opts.pluginRoot, 'bin', 'server.js')],
			cwd: this.opts.cwd,
			// The SDK's default environment is filtered; the stock server needs the inherited one.
			env: CSHARP_REVISION ? { ...process.env, SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT: this.opts.pluginRoot } : { ...process.env },
		})
		const client = new Client(
			{ name: 'semantic-search-idle-proxy', version: SERVER_VERSION },
			{ capabilities: {} },
		)
		// Only the transport closing means the child is gone; an error alone may leave it running.
		client.onclose = () => {
			this.closed = true
		}
		client.onerror = () => {}
		client.setNotificationHandler(types.LoggingMessageNotificationSchema, (n) => {
			this.opts.forwardLog(n.params)
		})
		await client.connect(transport)
		this.client = client
		this.pid = transport.pid ?? null
		debug(`backend-start pid=${this.pid} cwd=${this.opts.cwd}`)
	}

	callSearch(params, signal, timeoutMs) {
		return this.client.callTool(params, types.CallToolResultSchema, { signal, timeout: timeoutMs })
	}

	/**
	 * The SDK close ends stdin, then signals the child. This also waits for the pid to leave and
	 * kills it outright if it has not, so no caller frees a root while its child still holds the index.
	 */
	async close() {
		const client = this.client
		const pid = this.pid
		this.client = null
		this.closed = true
		if (client) {
			try {
				await client.close()
			} catch {
				/* the child is already gone */
			}
		}
		if (pid) {
			let gone = await waitForProcessExit(pid, 5000)
			if (!gone) {
				try {
					process.kill(pid, 'SIGKILL')
				} catch {
					/* it left between the check and the kill */
				}
				gone = await waitForProcessExit(pid, 5000)
			}
			debug(`backend-exit pid=${pid} exited=${gone}`)
		}
	}
}

// ---------------------------------------------------------------------------
// Pipe wire: newline-delimited JSON. JSON.stringify never emits a raw newline, and the reader
// decodes incrementally, so split and coalesced chunks both yield whole frames.
// ---------------------------------------------------------------------------
function readFrames(socket, onFrame) {
	let buf = ''
	socket.setEncoding('utf8')
	socket.on('data', (chunk) => {
		buf += chunk
		let i
		while ((i = buf.indexOf('\n')) >= 0) {
			const line = buf.slice(0, i)
			buf = buf.slice(i + 1)
			if (!line.trim()) continue
			let frame
			try {
				frame = JSON.parse(line)
			} catch {
				continue
			}
			onFrame(frame)
		}
	})
}

function sendFrame(socket, frame) {
	if (!socket.destroyed && socket.writable) socket.write(JSON.stringify(frame) + '\n')
}

function validIdleMinutes(value) {
	return typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= MAX_IDLE_MINUTES
		? value
		: IDLE_MINUTES
}

function validTimeoutMs(value) {
	return typeof value === 'number' && Number.isFinite(value) && value > 0
		? Math.min(Math.floor(value), NO_REQUEST_TIMEOUT_MS)
		: NO_REQUEST_TIMEOUT_MS
}

// ---------------------------------------------------------------------------
// Roots: canonical caller directories map to the same relative directory in their Git primary
// checkout. Only that index root participates in backend election; each caller rebases its own reply.
// ---------------------------------------------------------------------------
const PLUGIN_CANONICAL = canonical(PLUGIN_ROOT)
const execGit = promisify(execFile)

function canonical(p) {
	try {
		return realpathSync.native(p)
	} catch {
		return p
	}
}

function canonicalRoot(searchDir) {
	const raw = typeof searchDir === 'string' && searchDir !== '' ? searchDir : '.'
	return canonical(path.resolve(process.cwd(), raw))
}

function within(root, target) {
	const relative = path.relative(root, target)
	return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative)
}

async function searchRoot(searchDir) {
	const callerDir = canonicalRoot(searchDir)
	const unchanged = { callerDir, indexDir: callerDir }
	if (!isDirectory(callerDir)) return unchanged
	const env = { ...process.env }
	for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE']) delete env[key]
	const git = async (args) => (await execGit('git', ['-C', callerDir, ...args], { env, encoding: 'utf8', windowsHide: true })).stdout
	let metadata
	try {
		metadata = await git(['rev-parse', '--path-format=absolute', '--show-toplevel', '--git-dir', '--git-common-dir'])
	} catch (err) {
		if (err.code === 'ENOENT' || /not a git repository/i.test(err.stderr ?? '')) return unchanged
		throw new Error(`Cannot resolve semantic search Git root: ${messageOf(err)}`)
	}
	const fields = metadata.trimEnd().split(/\r?\n/)
	if (fields.length !== 3) throw new Error('Cannot resolve semantic search Git root: unexpected Git metadata')
	const [top, gitDir, commonDir] = fields.map(canonical)
	if (gitDir === commonDir) return unchanged
	const worktrees = (await git(['worktree', 'list', '--porcelain', '-z'])).split('\0')
	if (worktrees[1] === 'bare') throw new Error('A bare repository has no primary checkout to share; search a standalone checkout instead.')
	const first = worktrees[0]
	if (!first.startsWith('worktree ')) throw new Error('Cannot resolve semantic search primary checkout')
	const primary = canonical(first.slice('worktree '.length))
	const indexDir = canonical(path.resolve(primary, path.relative(top, callerDir)))
	if (!within(primary, indexDir) || !isDirectory(indexDir)) {
		throw new Error('The primary checkout has no matching search directory; search an existing parent and read branch-only files locally.')
	}
	if (RESULT_FORMAT_DRIFT) throw new Error(RESULT_FORMAT_DRIFT)
	return { callerDir, indexDir }
}

function callerResult(result, { callerDir, indexDir }) {
	if (callerDir === indexDir || result.isError) return result
	const content = (result.content ?? []).map((block) => {
		if (block.type !== 'text') return block
		const text = block.text.replace(/^(\d+\. )(.+)(:L\d+(?:-\d+)? \([^\r\n]*\))$/gm, (_, prefix, file, suffix) => {
			const absolute = path.resolve(indexDir, file)
			if (!within(indexDir, absolute)) throw new Error('Result path outside the shared index root; cannot rebase this result safely.')
			return prefix + path.resolve(callerDir, path.relative(indexDir, absolute)) + suffix
		})
		return { ...block, text }
	})
	return { ...result, content: [...content, { type: 'text', text: 'Shared primary checkout index: ranks, previews and line ranges use the primary copy. Read the worktree file before relying on them; branch-only files are not indexed.' }] }
}

function isDirectory(p) {
	try {
		return statSync(p).isDirectory()
	} catch {
		return false
	}
}

class HostLost extends Error {}

/** One root as seen from this frontend: its host role, or its connection to the host. */
class Root {
	constructor(dir) {
		this.dir = dir
		this.key = JSON.stringify([SHARE_PROTOCOL, dir, PLUGIN_CANONICAL, SERVER_VERSION, CSHARP_REVISION])
		this.pipe = '\\\\.\\pipe\\semantic-search-' + createHash('sha256').update(this.key).digest('hex').slice(0, 24)
		this.hosting = false
		this.conn = null
		this.electing = null
		this.electWaiters = 0
		// Host state.
		this.server = null
		this.sockets = new Set()
		this.guests = new Set()
		this.backend = null
		this.retiring = null
		this.spawnPromise = null
		this.inFlight = 0
		this.idleTimer = null
		this.releasing = null
		this.drained = null
		this.logSinks = new Map()
	}

	// -- routing -------------------------------------------------------------
	async call(params, signal, timeoutMs, sink) {
		for (;;) {
			if (signal.aborted) throw signal.reason ?? new Error('aborted')
			const route = await this.route(signal)
			if (route === 'host') return this.hostCall(params, signal, timeoutMs, sink)
			const out = await route.call(params, signal, timeoutMs, sink)
			if (out.unsent) continue
			if (out.closing) {
				debug(`election-wait reason=closing pipe=${this.pipe}`)
				await sleep(CLOSING_WAIT_MS)
				continue
			}
			return out.result
		}
	}

	async route(signal) {
		if (this.releasing) await this.releasing
		if (this.hosting) return 'host'
		if (this.conn && this.conn.alive) return this.conn
		if (!SHARED) {
			this.hosting = true
			return 'host'
		}
		if (!this.electing) {
			this.electing = this.elect().finally(() => {
				this.electing = null
			})
		}
		this.electWaiters += 1
		try {
			return await raceAbort(this.electing, signal)
		} finally {
			this.electWaiters -= 1
		}
	}

	async elect() {
		for (;;) {
			const outcome = await this.tryConnect()
			if (outcome instanceof GuestConn) {
				this.conn = outcome
				return outcome
			}
			if (outcome === 'absent' && (await this.tryListen())) {
				this.hosting = true
				return 'host'
			}
			// Every caller that wanted this election has aborted: stop instead of retrying forever.
			if (this.electWaiters === 0) throw new Error('election abandoned: no caller is waiting')
			if (outcome === 'closing') debug(`election-wait reason=closing pipe=${this.pipe}`)
			await sleep(outcome === 'closing' ? CLOSING_WAIT_MS : ELECTION_RETRY_MS)
		}
	}

	/** GuestConn, or 'absent' (no host), 'closing' (host releasing) or 'retry'. */
	tryConnect() {
		return new Promise((resolve, reject) => {
			const socket = net.connect(this.pipe)
			let settled = false
			const settle = (value, isError) => {
				if (settled) return
				settled = true
				clearTimeout(timer)
				if (!(value instanceof GuestConn)) socket.destroy()
				if (isError) reject(value)
				else resolve(value)
			}
			const timer = setTimeout(() => settle('retry'), HELLO_TIMEOUT_MS)
			socket.on('error', (err) => settle(err.code === 'ENOENT' ? 'absent' : 'retry'))
			socket.on('close', () => settle('retry'))
			socket.on('connect', () => {
				sendFrame(socket, { t: 'hello', key: this.key, idleMinutes: IDLE_MINUTES })
			})
			readFrames(socket, (frame) => {
				if (settled) return
				if (frame.t === 'welcome') {
					debug(`guest-connect pipe=${this.pipe}`)
					settle(new GuestConn(this, socket))
				} else if (frame.t === 'closing') {
					settle('closing')
				} else if (frame.t === 'refused') {
					settle(new Error(`semantic-search host refused this client: ${frame.reason}`), true)
				}
			})
		})
	}

	async tryListen() {
		const srv = net.createServer((socket) => this.acceptGuest(socket))
		const err = await new Promise((resolve) => {
			srv.once('error', resolve)
			srv.listen(this.pipe, () => resolve(null))
		})
		if (err) {
			if (err.code === 'EADDRINUSE') return false
			throw err
		}
		srv.on('error', () => {})
		this.server = srv
		debug(`host-listen pipe=${this.pipe} key=${this.key}`)
		return true
	}

	// -- host: guests ---------------------------------------------------------
	acceptGuest(socket) {
		socket.on('error', () => {})
		this.sockets.add(socket)
		const guest = { socket, idleMinutes: IDLE_MINUTES, calls: new Map(), welcomed: false }
		const helloTimer = setTimeout(() => {
			if (!guest.welcomed) socket.destroy()
		}, HELLO_TIMEOUT_MS)
		const sink = (params) => sendFrame(socket, { t: 'log', params })
		readFrames(socket, (frame) => {
			if (!guest.welcomed) {
				if (frame.t !== 'hello') return socket.destroy()
				if (frame.key !== this.key) {
					sendFrame(socket, { t: 'refused', reason: 'key mismatch' })
					return socket.end()
				}
				if (this.releasing) {
					sendFrame(socket, { t: 'closing' })
					return socket.end()
				}
				guest.welcomed = true
				clearTimeout(helloTimer)
				guest.idleMinutes = validIdleMinutes(frame.idleMinutes)
				this.guests.add(guest)
				return sendFrame(socket, { t: 'welcome' })
			}
			if (frame.t === 'call') {
				if (this.releasing) return sendFrame(socket, { t: 'closing', id: frame.id })
				if (guest.calls.has(frame.id)) {
					return sendFrame(socket, { t: 'result', id: frame.id, result: errorResult(`duplicate call id ${frame.id}`) })
				}
				const controller = new AbortController()
				guest.calls.set(frame.id, controller)
				debug(`guest-call id=${frame.id} pipe=${this.pipe}`)
				this.hostCall(frame.params, controller.signal, validTimeoutMs(frame.timeoutMs), sink)
					.then((result) => sendFrame(socket, { t: 'result', id: frame.id, result }))
					.finally(() => guest.calls.delete(frame.id))
			} else if (frame.t === 'cancel') {
				guest.calls.get(frame.id)?.abort()
			}
		})
		socket.on('close', () => {
			clearTimeout(helloTimer)
			this.sockets.delete(socket)
			const hadWindow = this.guests.delete(guest)
			for (const controller of guest.calls.values()) controller.abort()
			// The departed guest's window no longer counts.
			if (hadWindow && this.inFlight === 0 && this.backend && !this.releasing) this.armIdleTimer()
		})
	}

	// -- host: the backend ---------------------------------------------------
	async hostCall(params, signal, timeoutMs, sink) {
		this.inFlight += 1
		this.clearIdleTimer()
		this.logSinks.set(sink, (this.logSinks.get(sink) ?? 0) + 1)
		let be = null
		try {
			be = await this.ensureBackend()
			return await be.callSearch(params, signal, timeoutMs)
		} catch (err) {
			if (be && be.closed && this.backend === be) {
				this.backend = null
				this.retiring = be.close()
			}
			return errorResult(`Semantic search backend unavailable: ${messageOf(err)}`)
		} finally {
			const left = this.logSinks.get(sink) - 1
			if (left > 0) this.logSinks.set(sink, left)
			else this.logSinks.delete(sink)
			this.inFlight -= 1
			if (this.inFlight === 0) {
				if (this.drained) this.drained()
				else this.armIdleTimer()
			}
		}
	}

	async ensureBackend() {
		if (this.backend) return this.backend
		if (this.spawnPromise) return this.spawnPromise
		if (this.retiring) {
			const retiring = this.retiring
			await retiring
			if (this.retiring === retiring) this.retiring = null
			return this.ensureBackend()
		}
		const be = new StdioBackend({
			pluginRoot: PLUGIN_ROOT,
			cwd: isDirectory(this.dir) ? this.dir : process.cwd(),
			forwardLog: (params) => {
				for (const sink of this.logSinks.keys()) sink(params)
			},
		})
		const pending = be.open().then(() => {
			this.backend = be
			return be
		})
		this.spawnPromise = pending
		const clear = () => {
			if (this.spawnPromise === pending) this.spawnPromise = null
		}
		pending.then(clear, clear)
		return pending
	}

	/** The window is the largest idle setting among this host and its connected guests. */
	idleMs() {
		let minutes = IDLE_MINUTES
		for (const guest of this.guests) minutes = Math.max(minutes, guest.idleMinutes)
		return Math.round(minutes * 60000)
	}

	clearIdleTimer() {
		if (this.idleTimer) {
			clearTimeout(this.idleTimer)
			this.idleTimer = null
		}
	}

	armIdleTimer() {
		this.clearIdleTimer()
		if (!this.backend) return
		this.idleTimer = setTimeout(() => {
			this.idleTimer = null
			if (this.inFlight === 0) void this.release('idle')
		}, this.idleMs())
	}

	/**
	 * Idle and departure share one release. The pipe name stays held until the backend has
	 * exited, so no new host can start a second backend on this root while this one lives.
	 */
	release(reason) {
		if (this.releasing) return this.releasing
		this.clearIdleTimer()
		debug(`host-release reason=${reason} pipe=${this.pipe}`)
		this.releasing = (async () => {
			if (this.inFlight > 0) await new Promise((resolve) => (this.drained = resolve))
			this.drained = null
			if (this.spawnPromise) await this.spawnPromise.catch(() => {})
			const be = this.backend
			this.backend = null
			if (be) await be.close()
			if (this.retiring) await this.retiring
			for (const socket of this.sockets) socket.destroy()
			this.sockets.clear()
			this.guests.clear()
			if (this.server) {
				const srv = this.server
				this.server = null
				await new Promise((resolve) => srv.close(() => resolve()))
			}
			this.hosting = false
			debug(`host-released pipe=${this.pipe}`)
		})().finally(() => {
			this.releasing = null
		})
		return this.releasing
	}
}

/** This frontend's connection to another frontend that hosts the root. */
class GuestConn {
	constructor(root, socket) {
		this.root = root
		this.socket = socket
		this.alive = true
		this.nextId = 1
		this.pending = new Map()
		socket.removeAllListeners('close')
		socket.removeAllListeners('error')
		socket.on('error', () => {})
		socket.on('close', () => {
			this.retire()
			for (const p of this.pending.values()) p.reject(new HostLost('host lost'))
			this.pending.clear()
		})
		socket.removeAllListeners('data')
		readFrames(socket, (frame) => {
			const p = this.pending.get(frame.id)
			if (frame.t === 'log') {
				for (const sink of new Set([...this.pending.values()].map((q) => q.sink))) sink(frame.params)
				return
			}
			if (!p) return
			if (frame.t === 'result') {
				this.pending.delete(frame.id)
				p.resolve({ result: frame.result })
				if (!this.alive && this.pending.size === 0) this.socket.end()
			} else if (frame.t === 'closing') {
				// The host is releasing: route nothing new here, but let dispatched calls finish.
				this.pending.delete(frame.id)
				this.retire()
				if (this.pending.size === 0) this.socket.end()
				p.resolve({ closing: true })
			}
		})
	}

	/**
	 * `{result}`, `{closing}` or `{unsent}`. A frame once written is never resent: after a lost
	 * host the caller cannot know whether the call reached a backend, and a replay could repeat a
	 * rebuild.
	 */
	retire() {
		this.alive = false
		if (this.root.conn === this) this.root.conn = null
	}

	async call(params, signal, timeoutMs, sink) {
		if (!this.alive || this.socket.destroyed) {
			this.retire()
			return { unsent: true }
		}
		if (signal.aborted) throw signal.reason ?? new Error('aborted')
		const id = this.nextId++
		const pending = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, sink }))
		const onAbort = () => {
			sendFrame(this.socket, { t: 'cancel', id })
			const p = this.pending.get(id)
			this.pending.delete(id)
			p?.reject(signal.reason ?? new Error('aborted'))
		}
		signal.addEventListener('abort', onAbort, { once: true })
		sendFrame(this.socket, { t: 'call', id, params, timeoutMs })
		try {
			return await pending
		} catch (err) {
			if (err instanceof HostLost) {
				debug(`guest-host-lost id=${id} pipe=${this.root.pipe}`)
				return { result: errorResult('Semantic search host exited mid-call; the next search starts a new backend.') }
			}
			throw err
		} finally {
			signal.removeEventListener('abort', onAbort)
		}
	}
}

function raceAbort(promise, signal) {
	if (!signal) return promise
	return new Promise((resolve, reject) => {
		const onAbort = () => reject(signal.reason ?? new Error('aborted'))
		if (signal.aborted) return onAbort()
		signal.addEventListener('abort', onAbort, { once: true })
		promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', onAbort))
	})
}

const roots = new Map()

function rootFor(dir) {
	let root = roots.get(dir)
	if (!root) {
		root = new Root(dir)
		roots.set(dir, root)
	}
	return root
}

// ---------------------------------------------------------------------------
// Request handling
// ---------------------------------------------------------------------------
const localCalls = new Set()
const localSink = (params) => {
	server.sendLoggingMessage(params).catch(() => {})
}

server.setRequestHandler(types.ListToolsRequestSchema, async () => ({ tools: [SEARCH_TOOL] }))

server.setRequestHandler(types.CallToolRequestSchema, async (request, extra) => {
	if (request.params.name !== SEARCH_TOOL.name) {
		return { content: [{ type: 'text', text: `Unknown tool: ${request.params.name}` }], isError: true }
	}
	const controller = new AbortController()
	const onAbort = () => controller.abort(extra.signal.reason)
	extra.signal.addEventListener('abort', onAbort, { once: true })
	localCalls.add(controller)
	try {
		const mapping = await searchRoot(request.params.arguments?.searchDir)
		const params = { ...request.params, arguments: { ...(request.params.arguments ?? {}), searchDir: mapping.indexDir } }
		const result = callerResult(await rootFor(mapping.indexDir).call(params, controller.signal, REQUEST_TIMEOUT_MS, localSink), mapping)
		return CSHARP_OFF ? { ...result, content: [...(result.content ?? []), { type: 'text', text: CSHARP_OFF }] } : result
	} catch (err) {
		return errorResult(`Semantic search backend unavailable: ${messageOf(err)}`)
	} finally {
		localCalls.delete(controller)
		extra.signal.removeEventListener('abort', onAbort)
	}
})

// The stdio server transport never reports stdin EOF, so the shim watches stdin itself: its end
// is the only signal that this frontend's client is gone.
let departing = null
function depart() {
	if (departing) return departing
	debug('frontend closing')
	for (const controller of localCalls) controller.abort()
	departing = (async () => {
		const work = []
		for (const root of roots.values()) {
			if (root.conn) root.conn.socket.destroy()
			if (root.hosting || root.releasing) work.push(root.release('departure'))
		}
		await Promise.allSettled(work)
		process.exit(0)
	})()
	return departing
}
process.stdin.once('end', depart)
process.stdin.once('close', depart)
server.onclose = () => void depart()

await server.connect(new StdioServerTransport())
debug('frontend ready')
