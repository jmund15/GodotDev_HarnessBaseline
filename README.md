# Harness Baseline — the shared Claude Code harness

This repo is the one upstream for the harness files every project shares. `template/.claude/` holds them; a project
keeps the same bytes under its own `.claude/`. There are no layers and no substitutions: every project gets the whole
shared set byte for byte, and each project's own values live in its project-owned files.

## Shared and project-owned

| Shared (this repo, `template/.claude/`) | Project-owned (each repo, never synced) |
|---|---|
| `CLAUDE.core.md`, the floor hooks (`hooks/pre_bash.py` and the guards it runs), kept hooks, tools, commands, skills, workflows, `guards/rails.md`, `seats.json` and the seat launcher, and their tests under `tests/` | `CLAUDE.md` (it `@`-imports `CLAUDE.core.md`), `settings.json`, `skills/project_subsystems/adaptation.json`, `auto-memory/`, and the project's own commands, skills, hooks and tests |

A shared file reads a project value from `adaptation.json` through `tools/adaptation.py`
(`python3 .claude/tools/adaptation.py get project_name`); the key list and each key's readers are in that tool's
`DEFAULTS`. A project file can add to a shared floor rule, never weaken it.

## Sync: `tools/harness_sync.py`

Run from the project root. `<shared>` is a local checkout of this repo.

```bash
# Adopt, or update to a newer shared commit
python3 <shared>/template/.claude/tools/harness_sync.py pull <ref> --source <shared> --project .

# What differs from the last pull, and whether settings.json registers the floor hook
python3 .claude/tools/harness_sync.py status

# Send hand-edited shared files upstream as a pull request (gh must be logged in)
python3 .claude/tools/harness_sync.py push --source <shared> [--branch NAME] [--dry-run]
```

- `pull` writes `.claude/harness.manifest.json`: the pulled commit, each shared file's sha256 and the floor hook
  path. It copies raw blobs, so line endings never change. A shared file the project edited since the last pull
  refuses the whole pull and writes nothing. A file the shared repo deleted is deleted from the project unless it
  was edited.
- `status` lists `edited` and `missing` shared files and exits 1 when a file is missing or `settings.json` does not
  register `.claude/hooks/pre_bash.py`.
- `push` commits each edited shared file onto a branch cut from the manifest's commit and opens the pull request.
  It carries edits only: a new shared file is added in this repo directly.

A repo never forks a shared file. When a shared file needs a change, edit it, push it, and pull the merged result.

A new project also writes its own `CLAUDE.md` (with `@CLAUDE.core.md`), its `settings.json` (registering the floor
hook) and its `adaptation.json`.

## CI and tests

`.github/workflows/baseline.yml` runs one job, `baseline`, the status context the branch ruleset requires:

- `python tools/ci_totals.py <previous commit>` prints code, test and prose totals and each one's change since the
  previous run.
- `python -m pytest -n auto -q --durations=20 tests template/.claude/tests` runs every shared test, slowest 20
  listed. `tests/test_context_budget.py` fails a push that takes the always-loaded text past 15 KB or the
  model-visible skill and command descriptions past 6 KB.

Run the same pytest line locally before a push. A harness change runs its unit's test file
(`python -m pytest template/.claude/tests/<file> -q`).

## `v2/`

`v2/` holds the cutover's working records: the carried/dropped table (`v2/carried_dropped.md` and its per-job
fragments), the delete list and the drafts of each project's `CLAUDE.md`. It sits outside `template/`, so `pull`
never copies it.
