#!/usr/bin/env bash
# publish.sh — one-time: lift this directory out of the source project into its own
# git repo and push it.
#
#   ./publish.sh git@github.com:you/harness-baseline.git [main]
#   ./publish.sh https://github.com/you/harness-baseline.git [main]
#
# Gates on a clean separation audit, then creates a clean copy in a temp dir (so the
# source project's history stays behind), initializes git, commits, and pushes.
# Create the (empty) GitHub repo first.
set -euo pipefail
REMOTE="${1:?usage: ./publish.sh <remote-url> [branch]}"
BRANCH="${2:-main}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Gate: never publish a baseline that fails its own separation audit.
echo "Running separation audit..."
python3 "$HERE/tools/audit_baseline.py"

# Warn if the remote already has commits — publishing assumes a fresh, empty repo.
if git ls-remote --heads "$REMOTE" 2>/dev/null | grep -q .; then
  echo "warning: $REMOTE already has branches — this script targets an empty repo." >&2
  echo "         Push may be rejected or create a divergent history. Continue? [y/N]" >&2
  read -r reply
  [[ "$reply" == [yY] ]] || { echo "aborted."; exit 1; }
fi

STAGE="$(mktemp -d)/harness-baseline"
trap 'rm -rf "$(dirname "$STAGE")"' EXIT  # clean the temp stage on any exit path

mkdir -p "$STAGE"
cp -r "$HERE/." "$STAGE/"
rm -rf "$STAGE/.git"

# Inherit the committer identity from the source repo — the fresh temp repo has no
# local identity, and a shell without a visible global gitconfig would otherwise fail
# with "empty ident name".
NAME="$(git -C "$HERE" config user.name 2>/dev/null || true)"
EMAIL="$(git -C "$HERE" config user.email 2>/dev/null || true)"

git -C "$STAGE" init -b "$BRANCH"
git -C "$STAGE" add -A
git -C "$STAGE" \
  -c user.name="${NAME:-Harness Baseline}" \
  -c user.email="${EMAIL:-noreply@users.noreply.github.com}" \
  commit -m "feat: initial harness baseline (extracted from PushinPotions)"
git -C "$STAGE" remote add origin "$REMOTE"
# Force the gh CLI credential helper for this push, clearing any inherited helper
# (e.g. Git Credential Manager) first. GitHub rejects password auth over HTTPS;
# gh supplies the OAuth token non-interactively. Requires `gh auth login` done.
# Run this script from Git Bash, not a PowerShell-launched sh: git invokes the
# helper via `sh -c`, which must find `gh` on PATH (PowerShell's sh does not).
git -C "$STAGE" \
  -c credential.helper= \
  -c 'credential.helper=!gh auth git-credential' \
  push -u origin "$BRANCH"

trap - EXIT  # published successfully — keep the working clone for inspection
echo
echo "Published to $REMOTE ($BRANCH). Working clone: $STAGE"
echo "Consumer projects' baseline.lock.json should point baseline_repo at this URL."
