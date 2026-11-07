"""Shrink long tool output to fit a token budget while keeping what matters.

The pipeline runs five stages, cheapest and safest first, and stops as soon
as the text fits the budget, so it never removes more than it has to:

    1. clean     strip color codes, collapse progress bars, squeeze blank lines
    2. dedupe    collapse runs of lines with the same "shape"
    3. clip      shorten giant single lines (minified JSON, base64 blobs)
    4. score     rate every line's importance (errors > summaries > warnings)
    5. fit       keep head + tail, then add the most important lines
                 (with context) until the budget is used up -- a greedy
                 knapsack over lines, where value = score and cost = tokens

Stages 1-3 lose almost no information. Stages 4-5 only run when the output is
still too big, and they always leave a visible marker where lines were cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from .tokens import chars_for_tokens, estimate_tokens


@dataclass
class CompressionResult:
    text: str
    tokens_before: int
    tokens_after: int
    lines_before: int
    lines_after: int
    stages: List[str] = field(default_factory=list)

    @property
    def compressed(self) -> bool:
        return bool(self.stages)

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after


# ---------------------------------------------------------------- stage 1: clean

# ANSI escape sequences: colors (CSI ... m), cursor moves, and OSC titles/links.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def clean(text: str) -> List[str]:
    """Remove terminal noise. Returns the cleaned text as a list of lines."""
    text = _ANSI.sub("", text)
    out: List[str] = []
    blank_run = 0
    for line in text.split("\n"):
        # A progress bar redraws itself with carriage returns on one line:
        # "10%\r20%\r...100%". Only the final state is worth keeping.
        if "\r" in line:
            parts = [p for p in line.split("\r") if p.strip()]
            line = parts[-1] if parts else ""
        line = line.rstrip()
        if not line:
            blank_run += 1
            if blank_run > 1:  # keep at most one blank line in a row
                continue
        else:
            blank_run = 0
        out.append(line)
    while out and not out[-1]:
        out.pop()
    while out and not out[0]:
        out.pop(0)
    return out


# --------------------------------------------------------------- stage 2: dedupe

# A line's "shape" is the line with its variable parts replaced by
# placeholders. "PASS test_17 (3ms)" and "PASS test_204 (12ms)" share the
# shape "PASS test_# (#ms)", so a run of them can collapse to one line.
_HEX = re.compile(r"\b(?:0x)?[0-9a-f]{7,}\b", re.IGNORECASE)
_NUM = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


def shape(line: str) -> str:
    # Whitespace is normalized too, so "[  9%]" and "[ 10%]" match.
    return _SPACES.sub(" ", _NUM.sub("#", _HEX.sub("H", line))).strip()


def dedupe(lines: List[str], min_run: int = 3) -> List[str]:
    """Collapse runs of ``min_run`` or more same-shape lines.

    Keeps the first and last line of each run (they show the range, such as
    "downloading 1/300" ... "downloading 300/300") and puts a count between.
    """
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i + 1
        s = shape(lines[i])
        if s:  # never collapse blank lines here; clean() already did
            while j < n and shape(lines[j]) == s:
                j += 1
        run = j - i
        if run >= min_run:
            out.append(lines[i])
            out.append(f"    [... {run - 2} more similar lines ...]")
            out.append(lines[j - 1])
        else:
            out.extend(lines[i:j])
        i = j
    return out


# ----------------------------------------------------------------- stage 3: clip


def clip_long_lines(lines: List[str], max_chars: int = 400) -> List[str]:
    """Cut giant lines to their start and end, noting how much was removed."""
    keep_head = max_chars * 2 // 3
    keep_tail = max_chars // 4
    out = []
    for line in lines:
        if len(line) > max_chars:
            cut = len(line) - keep_head - keep_tail
            line = f"{line[:keep_head]} [... {cut} chars clipped ...] {line[-keep_tail:]}"
        out.append(line)
    return out


# ---------------------------------------------------------------- stage 4: score

# Ordered by importance. A line gets the score of the first pattern it matches.
_RULES = [
    (10, re.compile(
        r"\b(error|errors|exception|traceback|fatal|panic|failed|failure|"
        r"segmentation fault|undefined reference|cannot|could not|couldn't|"
        r"not found|no such file|permission denied|refused|aborted|timed? ?out)\b"
        r"|^E\s|^FAIL\b|\bERR!|[✗✘❌]",
        re.IGNORECASE,
    )),
    (8, re.compile(
        r"\b\d+ (passed|failed|errors?|warnings?|skipped|tests?|deselected)\b"
        r"|\b(summary|total|exit code|exited with|returned \d|build (success|failed))\b"
        r"|^=+ .* =+$",
        re.IGNORECASE,
    )),
    (6, re.compile(r'^\s+File ".+", line \d+|^\s+at .+[(:]\d+|^\s*-->\s')),  # stack frames
    (5, re.compile(r"\b(warn|warning|deprecated|deprecation)\b", re.IGNORECASE)),
    (3, re.compile(r"^\s+\[\.\.\. \d+ more similar lines \.\.\.\]$")),  # our own counts
]


def score_line(line: str) -> int:
    for score, pattern in _RULES:
        if pattern.search(line):
            return score
    return 0


# ------------------------------------------------------------------ stage 5: fit


def _omitted(count: int) -> str:
    return f"[... {count} lines omitted ...]"


def fit_to_budget(
    lines: List[str],
    budget: int,
    head: int = 15,
    tail: int = 40,
    context: int = 2,
) -> List[str]:
    """Pick the lines to keep so the result fits ``budget`` tokens.

    1. Always keep the first ``head`` lines (what command ran, how it started)
       and the last ``tail`` lines (where summaries and final errors live).
    2. Rank every other line by importance score.
    3. Greedily add the best lines, each with ``context`` lines around it,
       while the total stays under budget.
    """
    n = len(lines)
    cost = [estimate_tokens(line) + 1 for line in lines]  # +1 for the newline
    marker_cost = estimate_tokens(_omitted(99999)) + 1

    # If head + tail alone blow the budget, shrink them (tail keeps priority).
    def base_cost(h: int, t: int) -> int:
        idx = set(range(min(h, n))) | set(range(max(0, n - t), n))
        return sum(cost[i] for i in idx) + marker_cost

    while (head > 3 or tail > 5) and base_cost(head, tail) > budget:
        head = max(3, head * 2 // 3)
        tail = max(5, tail * 2 // 3)

    keep = set(range(min(head, n))) | set(range(max(0, n - tail), n))
    used = sum(cost[i] for i in keep)

    # Candidates: important lines in the middle. Highest score first; among
    # equal scores, later lines first (later errors are usually the cause).
    candidates = [(score_line(lines[i]), i) for i in range(n) if i not in keep]
    candidates = [c for c in candidates if c[0] > 0]
    candidates.sort(key=lambda c: (-c[0], -c[1]))

    for _score, i in candidates:
        window = range(max(0, i - context), min(n, i + context + 1))
        new = [k for k in window if k not in keep]
        extra = sum(cost[k] for k in new) + marker_cost  # a new gap may appear
        if used + extra > budget:
            continue  # this one doesn't fit; a smaller one later still might
        keep.update(new)
        # Conservative: reserve room for a gap marker every time. Over-reserving
        # can only leave the result a little under budget, never over it.
        used += extra

    # Rebuild in original order, replacing each gap with one marker line.
    out: List[str] = []
    gap = 0
    for i in range(n):
        if i in keep:
            if gap:
                out.append(_omitted(gap))
                gap = 0
            out.append(lines[i])
        else:
            gap += 1
    if gap:
        out.append(_omitted(gap))
    return out


def _hard_truncate(text: str, budget: int) -> str:
    """Last resort when even the trimmed lines don't fit: cut by characters."""
    limit = chars_for_tokens(budget)
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} chars omitted ...]\n{text[-tail:]}"


# -------------------------------------------------------------------- pipeline


def compress(
    text: str,
    budget: int = 2000,
    *,
    head: int = 15,
    tail: int = 40,
    context: int = 2,
    max_line_chars: int = 400,
) -> CompressionResult:
    """Run the pipeline on ``text`` and return the result with stats."""
    tokens_before = estimate_tokens(text)
    lines_before = text.count("\n") + 1 if text else 0

    def done(lines: List[str], stages: List[str]) -> CompressionResult:
        out = "\n".join(lines)
        return CompressionResult(out, tokens_before, estimate_tokens(out),
                                 lines_before, len(lines), stages)

    if tokens_before <= budget:
        return CompressionResult(text, tokens_before, tokens_before,
                                 lines_before, lines_before, [])

    stages: List[str] = []
    lines = clean(text)
    stages.append("clean")
    if estimate_tokens("\n".join(lines)) <= budget:
        return done(lines, stages)

    lines = dedupe(lines)
    stages.append("dedupe")
    if estimate_tokens("\n".join(lines)) <= budget:
        return done(lines, stages)

    lines = clip_long_lines(lines, max_line_chars)
    stages.append("clip")
    if estimate_tokens("\n".join(lines)) <= budget:
        return done(lines, stages)

    lines = fit_to_budget(lines, budget, head=head, tail=tail, context=context)
    stages.append("fit")
    result = done(lines, stages)
    if result.tokens_after > budget:
        out = _hard_truncate(result.text, budget)
        stages.append("truncate")
        result = CompressionResult(out, tokens_before, estimate_tokens(out),
                                   lines_before, out.count("\n") + 1, stages)
    return result
