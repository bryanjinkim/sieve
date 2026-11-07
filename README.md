# Sieve

A Claude Code plugin that cuts token usage by filtering out noise and keeping what matters.

Most tokens in a Claude Code session aren't the prompts you type. They're tool results: long build
logs, test runs and whole files. Sieve sits between the tools and Claude and:

- **Compresses long command output.** It removes progress bars and repeated lines and keeps errors
  and summaries, so a 3,000-line log becomes about 300 useful lines.
- **Guards large file reads.** Instead of loading a 5,000-line file, Claude gets an outline and
  reads only the part it needs.
- **Tracks a per-session token budget** and warns before you go over.
- **Reports where tokens went** with `/sieve:report`.

## Layout

```
sieve/
├── .claude-plugin/plugin.json   plugin identity (name, version, description)
├── sieve/                       Python library: all the logic, no Claude Code knowledge
├── scripts/                     thin hook adapters: JSON in → library → JSON out
├── hooks/hooks.json             which script runs on which Claude Code event
├── config/default.json          default per-resource token budgets
├── tests/                       unit tests (python3 -m unittest)
└── pyproject.toml               makes the library pip-installable on its own
```

## Status

Work in progress. Built step by step.
