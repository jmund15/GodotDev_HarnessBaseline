# Evidence Contract

The one contract every claim, finding or verdict an engine returns checks its evidence against. `tools/verify_claims.py` is the one executable implementation; `schemas/evidence.json` is the machine twin. A claim carries `evidence: [item, ...]`, one item per source.

## Items

Each item is exactly one of four kinds.

| Item | Checker semantics |
|---|---|
| `{file, quote}` | `file` is a repository-relative or absolute path, optionally `:N` or `:N-M`; `path` is read as `file`. VERIFIED when the normalized quote (in-order `...` gaps allowed, both sides case-folded, a leading `path:N:` or `N:` grep prefix stripped) occurs in the normalized file. UNVERIFIED when the file is readable and the quote is absent, or the path does not exist. UNCHECKED (`unreadable`) when the file exists but cannot be read, for example a dehydrated OneDrive vault file. The line range is reported (`rangeHit`), never decisive, because lines drift. |
| `{command, output}` | A read-only command from the allow-list, matched by full argv shape per subcommand and never by subcommand name alone: `git grep`, `ls-files`, `ls-tree`, `log`, `show`, `branch`, `check-ignore`, `cat-file`, `rev-parse`, `diff --stat\|--name-only`, `status --short`. Each subcommand has a positive option allow-list: every permitted flag is spelled in full, a grouped short option is split and each letter checked, and any unlisted `-` or `--` token (an abbreviated long option included) is refused, as is a git-level option before the subcommand. Parsed with `shlex`, run without a shell from the subject root under a timeout, refused on `\| ; & > < $ \``. VERIFIED when the command exits 0 (or 1, a no-match `git grep`) with empty stderr and the normalized `output` occurs in stdout, or when `output` is `""`, stdout is empty and every pathspec the command names matches at least one tracked path. An empty result over a pathspec that matches nothing, any other non-zero exit, or any stderr is UNCHECKED (`vacuous-scope`). UNVERIFIED otherwise. UNCHECKED (`not-runnable`) outside the allow-list, UNCHECKED (`timeout`) past the timeout. This leg covers tool output, git history (`git show <rev>:<path>`, `git log`), counts (`git grep -c`) and `absent` claims. A `git show <rev>:<path>` item names a fixed revision (a SHA), never `HEAD` or a branch: the ref moves once the commit lands, and a later check reads the new text. Any other rev is UNCHECKED (`moving-ref`). |
| `{owner, question?, task?}` | The owner's verbatim words. VERIFIED when an owner turn of the session transcript holds them (`_owner_text.owner_turns`), or, when `task` names a task record, when a row of that record holds them and re-resolves now (`task_record._condition_row` status `fresh`: its `message_id` turn in its stored transcript still holds the quote, and `delegate` is false). A `DELEGATE`, `MISSING` or `UNRESOLVED` row is UNCHECKED. An owner turn includes the owner's answer to an `AskUserQuestion` call. The picked label verifies only when `question` holds the question that call asked, matched against the same answer. A bare label is UNVERIFIED `answer-without-question`, and the assistant's question alone is never the owner's words. A runtime envelope such as `<task-notification>` is not an owner turn. UNCHECKED when neither the transcript nor the named record can be read; UNVERIFIED when the transcript is readable and no turn holds the words. |
| `{url, quote, artifact?}` | External. A fetched `artifact` (from the `fetch_source.sh` manifest) is checked like a file. Without one the item is UNCHECKED (`external`). |

## Claim verdicts

VERIFIED when every item is VERIFIED. UNVERIFIED when any item is UNVERIFIED. UNCHECKED otherwise, including a claim with no items, a claim whose only evidence is external, and polarity `unclear`. Only VERIFIED keeps `confidence: verified`.

UNCHECKED (formerly `NOT-APPLICABLE`) is counted and shown, never folded into a pass, and a claim with any UNCHECKED item is never VERIFIED.

## Load-bearing premises

A claim is load-bearing when it is a premise of a doctrine commit or a `task_record.py` decision. A doctrine path is a `.md` file admitted by `hooks/harness_edit_skill_reminder.py`'s `is_harness_file`; `is_doctrine_path` is its shared markdown predicate. `auto-memory/**` and `plans/**` are excluded, along with the predicate's other non-guidance paths. Code edits are excluded; their proofs gate them. An owner-facing claim is checked when it drives one of those edits or decisions, not when it is spoken.

A `decision` doctrine commit owes a fenced `premises` block when its change rests on a claim (a cause, a count or a ruling). The block has one JSON object per line: `{"claim": "...", "evidence": [items]}`. State the load-bearing claims using the Items contract above. An owner-directed edit uses an `{owner}` item; executing another session's ruling uses `{owner, task}` so the quote resolves against the task record's checked owner turn.

````
fix(harness): apply the requested edit

```premises
{"claim": "the owner requested this edit", "evidence": [{"owner": "<verbatim owner quote>", "task": "<task id>"}]}
```
````

A commit-time check verifies a present block through `verify_claims.py` and lists each UNVERIFIED item. An absent block asks whether the decision rests on a claim; it is not judged at push.

## Forbidden shapes

- Several sources joined in one string: one item per source.
- A re-rendered structure such as a Python repr of JSON: quote the file's raw bytes.
- A `memory:` prefix: cite the memory file's path.
- A prose `verification` sentence for an `absent` claim: use a `{command, output: ""}` item.

## Legacy adapter

`verify_claims.items_of` converts a string `evidence` plus `file` into items: it splits the string into fragments and checks each; a fragment it cannot locate is UNCHECKED. `file: null` gives UNCHECKED (`no-file`). Splitting cannot create a false pass, because every fragment must match.
