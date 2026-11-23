"""Slice one deterministic hook text into parts, each a separately registered command under the client's cap.

The client saves a hook command's output past `HOOK_TEXT_CAP` to a file and shows the agent a 2,000-character
preview, and it caps each registered command on its own. A hook whose text can pass the cap registers N commands
(`<hook>.py --part 1` .. `--part N` in `settings.base.json`); each computes the same whole text and prints its slice
through `part_of`.

A part holds whole lines only, counted in UTF-8 bytes (the stricter reading of the cap), so no line is cut and a part
that arrives alone is still a set of complete lines. The slices joined in order are the whole text. A part past the end
of the text prints nothing. A text that needs more than N parts prints N parts, the last ending with the caller's
`overflow(dropped_lines)` note in place of the lines that did not fit, so the session is told and nothing drops
silently. One line wider than the cap sits alone in its part, over the cap: the caller's proof reds on it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The client's cap on hook text it injects (additionalContext, stdout); past it the client shows a 2,000-character
# preview and a saved-file path instead (code.claude.com/docs/en/hooks).
HOOK_TEXT_CAP = 10_000
NL = chr(10)


def _size(line):
    return len(line.encode("utf-8"))


def _chunks(lines, cap):
    """Greedy runs of consecutive lines, each at most `cap` bytes (a line over the cap sits alone)."""
    runs, run, used = [], [], 0
    for line in lines:
        width = _size(line)
        if run and used + width > cap:
            runs.append(run)
            run, used = [], 0
        run.append(line)
        used += width
    if run:
        runs.append(run)
    return runs


def part_of(text, n, parts, cap=HOOK_TEXT_CAP, overflow=None):
    """Part `n` (1-based) of `text` split across `parts` commands: whole lines, at most `cap` UTF-8 bytes.

    `overflow(dropped)` returns the note, ending in a newline, that replaces the `dropped` lines which fit no part.
    """
    lines = text.splitlines(keepends=True)
    runs = _chunks(lines, cap)
    if n < 1 or n > parts:
        return ""
    if len(runs) <= parts:
        return "".join(runs[n - 1]) if n <= len(runs) else ""
    if n < parts:
        return "".join(runs[n - 1])
    note = overflow or (lambda dropped: "[overflow] %d lines did not fit the registered parts." % dropped + NL)
    kept_before = sum(len(r) for r in runs[:parts - 1])
    room = cap - _size(note(len(lines)))
    last = _chunks(lines[kept_before:], room)[0]
    return "".join(last) + note(len(lines) - kept_before - len(last))


def bounded_report(lead, rows, full_path, cap=HOOK_TEXT_CAP, reserve=0):
    """`lead` (verdict and instruction lines, each ending in a newline), then as many whole `rows` as fit
    `cap - reserve` UTF-8 bytes in all, then a count line naming `full_path`. Rows are lines without a newline.

    Nothing is cut when every row fits. A cut writes `lead` and every row to `full_path`, so the count line's
    pointer reaches the whole list. A file that cannot be written never stops the report: the count line then
    says the full list could not be saved."""
    body = "".join(row + NL for row in rows)
    total = len(rows)

    def count_line(dropped, saved=True):
        tail = "full list: " + full_path if saved else "full list could not be saved"
        return "  ... %d of %d lines not shown; %s%s" % (dropped, total, tail, NL)

    room = cap - reserve - _size(lead) - max(_size(count_line(total)), _size(count_line(total, False)))
    kept_text, kept, _ = head_of(body, room)
    if kept == total:
        return lead + body
    try:
        _write_whole(full_path, lead + body)
    except OSError:
        return lead + kept_text + count_line(total - kept, False)
    return lead + kept_text + count_line(total - kept)


def bounded_decision(wrap, lead, rows, full_path, cap=HOOK_TEXT_CAP):
    """The JSON document `wrap(report_text)` returns, at most `cap` UTF-8 bytes, carrying `bounded_report`'s text.

    `wrap` puts the text into a hook JSON field and serializes it; JSON escaping makes the document wider than
    the text, so the reserve grows by the overshoot until the whole document fits."""
    reserve = _size(wrap(""))
    out = wrap(bounded_report(lead, rows, full_path, cap, reserve))
    for _ in range(8):
        over = _size(out) - cap
        if over <= 0:
            break
        reserve += over
        out = wrap(bounded_report(lead, rows, full_path, cap, reserve))
    return out


def _write_whole(path, text):
    from _hook_state import replace_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    replace_path(tmp, path)


def head_of(text, room):
    """The whole leading lines of `text` that fit `room` UTF-8 bytes: `(kept_text, kept_lines, total_lines)`.

    For a hook that prints one output under the cap and points at a file for the rest, where `part_of` would
    register more commands. A first line wider than `room` keeps nothing."""
    lines = text.splitlines(keepends=True)
    kept, used = 0, 0
    while kept < len(lines) and used + _size(lines[kept]) <= room:
        used += _size(lines[kept])
        kept += 1
    return "".join(lines[:kept]), kept, len(lines)
