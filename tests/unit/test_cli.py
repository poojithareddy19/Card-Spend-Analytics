"""The command line entry point.

Every Makefile target is a thin wrapper around one of these subcommands, so an argument that stops
parsing breaks the documented way into the project. The defaults matter as much as the dispatch:
they are the mechanism by which `config/settings.yaml` reaches the CLI at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from card_spend import config
from card_spend.cli import _build_parser, main
from card_spend.generate import spec


def test_profiles_lists_every_scale_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profiles"]) == 0
    printed = capsys.readouterr().out
    for name in spec.PROFILES:
        assert name in printed


def test_the_profiles_are_ordered_smallest_first(capsys: pytest.CaptureFixture[str]) -> None:
    main(["profiles"])
    printed = capsys.readouterr().out
    assert printed.index("smoke") < printed.index("dev") < printed.index("bench")


def test_the_generate_defaults_come_from_the_settings_file() -> None:
    """The settings file is only real if the CLI actually starts from it."""
    args = _build_parser().parse_args(["generate"])
    assert args.profile == config.get_str("generation.default_profile", "dev")
    assert args.seed == config.get_int("generation.seed", 20260916)
    assert args.out == Path(config.get_str("paths.data_dir", "./data"))


def test_the_data_and_contract_defaults_come_from_the_settings_file() -> None:
    args = _build_parser().parse_args(["ingest"])
    assert args.data == Path(config.get_str("paths.data_dir", "./data"))
    assert args.contracts == Path(config.get_str("paths.contracts_dir", "./contracts"))


def test_the_bench_repeats_default_comes_from_the_settings_file() -> None:
    args = _build_parser().parse_args(["bench"])
    assert args.repeats == config.get_int("benchmark.repeats", 5)


def test_every_subcommand_the_makefile_uses_parses() -> None:
    parser = _build_parser()
    for command in ("generate", "profiles", "contracts", "ingest", "build", "load", "bench", "report"):
        assert parser.parse_args([command]).command == command


def test_a_missing_subcommand_is_refused() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_an_unknown_subcommand_is_refused() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["deploy"])


def test_contracts_reports_a_breaking_verdict_as_a_non_zero_exit(tmp_path: Path) -> None:
    """The exit code is what a scheduler acts on, so it has to be the refusal, not just the text."""
    feed = tmp_path / "raw" / "cards"
    feed.mkdir(parents=True)
    # A cards extract carrying its key and nothing else the contract promises.
    (feed / "cards.csv").write_text("card_id\nCRD-000000000001\n", encoding="utf-8")
    assert main(["contracts", "--data", str(tmp_path)]) == 2


def test_contracts_on_a_dataset_with_no_feeds_at_all_is_not_a_refusal(tmp_path: Path) -> None:
    """Nothing delivered is an empty run, not a breaking change. The scheduler should not page."""
    (tmp_path / "raw").mkdir()
    assert main(["contracts", "--data", str(tmp_path)]) == 0
