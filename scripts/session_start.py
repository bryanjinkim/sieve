#!/usr/bin/env python3
"""SessionStart hook: give Claude a few token-saving habits at the start of a session.

Plain text printed by a SessionStart hook is added to Claude's context. The
guidelines are kept to about 150 tokens: they're paid for once per session
and then served from the prompt cache, while each habit they encourage
(searching before reading, delegating to the scout) can save thousands.

Turn them off with {"session": {"tips": false}} in sieve.json.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sieve import ledger  # noqa: E402
from sieve.config import Config, load_config  # noqa: E402

TIPS = """[sieve] Token-saving habits for this session:
- Find before you read: Grep or Glob for what you need, then Read with offset/limit.
- Don't re-read a file you've already read unless it has changed.
- For broad read-only searches, delegate to the sieve:scout agent. It runs on a cheaper model and returns only a summary.
- Run commands once, without piping them through tail or head: Sieve compresses long output and keeps every error, warning and summary, so one full run is cheaper than several trimmed ones.
- Keep replies concise: don't restate the question or repeat code the user already has."""


def handle(event: Dict[str, Any], cfg: Config) -> Optional[str]:
    if not cfg.enabled or not cfg.data["session"].get("tips", True):
        return None
    return TIPS


def main() -> int:
    event = None
    try:
        event = json.load(sys.stdin)
        ledger.log_event("session_start", event)
        project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")
        text = handle(event, load_config(project_dir=project))
        if text:
            sys.stdout.write(text + "\n")
    except Exception as exc:  # fail-open, but leave a trace
        ledger.log_event("session_start", event, exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
