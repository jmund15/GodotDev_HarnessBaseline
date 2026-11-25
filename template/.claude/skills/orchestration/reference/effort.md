# Effort

Read from `orchestration` §5 *Effort* before setting an effort by hand, or when a job under-resolves.

A check-scored job's effort is a lookup: `model_registry.effort_for` reads its `spec` (with its `kind`) against the row's ladder cell, and every route fills it and caps an explicit effort above it. Only an owner-named pin (`pick: owner`) or a measurement run keeps a higher effort; a `justification` is recorded for metrics and changes no effort.

An engine's own checked verify and merge steps run on `args.checker`; when the call names none, the guard fills the cheapest verify-tier row by ladder `cost` on the session's transport, at its lowest effort level. Pin `args.checker` yourself when the step needs more. Inspect a committed Workflow script's live pins before reuse: it is not automatically Anthropic-only.

No check scores a review seat, so no tool computes its effort: set it in the row's review range from what the seat must read (the subject's size, coupling and depth), and never bump a whole panel. A later round's seat sets effort from what its changed units can hide; a first round's raise never carries over.

When a job under-resolves, find why first: a brief or evidence gap is fixed in the brief, effort starvation shows as `effort_fit: under` ratings that move the cell on the owner's yes, and a capability limit changes the tier.
