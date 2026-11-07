#!/usr/bin/env python3
"""PreToolUse hook: stop whole-file reads of huge files and send an outline instead.

Claude Code runs this before every Read call:

    {"tool_name": "Read", "tool_input": {"file_path": "/abs/path", "offset": 10, "limit": 50},
     "session_id": "...", ...}

Decision rules, in order:
    1. A read with an explicit offset or limit is always allowed: Claude is
       already reading just a part.
    2. Images, PDFs, notebooks and binary files are allowed: Read handles
       those itself.
    3. If the read would fit budgets.file_read, allow it.
    4. Otherwise deny it, and put the file's outline in the reason so Claude
       can pick the lines it needs.
    5. Escape hatch: if Claude asks for the same whole file again right after
       a denial, allow it. It evidently needs the whole thing, and a guard
       that can trap Claude in a loop is worse than no guard.

"Allow" here means "no opinion": we print nothing, and Claude Code's normal
permission rules decide. Sieve never auto-approves anything.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sieve import ledger  # noqa: E402
from sieve.config import Config, load_config  # noqa: E402
from sieve.outline import format_outline, limit_entries, outline_lines, read_head  # noqa: E402
from sieve.tokens import estimate_tokens  # noqa: E402

QUICK_ALLOW_BYTES_PER_TOKEN = 1.5

# Claude Code's Read returns at most this many lines when no limit is given,
# so that is the most a whole-file read can cost.
READ_DEFAULT_LINES = 2000

# Read prefixes each line with its number ("  123→"), which costs tokens too.
LINE_NUMBER_TOKENS = 2

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz", ".tiff",
    ".pdf", ".ipynb",
    ".zip", ".gz", ".tar", ".jar", ".so", ".dylib", ".dll", ".exe", ".bin", ".wasm",
}

GUARD_DIR = "guard"
MAX_OUTLINE_ENTRIES = 80


def _denials_path(data_root: str, session_id: str) -> str:
    folder = os.path.join(data_root, GUARD_DIR)
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:120]
    return os.path.join(folder, safe + ".json")


def _load_denials(path: str) -> Dict[str, float]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_denials(path: str, denials: Dict[str, float]) -> None:
    with open(path, "w") as f:
        json.dump(denials, f)


def handle(event: Dict[str, Any], cfg: Config, data_root: str) -> Optional[Dict[str, Any]]:
    """Return a deny decision with an outline, or None to stay out of the way."""
    if not cfg.enabled or event.get("tool_name") != "Read":
        return None
    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None \
            or tool_input.get("pages") is not None:
        return None  # rule 1
    ext = os.path.splitext(path)[1].lower()
    if ext in SKIP_EXTENSIONS:
        return None  # rule 2

    budget = cfg.budget("file_read")
    try:
        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
    except OSError:
        return None  # missing file: let Read report the error itself

    # Quick exit from the file size alone, without opening the file. Even the
    # densest text (logs, numbers) costs more than 1.5 bytes per token, so a
    # file this small can't be over budget and is never opened.
    if size <= budget * QUICK_ALLOW_BYTES_PER_TOKEN:
        return None

    lines = read_head(path, max_lines=200_000)
    if lines is None:
        return None  # binary: rule 2

    shown = lines[:READ_DEFAULT_LINES]
    read_cost = estimate_tokens("\n".join(shown)) + LINE_NUMBER_TOKENS * len(shown)
    if read_cost <= budget:
        return None  # rule 3

    session = str(event.get("session_id", "unknown"))
    denials_file = _denials_path(data_root, session)
    denials = _load_denials(denials_file)
    if denials.get(path) == mtime:
        denials.pop(path)  # rule 5: asked again for the same, unchanged file
        _save_denials(denials_file, denials)
        return None

    total = len(lines)
    found = outline_lines(lines, ext, max_entries=10**9)
    entries = limit_entries(found, MAX_OUTLINE_ENTRIES)
    partial = "" if len(entries) == len(found) else \
        f", {len(entries)} of {len(found)} entries spread across the file; Grep for a name to find the rest"
    more = "" if total <= READ_DEFAULT_LINES else \
        f" A whole-file read would only show the first {READ_DEFAULT_LINES:,} lines anyway."
    reason = (
        f"[sieve] {os.path.basename(path)} has {total:,} lines (~{read_cost:,} tokens to read), "
        f"over the {budget:,}-token read budget.{more} Read only the part you need with "
        f"offset/limit, or Grep for it.\n"
        + (f"Outline (line: text{partial}):\n{format_outline(entries)}\n" if entries else "")
        + "If you really need the whole file, request the same read again and it will be allowed."
    )

    denials[path] = mtime
    _save_denials(denials_file, denials)
    ledger.record(data_root, {
        "session": session,
        "tool": "Read",
        "before": read_cost,
        "after": estimate_tokens(reason),
        "file": path[-200:],
        "estimated": True,  # Claude will still read some part of the file
    })

    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def main() -> int:
    event = None
    try:
        event = json.load(sys.stdin)
        ledger.log_event("pre_read", event)
        project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
        reply = handle(event, load_config(project_dir=project), ledger.data_dir())
        if reply:
            sys.stdout.write(json.dumps(reply))
    except Exception as exc:  # fail-open, but leave a trace
        ledger.log_event("pre_read", event, exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
