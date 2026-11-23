# Choosing what to test, and curating a suite

Read before writing a suite and when reviewing or curating one.

## Building block or composed instance

Ask first: is the subject a reusable building block, or one composed instance of blocks?

- **Building blocks** (components, services, strategies, data types with behaviour) get unit, integration and end-to-end coverage. Coverage here protects every instance built from them.
- **Composed instances** (one entity, one page, one configured record) get one parameterized wiring suite over every instance, discovered rather than listed, plus a few representative end-to-end tests. No suite per instance.
- **Data-integrity checks are per schema, never per instance.** A project-wide convention is checked once, over the discovered set.
- *Litmus:* if this test failed, would the defect be in the block or in one instance's wiring? Block: test the block. Wiring: the wiring suite owns it.

## Levels

A new or complex system needs all three: **unit** (one component), **integration** (components together), **end-to-end** (the full path a user takes). Passing unit tests do not show the system works; a system that crosses domains needs the end-to-end test most.

**The primary observable behaviour.** Each new system a user can observe has at least one integration or end-to-end test asserting what a user would notice if it broke: a spawned instance appears, a saved record shows on reload. One per system, not per behaviour. Unit tests at a boundary miss lifecycle failures (initialization order, event wiring, timing); this test is the last line against them. Unit depth is a different axis and never waives it.

Prefer asserting the observable outcome over an internal count or flag.

## Tests that protect nothing

- **Documentation-only.** Every assertion must consume a value production code produced. An assertion built only from local literals is documentation, however real the setup around it.
- **Constant mirror.** Asserting a field equals its default breaks on every intended change and catches no bug. Replace it with a test of the default's consequence; never keep both.
- **Compiler- or storage-only.** Each shape below protects nothing, except in its carve-out, where it becomes a rewrite onto the consumer, not a delete:

| Shape | Keep (rewrite) when |
|---|---|
| A type or interface check | the interface has no consumer that would fail without it |
| An enum's numeric value | the enum is stored by value, so renumbering remaps saved data |
| A constructor stores a field | the constructor validates, defaults or transforms |
| A property set and get | the setter has side effects, or production code branches on the value |

Before deleting a test as redundant, find its successor (`git grep -n -F <symbol>`). An assumed successor that does not exist turns a cleanup into lost coverage.

## Generative domains

For distributional behaviour (procedural generation, spawning, loot, scheduling under random input), prefer one measurement test over many example pins: N seeds through the real entry point, asserting rates or bounds set from the first observed run with headroom, and a failure message that names the dominant cause.

## Curation

Keep every building-block behaviour test. A change that touches a suite retires that suite's redundant, documentation-only and per-instance duplicate cases in the same change. Delete a test when its feature is gone, a block-level test covers it, or it checks implementation instead of behaviour. Every test is maintenance surface and run time.

## Before handing a manual check list to the owner

Screen each item; such lists mix taste with automatable behaviour. Wiring (a button fires its handler), leaks on close, focus after opening, and "the app boots without errors" are automatable: write the test or the smoke run. Only taste stays manual: look, feel, timing, fit across screen sizes.
