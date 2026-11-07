"""Summarize a file's structure: its functions, classes and headings, with line numbers.

When Claude asks to read a huge file, an outline is usually all it needs to
find the 40 lines that matter. Building one is pattern matching, not parsing:
each language gets a few regexes for the lines that *start* something
(``def``, ``class``, ``func``, ``# Heading``). That's fast, needs no
dependencies, and degrades gracefully on syntax a parser would reject.

If the outline has more entries than fit, the least-indented ones (top-level
definitions) are kept first, then shown back in file order.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

Entry = Tuple[int, str]  # (1-based line number, the line's text)

_PY = re.compile(r"^\s*(async\s+def|def|class)\s+\w")
_JS = re.compile(
    r"^\s*(export\s+)?(default\s+)?(async\s+)?(function\*?|class|interface|enum|type\s+\w+\s*=)\s*\w*"
    r"|^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?(\([^)]*\)|\w+)\s*=>"
    r"|^\s*(public|private|protected|static|async|get|set)?\s*\w+\s*\([^)]*\)\s*\{\s*$"
)
_GO = re.compile(r"^(func|type)\s")
_RUST = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(fn|struct|enum|trait|impl|mod|macro_rules!)\b")
_JVM = re.compile(
    r"^\s*(public|private|protected|internal|static|final|abstract|open|override|data|sealed|\s)*"
    r"(class|interface|enum|record|object|fun|func|struct|protocol|extension)\s+\w"
    r"|^\s*(public|private|protected)\s+[\w<>\[\],\s]+\s+\w+\s*\([^;]*$"
)
_C = re.compile(
    r"^(struct|class|enum|union|typedef|namespace)\b"
    r"|^[A-Za-z_][\w\s\*&:<>,]*\s[\*&]*[A-Za-z_][\w:]*\s*\([^;]*$"
)
_RUBY = re.compile(r"^\s*(def|class|module)\s")
_PHP = re.compile(r"^\s*((abstract|final|public|private|protected|static)\s+)*(function|class|interface|trait)\s")
_SHELL = re.compile(r"^\s*(function\s+\w+|\w+\s*\(\)\s*\{)")
_SQL = re.compile(r"^\s*(create|alter)\s+(or\s+replace\s+)?(table|view|function|procedure|index|trigger)\b", re.I)
_MD = re.compile(r"^#{1,6}\s")
_YAML = re.compile(r"^[\w\"'-][^:#]*:(\s|$)")
_JSON = re.compile(r'^\s{0,4}"[^"]+"\s*:')
_HTML = re.compile(r"^\s*<(h[1-6]|section|header|footer|main|nav|form|table|script|style)\b", re.I)
_CSS = re.compile(r"^[^\s{}][^{}]*\{\s*$|^@(media|supports|keyframes)\b")

_BY_EXTENSION = {
    ".py": _PY, ".pyi": _PY,
    ".js": _JS, ".jsx": _JS, ".mjs": _JS, ".cjs": _JS, ".ts": _JS, ".tsx": _JS,
    ".go": _GO,
    ".rs": _RUST,
    ".java": _JVM, ".kt": _JVM, ".kts": _JVM, ".scala": _JVM, ".swift": _JVM, ".cs": _JVM,
    ".c": _C, ".h": _C, ".cc": _C, ".cpp": _C, ".hpp": _C, ".m": _C, ".mm": _C,
    ".rb": _RUBY,
    ".php": _PHP,
    ".sh": _SHELL, ".bash": _SHELL, ".zsh": _SHELL,
    ".sql": _SQL,
    ".md": _MD, ".mdx": _MD, ".markdown": _MD,
    ".yaml": _YAML, ".yml": _YAML,
    ".json": _JSON,
    ".html": _HTML, ".htm": _HTML, ".vue": _HTML, ".svelte": _HTML,
    ".css": _CSS, ".scss": _CSS, ".less": _CSS,
}

MAX_LINE_CHARS = 120


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _spread(items: List[Entry], k: int) -> List[Entry]:
    """Pick ``k`` items evenly spaced across the list (always the first and last)."""
    if k >= len(items):
        return list(items)
    if k == 1:
        return [items[0]]
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


def limit_entries(entries: List[Entry], k: int) -> List[Entry]:
    """Choose ``k`` entries: shallowest levels first, each level spread evenly.

    Taking simply the first ``k`` would only describe the top of the file. A
    3,000-line module with 300 functions would show its first 700 lines and
    nothing else. Spreading the picks keeps the outline a map of the whole file.
    """
    by_level: dict = {}
    for e in entries:
        by_level.setdefault(_indent(e[1]), []).append(e)
    chosen: List[Entry] = []
    for level in sorted(by_level):
        room = k - len(chosen)
        if room <= 0:
            break
        chosen += _spread(by_level[level], room)
    return sorted(chosen)


def outline_lines(lines: List[str], extension: str, max_entries: int = 80) -> List[Entry]:
    """Return outline entries for ``lines`` of a file with ``extension``."""
    pattern = _BY_EXTENSION.get(extension.lower())
    entries: List[Entry] = []
    if pattern is not None:
        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                entries.append((i, line.rstrip()))

    if not entries:
        # Unknown file type, or nothing matched: fall back to the
        # unindented, non-blank lines, which tend to be where things start.
        entries = [(i, l.rstrip()) for i, l in enumerate(lines, 1)
                   if l.strip() and _indent(l) == 0 and not l.lstrip().startswith(("}", ")", "]"))]

    if len(entries) > max_entries:
        entries = limit_entries(entries, max_entries)

    return [(n, t if len(t) <= MAX_LINE_CHARS else t[:MAX_LINE_CHARS] + " ...") for n, t in entries]


def format_outline(entries: List[Entry]) -> str:
    width = len(str(entries[-1][0])) if entries else 1
    return "\n".join(f"{n:>{width}}: {text}" for n, text in entries)


def read_head(path: str, max_lines: int, max_bytes: int = 20_000_000) -> Optional[List[str]]:
    """Read up to ``max_lines`` lines of a text file, or None if it looks binary."""
    try:
        with open(path, "rb") as f:
            if b"\x00" in f.read(8192):
                return None
        lines: List[str] = []
        read = 0
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
                read += len(line)
                if len(lines) >= max_lines or read >= max_bytes:
                    break
        return lines
    except OSError:
        return None


def outline_file(path: str, max_entries: int = 80, max_lines: int = 200_000) -> List[Entry]:
    lines = read_head(path, max_lines) or []
    return outline_lines(lines, os.path.splitext(path)[1], max_entries)
