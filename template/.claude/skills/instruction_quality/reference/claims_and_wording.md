# Reference content and word choice — §17–§18

Read in full when the edit changes `.claude/reference/` prose, asserts a fact about a tool, file or schema, or introduces a new term. `SKILL.md` §1–§6b apply to it too.

## Reference-content

### 17. Claim freshness
Verify the claims the reviewed change's decision rests on against current code, configuration and live tool schemas. Bind each such claim of tool behavior to a case that exercises it; trace the implementing branch and report the checker's typed outcome (`python3 .claude/tools/extract_numbers.py check --claims behavior --paths <surfaces> --json`). Distinguish recorded observations from current facts and versions. Report sampling and unchecked scope; do not turn file existence or zero errors into broad correctness. A comment states only a fact its own file can keep true: `.claude/reference/` prose is read as current, so it never asserts world state another artifact owns, and a negative names the artifact that would falsify it. Multi-file audits return evidence, not unsupported verdicts.

## Word choice

### 18. Leading words, and the negation trap
Prefer established technical terms to new labels: rename a coinage to a known term before documenting it, and define a necessary new term once, in its one owner (§3). Use plain verbs such as “use” and “help.” Remove filler and vague success claims. Named no-ops, replaced with the concrete claim: additionally, crucial, delve, enhance, fostering, interplay, intricate, landscape, pivotal, showcase, testament, underscore, vibrant. Pair prohibitions with the correct action.
