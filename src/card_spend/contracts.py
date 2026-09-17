"""Schema contracts, enforced at the ingest boundary.

Each feed promises a set of fields. The contract is checked **before** anything is landed, so a
producer-side change is refused with a named field and a named owner rather than arriving as a
column full of nulls that a completeness rule later blames on the data team.

Three verdicts, and the distinction between them is the whole point:

``compatible``  the feed carries exactly what it promised
``additive``    the feed carries something extra; load it, record it, chase the producer later
``breaking``    a promised field is missing or was renamed; refuse the batch

A rename is breaking rather than additive. The absent old name is what decides the verdict, and the
new name is reported alongside it so the producer sees both halves of their change in one message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class Verdict(StrEnum):
    COMPATIBLE = "compatible"
    ADDITIVE = "additive"
    BREAKING = "breaking"


class ContractError(Exception):
    """A contract file itself is malformed. Distinct from a feed violating a valid contract."""


@dataclass(frozen=True)
class ContractCheck:
    feed: str
    verdict: Verdict
    missing_fields: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    owner: str = ""
    producer: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is not Verdict.BREAKING

    def message(self) -> str:
        if self.verdict is Verdict.COMPATIBLE:
            return f"{self.feed}: compatible"
        if self.verdict is Verdict.ADDITIVE:
            return f"{self.feed}: additive, unknown fields {list(self.unknown_fields)} (owner {self.owner})"
        parts = [f"{self.feed}: BREAKING, missing {list(self.missing_fields)}"]
        if self.unknown_fields:
            parts.append(f"possible rename to {list(self.unknown_fields)}")
        parts.append(f"producer {self.producer}, owner {self.owner}")
        return ", ".join(parts)


@dataclass(frozen=True)
class FeedContract:
    """The promise for one feed, normalised from whatever file format declared it."""

    feed: str
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    owner: str = ""
    producer: str = ""
    delivery_window: str = ""
    source: str = ""

    @property
    def promised(self) -> frozenset[str]:
        return self.required | self.optional

    def check(self, observed: set[str]) -> ContractCheck:
        missing = tuple(sorted(self.required - observed))
        unknown = tuple(sorted(observed - self.promised))
        if missing:
            verdict = Verdict.BREAKING
        elif unknown:
            verdict = Verdict.ADDITIVE
        else:
            verdict = Verdict.COMPATIBLE
        return ContractCheck(self.feed, verdict, missing, unknown, self.owner, self.producer)


# ------------------------------------------------------------------------------------------------
# Loaders, one per declaration format. Normalising three declaration styles into one FeedContract is
# the point: downstream code should not care that the processor ships Avro and core banking ships a
# hand-written YAML promise.
# ------------------------------------------------------------------------------------------------


def _meta_from_doc(doc: str) -> dict[str, str]:
    """Pull `Owner: x. Producer: y.` style metadata out of a free-text schema doc string."""
    out: dict[str, str] = {}
    for chunk in doc.replace("\n", " ").split("."):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            out[key.strip().lower().replace(" ", "_")] = value.strip()
    return out


def load_avro_contract(path: Path, feed: str) -> FeedContract:
    definition = json.loads(path.read_text(encoding="utf-8"))
    if definition.get("type") != "record":
        raise ContractError(f"{path} is not an Avro record schema")
    fields = definition.get("fields")
    if not fields:
        raise ContractError(f"{path} declares no fields")

    required: set[str] = set()
    optional: set[str] = set()
    for f in fields:
        # A field with a default is one the producer may omit. A nullable field without a default
        # still owes a column: it is the values inside it that may be empty, not the column itself.
        (optional if "default" in f else required).add(f["name"])

    meta = _meta_from_doc(definition.get("doc", ""))
    return FeedContract(
        feed=feed,
        required=frozenset(required),
        optional=frozenset(optional),
        owner=meta.get("owner", ""),
        producer=meta.get("producer", ""),
        delivery_window=meta.get("delivery_window", ""),
        source=str(path),
    )


def load_jsonschema_contract(path: Path, feed: str) -> FeedContract:
    definition = json.loads(path.read_text(encoding="utf-8"))
    properties = definition.get("properties")
    if not properties:
        raise ContractError(f"{path} declares no properties")
    required = set(definition.get("required", []))
    meta = _meta_from_doc(definition.get("description", ""))
    return FeedContract(
        feed=feed,
        required=frozenset(required),
        optional=frozenset(set(properties) - required),
        owner=meta.get("owner", ""),
        producer=meta.get("producer", ""),
        delivery_window=meta.get("delivery_window", ""),
        source=str(path),
    )


def load_tabular_contracts(path: Path) -> dict[str, FeedContract]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, FeedContract] = {}
    for feed, entry in doc["feeds"].items():
        columns = entry["columns"]
        out[feed] = FeedContract(
            feed=feed,
            # Presence is validated, not nullability, so every declared column is required.
            required=frozenset(columns),
            owner=entry.get("owner", ""),
            producer=entry.get("producer", ""),
            delivery_window=entry.get("delivery_window", ""),
            source=f"{path}#{feed}",
        )
    return out


@dataclass
class ContractRegistry:
    """Every feed's current promise, loaded once from ``contracts/``."""

    contracts: dict[str, FeedContract] = field(default_factory=dict)

    @classmethod
    def load(cls, contracts_dir: Path) -> ContractRegistry:
        registry = cls()
        # The card processor's promise is the *latest* schema version it publishes. Older files are
        # still readable because v2 is backward compatible, which the evolution test pins down.
        registry.contracts["card_transactions"] = load_avro_contract(
            contracts_dir / "card_transactions.v2.avsc", "card_transactions"
        )
        registry.contracts["customers"] = load_jsonschema_contract(contracts_dir / "customers.schema.json", "customers")
        registry.contracts.update(load_tabular_contracts(contracts_dir / "tabular_feeds.yaml"))
        return registry

    def check(self, feed: str, observed: set[str]) -> ContractCheck:
        if feed not in self.contracts:
            raise ContractError(f"no contract registered for feed {feed!r}")
        return self.contracts[feed].check(observed)

    def check_all(self, observed: dict[str, set[str]]) -> list[ContractCheck]:
        return [self.check(feed, fields) for feed, fields in sorted(observed.items())]


def summarise(checks: list[ContractCheck]) -> dict[str, Any]:
    return {
        "checked": len(checks),
        "breaking": [c.feed for c in checks if c.verdict is Verdict.BREAKING],
        "additive": [c.feed for c in checks if c.verdict is Verdict.ADDITIVE],
        "compatible": [c.feed for c in checks if c.verdict is Verdict.COMPATIBLE],
        "detail": [c.message() for c in checks],
    }
