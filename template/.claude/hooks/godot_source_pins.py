#!/usr/bin/env python3
"""Godot documentation pins for the WebFetch routing classifier.

`routing_classifier` loads this module where the project adopted the godot layer. Two
docs.godotengine.org shapes miss the version pin, both about VERSION rather than
reachability (the host is only intermittently Cloudflare-gated, so it is not blanket-banned):
a class page is generated from XML the cache holds at the exact engine pin, and `/en/stable/`
is a moving alias that cannot be pinned at all. A version-pinned non-class URL (`/en/4.7/...`)
is fine.
"""
from __future__ import annotations


def classify_webfetch(url: str):
    """`(rule, reason)` when the URL bypasses a pinned Godot source, else None."""
    low = url.lower()
    if "docs.godotengine.org" not in low:
        return None
    if "class_" in low:
        return (
            "webfetch-godot-class-page-not-pinned",
            "a rendered class page cannot be pinned to the engine — read the "
            "version-pinned XML it is generated from under .claude/cache/godot-docs "
            "(build: .claude/scripts/godot_docs_cache.sh)",
        )
    if "/en/stable/" in low:
        return (
            "webfetch-godot-stable-alias-unpinnable",
            "/en/stable/ is a moving alias and cannot satisfy the version-pin rule — "
            "use context7 /websites/godotengine_en_4_7, or a /en/4.7/ URL",
        )
    return None
