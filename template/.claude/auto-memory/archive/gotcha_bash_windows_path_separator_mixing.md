---
name: gotcha-bash-windows-path-separator-mixing
description: Combining $USERPROFILE (backslash-style) with forward-slash path construction in one Bash command produces an unresolvable mixed-separator path
metadata: 
  node_type: memory
  retire_when: 
    - review-by: 2027-03-18
  type: project
  originSessionId: dfe017b3-b84e-41bf-8517-359f6b15b971
  modified: 2026-09-22T00:31:39.615Z
---

`$USERPROFILE` on Windows expands to a backslash path (`C:\Users\<name>`). Writing
`"$USERPROFILE/.claude/scripts/foo.sh"` mixes that backslash prefix with forward slashes; `sed`,
`ls` and some other POSIX tools fail to resolve the result even though the same logical path
resolves fine when built consistently (`/c/Users/<name>/.claude/scripts/foo.sh`, or
`$USERPROFILE\.claude\scripts\foo.sh` quoted for Windows-style tools).

**Why:** hit this 3 times in one session (`sed`/`ls` all reporting "No such file or directory" on a
file that existed) before switching to one consistent separator convention.

**How to apply:** in the Bash tool (Git Bash), build paths as `/c/Users/...` throughout, or resolve
`$USERPROFILE`/`$HOME` once and never concatenate a literal `/` onto it inline — pick one convention
per command and don't mix.
