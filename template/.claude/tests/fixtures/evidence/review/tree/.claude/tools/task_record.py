
def _session_transcript(session_id):
    import session_digest
    return str(session_digest.projects_dir(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
               / (session_id + ".jsonl"))


def record_condition(task_id, kind, quote, effect, session_id=None, transcript=None):
    """Store a run condition against the owner turn that holds `quote`. Refuses an unknown kind, an empty
    quote, an unreadable transcript, or a quote no owner turn of the session holds. A delegate
    (`CLAUDE_CODE_SIDECAR` set) is stored with `delegate` true, never refused."""
    if kind not in CONDITION_KINDS:
        raise ConditionRefused("condition kind must be one of %s" % ", ".join(CONDITION_KINDS))
    quote = (quote or "").strip()
    if not quote:
        raise ConditionRefused("condition needs the owner's words in --quote")
    session_id = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not transcript:
        if not session_id:
            raise ConditionRefused("no session id: set CLAUDE_CODE_SESSION_ID or pass the transcript")
        transcript = _session_transcript(session_id)
    turns = _owner_turns(transcript)
    if turns is None:
        raise ConditionRefused("cannot read the session transcript %s" % transcript)
    holders = [uuid for uuid, text in turns if quote in text]
    if not holders:
        raise ConditionRefused("no owner turn in this session holds the quote")

    def mutation(state):
        _require(state, task_id)
        rows = state.setdefault("conditions", [])
        row = {"id": "C%d" % (len(rows) + 1), "kind": kind, "quote": quote, "effect": effect,
               "message_id": holders[-1], "session_id": session_id, "transcript": str(transcript),
               "delegate": bool(os.environ.get("CLAUDE_CODE_SIDECAR")), "at": now()}
        rows.append(row)
        return row
