"""Size vocabulary: the triage fields name one size hierarchy, and a retired size still parses from an old trailer."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))

import triage  # noqa: E402


def test_size_values_are_one_hierarchy():
    assert triage.FIELDS["size"] == ("one-context", "milestones", "parts"), triage.FIELDS["size"]


def test_retired_size_values_still_parse_from_old_trailers():
    for old, new in (("multi-part", "milestones"), ("programme", "parts")):
        line = ("Triage: reach=local reversibility=clean design=settled grounding=verified "
                "oracle=gate size=%s" % old)
        fields, error = triage.parse_trailer(line)
        assert error is None and fields["size"] == new, (old, fields, error)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
