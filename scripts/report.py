#!/usr/bin/env python3
"""Print a token report: where tokens went, and what Sieve saved.

    python3 scripts/report.py                 the current (latest) session
    python3 scripts/report.py --all           every session on this machine
    python3 scripts/report.py --days 7        sessions from the last 7 days
    python3 scripts/report.py --session FILE  one specific session log

Output is Markdown, so Claude can show it as-is when /sieve:report runs.

Two kinds of numbers appear, and the report labels them:
    real       from the API usage that Claude Code logged
    estimated  from Sieve's token estimator (tool results aren't counted
               individually in the logs)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sieve import ledger  # noqa: E402
from sieve.config import load_config  # noqa: E402
from sieve.transcript import Session, all_session_paths, latest_session_path, parse  # noqa: E402


def fmt(n: float) -> str:
    n = int(round(n))
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1000:.0f}k"
    return f"{n:,}"


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def usage_section(sessions: List[Session], budget: int, single: bool) -> List[str]:
    calls = [c for s in sessions for c in s.calls]
    processed = sum(c.context for c in calls)
    cached = sum(c.cache_read for c in calls)
    out = [
        "## Usage (real, from Claude Code's logs)",
        "",
        "| | |",
        "|---|---|",
    ]
    if single:
        s = sessions[0]
        out.append(f"| Conversation size now | **{fmt(s.context_now)}** tokens "
                   f"({pct(s.context_now, budget)} of the {fmt(budget)} budget) |")
    else:
        out.append(f"| Sessions | {len(sessions)} |")
    out += [
        f"| Model calls | {len(calls):,} |",
        f"| Input tokens processed | {fmt(processed)} (each call re-reads the conversation) |",
        f"| ...of which read from cache | {fmt(cached)} ({pct(cached, processed)}) |",
        f"| Output tokens | {fmt(sum(c.output for c in calls))} |",
        "",
    ]
    return out


def tools_section(sessions: List[Session], top: int = 5) -> List[str]:
    results = [r for s in sessions for r in s.results]
    if not results:
        return ["## Tool results", "", "No tool results in this range.", ""]
    by_tool: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        by_tool[r.tool][0] += 1
        by_tool[r.tool][1] += r.tokens
    total = sum(v[1] for v in by_tool.values())
    out = [
        "## Where tool output went (estimated)",
        "",
        "| Tool | Calls | Tokens | Share |",
        "|---|---:|---:|---:|",
    ]
    for tool, (count, tokens) in sorted(by_tool.items(), key=lambda kv: -kv[1][1]):
        out.append(f"| {tool} | {count} | {fmt(tokens)} | {pct(tokens, total)} |")
    out += ["", f"**Biggest single results:**", ""]
    for r in sorted(results, key=lambda r: -r.tokens)[:top]:
        label = r.label.replace("|", "\\|").replace("`", "'")
        out.append(f"- {fmt(r.tokens)} tokens: {r.tool} `{label}`")
    out.append("")
    return out


def savings_section(sessions: List[Session], data_root: str, since: float) -> List[str]:
    ids = {s.session_id for s in sessions}
    entries = [e for e in ledger.read(data_root)
               if e.get("session") in ids or (len(ids) > 1 and e.get("ts", 0) >= since)]
    if not entries:
        return ["## Sieve savings", "",
                "Nothing compressed or guarded yet in this range.", ""]
    by_id = {s.session_id: s for s in sessions}
    rows: Dict[str, List[float]] = defaultdict(lambda: [0, 0, 0])
    compounded = 0.0
    for e in entries:
        saved = max(0, int(e.get("before", 0)) - int(e.get("after", 0)))
        row = rows[str(e.get("tool", "?"))]
        row[0] += 1
        row[1] += int(e.get("before", 0))
        row[2] += int(e.get("after", 0))
        # A token removed once is also not re-read on every later call.
        session = by_id.get(e.get("session"))
        later = session.calls_after(float(e.get("ts", 0))) if session else 1
        compounded += saved * max(1, later)
    before = sum(r[1] for r in rows.values())
    after = sum(r[2] for r in rows.values())
    out = [
        "## Sieve savings (estimated)",
        "",
        "| What | Times | Before | After | Saved |",
        "|---|---:|---:|---:|---:|",
    ]
    names = {"Bash": "Shell output compressed", "Read": "Large reads replaced by outline"}
    for tool, (count, b, a) in sorted(rows.items()):
        out.append(f"| {names.get(tool, tool)} | {int(count)} | {fmt(b)} | {fmt(a)} | "
                   f"{fmt(b - a)} ({pct(b - a, b)}) |")
    out += [
        f"| **Total** | {sum(int(r[0]) for r in rows.values())} | {fmt(before)} | {fmt(after)} | "
        f"**{fmt(before - after)} ({pct(before - after, before)})** |",
        "",
        f"Because every later call re-reads the conversation, those savings add up to about "
        f"**{fmt(compounded)} fewer input tokens processed** so far.",
        "",
    ]
    if any(e.get("estimated") for e in entries):
        out += ["_Outline savings assume the full read was avoided; Claude usually reads a "
                "smaller part afterwards._", ""]
    return out


def build_report(sessions: List[Session], data_root: str, budget: int,
                 title: str, since: float, single: bool = True) -> str:
    lines = [f"# Sieve report: {title}", ""]
    lines += usage_section(sessions, budget, single)
    lines += tools_section(sessions)
    lines += savings_section(sessions, data_root, since)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true", help="every session on this machine")
    parser.add_argument("--days", type=float, help="sessions from the last N days")
    parser.add_argument("--session", help="path to one session log (.jsonl)")
    args = parser.parse_args(argv)

    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = load_config(project_dir=project)
    since = 0.0
    if args.session:
        paths, title = [args.session], "one session"
    elif args.all or args.days:
        since = time.time() - args.days * 86400 if args.days else 0.0
        paths = all_session_paths(since=since if args.days else None)
        title = f"last {args.days:g} days" if args.days else "all sessions"
    else:
        latest = latest_session_path(project)
        paths, title = ([latest] if latest else []), "this session"

    sessions = [s for s in (parse(p) for p in paths) if s.calls]
    if not sessions:
        print("No Claude Code session logs found yet.")
        return 0
    single = not (args.all or args.days)
    print(build_report(sessions, ledger.data_dir(), cfg.budget("session_context"), title, since,
                       single=single))
    return 0


if __name__ == "__main__":
    sys.exit(main())
