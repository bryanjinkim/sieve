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

## Configuration

Each resource has its own token budget. Output under its budget passes through untouched; output
over it is compressed to fit.

| Setting | Default | What it limits |
|---|---|---|
| `budgets.bash_output` | 2500 | output of one shell command |
| `budgets.grep_output` | 2500 | results of one search |
| `budgets.mcp_output` | 4000 | output of one MCP (connected) tool call |
| `budgets.file_read` | 10000 | a whole-file read; larger files get an outline instead |
| `budgets.session_context` | 150000 | the whole conversation; Sieve warns at `session.warn_at` (80%) |

Override any of them in `~/.claude/sieve.json` (all projects) or `<project>/.claude/sieve.json`
(one project). List only what you change:

```json
{ "budgets": { "bash_output": 4000 } }
```

Other switches:

- **Skip compression for one command:** add `# sieve:off` to the end of the command.
- **Turn Sieve off for a session:** start Claude Code with `SIEVE_OFF=1`.
- **See the settings in effect:** `python3 -m sieve.config`, run from the plugin folder.

Invalid settings never break anything. They're skipped with a warning, and the default is used instead.

## Status

Work in progress. Built step by step.
