#!/usr/bin/env python3
"""Claude Code status line — prompt-cache diagnostics.

Reads the session JSON on stdin and renders one line:

    Fable 5 ·high | {{PROJECT_NAME}} | ctx 42% | cache 98% (r487k w8k) | sess 96%
    | warm 48m | boot 69k | miss 1.2M x3 tools_changed | $12.34

`boot` is the session's opening write — the prefix every session pays for once
(system prompt, tool schemas, CLAUDE.md, memory index).

`cache%` is read/(read+write) for the LAST API call. A healthy turn appends a
little and reads the rest, so it sits near 100%; a rebuilt prefix inverts it.

`warm`/`cold` count down the cached prefix, and appear on the Anthropic
transport only: the TTL behind them is chosen client-side and no other provider
accepts it. Elsewhere the same slot shows `idle` — time since the last request,
which every cache policy measures from, leaving the window to the reader.

`miss` accumulates the re-cache cost of the turns that lost the prefix. Its
source is the CLI's own `prompt_cache` ledger where the payload carries one:
that ledger excludes the rebuilds a compaction ANNOUNCED and names the cause of
the last real miss, neither of which is recoverable from token counts. Without
the block the segment falls back to this script's created>read heuristic and is
marked `est`.

Never raises: a status line that crashes shows nothing, which is worse than a
status line that shows less.
"""

import json
import os
import sys
import tempfile
import time

# Claude Code hands this script a cp1252 stdout on Windows. Any non-ASCII byte
# then raises UnicodeEncodeError mid-print — including one that arrives as data,
# such as an accented directory name in workspace.current_dir. Reconfigure so
# that degrades to a replacement char instead of killing the line.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# current_usage is null before the first API call and again after /compact,
# so every read below has to tolerate absence rather than assume shape.
RESET = "\033[0m"
DIM = "\033[2m"
SEP = f"{DIM}|{RESET}"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def human(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


def state_path(session_id):
    # session_id is stable for the session and unique across concurrent
    # sessions. A pid would change on every invocation and defeat the file.
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(tempfile.gettempdir(), f"cc-cachestat-{safe or 'unknown'}.json")


def effort_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(tempfile.gettempdir(), f"cc-effort-{safe or 'unknown'}.txt")


def publish_effort(session_id, effort):
    """Hand the live effort level to the session's hooks.

    The session model cannot read its own effort and no hook payload carries it, so without this
    file session_model_rails.py can only tell the model to assume nothing. Rewritten only on a
    change, because the status line repaints on a timer."""
    if not session_id or not effort:
        return
    try:
        path = effort_path(session_id)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if fh.read().strip() == str(effort):
                    return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(effort))
    except OSError:
        pass


def prune_stale(keep_days=7):
    """Drop state files from sessions that ended long ago.

    One file per session accumulates otherwise. Called only when a session
    writes its first entry, so this costs one directory scan per session
    rather than one per status-line repaint.
    """
    cutoff = time.time() - keep_days * 86400
    try:
        tmp = tempfile.gettempdir()
        for name in os.listdir(tmp):
            if not (name.startswith("cc-cachestat-") and name.endswith(".json")):
                continue
            full = os.path.join(tmp, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass
    except Exception:
        pass


def snapshot_rate_limits(raw):
    """Normalize the payload's rate_limits block for the state file.

    Present only for Pro/Max subscribers, or behind a gateway that sets a spend
    limit, and only after the first API response; each window may be
    independently absent. Returns None when nothing usable arrived, so the
    caller can keep last-known-good instead of overwriting a real capture with
    nulls.

    spend_limit is captured but not rendered: budget_posture.py reads this file
    by window name, so a window absent here can never reach the gate.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for win in ("five_hour", "seven_day", "spend_limit"):
        w = raw.get(win)
        if not isinstance(w, dict):
            continue
        entry = {}
        used = w.get("used_percentage")
        resets = w.get("resets_at")
        if isinstance(used, (int, float)):
            entry["used_percentage"] = used
        if isinstance(resets, (int, float)):
            entry["resets_at"] = resets
        if entry:
            out[win] = entry
    if not out:
        return None
    out["captured_at"] = time.time()
    return out


def session_link(session_id, transcript_path):
    """The session id, hyperlinked to this session's own directory.

    Prefers `<project>/<session_id>/` — the subagent and workflow tree. That
    directory is created only once a session actually dispatches something, so
    it is missing for roughly half of them (measured: 393 transcripts, 204
    dirs), and linking to it unconditionally would give a dead link on the
    other half. Falling back to the transcript's parent keeps the link live;
    that is also the directory holding the transcript itself, which is all
    there is to open when nothing was dispatched.

    OSC 8 is emitted rather than a bare path because the path is ~100 columns
    and the id is 8. A terminal that does not understand the sequence still
    prints the id, so the segment degrades to the no-link version rather than
    to nothing. Set CC_STATUSLINE_NO_LINK=1 to force that plainer form when a
    terminal mismeasures the escape and disturbs the line.
    """
    full = str(session_id or "")
    short = full[:8]
    if not short:
        return None
    plain = f"{DIM}{short}{RESET}"
    if os.environ.get("CC_STATUSLINE_NO_LINK"):
        return plain
    try:
        parent = os.path.dirname(str(transcript_path or ""))
        if not parent:
            return plain
        own = os.path.join(parent, full)
        target = own if full and os.path.isdir(own) else parent
        url = "file:///" + target.replace("\\", "/").lstrip("/")
        return f"\033]8;;{url}\033\\{plain}\033]8;;\033\\"
    except Exception:
        return plain


def prompt_cache_view(raw):
    """Normalize the CLI's native `prompt_cache` ledger, or None when unusable.

    Shipped from Claude Code 2.1.x, populated after the first API response. It
    beats this script's created>read heuristic on the one distinction the
    heuristic cannot make: a prefix rewrite a compaction ANNOUNCED
    (`expected_rebuilds`) versus one that simply lost the cache (`misses`). It
    also names the cause of the last real miss.

    `caching_observed is not True` means the provider reported no cache tokens
    at all — the engine saying its own ledger is empty, not that the cache is
    cold. Returning None there hands the render back to the heuristic rather
    than printing a confident 0.

    Booleans are read with `is`: `warm: false` is a real value and must not
    collapse into absent the way a truthiness test would collapse it.
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("caching_observed") is not True:
        return None
    ratio = raw.get("hit_ratio")
    cause = raw.get("last_miss_cause")
    causes = cause.get("causes") if isinstance(cause, dict) else None
    return {
        "misses": raw.get("misses") or 0,
        # miss_recache_tokens is the spend attributable to the misses;
        # cache_write_tokens is every write including the boot prefix, so it is
        # only a stand-in when the narrower figure is absent.
        "miss_tokens": raw.get("miss_recache_tokens") or raw.get("cache_write_tokens") or 0,
        "hit_pct": ratio * 100 if isinstance(ratio, (int, float)) else None,
        "ttl": raw.get("ttl"),
        # A future timestamp; `warm` cannot substitute, being derived from the
        # last response rather than the clock.
        "expires_at": raw.get("expires_at"),
        # Time of the last request, recovered exactly rather than estimated: the
        # ledger builds expiry as lastActivity + the TTL it also exports, so
        # subtracting one from the other inverts it. Unlike the expiry itself
        # this survives leaving the Anthropic transport — every cache policy
        # measures from last use, whatever its window.
        "last_activity": (
            raw["expires_at"] - {"5m": 300, "1h": 3600}[raw["ttl"]]
            if isinstance(raw.get("expires_at"), (int, float))
            and raw.get("ttl") in ("5m", "1h")
            else None
        ),
        "warm": raw.get("warm"),
        "cause": str(causes[0]) if isinstance(causes, list) and causes else None,
    }


def accumulate(session_id, created, read, fresh, ctx_pct=None, rate_limits=None):
    """Fold this turn into the session's rebuild tally.

    The status line re-runs on events, so the same current_usage can arrive
    several times. Keying on the (write, read, input) triple makes repeat
    invocations idempotent — only a genuinely new API call moves the counters.

    ctx_pct rides along so agents can Read the session's context gauge from
    this state file (the model cannot self-measure context usage).
    """
    path = state_path(session_id)
    fingerprint = f"{created}:{read}:{fresh}"
    first_write = not os.path.exists(path)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {}

    if first_write:
        prune_stale()

    def rl_changed():
        if rate_limits is None:
            return False
        old = state.get("rate_limits") or {}
        strip = lambda d: {k: v for k, v in d.items() if k != "captured_at"}
        return strip(old) != strip(rate_limits)

    if state.get("last") == fingerprint:
        dirty = False
        if ctx_pct is not None and state.get("context_pct") != ctx_pct:
            state["context_pct"] = ctx_pct
            dirty = True
        if rl_changed():
            state["rate_limits"] = rate_limits
            dirty = True
        if dirty:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(state, fh)
            except Exception:
                pass
        return (
            state.get("rebuilt_tokens", 0),
            state.get("rebuilds", 0),
            state.get("cold_tokens", 0),
            state.get("total_read", 0),
            state.get("total_created", 0),
        )

    rebuilt = state.get("rebuilt_tokens", 0)
    count = state.get("rebuilds", 0)
    cold = state.get("cold_tokens", 0)
    total_read = state.get("total_read", 0)
    total_created = state.get("total_created", 0)

    # A normal turn writes the new increment and reads the whole prefix, so
    # reads dominate. Writes exceeding reads means the prefix itself was
    # rewritten — a miss, not an append.
    #
    # The session's first cache write is the exception: it is the unavoidable
    # cold prefix, and counting it would open every session at x1. Gate on
    # `cold`, not on an empty state — a first turn can arrive with both counters
    # at zero, consuming the empty state and leaving the real cold write to be
    # tallied as a miss.
    if not cold and created > 0:
        cold = created
    elif created > read and created > 0:
        rebuilt += created
        count += 1

    total_read += read
    total_created += created

    # Last-known-good: a turn without rate_limits keeps the previous capture
    # rather than overwriting it with nothing — at weekly granularity a stale
    # capture beats an empty one, and the consumer states its age.
    rl_keep = rate_limits if rate_limits is not None else state.get("rate_limits")

    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "last": fingerprint,
                    "rebuilt_tokens": rebuilt,
                    "rebuilds": count,
                    "cold_tokens": cold,
                    "total_read": total_read,
                    "total_created": total_created,
                    "context_pct": ctx_pct if ctx_pct is not None else state.get("context_pct"),
                    "rate_limits": rl_keep,
                },
                fh,
            )
    except Exception:
        pass

    return rebuilt, count, cold, total_read, total_created


def external_model_tag(model_block, cwd):
    """`(label, externally_billed)` for the session's model.

    Two answers, deliberately separate. `label` is the identity tag to render
    (`pro $0.435/1M`), the only surface that survives a mid-session /model
    switch — a launch banner goes stale the moment the picker is used. It is
    None when there is nothing worth adding, INCLUDING when a priceless alias is
    already a substring of the display name, which otherwise renders the model
    twice: `gpt-6-astra astra`.

    `externally_billed` is the billing fact, and the cost segment reads it
    instead of the label. Collapsing the two hid the flag whenever the label was
    suppressed, on exactly the models whose cost figure is wrong.

    Rows on the `anthropic` transport are neither: the registry is the whole
    ladder roster, so membership no longer implies a model Claude Code cannot
    price. Test the transport, not membership.

    This file is USER-GLOBAL and runs in every project, while the registry is
    per-project, so it is located by walking up from the payload's cwd.

    NEVER raises: this process also writes cc-cachestat-<session>.json, the sole
    input budget_posture.py reads to gate paid dispatch.
    """
    try:
        display = str((model_block or {}).get("display_name") or "")
        wanted = [
            str(v) for v in (
                (model_block or {}).get("id"),
                (model_block or {}).get("display_name"),
            ) if v
        ]
        if not wanted or not cwd:
            return None, False
        here = os.path.abspath(str(cwd))
        for _ in range(24):
            candidate = os.path.join(here, ".claude", "reference", "external_models.json")
            if os.path.isfile(candidate):
                with open(candidate, encoding="utf-8") as handle:
                    for entry in (json.load(handle) or {}).get("models") or []:
                        if entry.get("id") in wanted or entry.get("alias") in wanted:
                            if entry.get("transport") == "anthropic":
                                return None, False
                            rate = (entry.get("price") or {}).get("cacheMissPer1M")
                            alias = str(entry.get("alias") or entry.get("id"))
                            # Drop an alias the display name already carries as
                            # a WORD — `gpt-6-astra astra`, `muse muse` — while
                            # keeping the price, which the display name never
                            # supplies. Word-level, not substring: alias `pro`
                            # has to survive a display name like `Prometheus`.
                            words = display.lower().replace("-", " ").replace("_", " ").split()
                            if alias.lower() in words:
                                alias = ""
                            label = " ".join(
                                x for x in (alias, f"${rate}/1M" if rate is not None else "") if x
                            )
                            return (label or None), True
                return None, False
            parent = os.path.dirname(here)
            if parent == here:
                return None, False
            here = parent
    except Exception:
        return None, False
    return None, False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("")
        return

    parts = []

    workspace = data.get("workspace") or {}
    cwd = workspace.get("current_dir") or data.get("cwd")

    model_block = data.get("model") or {}
    model = model_block.get("display_name")
    # The live effort level, which the session itself cannot see. Written out so a SessionStart
    # hook can state it (session_model_rails.py); no hook payload carries it.
    effort = (data.get("effort") or {}).get("level")
    publish_effort(data.get("session_id"), effort)
    # `external` is read again by the cost segment; both must exist with no model.
    tag, external = None, False
    if model:
        tag, external = external_model_tag(model_block, cwd)
        segment = f"{CYAN}{model}{RESET}"
        if tag:
            segment += f" {DIM}{tag}{RESET}"
        if effort:
            segment += f" {DIM}·{effort}{RESET}"
        parts.append(segment)
    elif effort:
        parts.append(f"{DIM}{effort}{RESET}")

    # `worktree` is populated for a --worktree session, `workspace.git_worktree`
    # for a cwd that merely sits inside a linked worktree. Neither implies the
    # other, so both are consulted.
    tree = data.get("worktree") or {}
    tree_name = tree.get("name") or workspace.get("git_worktree")
    branch = tree.get("branch")
    if cwd:
        segment = os.path.basename(str(cwd).rstrip("/\\")) or str(cwd)
        if tree_name:
            # A worktree directory is usually named after the worktree, which
            # would render `sync-pr108@sync-pr108`. Drop the duplicate half: a
            # leading `@` still reads as "not the main checkout", which is the
            # whole signal.
            if segment == tree_name:
                segment = ""
            segment += f"{DIM}@{tree_name}{RESET}"
            # Printed only when the branch has drifted from the worktree it
            # was created for — a detached or re-pointed HEAD, which is the
            # state worth seeing. Containment, not inequality: a branch is
            # routinely the name plus an affix (`worktree-<name>`), which
            # differs as a string while saying nothing new. `main` or
            # `HEAD-detached` do not contain the name, so those still show.
            if branch and str(tree_name) not in str(branch):
                segment += f" {DIM}({branch}){RESET}"
        parts.append(segment)

    # Set only by /rename, so absent unless the session was deliberately named.
    session_name = data.get("session_name")
    if session_name:
        parts.append(f"{DIM}{session_name}{RESET}")

    link = session_link(data.get("session_id"), data.get("transcript_path"))
    if link:
        parts.append(link)

    window = data.get("context_window") or {}
    pct = window.get("used_percentage")
    if isinstance(pct, (int, float)):
        colour = RED if pct >= 80 else YELLOW if pct >= 60 else ""
        parts.append(f"ctx {colour}{pct:.0f}%{RESET if colour else ''}")

    ledger = prompt_cache_view(data.get("prompt_cache"))

    usage = window.get("current_usage")
    if not usage:
        # Null before the first call, and between /compact and the next call.
        parts.append(f"{DIM}cache warming{RESET}")
    else:
        read = usage.get("cache_read_input_tokens") or 0
        created = usage.get("cache_creation_input_tokens") or 0
        fresh = usage.get("input_tokens") or 0
        cached_total = read + created

        if cached_total <= 0:
            parts.append(f"{DIM}cache warming{RESET}")
        else:
            ratio = read / cached_total * 100
            colour = GREEN if ratio >= 90 else YELLOW if ratio >= 50 else RED
            parts.append(
                f"cache {colour}{ratio:.0f}%{RESET} "
                f"{DIM}(r{human(read)} w{human(created)}){RESET}"
            )

        rebuilt, count, cold, total_read, total_created = accumulate(
            data.get("session_id", "unknown"),
            created,
            read,
            fresh,
            pct if isinstance(pct, (int, float)) else None,
            snapshot_rate_limits(data.get("rate_limits")),
        )
        # Session-lifetime hit ratio — the durable counterpart to the per-call
        # cache%. The per-call number snaps back to ~100% one turn after any
        # miss; this one keeps the damage visible for the whole session. The
        # engine's ratio also charges uncached input, which the local one
        # cannot see, so prefer it wherever the ledger arrived.
        if ledger and ledger["hit_pct"] is not None:
            sess = ledger["hit_pct"]
        else:
            session_total = total_read + total_created
            sess = total_read / session_total * 100 if session_total > 0 else None
        if sess is not None:
            colour = GREEN if sess >= 90 else YELLOW if sess >= 50 else RED
            segment = f"{DIM}sess{RESET} {colour}{sess:.0f}%{RESET}"
            # Every TTL-derived reading below is Anthropic-only. Claude Code
            # picks the TTL itself and writes it into an Anthropic
            # `cache_control` block, then derives expiry as
            # last-activity + that-same-TTL. No provider confirms it. Send that
            # to an OpenAI-backed model and it describes a parameter the API
            # never accepts — caching there is automatic, the sole supported
            # value is 30m, and no response reports expiry back. A `cold 4m` on
            # a prefix the provider holds for ~30m is worse than showing
            # nothing, so `external` suppresses the whole group.
            ttl_trustworthy = ledger and not external

            # `ttl` is the TIER the last request wrote, not a countdown: 5m
            # means this session lost the 1h cache and every gap over five
            # minutes now pays a rebuild. It is a standing condition.
            if ttl_trustworthy and ledger["ttl"] == "5m":
                segment += f" {YELLOW}5m{RESET}"

            # `expires_at` is the actual deadline. Rendering it as a countdown
            # against the local clock is what makes it visible while IDLE —
            # the case that matters, because an idle prefix is the one that
            # goes cold, and an idle session produces no API response to
            # refresh any server-computed flag. Needs statusLine.refreshInterval
            # set, or nothing repaints to advance the number.
            parts.append(segment)

            # Runway on the cached prefix, at whatever magnitude it has. Shown
            # always rather than past a threshold: a warning that appears only
            # once the cache is nearly gone tells you when it is already too
            # late to decide anything.
            expiry = ledger["expires_at"] if ttl_trustworthy else None
            if isinstance(expiry, (int, float)):
                left = expiry - time.time()
                if left <= 0:
                    parts.append(f"{RED}cold{RESET}")
                else:
                    minutes = int(left // 60)
                    colour = YELLOW if left < 300 else DIM
                    parts.append(
                        f"{colour}warm {minutes}m{RESET}" if minutes
                        else f"{colour}warm <1m{RESET}"
                    )
            elif ledger and ledger["last_activity"] is not None:
                # No trustworthy expiry off the Anthropic transport, so report
                # the input every cache policy keys on instead and let the
                # operator apply their provider's window. Uncoloured on purpose:
                # colouring it would assert a threshold this side does not know.
                idle = time.time() - ledger["last_activity"]
                minutes = int(idle // 60)
                parts.append(
                    f"{DIM}idle {minutes}m{RESET}" if minutes else f"{DIM}idle <1m{RESET}"
                )

        if cold:
            parts.append(f"{DIM}boot {human(cold)}{RESET}")

        if ledger:
            if ledger["misses"]:
                colour = RED if ledger["misses"] >= 3 else YELLOW
                cause = f" {DIM}{ledger['cause']}{RESET}" if ledger["cause"] else ""
                parts.append(
                    f"miss {colour}{human(ledger['miss_tokens'])} "
                    f"x{ledger['misses']}{RESET}{cause}"
                )
        elif count:
            # No ledger: the created>read fallback, which counts a compaction's
            # rebuild as a miss. Marked so the two are never read as one number.
            colour = RED if count >= 3 else YELLOW
            parts.append(f"miss {colour}{human(rebuilt)} x{count}{RESET} {DIM}est{RESET}")

    cost = (data.get("cost") or {}).get("total_cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        # Claude Code has no price table for a non-Anthropic model, so it bills
        # those tokens at Anthropic rates: measured on sidecar children, 39-73x
        # the real figure, a MODEL- and cache-dependent multiple that cannot be
        # scaled back into a usable number. Flag it rather than print a dollar
        # amount that reads as this model's spend. Gate on `external`, never on
        # `tag` — a suppressed label must not suppress the warning.
        if external:
            parts.append(f"{YELLOW}${cost:.2f} anthropic-rate{RESET}")
        else:
            parts.append(f"{DIM}${cost:.2f}{RESET}")

    print(f" {SEP} ".join(parts))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("")
