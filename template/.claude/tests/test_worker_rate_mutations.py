"""Mutation probe: break each guarded property and confirm the suite notices.

A passing suite proves the code works today. It does not prove the suite would object if
someone removed the guard tomorrow -- and an assertion that cannot fail is coverage on
paper only. So each mutation disables exactly one property and the suite must go red.
"""
import pathlib, subprocess, sys, tempfile

TESTS = pathlib.Path(__file__).resolve().parent
TOOL = TESTS.parent / "tools" / "worker_rate.py"
SUITE = TESTS / "test_worker_rate_difficulty.py"

MUTATIONS = [
    ("guard removed (no verdict needs characterising)",
     'if args.outcome in FIDELITY and not args.derivation:',
     'if False and not args.derivation:'),
    ("unknown breadth defaults into a band instead of grouping apart",
     '    if not files and not toks:\n        return "?"',
     '    if not files and not toks:\n        return "narrow"'),
    ("breadth stored on the rating (a second home for a derived value)",
     '"derivation": args.derivation,',
     '"derivation": args.derivation, "breadth": breadth_of(target),'),
]

src = TOOL.read_text(encoding="utf-8")
backup = tempfile.mkstemp(suffix=".py")[1]
pathlib.Path(backup).write_text(src, encoding="utf-8")
failures = []
try:
    for label, old, new in MUTATIONS:
        if old not in src:
            print(f"SKIP (anchor moved): {label}")
            failures.append(label)
            continue
        TOOL.write_text(src.replace(old, new, 1), encoding="utf-8")
        p = subprocess.run([sys.executable, str(SUITE)], capture_output=True,
                           text=True, timeout=300)
        if p.returncode == 0:
            print(f"SURVIVED (suite did not notice): {label}")
            failures.append(label)
        else:
            print(f"caught: {label}")
finally:
    TOOL.write_text(src, encoding="utf-8")

print()
if failures:
    print(f"{len(failures)} mutation(s) survived — those assertions are not load-bearing")
    raise SystemExit(1)
print(f"ALL {len(MUTATIONS)} MUTATIONS CAUGHT")
