---
disable-model-invocation: true
---

# `/sync_baseline` — Upgrade and Audit

Two forms of [`/sync_baseline`](../sync_baseline.md) that run from a baseline checkout rather than a consumer's own routine sync. Its Classification Rules and Verdicts apply here too.

**Upgrade (schema-1 consumer):** its own engine predates `migrate`, so run the engine from a baseline checkout and name the project:
`python3 <checkout>/template/.claude/tools/baseline_sync.py upgrade --project <consumer> --baseline-dir <checkout> --layers <adopted prefix> [--abbrev <ABBR>]`. Omitting `--layers` adopts all three layers; `--abbrev` records the project's abbreviations for the publish identity scan.
1. It refuses a schema-2 lock, an uncommitted `CLAUDE.md`, `settings.json`, `settings.project.json` or lock, and a `project_subsystems` adaptation contract that fails. Only `upgrade` and `migrate` accept a v1 lock; every other write refuses it.
2. It migrates the lock and replaces `CLAUDE.md`'s `BASELINE:core` region with layer imports. When no `settings.project.json` exists, it splits `settings.json` into the pulled `settings.base.json` and adopted layers' overlays plus a derived `settings.project.json` holding what those shared inputs do not provide, listing each shared entry the old file lacked. An existing project input leaves an unsplit `settings.json` row for triage unless it is already `composed`. Inspect the migration's split result. It then pulls, composes and prints `check --strict`. Other projects' `.claude/auto-memory/` files are manifest `sync: offer` rows: they stay unpulled and report `offered` (`/sync_baseline` **Check** step 4).
3. If it stops after migrating, finish from the consumer root with the checkout's engine: `pull --baseline-dir <checkout>`, then `compose`.
4. Commit the result, then run **Check** in the consumer: its triage clears the remaining judgment rows until `check --strict` exits 0.

**Audit (maintainer-side separation health) — run against a baseline checkout:**

This is the maintainer counterpart to `check`: `check` finds *consumer* drift; `audit` proves the *baseline itself* stays project-agnostic. Run it by hand after moving or adding template files.

1. **Mechanical pass.** From the baseline repo root, run `python3 audit_baseline.py --strict` from its `tools/` directory (`--json` for machine output). It gates on identity tokens across the whole tree (hashed per consumer in `identity_digests.json` in the baseline repository's `tools/` directory), secret and credential shapes, manifest⇄disk integrity, manifest staleness vs `gen_manifest.py`, layer tags, and layer closure (`template/.claude/tools/layer_closure.py` over the manifest). **Any ERROR blocks publication** — fix at the source (genericize, re-layer, or regenerate the manifest) and re-run to green.
2. **Judgment pass (what the script deliberately can't do).** The digests catch each consumer's listed content nouns, not a contributing game's taxonomy expressed in other words. A universal- or godot-layer file that bakes in content tables, folder paths or domain routing is still a leak. Sweep for it:
   - Build the noun list from the content domains of every project the baseline has absorbed code from (each contributor's `skills/game_vision` + `skills/project_subsystems` enumerate them), then `git grep -n -w -E "<noun1>|<noun2>|..." -- template/.claude/commands template/.claude/rules template/.claude/skills`. For each hit, decide *illustrative-generic* (a one-off example teaching a concept, fine to keep) or *baked-in config* (keyword→category tables, hardcoded `Source/<Subsystem>/` paths, content-domain routing tables). Move baked-in config to `adaptation.json` or a project-owned file.
   - Cross-check the README's **Known adaptation points** list: every file that legitimately carries source-project shape *and is meant to be adapted per project* must be listed there. An adaptation-shaped file missing from that list is the finding.
3. Report ERROR/WARN/judgment findings tiered; fix mechanical blockers in-place, propose genericizations for judgment-level leaks, and only then run `/sync_baseline` **Publish**.
