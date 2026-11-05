// Proof for tools/semantic_search_csharp_hook.mjs: the upstream-drift and grammar-integrity gates, the
// --check probe, .searchignore scanning and C# type-chunk trimming. Runs against the installed semantic-search plugin (or SEMANTIC_SEARCH_IDLE_TEST_PLUGIN_ROOT).
//   node --test .claude/tests/test_semantic_search_csharp_hook.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
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
	return builtCopy(user[0].installPath)
}

// The registered copy when its backend is built, else the newest built version beside it: a plugin update
// leaves the registered copy unbuilt until its first launch, and the proof needs a real dist.
function builtCopy(registered) {
	const built = (root) => existsSync(path.join(root, 'dist', 'mcp-server.js'))
		&& existsSync(path.join(root, 'node_modules')) && readdirSync(path.join(root, 'node_modules')).length > 0
	const parent = path.dirname(registered)
	if (built(registered) || !existsSync(parent)) return registered
	const version = (name) => name.split('.').map((part) => (/^\d+$/.test(part) ? Number(part) : -1))
	const newer = (a, b) => { const x = version(a), y = version(b); for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] ?? -1) !== (y[i] ?? -1)) return (y[i] ?? -1) - (x[i] ?? -1) } return 0 }
	const siblings = readdirSync(parent).filter((name) => built(path.join(parent, name))).sort(newer)
	if (siblings.length === 0) return registered
	console.log(`note: ${registered} is not built; the proof uses ${path.join(parent, siblings[0])}`)
	return path.join(parent, siblings[0])
}

const PLUGIN = pluginRoot()
const { buildCsharpPatch } = await import(pathToFileURL(HOOK).href)

test('H1 the reviewed upstream builds a patch that maps .cs and adds the grammar', () => {
	const { sources, revision } = buildCsharpPatch(PLUGIN)
	assert.equal(sources.size, 4)
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

/** Run `code` in a child whose plugin modules load through the real hook; returns its JSON stdout. */
function runHooked(code) {
	const prelude = `const load = (m) => import(${JSON.stringify(pathToFileURL(path.join(PLUGIN, 'dist')).href)} + '/' + m);\n`
	const out = execFileSync(process.execPath, ['--import', pathToFileURL(HOOK).href, '--input-type=module', '-e', prelude + code], {
		encoding: 'utf8',
		env: { ...process.env, SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT: PLUGIN },
	})
	return JSON.parse(out)
}

function scanTree(searchignore) {
	const root = mkdtempSync(path.join(tmpdir(), 'csharp-hook-scan-'))
	for (const dir of ['Tests/Unit', 'Src']) mkdirSync(path.join(root, dir), { recursive: true })
	writeFileSync(path.join(root, 'Tests/Unit/ArmorTest.cs'), 'public class ArmorTest {}\n')
	writeFileSync(path.join(root, 'Src/Armor.cs'), 'public class Armor {}\n')
	writeFileSync(path.join(root, 'notes.md'), '# notes\n')
	if (searchignore !== undefined) writeFileSync(path.join(root, '.searchignore'), searchignore)
	try {
		return runHooked(`const { scanProject } = await load('scanner.js');\nconst files = await scanProject(${JSON.stringify(root)});\nprocess.stdout.write(JSON.stringify(files.map((f) => f.filePath).sort()))`)
	} finally {
		rmSync(root, { recursive: true, force: true })
	}
}

test('H5 a path listed in .searchignore is not indexed; the rest of the tree is', () => {
	assert.deepEqual(scanTree('# proofs live apart\r\nTests/\r\n'), ['Src/Armor.cs', 'notes.md'])
})

test('H6 a tree with no .searchignore scans exactly as before', () => {
	assert.deepEqual(scanTree(), ['Src/Armor.cs', 'Tests/Unit/ArmorTest.cs', 'notes.md'])
})

test('H7 a C# type chunk holds only its own lines; each member is its own chunk, indexed once', () => {
	const source = [
		'/// <summary>Armor with a café-grade finish.</summary>',
		'[GlobalClass]',
		'public partial class Armor : Resource {',
		'    [Export] public float BaseReduction = 0.25f; // café',
		'    public Armor() { ArmorCtorBodyToken(); }',
		'    public float Value { get => ArmorPropertyBodyToken; set {} }',
		'    /// <summary>Reduce damage.</summary>',
		'    public float ApplyDamageReduction(float damage) {',
		'        return damage * ArmorMethodBodyToken;',
		'    }',
		'    private sealed class Plate {',
		'        public int PlateDepth;',
		'        public void Polish() { PlateMethodBodyToken(); }',
		'    }',
		'}',
		'public struct Health { public int HealthValue { get; set; } }',
		'',
	].join('\n')
	const chunks = runHooked(`const { chunkFile } = await load('chunker.js');\nconst chunks = await chunkFile(${JSON.stringify(source)}, 'Armor.cs', 'csharp');\nprocess.stdout.write(JSON.stringify(chunks.map(({ name, kind, content }) => ({ name, kind, content }))))`)
	const one = (name, kind) => {
		const found = chunks.filter((c) => c.name === name && c.kind === kind)
		assert.equal(found.length, 1, `expected one ${kind} ${name}: ${JSON.stringify(chunks)}`)
		return found[0].content
	}
	assert.equal(one('Armor', 'class'), [
		'[GlobalClass]',
		'public partial class Armor : Resource {',
		'    [Export] public float BaseReduction = 0.25f; // café',
		'    /// <summary>Reduce damage.</summary>',
		'}',
	].join('\n'), 'the type keeps its attributes, declaration, fields and comments; non-ASCII text does not shift the cut')
	assert.match(one('ApplyDamageReduction', 'method'), /ArmorMethodBodyToken/)
	assert.match(one('Armor', 'constructor'), /ArmorCtorBodyToken/)
	assert.match(one('Value', 'property'), /ArmorPropertyBodyToken/)
	const plate = one('Plate', 'class')
	assert.match(plate, /PlateDepth/)
	assert.ok(!plate.includes('PlateMethodBodyToken'), 'nested class Plate still carries its method body')
	assert.match(one('Polish', 'method'), /PlateMethodBodyToken/)
	const health = one('Health', 'struct')
	assert.match(health, /public struct Health \{/)
	assert.ok(!health.includes('get; set;'), 'a one-line struct still carries its property')
	for (const token of ['BaseReduction', 'ArmorCtorBodyToken', 'ArmorPropertyBodyToken', 'ArmorMethodBodyToken', 'PlateDepth', 'PlateMethodBodyToken', 'get; set;']) {
		assert.equal(chunks.filter((c) => c.content.includes(token)).length, 1, `${token} is not indexed exactly once`)
	}
})

const chunkCsharp = (source) => runHooked(`const { chunkFile } = await load('chunker.js');\nconst chunks = await chunkFile(${JSON.stringify(source)}, 'Probe.cs', 'csharp');\nprocess.stdout.write(JSON.stringify(chunks.map(({ name, kind, content }) => ({ name, kind, content }))))`)

test('H8 a C# gap of only namespace, using and directive lines is not a chunk; a gap with code is', () => {
	const preamble = chunkCsharp([
		'#nullable enable',
		'namespace Acme.Storage;',
		'',
		'using System;',
		'using global::Godot;',
		'using GCol = Godot.Collections;',
		'// shared aliases',
		'',
		'public class Inventory { }',
		'',
	].join('\n'))
	assert.deepEqual(preamble.map((c) => c.kind), ['class'], JSON.stringify(preamble))
	const withCode = chunkCsharp([
		'namespace Acme.Storage;',
		'using System;',
		'public delegate void InventoryChanged(int slot);',
		'[assembly: InternalsVisibleTo("Tests")]',
		'using Godot;',
		'public class Inventory { }',
		'',
	].join('\n'))
	assert.ok(withCode.some((c) => c.kind === 'file' && c.content.includes('delegate void InventoryChanged')), JSON.stringify(withCode))
	const documented = chunkCsharp('using System;\nusing Godot;\nnamespace Foo;\n/// <summary>Inventory tracks every stored item.</summary>\npublic class Inventory { }\n')
	assert.ok(documented.some((c) => c.content.includes('Inventory tracks every stored item')), `a type's /// summary stays indexed: ${JSON.stringify(documented)}`)
	const typeless = chunkCsharp('namespace {{PROJECT_NAME}};\nusing System;\nusing Godot;\nusing GCol = Godot.Collections;\nusing Sys = System.IO;\n')
	assert.equal(typeless.length, 1, 'a file with no declarations stays one whole-file chunk')
})

test('H9 documents embed in length order, so a batch pads to similar lengths; vectors return in input order', () => {
	const texts = ['ccc', 'a', 'eeeee', 'bb', 'dddd', 'a', 'ffffff']
	const run = runHooked(`const { Embedder } = await load('embedder.js');
const e = new Embedder({ batchSize: 2 });
const batches = [];
const progress = [];
e.extractor = async (batch) => { batches.push(batch); return { data: Float32Array.from(batch.map((t) => t.length)) } };
const vectors = await e.embedDocuments(${JSON.stringify(texts)}, (phase, done, total) => progress.push([phase, done, total]));
process.stdout.write(JSON.stringify({ batches, progress, vectors: vectors.map((v) => Array.from(v)) }))`)
	const lengths = run.batches.flat().map((t) => t.length)
	assert.deepEqual(lengths, [...lengths].sort((a, b) => a - b), `embedded out of length order: ${JSON.stringify(run.batches)}`)
	assert.deepEqual(run.vectors, texts.map((t) => [t.length]), 'each vector belongs to its own input text')
	assert.deepEqual(run.progress.at(-1), ['embedding', texts.length, texts.length], 'progress still counts every document')
})
