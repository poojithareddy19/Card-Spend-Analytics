"""Run the reports and build the dashboard.

Every report is written as CSV as well as rendered, because the people who ask for these numbers
mostly want to put them in a spreadsheet, and a dashboard that cannot be exported gets rebuilt by
hand in Excel within a fortnight.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from card_spend.reports import dashboard as dash
from card_spend.reports.queries import REPORTS, REPORTS_BY_KEY

TOP_CATEGORIES = 8


def run_reports(data_dir: Path) -> dict[str, pd.DataFrame]:
    lake = str((data_dir / "lake").resolve())
    con = duckdb.connect(":memory:")
    try:
        return {r.key: con.execute(r.sql.format(lake=lake)).df() for r in REPORTS}
    finally:
        con.close()


def build_reports(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    frames = run_reports(data_dir)
    out_dir = Path(out_dir)
    (out_dir / "csv").mkdir(parents=True, exist_ok=True)

    for key, frame in frames.items():
        frame.to_csv(out_dir / "csv" / f"{key}.csv", index=False)

    html_path = out_dir / "dashboard.html"
    html_path.write_text(_render(frames, data_dir), encoding="utf-8")

    return {
        "reports": {k: len(v) for k, v in frames.items()},
        "csv_dir": str(out_dir / "csv"),
        "dashboard": str(html_path),
    }


def _fold_tail(frame: pd.DataFrame, label_col: str, value_col: str, keep: int) -> pd.DataFrame:
    """Keep the top N and fold the rest into "Other".

    Categorical hues are assigned in fixed order and never cycled, and the same discipline applies
    to bar rows: past a readable count, the tail becomes one honest "Other" rather than a long list
    nobody reads to the bottom of.
    """
    ordered = frame.sort_values(value_col, ascending=False)
    if len(ordered) <= keep:
        return ordered
    head = ordered.head(keep).copy()
    tail_total = ordered.tail(len(ordered) - keep)[value_col].sum()
    other = pd.DataFrame({label_col: ["Other"], value_col: [tail_total]})
    return pd.concat([head[[label_col, value_col]], other], ignore_index=True)


def _render(frames: dict[str, pd.DataFrame], data_dir: Path) -> str:
    head = frames["headline"].iloc[0]
    months = frames["spend_by_month"]
    categories = frames["spend_by_category"]
    channel = frames["channel_mix_by_month"]
    segments = frames["spend_by_segment"].sort_values("spend_per_customer_gbp", ascending=False)
    dq_dim = frames["dq_by_dimension"]
    dq_rule = frames["dq_by_defect"]
    fx = frames["fx_exposure"]
    lag = frames["posting_lag"]

    tiles = "".join(
        [
            _tile("Net spend", dash._money(head["spend_gbp"]), f"{int(head['txn_count']):,} settled transactions"),
            _tile("Active customers", f"{int(head['active_customers']):,}", "with at least one settled purchase"),
            _tile("Average basket", dash._money(head["avg_basket_gbp"], 2), "spend divided by settled purchases"),
            _tile("Online share", f"{head['online_share']:.1%}", "card-not-present as a share of spend"),
        ]
    )

    cards: list[str] = []

    r = REPORTS_BY_KEY["spend_by_month"]
    cards.append(
        dash.card(
            r.title,
            r.question,
            r.date_basis,
            dash.line_chart(months["month_key"].tolist(), months["spend_gbp"].tolist())
            + dash.table(
                ["Month", "Spend", "Transactions"],
                [
                    (m, dash._money(s), f"{int(t):,}")
                    for m, s, t in zip(months["month_key"], months["spend_gbp"], months["txn_count"], strict=True)
                ],
                [False, True, True],
            ),
        )
    )

    folded = _fold_tail(categories, "category", "spend_gbp", TOP_CATEGORIES)
    r = REPORTS_BY_KEY["spend_by_category"]
    cards.append(
        dash.card(
            r.title,
            r.question,
            r.date_basis,
            dash.hbar_chart([dash.pretty(c) for c in folded["category"]], folded["spend_gbp"].tolist())
            + dash.table(
                ["Category", "Spend", "Transactions", "Average basket"],
                [
                    (dash.pretty(c), dash._money(s), f"{int(n):,}", dash._money(a, 2))
                    for c, s, n, a in zip(
                        categories["category"],
                        categories["spend_gbp"],
                        categories["txn_count"],
                        categories["avg_basket_gbp"],
                        strict=True,
                    )
                ],
                [False, True, True, True],
            ),
        )
    )

    r = REPORTS_BY_KEY["channel_mix_by_month"]
    cards.append(
        dash.card(
            r.title,
            r.question,
            r.date_basis,
            dash.legend([("Card present", "var(--series-1)"), ("Online", "var(--series-2)")])
            + dash.stacked_bar_chart(
                channel["month_key"].tolist(),
                channel["card_present_gbp"].tolist(),
                channel["online_gbp"].tolist(),
                ("Card present", "Online"),
            )
            + dash.table(
                ["Month", "Card present", "Online", "Online share"],
                [
                    (m, dash._money(a), dash._money(b), f"{(b / (a + b or 1)):.1%}")
                    for m, a, b in zip(
                        channel["month_key"], channel["card_present_gbp"], channel["online_gbp"], strict=True
                    )
                ],
                [False, True, True, True],
            ),
        )
    )

    r = REPORTS_BY_KEY["dq_by_dimension"]
    cards.append(
        dash.card(
            r.title,
            r.question,
            r.date_basis,
            dash.rate_chart([dash.pretty(d) for d in dq_dim["dimension"]], dq_dim["pass_rate"].tolist())
            + dash.table(
                ["Rule", "Dimension", "Rows checked", "Rows failed", "Pass rate"],
                [
                    (code, dash.pretty(dim), f"{int(n):,}", f"{int(f):,}", f"{p:.3%}")
                    for code, dim, n, f, p in zip(
                        dq_rule["defect_code"],
                        dq_rule["dimension"],
                        dq_rule["rows_checked"],
                        dq_rule["rows_failed"],
                        dq_rule["pass_rate"],
                        strict=True,
                    )
                ],
                [False, False, True, True, True],
            ),
        )
    )

    r = REPORTS_BY_KEY["spend_by_segment"]
    cards.append(
        dash.card(
            r.title,
            r.question,
            r.date_basis,
            dash.hbar_chart(
                [dash.pretty(s) for s in segments["segment"]],
                segments["spend_per_customer_gbp"].tolist(),
                tips=[
                    f"<b>{dash.pretty(s)}</b><br>{int(c):,} customers<br>"
                    f"{dash._money(pc, 2)} per customer<br>{dash._money(ab, 2)} average basket"
                    for s, c, pc, ab in zip(
                        segments["segment"],
                        segments["customers"],
                        segments["spend_per_customer_gbp"],
                        segments["avg_basket_gbp"],
                        strict=True,
                    )
                ],
            )
            + dash.table(
                ["Segment", "Customers", "Spend", "Spend per customer", "Average basket"],
                [
                    (dash.pretty(s), f"{int(c):,}", dash._money(sp), dash._money(pc, 2), dash._money(ab, 2))
                    for s, c, sp, pc, ab in zip(
                        segments["segment"],
                        segments["customers"],
                        segments["spend_gbp"],
                        segments["spend_per_customer_gbp"],
                        segments["avg_basket_gbp"],
                        strict=True,
                    )
                ],
                [False, True, True, True, True],
            ),
        )
    )

    fx_rows = [
        (c, f"{int(n):,}", dash._money(b), dash._money(rv), dash._money(u))
        for c, n, b, rv, u in zip(
            fx["currency"],
            fx["txn_count"],
            fx["booked_gbp"],
            fx["revalued_gbp"],
            fx["unrealised_gbp"],
            strict=True,
        )
    ]
    lag_rows = [
        (m, f"{a:.2f}", f"{p:.1f}", f"{int(b):,}", f"{int(b) / int(t):.2%}")
        for m, a, p, b, t in zip(
            lag["month_key"],
            lag["avg_lag_days"],
            lag["p95_lag_days"],
            lag["breaches"],
            lag["txn_count"],
            strict=True,
        )
    ]
    cards.append(
        dash.card(
            "Treasury and operations",
            "What is the foreign-currency exposure, and is the posting lag holding to its SLO",
            "posted date",
            dash.table(
                ["Currency", "Transactions", "Booked", "Revalued at latest rate", "Unrealised"],
                fx_rows,
                [False, True, True, True, True],
                label="Foreign currency exposure",
            )
            + dash.table(
                ["Month", "Average lag (days)", "p95 lag", "Breaches over 2 days", "Breach rate"],
                lag_rows,
                [False, True, True, True, True],
                label="Posting lag against the two-day SLO",
            ),
        )
    )

    partitions = len(list((data_dir / "lake" / "marts" / "fct_card_transaction").glob("posted_date=*")))
    meta = (
        f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} from {int(head['txn_count']):,} settled transactions "
        f"across {partitions} daily partitions. Average row quality score "
        f"{head['avg_dq_score']:.1f} out of 100."
    )
    footer = (
        "Synthetic data. Every figure is reproducible from the seeded generator: "
        "<code>card-spend generate --profile dev</code>, then ingest, build and report. "
        "Definitions for every term used here are in <code>docs/model/03_glossary.md</code>."
    )
    return dash.page(
        "Card spend and data quality",
        "Card spend analytics",
        meta,
        tiles,
        "\n".join(cards),
        footer,
    )


def _tile(label: str, value: str, sub: str) -> str:
    return f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{sub}</div></div>'
