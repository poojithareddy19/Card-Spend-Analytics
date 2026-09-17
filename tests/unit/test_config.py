"""The settings file and the code that reads it must not drift apart.

`config/settings.yaml` previously described four sections that nothing read: paths, generation,
stores and a governance block promising PII masking that was never built. Nobody noticed, because
nothing fails when a settings file is wrong. These tests are the thing that fails.

The first two walk the source for `config.get_*("dotted.path")` calls and reconcile them against the
file in both directions, so a key deleted from the file or added to it without a reader is caught at
the next test run rather than by the first person to change a value and see nothing happen.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from card_spend import config

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "card_spend"
SETTINGS = REPO_ROOT / "config" / "settings.yaml"

# `benchmark.budgets_ms` is indexed by access-pattern key at runtime by tests/performance, so its
# leaves have no literal `config.get_*` call to find. The prefix is read; the leaves are data.
DYNAMIC_PREFIXES = ("benchmark.budgets_ms",)

CALL = re.compile(r"""config\.get(?:_str|_int|_float)?\(\s*["']([a-z0-9_.]+)["']""")


def _paths_read_by_the_code() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        found.update(CALL.findall(path.read_text(encoding="utf-8")))
    return found


def _leaves(node: Any, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    out: set[str] = set()
    for key, value in node.items():
        out |= _leaves(value, f"{prefix}.{key}" if prefix else str(key))
    return out


@pytest.fixture(scope="module")
def loaded() -> dict[str, Any]:
    parsed = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def test_every_setting_the_code_reads_exists_in_the_file(loaded: dict[str, Any]) -> None:
    """A default silently winning because the key is absent is the failure mode this catches."""
    missing = sorted(p for p in _paths_read_by_the_code() if p not in _leaves(loaded))
    assert not missing, f"read by the code but absent from settings.yaml: {missing}"


def test_every_setting_in_the_file_is_read_by_something(loaded: dict[str, Any]) -> None:
    """The original defect, in the other direction: config describing behaviour nothing implements."""
    read = _paths_read_by_the_code()
    orphans = sorted(leaf for leaf in _leaves(loaded) if leaf not in read and not leaf.startswith(DYNAMIC_PREFIXES))
    assert not orphans, f"in settings.yaml but read by nothing: {orphans}"


def test_the_generic_override_beats_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSA__BENCHMARK__REPEATS", "9")
    assert config.get_int("benchmark.repeats", 5) == 9


def test_the_specific_alias_beats_the_generic_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI and the docs name CSA_POSTGRES_DSN, so it has to win."""
    monkeypatch.setenv("CSA__STORES__POSTGRES_DSN", "postgresql://generic/db")
    monkeypatch.setenv("CSA_POSTGRES_DSN", "postgresql://specific/db")
    assert config.get_str("stores.postgres_dsn", "") == "postgresql://specific/db"


def test_an_unknown_key_falls_back_to_the_caller_default() -> None:
    assert config.get_str("no.such.key", "fallback") == "fallback"


def test_a_value_that_will_not_coerce_falls_back_rather_than_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a settings file should not take the pipeline down with a ValueError."""
    monkeypatch.setenv("CSA__BENCHMARK__REPEATS", "not-a-number")
    assert config.get_int("benchmark.repeats", 5) == 5
    monkeypatch.setenv("CSA__GENERATION__V2_SWITCH_FRACTION", "two thirds")
    assert config.get_float("generation.v2_switch_fraction", 0.66) == 0.66
