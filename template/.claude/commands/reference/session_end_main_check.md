---
disable-model-invocation: true
---

# /session_end step 7c: main check

Fetch the state first: `git fetch -q origin main-check-state`. Then:
- Print this session's open rows: `python3 .claude/scripts/main_check.py --rows --session <the Claude-Session value this session's commits carry>`. Fix or hand off each row (`change_control` §A red on main).
- Print the health line: `python3 .claude/scripts/main_check.py --status`. It prints nothing when `main` is checked and current.
- Print the census line: `python3 .claude/tools/mechanism_census.py --line`. It is a fact line: the census's staleness or changed-item count, the code and test lines with their change, and each suite's last wall (main check, harness proofs, publish, C# gate; `<suite>: no record` where a suite has none).
- When any of these names something the owner must act on, print one owner line: the open row count (`main_check.py --rows`, no `--session`), the health line and the census line when it names a stale census or a changed item. Print nothing otherwise.
