#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit dispatcher entry: its members, contract and fault posture are `_chain.EVENTS["prompt"]`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _chain  # noqa: E402

if __name__ == "__main__":
    _chain.main("prompt")
