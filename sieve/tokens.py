"""Estimate how many tokens a piece of text will cost.

Why estimate instead of counting exactly?
    Claude's tokenizer isn't available offline. Asking the API to count would
    add a network round trip to every tool call, which is too slow for a hook
    that must finish in milliseconds. For budgeting we only need to be roughly
    right (within about 10-20%), so a fast heuristic is the better trade.

How the heuristic works
    A tokenizer splits text into common chunks. Short, common words are
    usually one token; long or unusual strings break into several. So instead
    of a flat "characters / 4", we split the text into kinds of pieces and
    give each kind its own cost:

        piece                  example          estimated cost
        ---------------------  ---------------  -------------------------------
        word (letters)         "compress"       1 token per ~6 letters, min 1
        number                 "2026"           1 token per ~3 digits
        punctuation run        "->", "=====",   1 token per ~2 characters
        non-ASCII character    "é", "日", "🙂"   ~1 token each
        newline                "\\n"             ~0.5 token
        indentation (2+ sp.)   "        "       ~1 token per run

    Single spaces cost nothing: tokenizers attach them to the next word.

    The result is multiplied by CALIBRATION, a single knob we can tune later
    against real token counts from Claude Code's session logs.
"""

from __future__ import annotations

import math
import os
import re

# One knob to correct systematic over- or under-estimation.
CALIBRATION = 1.0

# Average bytes per token for typical source code and logs. Used when we only
# know a file's size (from the file system) and don't want to read it.
BYTES_PER_TOKEN_FILE = 3.6

# One regex splits text into the piece kinds from the table above. Each named
# group is one kind; finditer walks the text once, so the cost is O(n).
_PIECES = re.compile(
    r"(?P<word>[A-Za-z]+)"
    r"|(?P<number>[0-9]+)"
    r"|(?P<newline>\n)"
    r"|(?P<indent>[ \t]{2,})"
    r"|(?P<space>[ \t\r\f\v])"
    r"|(?P<punct>[!-/:-@\[-`{-~]+)"  # ASCII punctuation, in runs
    r"|(?P<other>[^\x00-\x7f])"  # any non-ASCII character
    r"|(?P<control>[\x00-\x1f\x7f])"
)


def estimate_tokens(text: str) -> int:
    """Return an estimated token count for ``text``."""
    if not text:
        return 0

    total = 0.0
    for match in _PIECES.finditer(text):
        kind = match.lastgroup
        length = match.end() - match.start()
        if kind == "word":
            total += max(1, math.ceil(length / 6))
        elif kind == "number":
            total += math.ceil(length / 3)
        elif kind == "punct":
            total += math.ceil(length / 2)
        elif kind == "other":
            total += 1
        elif kind == "newline":
            total += 0.5
        elif kind == "indent":
            total += 1
        elif kind == "control":
            total += 1
        # "space": free, it merges into the next word's token

    return max(1, round(total * CALIBRATION))


def estimate_file_tokens(path: str) -> int:
    """Estimate a file's tokens from its size alone, without reading it.

    This is what lets the large-file guard decide in microseconds: asking the
    file system for a size is instant even for a 50 MB file.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    return round(size / BYTES_PER_TOKEN_FILE * CALIBRATION)


def chars_for_tokens(tokens: int) -> int:
    """Rough inverse: how many characters fit in a token budget.

    Uses a conservative ratio, so text cut to this length usually stays under
    budget. Handy for quick truncation before the exact estimate runs.
    """
    return max(0, int(tokens * 3.5))
