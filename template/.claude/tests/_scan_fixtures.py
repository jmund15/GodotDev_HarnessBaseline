"""Identity, secret and placeholder tokens that scanner proofs plant in temp trees.

Each value is assembled from fragments, so no committed line holds a whole token
that a scanner matches. Pure: no I/O.
"""
from __future__ import annotations

HOME_USER = "zq" + "-user"
ABSENT_TERM = "zq" + "xjv" + "absent"

SK_KEY = "sk-" + "a1B2c3D4e5F6g7H8"
AWS_KEY = "AKI" + "AABCDEFGHIJKLMNOP"
GITHUB_TOKEN = "ghp_" + "a" * 36
SLACK_TOKEN = "xox" + "b-1234567890-abcdef"
PEM_HEADER = "-----BEGIN" + " PRIVATE KEY-----"
CLAUDE_OAUTH_TOKEN = "sk-ant-" + "planted"


def placeholder(name: str) -> str:
    return "{{" + name + "}}"
