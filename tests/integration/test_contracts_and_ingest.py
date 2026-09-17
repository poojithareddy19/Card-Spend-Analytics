"""Day-2 tests: the contract gate and the lake landing.

The valuable tests here are the refusals. A gate that has never been shown to refuse anything is
indistinguishable from no gate at all.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from card_spend.contracts import ContractRegistry, Verdict
from card_spend.generate.generator import generate
from card_spend.ingest import readers
from card_spend.ingest.lake import SchemaDriftError, check_contracts, ingest

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@pytest.fixture(scope="module")
def landed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("d2")
    generate("smoke", root, seed=11)
    ingest(root / "raw", root / "lake", CONTRACTS)
    return root


def test_a_clean_dataset_is_compatible_on_every_feed(landed: Path) -> None:
    checks = check_contracts(landed / "raw", CONTRACTS)
    assert {c.feed for c in checks} == {"customers", "accounts", "cards", "merchants", "fx_rates", "card_transactions"}
    assert all(c.verdict is Verdict.COMPATIBLE for c in checks), [c.message() for c in checks]


def test_a_missing_promised_column_is_breaking_and_names_its_owner() -> None:
    registry = ContractRegistry.load(CONTRACTS)
    observed = set(registry.contracts["accounts"].required) - {"currency"}
    check = registry.check("accounts", observed)
    assert check.verdict is Verdict.BREAKING
    assert check.missing_fields == ("currency",)
    assert check.owner == "core-banking@example.com"
    assert "currency" in check.message()


def test_a_rename_is_breaking_and_reports_both_halves() -> None:
    """The absent old name decides the verdict; the new name rides along so the producer sees both."""
    registry = ContractRegistry.load(CONTRACTS)
    observed = (set(registry.contracts["cards"].required) - {"network"}) | {"card_network"}
    check = registry.check("cards", observed)
    assert check.verdict is Verdict.BREAKING
    assert check.missing_fields == ("network",)
    assert check.unknown_fields == ("card_network",)


def test_an_extra_column_is_additive_not_breaking() -> None:
    registry = ContractRegistry.load(CONTRACTS)
    observed = set(registry.contracts["merchants"].required) | {"vendor_confidence_score"}
    check = registry.check("merchants", observed)
    assert check.verdict is Verdict.ADDITIVE
    assert check.unknown_fields == ("vendor_confidence_score",)


def test_a_nullable_avro_field_with_a_default_is_optional_not_required() -> None:
    """v1 files omit the two fields v2 added. Because both carry a default, v1 stays compatible."""
    registry = ContractRegistry.load(CONTRACTS)
    v1_shape = set(registry.contracts["card_transactions"].required)
    assert "pos_entry_mode" not in v1_shape
    assert registry.check("card_transactions", v1_shape).verdict is Verdict.COMPATIBLE


def test_a_breaking_feed_refuses_the_batch_and_lands_nothing(tmp_path: Path) -> None:
    generate("smoke", tmp_path, seed=12)
    accounts = tmp_path / "raw" / "accounts" / "accounts.csv"
    df = pd.read_csv(accounts).drop(columns=["currency"])
    df.to_csv(accounts, index=False)

    with pytest.raises(SchemaDriftError) as exc:
        ingest(tmp_path / "raw", tmp_path / "lake", CONTRACTS)
    assert "currency" in str(exc.value)
    assert not (tmp_path / "lake" / "card_transactions").exists(), "a refused batch must land nothing"


def test_force_overrides_the_refusal(tmp_path: Path) -> None:
    generate("smoke", tmp_path, seed=13)
    cards = tmp_path / "raw" / "cards" / "cards.csv"
    pd.read_csv(cards).rename(columns={"network": "card_network"}).to_csv(cards, index=False)
    result = ingest(tmp_path / "raw", tmp_path / "lake", CONTRACTS, force=True)
    assert result.row_counts["card_transactions"] > 0
    assert any("BREAKING" in m for m in result.contract_checks)


def test_the_whole_history_reads_as_one_shape_despite_two_feed_versions(landed: Path) -> None:
    """The payoff of the evolution work: one frame, one set of columns, no version branching."""
    from card_spend.ingest.lake import read_lake_transactions

    df = read_lake_transactions(landed / "lake")
    assert set(df["feed_version"].unique()) == {1, 2}
    v1 = df[df["feed_version"] == 1]
    v2 = df[df["feed_version"] == 2]
    assert v1["pos_entry_mode"].isna().all(), "v1 predates the field, so it must be null"
    assert v2["pos_entry_mode"].notna().all(), "v2 always populates it"
    assert list(v1.columns) == list(v2.columns)


def test_posted_date_lives_in_the_partition_path_not_the_file(landed: Path) -> None:
    """Hive convention. Storing the partition key in both places lets the two copies disagree."""
    import pyarrow.parquet as pq

    part = sorted((landed / "lake" / "card_transactions").glob("posted_date=*/part-*.parquet"))[0]
    assert "posted_date" not in pq.read_schema(part).names

    from card_spend.ingest.lake import read_lake_transactions

    df = read_lake_transactions(landed / "lake", columns=["transaction_id", "posted_date"])
    assert df["posted_date"].nunique() == len(list((landed / "lake" / "card_transactions").glob("posted_date=*")))


def test_the_lake_keeps_the_partitioning(landed: Path) -> None:
    raw_parts = len(list((landed / "raw" / "card_transactions").glob("posted_date=*")))
    lake_parts = len(list((landed / "lake" / "card_transactions").glob("posted_date=*")))
    assert raw_parts == lake_parts > 0


def test_no_rows_are_lost_or_invented_between_raw_and_lake(landed: Path) -> None:
    manifest = json.loads((landed / "raw" / "_manifest.json").read_text())
    landed_report = json.loads((landed / "lake" / "_ingest.json").read_text())
    for feed in ("customers", "accounts", "cards", "merchants", "fx_rates", "card_transactions"):
        assert landed_report["row_counts"][feed] == manifest["row_counts"][feed], feed


def test_the_nested_customer_document_survives_flattening(landed: Path) -> None:
    """Flattening is for the warehouse. The document store on day 4 needs the original back."""
    df = pd.read_parquet(landed / "lake" / "customers" / "customers.parquet")
    doc = json.loads(df["document"].iloc[0])
    assert doc["kyc"]["status"] == df["kyc_status"].iloc[0]
    assert doc["address"]["postcode"] == df["postcode"].iloc[0]


def test_csv_ids_keep_their_declared_string_type(landed: Path) -> None:
    """An inferred dtype is how an id column of digits silently becomes an int between two loads."""
    accounts = readers.read_accounts(landed / "raw" / "accounts" / "accounts.csv")
    assert str(accounts["account_id"].dtype) == "string"
    assert str(accounts["overdraft_limit_minor"].dtype) == "int64"


def test_contract_observation_does_not_read_the_whole_feed(landed: Path, tmp_path: Path) -> None:
    """Refusing a bad feed should cost a header read. Copy a partition, truncate the body, observe."""
    part = sorted((landed / "raw" / "card_transactions").glob("posted_date=*/part-*.avro"))[0]
    original = part.read_bytes()
    clipped = tmp_path / "clipped.avro"
    clipped.write_bytes(original[: len(original) // 3])
    assert readers.observe_avro(clipped) == readers.observe_avro(part)


def test_observe_all_skips_feeds_that_did_not_arrive(landed: Path, tmp_path: Path) -> None:
    partial = tmp_path / "raw"
    shutil.copytree(landed / "raw", partial)
    shutil.rmtree(partial / "merchants")
    observed = readers.observe_all(partial)
    assert "merchants" not in observed
    assert "card_transactions" in observed
