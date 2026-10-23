// Proof for tools/semantic_search_csharp_hook.mjs: the upstream-drift and grammar-integrity gates, and the
// --check probe. Runs against the installed semantic-search plugin (or SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT).
//   node --test .claude/tests/test_semantic_search_csharp_hook.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const TOOLS = path.resolve(HERE, '..', 'tools')
const HOOK = path.join(TOOLS, 'semantic_search_csharp_hook.mjs')

const REGISTRY = path.join(homedir(), '.claude', 'plugins', 'installed_plugins.json')
if (!process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT && !existsSync(REGISTRY)) {
	console.log('PLATFORM-SKIP-PROOF test_semantic_search_csharp_hook.mjs: no semantic-search plugin is installed on this machine')
	process.exit(0)
}

function pluginRoot() {
	if (process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT) return process.env.SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT
	const registry = REGISTRY
	const entries = (JSON.parse(readFileSync(registry, 'utf8')).plugins || {})['semantic-search@dreb'] || []
	const user = entries.filter((entry) => entry.scope === 'user')
	assert.equal(user.length, 1, 'expected exactly one user-scope semantic-search@dreb registration')
	return user[0].installPath
}

const PLUGIN = pluginRoot()
const { buildCsharpPatch } = await import(pathToFileURL(HOOK).href)

test('H1 the reviewed upstream builds a patch that maps .cs and adds the grammar', () => {
	const { sources, revision } = buildCsharpPatch(PLUGIN)
	assert.equal(sources.size, 3)
	assert.match(revision, /^[0-9a-f]{64}$/)
	const joined = [...sources.values()].join('\n')
	assert.match(joined, /\[".cs", "csharp"\]/)
	assert.match(joined, /csharp: \[/)
})

test('H2 an upstream file with changed bytes is refused, naming the file', () => {
	const copy = mkdtempSync(path.join(tmpdir(), 'csharp-hook-'))
	try {
		cpSync(path.join(PLUGIN, 'dist'), path.join(copy, 'dist'), { recursive: true })
		const scanner = path.join(copy, 'dist', 'scanner.js')
		writeFileSync(scanner, readFileSync(scanner, 'utf8') + '\n// changed upstream\n')
		assert.throws(() => buildCsharpPatch(copy), /scanner\.js: upstream SHA256 .* is not reviewed/)
	} finally {
		rmSync(copy, { recursive: true, force: true })
	}
})

test('H3 a grammar whose bytes changed is refused before any upstream is read', () => {
	const copy = mkdtempSync(path.join(tmpdir(), 'csharp-hook-'))
	try {
		cpSync(HOOK, path.join(copy, 'semantic_search_csharp_hook.mjs'))
		writeFileSync(path.join(copy, 'tree-sitter-c_sharp.wasm'), Buffer.from([0, 97, 115, 109]))
		const probe = spawnSync(process.execPath, ['--input-type=module', '-e',
			`import { buildCsharpPatch } from ${JSON.stringify(pathToFileURL(path.join(copy, 'semantic_search_csharp_hook.mjs')).href)}; buildCsharpPatch(${JSON.stringify(PLUGIN)})`],
		{ encoding: 'utf8' })
		assert.notEqual(probe.status, 0)
		assert.match(probe.stderr, /C# grammar checksum mismatch/)
	} finally {
		rmSync(copy, { recursive: true, force: true })
	}
})

test('H4 --check proves AST injection and fail-loud parsing on the reviewed upstream', () => {
	const out = execFileSync(process.execPath, [HOOK, '--check', PLUGIN], { encoding: 'utf8' })
	const report = JSON.parse(out)
	assert.equal(report.fileType, 'csharp')
	assert.equal(report.parseFailureRejected, true)
})
