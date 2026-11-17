"""Re-runnable proof: which checkout writes leave a proof's reads observable (`_PassCache.manifest`).

A write into the checkout marks the reads opaque, because the next run may read what this one wrote. The
runner's own exhaust is the exception: the gitignored log, cache and scratch folders that hooks and tools
fill whenever they run, and `.claude/tmp`, where a proof plants a fixture under a name unique to the run. Eleven proofs were never reused because a hook they ran wrote `logs/routing_audit.jsonl`
and eight because a tool they ran wrote `.claude/cache/bench_root_digests.json`.

    python3 .claude/tests/test_harness_tests_write_exhaust.py
"""
import os
import subprocess
import sys
import tempfile

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402

FAILS = []
TOTAL = 0


def check(name, ok, detail=""):
    global TOTAL
    TOTAL += 1
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def make_repo():
    repo = tempfile.mkdtemp(prefix="harnesswriteexhaust_")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=repo, check=True, capture_output=True)
    proof = os.path.join(repo, ".claude", "tests", "test_alpha.py")
    os.makedirs(os.path.dirname(proof))
    with open(proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    return repo, proof


def manifest_after_write(rel):
    """The manifest of a proof that read its own file and wrote `rel` under the checkout."""
    repo, proof = make_repo()
    cache = harness_tests._pass_cache(repo, [proof])
    temp = tempfile.mkdtemp(prefix="harnesswriteexhaust_temp_")
    events = [["r", proof], ["w", os.path.join(repo, *rel.split("/"))]]
    return cache.manifest(proof, {"events": events, "temp": temp})


def case_exhaust_folders_leave_the_reads_observable():
    for rel in ("logs/routing_audit.jsonl", ".claude/logs/x.jsonl", ".claude/scratch/a/b.txt",
                ".claude/cache/bench_root_digests.json.81208.tmp", ".claude/.cache/runaway-scan-reaper.stamp",
                ".claude/tmp/pe_absent_delete_x/main_repo/readme.md"):
        manifest = manifest_after_write(rel)
        check("a write to %s leaves the reads observable and out of the key" % rel,
              isinstance(manifest, dict) and manifest["opaque"] is False
              and not any(rel in r for k in ("files", "probes", "dirs") for r in manifest[k]), str(manifest))


def case_a_write_to_an_input_still_marks_the_reads_opaque():
    for rel in (".claude/tools/out.json", ".claude/cachefoo/x", ".claude/logsfoo/x", "notes/logs/x", "src/logs.txt"):
        manifest = manifest_after_write(rel)
        check("a write to %s still marks the reads opaque" % rel,
              isinstance(manifest, dict) and manifest.get("opaque") is True, str(manifest))


def main():
    case_exhaust_folders_leave_the_reads_observable()
    case_a_write_to_an_input_still_marks_the_reads_opaque()
    print("\n%d/%d cases pass" % (TOTAL - len(FAILS), TOTAL))
    for f in FAILS:
        print("  FAIL " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
