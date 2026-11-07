# Benchmark results

## Run 1: 2026-11-06, Sonnet, 12 sessions (`results/20261106-194631.json`)

| Task | Baseline input processed | Sieve input processed | Turns (baseline → Sieve) | Correct |
|---|---:|---:|---|---|
| build | 162k | 246k (**+52%**) | 5, 5 → 7, 8 | 4/4 |
| tests | 107k | 107k (0%) | 3 → 3 | 4/4 |
| find | 69k | 70k (+1%) | 2 → 2 | 4/4 |

**Sieve saved nothing, and the build task got worse.** Diagnostic runs with `SIEVE_DEBUG=1` showed:

1. **Every hook ran** (SessionStart 72 ms, UserPromptSubmit 50 ms, PostToolUse 45 ms), with no errors.
2. **Compression never triggered.** Claude trimmed output itself with commands like
   `./build.sh 2>&1 | tail -50`, so the hook received only 270–4,780 characters, all under budget.
3. **That blind trimming cost turns.** `tail -50` cut out the W104 warning in the middle of the build
   log, so Claude ran more commands to find it. Every extra turn re-reads the ~30k-token base context,
   which outweighs any output savings.
4. **Sieve's own tip made it worse.** It told Claude to "pipe through head, tail or grep", which
   encourages exactly the blind trimming Sieve is meant to replace.
5. **The find task:** Claude used Grep first in both arms, so the large-file guard never fired.

**Lessons:**
- In modern Claude Code, the main cost driver is **the number of turns** (each re-reads the system
  prompt and tools), not just the size of tool output.
- A tip that increases turns can cost more than it saves.

**Changes after run 1:** the tip now says to run commands once without tail/head and let Sieve
compress. Hooks log errors (always) and call shapes (with `SIEVE_DEBUG=1`) to `hooks.log`.
