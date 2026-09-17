"""Redis: the key-value document store.

It answers exactly one of the five patterns, and it is in the benchmark for that reason rather than
in spite of it. A store that does one thing an order of magnitude faster than the alternatives, and
cannot do the other four at all, is a real architectural choice, and a comparison that only included
stores capable of everything would hide it.

The customer document is stored verbatim as the onboarding platform sent it, nested, which is why
the ingest step kept the original JSON alongside the flattened columns.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pandas as pd

from card_spend import config
from card_spend.stores.base import UnsupportedPatternError

if TYPE_CHECKING:
    import redis
else:  # pragma: no cover - import guard
    # redis-py is the `docstore` extra. Absent, the store reports itself unreachable rather than
    # raising at import, so the rest of the pipeline still runs and the benchmark records the gap.
    try:
        import redis
    except ImportError:
        redis = None

DEFAULT_URL = config.get_str("stores.redis_url", "redis://127.0.0.1:6379/0")
KEY_PREFIX = "customer:"
SUPPORTED = {"q1_customer_profile_lookup"}


class KeyValueStore:
    name = "redis"
    kind = "key-value document store"

    def __init__(self, url: str = DEFAULT_URL) -> None:
        self.url = url
        self._client: Any = None

    def client(self) -> Any:
        if redis is None:
            raise UnsupportedPatternError("redis-py is not installed")
        if self._client is None:
            self._client = redis.from_url(self.url, decode_responses=True, socket_timeout=5)
        return self._client

    def reachable(self) -> bool:
        """Answers a ping. Says nothing about whether it holds any documents."""
        if redis is None:
            return False
        try:
            return bool(self.client().ping())
        except Exception:
            return False

    def available(self) -> bool:
        """Reachable **and** loaded.

        A store that answers instantly because it holds nothing is the fastest store in any
        benchmark and the most useless. A responding Redis with an empty customer keyspace counts
        as unavailable rather than as a sub-millisecond win on zero rows.

        As with Postgres, the loader checks `reachable()` instead, because an empty keyspace is the
        normal starting state for a load rather than a reason to refuse one.
        """
        if not self.reachable():
            return False
        try:
            _cursor, keys = self.client().scan(cursor=0, match=f"{KEY_PREFIX}*", count=1)
            return bool(keys)
        except Exception:
            return False

    def supports(self, pattern_key: str) -> bool:
        return pattern_key in SUPPORTED

    def run(self, pattern_key: str, **params: Any) -> pd.DataFrame:
        if pattern_key not in SUPPORTED:
            raise UnsupportedPatternError(
                f"{self.name} cannot answer {pattern_key}: it has no aggregation, no join and no scan"
            )
        raw = self.client().get(f"{KEY_PREFIX}{params['customer_id']}")
        if raw is None:
            return pd.DataFrame()
        return pd.DataFrame([{"customer_id": params["customer_id"], "document": raw}])

    def fetch_document(self, customer_id: str) -> dict[str, Any] | None:
        raw = self.client().get(f"{KEY_PREFIX}{customer_id}")
        return json.loads(raw) if raw else None

    def explain(self, pattern_key: str, **params: Any) -> str:
        return "GET customer:<id> — one key lookup, O(1), no plan to show"

    def load(self, documents: dict[str, str], batch_size: int = 5_000) -> int:
        client = self.client()
        written = 0
        items: dict[str, str] = {}
        for customer_id, document in documents.items():
            items[f"{KEY_PREFIX}{customer_id}"] = document
            if len(items) >= batch_size:
                client.mset(items)
                written += len(items)
                items = {}
        if items:
            client.mset(items)
            written += len(items)
        return written

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
