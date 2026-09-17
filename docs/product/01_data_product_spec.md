# Data product spec: card spend analytics

**Requested by** Cards product (Priya N, product lead) · **Owned by** Data platform
**Status** Accepted · **Version** 1.1 · **Last reviewed** 2026-09-16

---

## The ask, in the requester's words

> "We can't answer basic questions about our own card book without someone running a query by hand.
> I need to know what customers are spending, where, on what channel, and whether the number I'm
> looking at is trustworthy. And I need Finance to stop disagreeing with my spend figure."

## The question this product answers

For any customer, segment, merchant category, channel and month: how much was spent, how many
transactions, and how confident can the reader be in the answer.

## Grain

**One settled card transaction.** Not one authorisation, not one clearing event, not one daily
balance. Everything else is an aggregation of this.

The grain is the first thing to settle and the last thing anyone should change. Two of the questions
in the original request (chargeback rates, and balances at month end) are *not* answerable at this
grain, and are out of scope for that reason rather than being squeezed in.

## Required dimensions

| Dimension | Why it is needed | Type |
|---|---|---|
| Date | Every question is "per month" or "over time" | Conformed, generated |
| Customer | Segment analysis, and per-customer views in the app | SCD Type 2 |
| Account | Product-level analysis; the funding account at the time of the transaction | SCD Type 2 |
| Card | Debit against credit, network mix | Type 1 |
| Merchant | The category question, and the top-merchant question | Type 1 |
| Transaction context | Channel, type and status, always queried together | Junk |
| Currency | Foreign spend, and the FX conversion | Static |

## Measures and how they must behave

| Measure | Definition | Additivity |
|---|---|---|
| `amount_gbp_minor` | Amount converted at the rate in force on the posting date | Fully additive. The only measure safe to sum across currencies |
| `amount_minor` | Amount in the transaction's own currency | Additive within one currency only |
| `txn_count` | Settled transactions. Declines and reversals excluded | Additive |
| `dq_score` | Row quality, 0 to 100 | Non-additive, average it |
| `posting_lag_days` | Posting date less authorisation date | Semi-additive, average it, never sum it |

Every term above is defined in [the glossary](../model/03_glossary.md) with a named owner. Where
Finance and Cards product disagree on a definition, the glossary carries both and the marts expose
both, rather than one of them being quietly right.

## The Finance disagreement, resolved

The original complaint was that Finance and Cards product produced different spend figures for the
same month. They were both correct. Finance closes a month on **posting date**, because that is when
the money moved on the ledger. Cards product measures on **authorisation date**, because that is when
the customer chose to buy. A transaction authorised on 31 January and posted on 2 February belongs
to January for one and February for the other.

The product therefore carries **both dates as first-class keys** and every report states which one it
uses in its header. This is the single most valuable decision in the spec and it came out of a
twenty-minute conversation rather than a modelling exercise.

## Freshness and quality SLAs

See [dataset SLAs](03_dataset_slas.md) for the full commitments. In summary: available by 07:00
Europe/London on T+1, completeness above 99.5%, and any transaction posting more than two days after
authorisation reported as a timeliness breach rather than an error.

## Acceptance criteria

1. Total settled spend for a month, computed from the product, ties to the general ledger to the
   penny on posting date.
2. Any customer's twelve-month spend by category returns in under 150ms at production volume.
3. Every transaction in the source appears exactly once in the fact, and the reconciliation that
   proves it runs on every build.
4. A transaction in a currency with no published rate for its posting date still converts, using the
   most recent prior rate, and is marked as having done so.
5. A breaking change in any source feed is refused with the field name and the owning team, before
   anything is written.
6. Every published figure is reproducible from a seed: same input, same output, byte for byte.

All six are enforced by tests. Criteria 1, 3 and 4 are dbt tests that run on every build; 2 is
`tests/performance/test_budgets.py`; 5 is `tests/integration/test_contracts_and_ingest.py`; 6 is
`tests/unit/test_generator.py`.

## Explicitly out of scope

Named here so nobody has to ask twice.

- **Authorisation and clearing as separate events.** The processor feed arrives already collapsed to
  one row per transaction. Modelling the lifecycle needs two facts and a matching process.
- **Balances.** Money sitting, not money moving. Different business process, different grain.
- **Disputes and chargebacks.** Requested, genuinely useful, different grain. Next quarter.
- **Real-time.** This is a daily batch product. If the answer needs to be right within minutes, this
  is the wrong product and we should talk about a different one.

## Open questions

1. **Dormancy.** Core banking counts twelve months of any activity; Cards product counts card
   activity only. Both are in the marts as separate fields until somebody decides. Owner: Priya N.
2. **Refund attribution.** A refund in March of a February purchase currently reduces March. Finance
   are comfortable; Cards product are not sure. No change until it is settled.

## Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-28 | First accepted version |
| 1.1 | 2026-09-16 | Added the authorisation-date key after the Finance reconciliation session |
