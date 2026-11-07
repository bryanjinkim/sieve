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

## Run 2: 2026-11-06, Sonnet, 12 sessions (`results/20261106-201542.json`)

After changing the tip to "run once, without tail/head".

| Task | Baseline | Sieve | Change | Turns (baseline → Sieve) | Correct |
|---|---:|---:|---:|---|---|
| build | 219k | 218k | **-1%** | 6, 7 → 6, 6 | 4/4 |
| tests | 106k | 70k | **-34%** | 3, 3 → 2, 2 | 4/4 |
| find | 69k | 70k | +1% | 2 → 2 | 4/4 |
| **all three** | **394k** | **358k** | **-9%** | | 12/12 |

Cost: $0.216 baseline vs $0.208 with Sieve (median per task, summed).

**What the logs showed:**
- **tests:** Claude ran the full command. Sieve compressed the 27,057-character output while keeping
  `Ran 500 tests` and the skipped test, and Claude answered in one fewer turn.
- **build:** Claude ran the full `./build.sh`, but Claude Code had already cut stdout to a
  **30,000-character preview** and saved the full output to `persistedOutputPath`. Both warnings were
  past the preview, so Claude still had to read the saved file.
- **find:** Claude Grepped first, so the guard never fired. The 1% overhead is the session-start
  tips and the scout description, re-read every turn.

**Changes after run 2:** when `persistedOutputPath` is present, Sieve compresses the **full** saved
output instead of the preview, and points Claude at Claude Code's own saved copy.
