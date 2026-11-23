---
disable-model-invocation: true
---

# Red-Team Lenses

The question set `/architecture_brainstorm_redteam` puts in its `/review` brief. The brief names the seated lenses; the seat applies only those.

## Classification

Every finding takes `/review`'s findings format, and its heading opens with one class:

- **design-hole:** the design has one right answer it misses. The change line states the correction.
- **taste-fork:** the answer depends on the owner's vision or appetite (`skills/_brainstorm_shared/appetite_invariant.md`). The change line lists the viable options, each ending `wins when <condition>`, and recommends none. The critic presents, and never picks.
- **dead-end:** a design-hole with no fix inside the design's constraints, such as an invariant that cannot hold without breaking the framework boundary. The change line names the constraint to reframe.

The findings file ends with `## Coverage`: one line per seated lens, `<key>: <n> findings`. A lens with no findings still gets its line.

## Lenses

Each `interleave:` line names the `architecture_brainstorm` steps where the lens can fire in Mode B. The triggered lenses fire when the command's §2 seats them.

### rt-boundary: framework boundary and layering

interleave: Step 2 (a premise that leaks), 4, 5, 7

Does the design make a framework or shared library reference the project's own namespace, or move project logic into it? Cite the violated seam.

### rt-abstraction: a missed existing owner

interleave: Step 4, 5, 7 (needs a proposed type)

Does it invent a parallel type where an existing owner already covers the concern? Name that owner from the inventory the brief lists.

### rt-failuremode: the known-failure walk

interleave: Step 2 (a tentative pick), 4, 5, 7

Does it walk into a recorded failure mode: init timing, disposal and lifecycle, pooling reset scope, a magnitude used as a type discriminator, load order, an exit or enter hook that clobbers state? Cite the recorded case.

### rt-yagni-scope: speculation against stated direction

interleave: Step 2 (a speculative premise), 4, 5, 7

Is it built for an imagined need? A direction the owner stated is not speculation (`CLAUDE.core.md` §Core Principles: build for evolutions the owner names). Also flag a touched subsystem that serves another behaviour, and a seam with no owner.

### rt-testability: test-first feasibility

interleave: Step 4 (only when an approach names its test surface), 5, 7 (needs commitments)

Can each commitment be driven test-first? Logic takes named test cases. Behaviour that needs the running app takes a scene or end-to-end test, or an explicit subjective flag. A person's task on a rendered screen takes a row of the project's agent playtest command, and subjective covers taste only. A test can assert only a value the design states, so check each derived value (a default, a suggestion, a count, a label, which view opens) against `architecture_philosophy` §Behaviour per situation. These are design-holes: a task stated for first use only; a value with no stated facts, or no value in some situation the person meets; a value that ignores a fact its own surface shows; and an owner approval claimed for a state no approved artifact shows.

### rt-systemic: a neighbouring design (triggered)

Does this design conflict with, duplicate or quietly reshape a neighbouring committed design? Judge the adjacent designs the brief lists, and cite the conflicting pair of sections.

### rt-vision: product fit (triggered)

Does it serve the product as the vision owner the brief names describes it? A fit finding is usually a taste-fork: flag the misalignment, and never decide fit.
