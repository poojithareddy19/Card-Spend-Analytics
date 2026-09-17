"""Day-1 tests: the generator is the foundation everything else is measured against, so it gets
determinism, format and manifest coverage before any pipeline code exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from card_spend.generate import spec, writers
from card_spend.generate.generator import generate

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("smoke")
    generate("smoke", out, seed=7)
    return out / "raw"


def test_every_feed_is_written_in_its_own_format(dataset: Path) -> None:
    assert (dataset / "customers" / "customers.jsonl").exists()
    assert (dataset / "accounts" / "accounts.csv").exists()
    assert (dataset / "cards" / "cards.csv").exists()
    assert (dataset / "merchants" / "merchants.parquet").exists()
    assert list(dataset.glob("fx_rates/rate_date=*/rates.json"))
    assert list(dataset.glob("card_transactions/posted_date=*/part-00000.avro"))


def test_transactions_are_partitioned_by_posted_date(dataset: Path) -> None:
    parts = sorted(dataset.glob("card_transactions/posted_date=*/part-00000.avro"))
    manifest = json.loads((dataset / "_manifest.json").read_text())
    assert len(parts) == manifest["partitions"]["card_transactions"]
    # Every row in a partition really carries that partition's date, otherwise pruning would
    # silently return wrong answers rather than slow ones.
    part = parts[len(parts) // 2]
    expected = part.parent.name.split("=", 1)[1]
    rows = writers.read_avro(part)
    assert {r["posted_date"].isoformat() for r in rows} == {expected}


def test_avro_rows_satisfy_the_contract_enums(dataset: Path) -> None:
    part = sorted(dataset.glob("card_transactions/posted_date=*/part-00000.avro"))[0]
    rows = writers.read_avro(part)
    assert rows
    assert {r["txn_type"] for r in rows} <= set(spec.TXN_TYPES)
    assert {r["channel"] for r in rows} <= set(spec.CHANNELS)


def test_schema_evolution_is_compatible_in_both_directions(dataset: Path) -> None:
    """v1 bytes must decode under a v2 reader, and v2 bytes under a v1 reader.

    This is the property that lets one consumer read the whole history in a single pass. If someone
    later adds a required field to v2, this test is what tells them they broke every existing file.
    """
    v1 = writers.load_avro_schema(CONTRACTS / "card_transactions.v1.avsc")
    v2 = writers.load_avro_schema(CONTRACTS / "card_transactions.v2.avsc")
    parts = sorted(dataset.glob("card_transactions/posted_date=*/part-00000.avro"))
    old, new = parts[0], parts[-1]

    forward = writers.read_avro_as(old, v2)[0]
    assert forward["pos_entry_mode"] is None and forward["is_recurring"] is None

    backward = writers.read_avro_as(new, v1)[0]
    assert "pos_entry_mode" not in backward


def test_customers_json_is_nested_not_flattened(dataset: Path) -> None:
    first = json.loads((dataset / "customers" / "customers.jsonl").read_text().splitlines()[0])
    assert set(first["profile"]) >= {"first_name", "last_name", "date_of_birth", "email"}
    assert set(first["address"]) >= {"line1", "city", "postcode", "country"}
    assert set(first["kyc"]) >= {"status", "verified_at", "risk_band"}


def test_merchants_parquet_keeps_its_declared_types(dataset: Path) -> None:
    import pyarrow.parquet as pq

    schema = pq.read_schema(dataset / "merchants" / "merchants.parquet")
    assert str(schema.field("mcc").type) == "int32"
    assert str(schema.field("first_seen_date").type) == "date32[day]"
    assert str(schema.field("is_online").type) == "bool"


def test_manifest_defect_counts_match_the_data(dataset: Path) -> None:
    manifest = json.loads((dataset / "_manifest.json").read_text())
    rows: list[dict] = []
    for part in sorted(dataset.glob("card_transactions/posted_date=*/part-00000.avro")):
        rows.extend(writers.read_avro(part))
    df = pd.DataFrame(rows)

    # D02: duplicated transaction_id
    dupes = len(df) - df["transaction_id"].nunique()
    assert dupes == manifest["defect_counts"]["D02"]

    # D05: mcc outside the ISO 18245 range
    assert int((df["mcc"] > 9999).sum()) == manifest["defect_counts"]["D05"]

    # D03: card_id not present in the cards extract
    cards = set(pd.read_csv(dataset / "cards" / "cards.csv")["card_id"])
    orphans = df.drop_duplicates("transaction_id")
    assert int((~orphans["card_id"].isin(cards)).sum()) == manifest["defect_counts"]["D03"]


def test_generation_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    a = generate("smoke", tmp_path / "a", seed=99)
    b = generate("smoke", tmp_path / "b", seed=99)
    assert a.row_counts == b.row_counts
    assert a.defect_counts == b.defect_counts

    left = (tmp_path / "a" / "raw" / "accounts" / "accounts.csv").read_bytes()
    right = (tmp_path / "b" / "raw" / "accounts" / "accounts.csv").read_bytes()
    assert left == right


def test_a_different_seed_produces_different_data(tmp_path: Path) -> None:
    generate("smoke", tmp_path / "a", seed=1)
    generate("smoke", tmp_path / "b", seed=2)
    left = (tmp_path / "a" / "raw" / "accounts" / "accounts.csv").read_bytes()
    right = (tmp_path / "b" / "raw" / "accounts" / "accounts.csv").read_bytes()
    assert left != right


def test_fx_history_has_the_declared_gaps(dataset: Path) -> None:
    manifest = json.loads((dataset / "_manifest.json").read_text())
    days = spec.PROFILES["smoke"].days
    written = len(list(dataset.glob("fx_rates/rate_date=*/rates.json")))
    assert written == days - manifest["defect_counts"]["D11"]
