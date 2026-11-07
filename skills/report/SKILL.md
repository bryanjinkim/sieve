---
name: report
description: Show where this Claude Code session's tokens went and how much Sieve saved. Use when the user runs /sieve:report or asks about their token usage, context size or Sieve's savings.
argument-hint: "[--all | --days N]"
allowed-tools: Bash(python3:*)
---

Run this command once, passing along any arguments the user gave (`--all` for every session,
`--days N` for recent ones):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report.py" $ARGUMENTS
```

Show the Markdown it prints to the user exactly as it is. After it, add at most two sentences:
the single biggest token cost in the report, and one concrete tip for reducing it. Don't run any
other commands.
