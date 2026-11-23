#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse Write|Edit dispatcher entry: its members, contract and fault posture are `_chain.EVENTS["pre_edit"]`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chain  # noqa: E402

if __name__ == "__main__":
    _chain.main("pre_edit")
