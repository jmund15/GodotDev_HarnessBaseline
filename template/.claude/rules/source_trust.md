# Source Trust Tiers

Doctrine for any claim about behavior outside this project (an engine or runtime
internal, a library, a general technique cited in a deliverable). Loaded by reference
from orchestration/research skills, not duplicated there.

**Cite or gap.** Every external claim ends with its source tier, or it is recorded as a
gap. There is no third state — an uncited external claim is an inference wearing a fact's
clothes, and a deliverable built on one is fiction.

## Tiers

**P1 — citable alone.** First-party and authoritative: the thing itself, not an account
of it. The runtime's own class reference, a library's own README/source, official docs.

**P2 — citable, must name the version.** Official but secondary: vendor-domain blog
posts, maintainer talks, migration guides. The claim text states the version it's true
for; without it a P2 citation is P3.

**P3 — never load-bearing alone.** Issue trackers, forums, Stack Overflow, third-party
write-ups — a secondary account, not the behavior's owner. A P3 hit is a pointer, not an
answer.

## Rules

1. **Never answer from memory.** Fetch it. Could not fetch it → gap, not inference.
2. **Escalate every P3 to its owner.** Chase a P3 hit to the P1/P2 source that owns the
   behavior and cite that instead. No owning source exists → the absence IS the finding —
   flag it as unclear with a note on what would settle it, never state it as fact.
3. **Pin the version in the claim text** when the claim is version-sensitive.
4. **Citation-as-audit.** Before trusting a returned claim, follow the citation yourself.

## Claim shape
- `file` — the URL fetched.
- `evidence` — the VERBATIM sentence from that page. A paraphrase is not a citation.
- `claim` — ends with the tier tag, `[P1]` / `[P2]` / `[P3]`.
- A claim with no URL in `file` is unverified, whatever its tier tag says.
