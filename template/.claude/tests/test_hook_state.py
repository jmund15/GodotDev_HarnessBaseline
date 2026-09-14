"""Direct proof for hooks/_hook_state.py.

The library exists because two hooks writing the shared per-session state file interleaved into
one complete JSON document followed by a fragment of another, which bricked harness editing for a
whole session. Its two properties are therefore load-bearing and are asserted here directly rather
than through a consumer: atomic writes stop NEW damage, salvage stops EXISTING damage from being
permanent. A consumer test exercises the happy path and would miss either.

    python3 .claude/tests/test_hook_state.py
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from _hook_state import read_json_salvage, write_json_atomic  # noqa: E402

failures = []
ran = 0


def check(label, cond, detail=""):
    global ran
    ran += 1
    print("%-4s %s" % ("ok" if cond else "FAIL", label))
    if not cond:
        failures.append("%s %s" % (label, detail))


d = tempfile.mkdtemp(prefix="hookstate_")

# --- salvage: the exact corruption shape that caused the incident -----------
torn = os.path.join(d, "torn.json")
good = {"skills_loaded": ["instruction_quality"], "memory_check_fires": 2}
io.open(torn, "w", encoding="utf-8").write(
    json.dumps(good) + 'h__search", "target": "SEARCH:x", "ts": 1}], "skills_loaded": ["a"]}')
got = read_json_salvage(torn)
check("salvages a complete document followed by debris",
      got.get("skills_loaded") == ["instruction_quality"], repr(got)[:120])
check("salvage keeps every field of the recovered document",
      got.get("memory_check_fires") == 2, repr(got)[:120])

# --- salvage: fail-open, never raise ---------------------------------------
io.open(os.path.join(d, "empty.json"), "w").write("")
io.open(os.path.join(d, "junk.json"), "w").write("not json at all")
io.open(os.path.join(d, "list.json"), "w").write("[1,2,3]")
io.open(os.path.join(d, "frag.json"), "w").write('{"a": 1, "b')
check("missing file -> {}", read_json_salvage(os.path.join(d, "nope.json")) == {})
check("empty file -> {}", read_json_salvage(os.path.join(d, "empty.json")) == {})
check("garbage -> {}", read_json_salvage(os.path.join(d, "junk.json")) == {})
check("non-dict JSON -> {}", read_json_salvage(os.path.join(d, "list.json")) == {})
check("leading fragment with no complete document -> {}",
      read_json_salvage(os.path.join(d, "frag.json")) == {})

# --- atomic write ----------------------------------------------------------
tgt = os.path.join(d, "nested", "state.json")
check("write creates missing parent dirs", write_json_atomic(tgt, {"a": 1}) is True)
check("write round-trips", json.load(io.open(tgt, encoding="utf-8")) == {"a": 1})
check("write leaves no temp file behind",
      not [f for f in os.listdir(os.path.dirname(tgt)) if f.startswith(".tmp_")],
      str(os.listdir(os.path.dirname(tgt))))

# The replacement must be all-or-nothing: a reader sees the old doc or the new one.
write_json_atomic(tgt, {"a": 2, "b": 3})
check("overwrite is complete, never partial",
      json.load(io.open(tgt, encoding="utf-8")) == {"a": 2, "b": 3})

# --- write fail-open -------------------------------------------------------
check("unserializable payload returns False, does not raise",
      write_json_atomic(os.path.join(d, "bad.json"), {"k": {1, 2}}) is False)
check("a failed write leaves no temp file",
      not [f for f in os.listdir(d) if f.startswith(".tmp_")], str(os.listdir(d)))

# --- round trip through both ------------------------------------------------
rt = os.path.join(d, "rt.json")
write_json_atomic(rt, {"skills_loaded": ["testing"], "n": 4})
check("salvage reads what atomic write produced",
      read_json_salvage(rt) == {"skills_loaded": ["testing"], "n": 4})

print("\n%d/%d checks pass" % (ran - len(failures), ran))
for f in failures:
    print("  FAIL " + f)
sys.exit(1 if failures else 0)
