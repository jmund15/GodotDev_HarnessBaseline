# Source Trust Tiers

Doctrine for any claim about behavior **outside this repo** — engine, library, runtime, spec, tooling. Loaded by reference, not by path: `/research` and `/explore`'s `exp-external-truth` lens both point here, and neither carries a copy.

**Cite or gap.** Every external claim ends with its source tier, or it is recorded as a gap. There is no third state — an uncited external claim is an inference wearing a fact's clothes, and a plan built on one is fiction.

## Tiers

**P1 — citable alone.** First-party and authoritative: the thing itself, not an account of it.

- Godot class reference — `.claude/cache/godot-docs/doc/classes/<Class>.xml`, version-pinned to the
  engine (populate or refresh with `.claude/scripts/godot_docs_cache.sh`, ~5s). This XML is the
  first-party source the HTML reference is *generated from*, tagged to the exact pin, so it outranks
  the rendered page on both authority and version-exactness. Read it directly — no fetch, no model,
  no truncation. Discovery ("which class does X?") goes to `.claude/reference/godot_class_index.md`.
- Godot tutorials/guides, or a class the cache cannot answer —
  `mcp__plugin_context7_context7__query-docs` against `/websites/godotengine_en_4_7`.
  **`docs.godotengine.org` is not the source of record for a class.** Two independent reasons, one
  permanent and one intermittent. Permanent: `/en/stable/` is a moving alias, so it cannot satisfy
  rule 3, and the rendered page costs ~1.6 MB to answer what the cache answers in ~1 KB. Intermittent:
  the host is Cloudflare bot-gated under load — measured 2026-08-09, HTTP 429 to `curl` and
  `WebFetch` host-wide in one window, then 200 with the full page hours later to `curl` and `httpx`
  alike, while a browser saw the verification interstitial. Treat it as unreliable, not unusable: a
  200 from it is legitimate P1 **for a version-pinned URL**, and a failure from it is never evidence
  about content — record a gap and go to the cache or context7.
- GdUnit4 README — `https://raw.githubusercontent.com/godot-gdunit-labs/gdUnit4Net/master/README.md`
- GdUnit4 examples — `https://github.com/godot-gdunit-labs/gdUnit4NetExamples/tree/master` (browse URL; no raw form)
- GdUnit4 CMD runner — `https://godot-gdunit-labs.github.io/gdUnit4/latest/advanced_testing/cmd/`
- C# / .NET 9 — `https://learn.microsoft.com/en-us/dotnet/csharp/`
- `mcp__plugin_context7_context7__query-docs` against a resolved library id
- First-party source read from `raw.githubusercontent.com`, official changelogs, release notes, and spec text

**P2 — citable, must name the version.** Official but secondary: vendor-domain blog posts, maintainer conference talks, migration guides. The claim text states the version the answer is true for; without it a P2 citation is P3.

**P3 — never load-bearing alone.** Issue trackers, proposals, forums, Stack Overflow, third-party write-ups — a secondary account of the behavior rather than the behavior's owner. A P3 hit is a **pointer**, not an answer.

## Rules

1. **Never answer from memory.** This harness has no reliable built-in knowledge of Godot 4.7.1, GdUnit4, or .NET 9. Fetch it. Could not fetch it → gap, not inference.
2. **Escalate every P3 to its owner.** Chase the P3 hit to the P1/P2 source that owns the behavior and cite that instead. No owning source exists → the absence IS the finding: emit `polarity: "unclear"` with a gap naming what would settle it, never `exists`.
3. **Pin the version in the claim text.** Godot 4.7.1 with Jolt physics, .NET 9 (SSOT: `.claude/reference/project_stack.md`). A doc page for another major version answers a different question — measured: `Node.reparent` gained a `physics_interpolation` warning between 4.4 and 4.7.1, and this project runs Jolt. **GdUnit4 is the exception that must be stated, never assumed:** its P1 URLs above are `master`/`latest` while this repo runs a patched fork (`5.1.0-rc4-pp.1`, per `{{PROJECT_NAME}}.csproj`). A GdUnit4 claim names the fork and that the cited page is unversioned upstream, or it is P3.
4. **A partial reader cannot prove absence.** A digest layer that drops part of a page reports the gap as silence, and silence reads as absence. `read_web` now declares what it saw — its preflight block carries `mode=` and `raw=Nc, seen=Nc` per URL, plus a flag when retention is low, an interstitial was withheld, or extraction came back empty. **Read that ratio before recording any negative**, and treat a flagged URL as unread rather than empty. (It once destroyed content silently: it ran an HTML boilerplate-remover over non-HTML, keeping 70.5% of the Godot `Node.xml` and 46.0% of a markdown README. Fixed 2026-08-09 by content-type routing — non-HTML now passes through verbatim.) Confirm true absence only against bytes you fetched whole; the same rule binds any digest layer between you and the page.
5. **Citation-as-audit — check the quote against bytes, not against another summary.** Before consuming a returned claim set, verify two or three `evidence` quotes yourself by matching them against the fetched artifact. Re-fetching through the same digest layer that produced the claim audits nothing. Land on a summary of the thing rather than the thing, and the run failed at its one job — re-dispatch rather than report.
6. **Stopping criterion.** A run ends when every listed question is P1/P2-answered or recorded as a gap. Not sooner, not later; "went deeper than asked" and "missed the one detail that mattered" are the same defect.

## Claim shape

The claims schema carries no `tier` field, so tier rides in the fields it already has:

- `file` — the source cited: the URL fetched, or the local path when the source IS local (the
  version-pinned Godot cache).
- `artifact` — the local bytes the quote was taken from, when any exist. This is what makes the
  quote checkable (`.claude/tools/verify_claims.py`); `fetch_source.sh`'s TSV manifest carries the
  URL→artifact mapping that joins the two. A local artifact is *stronger* evidence than a URL, never
  a missing one.
- `evidence` — the VERBATIM sentence from that source. A paraphrase is not a citation.
- `claim` — ends with the tier tag, `[P1]` / `[P2]` / `[P3]`, plus the version it is pinned to.
- A claim citing neither a URL nor a local source in `file` is `unverified`, whatever its tier tag
  says. **Absence of an `artifact` is not that** — context7 and `WebSearch` are legitimately
  artifact-less, and demoting them would mechanically downgrade a tier this file calls citable
  alone. Unverifiable-by-machine and uncited are different states; only the second is a defect.
