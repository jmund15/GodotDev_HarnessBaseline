# Source Trust Tiers

Doctrine for any claim about behavior **outside this repo** — engine, library, runtime, spec, tooling. Loaded by reference, not by path: every research or external-truth lens points here, and none carries a copy. A stack layer may add its own trusted sources and fetch mechanisms in a sibling file; its always-loaded layer doctrine names that file.

Claims about behavior **inside** this repo take the other axis — inference distance, not source authority: `claim_confidence.md`.

**Cite or gap.** Every external claim ends with its source tier, or it is recorded as a gap. There is no third state — an uncited external claim is an inference wearing a fact's clothes, and a plan built on one is fiction.

## Tiers

**P1 — citable alone.** First-party and authoritative: the thing itself, not an account of it.

- A version-pinned local docs cache of the thing itself, where the project keeps one. Read it directly — no fetch, no model, no truncation.
- `mcp__plugin_context7_context7__query-docs` against a resolved library id
- First-party source read from `raw.githubusercontent.com`, official changelogs, release notes, and spec text

**P2 — citable, must name the version.** Official but secondary: vendor-domain blog posts, maintainer conference talks, migration guides. The claim text states the version the answer is true for; without it a P2 citation is P3.

**P3 — never load-bearing alone.** Issue trackers, proposals, forums, Stack Overflow, third-party write-ups — a secondary account of the behavior rather than the behavior's owner. A P3 hit is a **pointer**, not an answer.

## Rules

1. **Never answer from memory.** This harness has no reliable built-in knowledge of the pinned versions in `reference/project_stack.md`. Fetch it. Could not fetch it → gap, not inference.
2. **Escalate every P3 to its owner.** Chase the P3 hit to the P1/P2 source that owns the behavior and cite that instead. No owning source exists → the absence IS the finding: emit `polarity: "unclear"` with a gap naming what would settle it, never `exists`.
3. **Pin the version in the claim text.** Use the engine and runtime versions pinned in `reference/project_stack.md`. A doc page for another major version answers a different question. A P1 URL that tracks `master`/`latest` is unversioned: the claim names the version the project actually runs and that the cited page is unversioned upstream, or it is P3.
4. **A partial reader cannot prove absence.** A digest layer that drops part of a page reports the gap as silence, and silence reads as absence. `read_web` now declares what it saw — its preflight block carries `mode=` and `raw=Nc, seen=Nc` per URL, plus a flag when retention is low, an interstitial was withheld, or extraction came back empty. **Read that ratio before recording any negative**, and treat a flagged URL as unread rather than empty. Confirm true absence only against bytes you fetched whole; the same rule binds any digest layer between you and the page.
5. **Citation-as-audit — check the quote against bytes, not against another summary.** Before consuming a returned claim set, verify two or three `evidence` quotes yourself by matching them against the fetched artifact. Re-fetching through the same digest layer that produced the claim audits nothing. Land on a summary of the thing rather than the thing, and the run failed at its one job — re-dispatch rather than report.
6. **Stopping criterion.** A run ends when every listed question is P1/P2-answered or recorded as a gap. Not sooner, not later; "went deeper than asked" and "missed the one detail that mattered" are the same defect.

## Claim shape

The claims schema carries no `tier` field, so tier rides in the fields it already has:

- `file` — the source cited: the URL fetched, or the local path when the source IS local (a
  version-pinned docs cache).
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

## Fetch order

`CLAUDE.core.md` §4 points here; this section owns the order and each tier's mechanism.

1. **A version-pinned local docs cache**, where the project keeps one.
2. **`scripts/fetch_source.sh`** — bytes to disk, free, quotable. Landed bytes are what make a quote checkable: `tools/verify_claims.py` grades them advisory-only and never rewrites a claim into a gap. GitHub file reads use raw.githubusercontent.com — browse/tree URLs have no raw form.
3. **`WebFetch`** — one URL.
4. **context7** — a resolved library id; needs the context7 plugin enabled.
5. **`read_web`** — multi-page synthesis only.
6. **`WebSearch`** — docs silent, or a specific engine bug.

Trust tiers and trusted anchors: §Tiers above (P1).
