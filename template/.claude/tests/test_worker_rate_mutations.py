"""Mutation probe: break each guarded property and confirm the suite notices.

A passing suite proves the code works today. It does not prove the suite would object if
someone removed the guard tomorrow -- and an assertion that cannot fail is coverage on
paper only. So each mutation disables exactly one property and the suite must go red.

Every mutation runs against a copy of the tool and the suite in a temporary `.claude/` tree,
so the tracked `tools/worker_rate.py` is never written: a mutated source mid-battery stales
the harness stamp, and a killed run would have left the broken tool on disk. The unmutated
copy must pass first, or a "caught" would only mean the copy is broken.
"""
import hashlib, pathlib, shutil, subprocess, sys, tempfile

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

before = hashlib.sha256(TOOL.read_bytes()).hexdigest()
src = TOOL.read_bytes().decode("utf-8").replace("\r\n", "\n")
failures = []
with tempfile.TemporaryDirectory(prefix="worker_rate_mutations_") as tmp:
    tmp_tests = pathlib.Path(tmp) / ".claude" / "tests"
    tmp_tools = pathlib.Path(tmp) / ".claude" / "tools"
    tmp_tests.mkdir(parents=True)
    tmp_tools.mkdir(parents=True)
    shutil.copyfile(SUITE, tmp_tests / SUITE.name)

    def run_suite(text):
        (tmp_tools / TOOL.name).write_bytes(text.encode("utf-8"))
        return subprocess.run([sys.executable, str(tmp_tests / SUITE.name)], capture_output=True,
                              text=True, timeout=300)

    control = run_suite(src)
    if control.returncode != 0:
        print("FAIL: the unmutated copy fails its suite, so no mutation result means anything")
        print((control.stdout + control.stderr)[-2000:])
        raise SystemExit(1)
    print("control: the unmutated copy passes")

    for label, old, new in MUTATIONS:
        if old not in src:
            print(f"SKIP (anchor moved): {label}")
            failures.append(label)
            continue
        if run_suite(src.replace(old, new, 1)).returncode == 0:
            print(f"SURVIVED (suite did not notice): {label}")
            failures.append(label)
        else:
            print(f"caught: {label}")

if hashlib.sha256(TOOL.read_bytes()).hexdigest() != before:
    print(f"FAIL: the tracked {TOOL.name} changed during the probe")
    failures.append("tracked tool changed")

print()
if failures:
    print(f"{len(failures)} failure(s) — a mutation survived, an anchor moved, or the tracked tool changed")
    raise SystemExit(1)
print(f"ALL {len(MUTATIONS)} MUTATIONS CAUGHT; tracked tool untouched")
