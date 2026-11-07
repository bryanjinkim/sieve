"""Load Sieve's settings: per-resource token budgets and compressor options.

Settings come in layers. Each layer only lists what it wants to change, and
later layers win:

    1. config/default.json            shipped with the plugin
    2. ~/.claude/sieve.json           your personal overrides (all projects)
    3. <project>/.claude/sieve.json   overrides for one project
    4. SIEVE_OFF=1 environment var    turns Sieve off for one session

Layers are merged key by key (a "deep merge"), so a project file containing
only {"budgets": {"bash_output": 4000}} changes that one number and keeps
every other default.

A broken setting must never break Claude Code, so loading is forgiving: a
file with invalid JSON, a value of the wrong type, or a budget that makes no
sense is skipped with a warning, and the default is used instead.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS_PATH = os.path.join(PLUGIN_ROOT, "config", "default.json")
FILE_NAME = "sieve.json"

# Smallest budget that still leaves room for a useful head, tail and errors.
MIN_BUDGET = 200


@dataclass
class Config:
    data: Dict[str, Any]
    sources: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(self.data["enabled"])

    def budget(self, resource: str) -> int:
        return int(self.data["budgets"][resource])

    @property
    def compress_options(self) -> Dict[str, int]:
        return dict(self.data["compress"])

    @property
    def warn_at(self) -> float:
        return float(self.data["session"]["warn_at"])

    @property
    def save_full_output(self) -> bool:
        return bool(self.data["save_full_output"])

    def is_passthrough(self, command: str) -> bool:
        """True if a command asks to skip compression, e.g. `make # sieve:off`."""
        return any(re.search(p, command) for p in self.data["passthrough_patterns"])


# ------------------------------------------------------------------ merging


def _merge(base: Dict[str, Any], override: Dict[str, Any], defaults: Dict[str, Any],
           path: str, warnings: List[str]) -> None:
    """Deep-merge ``override`` into ``base`` in place, validating as we go.

    ``defaults`` is the matching part of the shipped defaults; it defines
    which keys exist and what type each value must have.
    """
    for key, value in override.items():
        where = f"{path}{key}"
        if key not in defaults:
            warnings.append(f"unknown setting '{where}' ignored")
            continue
        expected = defaults[key]
        if isinstance(expected, dict):
            if isinstance(value, dict):
                _merge(base[key], value, expected, where + ".", warnings)
            else:
                warnings.append(f"'{where}' should be an object; ignored")
            continue
        problem = _check(where, value, expected)
        if problem:
            warnings.append(problem)
        else:
            base[key] = value


def _check(where: str, value: Any, expected: Any) -> Optional[str]:
    """Return a warning if ``value`` isn't valid where ``expected`` lives."""
    if isinstance(expected, bool):
        return None if isinstance(value, bool) else f"'{where}' should be true or false; ignored"
    if isinstance(expected, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{where}' should be a number; ignored"
        if where.startswith("budgets.") and value < MIN_BUDGET:
            return f"'{where}' must be at least {MIN_BUDGET}; ignored"
        if where == "session.warn_at" and not 0 < value <= 1:
            return f"'{where}' must be between 0 and 1; ignored"
        if where.startswith("compress.") and value < 0:
            return f"'{where}' can't be negative; ignored"
        return None
    if isinstance(expected, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"'{where}' should be a list of strings; ignored"
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                return f"'{where}' has an invalid pattern {pattern!r} ({exc}); ignored"
        return None
    return None


def _read_json(path: str, warnings: List[str]) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        warnings.append(f"{path}: could not read ({exc}); using other settings")
        return None
    if not isinstance(data, dict):
        warnings.append(f"{path}: top level must be a JSON object; ignored")
        return None
    return data


# ------------------------------------------------------------------ loading


def load_config(
    project_dir: Optional[str] = None,
    user_dir: Optional[str] = None,
    defaults_path: str = DEFAULTS_PATH,
    env: Optional[Dict[str, str]] = None,
) -> Config:
    """Build the effective config from all layers.

    ``project_dir`` is the project root (Claude Code passes it to hooks as
    CLAUDE_PROJECT_DIR); ``user_dir`` defaults to ~/.claude.
    """
    env = os.environ if env is None else env
    warnings: List[str] = []

    with open(defaults_path, encoding="utf-8") as f:
        defaults = json.load(f)
    data = copy.deepcopy(defaults)
    sources = [defaults_path]

    user_dir = user_dir or os.path.join(os.path.expanduser("~"), ".claude")
    layers = [os.path.join(user_dir, FILE_NAME)]
    if project_dir:
        layers.append(os.path.join(project_dir, ".claude", FILE_NAME))

    for path in layers:
        override = _read_json(path, warnings)
        if override is not None:
            _merge(data, override, defaults, "", warnings)
            sources.append(path)

    if env.get("SIEVE_OFF", "").strip().lower() in ("1", "true", "yes", "on"):
        data["enabled"] = False
        sources.append("SIEVE_OFF environment variable")

    return Config(data, sources, warnings)


if __name__ == "__main__":
    # `python3 -m sieve.config` prints the effective settings and where they came from.
    cfg = load_config(project_dir=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    print(json.dumps(cfg.data, indent=2))
    print("\nloaded from:\n  " + "\n  ".join(cfg.sources))
    if cfg.warnings:
        print("\nwarnings:\n  " + "\n  ".join(cfg.warnings))
