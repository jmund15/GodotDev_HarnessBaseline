---
name: gotcha-program-spawned-shell-is-not-your-shell
description: "A shell spawned by a program does not reproduce your interactive shell — on Windows a bare `bash` from Python resolves to WSL's, a different filesystem namespace, and every $HOME/PATH-relative lookup misses while the error blames the script."
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-20T05:45:00.436Z
---

A script that works when you run it and fails when a program runs it has usually not changed
behavior — it changed *shell*. The failure is diagnostic-hostile because the error message names
the script's own subsystem (a missing binary, a missing credential) rather than the environment
that went missing.

Three layers, in the order they bite. Each was measured 2026-08-20 on Windows 11 / Git Bash /
Windows Python, dispatching `.claude/scripts/*_sidecar.sh` from `subprocess.run`.

**1. Which `bash` — the worst one, because it is silent and total.** Windows Python resolves a
bare `bash` against the Windows PATH, where the first hit is `C:\Windows\System32\bash.exe`:
**WSL's** bash. That is not a different configuration, it is a different filesystem namespace —
`HOME=/home/<user>`, PATH rooted at `/usr/bin`, the Windows drive mounted at `/mnt/c`. Every
`$HOME`-relative credential path misses, and the launcher reports "not logged in" on a machine
that is logged in. Resolve an explicit Git Bash (`C:\Program Files\Git\bin\bash.exe`) and never
pass a bare `bash` to `subprocess`.

**2. PATH — a non-login shell sources no profile.** `bash script.sh` is non-interactive and
non-login, so it inherits the *parent's* PATH rather than your profile's. Anything living in a
profile-added directory (`~/.local/bin`, an npm global bin) is invisible, and `command -v foo`
returns nothing for a tool you can run by hand. Fix it in the script, not the caller: resolve the
binary explicitly with known fallbacks, so the script behaves the same whoever starts it.

**3. HOME — set, but to the wrong root.** Follows from (1). Worth checking separately because a
script can pass its `command -v` checks and still fail on a `$HOME/.config/...` lookup, which
reads as a credential problem rather than an environment one.

**Diagnose before theorizing.** One probe (`echo HOME; echo $PATH; command -v <tool>`) run
*through the same spawn path that failed* settles all three in a single call. Reasoning about
which layer broke from the error text alone points at the wrong one — here the error said "no
auth.json", which is layer 3, while the cause was layer 1.

The project's shared resolver is `.claude/tools/sidecar_launch.py`; launcher-side binary
resolution lives in `sidecar_common.sh` (`sc_claude_bin`). Related:
[[gotcha_claude_code_entrypoint_leaks_host_auth_to_child]] — the same class one level up, where an
*inherited* env var rather than a missing one redirects a child's auth.
