"""The five access patterns, defined once, implemented per store.

The whole project turns on this file. Each store answers the same five business questions, so the
benchmark compares engines rather than comparing five different queries that happen to run in five
different places. Where a store cannot answer a pattern at all, it says so instead of faking it, and
the results table records the gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class AccessPattern:
    key: str
    title: str
    question: str
    expected_store: str
    latency_class: str


PATTERNS: tuple[AccessPattern, ...] = (
    AccessPattern(
        "q1_customer_profile_lookup",
        "Customer profile lookup",
        "Show me everything we hold about this one customer, including their nested KYC and address",
        "document",
        "interactive, serves a screen",
    ),
    AccessPattern(
        "q2_customer_12m_spend_by_category",
        "One customer's year of spend by category",
        "What has this customer spent, by category, each month for the last twelve months",
        "relational",
        "interactive, serves a screen",
    ),
    AccessPattern(
        "q3_category_month_spend_all_customers",
        "Spend by category and month, whole book",
        "How is spend split across merchant categories by month, across every customer",
        "columnar",
        "analytical, seconds are fine",
    ),
    AccessPattern(
        "q4_top_merchants_per_segment",
        "Top merchants per segment",
        "Which twenty merchants take the most spend in each customer segment",
        "columnar",
        "analytical, seconds are fine",
    ),
    AccessPattern(
        "q5_asof_fx_revaluation",
        "As-of FX revaluation over the full history",
        "Restate every foreign-currency transaction at the latest rate, for the whole history",
        "columnar",
        "batch, runs once per load",
    ),
)

PATTERNS_BY_KEY = {p.key: p for p in PATTERNS}


@runtime_checkable
class Store(Protocol):
    """A store that can answer some subset of the access patterns."""

    name: str
    kind: str

    def reachable(self) -> bool:
        """Can be connected to. The loader's precondition."""
        ...

    def available(self) -> bool:
        """Reachable and holding data. The benchmark's precondition."""
        ...

    def supports(self, pattern_key: str) -> bool: ...

    def run(self, pattern_key: str, **params: object) -> pd.DataFrame: ...

    def explain(self, pattern_key: str, **params: object) -> str: ...

    def close(self) -> None: ...


class UnsupportedPatternError(RuntimeError):
    """The store genuinely cannot answer this pattern. Recorded, not worked around."""
