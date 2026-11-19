"""Mechanism census: measured facts per proof, hook, tool and script, with no verdict.

Prints facts per item, sorted by a fact column the caller names (`--table`), and writes the whole reading as JSON
(`--out`). It never says keep, fix or cut: a judged ruling reads these facts (`/harness_prune`) and decides. Census
rests on G1-G5 (plan verification-land-first.md §Census and rulings); `totals` in the JSON carries the same
groupings the scratch scripts printed, so a reading reproduces them.

Sources, all read-only:
- proof runs and fails: `.claude/logs/harness_runs.json` in every checkout (`git worktree list`);
- FLAKY rows: `.claude/logs/harness_flaky.jsonl` in every checkout, and the gate ledgers (`harness/gate_ledger` in
  the common git dir, and each checkout's `.claude/scratch/gate_ledger`). Gate ledger records carry no flaky field
  (observed, 2026-11-18), so they count as gate runs only;
- transcripts: `*.jsonl` and `*/subagents/**/*.jsonl` under `--transcripts` (default: the main checkout's Claude
  project folder), by file mtime inside the window: tool calls by name, and denies from hook output;
- git history on `--ref` (default `main`): fix commits, files added, proof and hook listings;
- the main check's state file (`--main-check`, default `origin/main-check-state:main_check.json`);
- rulings (`--rulings`, default `.claude/census_reviews.jsonl`): an item changed when a commit, a red, a flaky row or
  a deny for it comes after its last ruling date (the record's `date`, latest row for the item).

Definitions:
- proof clearing kinds, for each recorded fail and the next recorded pass of the same proof: `same-head` (the two
  heads match, or nothing changed), `unpaired` (no later pass, or a commit is unknown), `proof-only` (only the proof
  file changed among the files, nothing else: the proof file is among them and no other file is), `both` (the proof
  file and other files changed), `other-only` (the proof file did not change). Facts about the clearing range, never
  its cause.
- fix commit: a commit with subject `fix(` or `fix:` on the ref, no merges, touching a path of the item. A commit whose
  scope names `review`, `audit` or `fold` is excluded from every item's fix count (C53): those are workflow artifacts.
- commit-cause classes (judged by a reader, not computed here; churn/context.md E1): (a) sibling instance: the same
  failure class an earlier fix on that mechanism already addressed for another instance; (b) unaccounted variable:
  a value the author's runs held fixed that varies in use (platform, machine load or timing, concurrent sessions or
  worktrees, scale or input size, cache state, PATH or runner environment, other); (c) judgment encoded as
  determinism: a pattern, regex or keyword check that false-blocked or false-passed on prose or intent; (d) fix of a
  recent fix: repairs or reverts a fix made within the window; (e) new requirement or owner-directed change, not a
  defect; (f) one-off defect. A commit that fits two names both.
- fix-commit sampling rule (the baseline of 205 that class b's share is measured against, Milestone C): every fix
  commit on the ref in the window (no merges, the baseline's population: review, audit and fold commits included).
  Per harness code file (`.py .ps1 .sh .js .mjs`, outside `tests/` and `scratch/`) fixed 6 or more times, take
  commits evenly spaced over that file's list, k = 15 for the 8 most-fixed files and 2 for the rest; each commit once,
  listed with every file it touched among the sampled set. The judge classifies the sample; the tool lists it.
- hook spawn time: median of 3 runs of each hook that a plain Bash call runs (a PreToolUse or PostToolUse entry whose
  matcher includes Bash and whose command has no `if`), on the recorded payload below. Off unless `--time-hooks`.
  Hooks run with `CLAUDE_PROJECT_DIR` set and session id `census-probe`.
- deny: a tool result in a transcript whose text is hook output (`PreToolUse`, `PostToolUse`, `PermissionDenied`, or
  `[command hook`) and contains `DENIED` or `BLOCKED`. Attributed to the hook named by `hooks/<name>.py` in the text.

Freshness: `--line [census.json]` prints the `/session_end` census line: `census stale (<date>)` when `generatedAt`
is older than 32 days, else `census: N items changed since their last ruling` when N > 0, else nothing. A
`main_check.json` (`--main-check`, else `origin/main-check-state:main_check.json`) adds the last finished run's wall
to that line, `run.wall`'s close + gate + finalize seconds; an absent, unreadable or unfinished state adds nothing.
With no census path, the census is read from `origin/main-check-state:mechanism_census.json`.

Usage:
    python3 .claude/tools/mechanism_census.py --out <census.json> [--table proofs|hooks|tools --sort <column>]
        [--checkout DIR]... [--transcripts DIR]... [--main-check FILE] [--rulings FILE] [--ref main] [--days 30]
        [--now ISO] [--time-hooks] [--repo DIR]
    python3 .claude/tools/mechanism_census.py --line [census.json] [--main-check FILE] [--repo DIR] [--now ISO]
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import statistics
import subprocess
import sys
import time

STALE_DAYS = 32
CODE_EXT = (".py", ".ps1", ".sh", ".js", ".mjs")
FIX_RE = re.compile(r"^fix(\(|:)")
EXCLUDED_SCOPE_RE = re.compile(r"review|audit|fold")
PROOF_RE = re.compile(r"(^|/)(test_[^/]+\.(py|sh|ps1)|[^/]+_test\.\w+)$")
CLEARING_KINDS = ("proof-only", "other-only", "both", "same-head", "unpaired")
HOOK_PAYLOAD = {"session_id": "census-probe", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "git status"}}
DENY_RE = re.compile(r"(PreToolUse|PostToolUse|PermissionDenied|\[command hook)")


def _now(args=None):
    if args and args.now:
        return datetime.datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def git(repo, *args):
    out = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return out.stdout if out.returncode == 0 else ""


def worktrees(repo):
    return [line[len("worktree "):] for line in git(repo, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


def load_json_rows(path):
    """Run records from a checkout's harness_runs.json; None when absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    rows = data if isinstance(data, list) else data.get("runs") or list(data.values())
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("proofs"), list)]


def load_flaky(path, cut):
    rows = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and float(r.get("ts") or 0) >= cut:
                rows.append(r)
    return rows


def load_ledger(paths):
    seen = {}
    for d in paths:
        for p in glob.glob(os.path.join(d, "*.result.json")):
            try:
                with open(p, encoding="utf-8-sig") as fh:
                    r = json.load(fh)
            except (OSError, ValueError):
                continue
            seen.setdefault(r.get("id") or p, r)
    return list(seen.values())


def clearing_kind(proof, head, next_head, files):
    if not head or not next_head:
        return "unpaired"
    if next_head[:12] == head[:12]:
        return "same-head"
    if files is None:
        return "unpaired"
    if not files:
        return "same-head"
    tests = [f for f in files if f.endswith("/tests/" + proof)]
    proof_touched = any(f.endswith("/" + proof) or ("/" + proof.rsplit(".", 1)[0] + "_part") in f for f in files)
    if proof_touched and len(files) <= len(tests):
        return "proof-only"
    if proof_touched:
        return "both"
    return "other-only"


def fix_index(repo, ref, days):
    """Fix commits on `ref` in the window, no merges, touching `.claude` or `.github`: sha, date, subject, files,
    and whether the scope is excluded from counts."""
    log = git(repo, "log", ref, "--no-merges", "--since=%d.days" % days, "--format=@@%H%x09%ad%x09%s", "--date=short",
              "--name-only", "--", ".claude", ".github")
    commits = []
    cur = None
    for line in log.splitlines():
        if line.startswith("@@"):
            sha, date, subj = line[2:].split("\t", 2)
            cur = {"sha": sha, "date": date, "subject": subj, "files": []} if FIX_RE.match(subj) else None
            if cur:
                scope = re.match(r"^fix\(([^)]*)\)", subj)
                cur["excluded"] = bool(scope and EXCLUDED_SCOPE_RE.search(scope.group(1)))
                commits.append(cur)
        elif cur and line.strip():
            cur["files"].append(line.strip())
    return commits


def sample_fix_commits(index):
    """The fix-commit sampling rule (module docstring). `index` maps a path to its fix commits (dicts with sha, date,
    subject, files). Returns the unique sampled commits, each with the path it was picked for."""
    hot = [(p, cs) for p, cs in index.items()
           if len(cs) >= 6 and p.endswith(CODE_EXT) and not re.search(r"/(tests|scratch)/", p)]
    hot.sort(key=lambda kv: -len(kv[1]))
    picked = collections.OrderedDict()
    for i, (path, cs) in enumerate(hot):
        k = 15 if i < 8 else 2
        n = len(cs)
        for j in sorted(set(int(t * (n - 1) / max(k - 1, 1)) for t in range(min(k, n)))):
            c = cs[j]
            picked.setdefault(c["sha"], {"sha": c["sha"], "date": c["date"], "subject": c["subject"], "file": path,
                                         "files": c.get("files", 1)})
    return list(picked.values())


def transcript_files(dirs, cutoff):
    out = []
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*", "subagents", "**", "*.jsonl"),
                                                                   recursive=True):
            if os.path.getmtime(p) >= cutoff:
                out.append(p)
    return out


def scan_transcripts(paths, names):
    """Per entry-point name: Bash/PowerShell calls and sessions; per deny: count, sessions, hook, first-seen time."""
    pat = None
    if names:
        pat = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
    calls = collections.Counter()
    call_sessions = collections.defaultdict(set)
    denies = collections.defaultdict(lambda: {"count": 0, "sessions": set(), "hook": "", "times": []})
    for p in paths:
        sid = os.path.basename(p)[:8]
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                msg = r.get("message") or {}
                for c in msg.get("content") or []:
                    if not isinstance(c, dict):
                        continue
                    if pat and c.get("type") == "tool_use" and c.get("name") in ("Bash", "PowerShell"):
                        cmd = (c.get("input") or {}).get("command") or ""
                        for m in set(pat.findall(cmd)):
                            calls[m] += 1
                            call_sessions[m].add(sid)
                    if c.get("type") == "tool_result":
                        text = c.get("content")
                        if isinstance(text, list):
                            text = " ".join(x.get("text", "") for x in text if isinstance(x, dict))
                        text = str(text or "")
                        if ("DENIED" in text or "BLOCKED" in text) and DENY_RE.search(text):
                            hook = re.search(r"hooks/([\w.-]+\.py)", text)
                            key = re.sub(r"^\S+ hook error: ", "", text.strip())[:100]
                            d = denies[key]
                            d["count"] += 1
                            d["sessions"].add(sid)
                            d["hook"] = hook.group(1) if hook else "unattributed"
                            d["times"].append(_parse_ts(r.get("timestamp")))
    return calls, call_sessions, denies


def time_hooks(root, hook_paths, repeat=3):
    out = {}
    payload = dict(HOOK_PAYLOAD, cwd=root)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    for path in hook_paths:
        times = []
        for _ in range(repeat):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, path], input=json.dumps(payload), capture_output=True, text=True,
                           cwd=root, env=env, timeout=60)
            times.append((time.perf_counter() - t0) * 1000)
        out[os.path.basename(path)] = round(statistics.median(times))
    return out


def plain_bash_hooks(root):
    """Hook file names that a plain Bash call runs: PreToolUse/PostToolUse entries matching Bash, with no `if`."""
    try:
        with open(os.path.join(root, ".claude", "settings.json"), encoding="utf-8") as fh:
            settings = json.load(fh)
    except (OSError, ValueError):
        return []
    names = set()
    for event in ("PreToolUse", "PostToolUse"):
        for entry in settings.get("hooks", {}).get(event, []):
            if not re.search(r"(^|\|)(Bash|\*)(\||$)", entry.get("matcher", "*")):
                continue
            for spec in entry.get("hooks", []):
                cmd = spec.get("command", "")
                m = re.search(r"hooks/([\w.-]+\.py)", cmd)
                if m and "if" not in spec:
                    names.add(m.group(1))
    return sorted(names)


def list_items(repo, ref, prefix, pattern):
    files = git(repo, "ls-tree", "-r", "--name-only", ref, "--", prefix).splitlines()
    return [f for f in files if pattern.search(f)]


def line_count(repo, path):
    try:
        with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def build(args):
    repo = os.path.abspath(args.repo)
    now = _now(args)
    cut_epoch = now.timestamp() - args.days * 86400
    roots = args.checkout or worktrees(repo)
    main_root = worktrees(repo)[0] if worktrees(repo) else repo
    transcript_dirs = args.transcripts or [os.path.expanduser(
        "~/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", main_root))]

    # Proof runs, fails, and clearing kinds (all recorded history, as the scratch scripts counted).
    runs = collections.Counter(); secs = collections.Counter(); fails = collections.Counter(); close_reds = collections.Counter()
    events = collections.defaultdict(list)
    run_records = 0
    checkouts_read = []
    for root in roots:
        rows = load_json_rows(os.path.join(root, ".claude", "logs", "harness_runs.json"))
        if rows is None:
            continue
        checkouts_read.append(root)
        for r in rows:
            run_records += 1
            ts = float(r.get("ts") or 0)
            for q in r["proofs"]:
                if not isinstance(q, dict):
                    continue
                name = q.get("proof")
                st = q.get("status")
                if not q.get("reused"):
                    runs[name] += 1
                    secs[name] += float(q.get("run_s") or 0)
                if st == "fail":
                    fails[name] += 1
                    if r.get("mode") == "close":
                        close_reds[name] += 1
                if st in ("pass", "fail"):
                    events[name].append((ts, str(r.get("head") or ""), st, r.get("mode")))

    diff_cache = {}
    clearing = collections.defaultdict(collections.Counter)
    for name, ev in events.items():
        ev.sort()
        for i, (ts, head, st, mode) in enumerate(ev):
            if st != "fail":
                continue
            nxt = next(((t, h) for t, h, s, m in ev[i + 1:] if s == "pass"), None)
            files = None
            if nxt and head and nxt[1] and nxt[1][:12] != head[:12]:
                key = (head, nxt[1])
                if key not in diff_cache:
                    out = subprocess.run(["git", "-C", repo, "diff", "--name-only", head, nxt[1]], capture_output=True,
                                         text=True)
                    diff_cache[key] = out.stdout.split() if out.returncode == 0 else None
                files = diff_cache[key]
            clearing[name][clearing_kind(name, head, nxt[1] if nxt else None, files)] += 1

    # Flaky rows: every checkout's log, and the gate ledgers.
    flaky = collections.Counter(); flaky_by_checkout = collections.defaultdict(collections.Counter)
    flaky_rows = []
    for root in roots:
        for r in load_flaky(os.path.join(root, ".claude", "logs", "harness_flaky.jsonl"), cut_epoch):
            flaky[r.get("proof")] += 1
            flaky_by_checkout[r.get("proof")][os.path.basename(root)] += 1
            flaky_rows.append(r)
    common = git(repo, "rev-parse", "--git-common-dir").strip()
    ledger_dirs = [os.path.join(os.path.abspath(os.path.join(repo, common)), "harness", "gate_ledger")]
    ledger_dirs += [os.path.join(r, ".claude", "scratch", "gate_ledger") for r in roots]
    ledger = load_ledger(ledger_dirs)

    # Git history.
    fixes = fix_index(repo, args.ref, args.days)
    fixes_counted = [c for c in fixes if not c["excluded"]]

    def fixes_for(path):
        return [{"sha": c["sha"][:9], "date": c["date"], "subject": c["subject"]}
                for c in fixes_counted if path in c["files"]]

    proofs_live = [f for f in list_items(repo, args.ref, ".claude/tests", PROOF_RE) if f.count("/") == 2]
    proof_names = {os.path.basename(p): p for p in proofs_live}
    tool_files = [f for f in list_items(repo, args.ref, ".claude/tools", re.compile(r"\.(py|ps1|sh|js|mjs)$"))
                  + list_items(repo, args.ref, ".claude/scripts", re.compile(r"\.(py|ps1|sh|js|mjs)$"))
                  if "/lib/" not in f and "/fixtures/" not in f and not os.path.basename(f).startswith("_")]
    hook_files = [f for f in list_items(repo, args.ref, ".claude/hooks", re.compile(r"\.py$"))
                  if not os.path.basename(f).startswith("_")]
    entry_names = {os.path.basename(f): f for f in tool_files}

    # Transcripts: calls per entry point, denies per hook.
    tpaths = transcript_files(transcript_dirs, cut_epoch)
    calls, call_sessions, denies = scan_transcripts(tpaths, list(entry_names))
    deny_by_hook = collections.defaultdict(lambda: {"count": 0, "sessions": set(), "messages": collections.Counter(),
                                                    "times": []})
    for key, d in denies.items():
        h = deny_by_hook[d["hook"]]
        h["count"] += d["count"]
        h["sessions"] |= d["sessions"]
        h["messages"][key] += d["count"]
        h["times"] += [t for t in d["times"] if t]

    spawn = {}
    if args.time_hooks:
        spawn = time_hooks(repo, [os.path.join(repo, ".claude", "hooks", h) for h in plain_bash_hooks(repo)])

    # Proof rows.
    proof_rows = []
    for path in proofs_live:
        name = os.path.basename(path)
        cl = clearing.get(name, collections.Counter())
        fx = fixes_for(path)
        proof_rows.append({"name": name, "path": path, "kind": "proof", "runs": runs[name],
                           "seconds": round(secs[name]), "fails": fails[name], "closeReds": close_reds[name],
                           "flaky": flaky[name], "flakyByCheckout": dict(flaky_by_checkout[name]),
                           "clearing": {k: cl[k] for k in CLEARING_KINDS}, "fixCommits": len(fx), "fixes": fx})
    proof_seconds = {os.path.basename(p): secs[os.path.basename(p)] for p in proofs_live}

    hook_rows = []
    for path in hook_files:
        name = os.path.basename(path)
        d = deny_by_hook.get(name, {"count": 0, "sessions": set(), "messages": collections.Counter()})
        fx = fixes_for(path)
        hook_rows.append({"name": name, "path": path, "kind": "hook", "lines": line_count(repo, path),
                          "spawnMs": spawn.get(name), "denies": d["count"], "denySessions": len(d["sessions"]),
                          "denyMessages": [{"message": m, "count": c} for m, c in d["messages"].most_common(3)],
                          "fixCommits": len(fx), "fixes": fx})

    tool_rows = []
    for path in tool_files:
        name = os.path.basename(path)
        stem = re.sub(r"\.\w+$", "", name)
        refs = [x for x in git(repo, "grep", "-l", "-F", name, "--", ".claude", ".github", "CLAUDE.md").split()
                if x != path and not x.startswith(".claude/tests/")]
        own_proofs = [p for p in proof_names if p.startswith("test_" + stem) or p.startswith(stem + "_test")]
        fx = fixes_for(path)
        tool_rows.append({"name": name, "path": path, "kind": "script" if "/scripts/" in path else "tool",
                          "calls": calls[name], "sessions": len(call_sessions[name]), "docRefs": len(refs),
                          "lines": line_count(repo, path), "proofs": len(own_proofs),
                          "proofSeconds": round(sum(proof_seconds[p] for p in own_proofs)),
                          "fixCommits": len(fx), "fixes": fx})

    # Main check rows.
    main_check = read_main_check(repo, args, now)

    # Rulings: changed since.
    changed = changed_since_rulings(repo, args, now, flaky_rows, deny_by_hook, events)

    # Totals (G1-G5 groupings, measured on this reading's window).
    totals = totals_block(repo, args, now, fixes, fixes_counted, proof_rows, hook_rows, tool_rows, ledger,
                          run_records, checkouts_read, len(tpaths), spawn, deny_by_hook)

    index = collections.defaultdict(list)
    for c in fixes:
        for f in c["files"]:
            index[f].append({"sha": c["sha"], "date": c["date"], "subject": c["subject"], "files": len(c["files"])})
    sample = sample_fix_commits(index)

    return {
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "windowDays": args.days, "ref": args.ref, "checkoutsRead": checkouts_read,
        "transcriptFiles": len(tpaths), "totals": totals, "proofs": proof_rows, "hooks": hook_rows, "tools": tool_rows,
        "mainCheck": main_check, "changedSinceRuling": changed,
        "fixSampling": {"rule": "module docstring §fix-commit sampling rule", "count": len(sample), "commits": sample},
        "sources": {"runRecords": run_records, "flakyRows": len(flaky_rows), "gateLedgerRecords": len(ledger)},
    }


def totals_block(repo, args, now, fixes, fixes_counted, proof_rows, hook_rows, tool_rows, ledger, run_records,
                 checkouts_read, transcript_count, spawn, deny_by_hook):
    ref = args.ref
    since = "--since=%d.days" % args.days
    first_parent = git(repo, "log", "--first-parent", ref, since, "--format=@@%H%x09%s", "--name-only")
    harness_only = game = 0
    types = collections.Counter()
    added_tests = []
    added_hooks = []
    blocks_fp = []
    for line in first_parent.splitlines():
        if line.startswith("@@"):
            sha, subj = line[2:].split("\t", 1)
            types[re.split(r"[ (:]", subj, maxsplit=1)[0]] += 1
            blocks_fp.append([])
            continue
        if line.strip() and blocks_fp:
            blocks_fp[-1].append(line.strip())
    for touched in blocks_fp:
        if not touched:
            continue
        if all(t.startswith(".claude/") for t in touched):
            harness_only += 1
        else:
            game += 1
    added = git(repo, "log", "--first-parent", ref, since, "--diff-filter=A", "--name-only", "--format=@@%h %s")
    cur_subj = ""
    cur_files = []
    blocks = []
    for line in added.splitlines():
        if line.startswith("@@"):
            if cur_files:
                blocks.append((cur_subj, cur_files))
            cur_subj, cur_files = line[2:], []
        elif line.strip():
            cur_files.append(line.strip())
    if cur_files:
        blocks.append((cur_subj, cur_files))
    mech = re.compile(r"^\.claude/(hooks|tools|scripts)/[^/]+\.(py|ps1|sh|js|mjs)$")
    by_kind = collections.Counter()
    for subj, files in blocks:
        for f in files:
            if PROOF_RE.search(f) and f.startswith(".claude/tests/"):
                by_kind["with a new hook/tool/script" if any(mech.match(x) for x in files) else "for existing code"] += 1
                added_tests.append(f)
            if f.startswith(".claude/hooks/") and f.endswith(".py") and not os.path.basename(f).startswith("_"):
                added_hooks.append(os.path.basename(f))
    code_index = collections.defaultdict(int)
    for c in fixes_counted:
        for f in c["files"]:
            if f.endswith(CODE_EXT) and not re.search(r"/(tests|scratch)/", f):
                code_index[f] += 1
    fixed_files = len(code_index)
    three_plus = sum(1 for v in code_index.values() if v >= 3)
    fix_all = len(fixes)
    fix_excl = sum(1 for c in fixes if c["excluded"])
    deny_total = sum(h["count"] for h in deny_by_hook.values())
    # G4 groupings, as census.py printed them.
    groups = collections.Counter(); hours = collections.Counter()
    tot_h = sum(r["seconds"] for r in proof_rows if r["runs"]) / 3600.0
    for r in proof_rows:
        k = r["clearing"]
        f = r["fails"]
        fl = r["flaky"]
        if f == 0 and not fl:
            c = "never red"
        elif (k["both"] + k["proof-only"]) > 0 and k["other-only"] == 0:
            c = "red, every clearing range changed the proof file"
        elif fl >= 3 and k["other-only"] == 0:
            c = "flaky only"
        elif k["other-only"] > 0 and (k["both"] + k["proof-only"]) == 0:
            c = "red, every clearing range left the proof file unchanged"
        elif k["other-only"] > 0:
            c = "red, mixed fixes"
        else:
            c = "red, unpaired or same-head"
        groups[c] += 1
        hours[c] += r["seconds"] / 3600.0
    g3_never = [t for t in tool_rows if t["calls"] == 0]
    g3_low = [t for t in tool_rows if 0 < t["calls"] <= 3]
    g5 = {"plainBashHookSpawnMs": spawn, "denies": deny_total}
    return {
        "G1": {"commitsFirstParent": {"harness-only": harness_only, "game": game}, "commitTypes": dict(types.most_common(6)),
               "fixCommitsTouchingClaudeOrGithub": fix_all, "fixCommitsExcludedReviewAuditFold": fix_excl,
               "fixedCodeFiles": fixed_files, "fixedCodeFilesThreeOrMore": three_plus,
               "proofFilesNow": len(proof_rows)},
        "G2": {"proofFilesAdded": len(added_tests), "byAddingCommit": dict(by_kind), "hooksAdded": len(added_hooks)},
        "G3": {"entryPointsNeverCalled": len(g3_never), "neverCalledLines": sum(t["lines"] for t in g3_never),
               "entryPointsCalledOneToThree": len(g3_low), "transcriptFiles": transcript_count},
        "G4": {"proofGroups": {k: groups[k] for k in groups}, "proofGroupHours": {k: round(hours[k], 1) for k in hours},
               "recordedHours": round(tot_h, 1)},
        "G5": g5,
        "gateLedger": gate_summary(ledger),
        "runRecords": run_records, "checkoutsRead": len(checkouts_read),
    }


def gate_summary(ledger):
    rows = []
    for r in ledger:
        a, b = r.get("startedAt"), r.get("finishedAt")
        dur = None
        try:
            if a and b:
                dur = (datetime.datetime.fromisoformat(b[:19]) - datetime.datetime.fromisoformat(a[:19])).total_seconds() / 60
        except ValueError:
            dur = None
        rows.append((r.get("mode"), r.get("status"), dur, bool(r.get("reusedFrom"))))
    counts = collections.Counter("%s/%s" % (m, s) for m, s, _, _ in rows)
    full = sorted(d for m, s, d, reused in rows if m == "full" and d is not None and not reused)
    out = {"records": len(rows), "modeStatus": dict(counts)}
    if full:
        out["fullNonReusedMinutes"] = {"n": len(full), "median": round(full[len(full) // 2], 1),
                                       "p90": round(full[int(len(full) * 0.9)], 1), "max": round(full[-1], 1)}
    return out


def read_main_check(repo, args, now):
    if args.main_check:
        path = args.main_check
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError) as exc:
            return {"status": "absent" if isinstance(exc, OSError) else "unreadable", "reason": str(exc)[:200]}
    else:
        text = git(repo, "show", "refs/remotes/origin/main-check-state:main_check.json")
        if not text:
            return {"status": "absent", "reason": "no origin/main-check-state:main_check.json"}
        try:
            state = json.loads(text)
        except ValueError as exc:
            return {"status": "unreadable", "reason": str(exc)[:200]}
    rows = state.get("rows") or []
    openr = collections.Counter()
    ages = []
    cleared = []
    cut = now - datetime.timedelta(days=30)
    for r in rows:
        if r.get("clearedAt") is None:
            openr["%s/%s" % (r.get("kind"), r.get("platform"))] += 1
            seen = _parse_ts(r.get("firstSeen"))
            if seen:
                ages.append((now - seen).total_seconds() / 86400)
        else:
            when = _parse_ts(r.get("clearedAt"))
            if when and when < cut:
                continue
            files = []
            if r.get("commit") and r.get("clearedBy"):
                files = git(repo, "diff", "--name-only", r["commit"], r["clearedBy"]).split()
            cleared.append({"name": r.get("name"), "kind": r.get("kind"), "platform": r.get("platform"),
                            "commit": r.get("commit"), "clearedBy": r.get("clearedBy"), "clearedAt": r.get("clearedAt"),
                            "files": files})
    return {"status": "ok", "checkedTo": state.get("checkedTo"),
            "open": dict(openr), "openAgeDays": {"n": len(ages), "median": round(statistics.median(ages), 1) if ages else None,
                                                 "max": round(max(ages), 1) if ages else None},
            "cleared": cleared}


def load_rulings(path):
    """Each item's latest ruling date from the census record: item -> date."""
    latest = {}
    if not path or not os.path.isfile(path):
        return latest
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            item, when = r.get("item"), r.get("date")
            if not item or not when:
                continue
            if item not in latest or str(when) > latest[item]:
                latest[item] = str(when)
    return latest


def changed_since_rulings(repo, args, now, flaky_rows, deny_by_hook, events):
    out = []
    for item, since in sorted(load_rulings(args.rulings or os.path.join(repo, ".claude",
                                                                        "census_reviews.jsonl")).items()):
        when = _parse_ts(since) or now
        base = os.path.basename(item)
        commits = git(repo, "log", "--follow", "--since=%s" % since, "--format=%h %s", "--", item).splitlines()
        reds = sum(1 for ts, _head, st, _mode in events.get(base, []) if st == "fail" and ts >= when.timestamp())
        flaky = sum(1 for row in flaky_rows
                    if row.get("proof") == base and float(row.get("ts") or 0) >= when.timestamp())
        denies = sum(1 for t in deny_by_hook.get(base, {}).get("times", []) if t.timestamp() >= when.timestamp())
        if commits or reds or flaky or denies:
            out.append({"item": item, "date": since, "commits": commits, "reds": reds, "flaky": flaky,
                        "denies": denies})
    return out


def last_finished_wall(repo, path=None):
    """Seconds the last finished main check took: close + gate + finalize from `run.wall`. None when the state is
    absent or unreadable, or its last run never finished."""
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            return None
    else:
        text = git(repo, "show", "refs/remotes/origin/main-check-state:main_check.json")
        if not text:
            return None
        try:
            state = json.loads(text)
        except ValueError:
            return None
    run = state.get("run") if isinstance(state, dict) else None
    if not isinstance(run, dict) or not run.get("end"):
        return None
    wall = run.get("wall")
    if not isinstance(wall, dict):
        return None
    parts = [wall.get(k) for k in ("close", "gate", "finalize")]
    parts = [p for p in parts if isinstance(p, (int, float))]
    return sum(parts) if parts else None


def census_line(census, now, wall=None):
    gen = _parse_ts(census.get("generatedAt"))
    if gen is None or (now - gen).total_seconds() > STALE_DAYS * 86400:
        line = "census stale (%s)" % (gen.date().isoformat() if gen else "unknown")
    else:
        n = len(census.get("changedSinceRuling") or [])
        if not n:
            return ""
        line = "census: %d item%s changed since their last ruling" % (n, "" if n == 1 else "s")
    if wall is not None:
        line += "; main check wall %.1f min" % (wall / 60.0)
    return line


TABLE_COLUMNS = {
    "proofs": ["name", "runs", "seconds", "fails", "closeReds", "flaky", "proof-only", "other-only", "both",
               "same-head", "unpaired", "fixCommits"],
    "hooks": ["name", "spawnMs", "denies", "denySessions", "fixCommits", "lines"],
    "tools": ["name", "calls", "sessions", "docRefs", "lines", "proofs", "proofSeconds", "fixCommits"],
}


def table_rows(census, kind):
    rows = []
    for r in census[kind]:
        flat = dict(r)
        for k in CLEARING_KINDS:
            flat[k] = (r.get("clearing") or {}).get(k, 0)
        rows.append(flat)
    return rows


def render(census, kind, sort_col, desc=True):
    cols = TABLE_COLUMNS[kind]
    if sort_col not in cols:
        raise SystemExit("--sort must be one of: %s" % ", ".join(cols))
    rows = table_rows(census, kind)
    rows.sort(key=lambda r: (r.get(sort_col) is None, -(r.get(sort_col) or 0) if desc else (r.get(sort_col) or 0)))
    lines = ["\t".join(cols)]
    for r in rows:
        lines.append("\t".join(str(r.get(c, "")) for c in cols))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--repo", default=".")
    p.add_argument("--ref", default="main")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--now", default=None)
    p.add_argument("--checkout", action="append")
    p.add_argument("--transcripts", action="append")
    p.add_argument("--main-check", dest="main_check", default=None)
    p.add_argument("--rulings", default=None)
    p.add_argument("--time-hooks", dest="time_hooks", action="store_true")
    p.add_argument("--out", default=None)
    p.add_argument("--table", choices=sorted(TABLE_COLUMNS), default=None)
    p.add_argument("--sort", default="runs")
    p.add_argument("--line", nargs="?", const="", default=None,
                   help="print the /session_end census line for this census file; with no value, read the state ref")
    args = p.parse_args(argv)
    if args.line is not None:
        repo = os.path.abspath(args.repo)
        if args.line:
            try:
                with open(args.line, encoding="utf-8") as fh:
                    census = json.load(fh)
            except (OSError, ValueError):
                census = {}
        else:
            text = git(repo, "show", "refs/remotes/origin/main-check-state:mechanism_census.json")
            try:
                census = json.loads(text) if text else {}
            except ValueError:
                census = {}
        line = census_line(census, _now(args), last_finished_wall(repo, args.main_check))
        if line:
            print(line)
        return 0
    census = build(args)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(census, fh, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    if args.table:
        print(render(census, args.table, args.sort))
    elif not args.out:
        print(json.dumps(census["totals"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
