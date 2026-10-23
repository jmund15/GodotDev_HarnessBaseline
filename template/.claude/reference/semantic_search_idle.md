# Semantic Search — Shared Backend and Idle Unload (project-local)

The stock server never releases its warmed engine: a backend that embedded holds ~2.5 GB private, and one that only searched a pre-built full index holds ~0.56 GB. `.claude/tools/semantic_search_idle_proxy.mjs` answers the handshake and `tools/list` from a pinned snapshot of the upstream `search` tool. On the first `tools/call` for an index root it starts `bin/server.js` as a child with the C# import hook below, shares that child with every other frontend searching the same root, and closes it once the idle window passes with nothing in flight. The tool name and schema do not change.

- **Root.** The frontend resolves `searchDir` against its own cwd (absent means its cwd), canonicalizes it with `realpath.native` and forwards that absolute path. A relative path means the caller's directory; a junction and its target are one root.
- **One backend per root**, spawned with cwd = the root. The stock engine keeps one embedder per root anyway.
- **Election.** Per root, the pipe is `\\.\pipe\semantic-search-<24 hex of sha256[protocol, root, plugin root, version, C# hook/grammar revision]>`. The first frontend to search listens and hosts the backend; the others connect as guests. A guest presenting another key is refused. There is no lock file: Windows drops a pipe name with its owner, including after a hard kill.
- **Wire.** Newline-delimited JSON: `hello`/`welcome`/`refused`/`closing`, `call`/`cancel`/`result`/`log`. Call ids are scoped to their socket; a cancel, or a guest's death, cancels that call at the backend.
- **No replay.** A call frame, once written, is never resent. Host loss mid-call returns `Semantic search host exited mid-call`, and the next call re-elects. A backend failure is forwarded as one error with no respawn until the next call.
- **Release.** Idle, or the host's stdin closing, starts one release. The host keeps the pipe name and answers new calls with `closing`. It finishes dispatched guest calls, then frees the name only after its backend exits, so no second backend starts on the root.
- **Client teardown.** An SDK client kills a closing server 2 s after ending its stdin; a guest call cut off there gets the mid-call error.
- **Hard kill.** A hard-killed host takes its backend with it: libuv puts every non-detached child in a kill-on-close job object [P1; libuv `src/win/process.c:96,1149-1152`; proof S3.4].
- **Settings.** `SEMANTIC_SEARCH_IDLE_MINUTES` sets the window (default 10); a value that is not positive, or exceeds the 35791 minutes a Node timer can hold, stops the shim at startup. A host uses the largest window among itself and its connected guests. `MCP_TOOL_TIMEOUT` (ms) bounds each of the caller's requests.
- **Platform.** Sharing runs on win32 only. Elsewhere each frontend keeps a private backend per root: a POSIX socket leaves a stale file after a crash, and nothing here proves its recovery.
- **Install.** `python3 .claude/tools/semantic_search_idle_install.py --install` copies the proxy, C# hook, grammar, provenance and license to `~/.claude/semantic-search-idle/`, and repoints the plugin manifest's `mcpServers.semantic-search` entry there. It backs up a manifest that does not already carry the shim. `--rollback` restores the newest stock backup byte-exactly and drops the SessionStart checker.
- **Check.** `python3 .claude/tools/semantic_search_idle_install.py --check` is the read-only, silent-when-healthy SessionStart check. It compares the live tool contract, installed asset hashes, reviewed upstream module hashes and a C# AST smoke parse. A session keeps the shim it started with; old and new shims do not share a backend.
- **After a plugin update:** run `--check`. A restored stock entry, moved install path, changed contract or changed hooked module is drift. Review changed upstream code and repin the hook's hashes/anchors if needed, rerun the proofs, then `--install`. Restart semantic-search MCP connections before reindexing; an already-running frontend keeps its old launch code.

## C# AST hook

Node must support `node:module.registerHooks` (proved on Node 24.12.0). The proxy passes `--import semantic_search_csharp_hook.mjs` and the plugin root only to its backend child. The hook replaces module source in memory, leaving cache JavaScript unchanged. It adds `.cs` to `EXTENSION_MAP`, `csharp` to `TREE_SITTER_LANGUAGES`, an absolute grammar path and named extractors to `LANGUAGE_EXTRACTORS`.

C# chunks include classes, structs, interfaces, enums, records (including record structs), methods, constructors and properties. Unlike the stock outermost-only extraction, C# retains members alongside enclosing types. Gap coverage advances over their union. C# parser failures propagate instead of falling back to plaintext. Missing or altered grammar bytes or a changed upstream module turn C# off, not search: the proxy starts the stock backend, ends every search result with a line naming the drift (proof S2.12), and `--check` fails. A failed declaration smoke test stops the backend child.

The vendored `tools/tree-sitter-c_sharp.wasm` is the unmodified WASM shipped by npm `tree-sitter-c-sharp@0.23.5`, compatible with the installed `web-tree-sitter@0.26.13`. Source URL, integrity and checksum are in `tools/tree-sitter-c_sharp.provenance.json`; the adjacent `.LICENSE` travels with every install. `node .claude/tools/semantic_search_csharp_hook.mjs --check <plugin-root>` checks source compatibility and parses all requested declaration kinds without loading the embedder.

## Measured (2026-09-25, `measure_semantic_search_shared.ps1`)

One pre-built index per row, frozen by hash between arms; arms ran HEAD, shared, shared, HEAD. Figures are peak private bytes of the backends.

| Root, clients | Per-session shim (`3b1193cd`) | Shared shim |
|---|---|---|
| 168-file subset, 3 | 3 backends, 0.57–0.59 GB | 1 backend, 0.19–0.20 GB |
| full repo (70 MB index), 7 | 7 backends, 2.41–2.81 GB; one client failed `database is locked` | 1 backend, 0.56 GB, no errors |

A shim that never searches costs 0.054 GB and starts no backend. Artifacts: `.claude/scratch/semantic_search_shared/compare_n3_subset.json`, `compare_n7_full.json`, `probe_n7_subset.json`, `probe_n7_full.json`.

**No shape default drops search for no-search children.** Paired haiku review children: the default profile ran one 0.054 GB shim and no backend; `-mcp:semantic-search` ran none.
The per-job delta already exists (`reference/sidecar_dispatch.md` §Tool profile). Revisit if a shim measures above 0.2 GB, or a stock change starts a backend before the first call.

## Proofs

- `node --test .claude/tests/test_semantic_search_idle_proxy.mjs`: S1 contract, S2 idle lifecycle, S3 sharing, plus C# named declaration parsing and a real search through the injected child. It is a `proof_heavy` proof: the stamp runs it when the shim or the proof changes, the default battery skips it. `SEMANTIC_SEARCH_IDLE_TEST_PROXY=<copy>` runs against a planted shim beside copies of its hook and grammar; removing its `--import` must turn the C# launch case red. A planted defect in an S3 seam must turn its case red.
- `python3 .claude/tests/test_semantic_search_idle_install.py`: installation/rollback, missing C# assets and drift in a hooked upstream symbol through `--check`.
- `.claude/tests/measure_semantic_search_idle.ps1`: single-client private bytes and idle unload, against an isolated root under `.claude/scratch`.
- `.claude/tests/measure_semantic_search_shared.ps1`: N concurrent clients per arm, `-Proxies` arms in one invocation, RAM admission, frozen-index gate. A wrong `-ExpectPeakBackends`, an unmeetable `-MinFreeGb`, a too-short reply timeout, a too-slow sampler, an error reply, a silent child, an empty `-Proxies` and a failed setup step each exit non-zero and record `ok: false`.
- Both measure scripts share `.claude/tests/lib/measure_common.ps1` and delete only a `-Root` below `.claude/scratch`.
