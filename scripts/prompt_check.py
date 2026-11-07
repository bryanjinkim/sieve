#!/usr/bin/env python3
"""UserPromptSubmit hook: warn when the conversation nears its token budget.

Claude Code runs this each time you send a message. It reads the size of the
conversation from the end of the session log, and when that passes
session.warn_at (default 80%) of budgets.session_context it:

    - shows you a one-line notice (systemMessage), and
    - tells Claude to be economical (additionalContext).

Each 10% band (80%, 90%, 100%, ...) is announced once per session, so the
warning itself doesn't become a recurring token cost.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sieve import ledger  # noqa: E402
from sieve.config import Config, load_config  # noqa: E402
from sieve.transcript import last_context  # noqa: E402

STATE_DIR = "tracker"


def _state_path(data_root: str, session_id: str) -> str:
    folder = os.path.join(data_root, STATE_DIR)
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:120]
    return os.path.join(folder, safe + ".json")


def handle(event: Dict[str, Any], cfg: Config, data_root: str) -> Optional[Dict[str, Any]]:
    if not cfg.enabled:
        return None
    transcript = event.get("transcript_path")
    if not isinstance(transcript, str):
        return None
    context = last_context(transcript)
    budget = cfg.budget("session_context")
    share = context / budget
    if share < cfg.warn_at:
        return None

    band = int(share * 10)  # 8 = 80-89%, 9 = 90-99%, 10 = 100-109%, ...
    state_file = _state_path(data_root, str(event.get("session_id", "unknown")))
    try:
        with open(state_file) as f:
            warned = int(json.load(f).get("band", 0))
    except (OSError, ValueError, AttributeError):
        warned = 0
    if band <= warned:
        return None
    with open(state_file, "w") as f:
        json.dump({"band": band}, f)

    pct = round(share * 100)
    return {
        "systemMessage": (f"Sieve: this conversation is ~{context:,} tokens, {pct}% of your "
                          f"{budget:,}-token session budget. Consider /compact or a new session."),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[sieve] The conversation is at {pct}% of its {budget:,}-token budget. Be "
                "economical: use Grep and ranged reads instead of whole files, don't re-read "
                "files you've already seen, and keep replies short. If the user's topic has "
                "changed, suggest /compact or a fresh session."),
        },
    }


def main() -> int:
    try:
        event = json.load(sys.stdin)
        project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
        reply = handle(event, load_config(project_dir=project), ledger.data_dir())
        if reply:
            sys.stdout.write(json.dumps(reply))
    except Exception:  # fail-open: never block a prompt
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
