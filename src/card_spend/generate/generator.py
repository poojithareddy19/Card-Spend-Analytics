"""Seeded generator for the synthetic UK retail bank.

Design rules this file sticks to:

* **Seeded and reproducible.** The same seed and profile produce byte-identical output, so a golden
  test and a benchmark quote the same dataset.
* **Native format per feed.** Customers arrive as nested JSON, transactions as Avro, merchants as
  Parquet, accounts and cards as CSV, rates as JSON. Nothing is CSV-for-convenience.
* **Defects are declared, not accidental.** Every injected defect is recorded in the manifest with
  an exact count, so downstream rules are asserted rather than eyeballed.
* **Partitioned on write.** Transactions land under ``posted_date=YYYY-MM-DD/``, which is what makes
  the day-5 partition-pruning benchmark measurable rather than theoretical.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

from card_spend.generate import spec, writers

EPOCH = dt.date(1970, 1, 1)

# ISO 8583 field 22, point-of-service entry mode. Added by the processor in feed version 2.
POS_ENTRY_MODE = {
    "contactless": "07",
    "chip_and_pin": "05",
    "ecommerce": "81",
    "atm": "05",
    "mail_order": "01",
}


def _street(rng: np.random.Generator, city_idx: int) -> str:
    """A plausible UK street line. Kept out of the record literal so that line stays readable."""
    name = spec.MERCHANT_PREFIXES[city_idx % len(spec.MERCHANT_PREFIXES)]
    return f"{rng.integers(1, 250)} {name} Road"


CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


def _days_since_epoch(d: dt.date) -> int:
    return (d - EPOCH).days


@dataclass
class Manifest:
    """What the generator promises it produced. Written to ``_manifest.json`` beside the data."""

    profile: str
    seed: int
    start_date: str
    end_date: str
    v2_switch_date: str
    row_counts: dict[str, int] = field(default_factory=dict)
    defect_counts: dict[str, int] = field(default_factory=dict)
    defect_catalogue: list[dict[str, Any]] = field(default_factory=list)
    partitions: dict[str, int] = field(default_factory=dict)
    bytes_on_disk: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)


class Generator:
    def __init__(
        self,
        profile: spec.ScaleProfile,
        out_dir: Path,
        seed: int = 20260916,
        end_date: dt.date | None = None,
    ) -> None:
        self.profile = profile
        self.out = out_dir
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.end_date = end_date or dt.date(2026, 6, 30)
        self.start_date = self.end_date - dt.timedelta(days=profile.days - 1)
        # The processor upgrades its feed two thirds of the way through the window, so any consumer
        # reading the full history has to handle both schema versions in one pass.
        self.v2_switch = self.start_date + dt.timedelta(days=int(profile.days * 0.66))
        self.manifest = Manifest(
            profile=profile.name,
            seed=seed,
            start_date=self.start_date.isoformat(),
            end_date=self.end_date.isoformat(),
            v2_switch_date=self.v2_switch.isoformat(),
            defect_catalogue=[asdict(d) for d in spec.DEFECTS],
        )
        self.manifest.defect_counts = {d.code: 0 for d in spec.DEFECTS}
        # (transaction_id, defect_code) for every injected transaction defect. Written beside the
        # data as `_defects.parquet`, which is what makes a detection rule's recall and precision
        # measurable rather than a matter of opinion.
        self.defect_ledger: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ helpers

    def _weighted(self, options: Sequence[tuple[str, float, *tuple[Any, ...]]], n: int) -> np.ndarray:
        names = np.array([o[0] for o in options])
        p = np.array([o[1] for o in options], dtype=float)
        return self.rng.choice(names, size=n, p=p / p.sum())

    def _bump(self, code: str, n: int) -> None:
        self.manifest.defect_counts[code] += int(n)

    # ------------------------------------------------------------------ customers

    def customers(self) -> pd.DataFrame:
        n = self.profile.customers
        ids = np.array([f"CUS-{i:09d}" for i in range(1, n + 1)])
        segments = self._weighted(spec.SEGMENTS, n)
        seg_mult = {s[0]: s[2] for s in spec.SEGMENTS}

        # Onboarding is spread over the four years before the window closes, weighted towards recent.
        age_days = (self.rng.beta(1.6, 2.4, n) * 1460).astype(int)
        created = np.array([self.end_date - dt.timedelta(days=int(a)) for a in age_days])

        city_idx = self.rng.integers(0, len(spec.UK_CITIES), n)
        kyc_status = self._weighted(spec.KYC_STATUSES, n)
        risk_band = self._weighted(spec.RISK_BANDS, n)
        dob_year = self.rng.integers(1950, 2007, n)

        bad_postcode = self.rng.random(n) < spec.DEFECTS[7].rate  # D08
        missing_verified = (self.rng.random(n) < spec.DEFECTS[8].rate) & (kyc_status == "verified")  # D09
        self._bump("D08", bad_postcode.sum())
        self._bump("D09", missing_verified.sum())

        records: list[dict[str, Any]] = []
        for i in range(n):
            city, pc_prefix = spec.UK_CITIES[int(city_idx[i])]
            postcode = (
                f"{pc_prefix} {self.rng.integers(1, 10)}{chr(65 + int(self.rng.integers(0, 26)))}"
                f"{chr(65 + int(self.rng.integers(0, 26)))}"
            )
            if bad_postcode[i]:
                postcode = postcode.replace(" ", "").lower()  # the shape a hand-typed form produces
            verified_at = None
            if kyc_status[i] == "verified" and not missing_verified[i]:
                verified_at = (created[i] + dt.timedelta(days=int(self.rng.integers(0, 3)))).isoformat() + "T09:00:00Z"
            records.append(
                {
                    "customer_id": ids[i],
                    "created_at": created[i].isoformat() + "T00:00:00Z",
                    "segment": segments[i],
                    "marketing_consent": bool(self.rng.random() < 0.58),
                    "profile": {
                        "first_name": f"Customer{i + 1}",
                        "last_name": spec.MERCHANT_PREFIXES[i % len(spec.MERCHANT_PREFIXES)],
                        "date_of_birth": f"{dob_year[i]}-{self.rng.integers(1, 13):02d}-{self.rng.integers(1, 29):02d}",
                        "email": f"customer{i + 1}@example.invalid",
                        "phone": f"+447{self.rng.integers(100000000, 999999999)}" if self.rng.random() < 0.92 else None,
                    },
                    "address": {
                        "line1": _street(self.rng, int(city_idx[i])),
                        "line2": None,
                        "city": city,
                        "postcode": postcode,
                        "country": "GB",
                    },
                    "kyc": {
                        "status": kyc_status[i],
                        "verified_at": verified_at,
                        "risk_band": risk_band[i],
                    },
                }
            )

        path = self.out / "raw" / "customers" / "customers.jsonl"
        self.manifest.row_counts["customers"] = writers.write_jsonl(path, records)
        self.manifest.bytes_on_disk["customers"] = path.stat().st_size

        return pd.DataFrame(
            {
                "customer_id": ids,
                "segment": segments,
                "spend_multiplier": [seg_mult[s] for s in segments],
                "created_date": created,
            }
        )

    # ------------------------------------------------------------------ accounts and cards

    def accounts_and_cards(self, customers: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        n_cust = len(customers)
        per_customer = self.rng.choice([1, 2, 3], size=n_cust, p=[0.62, 0.30, 0.08])
        cust_idx = np.repeat(np.arange(n_cust), per_customer)
        n_acc = len(cust_idx)

        account_ids = np.array([f"ACC-{i:010d}" for i in range(1, n_acc + 1)])
        products = self._weighted(spec.PRODUCTS, n_acc)
        status = self.rng.choice(["open", "dormant", "closed"], size=n_acc, p=[0.88, 0.07, 0.05])
        opened = customers["created_date"].to_numpy()[cust_idx]
        closed = np.array([None] * n_acc, dtype=object)
        closed_mask = status == "closed"
        closed[closed_mask] = [(o + dt.timedelta(days=int(self.rng.integers(30, 900)))) for o in opened[closed_mask]]

        bad_closed = (self.rng.random(n_acc) < spec.DEFECTS[9].rate) & (status == "open")  # D10
        self._bump("D10", bad_closed.sum())
        closed[bad_closed] = [o + dt.timedelta(days=45) for o in opened[bad_closed]]

        accounts = pd.DataFrame(
            {
                "account_id": account_ids,
                "customer_id": customers["customer_id"].to_numpy()[cust_idx],
                "product_code": products,
                "currency": "GBP",
                "status": status,
                "opened_date": [o.isoformat() for o in opened],
                "closed_date": [c.isoformat() if c is not None else "" for c in closed],
                "overdraft_limit_minor": self.rng.choice(
                    [0, 25000, 50000, 100000, 250000], size=n_acc, p=[0.35, 0.25, 0.20, 0.15, 0.05]
                ),
            }
        )

        cards_per_account = self.rng.choice([1, 2], size=n_acc, p=[0.82, 0.18])
        acc_idx = np.repeat(np.arange(n_acc), cards_per_account)
        n_card = len(acc_idx)
        card_ids = np.array([f"CRD-{i:012x}" for i in range(1, n_card + 1)])
        issued = np.array([opened[i] + dt.timedelta(days=int(self.rng.integers(0, 60))) for i in acc_idx])

        cards = pd.DataFrame(
            {
                "card_id": card_ids,
                "account_id": account_ids[acc_idx],
                "card_type": self.rng.choice(["debit", "credit"], size=n_card, p=[0.86, 0.14]),
                "network": self.rng.choice(["visa", "mastercard"], size=n_card, p=[0.58, 0.42]),
                "issued_date": [i.isoformat() for i in issued],
                "expiry_date": [(i + dt.timedelta(days=365 * 4)).isoformat() for i in issued],
                "status": self._weighted(spec.CARD_STATUSES, n_card),
            }
        )

        acc_path = self.out / "raw" / "accounts" / "accounts.csv"
        card_path = self.out / "raw" / "cards" / "cards.csv"
        self.manifest.row_counts["accounts"] = writers.write_csv(acc_path, accounts)
        self.manifest.row_counts["cards"] = writers.write_csv(card_path, cards)
        self.manifest.bytes_on_disk["accounts"] = acc_path.stat().st_size
        self.manifest.bytes_on_disk["cards"] = card_path.stat().st_size

        # Spend weight per card comes from the owning customer's segment.
        cards_meta = pd.DataFrame(
            {
                "card_id": card_ids,
                "account_id": account_ids[acc_idx],
                "spend_multiplier": customers["spend_multiplier"].to_numpy()[cust_idx][acc_idx],
                "active": (cards["status"].to_numpy() == "active"),
            }
        )
        return accounts, cards_meta

    # ------------------------------------------------------------------ merchants

    def merchants(self) -> pd.DataFrame:
        n = self.profile.merchants
        mcc_idx = self.rng.choice(
            len(spec.MCCS), size=n, p=[m.weight for m in spec.MCCS] / np.sum([m.weight for m in spec.MCCS])
        )
        mccs = np.array([spec.MCCS[i].mcc for i in mcc_idx])
        cats = np.array([spec.MCCS[i].category for i in mcc_idx])
        online = np.array([self.rng.random() < spec.MCCS[i].online_share for i in mcc_idx])

        names = [
            f"{spec.MERCHANT_PREFIXES[int(self.rng.integers(0, len(spec.MERCHANT_PREFIXES)))]} "
            f"{spec.MERCHANT_SUFFIXES[int(self.rng.integers(0, len(spec.MERCHANT_SUFFIXES)))]}"
            for _ in range(n)
        ]
        first_seen = [self.start_date - dt.timedelta(days=int(self.rng.integers(0, 1500))) for _ in range(n)]

        df = pd.DataFrame(
            {
                "merchant_id": [f"MER-{i:08d}" for i in range(1, n + 1)],
                "merchant_name": names,
                "mcc": mccs.astype("int32"),
                "category": cats,
                "country": self.rng.choice(
                    ["GB", "IE", "FR", "DE", "US", "ES"], size=n, p=[0.88, 0.02, 0.025, 0.025, 0.035, 0.015]
                ),
                "is_online": online,
                "first_seen_date": pd.to_datetime(first_seen).date,
            }
        )
        schema = pa.schema(
            [
                ("merchant_id", pa.string()),
                ("merchant_name", pa.string()),
                ("mcc", pa.int32()),
                ("category", pa.string()),
                ("country", pa.string()),
                ("is_online", pa.bool_()),
                ("first_seen_date", pa.date32()),
            ]
        )
        path = self.out / "raw" / "merchants" / "merchants.parquet"
        self.manifest.row_counts["merchants"] = writers.write_parquet(path, df, schema)
        self.manifest.bytes_on_disk["merchants"] = path.stat().st_size
        return df

    # ------------------------------------------------------------------ fx rates

    def fx_rates(self) -> None:
        rows = 0
        gaps = 0
        for offset in range(self.profile.days):
            d = self.start_date + dt.timedelta(days=offset)
            if offset % spec.FX_GAP_EVERY_N_DAYS == 0 and offset > 0:
                gaps += 1  # D11: whole day missing, the as-of join has to carry the previous rate
                continue
            drift = 1 + self.rng.normal(0, 0.004)
            payload = [
                {
                    "rate_date": d.isoformat(),
                    "currency": code,
                    # The reporting currency is quoted against itself, so it is exactly 1 and never
                    # drifts. A drifting home rate is how a GBP transaction ends up converting to
                    # something other than itself, which nothing downstream would ever reconcile.
                    "rate_to_gbp": 1.0 if code == "GBP" else round(base * drift, 6),
                    "source": "treasury-rates-api",
                }
                for code, _share, base in spec.CURRENCIES
            ]
            writers.write_json(self.out / "raw" / "fx_rates" / f"rate_date={d.isoformat()}" / "rates.json", payload)
            rows += len(payload)
        self.manifest.row_counts["fx_rates"] = rows
        self._bump("D11", gaps)
        self.manifest.partitions["fx_rates"] = self.profile.days - gaps

    # ------------------------------------------------------------------ transactions

    def transactions(self, cards_meta: pd.DataFrame, merchants: pd.DataFrame) -> None:
        v1 = writers.load_avro_schema(CONTRACTS_DIR / "card_transactions.v1.avsc")
        v2 = writers.load_avro_schema(CONTRACTS_DIR / "card_transactions.v2.avsc")

        card_ids = cards_meta["card_id"].to_numpy()
        account_ids = cards_meta["account_id"].to_numpy()
        weights = cards_meta["spend_multiplier"].to_numpy() * np.where(cards_meta["active"].to_numpy(), 1.0, 0.05)
        weights = weights / weights.sum()

        merchant_ids = merchants["merchant_id"].to_numpy()
        merchant_mcc = merchants["mcc"].to_numpy()
        merchant_online = merchants["is_online"].to_numpy()

        cur_codes = np.array([c[0] for c in spec.CURRENCIES])
        cur_p = np.array([c[1] for c in spec.CURRENCIES])
        cur_p = cur_p / cur_p.sum()

        mcc_lookup = {m.mcc: m for m in spec.MCCS}

        total = 0
        partitions = 0
        total_bytes = 0
        seq = 0

        for offset in range(self.profile.days):
            d = self.start_date + dt.timedelta(days=offset)
            base = self.profile.customers * self.profile.txns_per_customer_per_day
            factor = spec.WEEKDAY_FACTOR[d.weekday()] * spec.MONTH_FACTOR[d.month - 1]
            n = int(self.rng.poisson(base * factor))
            if n <= 0:
                continue

            ci = self.rng.choice(len(card_ids), size=n, p=weights)
            mi = self.rng.integers(0, len(merchant_ids), n)
            mcc = merchant_mcc[mi].copy()
            currency = self.rng.choice(cur_codes, size=n, p=cur_p)

            mu = np.array([mcc_lookup[int(m)].amount_mu if int(m) in mcc_lookup else 7.5 for m in mcc])
            sigma = np.array([mcc_lookup[int(m)].amount_sigma if int(m) in mcc_lookup else 0.8 for m in mcc])
            amount = (self.rng.lognormal(mu, sigma) * cards_meta["spend_multiplier"].to_numpy()[ci]).astype(np.int64)
            amount = np.clip(amount, 50, 500_000_00)

            txn_type = np.where(mcc == 6011, "atm_withdrawal", "purchase").astype(object)
            is_refund = self.rng.random(n) < 0.021
            txn_type[is_refund & (txn_type == "purchase")] = "refund"
            is_fee = self.rng.random(n) < 0.004
            txn_type[is_fee] = "fee"
            amount = np.where(txn_type == "refund", -amount, amount)

            channel = np.where(
                mcc == 6011,
                "atm",
                np.where(
                    merchant_online[mi], "ecommerce", np.where(self.rng.random(n) < 0.72, "contactless", "chip_and_pin")
                ),
            ).astype(object)

            status = self.rng.choice(
                ["settled", "authorised", "reversed", "declined"], size=n, p=[0.958, 0.021, 0.012, 0.009]
            )

            merchant_col = merchant_ids[mi].astype(object)
            merchant_col[mcc == 6011] = None
            merchant_col[txn_type == "fee"] = None

            lag_days = self.rng.choice([0, 1, 2], size=n, p=[0.58, 0.34, 0.08])

            # ---- defects -------------------------------------------------
            r = self.rng.random((7, n))
            d01 = (r[0] < spec.DEFECTS[0].rate) & (txn_type == "purchase")
            merchant_col[d01] = None
            self._bump("D01", d01.sum())

            d03 = r[2] < spec.DEFECTS[2].rate
            self._bump("D03", d03.sum())

            d04 = (r[3] < spec.DEFECTS[3].rate) & (txn_type == "purchase")
            amount = np.where(d04, -np.abs(amount), amount)
            self._bump("D04", d04.sum())

            d05 = r[4] < spec.DEFECTS[4].rate
            mcc = np.where(d05, 99999, mcc)
            self._bump("D05", d05.sum())

            d06 = r[5] < spec.DEFECTS[5].rate
            lag_days = np.where(d06, self.rng.integers(3, 21, n), lag_days)
            self._bump("D06", d06.sum())

            d07 = r[6] < spec.DEFECTS[6].rate
            amount = np.where(d07, amount * 100, amount)
            self._bump("D07", d07.sum())

            posted = _days_since_epoch(d)
            ts_micros = (
                (posted - lag_days) * 86_400_000_000 + self.rng.integers(7 * 3_600_000_000, 23 * 3_600_000_000, n)
            ).astype(np.int64)

            use_v2 = d >= self.v2_switch
            schema = v2 if use_v2 else v1

            card_col = card_ids[ci].astype(object)
            card_col[d03] = np.array([f"CRD-ffffffff{i:04x}" for i in range(int(d03.sum()))], dtype=object)

            # Transaction ids are assigned up front so the defect ledger below can name the exact
            # rows each defect was injected into. Without that, a detection rule's recall can only
            # be guessed at.
            ids = np.array([f"TXN-{(seq + k + 1):016x}" for k in range(n)], dtype=object)
            for code, mask in (("D01", d01), ("D03", d03), ("D04", d04), ("D05", d05), ("D06", d06), ("D07", d07)):
                for tid in ids[mask]:
                    self.defect_ledger.append((tid, code))

            records: list[dict[str, Any]] = []
            for k in range(n):
                seq += 1
                rec: dict[str, Any] = {
                    "transaction_id": ids[k],
                    "card_id": card_col[k],
                    "account_id": account_ids[ci[k]],
                    "merchant_id": merchant_col[k],
                    "txn_timestamp": int(ts_micros[k]),
                    "posted_date": posted,
                    "amount_minor": int(amount[k]),
                    "currency": str(currency[k]),
                    "mcc": int(mcc[k]),
                    "auth_code": None if txn_type[k] == "fee" else f"A{seq % 100000:05d}",
                    "txn_type": str(txn_type[k]),
                    "channel": str(channel[k]),
                    "status": str(status[k]),
                }
                if use_v2:
                    rec["pos_entry_mode"] = POS_ENTRY_MODE[rec["channel"]]
                    rec["is_recurring"] = bool(rec["mcc"] in (4816, 4900) and self.rng.random() < 0.6)
                records.append(rec)

            # D02: the processor emits the same row twice.
            dup_mask = self.rng.random(len(records)) < spec.DEFECTS[1].rate
            dups = [records[i] for i in np.flatnonzero(dup_mask)]
            records.extend(dups)
            for rec in dups:
                self.defect_ledger.append((rec["transaction_id"], "D02"))
            self._bump("D02", len(dups))

            path = self.out / "raw" / "card_transactions" / f"posted_date={d.isoformat()}" / "part-00000.avro"
            written = writers.write_avro(path, schema, records)
            total += written
            partitions += 1
            total_bytes += path.stat().st_size

        self.manifest.row_counts["card_transactions"] = total
        self.manifest.partitions["card_transactions"] = partitions
        self.manifest.bytes_on_disk["card_transactions"] = total_bytes

        ledger = pd.DataFrame(self.defect_ledger, columns=["transaction_id", "defect_code"])
        writers.write_parquet(self.out / "raw" / "_defects.parquet", ledger)
        self.manifest.row_counts["_defects"] = len(ledger)

    # ------------------------------------------------------------------ orchestration

    def run(self) -> Manifest:
        steps: list[tuple[str, Any]] = []
        t0 = time.perf_counter()
        customers = self.customers()
        steps.append(("customers", time.perf_counter() - t0))

        t = time.perf_counter()
        _accounts, cards_meta = self.accounts_and_cards(customers)
        steps.append(("accounts_and_cards", time.perf_counter() - t))

        t = time.perf_counter()
        merchants = self.merchants()
        steps.append(("merchants", time.perf_counter() - t))

        t = time.perf_counter()
        self.fx_rates()
        steps.append(("fx_rates", time.perf_counter() - t))

        t = time.perf_counter()
        self.transactions(cards_meta, merchants)
        steps.append(("card_transactions", time.perf_counter() - t))

        self.manifest.seconds = {name: round(secs, 3) for name, secs in steps}
        self.manifest.seconds["total"] = round(time.perf_counter() - t0, 3)
        writers.write_json(self.out / "raw" / "_manifest.json", asdict(self.manifest))
        return self.manifest


def generate(profile_name: str, out_dir: Path, seed: int = 20260916) -> Manifest:
    """Entry point used by the CLI and the tests."""
    profile = spec.PROFILES[profile_name]
    return Generator(profile, out_dir, seed=seed).run()
