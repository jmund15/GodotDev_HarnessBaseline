import { createHash } from 'node:crypto'
import { readFileSync, realpathSync } from 'node:fs'
import { registerHooks } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const GRAMMAR = fileURLToPath(new URL('./tree-sitter-c_sharp.wasm', import.meta.url))
const GRAMMAR_SHA256 = '6f69e1cae44e1c32c1eccc170dc5a9778fb94ff716f71113fe1f8c4299aa2f40'
// Reviewed DreB 2.71.0 bytes. Even unchanged anchors cannot prove changed callers safe.
const UPSTREAM = {
	'scanner.js': 'f55c8eb49b1fe0d6f6c6408fbd28203cce3acd39785405403a6c2ac69a10e5d3',
	'chunker.js': '21405cfbb9ba90c6c79035662275fb8e6a3e4efa131614eb74692653e6fd9efa',
	'tree-sitter-chunker.js': '23fc510669da81397d139e6baefad7e3a9ac7b9182ada542a9f6b3c763cd4a7f',
}
const CSHARP_EXTRACTORS = [
	['class_declaration', 'class'], ['struct_declaration', 'struct'],
	['interface_declaration', 'interface'], ['enum_declaration', 'enum'],
	['record_declaration', 'record'], ['method_declaration', 'method'],
	['constructor_declaration', 'constructor'], ['property_declaration', 'property'],
]
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')

// Injected into tree-sitter-chunker.js as source; each may reference only its argument.
function isCsharpPreamble(content) {
	const preambleLine = /^\s*(?:(?:global\s+)?using\s+[^;(]*;|namespace\s+[\w.]+\s*[;{]?|[{}]|#.*|\/\/(?!\/).*)?\s*$/
	return content.split('\n').every((line) => preambleLine.test(line))
}

function trimCsharpTypes(regions) {
	const typeKinds = new Set(['class', 'struct', 'interface', 'enum', 'record'])
	return regions.map((region) => {
		if (!typeKinds.has(region.kind)) return region
		const members = regions
			.filter((r) => r !== region && r.startIndex >= region.startIndex && r.endIndex <= region.endIndex)
			.sort((a, b) => a.startIndex - b.startIndex || b.endIndex - a.endIndex)
		let content = ''
		let cursor = region.startIndex
		for (const member of members) {
			if (member.startIndex < cursor) continue
			content += region.content.slice(cursor - region.startIndex, member.startIndex - region.startIndex)
			cursor = member.endIndex
		}
		content += region.content.slice(cursor - region.startIndex)
		return { ...region, content: content.split('\n').filter((line) => line.trim()).join('\n') }
	})
}

function replaceOnce(source, anchor, replacement, name) {
	if (source.split(anchor).length !== 2) throw new Error(`C# hook drift: ${name}: anchor ${JSON.stringify(anchor)} is not unique`)
	return source.replace(anchor, replacement)
}

export function buildCsharpPatch(pluginRoot) {
	const grammar = readFileSync(GRAMMAR)
	if (hash(grammar) !== GRAMMAR_SHA256) throw new Error('C# grammar checksum mismatch')
	const sources = new Map()
	for (const [name, expected] of Object.entries(UPSTREAM)) {
		const filename = realpathSync(path.join(pluginRoot, 'dist', name))
		const bytes = readFileSync(filename)
		if (hash(bytes) !== expected) throw new Error(`C# hook drift: ${name}: upstream SHA256 ${hash(bytes)} is not reviewed; repin and rerun proofs before installing`)
		let source = bytes.toString('utf8')
		const replace = (anchor, replacement) => { source = replaceOnce(source, anchor, replacement, name) }
		if (name === 'scanner.js') {
			replace('const EXTENSION_MAP = new Map([', 'const EXTENSION_MAP = new Map([\n    [".cs", "csharp"],')
			// .searchignore drops tracked paths from the index without hiding them from git.
			replace('        loadGitignore(ig, projectRoot, projectRoot);\n', '        loadGitignore(ig, projectRoot, projectRoot);\n        const searchignorePath = join(projectRoot, ".searchignore");\n        if (existsSync(searchignorePath)) ig.add(readFileSync(searchignorePath, "utf-8"));\n')
		} else if (name === 'chunker.js') {
			replace('const TREE_SITTER_LANGUAGES = new Set([', 'const TREE_SITTER_LANGUAGES = new Set([\n    "csharp",')
			replace('        catch {', '        catch (error) {\n            if (fileType === "csharp") throw error;')
		} else {
			replace('const GRAMMAR_PATHS = {', `const GRAMMAR_PATHS = {\n    csharp: ${JSON.stringify(GRAMMAR)},`)
			const extractors = CSHARP_EXTRACTORS.map(([type, kind]) => `        { type: "${type}", kind: "${kind}", getName: nameField },`).join('\n')
			replace('const LANGUAGE_EXTRACTORS = {', `const LANGUAGE_EXTRACTORS = {\n    csharp: [\n${extractors}\n    ],`)
			// Stock outermost-only extraction drops every member of a named type. C# keeps members as
			// their own chunks and cuts their text out of each enclosing type, so every line is indexed once.
			replace('            content: node.text,\n        });', '            content: node.text,\n            startIndex: node.startIndex,\n            endIndex: node.endIndex,\n        });')
			replace('function extractRegions(', `${trimCsharpTypes.toString()}\n${isCsharpPreamble.toString()}\nfunction extractRegions(`)
			// A gap of only namespace, using and directive lines matches every query on its imports and says nothing.
			replace('        const gaps = collectGaps(regions, sourceLines, filePath, language);', '        const gaps = collectGaps(regions, sourceLines, filePath, language).filter((gap) => language !== "csharp" || !isCsharpPreamble(gap.content));')
			replace('    // Remove nested regions — keep only outermost', '    if (extractors === LANGUAGE_EXTRACTORS.csharp) return trimCsharpTypes(raw);\n    // Remove nested regions — keep only outermost')
			// Gap coverage uses the union of enclosing types, not overlapping members.
			replace('        cursor = region.endLine + 1;', '        cursor = Math.max(cursor, region.endLine + 1);')
			replace('        const lines = content.split("\\n");', '        if (language === "csharp") throw new Error("C# AST parse failed");\n        const lines = content.split("\\n");')
		}
		sources.set(pathToFileURL(filename).href, source)
	}
	return { sources, revision: hash(Buffer.concat([readFileSync(fileURLToPath(import.meta.url)), grammar])) }
}

async function activate(pluginRoot) {
	const { sources } = buildCsharpPatch(pluginRoot)
	registerHooks({
		load(url, context, nextLoad) {
			const result = nextLoad(url, context)
			const source = sources.get(url)
			return source === undefined ? result : { ...result, source }
		},
	})
	const scanner = await import(pathToFileURL(realpathSync(path.join(pluginRoot, 'dist/scanner.js'))).href)
	const { chunkFile } = await import(pathToFileURL(realpathSync(path.join(pluginRoot, 'dist/chunker.js'))).href)
	const sample = `public class Armor {
    public Armor() {}
    public float Value { get; set; }
    public float ApplyDamageReduction(float damage) => damage * Value;
}
public struct Health { public int Value { get; set; } }
public interface IArmor { float Value { get; } }
public enum Element { Fire, Earth }
public record Hit(float Damage);
public record struct Packet(float Damage);
`
	const chunks = await chunkFile(sample, 'probe.cs', 'csharp')
	const expected = ['Armor', 'Health', 'IArmor', 'Element', 'Hit', 'Packet', 'ApplyDamageReduction', 'Value']
	for (const name of expected) {
		if (!chunks.some((c) => c.name === name && c.fileType === 'csharp')) throw new Error(`C# AST smoke failed: missing ${name}`)
	}
	for (const [, kind] of CSHARP_EXTRACTORS) {
		if (!chunks.some((c) => c.kind === kind)) throw new Error(`C# AST smoke failed: missing ${kind}`)
	}
	if (scanner.detectFileType('probe.cs') !== 'csharp') throw new Error('C# scanner injection failed')
	return { fileType: scanner.detectFileType('probe.cs'), nonCsharpType: scanner.detectFileType('probe.py'), chunks: chunks.map(({ name, kind }) => ({ name, kind })) }
}

async function probeParseFailure(pluginRoot) {
	const { chunkFile } = await import(pathToFileURL(realpathSync(path.join(pluginRoot, 'dist/chunker.js'))).href)
	try { await chunkFile(null, 'invalid.cs', 'csharp') } catch { return true }
	throw new Error('C# AST errors silently fall back to plaintext')
}

// Two entry points: `--check <pluginRoot>` (CLI probe) and `--import` activation via SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT.

if (process.argv[2] === '--check' && process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	try {
		const pluginRoot = path.resolve(process.argv[3])
		const result = await activate(pluginRoot)
		result.parseFailureRejected = await probeParseFailure(pluginRoot)
		process.stdout.write(JSON.stringify(result) + '\n')
	} catch (error) {
		process.stderr.write(`${error.message}\n`)
		process.exitCode = 1
	}
} else if (process.env.SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT) {
	await activate(process.env.SEMANTIC_SEARCH_CSHARP_PLUGIN_ROOT)
}
