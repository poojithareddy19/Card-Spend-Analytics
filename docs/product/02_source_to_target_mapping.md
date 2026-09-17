# Source-to-target mapping

The artefact a data engineer actually needs from an analyst. Column by column: where it comes from,
what happens to it, where it lands, and who to ask when it is wrong.

Conventions used throughout:

- **Source** is the field as the producer sends it, in the producer's own format.
- **Rule** is the transformation. `as-is` means no change, which is worth stating explicitly so
  nobody wonders whether something was done to it.
- **Target** is the column in the gold layer.
- A rule that can *fail* names the defect code it is checked by, so this document and the data
  quality scorecard refer to the same thing.

---

## 1. `card_transactions` — Avro binary, card processor

**Owner** cards-platform@example.com · **Window** T+1 by 04:00 Europe/London · **Contract**
`contracts/card_transactions.v2.avsc` · **Volume** ~1.15 transactions per customer per day

| Source field | Type | Rule | Target | Checked by |
|---|---|---|---|---|
| `transaction_id` | string | Deduplicate: keep one row per id, the latest by `txn_timestamp` | `transaction_id` | D02 |
| `card_id` | string | Resolve to `dim_card`; unresolved lands on the UNKNOWN member | `card_key` | D03 |
| `account_id` | string | As-is. Kept on the fact rather than derived through the card, because the funding account at the time is a historical fact | `account_key` via SCD2 | — |
| `merchant_id` | nullable string | Null is legitimate for ATM and fee rows; null on a purchase is a defect. Unresolved lands on UNKNOWN | `merchant_key` | D01 |
| `txn_timestamp` | timestamp-micros UTC | As-is, plus `date(txn_timestamp at Europe/London)` | `txn_timestamp`, `auth_date` | — |
| `posted_date` | date (Hive partition key) | Read from the partition path, not the file | `posted_date` | — |
| `amount_minor` | long, signed | As-is. Also multiplied by the as-of rate and rounded to whole pence | `amount_minor`, `amount_gbp_minor` | D04, D07 |
| `currency` | string | As-is | `currency_key` | — |
| `mcc` | int | As-is. Kept on the fact as well as on the merchant, because the two are allowed to disagree | `mcc` | D05 |
| `auth_code` | nullable string | Dropped from the gold layer. No analytical use, and it is operational data | — | — |
| `txn_type`, `channel`, `status` | enum | Collapsed into the junk dimension | `txn_context_key` | — |
| `pos_entry_mode` | nullable string, **v2 only** | As-is. Null on v1 rows means "the field did not exist", not "no value" | `pos_entry_mode` | — |
| `is_recurring` | nullable boolean, **v2 only** | As-is, same caveat | `is_recurring` | — |
| *derived* | | Which schema version wrote the file | `feed_version` | — |
| *derived* | | `posted_date - auth_date` | `posting_lag_days` | D06 |
| *derived* | | 100 less the weighted penalty of every failed rule | `dq_score` | — |

**The v1/v2 note is the important row in this table.** Two thirds of the history was written by
schema v1, which did not have the last two fields. Because both were added as nullable with a
default, one reader schema decodes the whole history in a single pass, and `feed_version` records
which shape the file actually had. Without that column, a null in `pos_entry_mode` would be
ambiguous and any analysis of entry modes over time would be quietly wrong.

---

## 2. `customers` — nested JSON Lines, onboarding platform

**Owner** onboarding@example.com · **Window** T+1 by 03:00 · **Contract**
`contracts/customers.schema.json`

| Source path | Rule | Target | Checked by |
|---|---|---|---|
| `customer_id` | As-is | `customer_id` | — |
| `created_at` | Parse ISO-8601, keep UTC | `onboarded_at`, `onboarded_on` | — |
| `segment` | As-is. **SCD2 watched column** | `segment` | — |
| `marketing_consent` | As-is | `marketing_consent` | — |
| `profile.first_name` | Flatten. Classified **personal** | `first_name` | — |
| `profile.last_name` | Flatten. Classified **personal** | `last_name` | — |
| `profile.date_of_birth` | Flatten, cast to date. Classified **sensitive** | `date_of_birth` | — |
| `profile.email` | Flatten. Classified **personal** | `email` | — |
| `profile.phone` | Flatten, nullable. Classified **personal** | `phone` | — |
| `address.line1`, `address.city`, `address.postcode`, `address.country` | Flatten | `address_line1`, `city`, `postcode`, `country` | D08 (postcode format) |
| `kyc.status` | Flatten. **SCD2 watched column** | `kyc_status` | D09 |
| `kyc.verified_at` | Flatten, nullable. Must be present when status is `verified` | `kyc_verified_at` | D09 |
| `kyc.risk_band` | Flatten. **SCD2 watched column** | `kyc_risk_band` | — |
| *whole document* | Kept verbatim as a JSON string | `document` | — |

**Two decisions worth defending.**

The whole nested document is kept alongside the flattened columns. Flattening serves the warehouse;
the document store serves a single-key deep read of the original shape, and re-nesting a flattened
frame is both lossy and pointless.

Only three columns are watched for SCD2. Watching every column would open a new customer version
every time somebody corrected their phone number, and since the fact resolves its SCD2 key at load,
that would change transaction keys for no analytically meaningful reason.

---

## 3. `accounts` and `cards` — CSV, core banking nightly extract

**Owner** core-banking@example.com · **Window** T+1 by 02:00 · **Contract**
`contracts/tabular_feeds.yaml`

| Source column | Rule | Target | Checked by |
|---|---|---|---|
| `account_id`, `customer_id` | Read as **declared string**, never inferred | `account_id`, `customer_id` | — |
| `product_code`, `status` | As-is. **SCD2 watched columns** | `product_code`, `status` | D10 |
| `currency` | As-is | `currency` | — |
| `opened_date`, `closed_date` | Cast to date; empty string to null | `opened_on`, `closed_on` | D10 |
| `overdraft_limit_minor` | Cast to int64. Deliberately **not** SCD2 watched: it moves too often | `overdraft_limit_minor` | — |
| `card_id`, `account_id` (cards) | Declared string | `card_id`, `account_id` | — |
| `card_type`, `network`, `status` | As-is | same | — |
| `issued_date`, `expiry_date` | Cast to date | `issued_on`, `expires_on` | — |

Dtypes are declared rather than inferred throughout. An inferred dtype changes with the data, which
is how an id column of digits silently becomes an integer and loses its leading zeros between one
load and the next.

---

## 4. `merchants` — Parquet, enrichment vendor

**Owner** data-platform@example.com · **Window** Monday by 06:00 · Weekly full refresh

| Source column | Rule | Target |
|---|---|---|
| `merchant_id` | As-is, carried as `vendor_merchant_id` | `merchant_key`, `vendor_merchant_id` |
| *none* | Placeholder, null today | `acquirer_merchant_id` |
| `merchant_name`, `mcc`, `category`, `country`, `is_online` | As-is | same |
| `first_seen_date` | As-is | `first_seen_on` |

`acquirer_merchant_id` exists and is null on purpose. The bus matrix flags merchant as the one
dimension that would stop conforming if a second business process sourced merchants from the
acquirer instead of the vendor. Adding the column now costs nothing; adding it later costs a
migration and a rewrite of every consumer.

Type 1 rather than Type 2, because the vendor ships a full refresh every week and versioning that
would produce a new row for every cosmetic reclassification.

---

## 5. `fx_rates` — JSON, treasury rates API

**Owner** treasury@example.com · **Window** Daily by 01:00

| Source field | Rule | Target | Checked by |
|---|---|---|---|
| `rate_date` | Cast to date | `rate_date` | D11 |
| `currency` | As-is | `currency` | — |
| `rate_to_gbp` | Cast to double. **GBP is exactly 1.0 and never drifts** | `rate_to_gbp` | — |
| `source` | Dropped from gold; kept in the lake for audit | — | — |
| *derived* | Dense daily spine per currency, carrying the last known rate across missing days | `is_carried_forward` | D11 |

**The gap-filling rule is the one to read twice.** Whole rate days go missing, roughly one in
twenty-nine. A plain equi-join on `(posted_date, currency)` would drop every transaction on a
missing day, and that kind of loss reconciles to nothing and is noticed a quarter later. The spine
carries the last known rate forward, which is what the treasury policy says to do, and marks every
row where it did so, so the carried-forward population is countable.

---

## Load order and dependencies

```
contracts check (all five feeds, header only)
        └─ refuse the whole batch on a breaking verdict, land nothing
lake landing (five formats to one canonical Parquet schema)
        └─ staging views
              ├─ snapshots (SCD2: customers, accounts)
              └─ intermediate (dedupe → FX spine → rule scoring)
                    └─ marts (dimensions → junk dim → fact → aggregates)
                          └─ gold layer export → Postgres, Redis
```

The contract check runs first and reads only headers, so refusing a bad batch costs a fraction of a
second rather than a full load.
