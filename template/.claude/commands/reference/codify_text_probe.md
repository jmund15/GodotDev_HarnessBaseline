---
disable-model-invocation: true
---

# /codify — the counterfactual reader probe

Read at `/codify` Step 7 for a text fix or rule-removal candidate. Use fresh readers in each arm.

## Shared controls

- State the task's goal, never the decision under test. Score the action taken (the launch command, edit or tool call) over an answer about the decision. A probe that names the decision tests competence; the live failure is a decision made as a side step of another goal.
- Hand readers the production access path (a file to open, not its text inlined) and log what they opened, so "not read" and "read and misapplied" stay separate. Bound the setup to what the real decision reads: the rule file by path plus the files that step opens, with a turn bound, never an open source tree.
- Change one factor per arm. Score both against an independent action oracle neither arm produced (a hand-labeled sample, a test the arms never see). A reference run's output measures agreement with that run, not quality.

## Text-fix mode

Fresh readers of the failing model class run the old text and the new text.

- The old-text arm must reproduce the failure first. An arm that does not fail means the probe is too clean, and its pass proves nothing.
- Stage the readers: run the old arm first, and add a second old reader before calling one clean pass too clean; spend nothing on the new arm until the old arm fails. Then run two readers per arm, the smallest count that can show a split, and a third only in an arm that splits. Report the spread.

## Rule-removal ablation

Use this mode for flagged model rules and a sample of thin-basis rules. Record each candidate's path, exact rule span and marker, selection reason, task cases, and scored removal observation (`removeWhen` or `reverses-if`) before dispatch. Choose cases that exercise the weakness the rule compensates; record the sample's selection so a clean result cannot imply every thin-basis rule is safe to remove.

- For a model-change flag, use the current registry driver alias and resolve its version before pinning; do not probe the replaced model. For a thin-basis candidate, use the model class that receives the rule. Record the pin and registry revision.
- The retained arm reads the old text. The removed arm reads the same files with only the candidate rule and its marker removed. Keep the task, other guidance, access paths, model and effort equal. Strip the candidate from every injected copy in the removed arm; a surviving copy is not an ablation. Keep the file variants isolated from production and from each other.
- Ablation does not require the retained arm to fail. Run both arms even when retained succeeds. Use text-fix mode's per-arm reader count and split follow-up. Report actions, opened paths, oracle scores and spread by case and arm.
- When both arms succeed with no scored difference and meet the recorded removal observation, delete the rule and marker through `/codify`. State the tested model and task scope; a small clean sample does not establish universal equivalence. Equal failures, unopened rule files or incomplete runs are not removal evidence. If removal worsens the action, keep or revise the rule and record the failure that reverses deletion.
