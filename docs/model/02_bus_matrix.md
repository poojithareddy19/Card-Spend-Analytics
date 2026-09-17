# Bus matrix

The Kimball bus matrix: business processes down the side, conformed dimensions across the top. It is
the one page that answers "can these two reports be compared", because two facts can only be joined
on the dimensions they share.

Everything in **bold** is in scope for this repo. The rest is on the roadmap and is listed so that
the conformed dimensions are designed for it now rather than retrofitted later.

|  Business process | Grain | Date | Customer | Account | Card | Merchant | Merchant Category | Currency |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **Card spend** | One card transaction | X | X | X | X | X | X | X |
| **Customer onboarding** | One customer at their current KYC state | X | X | | | | | |
| **FX revaluation** | One currency per day | X | | | | | | X |
| Account balance snapshot | One account per day | X | X | X | | | | X |
| Dispute and chargeback | One dispute case | X | X | X | X | X | X | X |
| Direct debit and standing order | One mandate execution | X | X | X | | X | | X |
| Lending and arrears | One loan account per day | X | X | X | | | | X |

## Conformed dimensions

| Dimension | Grain | Type | Conformed across | Notes |
|---|---|---|---|---|
| `dim_date` | One calendar day | Static, generated | Every process | Carries UK bank holidays, ISO week, fiscal period. Generated, never sourced |
| `dim_customer` | One customer version | **SCD Type 2** | Card spend, onboarding, balances, lending | Segment and KYC status both change and both matter historically |
| `dim_account` | One account version | **SCD Type 2** | Card spend, balances, mandates | Product code and status change |
| `dim_card` | One card | SCD Type 1 | Card spend, disputes | Cards are reissued rather than amended, so history lives in new rows |
| `dim_merchant` | One merchant | SCD Type 1 with a change log | Card spend, disputes | The vendor ships a weekly full refresh, so Type 2 would be noise |
| `dim_merchant_category` | One MCC | Static, versioned | Card spend | Rollups change rarely and by committee. Worth a version column |
| `dim_currency` | One ISO 4217 code | Static | Every monetary process | Degenerate in some designs; kept real so FX joins have somewhere to hang |

## Why these are the conformed set

The test of a conformed dimension is whether two facts built by two different teams would agree on
it without a meeting. Three of these earn that:

- **`dim_date`** is generated from the calendar, so there is nothing to disagree about.
- **`dim_customer`** is keyed on the onboarding platform's `customer_id`, which every downstream
  system already carries. Nobody has to invent a mapping.
- **`dim_merchant_category`** is ISO 18245. It is conformed because a standards body did the work.

`dim_merchant` is the weak one and it is worth being honest about it. The merchant identifier comes
from an enrichment vendor, not from the bank, so if a second business process sourced merchants from
the acquirer instead, the two would not conform. That is a real risk to name in the design rather
than discover during a reconciliation. The mitigation is that `dim_merchant` carries the vendor key
and the acquirer key side by side from the first version, even though only one is populated today.

## Degenerate and junk dimensions

- `transaction_id` stays on the fact as a degenerate dimension. It has no attributes of its own and
  a `dim_transaction` would be a one-to-one join to nothing.
- `txn_type`, `channel` and `status` are low cardinality and always queried together. They collapse
  into a single junk dimension, `dim_txn_context`, of roughly 80 rows, rather than three joins or
  three wide text columns on a fact of hundreds of millions of rows. The width saved on the fact is
  the point; the benchmark on day 5 quotes the difference.
