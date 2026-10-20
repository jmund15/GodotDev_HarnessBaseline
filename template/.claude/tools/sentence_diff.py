"""Sentence-level comparison of two markdown texts, shared by the harness-growth hook and `citers.py`.

`sentences` is a file's rule sentences; `removed` is the ones one text lost against another, each marked
reworded or not; `homes` finds the other file that carries a removed sentence's fragment. Pure functions:
a caller supplies the texts and the corpus, so a hook and a tool read their own sources.
"""
import re

LIST_MARKER = re.compile(r"^(?:[-*] |\d+\. |\|\s*)")
SENTENCE_BREAK = re.compile(r"(?<=\.) +|; +")
MIN_SENTENCE_WORDS = 3
FRAGMENT_WORDS = 8
WORD_EDGE = ".,;:!?()[]{}`'\"*_"
# plans/ and generated/ are execution/derived docs, not loaded doctrine — density rules don't bind them.
# `rubrics`: benchmark judge rubrics are sealed instrument text a judge reads, pinned by sha256.
EXCLUDED = ("auto-memory", "scratch", "tests", "worktrees", "__pycache__", "cache", ".cache", "benchmark_runs",
            "plans", "generated", "rubrics")


def in_doctrine_scope(rel):
    """True for a repo-relative `.claude/**/*.md` path outside EXCLUDED: the files a removed sentence can be re-homed in."""
    parts = rel.split("/")
    return rel.endswith(".md") and parts[0] == ".claude" and not any(seg in EXCLUDED for seg in parts[1:-1])


def sentences(text):
    """Rule sentences of a markdown file: body lines outside code fences and headings, split on
    `. ` / `; ` / line ends, list markers dropped, whitespace normalized."""
    found, in_fence = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#") or not set(line) - set("|-: "):
            continue
        for part in SENTENCE_BREAK.split(LIST_MARKER.sub("", line)):
            part = " ".join(part.split()).rstrip("| ")
            if len(part.split()) >= MIN_SENTENCE_WORDS:
                found.append(part)
    return found


def fragment(sentence):
    """The longest FRAGMENT_WORDS-word window of a sentence: the most distinctive search key."""
    words = sentence.split()
    if len(words) <= FRAGMENT_WORDS:
        return sentence
    windows = (" ".join(words[i:i + FRAGMENT_WORDS]) for i in range(len(words) - FRAGMENT_WORDS + 1))
    return max(windows, key=len)


def words(sentence):
    return {w.strip(WORD_EDGE).lower() for w in sentence.split()} - {""}


def reworded(sentence, added):
    """True when more than half of the sentence's words reappear in one of the `added` word sets."""
    own = words(sentence)
    return any(len(own & other) * 2 > len(own) for other in added)


def removed(before_text, now_text):
    """Sentences in `before_text` and absent from `now_text`, in file order, each paired with whether a
    sentence `now_text` added rewords it."""
    before, now = sentences(before_text), sentences(now_text)
    kept, current = set(before), set(now)
    added = [words(s) for s in dict.fromkeys(now) if s not in kept]
    return [(s, reworded(s, added)) for s in dict.fromkeys(s for s in before if s not in current)]


def homes(gone, corpus, rel):
    """{sentence: the file in `corpus` ([(path, whitespace-normalized text)]) other than `rel` carrying its fragment}."""
    found = {}
    for sentence in gone:
        key = fragment(sentence)
        home = next((path for path, body in corpus if path != rel and key in body), None)
        if home:
            found[sentence] = home
    return found
