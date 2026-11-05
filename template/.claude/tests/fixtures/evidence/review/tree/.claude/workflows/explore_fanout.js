  '`evidence` — REQUIRED for `exists`/`partial`: VERBATIM tool output (a grep line, a signature, a quoted sentence), not your summary. Paraphrase survives fabrication; raw output rarely does. A single quoted line is a strong claim; a long quote is a weaker one.',

  '`verification` — REQUIRED for `absent`: the command that PROVES absence, not a search that came back empty. `ls`/`git ls-tree` on the directory, `git branch -a` + `git grep <pat> <branch>` for unmerged work, `git check-ignore -v <path>` for a gitignored dir.',

  '`bearing` — `premise-contradiction` (the topic assumes something the code refutes — the highest-value claim you can return; surface it even when it makes the rest of your sweep moot) · `reuse-candidate` (something already owns this concern) · `constraint` (a rule/invariant/gotcha the plan must respect) · `blast-radius` (a consumer a change would break) · `context` (orienting only — keep these few).',
