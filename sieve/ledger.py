"""Remember what Sieve did: full copies of compressed output, and a savings log.

Two kinds of data live in the plugin's private data folder:

    outputs/<session>/<tool_use_id>.log   the full, uncompressed output, so
                                          nothing is ever truly lost
    ledger.jsonl                          one line per compression:
                                          {"ts", "session", "tool", "before", "after"}

The ledger is append-only JSON Lines: each write is one small line, so two
hooks running at once can't corrupt each other's entries, and the report can
stream it without loading everything.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterator, Optional

LEDGER_FILE = "ledger.jsonl"
OUTPUTS_DIR = "outputs"

# Keep disk use bounded: saved outputs older than this, or beyond this count,
# are deleted when new ones are written.
MAX_SAVED_OUTPUTS = 300
MAX_AGE_SECONDS = 7 * 24 * 3600


def data_dir(env: Optional[Dict[str, str]] = None) -> str:
    """Where Sieve keeps its data. Claude Code provides CLAUDE_PLUGIN_DATA."""
    env = os.environ if env is None else env
    path = env.get("CLAUDE_PLUGIN_DATA") or os.path.join(
        os.path.expanduser("~"), ".claude", "plugins", "data", "sieve")
    os.makedirs(path, exist_ok=True)
    return path


def _safe(name: str) -> str:
    """Make an id safe to use as a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "unknown")[:120]


def save_full_output(root: str, session_id: str, tool_use_id: str, text: str) -> str:
    """Write the uncompressed output to disk and return its path."""
    folder = os.path.join(root, OUTPUTS_DIR, _safe(session_id))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _safe(tool_use_id) + ".log")
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        f.write(text)
    prune(os.path.join(root, OUTPUTS_DIR))
    return path


def prune(outputs_root: str, max_files: int = MAX_SAVED_OUTPUTS,
          max_age: float = MAX_AGE_SECONDS, now: Optional[float] = None) -> int:
    """Delete saved outputs that are too old or too many. Returns how many."""
    now = time.time() if now is None else now
    files = []
    for folder, _dirs, names in os.walk(outputs_root):
        for name in names:
            path = os.path.join(folder, name)
            try:
                files.append((os.path.getmtime(path), path))
            except OSError:
                pass
    files.sort(reverse=True)  # newest first
    removed = 0
    for i, (mtime, path) in enumerate(files):
        if i >= max_files or now - mtime > max_age:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    # remove session folders that are now empty
    for folder, dirs, names in os.walk(outputs_root, topdown=False):
        if folder != outputs_root and not dirs and not names:
            try:
                os.rmdir(folder)
            except OSError:
                pass
    return removed


def record(root: str, entry: Dict[str, Any]) -> None:
    """Append one compression event to the ledger."""
    entry = {"ts": round(time.time(), 3), **entry}
    with open(os.path.join(root, LEDGER_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def read(root: str) -> Iterator[Dict[str, Any]]:
    """Yield ledger entries, skipping any damaged lines."""
    try:
        with open(os.path.join(root, LEDGER_FILE), encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except FileNotFoundError:
        return
