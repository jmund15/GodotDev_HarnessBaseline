# Model evidence: probation and comparisons

Read before pinning a model the owner marked for its tier or one `for-role` does not list for it, and before running a same-task comparison (`orchestration` §5 points here).

A model is on probation for a tier when the owner marks it for that tier, or when it has neither a tier claim (its registry `roles`) nor a clean-rated dispatch at that tier in this project's metrics archive. Native dispatches infer the tier from the call or shape before using a written tier. Sidecar preflight derives it from `-G` through the profile row. Probation is computed, never a rating. `model_registry.py review-state <alias> <tier>` reads that tier's probation state.

Requested pins, runtime identity and capability evidence are different facts, and one result never promotes or demotes a model.

Same-task comparisons need an explicit request, named arms, frozen inputs and a finite budget; `/pin_ab` owns the common case, and a tool-profile pair runs only on strict sidecars. A mixed-model run is a comparison only under that contract.
