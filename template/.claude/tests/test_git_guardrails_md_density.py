#!/usr/bin/env python3
"""Proof for git_guardrails._md_density_verdict — the commit-time §5 density gate.

WHY IT EXISTS HERE and not in harness_growth_guard: that guard is a PostToolUse hook matching
`Write|Edit`. A `.md` authored through Bash — a python patch script, a heredoc, `sed -i` — never
reaches it, so on the authoring path a long session actually uses, the density standard was
unenforced. The commit sees the result however it was written.

The load-bearing cases are the NEGATIVES. A gate that blocks a pre-existing dense paragraph, or one
that fires on the designated evidence home, is unpassable — and an unpassable gate gets bypassed
rather than obeyed.

Run: python3 .claude/tests/test_git_guardrails_md_density.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
spec = importlib.util.spec_from_file_location("gg", os.path.join(HERE, "..", "hooks",
                                                                 "git_guardrails.py"))
gg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gg)

UNDER = "**A short rule.** It states one thing and stops."          # ~55 B
OVER = "**A packed rule.** " + ("narrative that restates itself and carries inline evidence " * 12)
assert len(OVER.encode()) > 350


def repo():
    """A tiny repo with one committed loaded surface and one evidence file."""
    d = tempfile.mkdtemp(prefix="ggdens_")
    for sub in (".claude/skills", ".claude/auto-memory/archive"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    write(d, ".claude/skills/x.md", "# X\n\n" + UNDER + "\n")
    write(d, ".claude/auto-memory/archive/e.md", "# E\n\n" + UNDER + "\n")
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
    return d


def write(d, rel, text):
    with open(os.path.join(d, rel), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def verdict(d, *paths):
    return gg._md_density_verdict(d, list(paths))


CASES = []


def case(name, fn):
    CASES.append((name, fn))


def c_new_over():
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + UNDER + "\n\n" + OVER + "\n")
    v = verdict(d, ".claude/skills/x.md")
    return v is not None and "audit trigger" in v and ".claude/skills/x.md" in v


def c_new_under():
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + UNDER + "\n\n**Another rule.** Short and done.\n")
    return verdict(d, ".claude/skills/x.md") is None


def c_preexisting_dense_untouched():
    """A file that ARRIVES dense is not this commit's fault."""
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + "\n")
    subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "dense"], capture_output=True)
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + "\n\n" + UNDER + "\n")
    return verdict(d, ".claude/skills/x.md") is None


def c_preexisting_dense_shrunk():
    """SHRINKING an over-cap unit must not read as adding one -- the false positive that would
    make the gate unpassable on every de-bloat pass."""
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + "\n")
    subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "dense"], capture_output=True)
    write(d, ".claude/skills/x.md", "# X\n\n**A packed rule.** now much shorter but still long "
                                    + ("padding " * 40) + "\n")
    return verdict(d, ".claude/skills/x.md") is None


def c_preexisting_dense_grown():
    """...but GROWING one still fires."""
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + "\n")
    subprocess.run(["git", "-C", d, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "dense"], capture_output=True)
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + " and now even more narrative appended.\n")
    v = verdict(d, ".claude/skills/x.md")
    return v is not None


def c_evidence_home_exempt():
    """auto-memory/ is where §5 SENDS the narrative. Firing there inverts the rule."""
    d = repo()
    write(d, ".claude/auto-memory/archive/e.md", "# E\n\n" + OVER + "\n")
    return verdict(d, ".claude/auto-memory/archive/e.md") is None


def c_generated_dot_cache_exempt():
    """A `.claude/.cache/` baseline mirror is generated, not authored doctrine."""
    d = repo()
    os.makedirs(os.path.join(d, ".claude/.cache/baseline/skills"), exist_ok=True)
    write(d, ".claude/.cache/baseline/skills/x.md", "# X\n\n" + UNDER + "\n\n" + OVER + "\n")
    return verdict(d, ".claude/.cache/baseline/skills/x.md") is None


def c_judge_rubric_exempt():
    """A benchmark judge rubric is sealed instrument text a JUDGE reads, pinned by sha256; splitting a
    unit to pass the gate would change a pinned instrument (2026-09-22: t3-v2.2.md blocked)."""
    d = repo()
    rel = ".claude/scripts/benchmark_campaign/scoring/rubrics/t3-v9.md"
    os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
    write(d, rel, "# T3\n\n" + OVER + "\n")
    return verdict(d, rel) is None


def c_benchmark_readme_still_fires():
    """The campaign README is model-facing guidance: the rubric exemption must not widen to it."""
    d = repo()
    rel = ".claude/scripts/benchmark_campaign/README.md"
    os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
    write(d, rel, "# R\n\n" + OVER + "\n")
    return verdict(d, rel) is not None


def c_non_md_ignored():
    d = repo()
    return verdict(d, ".claude/hooks/thing.py") is None


def c_names_the_fix():
    d = repo()
    write(d, ".claude/skills/x.md", "# X\n\n" + OVER + "\n")
    v = verdict(d, ".claude/skills/x.md") or ""
    return "§6" in v and "auto-memory/archive" in v and gg.BYPASS_VAR in v


for n, f in (("a NEW over-cap unit on a loaded surface is BLOCKED", c_new_over),
             ("a new under-cap unit passes", c_new_under),
             ("a pre-existing dense unit is not this commit's fault", c_preexisting_dense_untouched),
             ("SHRINKING a dense unit is not 'adding' one", c_preexisting_dense_shrunk),
             ("GROWING an already-dense unit still fires", c_preexisting_dense_grown),
             ("auto-memory/ (the evidence home) is exempt", c_evidence_home_exempt),
             (".claude/.cache/ (generated baseline mirror) is exempt", c_generated_dot_cache_exempt),
             ("a benchmark judge rubric (sealed instrument text) is exempt", c_judge_rubric_exempt),
             ("the benchmark campaign README still fires", c_benchmark_readme_still_fires),
             ("a non-.md staged path is ignored", c_non_md_ignored),
             ("the refusal names the §6 fix, the evidence home and the bypass", c_names_the_fix)):
    case(n, f)


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
