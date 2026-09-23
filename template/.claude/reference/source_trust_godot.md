# Source Trust — Godot, GdUnit4 and .NET

The Godot-stack sources and exceptions for `reference/source_trust.md`, whose tiers, rules, claim shape and fetch order apply unchanged.

## P1 sources

- Godot class reference — `.claude/cache/godot-docs/doc/classes/<Class>.xml`, version-pinned to the
  engine (populate or refresh with `.claude/scripts/godot_docs_cache.sh`, ~5s). This XML is the
  first-party source the HTML reference is *generated from*, tagged to the exact pin, so it outranks
  the rendered page on both authority and version-exactness. Read it directly — no fetch, no model,
  no truncation. Discovery ("which class does X?") goes to `.claude/reference/godot_class_index.md`.
  It is fetch-order step 1, the version-pinned local docs cache.
- Godot tutorials/guides, or a class the cache cannot answer —
  `mcp__plugin_context7_context7__query-docs` against `/websites/godotengine_en_4_7`.
  **`docs.godotengine.org` is not the source of record for a class.** Two independent reasons, one
  permanent and one intermittent. Permanent: `/en/stable/` is a moving alias, so it cannot satisfy
  rule 3, and the rendered page costs ~1.6 MB to answer what the cache answers in ~1 KB. Intermittent:
  the host is Cloudflare bot-gated under load. Treat it as unreliable, not unusable: a
  200 from it is legitimate P1 **for a version-pinned URL**, and a failure from it is never evidence
  about content — record a gap and go to the cache or context7.
- GdUnit4 README — `https://raw.githubusercontent.com/godot-gdunit-labs/gdUnit4Net/master/README.md`
- GdUnit4 examples — `https://github.com/godot-gdunit-labs/gdUnit4NetExamples/tree/master` (browse URL; no raw form)
- GdUnit4 CMD runner — `https://godot-gdunit-labs.github.io/gdUnit4/latest/advanced_testing/cmd/`
- C# / .NET 9 — `https://learn.microsoft.com/en-us/dotnet/csharp/`

## Version exceptions

- This harness has no reliable built-in knowledge of Godot 4.7.1, GdUnit4, or .NET 9 (rule 1).
- A doc page for another engine version answers a different question — measured: `Node.reparent` gained a `physics_interpolation` warning between 4.4 and 4.7.1, and this project runs Jolt.
- **GdUnit4 is the exception that must be stated, never assumed:** its P1 URLs above are `master`/`latest` while this repo may run a patched fork (check the `GdUnit4` package version in `{{PROJECT_NAME}}.csproj`). A GdUnit4 claim names the fork and that the cited page is unversioned upstream, or it is P3.
