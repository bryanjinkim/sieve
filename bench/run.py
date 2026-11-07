#!/usr/bin/env python3
"""Benchmark: run the same tasks in headless Claude Code with and without Sieve.

    python3 bench/run.py --dry-run        build the fake project, print the plan (free)
    python3 bench/run.py                  run it: 3 tasks x 2 arms x 2 repeats = 12 sessions
    python3 bench/run.py --repeats 1      cheaper: 6 sessions

Each session is a fresh `claude -p` run in a fresh copy of the fake project.
Token numbers come from Claude Code's own JSON output (real API usage, not
estimates). Each answer is checked against the known correct facts, so a
"saving" that breaks the answer counts as a failure.

Running this uses your Claude usage (or API credits).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import make_project  # noqa: E402

TASKS = [
    {
        "id": "build",
        "prompt": "Run ./build.sh and tell me the size of the artifact it built and every "
                  "warning code it printed. Answer in two short lines.",
        "expect": ["2.4", "W104", "W221"],
    },
    {
        "id": "tests",
        "prompt": "Run the test suite with `python3 -m unittest discover -v` and tell me how many "
                  "tests ran, and which test was skipped and why. Answer in two short lines.",
        "expect": ["500", "refund_roundtrip", "payment gateway"],
    },
    {
        "id": "find",
        "prompt": "In shop/catalog.py, how does apply_discount round the final price? "
                  "Answer in one sentence.",
        "expect": ["5 cent"],
    },
]

ALLOWED_TOOLS = ["Bash(./build.sh)", "Bash(python3 -m unittest:*)", "Read", "Grep", "Glob"]


def find_claude(explicit: str = None) -> str:
    candidates = [explicit, shutil.which("claude"),
                  os.path.expanduser("~/.local/bin/claude"), os.path.expanduser("~/.claude/local/claude")]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    sys.exit("Couldn't find the `claude` CLI. Install it, sign in, then rerun "
             "(or pass --claude /path/to/claude).")


def run_one(claude: str, task: Dict[str, Any], arm: str, model: str, timeout: int) -> Dict[str, Any]:
    work = tempfile.mkdtemp(prefix=f"sieve-bench-{task['id']}-{arm}-")
    make_project.make(work)
    cmd = [claude, "-p", task["prompt"], "--output-format", "json", "--model", model,
           "--allowedTools", *ALLOWED_TOOLS, "--no-session-persistence"]
    env = dict(os.environ)
    if arm == "sieve":
        cmd += ["--plugin-dir", PLUGIN]
        env["CLAUDE_PLUGIN_DATA"] = os.path.join(work, ".sieve-data")
    start = time.time()
    try:
        p = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
        out = json.loads(p.stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        return {"task": task["id"], "arm": arm, "error": str(exc)[:300]}
    usage = out.get("usage") or {}
    answer = str(out.get("result", ""))
    record = {
        "task": task["id"],
        "arm": arm,
        "seconds": round(time.time() - start, 1),
        "turns": out.get("num_turns"),
        "input": int(usage.get("input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cost_usd": out.get("total_cost_usd"),
        "correct": all(e.lower() in answer.lower() for e in task["expect"]),
        "answer": answer[:400],
        "is_error": bool(out.get("is_error")),
    }
    record["processed"] = record["input"] + record["cache_read"] + record["cache_write"]
    ledger = os.path.join(work, ".sieve-data", "ledger.jsonl")
    record["sieve_events"] = sum(1 for _ in open(ledger)) if os.path.exists(ledger) else 0
    shutil.rmtree(work, ignore_errors=True)
    return record


def summarize(results: List[Dict[str, Any]]) -> str:
    ok = [r for r in results if "error" not in r]
    lines = ["| Task | Arm | Runs | Input processed (median) | Output | Cost (median) | Correct | Sieve events |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    savings = []
    for task in TASKS:
        medians = {}
        for arm in ("baseline", "sieve"):
            rs = [r for r in ok if r["task"] == task["id"] and r["arm"] == arm]
            if not rs:
                continue
            processed = statistics.median(r["processed"] for r in rs)
            medians[arm] = processed
            cost = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
            lines.append(
                f"| {task['id']} | {arm} | {len(rs)} | {processed:,.0f} | "
                f"{statistics.median(r['output'] for r in rs):,.0f} | "
                f"{'$%.3f' % statistics.median(cost) if cost else '-'} | "
                f"{sum(r['correct'] for r in rs)}/{len(rs)} | {sum(r['sieve_events'] for r in rs)} |")
        if len(medians) == 2 and medians["baseline"]:
            savings.append((task["id"], 1 - medians["sieve"] / medians["baseline"]))
    lines.append("")
    for task_id, s in savings:
        lines.append(f"- **{task_id}**: Sieve used {100 * s:.0f}% fewer input tokens (median)")
    errors = [r for r in results if "error" in r]
    if errors:
        lines.append(f"- {len(errors)} run(s) failed: " + "; ".join(e["error"][:80] for e in errors))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--claude", help="path to the claude CLI")
    parser.add_argument("--tasks", help="comma-separated task ids to run")
    args = parser.parse_args()

    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks.split(",")]
    if args.dry_run:
        work = tempfile.mkdtemp(prefix="sieve-bench-dry-")
        make_project.make(work)
        for cmd, label in ((["./build.sh"], "build.sh"),
                           ([sys.executable, "-m", "unittest", "discover", "-v"], "unittest")):
            p = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
            print(f"{label}: exit {p.returncode}, {len((p.stdout + p.stderr).splitlines()):,} lines")
        size = sum(1 for _ in open(os.path.join(work, "shop", "catalog.py")))
        print(f"shop/catalog.py: {size:,} lines")
        print(f"\nWould run {len(tasks)} tasks x 2 arms x {args.repeats} repeats = "
              f"{len(tasks) * 2 * args.repeats} headless sessions on '{args.model}'.")
        shutil.rmtree(work, ignore_errors=True)
        return 0

    claude = find_claude(args.claude)
    results = []
    for rep in range(args.repeats):
        for task in tasks:
            for arm in ("baseline", "sieve"):
                print(f"[{len(results) + 1}/{len(tasks) * 2 * args.repeats}] {task['id']} / {arm} ...",
                      flush=True)
                results.append(run_one(claude, task, arm, args.model, args.timeout))
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print("\n" + summarize(results) + f"\n\nRaw results: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
