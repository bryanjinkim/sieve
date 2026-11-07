"""Read Claude Code session logs to see where tokens actually went.

Claude Code writes each session to a JSON Lines file under
~/.claude/projects/<encoded project path>/<session id>.jsonl. Each line is
one event. The two kinds that matter here:

    assistant  one model response. Its message.usage has the real token
               counts the API reported:
                 input_tokens                 new, uncached input
                 cache_read_input_tokens      input re-read from the cache
                 cache_creation_input_tokens  input written to the cache
                 output_tokens                what the model wrote
               A single response can be split across several lines that share
               one message.id, so responses are de-duplicated by id.

    user       includes tool results (type "tool_result"), matched to the
               tool call that asked for them by tool_use_id.

Context size at a call = input + cache_read + cache_creation: the whole
conversation the model had to read for that response.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from .tokens import estimate_tokens

PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")


@dataclass
class Call:
    """One model response."""
    ts: float
    model: str
    input: int
    cache_read: int
    cache_write: int
    output: int
    sidechain: bool = False  # a subagent's call: real usage, but not this conversation

    @property
    def context(self) -> int:
        return self.input + self.cache_read + self.cache_write


@dataclass
class ToolResult:
    ts: float
    tool: str
    label: str  # the command, file path or pattern, for the "biggest" list
    tokens: int  # estimated; logs don't record per-result token counts


@dataclass
class Session:
    path: str
    calls: List[Call] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def main_calls(self) -> List[Call]:
        return [c for c in self.calls if not c.sidechain]

    @property
    def context_now(self) -> int:
        main = self.main_calls
        return main[-1].context if main else 0

    @property
    def input_processed(self) -> int:
        """Every input token the model read, summed over all calls."""
        return sum(c.context for c in self.calls)

    @property
    def cache_read(self) -> int:
        return sum(c.cache_read for c in self.calls)

    @property
    def output(self) -> int:
        return sum(c.output for c in self.calls)

    def calls_after(self, ts: float) -> int:
        """Main-conversation calls after ``ts``: each one re-read the conversation."""
        return sum(1 for c in self.main_calls if c.ts > ts)


def project_log_dir(project_dir: str, root: str = PROJECTS_DIR) -> str:
    """Claude Code's log folder for a project: every non-alphanumeric -> '-'."""
    return os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_dir)))


def latest_session_path(project_dir: Optional[str] = None, root: str = PROJECTS_DIR) -> Optional[str]:
    """The most recently written log: for the current project if possible."""
    patterns = []
    if project_dir:
        patterns.append(os.path.join(project_log_dir(project_dir, root), "*.jsonl"))
    patterns.append(os.path.join(root, "*", "*.jsonl"))
    for pattern in patterns:
        files = glob.glob(pattern)
        if files:
            return max(files, key=os.path.getmtime)
    return None


def all_session_paths(root: str = PROJECTS_DIR, since: Optional[float] = None) -> List[str]:
    files = glob.glob(os.path.join(root, "*", "*.jsonl"))
    if since is not None:
        files = [f for f in files if os.path.getmtime(f) >= since]
    return sorted(files, key=os.path.getmtime)


def _ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _label(name: str, tool_input: Dict[str, Any]) -> str:
    for key in ("command", "file_path", "pattern", "url", "query", "path"):
        if isinstance(tool_input.get(key), str):
            return tool_input[key].strip().split("\n")[0][:100]
    return name


def _lines(path: str) -> Iterator[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def parse(path: str) -> Session:
    session = Session(path)
    seen = set()
    tools: Dict[str, tuple] = {}  # tool_use_id -> (name, label)
    for entry in _lines(path):
        kind = entry.get("type")
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        if kind == "assistant":
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "?"))
                    tools[str(block.get("id"))] = (name, _label(name, block.get("input") or {}))
            mid = message.get("id") or entry.get("requestId") or entry.get("uuid")
            usage = message.get("usage")
            if mid in seen or not isinstance(usage, dict):
                continue
            seen.add(mid)
            session.calls.append(Call(
                ts=_ts(entry.get("timestamp")),
                model=str(message.get("model", "?")),
                input=int(usage.get("input_tokens") or 0),
                cache_read=int(usage.get("cache_read_input_tokens") or 0),
                cache_write=int(usage.get("cache_creation_input_tokens") or 0),
                output=int(usage.get("output_tokens") or 0),
                sidechain=bool(entry.get("isSidechain")),
            ))
        elif kind == "user" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name, label = tools.get(str(block.get("tool_use_id")), ("?", "?"))
                    text = _text(block.get("content"))
                    session.results.append(
                        ToolResult(_ts(entry.get("timestamp")), name, label, estimate_tokens(text)))
    return session


def last_context(path: str, tail_bytes: int = 512_000) -> int:
    """Context size at the latest call, reading only the end of the file.

    The prompt hook runs on every message you send, so it must not re-parse a
    long session log from the start each time.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    for line in reversed(chunk.split("\n")):
        if '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # probably cut in half by the seek
        usage = (entry.get("message") or {}).get("usage") if isinstance(entry, dict) else None
        if entry.get("type") == "assistant" and isinstance(usage, dict) and not entry.get("isSidechain"):
            return int(usage.get("input_tokens") or 0) + \
                int(usage.get("cache_read_input_tokens") or 0) + \
                int(usage.get("cache_creation_input_tokens") or 0)
    return 0
