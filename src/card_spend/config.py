"""Settings, loaded from ``config/settings.yaml``.

A settings file that nothing reads is worse than no settings file: it documents behaviour the code
does not have, and the first person to change a value in it and see nothing happen stops trusting
the rest of the repo. So every key in ``config/settings.yaml`` is read from here, and anything that
stopped being true was deleted from the file rather than left to rot.

Three sources, in precedence order:

1. the specific environment variable, where one exists (``CSA_POSTGRES_DSN``, ``CSA_REDIS_URL``,
   ``CSA_DUCKDB``). These are what CI sets and what the docs quote, so they win.
2. the generic override ``CSA__SECTION__KEY``, upper-cased, for any key at all.
3. the value in ``config/settings.yaml``.

The defaults passed by callers are the last resort and exist so that a missing or truncated settings
file degrades to working behaviour rather than a stack trace.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = Path(os.environ.get("CSA_SETTINGS", REPO_ROOT / "config" / "settings.yaml"))

# Keys that had a specific variable before the generic mechanism existed. Kept because CI, the
# README and the benchmark docs all name them, and breaking them to tidy up would be a poor trade.
ALIASES: dict[str, str] = {
    "stores.postgres_dsn": "CSA_POSTGRES_DSN",
    "stores.redis_url": "CSA_REDIS_URL",
    "stores.duckdb_path": "CSA_DUCKDB",
}


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    """The parsed settings file, or an empty mapping if it is not there."""
    if not SETTINGS_PATH.exists():
        return {}
    loaded = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def get(dotted: str, default: Any = None) -> Any:
    """One setting, by dotted path, with both override mechanisms applied."""
    alias = ALIASES.get(dotted)
    if alias and os.environ.get(alias):
        return os.environ[alias]

    generic = "CSA__" + dotted.replace(".", "__").upper()
    if os.environ.get(generic):
        return os.environ[generic]

    node: Any = settings()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def get_str(dotted: str, default: str) -> str:
    value = get(dotted, default)
    return default if value is None else str(value)


def get_int(dotted: str, default: int) -> int:
    value = get(dotted, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(dotted: str, default: float) -> float:
    value = get(dotted, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
