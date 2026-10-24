"""The one reader for `claude -p` stream-json events: each JSON object line, in order, skipping
anything else. Per-event classifiers import it instead of parsing lines by hand.

Usage:  from stream_events import iter_stream_events
        for event in iter_stream_events(fh): ...
"""
import json


def iter_stream_events(lines):
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event
