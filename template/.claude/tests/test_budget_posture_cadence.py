"""Re-runnable proof for hooks/budget_posture.py's emission cadence (B5).

The posture line is emitted when it is NEW to the model: first prompt of the session, first
prompt after a compaction, a band change, or a ±0.15 pressure move. The 10-turn heartbeat is
gone — it re-sent text the model still had. Cases feed real UserPromptSubmit payloads and
assert on stdout.

Both temp roots are redirected: HARNESS_HOOK_STATE_DIR for the shared session state, TMP/TEMP/TMPDIR for
the cc-cachestat capture and the hook's own dedupe file. HARNESS_USAGE_SAMPLES redirects the plan-usage
sampler, whose cases follow the cadence cases; HOME/USERPROFILE are planted so the sampler's default
path lands in a temp home, where the last case asserts no spawn fell back to it.

    python3 .claude/tests/test_budget_posture_cadence.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# A cold capacity cache must not start a live probe from a proof (budget_posture.start_warm).
os.environ["HARNESS_CAPACITY_WARM"] = "0"

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "budget_posture.py")
PRECOMPACT = os.path.join(HOOKS, "transcript_backup.py")

SID = "bpc00001"
SEVEN_DAY = 7 * 24 * 3600


def write_capture(tmpdir, session, used_pct):
    """The rate_limits snapshot statusline.py writes every turn."""
    now = time.time()
    path = os.path.join(tmpdir, "cc-cachestat-%s.json" % session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": now,
            # Half the window elapsed, so pressure == used_percentage / 50.
            "seven_day": {"used_percentage": used_pct, "resets_at": now + SEVEN_DAY / 2},
        }}, fh)


def write_windows(tmpdir, session, used7, used5, captured_at):
    """A capture carrying both windows, stamped `captured_at`."""
    now = time.time()
    path = os.path.join(tmpdir, "cc-cachestat-%s.json" % session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {
            "captured_at": captured_at,
            "seven_day": {"used_percentage": used7, "resets_at": now + SEVEN_DAY / 2},
            "five_hour": {"used_percentage": used5, "resets_at": now + 3600},
        }}, fh)


def read_samples(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def default_samples_size(home):
    """The size of the default samples path *as the planted home resolves it*.

    The last case needs this path, not the user's real one: `tools/usage_samples.py:37` resolves
    the default through `~`, and every live session's UserPromptSubmit appends to the user-level
    file (`tools/usage_samples.py:8-13`, account-wide by design). An assertion on the real file
    therefore fails whenever any concurrent session prompts during the proof, and cannot tell that
    write apart from a spawn here that ignored HARNESS_USAGE_SAMPLES. Planting HOME/USERPROFILE
    keeps the check on the spawns this proof makes, and turns a fallback write into a file this
    proof owns.
    """
    path = os.path.join(home, ".claude", "usage_samples.jsonl")
    return os.path.getsize(path) if os.path.exists(path) else None


def sampler_cases(cases, home):
    state = tempfile.mkdtemp(prefix="bpsstate_")
    tmp = tempfile.mkdtemp(prefix="bpstmp_")
    samples = os.path.join(tempfile.mkdtemp(prefix="bpssamples_"), "samples.jsonl")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
               HOME=home, USERPROFILE=home,
               PYTHONIOENCODING="utf-8", HARNESS_USAGE_SAMPLES=samples)
    for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL"):
        env.pop(key, None)

    captured = time.time() - 5
    write_windows(tmp, "bps00001", 40.0, 12, captured)
    prompt(env, session="bps00001")
    rows = read_samples(samples)
    cases.append(("a fresh capture appends one sample per window",
                  sorted(r.get("window") for r in rows) == ["five_hour", "seven_day"]
                  and all(r.get("provider") == "anthropic" for r in rows)))
    cases.append(("each sample's ts is the capture's captured_at",
                  rows and all(r.get("ts") == captured for r in rows)
                  and {r["window"]: r.get("used") for r in rows} == {"five_hour": 12, "seven_day": 40.0}))

    prompt(env, session="bps00001")
    cases.append(("a repeat of the same capture appends nothing", len(read_samples(samples)) == 2))

    write_windows(tmp, "bps00001", 40.0, 13, time.time() - 2)
    out = prompt(env, session="bps00001")
    cases.append(("a sample lands when the posture emission is deduped",
                  out == "" and len(read_samples(samples)) == 4))

    write_windows(tmp, "bps00001", 41.0, 14, time.time() - 700)
    prompt(env, session="bps00001")
    cases.append(("a capture older than 10 minutes is skipped", len(read_samples(samples)) == 4))

    write_windows(tmp, "bps00001", 41.0, 14, time.time() - 60)
    prompt(env, session="bps00001")
    cases.append(("a capture no newer than the last sample is skipped", len(read_samples(samples)) == 4))

    write_windows(tmp, "bps00001", 42.0, 15, time.time() - 1)
    racers = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8")
              for _ in range(2)]
    for i, proc in enumerate(racers):
        proc.stdin.write(json.dumps({"session_id": "bpsrace%d" % i, "prompt": "go"}))
        proc.stdin.close()
    for proc in racers:
        proc.wait(timeout=60)
    cases.append(("two concurrent runs on one capture append one sample per window",
                  len(read_samples(samples)) == 6))

    blocker = os.path.join(tempfile.mkdtemp(prefix="bpsblock_"), "a_file")
    with open(blocker, "w", encoding="utf-8") as fh:
        fh.write("not a directory")
    control = prompt(dict(env, HARNESS_USAGE_SAMPLES=os.path.join(
        tempfile.mkdtemp(prefix="bpsctl_"), "samples.jsonl")), session="bps00003")
    broken = prompt(dict(env, HARNESS_USAGE_SAMPLES=os.path.join(blocker, "samples.jsonl")),
                    session="bps00004")
    cases.append(("an unwritable samples path leaves the posture line intact, adding nothing to stdout",
                  control.startswith("[budget-posture]") and broken == control))

    aged = os.path.join(tempfile.mkdtemp(prefix="bpsaged_"), "samples.jsonl")
    now = time.time()
    with open(aged, "w", encoding="utf-8") as fh:
        for ts in (now - 40 * 86400, now - 86400):
            fh.write(json.dumps({"ts": ts, "provider": "anthropic", "window": "seven_day",
                                 "used": 1, "resets_at": ts + 100}) + "\n")
    write_windows(tmp, "bps00005", 43.0, 16, now - 1)
    prompt(dict(env, HARNESS_USAGE_SAMPLES=aged), session="bps00005")
    kept = [r["ts"] for r in read_samples(aged)]
    cases.append(("the first append of a day drops lines older than 35 days",
                  len(kept) == 3 and min(kept) == now - 86400))


def checked_run(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode not in (0, 2) or "Traceback (most recent call last)" in combined:
        raise RuntimeError("helper subprocess failed (exit %d): %s" % (
            result.returncode, combined[-1000:]))
    return result


def prompt(env, session=SID):
    transcript = os.path.join(env['HARNESS_HOOK_STATE_DIR'], 'dispatch.jsonl')
    with open(transcript, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Agent', 'input': {'prompt': 'review'}}]}}) + '\n')
    r = checked_run([sys.executable, HOOK],
                    input=json.dumps({"session_id": session, "prompt": "next slice", 'transcript_path': transcript}),
                    capture_output=True, text=True, timeout=60, env=env,
                    encoding="utf-8")   # the hook prints §; locale decode mangles it
    return (r.stdout or "").strip()


def prompt_bare(env, session):
    """One prompt of a session with no transcript and no dispatch in it."""
    r = checked_run([sys.executable, HOOK], input=json.dumps({"session_id": session, "prompt": "next slice"}),
                    capture_output=True, text=True, timeout=60, env=env, encoding="utf-8")
    return (r.stdout or "").strip()


def precompact(env, session=SID):
    checked_run([sys.executable, PRECOMPACT],
                input=json.dumps({"session_id": session, "trigger": "auto"}),
                capture_output=True, text=True, timeout=60,
                env=dict(env, CLAUDE_PROJECT_DIR=env["HARNESS_HOOK_STATE_DIR"]))


def main():
    home = tempfile.mkdtemp(prefix="bpchome_")
    default_before = default_samples_size(home)
    state = tempfile.mkdtemp(prefix="bpcstate_")
    tmp = tempfile.mkdtemp(prefix="bpctmp_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=state, TMP=tmp, TEMP=tmp, TMPDIR=tmp,
               HOME=home, USERPROFILE=home,
               PYTHONIOENCODING="utf-8",
               HARNESS_USAGE_SAMPLES=os.path.join(tempfile.mkdtemp(prefix="bpcsamples_"), "s.jsonl"))
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    cases = []
    for label, source in (
        ("the proof helper rejects an abnormal exit", "raise SystemExit(7)"),
        ("the proof helper rejects a traceback even with exit zero",
         "print('Traceback (most recent call last):')"),
    ):
        try:
            checked_run([sys.executable, "-c", source], capture_output=True, text=True, timeout=60)
            rejected = False
        except RuntimeError:
            rejected = True
        cases.append((label, rejected))

    write_capture(tmp, SID, 40.0)                 # pressure 0.80 -> Surplus
    out = prompt(env)
    cases.append(("the first prompt emits the posture",
                  "[budget-posture]" in out and "Surplus" in out))
    cases.append(("the emitted NEVER clause cites its home", "orchestration §5" in out))

    # The plan rooms reach the session before its first dispatch: no dispatch, no transcript, still speaks.
    write_capture(tmp, "bpc00020", 40.0)
    out = prompt_bare(env, "bpc00020")
    cases.append(("the first prompt of a session that has dispatched nothing carries the plan rooms",
                  "[budget-posture]" in out and "(this session) Surplus: 7d" in out and "left" in out))
    cases.append(("and that line is deduped on the next prompt", prompt_bare(env, "bpc00020") == ""))
    write_windows(tmp, "bpc00021", 40.0, 95.0, time.time())
    out = prompt_bare(env, "bpc00021")
    cases.append(("a hot 5-hour window states the one-native-job rule and where the next job goes",
                  "one native job at a time" in out and "the next waits for it or goes to a row on another plan" in out))

    cases.append(("an unchanged posture is silent on the next prompt", prompt(env) == ""))
    cases.append(("and stays silent — no turn-count heartbeat", prompt(env) == ""))

    write_capture(tmp, SID, 60.0)                 # pressure 1.20 -> Ahead
    out = prompt(env)
    cases.append(("a band change re-emits", "Ahead" in out))

    precompact(env)
    out = prompt(env)
    cases.append(("a compaction re-emits the unchanged posture",
                  "[budget-posture]" in out))

    cases.append(("still deduped after that", prompt(env) == ""))

    # A spent week late in its window reads a mid-band pressure (~1.0), but nothing is left:
    # the host line must say Exhausted, as it already does for every other transport.
    now = time.time()
    with open(os.path.join(tmp, "cc-cachestat-bpc00005.json"), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"captured_at": now,
                                   "seven_day": {"used_percentage": 100.0, "resets_at": now + 3600}}}, fh)
    out = prompt(env, session="bpc00005")
    cases.append(("a 100%-used week reads Exhausted, never a pace band",
                  "(this session) Exhausted:" in out and "(this session) On pace" not in out and "(this session) Surplus" not in out))

    cli = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--five-hour",
                          "--session", "bpc00005"], capture_output=True, text=True, env=env, timeout=60)
    cases.append(("the CLI agrees with the spent-week posture instead of reporting On pace",
                  cli.returncode == 0 and cli.stdout.startswith("Exhausted\t")))

    with open(os.path.join(tmp, "cc-cachestat-bpc00006.json"), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"captured_at": now,
                                   "seven_day": {"used_percentage": 99.0,
                                                 "resets_at": now + 22 * 3600}}}, fh)
    out = prompt(env, session="bpc00006")
    cases.append(("99% used with 22 hours left warns Hot before the live refusal",
                  "(this session) Hot: 7d 1% left" in out and "99.0% used" in out))
    cli = subprocess.run([sys.executable, HOOK, "--band", "--pressure", "--five-hour",
                          "--session", "bpc00006"], capture_output=True, text=True, env=env, timeout=60)
    parts = cli.stdout.strip().split("\t")
    try:
        ratio = float(parts[4])
    except (IndexError, ValueError):
        ratio = None
    cases.append(("band CLI keeps four fields and appends weekly run-out for the native guard",
                  cli.returncode == 0 and len(parts) == 5 and parts[0] == "Hot"
                  and ratio is not None and 0.065 <= ratio <= 0.070))

    with open(os.path.join(tmp, "cc-cachestat-bpc00007.json"), "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"captured_at": now,
                                   "seven_day": {"used_percentage": 100.0, "resets_at": now - 10},
                                   "five_hour": {"used_percentage": 10.0,
                                                 "resets_at": now + 3600}}}, fh)
    stale = prompt(env, session="bpc00007")
    cli = subprocess.run([sys.executable, HOOK, "--band", "--session", "bpc00007"],
                         capture_output=True, text=True, env=env, timeout=60)
    cases.append(("a stale spent week cannot claim Exhausted while the short window is valid",
                  "(this session) Exhausted" not in stale and cli.returncode == 3
                  and cli.stdout.strip() == "unknown"))

    session = "bpc00008"
    path = os.path.join(tmp, "cc-cachestat-%s.json" % session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"captured_at": now,
                                   "seven_day": {"used_percentage": 98.9,
                                                 "resets_at": now + 3600}}}, fh)
    before_floor = prompt(env, session=session)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rate_limits": {"captured_at": now + 1,
                                   "seven_day": {"used_percentage": 99.0,
                                                 "resets_at": now + 3600}}}, fh)
    at_floor = prompt(env, session=session)
    cases.append(("crossing 98.9 to 99% re-emits the live-preflight warning",
                  "On pace" in before_floor and "99%" in at_floor
                  and "preflight" in at_floor and prompt(env, session=session) == ""))

    # Negative: no telemetry to read at all.
    empty = tempfile.mkdtemp(prefix="bpcnone_")
    bare = dict(env, TMP=empty, TEMP=empty, TMPDIR=empty)
    cases.append(("no capture on disk emits nothing",
                  prompt(bare, session="bpc00002") == ""))

    sampler_cases(cases, home)
    cases.append(("no spawn fell back to the default path: the planted ~/.claude/usage_samples.jsonl is unwritten",
                  default_samples_size(home) == default_before))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
