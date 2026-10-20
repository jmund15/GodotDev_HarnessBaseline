"""Words a model-facing quota line must never carry: the retired pace vocabulary.

The plan figures print quota left and window left (`provider_bands.window_figures`), never the derived
numbers below. A proof matches them as lower-case substrings of the line it checks.
"""

BANNED_DISPLAY_WORDS = ("pressure", "room", "spare", "bound by", "unused at even pace", "ahead of pace")


def banned_in(text):
    """The banned display words `text` carries."""
    lowered = text.lower()
    return [word for word in BANNED_DISPLAY_WORDS if word in lowered]
