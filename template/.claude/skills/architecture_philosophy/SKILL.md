---
name: Architecture Philosophy
description: >-
  Use when designing a system or feature, refactoring, or reviewing architecture.
  SKIP mechanical edits and where a file goes (structure_rules.md).
---

# Architecture Philosophy

Design-time rules for any codebase. `structure_rules.md` beside this file answers where a file lives. `reference/rules/design_litmus.md` holds the questions asked at the moment a type, setting, parameter or helper is introduced. A project's stack patterns are project files its `CLAUDE.md` names.

## Behaviour per situation

**Rule:** A design, plan or brief states each task as who, situation and goal, and states the behaviour in every situation the person meets over the object's life, not at first use only. The situations are open; common ones are first time, again, partly done, after an edit, empty, large and failure.

- **Every derived value names its facts.** For each derived value (a default, a suggestion, a count, a label, which view opens), state the facts it reads and its value in each situation. A value that ignores a fact its own surface shows is a defect.
- **A check asserts the value** in each situation, never only that its control appears.
- **An approval covers what it showed.** Behaviour in a state the approved artifact did not show is the agent's design, judged by this rule.
- *Litmus:* for each derived value, find a situation where a fact it does not read changes the right answer. One exists: the value is wrong or unstated.
- *Concrete:* a duration suggested from the estimate alone reads wrong once part of the work is booked.

## Initialization and state ownership

### One initialization path

**Rule:** Every component in one scope initializes through one framework path: producers publish into a shared context (a blackboard, a DI container, a service registry) and consumers resolve from it; neither names the other. A bespoke initialization signature is an exception with a stated condition, never a second default.

- *Resolve, then wire:* resolution and cross-component subscription are separate phases. Subscribe only after every sibling resolved; wiring during resolution is an order race.
- *Hard or soft:* a component fails loud at initialization, once, when a dependency it cannot serve without is missing. A dependency that gates only part of its behaviour is soft: resolve it optionally and degrade. *Litmus:* with this missing, does the component still do anything a user expects? No: hard.
- *Idempotent:* initialization may run twice; tear down before re-resolving.
- *Late data:* most late availability is an install-order bug. Fix the order first; only genuinely late data (another installer, an external or async source) takes a retry, and every retry is bounded and logs at Warning when it gives up.

### Component contract

1. **Divergence by composition.** Variation between entities is which components they compose plus authored data, never rewiring of shared blocks.
2. **Dependencies run one way.** The dependent knows its dependency, never the reverse.
3. **Every dependency is visible:** an explicit slot the author assigns, or auto-resolution that warns at authoring time and fails loud at initialization. Resolution survives regrouping; never walk bare parents or siblings.
4. **The contract is more than the members.** Which phase resolves what, hard against soft dependencies, idempotency and teardown are part of the interface; state them where the author reads the component (`reference/rules/design_litmus.md` #7).

Do not bypass the shared context with a direct reference, even for one consumer: a direct push beside a published key wires the data through two channels.

### Narrowest layer

**Rule:** A consumer reads the narrowest layer that answers its question: the raw substrate (perception, physics, stats) for raw facts, the deriving component for its derived answer. A derived layer is an optional capability over the substrate, never a funnel every consumer must pass.

1. The substrate stays usable without the derived layer; if removing the deriving component breaks substrate consumers, the layers are fused.
2. Consumers resolve the deriving component and call its typed members, never a value key it publishes. One shared value key makes two different questions indistinguishable.

- *Litmus:* name each consumer's question. Two consumers asking different questions of one surface: split it. A consumer needing only substrate facts that reads the derived layer: narrow it.
- *Concrete:* steering asks "what is near me" and reads perception; an attack asks "what have I committed to" and reads the target provider. Both reading one "current target" key is the fused shape.

### Owner-bound state

**Rule:** State whose owner's lifetime bounds it lives on the owner, not as a flag in the shared context; the shared context holds cross-cutting data with no single owner. A shared flag is a public field whose setter and clearer are paired by hand, so a missing clearer is a silent desync. Time-windowed metadata that matches a state's lifetime lives on that state: entry sets it, exit clears it. Apply this before §Narrowest layer: only owner-bound answers reach that rule.

### Capability query, polymorphism or central dispatch

When code branches on subtypes to get a shared capability, pick by the else branch:

- **Capability query** (`x is ICapability cap`): the capability is optional, the consumer decoupled, and absence a uniform no-op. A new participating subtype changes zero consumers.
- **Polymorphic member:** the behaviour is intrinsic and every variant provides it. The tell is an else branch with real alternative behaviour.
- **Central dispatch** (a switch in the consumer): the variants are data a central interpreter acts on, such as effect kinds applied by one system.

## Extension shapes

- **Default value.** `instanceOverride ?? projectDefault`, only where a real project-wide default exists. A framework never reaches into the consuming project: it exposes a seam that project startup fills, with a reset path for test isolation.
- **Registry lookup.** Paired `TryGet(key, out value)` and `Get(key)`: `Get` logs and throws on a missing key, `TryGet` returns false. Build lazily on first access; on duplicate keys warn and keep the first.
- **Shared configuration.** When two or more consumers need the same options, extract them into one authored configuration type, testable alone.
- **Strategy hierarchy.** Behaviour that varies by authored configuration is an abstract base with concrete subclasses authored as data; a slot needing several at once takes a composite. A `switch (type)` over behaviour is usually a strategy family not yet written.
- **Orthogonal axis: compose, do not subclass.** Before subclassing, name the axis the base already varies on. Behaviour on a different axis is an optional composable slot on the base. *Litmus:* could a sibling lacking the base's defining feature want it? Would a new combination need a new leaf class? Either yes: compose, and unwind fused rungs before extending.
- **Closed abstraction: refactor, do not fork.** When a feature cannot reuse the canonical mechanism because it is closed (a fixed enum, a fixed pipeline), make that mechanism extensible rather than building a parallel system. If the abstraction is not meant to be canonical, a parallel type may be right. A large refactor ships through its own design, and the feature waits on a stable seam.
- **Per-instance state off shared config.** A shared authored asset never caches per-instance mutable state. Prefer `CreateState()` returning a value the consumer owns and passes back (`Tick(state, delta)`); use a runner object the consumer owns only when it needs runtime-managed features (timers, events).

## Authored data is an API

- **Derive, don't duplicate.** Each authored value has one home; a second surface derives it. *Litmus:* changed in one place and shipped, what still reads the old value?
- **Every visible setting is read** in every context an author can reach it. An inert setting is a defect: wire it, narrow its type per context, or delete it.
- **One knob, one axis.** A setting's name describes everything that changes when it flips. A bool that selects two concerns is split, or becomes an enum naming the fusion; a bool that selects behaviour beside a strategy family is that family's slot.
- **A required dependency fails loud once,** at load or initialization, never as a warning on every use. A global instance decides its owner (tree-owned, singleton, lazily created) at design time.

## The Deletion Test

**Rule:** Before adding a class, component, subclass or helper, ask: if it were deleted and its body inlined at every caller, what would scatter?

- **Deep:** each caller would re-implement real logic and make its own decisions. Keep it.
- **Shallow:** each caller absorbs a line or two. Inline it, or deepen it: move decisions inside and narrow the parameters (a context object instead of six arguments).
- *Heuristic:* an interface that lists every parameter its implementation needs is shallow. A `*Helper`, `*Utils` or `*Manager` with one or two forwarding methods usually is.
- When debugging finds no correct seam for a test, the area is usually a chain of shallow modules; use this vocabulary in the worklog `arch` item.

## Choosing a data shape

| | Enum | Constant keys | Authored data file |
|---|---|---|---|
| Use for | finite logic states, directions, settings levels | decoupled lookups, shared-context keys | extensible records and categories |
| Avoid for | lists of content | internal state logic | simple boolean state |
