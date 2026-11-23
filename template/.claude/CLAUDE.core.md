## A Note from the Owner
I don't want systems convoluted or difficult to navigate, quite the opposite. I like ambitious ideas, but simple systems, and software that feels obvious. Do not preserve unneeded complexity just because it exists. Understand the real constraint, then fight for the smallest model that makes the correct behavior unsurprising. Channel "measure twice, cut once" - this is a LARGE, expansive codebase with many systems that interact with each other. However they are all and should all be built with extensibility, reusability, and interoperability in mind. This means when adding a new surface or feature, you may be able to find components or subsystems that have already done the work for you. In the case of large new feature integrations or refactors, the goal is emergence and reactive, not hard-coded.

Finally, one of the most important aspects of our work together — communication. Every word we say together reinforces our clear, concise, actionable communication. I read the last thing you write first, and much of what you write is read later — by me after a run, or by a delegate with none of this context — so the answer opens, the next action closes, and the close stands alone. Use plain, specific language. State each fact once, do not repeat yourself in the same prompt. Optimize for clarity and engineering value, not quotability.

Our main goal is to streamline communication and maximize efficiency. So in larger-scope sessions, use **Reference Points**:

Use numbered lists and markdown headings when they improve navigation.
When presenting three or more findings, decisions, options, risks, questions, or actions assign every one a short code.
Use D1, D2, DN for decisions.
Use O1, ... for options.
Use F1, ... for findings.
Use R1, ... for risks.
Use Q1, ... for questions.
Use A1, ... for actions.
Invent new references for sections we don't have.
Preserve the same codes throughout the conversation.
Do not create codes for short simple answers.

## Core Principles
- The goal is accepted results at the least time and cost, quality held fixed; rework costs most. A step, guard, rule or wider scope stays only while it catches more than it costs; this settles conflicts.
- Verify decisive premises before acting; thin evidence informs, never decides. A delegate's claim is a lead until you read its source.
- An unexpected result is a defect: trace it to the first link the harness owns, then fix it or name the blocker. A harness mechanism blocking correct work is the defect; fix it at its owner.
- Preserve peer work: run `git status --short <file>` before calling a defect peer-owned; clean means yours. Message a peer only while its cache is warm (a cold one re-reads everything) and only to change its next action.
- One rule, one owner (`instruction_quality` §3). A fix covers its class with the smallest change removing the shared cause: correct or delete what failed before adding a rule, tool or stage; a hook only for a loss case. Build for evolutions the owner names, not invented ones.
- Do surfaced work now; defer only for a reason `/worklog` names. Comments default to none.
- **The owning tool prints its call.** A tool owning an outward action (publish, deploy, land) prints its full invocation, so a successor re-runs it from that output, not prose.
- **A gate names what it does not exercise.** A check stubbing a live boundary (database, browser, network) says so, since a stubbed pass reads as coverage; the first step crossing it confirms it live once.
- **Project doctrine never lowers a shared floor.** A project rule may add to shared loss, quality and brief rules, never weaken them; the shared rule wins, and `/review` checks.

## Fixes and Redesigns
A fix to a file in the top 5% by fix commits since its last redesign commit, or to a shared contract (floor module, shared command, tool or skill), is planned as a redesign through `/plan` and `/review`: patching much-fixed files is how the harness grew. `git log --format=@%s --name-only | awk '/^@/{s=$0;next}NF&&!d[$0]{if(tolower(s)~"redesign")d[$0]=1;else if(s~"^@fix")c[$0]++}END{for(f in c)print c[f],f}' | sort -rn | awk '{a[NR]=$0}END{for(i=1;i<=(NR+19)/20;i++)print a[i]}'` lists them. A fix restoring a broken floor (a guard denying every call, a red main) lands first; its plan follows. A repeated fix to one decision across files, or an owner's "never again", runs `/codify`.

## Memory and Proofs
Memory lives in `.claude/auto-memory/`, not the runtime's store. Search it by concept on a domain change or surprising result; retire what the result disproves. New learning is cold: a hot pointer is paid every call. A harness change runs its unit's test file (`python -m pytest .claude/tests/<file> -q`); CI runs the suite per push; `rules/harness_tooling.md` owns proofs.

## Git
- Commit your own changes before a task ends, unasked, in categorical path-scoped commits, so nothing mixes into the next session's diff; messages go in `.claude/scratch/` for `git commit -F`. Never leave a shared index staged. A PR only for a review the owner asked for.
- Push main or a land branch only when the owner asked, invoked `/commit_push` or `/close`, or armed `/overnight` with push; that authority survives compaction. Push a land branch once per line of work: each land starts a main check.
- Never fork a shared harness file: upstream the change (`tools/harness_sync.py push`) or move it to a project file.

## Shell Discipline
- **Bash calls:** Use `git -C <path>`. Python with nested quotes, or a command past 8191 characters (the Bash tool cuts it), goes in a file you run. Run index writes (`add`, `rm`, `reset`, `stash`) in their own call: a denied compound runs none of its parts.
- **Destructive operations:** Name each target in the question or reply by the name its tool binds (a process by pid and launch label), never a pointer to a list. Get the owner's words before `git reset --hard`, `clean -f`, a checkout or restore that discards edits git holds nowhere else outside your own worktrees, force-push, `branch -D` of a branch no other ref holds outside `scratch/*`, unpreserved `stash drop` or `stash clear`, `reflog expire`, `gc --prune`, `filter-*`, a recursive delete outside caches, temp dirs, clean scratch clones, scratch folders holding no git repository, folders this session made with a flagless mkdir holding no git repository, your own worktrees and orphaned worktree directories holding only files git has and regenerable caches, drive formatting, or `TaskStop` on a dispatched agent or workflow. Ownership means this session provably created the worktree; only then may every ignored file go with it. Any worktree's regenerable caches, project-listed outputs and scratch output holding no repository or session-id-named folder go regardless. Recursive worktree deletion also requires clean retirement, commits pushed or landed on the default branch (submodules: pushed), and no other live session working in it. A guard denies only a loss it observes and reads no reply; the auto-mode classifier judges whether the owner asked.
- **Processes:** Stop a process only through `python3 .claude/tools/reap.py`.
- **Long jobs:** A wake re-reads the whole context, so wake only for a result the next step needs. Start a known-long job (build, gate, install, seat fan-out) with `run_in_background`. Never raise a timeout for a job you cannot time.

## 9. Tool Routing
Read known targets directly, in parallel. A chain of reads goes to one script, or one `/explore` or `/delegate` scout. Bound walks with git-aware search.

## Preferences
- **Working:** Keep stated requirements through delegation and resume. Check an objection against evidence; no performative agreement. Distress means the path is wrong: take the one the owner's reason supports. Never hand the owner a mechanical step. End a turn only when every step left waits on a wake or the owner.
- **Owner's words** reach as far as their reason. An example shows a principle, never what gets built. Say every, only or exactly only where the owner did.
- **Asking the owner:** one packet: options, facts checked this turn, a recommendation where evidence allows.
