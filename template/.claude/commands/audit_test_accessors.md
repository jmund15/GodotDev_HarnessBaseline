---
disable-model-invocation: true
---

Audit production code for test-only methods that should be wrapped in `#if TOOLS` guards.
These are `internal` methods added solely so tests can set `[Export]` properties or simulate signals on production types.
Without `#if TOOLS`, they ship in export builds and risk accidental production use.

## Constraints
- **Scope**: Production `.cs` files only — skip `Tests/`, `Jmodot/`, `addons/`, `script_templates/`.
- **Read-only by default**: Report findings. Do NOT apply fixes until user approves.
- **No behavioral changes**: Wrapping in `#if TOOLS` does not change runtime behavior (TOOLS is defined in editor + test builds, stripped from exports).

## Procedure

### 1. SCAN — Find all test-only internal methods in production code

Search non-test `.cs` files for these patterns:

**Pattern A — Explicit test naming** (highest confidence):
```
internal .* _Test\w+\(          # _TestSimulateHit, _TestSetReactiveComponent
internal .* SetTestValues?\(    # SetTestValues, SetTestValue
internal .* SetTestCurve\(      # SetTestCurve
internal .* SetTestModifiers\(  # SetTestModifiers
internal .* SetTestRotation\w+\( # SetTestRotationAxis
internal .* ResetForTesting\(   # ResetForTesting
internal .* ForTest\w*\(        # ForTestOnly, ForTesting
```

**Pattern B — Property setters** (high confidence — must be `internal void SetXxx(type value)`):
```
internal void Set[A-Z]\w+\(     # SetDamageMultiplier, SetAttackerOutcome
```
Exclude if the method body does more than assign a single property (i.e., has logic beyond `=> Prop = value;`).

**Pattern C — Simulation helpers** (high confidence):
```
internal void Simulate\w+\(     # SimulateIngredientEntered
```

### 2. CLASSIFY — Separate guarded from unguarded

For each match, check if it's already inside a `#if TOOLS` / `#endif` block.

Produce two lists:
- **GUARDED** (already wrapped) — report count only, no action needed.
- **UNGUARDED** (missing `#if TOOLS`) — these are the findings.

### 3. PRODUCTION CALLER CHECK — The critical audit

For every UNGUARDED method, grep the **entire codebase excluding Tests/** for call sites.

Classify results:
- **SAFE**: Only called from `Tests/` — needs `#if TOOLS` guard but no production breakage.
- **DANGEROUS**: Called from production code — this is a real bug. The production caller must be refactored BEFORE the method can be guarded.

**If ANY dangerous callers are found, STOP and report them prominently before proceeding.**

### 4. REPORT — Present findings

Format as a markdown table per pattern category:

| File | Method | Pattern | Status | Callers |
|------|--------|---------|--------|---------|
| `Combat/Reaction.cs:46` | `SetAttackerMatcher` | B | UNGUARDED | Tests only |

Summary statistics:
- Total test-only methods found
- Already guarded count
- Needs guarding count
- Dangerous production callers count (should be 0)

### 5. FIX — Apply `#if TOOLS` guards (after user approval)

For each unguarded file, wrap the test-only methods:

**Single method:**
```csharp
#if TOOLS
    internal void SetFoo(Bar value) => Foo = value;
#endif
```

**Multiple adjacent methods (same file):**
```csharp
#if TOOLS
    internal void SetFoo(Bar value) => Foo = value;
    internal void SetBar(Baz value) => Bar = value;
#endif
```

**Rules:**
- Group adjacent test methods under a single `#if TOOLS` / `#endif` pair.
- Preserve existing `#region` structure if present (place `#if TOOLS` inside the region).
- Do NOT move methods — guard them in place.
- If a file already has a `#if TOOLS` block for some methods but others are unguarded, extend the existing block OR add a new one (whichever produces cleaner code).

### 6. VERIFY — Build confirmation

After applying fixes:
```bash
dotnet build "{{PROJECT_ROOT}}" -consoleLoggerParameters:ErrorsOnly
```
Build must succeed. `#if TOOLS` should be transparent since TOOLS is defined in editor/test builds.

Then run a targeted test to confirm guarded methods are still callable:
```bash
dotnet test "{{PROJECT_ROOT}}" --settings .runsettings --verbosity quiet --filter "FullyQualifiedName~Logic" 2>&1
```

## Future Prevention

After the audit, remind the user to add this rule to the Architecture Philosophy skill:
> **Test Accessors**: All `internal` methods added for test access (`Set*`, `_Test*`, `Simulate*`, `ResetForTesting`) MUST be wrapped in `#if TOOLS` / `#endif`. This strips them from export builds and signals intent.

## Scope Exclusions
- Jmodot submodule (separate repo, separate audit)
- `addons/` directory (legitimately `#if TOOLS` for editor plugins)
- `[InternalsVisibleTo]` assembly attributes (orthogonal concern)
- Methods that are `internal` for non-test reasons (cross-namespace access) — Pattern B may flag these; use judgment
