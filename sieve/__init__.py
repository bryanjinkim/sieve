"""Sieve: token-saving building blocks for Claude Code.

This package is the "brain" of the plugin. It knows nothing about Claude Code
itself -- it only deals with text, budgets and numbers -- so it can be tested
on its own and reused in other projects.

Modules (added step by step):
    tokens      estimate how many tokens a piece of text costs
    compress    shrink long tool output while keeping what matters
    outline     summarize a large file's structure (functions, classes, headings)
    config      load per-resource token budgets
    ledger      record how many tokens each compression saved
    transcript  read Claude Code session logs for usage reports
"""

__version__ = "0.1.0"
