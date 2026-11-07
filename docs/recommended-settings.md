# Recommended Claude Code settings

Sieve trims output in a smart way. Claude Code also has built-in limits that cut output at a
fixed size. The two work well together: Sieve handles the common cases carefully, and the
built-in limits catch what Sieve can't reach, such as output from **failed** commands.

A plugin can't set these for you (plugins may only ship `agent` and `subagentStatusLine`
settings), so add the ones you want to the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "BASH_MAX_OUTPUT_LENGTH": "15000",
    "MAX_MCP_OUTPUT_TOKENS": "10000",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "75"
  }
}
```

| Setting | Default | Suggested | What it does | Trade-off |
|---|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` | 30000 chars | 15000 | Caps how much shell output Claude reads, keeping the start and end | Covers **failed** commands, which Sieve can't compress. The cut is blind, but the end of the output, where the errors usually are, is kept. |
| `MAX_MCP_OUTPUT_TOKENS` | 25000 | 10000 | Caps the output of connected (MCP) tools | Very large tool results get cut off |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | model default | 75 | Compacts the conversation earlier, so each later call re-reads less | Older details are summarized sooner |

## Optional: stronger savings, with quality costs

| Setting | Example | Saves | Costs |
|---|---|---|---|
| `CLAUDE_CODE_EFFORT_LEVEL` | `medium` | Thinking tokens on every reply | Less careful reasoning on hard problems. Use `/effort` to raise it when needed. |
| `MAX_THINKING_TOKENS` | `8000` | Puts a fixed cap on thinking | Same as above, and it overrides adaptive thinking |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `haiku` | Cheaper subagents everywhere | **Every** subagent gets the cheaper model, including planning and review agents. Sieve's own `scout` agent already uses Haiku, so this is rarely worth it. |

## Leave these alone

- `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` (default 25000): Sieve's large-file guard already
  steers reads to the part that's needed, with an outline, which is better than a blind cut.
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: lowering it can cut Claude off mid-answer or mid-file.

All names and defaults are from the Claude Code environment variable reference
(code.claude.com/docs/en/env-vars), checked in November 2026.
