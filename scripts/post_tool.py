#!/usr/bin/env python3
"""PostToolUse hook: compress a shell command's output before Claude sees it.

Claude Code runs this script after every successful Bash call. It sends the
call as JSON on stdin:

    {"tool_name": "Bash", "tool_input": {"command": "npm test", ...},
     "tool_response": {"stdout": "...", "stderr": "...", "interrupted": false,
                       "isImage": false},
     "session_id": "...", "tool_use_id": "...", ...}

If the output is over budget, we print JSON telling Claude Code to swap in a
compressed version:

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "updatedToolOutput": {...same shape, smaller...}}}

If it's under budget, or anything at all goes wrong, we print nothing and
exit 0, and Claude sees the original output. The hook must never break a
session (fail-open).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sieve import ledger  # noqa: E402
from sieve.compress import compress  # noqa: E402
from sieve.config import Config, load_config  # noqa: E402
from sieve.tokens import estimate_tokens  # noqa: E402

# Tokens set aside for the one-line note we add to compressed output.
NOTE_RESERVE = 80

# When stdout and stderr together are over budget, a stream that uses at most
# this share of the budget is kept whole (stderr is usually short and holds
# the errors, so it should survive intact).
SMALL_STREAM_SHARE = 0.4


def split_budget(budget: int, out_tokens: int, err_tokens: int):
    """Decide how many tokens stdout and stderr may each use."""
    if err_tokens <= budget * SMALL_STREAM_SHARE:
        return budget - err_tokens, err_tokens  # keep stderr whole
    if out_tokens <= budget * SMALL_STREAM_SHARE:
        return out_tokens, budget - out_tokens  # keep stdout whole
    return budget // 2, budget - budget // 2  # both big: split evenly


def handle(event: Dict[str, Any], cfg: Config, data_root: str) -> Optional[Dict[str, Any]]:
    """Return the hook's JSON reply, or None to leave the output alone."""
    if not cfg.enabled or event.get("tool_name") != "Bash":
        return None
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isImage"):
        return None
    command = str((event.get("tool_input") or {}).get("command", ""))
    if cfg.is_passthrough(command):
        return None

    stdout = response.get("stdout") or ""
    stderr = response.get("stderr") or ""
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        return None

    budget = cfg.budget("bash_output")
    out_tokens, err_tokens = estimate_tokens(stdout), estimate_tokens(stderr)
    before = out_tokens + err_tokens
    if before <= budget:
        return None  # under budget: pass through untouched

    usable = max(budget - NOTE_RESERVE, 100)
    out_budget, err_budget = split_budget(usable, out_tokens, err_tokens)
    opts = cfg.compress_options
    new_out = compress(stdout, out_budget, **opts) if out_tokens > out_budget else None
    new_err = compress(stderr, err_budget, **opts) if err_tokens > err_budget else None

    after = (new_out.tokens_after if new_out else out_tokens) + \
            (new_err.tokens_after if new_err else err_tokens)
    if after >= before:
        return None

    session = str(event.get("session_id", "unknown"))
    tool_use_id = str(event.get("tool_use_id", "unknown"))
    saved_path = None
    if cfg.save_full_output:
        saved_path = ledger.save_full_output(
            data_root, session, tool_use_id,
            f"$ {command}\n\n--- stdout ---\n{stdout}\n\n--- stderr ---\n{stderr}\n")

    where = f"grep the full output at {saved_path}, or rerun" if saved_path else "rerun"
    note = (f"[sieve: output compressed ~{before:,} -> ~{after:,} tokens; errors, warnings "
            f"and summaries kept. For more, {where} with `# sieve:off`.]")

    updated = dict(response)  # keep every field Claude Code sent, same shape
    if new_out:
        updated["stdout"] = note + "\n" + new_out.text
    if new_err:
        updated["stderr"] = new_err.text if new_out else note + "\n" + new_err.text

    ledger.record(data_root, {
        "session": session,
        "tool": "Bash",
        "before": before,
        "after": after + estimate_tokens(note),
        "command": command[:200],
    })

    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "updatedToolOutput": updated}}


def main() -> int:
    try:
        event = json.load(sys.stdin)
        project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
        reply = handle(event, load_config(project_dir=project), ledger.data_dir())
        if reply:
            sys.stdout.write(json.dumps(reply))
    except Exception:  # fail-open: never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
