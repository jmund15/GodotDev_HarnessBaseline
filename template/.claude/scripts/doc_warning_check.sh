#!/usr/bin/env bash
# Fails when the build emits XML-documentation defects.
#
# These warnings are real and always have been; `-consoleLoggerParameters:ErrorsOnly`
# (the project's normal build flag) discards them before anyone sees them. Measured
# 2026-08-12: 276 defects shipping in a green build.
#
# `-t:Rebuild` is load-bearing. An incremental build skips compilation and reports zero
# warnings — a green run that proves nothing. Costs a full recompile; that is the price
# of the check being true.
#
# Usage: doc_warning_check.sh [--quiet]
# Exit 0 = no defects. Exit 1 = defects (list on stdout). Exit 2 = build itself failed.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJ="$REPO/{{PROJECT_NAME}}.csproj"
QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

# Codes that mean "this doc comment is broken", not "this doc comment is missing".
# The list matches {{PROJECT_NAME}}.csproj:24-29, which names the doc family it deliberately
# leaves visible: CS1570/1572/1574/1711/1734/CS0419.
# CS1591 (missing doc on public member) is deliberately absent: it would demand a ///
# on every public member, the opposite of this codebase's default-to-none discipline.
CODES='CS1587|CS1574|CS1734|CS1570|CS1572|CS1711|CS0419'

# Third-party source we do not author, and generated source whose edits are erased.
EXCLUDE='examples_dd3d|debug_draw_3d'

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

if ! dotnet build "$PROJ" -t:Rebuild -v:normal >"$LOG" 2>&1; then
    if grep -qE '[0-9]+ Error\(s\)' "$LOG" && ! grep -qE '\b0 Error\(s\)' "$LOG"; then
        echo "doc_warning_check: BUILD FAILED — cannot assess doc warnings." >&2
        grep -E 'error CS' "$LOG" | head -20 >&2
        exit 2
    fi
fi

HITS="$(grep -oE "[A-Za-z]:\\\\[^(]+\([0-9]+,[0-9]+\): warning ($CODES)" "$LOG" \
        | sed -E 's/^[0-9]+>//' \
        | grep -vE "$EXCLUDE" \
        | sort -u)"

if [[ -z "$HITS" ]]; then
    [[ $QUIET -eq 1 ]] || echo "doc_warning_check: 0 doc defects."
    exit 0
fi

COUNT="$(printf '%s\n' "$HITS" | wc -l | tr -d ' ')"
echo "doc_warning_check: $COUNT XML-documentation defects (expected 0)."
echo
echo "By code:"
printf '%s\n' "$HITS" | grep -oE "warning CS[0-9]+" | sort | uniq -c | sort -rn
echo
echo "By file:"
printf '%s\n' "$HITS" | sed -E 's/\([0-9]+,[0-9]+\).*//' | sort | uniq -c | sort -rn | head -30
echo
echo "CS1587 = doc comment not on a valid element (never reaches the XML sidecar; on an"
echo "         [Export] this is a silently-missing Inspector tooltip)."
echo "CS1574 = <see cref> names a member that does not resolve."
echo "CS1734 = <paramref> names a parameter that does not exist."
echo
echo "Full list: rerun without --quiet, or see the doctrine at rules/csharp_patterns.md."
exit 1
