"""Unchanged transcript scans reuse locked per-session state, including misses."""
import builtins
import importlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import _hook_state as state

# Cut from live probe 10749e62-d94f-4520-a85f-12bcd127afe9; paths and ids omitted.
READ = {'type': 'assistant', 'message': {'content': [
    {'input': {'file_path': 'Probe.cs'}, 'name': 'Read', 'type': 'tool_use'}]}}


def budget():
    saved = sys.stdout
    try:
        sys.stdout = sys.__stdout__
        return importlib.import_module('budget_posture')
    finally:
        sys.stdout = saved


def setup(tmp_path, monkeypatch):
    bp = budget()
    monkeypatch.setenv('HARNESS_HOOK_STATE_DIR', str(tmp_path / 'state'))
    transcript = tmp_path / 'session.jsonl'
    transcript.write_bytes((json.dumps(READ) + '\n').encode())
    payload = {'session_id': 'bp-cache', 'transcript_path': str(transcript)}
    reads = []

    def counted(path, *args, **kwargs):
        if os.path.abspath(path) == str(transcript):
            reads.append(path)
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(bp, 'open', counted, raising=False)
    return bp, transcript, payload, reads


def test_unchanged_negative_scan_is_cached(tmp_path, monkeypatch):
    bp, transcript, payload, reads = setup(tmp_path, monkeypatch)
    assert bp.dispatch_seen(payload) is False
    assert bp.dispatch_seen(payload) is False
    assert len(reads) == 1
    assert state.read_json_salvage(state.state_path(payload['session_id']))


def test_append_rearms_and_sees_dispatch(tmp_path, monkeypatch):
    bp, transcript, payload, reads = setup(tmp_path, monkeypatch)
    assert bp.dispatch_seen(payload) is False
    dispatch = json.loads(json.dumps(READ))
    dispatch['message']['content'][0].update(name='Agent', input={'prompt': 'review'})
    with transcript.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(dispatch) + '\n')
    assert bp.dispatch_seen(payload) is True
    assert bp.dispatch_seen(payload) is True
    assert len(reads) == 2


def test_same_size_rewrite_invalidates_by_mtime(tmp_path, monkeypatch):
    bp, transcript, payload, reads = setup(tmp_path, monkeypatch)
    assert bp.dispatch_seen(payload) is False
    before = transcript.stat()
    text = transcript.read_text().replace('"Read"', '"Agent"').rstrip('\n')
    assert len(text.encode()) == before.st_size
    transcript.write_bytes(text.encode())
    os.utime(transcript, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert bp.dispatch_seen(payload) is True
    assert len(reads) == 2


def test_cache_is_single_entry_and_preserves_sibling_state(tmp_path, monkeypatch):
    bp, transcript, payload, reads = setup(tmp_path, monkeypatch)
    path = state.state_path(payload['session_id'])
    state.update_json_locked(path, lambda row: row.update(peer='untouched'))
    assert bp.dispatch_seen(payload) is False
    other = tmp_path / 'other.jsonl'
    other.write_text(json.dumps(READ).replace('"Read"', '"Agent"'))
    assert bp.dispatch_seen(dict(payload, transcript_path=str(other))) is True
    assert bp.dispatch_seen(payload) is False
    assert len(reads) == 2
    assert state.read_json_salvage(path)['peer'] == 'untouched'


def test_missing_transcript_does_not_cache_false(tmp_path, monkeypatch):
    bp, transcript, payload, reads = setup(tmp_path, monkeypatch)
    transcript.unlink()
    assert bp.dispatch_seen(payload) is False
    transcript.write_text(json.dumps(READ).replace('"Read"', '"Agent"'))
    assert bp.dispatch_seen(payload) is True
