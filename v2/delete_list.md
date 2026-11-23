# Delete list: test files dropped by TB3

Written by job TB3 (test conversion, doctrine-contract tests). Rule applied: a test file is dropped when its subject is already gone from `v2` or when it only polices prose. A test whose subject is a file still present on `v2` and classed delete is left for B16, which deletes it with its subject; those are listed in the TB3 return, not here. B16 appends its own rows below this table and writes the header count.

| File dropped (`template/.claude/tests/`) | Subject at base | Why dropped | Need or replacement |
|---|---|---|---|
| `test_instruction_quality_split.py` | the split layout of `instruction_quality` (`reference/` files, the harness-edit hook that named them) | the layout and its hook are gone (A9 moved the skill; V11 deletes `harness_edit_skill_reminder.py`); the asserts police the old file split | the skill's size is the re-attach cap in `skill_bodies`, checked by S7's budget test (`P:410`) |
| `test_rigor_doctrine.py` | `skills/orchestration/reference/rigor.md` and `rigor_stages.md` (deleted by A2) | the bar owner and stage table it cites are gone; its remaining cases police retired tier and verdict words (prose) and drive `harness_tests.py`'s proof corpus (V9) | the rigor bar is one line in the core doctrine (A1); no test polices its wording |
| `test_seat_outcome_parity.py` | `workflows/review_fanout.js` (V8, absent at base) and `tools/review_ledger.py`'s `_sidecar_seats` | one side of the parity it proves is deleted; the seat outcome rule lives in `/review`'s seat route now (Q6) | `/review` diff mode is covered by the seat launcher's own proofs (B1) |
| `test_check_learnings.py` | `check_learnings.sh`, the Stop hook's transcript scan (absent at base; no reference anywhere outside tests) | the autolearn scan is deleted with V11's `autolearn*`; the script is gone | none; the Stop hook no longer exists |

Manifest rows for these four files (`baseline.manifest.json`, `tools/layer_entries.json`) are repo-root v1 publish machinery; B16 removes them with the cutover delete list (Q11).
