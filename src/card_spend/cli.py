"""Command line entry point.

argparse rather than a CLI framework, deliberately: the only third-party dependencies in this repo
should be ones that do data work.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from card_spend.generate import spec
from card_spend.generate.generator import generate

DEFAULT_CONTRACTS = Path("contracts")


def _add_data_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", type=Path, default=Path("data"), help="dataset root, holds raw/ and lake/")
    p.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="card-spend", description="Card spend analytics")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate the synthetic raw feeds in their native formats")
    gen.add_argument("--profile", choices=sorted(spec.PROFILES), default="dev")
    gen.add_argument("--out", type=Path, default=Path("data"))
    gen.add_argument("--seed", type=int, default=20260916)

    sub.add_parser("profiles", help="List the scale profiles and their approximate sizes")

    check = sub.add_parser("contracts", help="Check each feed against its contract, landing nothing")
    _add_data_args(check)

    ing = sub.add_parser("ingest", help="Land every feed into the Parquet lake under one canonical schema")
    _add_data_args(ing)
    ing.add_argument("--force", action="store_true", help="land anyway after a breaking verdict")

    build = sub.add_parser("build", help="Run the dbt models over the lake")
    _add_data_args(build)
    build.add_argument("--target", default="dev")

    load = sub.add_parser("load", help="Load the marts into Postgres and the customer documents into Redis")
    _add_data_args(load)
    load.add_argument("--stores", default="postgres,redis")

    bench = sub.add_parser("bench", help="Run the access-pattern benchmark across every reachable store")
    _add_data_args(bench)
    bench.add_argument("--repeats", type=int, default=5)
    bench.add_argument("--out", type=Path, default=Path("docs/benchmarks/results.md"))

    report = sub.add_parser("report", help="Build the reports and the HTML dashboard")
    _add_data_args(report)
    report.add_argument("--out", type=Path, default=Path("out"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "profiles":
        for name, p in sorted(spec.PROFILES.items(), key=lambda kv: kv[1].approx_transactions):
            print(
                f"{name:<6} customers={p.customers:>9,}  merchants={p.merchants:>7,}  "
                f"days={p.days:>4}  approx_transactions={p.approx_transactions:>12,}"
            )
        return 0

    if args.command == "generate":
        manifest = generate(args.profile, args.out, seed=args.seed)
        print(
            json.dumps(
                {
                    "row_counts": manifest.row_counts,
                    "defect_counts": manifest.defect_counts,
                    "seconds": manifest.seconds,
                },
                indent=2,
            )
        )
        return 0

    from card_spend.contracts import Verdict
    from card_spend.ingest.lake import SchemaDriftError, check_contracts, ingest

    if args.command == "contracts":
        checks = check_contracts(args.data / "raw", args.contracts)
        for c in checks:
            print(c.message())
        return 2 if any(c.verdict is Verdict.BREAKING for c in checks) else 0

    if args.command == "ingest":
        try:
            result = ingest(args.data / "raw", args.data / "lake", args.contracts, force=args.force)
        except SchemaDriftError as exc:
            print(f"refused: {exc}")
            return 2
        print(
            json.dumps(
                {"row_counts": result.row_counts, "partitions": result.partitions, "seconds": result.seconds},
                indent=2,
            )
        )
        return 0

    if args.command == "build":
        from card_spend.models.run import run_dbt

        return run_dbt(args.data, target=args.target)

    if args.command == "load":
        from card_spend.stores.load import load_stores

        print(json.dumps(load_stores(args.data, args.stores.split(",")), indent=2, default=str))
        return 0

    if args.command == "bench":
        from card_spend.bench.harness import run_benchmark

        print(run_benchmark(args.data, repeats=args.repeats, out_path=args.out))
        return 0

    if args.command == "report":
        from card_spend.reports.build import build_reports

        print(json.dumps(build_reports(args.data, args.out), indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
